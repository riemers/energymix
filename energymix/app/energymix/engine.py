"""Collector → Planner → Executor in één lus."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

import aiohttp

from .config import Config
from .executor import Executor
from .ha import HomeAssistant
from .planner import CarState, Plan, State, make_plan
from .prices import PriceSlot
from .store import Store
from .tibber import fetch_prices
from .victron import Victron

log = logging.getLogger(__name__)


class Engine:
    def __init__(self, cfg: Config, session: aiohttp.ClientSession, store: Store):
        self.cfg, self.session, self.store = cfg, session, store
        self.ha = HomeAssistant(session, cfg.ha_url, cfg.ha_token)
        self.victron = Victron(
            cfg.mqtt_host, cfg.mqtt_port, cfg.victron_portal_id, cfg.victron_vebus_instance,
            cfg.mqtt_username, cfg.mqtt_password,
        )
        self.executor = Executor(cfg, self.ha, self.victron, store)
        self.prices: list[PriceSlot] = []
        self.prices_fetched = 0.0
        self.plan: Plan | None = None
        self.state: State | None = None
        self.errors: list[str] = []
        self._wake = asyncio.Event()
        self.ha.on_change(self._on_ha_change)

    @property
    def watched(self) -> set[str]:
        c = self.cfg
        ents = {
            c.carcharger_select_entity, c.vannacht_entity, c.zappi_plug_entity,
            c.zappi_status_entity, c.zappi_mode_entity,
        }
        for car in c.cars:
            ents |= {car.cable_entity, car.location_entity}
        return {e for e in ents if e}

    def _on_ha_change(self, entity_id: str, _new: dict) -> None:
        if entity_id in self.watched:
            log.debug("Wijziging %s: opnieuw plannen", entity_id)
            self._wake.set()

    def collect(self) -> State:
        c, s = self.cfg, self.ha.state

        def num(eid):
            try:
                return float(s(eid)) if eid else None
            except ValueError:
                return None

        return State(
            soc=num(c.battery_soc_entity),
            solar_today_kwh=num(c.solar_today_entity),
            sunchance=num(c.sunchance_entity),
            carcharger_mode=s(c.carcharger_select_entity).strip().lower() or "auto",
            vannacht=s(c.vannacht_entity).strip().lower() in {"on", "true"},
            zappi_plug=s(c.zappi_plug_entity),
            zappi_status=s(c.zappi_status_entity),
            zappi_mode=s(c.zappi_mode_entity),
            cars=[
                CarState(car.name, car.max_range_km, s(car.cable_entity), s(car.location_entity), s(car.range_entity))
                for car in c.cars
            ],
        )

    async def refresh_prices(self, force: bool = False) -> None:
        now = datetime.now(timezone.utc)
        covered = self.prices and self.prices[-1].end > now + timedelta(hours=12)
        stale = time.time() - self.prices_fetched > 3600
        # Morgen komt rond 13:00 binnen: tot die tijd elk uur opnieuw proberen
        if not (force or not self.prices or (not covered and stale)):
            return
        if not self.cfg.tibber_token:
            raise RuntimeError("Geen Tibber-token ingesteld")
        self.prices = await fetch_prices(self.session, self.cfg.tibber_token, self.cfg.price_resolution)
        self.prices_fetched = time.time()
        self.store.save_prices(self.prices)

    async def cycle(self) -> None:
        errors: list[str] = []
        try:
            await self.refresh_prices()
        except Exception as e:  # noqa: BLE001
            errors.append(f"Prijzen: {e}")
            log.warning("Prijzen ophalen mislukt: %s", e)
        if not self.ha.connected.is_set():
            errors.append("Geen verbinding met Home Assistant")
        self.state = self.collect()
        self.plan = make_plan(self.cfg, self.prices, self.state, datetime.now(timezone.utc))
        self.store.save_plan(self.plan.to_dict(), asdict(self.state))
        if self.plan.now and self.ha.connected.is_set():
            await self.executor.apply(self.plan.now)
        self.errors = errors

    async def run(self) -> None:
        asyncio.create_task(self.ha.run())
        try:
            await asyncio.wait_for(self.ha.connected.wait(), 20)
        except asyncio.TimeoutError:
            log.warning("HA nog niet verbonden, plan toch")
        while True:
            try:
                await self.cycle()
            except Exception:  # noqa: BLE001
                log.exception("Cyclus mislukt")
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), self._seconds_to_next_tick())
                await asyncio.sleep(5)  # debounce: meerdere wijzigingen tegelijk
            except asyncio.TimeoutError:
                pass

    def _seconds_to_next_tick(self) -> float:
        step = self.cfg.interval_minutes * 60
        return step - (time.time() % step) + 2
