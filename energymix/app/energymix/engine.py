"""Collector → Planner → Executor, plus de snelle regelaar en metingen."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import aiohttp

from . import helpers
from .config import Config
from .carlearn import CarLearner, discover, parse_time_to_full
from .discovery import suggest
from .executor import Executor
from .forecast import house_per_slot, pv_per_slot
from .ha import HomeAssistant
from .phases import headroom_a, victron_phase_idx
from .planner import CarState, Forecast, Plan, State, make_plan
from .prices import PriceSlot, slot_at
from .regulator import Regulator
from .store import Store
from .story import tell
from .tibber import fetch_prices
from .victron import Victron

log = logging.getLogger(__name__)

SAMPLE_SECONDS = 60
PUBLISH_PREFIX = "energymix"


class Engine:
    def __init__(self, cfg: Config, session: aiohttp.ClientSession, store: Store):
        self.base_cfg = cfg
        self.cfg = cfg
        self.session, self.store = session, store
        self.ha = HomeAssistant(session, cfg.ha_url, cfg.ha_token)
        self.victron = Victron(
            cfg.mqtt_host, cfg.mqtt_port, cfg.victron_portal_id, cfg.victron_vebus_instance,
            cfg.mqtt_username, cfg.mqtt_password,
        )
        self.executor = Executor(cfg, self.ha, self.victron, store)
        self.regulator = Regulator(cfg)
        self.learner = CarLearner(store)
        self.car_entities: dict[str, dict[str, str]] = {}
        self.prices: list[PriceSlot] = []
        self.prices_fetched = 0.0
        self.plan: Plan | None = None
        self.state: State | None = None
        self.story: list[str] = []
        self.helper_values: dict = {}
        self.master = True
        self.errors: list[str] = []
        self.stats_cache: tuple[float, dict] | None = None
        self._profile: tuple[float, dict[int, float]] | None = None
        self._helpers_checked = False
        self._wake = asyncio.Event()
        self._last_sample = 0.0
        self.sources: dict[str, str] = {}
        self._soc_warned = False
        self._master_seen = False
        self._boost_plugged = False
        self.ha.on_change(self._on_ha_change)

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.cfg.timezone)

    # ------------------------------------------------------------ toestand
    @property
    def watched(self) -> set[str]:
        c = self.cfg
        ents = {
            c.carcharger_select_entity, c.vannacht_entity, c.zappi_plug_entity,
            c.zappi_status_entity, c.zappi_mode_entity,
        }
        for car in c.cars:
            ents |= {car.cable_entity, car.location_entity}
        ents |= {h.entity_id for h in helpers.HELPERS}
        return {e for e in ents if e}

    def _on_ha_change(self, entity_id: str, _new: dict) -> None:
        if entity_id in self.watched:
            log.debug("Wijziging %s: opnieuw plannen", entity_id)
            self._wake.set()

    def collect(self) -> State:
        c, s, num = self.cfg, self.ha.state, self.ha.number
        st = State(
            soc=num(c.battery_soc_entity),
            solar_today_kwh=num(c.solar_today_entity),
            solar_remaining_kwh=num(c.solar_remaining_entity),
            solar_tomorrow_kwh=num(c.solar_tomorrow_entity),
            sunchance=num(c.sunchance_entity),
            carcharger_mode=s(c.carcharger_select_entity).strip().lower() or "auto",
            vannacht=s(c.vannacht_entity).strip().lower() in {"on", "true"},
            zappi_plug=s(c.zappi_plug_entity),
            zappi_status=s(c.zappi_status_entity),
            zappi_mode=s(c.zappi_mode_entity),
            cars=[
                CarState(car.name, car.max_range_km, s(car.cable_entity), s(car.location_entity), s(car.range_entity), car.kwh_per_km)
                for car in c.cars
            ],
            pv_w=num(c.pv_power_entity),
            grid_w=num(c.grid_power_entity),
            battery_w=num(c.battery_power_entity),
            house_w=num(c.house_power_entity),
            zappi_w=num(c.zappi_power_entity),
        )
        # kW-sensoren omrekenen
        for attr, eid in (("pv_w", c.pv_power_entity), ("grid_w", c.grid_power_entity),
                          ("battery_w", c.battery_power_entity), ("house_w", c.house_power_entity),
                          ("zappi_w", c.zappi_power_entity)):
            unit = str(self.ha.attributes(eid).get("unit_of_measurement", "")).lower() if eid else ""
            if unit == "kw" and getattr(st, attr) is not None:
                setattr(st, attr, getattr(st, attr) * 1000)
        self._car_details(st)
        # Stroom per fase (A); sensoren mogen W, kW of A zijn. Geen sensoren: Victron MQTT.
        self.sources = {}
        phase_ents = [c.grid_l1_entity, c.grid_l2_entity, c.grid_l3_entity][: max(1, c.grid_phases)]
        if any(phase_ents):
            st.phase_a = [self._phase_current(eid) for eid in phase_ents]
            self.sources["phases"] = "HA"
        else:
            vw = self.victron.grid_phase_w()[: max(1, c.grid_phases)]
            if any(x is not None for x in vw):
                st.phase_a = [x / c.grid_voltage if x is not None else None for x in vw]
                self.sources["phases"] = "Victron MQTT"
        if st.grid_w is None:
            vw = self.victron.grid_phase_w()
            if any(x is not None for x in vw):
                st.grid_w = sum(x for x in vw if x is not None)
                self.sources["grid_w"] = "Victron MQTT"
        if st.battery_w is None and self.victron.battery_w() is not None:
            st.battery_w = self.victron.battery_w()
            self.sources["battery_w"] = "Victron MQTT"
        # Accuniveau: de GX zelf gaat voor (dat is wat VRM/het display toont); HA-sensor als terugval
        v_soc = self.victron.soc(c.battery_soc_source)
        if v_soc is not None:
            ha_soc = st.soc
            st.soc = v_soc
            self.sources["soc"] = "Victron MQTT" + ("" if c.battery_soc_source == "system" else f" ({c.battery_soc_source})")
            if ha_soc is not None and abs(ha_soc - st.soc) >= 5 and not self._soc_warned:
                self._soc_warned = True
                log.warning(
                    "Accuniveau wijkt af: %s zegt %.1f%%, de Victron GX %.1f%%. Energymix gebruikt de GX.",
                    c.battery_soc_entity, ha_soc, st.soc,
                )
        elif st.soc is not None:
            self.sources["soc"] = "HA"
        # Zonnepanelen: de Envoy is leidend; de Victron PV-omvormer alleen als reserve
        # (die valt soms weg). Geen entity ingesteld: zoek de Envoy-sensor zelf op.
        pv_eid = c.pv_power_entity or suggest(self.ha.states, "pv_power_entity") or ""
        if pv_eid and st.pv_w is None:
            st.pv_w = self._watts(pv_eid)
        if st.pv_w is not None:
            self.sources["pv_w"] = "Envoy" + ("" if c.pv_power_entity else f" (gevonden: {pv_eid})")
        elif self.victron.pv_w() is not None:
            st.pv_w = self.victron.pv_w()
            self.sources["pv_w"] = "Victron PV-omvormer (Envoy niet beschikbaar)"
        if st.house_w is None and st.grid_w is not None:
            st.house_w = (st.pv_w or 0) + st.grid_w - (st.battery_w or 0) - (st.zappi_w or 0)
        return st

    def _car_details(self, st: State) -> None:
        """Tesla-gegevens per auto (accu %, laadlimiet, tijd tot vol, laadsnelheid) + leren."""
        now_dt = datetime.now(timezone.utc)
        fast = st.zappi_mode == "Fast" and st.zappi_charging and (
            st.zappi_w is None or st.zappi_w >= self.cfg.zappi_max_w * 0.8
        )
        complete = st.car_full
        self.car_entities = {}
        for car_cfg, cs in zip(self.cfg.cars, st.cars):
            ents = discover(self.ha.states, car_cfg)
            self.car_entities[car_cfg.name] = ents
            num = self.ha.number
            cs.soc = num(ents["battery_level"]) if "battery_level" in ents else None
            cs.charge_limit = num(ents["charge_limit"]) if "charge_limit" in ents else None
            if "time_to_full" in ents:
                cs.time_to_full_min = parse_time_to_full(
                    self.ha.state(ents["time_to_full"]), self.ha.attributes(ents["time_to_full"]), now_dt
                )
            if "charge_rate" in ents:
                rate = num(ents["charge_rate"])
                unit = str(self.ha.attributes(ents["charge_rate"]).get("unit_of_measurement", "")).lower()
                cs.charge_rate_kmh = rate * 1.609 if rate is not None and "mi" in unit else rate
            plugged_here = cs.connected and cs.home
            lr = self.learner.update(
                car_cfg.name, cs.range_value, cs.soc, cs.charge_limit, cs.charge_rate_kmh,
                charging_fast=fast and plugged_here, complete=complete and plugged_here,
            )
            cs.learned_max_km = round(lr.max_range_km) if lr.max_range_km else None
            cs.learned_speed_kmh = round(lr.speed_kmh, 1) if lr.speed_kmh and lr.speed_n >= 2 else None

    def _watts(self, eid: str) -> float | None:
        val = self.ha.number(eid)
        if val is None:
            return None
        unit = str(self.ha.attributes(eid).get("unit_of_measurement", "")).lower()
        return val * 1000 if unit == "kw" else val

    def _phase_current(self, eid: str) -> float | None:
        val = self.ha.number(eid)
        if val is None:
            return None
        unit = str(self.ha.attributes(eid).get("unit_of_measurement", "")).lower()
        if unit == "a":
            return val
        if unit == "kw":
            val *= 1000
        return val / self.cfg.grid_voltage

    def forecast(self, now: datetime) -> Forecast:
        c = self.cfg
        home = self.ha.attributes("zone.home")
        lat = float(home.get("latitude", 52.1))
        lon = float(home.get("longitude", 5.1))
        detailed = None
        for eid in (c.solar_remaining_entity, c.solar_tomorrow_entity):
            d = self.ha.attributes(eid).get("detailedForecast") if eid else None
            if d:
                detailed = (detailed or []) + list(d)
        st = self.state or State()
        pv = pv_per_slot(self.prices, now, self.tz, st.solar_remaining_kwh, st.solar_tomorrow_kwh, lat, lon, detailed)
        if self._profile is None or time.time() - self._profile[0] > 3600:
            self._profile = (time.time(), self.store.house_profile(self.tz))
        house = house_per_slot(self.prices, self.tz, self._profile[1], c.house_load_default_w)
        stats = self.battery_stats()
        return Forecast(pv, house, stats.get("learned_charge_w"))

    def battery_stats(self) -> dict:
        if self.stats_cache is None or time.time() - self.stats_cache[0] > 600:
            self.stats_cache = (time.time(), self.store.battery_stats(self.tz, self.cfg.battery_capacity_kwh))
        return self.stats_cache[1]

    # ------------------------------------------------------------ prijzen
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

    # ------------------------------------------------------------ helpers
    async def _helpers(self) -> None:
        if not self.ha.connected.is_set():
            return
        if self.base_cfg.create_helpers and not self._helpers_checked:
            self._helpers_checked = True
            await helpers.ensure_helpers(self.ha)
            await asyncio.sleep(1)
        self.helper_values = helpers.read_helpers(self.ha)
        self.cfg = self.effective(helpers.apply_overrides(self.base_cfg, self.helper_values))
        was, seen = self.master, self._master_seen
        self.master = self.helper_values.get("master", True)
        self._master_seen = True
        if seen and was and not self.master:
            log.warning("Aansturen uitgezet: Victron terug naar standaardwaarden")
            await self.executor.restore_defaults("Aansturen uitgezet")
        self.executor.cfg = self.cfg
        self.regulator.cfg = self.cfg

    def effective(self, cfg: Config) -> Config:
        """Vul "auto"-waarden in met wat de installatie zelf meldt."""
        from dataclasses import replace

        if str(cfg.victron_phases).strip().lower() == "auto":
            n = self.victron.vebus_phases() or 1
            cfg = replace(cfg, victron_phases=",".join(str(i) for i in range(1, n + 1)))
        return cfg

    # ------------------------------------------------------------ cyclus
    async def cycle(self) -> None:
        errors: list[str] = []
        try:
            await self.refresh_prices()
        except Exception as e:  # noqa: BLE001
            errors.append(f"Prijzen: {e}")
            log.warning("Prijzen ophalen mislukt: %s", e)
        if not self.ha.connected.is_set():
            errors.append("Geen verbinding met Home Assistant")
        try:
            await self._helpers()
        except Exception as e:  # noqa: BLE001
            log.warning("Helpers: %s", e)
        now = datetime.now(timezone.utc)
        self.state = self.collect()
        await self._boost_auto_off()
        history = list({p.start: p for p in [*self.store.price_history(8), *self.prices]}.values())
        self.plan = make_plan(self.cfg, self.prices, self.state, now, self.forecast(now), history)
        self.story = tell(self.plan, self.state, self.tz)
        self.store.save_plan(self.plan.to_dict(), asdict(self.state))
        if self.plan.now and self.ha.connected.is_set():
            dvcc = self.regulator.step(self.plan.now, self.state)
            await self.executor.apply(self.plan.now, self.master, dvcc)
            try:
                await self.publish()
            except Exception as e:  # noqa: BLE001
                log.warning("Sensoren publiceren mislukt: %s", e)
        self.errors = errors

    async def fast_tick(self) -> None:
        """Elke paar seconden: live toestand, regelaar en metingen."""
        if not self.ha.connected.is_set():
            return
        # Aantal fases van de Multi's komt pas binnen na de eerste MQTT-berichten
        if str(self.base_cfg.victron_phases).strip().lower() == "auto":
            eff = self.effective(helpers.apply_overrides(self.base_cfg, self.helper_values))
            if eff.victron_phases != self.cfg.victron_phases:
                log.info("Victron laadt op fase(s) %s (uit de Multi's)", eff.victron_phases)
                self.cfg = self.executor.cfg = self.regulator.cfg = eff
        self.state = self.collect()
        if self.cfg.car_boost:
            before = self.cfg.car_boost
            await self._boost_auto_off()
            if before and not self.cfg.car_boost:
                self._wake.set()  # direct opnieuw plannen
        now = time.time()
        if now - self._last_sample >= SAMPLE_SECONDS:
            self._last_sample = now
            st = self.state
            cur = slot_at(self.prices, datetime.now(timezone.utc))
            self.store.add_sample(
                datetime.now(timezone.utc), soc=st.soc, pv_w=st.pv_w, grid_w=st.grid_w,
                battery_w=st.battery_w, house_w=st.house_w, zappi_w=st.zappi_w, price=cur.price if cur else None,
            )
        if self.plan and self.plan.now:
            value, why = self.regulator.step(self.plan.now, self.state)
            if value is not None and value != self.executor._last_desired.get("dvcc"):
                await self.executor.apply_dvcc(value, why, self.master)

    async def run(self) -> None:
        asyncio.create_task(self.ha.run())
        asyncio.create_task(self.victron.run())
        asyncio.create_task(self._fast_loop())
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

    async def _boost_auto_off(self) -> None:
        """"Auto nu snel laden" zet zichzelf uit als de auto vol is of de stekker eruit gaat.

        Aangezet vóór het insteken? Dan wachten we tot de auto eerst aangesloten is
        geweest, anders zou hij direct weer uitgaan.
        """
        if not self.cfg.car_boost or not self.state:
            self._boost_plugged = False
            return
        st = self.state
        if st.car_plugged:
            self._boost_plugged = True
        reason = "auto vol" if st.car_full else "stekker eruit" if (self._boost_plugged and not st.car_plugged) else None
        if not reason:
            return
        log.info("Snel laden klaar (%s): schakelaar uit", reason)
        self._boost_plugged = False
        try:
            await helpers.set_helper(self.ha, "car_boost", False)
            self.helper_values["car_boost"] = False
            self.cfg = self.executor.cfg = self.regulator.cfg = self.effective(
                helpers.apply_overrides(self.base_cfg, self.helper_values)
            )
        except Exception as e:  # noqa: BLE001
            log.warning("Snel laden uitzetten mislukt: %s", e)

    async def shutdown(self) -> None:
        """Bij stoppen van de add-on: Victron niet in een tijdelijke stand achterlaten."""
        if self.master and self.cfg.control.any:
            log.info("Afsluiten: Victron terug naar standaardwaarden")
            try:
                await asyncio.wait_for(self.executor.restore_defaults("add-on gestopt"), 10)
            except Exception as e:  # noqa: BLE001
                log.error("Standaardwaarden terugzetten mislukt: %s", e)

    async def _fast_loop(self) -> None:
        while True:
            try:
                await self.fast_tick()
            except Exception:  # noqa: BLE001
                log.exception("Snelle lus mislukt")
            await asyncio.sleep(max(5, self.cfg.regulator_seconds))

    def _seconds_to_next_tick(self) -> float:
        step = self.cfg.interval_minutes * 60
        return step - (time.time() % step) + 2

    # ------------------------------------------------------------ naar HA
    async def publish(self) -> None:
        plan, st, p = self.plan, self.state, PUBLISH_PREFIX
        if not plan or not plan.now or not st:
            return
        cur = plan.now
        season_label = {"day": "Zomer (middag goedkoop)", "night": "Winter (nacht goedkoop)", "neutral": "Neutraal"}.get(
            plan.season.get("effective"), "Onbekend"
        )
        ess_label = "Laden van net" if cur.ess_state == 9 else "Terugleveren" if (cur.setpoint_w or 0) < 0 else "Zelfverbruik"
        story = " ".join(self.story)
        sensors = {
            f"sensor.{p}_status": (story[:250], {"friendly_name": "Energymix status", "icon": "mdi:lightning-bolt", "verhaal": self.story}),
            f"sensor.{p}_prijs_nu": (round(cur.price, 4), {"friendly_name": "Energymix prijs nu", "unit_of_measurement": "EUR/kWh", "icon": "mdi:currency-eur"}),
            f"sensor.{p}_accu_modus": (ess_label, {"friendly_name": "Energymix accu", "icon": "mdi:home-battery", "reden": cur.reasons.get("ess")}),
            f"sensor.{p}_zappi_plan": (cur.zappi_mode or "geen", {"friendly_name": "Energymix Zappi", "icon": "mdi:ev-station", "reden": cur.reasons.get("zappi")}),
            f"sensor.{p}_seizoen": (season_label, {"friendly_name": "Energymix seizoenpatroon", "icon": "mdi:weather-sunny-alert", **plan.season}),
            f"sensor.{p}_besparing": (plan.summary.get("saving_eur", 0), {"friendly_name": "Energymix verwachte besparing", "unit_of_measurement": "EUR", "icon": "mdi:piggy-bank"}),
            f"binary_sensor.{p}_terugleveren": ("on" if (cur.setpoint_w or 0) < 0 else "off", {"friendly_name": "Energymix levert terug", "icon": "mdi:transmission-tower-export"}),
        }
        if plan.summary.get("target_reached_at"):
            sensors[f"sensor.{p}_accu_vol_om"] = (plan.summary["target_reached_at"], {"friendly_name": "Energymix accu op doel om", "device_class": "timestamp"})
        if plan.car.full_at:
            sensors[f"sensor.{p}_auto_vol_om"] = (plan.car.full_at.isoformat(), {"friendly_name": "Energymix auto vol om", "device_class": "timestamp", "auto": plan.car.name})
        for eid, (state, attrs) in sensors.items():
            await self.ha.set_state(eid, state, attrs)

    # ------------------------------------------------------------ live
    def live(self) -> dict:
        st = self.state or State()
        cars = []
        for car, cs in zip(self.cfg.cars, st.cars):
            cars.append({
                "name": car.name, "range_km": cs.range_value, "max_range_km": cs.max_km,
                "configured_max_km": car.max_range_km, "learned_max_km": cs.learned_max_km,
                "learned_speed_kmh": cs.learned_speed_kmh, "soc": cs.soc, "charge_limit": cs.charge_limit,
                "time_to_full_min": cs.time_to_full_min, "charge_rate_kmh": cs.charge_rate_kmh,
                "connected": cs.connected, "home": cs.home, "entities": self.car_entities.get(car.name, {}),
            })
        cur = slot_at(self.prices, datetime.now(timezone.utc))
        reg = self.regulator.st.to_dict()
        if not reg["phase_free_a"] and st.phase_a and all(x is not None for x in st.phase_a):
            # Ook buiten het laden laten zien hoeveel ruimte elke fase heeft (zonder de accu zelf)
            vp = victron_phase_idx(self.cfg)
            batt_ac = max(0.0, st.battery_w or 0.0) / (self.cfg.charge_efficiency or 0.93)
            others = [float(x) - (batt_ac / len(vp) / self.cfg.grid_voltage if i in vp else 0) for i, x in enumerate(st.phase_a)]
            reg["phase_free_a"] = [round(x, 1) for x in headroom_a(self.cfg, others)]
        return {
            "ts": datetime.now(timezone.utc).isoformat(),
            "soc": st.soc, "pv_w": st.pv_w, "grid_w": st.grid_w, "battery_w": st.battery_w,
            "house_w": st.house_w, "zappi_w": st.zappi_w, "zappi_mode": st.zappi_mode,
            "zappi_status": st.zappi_status, "zappi_plug": st.zappi_plug, "cars": cars,
            "price": cur.price if cur else None,
            "phase_a": [round(x, 1) if x is not None else None for x in st.phase_a],
            "phase_max_a": self.cfg.grid_phase_max_a,
            "victron_phases": [p + 1 for p in victron_phase_idx(self.cfg)],
            "sources": self.sources,
            "victron_connected": self.victron.connected,
            "regulator": reg,
        }
