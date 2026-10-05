"""Executor: zet alleen wat afwijkt, en alleen voor onderdelen die live staan.

In shadow mode wordt niets geschreven; wel wordt elke wijziging in het gewenste
gedrag gelogd, zodat je kunt vergelijken met wat Node-RED doet.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from .config import Config
from .ha import HomeAssistant
from .planner import SlotPlan
from .store import Store
from .victron import Victron

log = logging.getLogger(__name__)

REASSERT_SECONDS = 3600  # Victron-instellingen zonder terugkoppeling eens per uur opnieuw zetten


@dataclass
class Action:
    component: str
    desired: object
    actual: object
    reason: str
    live: bool
    executed: bool = False
    error: str | None = None

    def to_dict(self) -> dict:
        return self.__dict__.copy()


class Executor:
    def __init__(self, cfg: Config, ha: HomeAssistant, victron: Victron, store: Store):
        self.cfg, self.ha, self.victron, self.store = cfg, ha, victron, store
        self._last_desired: dict[str, object] = {}
        self._last_written: dict[str, tuple[object, float]] = {}
        self.last_actions: list[Action] = []

    async def apply(self, sp: SlotPlan) -> list[Action]:
        c, ctl, r = self.cfg, self.cfg.control, sp.reasons
        actions: list[Action] = []

        if sp.pv_on is not None:
            actual = self.ha.state(c.pv_switch_entity)
            desired = "on" if sp.pv_on else "off"
            actions.append(Action("pv", desired, actual, r.get("pv", ""), ctl.pv))
        if sp.zappi_mode is not None:
            actions.append(Action("zappi", sp.zappi_mode, self.ha.state(c.zappi_mode_entity), r.get("zappi", ""), ctl.zappi))
        if sp.ess_state is not None:
            actions.append(Action("ess", sp.ess_state, self._written("ess"), r.get("ess", ""), ctl.ess))
        if sp.dvcc_current is not None:
            actions.append(Action("dvcc", sp.dvcc_current, self._written("dvcc"), r.get("dvcc", ""), ctl.dvcc))
        if sp.feed_in_disabled is not None:
            actions.append(Action("feed_in", sp.feed_in_disabled, self._written("feed_in"), r.get("feed_in", ""), ctl.feed_in))

        for a in actions:
            if a.live:
                try:
                    a.executed = await self._execute(a)
                except Exception as e:  # noqa: BLE001
                    a.error = str(e)
                    log.error("Uitvoeren %s mislukt: %s", a.component, e)
            if self._last_desired.get(a.component) != a.desired or a.executed:
                self.store.log_decision(a.component, a.desired, a.actual, a.reason, a.executed)
                log.info("%s %s -> %s (%s)", "LIVE" if a.live else "SHADOW", a.component, a.desired, a.reason)
            self._last_desired[a.component] = a.desired

        self.last_actions = actions
        return actions

    def _written(self, kind: str):
        v = self._last_written.get(kind)
        return v[0] if v else None

    async def _execute(self, a: Action) -> bool:
        if a.component == "pv":
            if a.actual == a.desired:
                return False
            await self.ha.call_service("switch", f"turn_{a.desired}", self.cfg.pv_switch_entity)
            return True
        if a.component == "zappi":
            if a.actual == a.desired:
                return False
            await self.ha.call_service("select", "select_option", self.cfg.zappi_mode_entity, {"option": a.desired})
            return True
        # Victron: geen read-back, dus schrijven bij wijziging of eens per uur
        last = self._last_written.get(a.component)
        if last and last[0] == a.desired and time.time() - last[1] < REASSERT_SECONDS:
            return False
        if not self.victron.configured:
            raise RuntimeError("MQTT/Victron niet geconfigureerd")
        await self.victron.write(a.component, int(a.desired))
        self._last_written[a.component] = (a.desired, time.time())
        return True
