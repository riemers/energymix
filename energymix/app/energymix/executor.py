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

    def live(self, component: str, master: bool = True) -> bool:
        return master and getattr(self.cfg.control, component, False)

    def actions_for(self, sp: SlotPlan, master: bool = True, dvcc: tuple[int | None, str] | None = None) -> list[Action]:
        c, r = self.cfg, sp.reasons
        actions: list[Action] = []
        if sp.pv_on is not None:
            desired = "on" if sp.pv_on else "off"
            actions.append(Action("pv", desired, self.ha.state(c.pv_switch_entity), r.get("pv", ""), self.live("pv", master)))
        if sp.zappi_mode is not None:
            actions.append(Action("zappi", sp.zappi_mode, self.ha.state(c.zappi_mode_entity), r.get("zappi", ""), self.live("zappi", master)))
        if sp.ess_state is not None:
            actions.append(Action("ess", sp.ess_state, self._written("ess"), r.get("ess", ""), self.live("ess", master)))
        if sp.dvcc_current is not None:
            value, why = dvcc if dvcc and dvcc[0] is not None else (sp.dvcc_current, r.get("dvcc", ""))
            actions.append(Action("dvcc", value, self._written("dvcc"), why, self.live("dvcc", master)))
        if sp.setpoint_w is not None:
            actions.append(Action("setpoint", sp.setpoint_w, self._written("setpoint"), r.get("setpoint", ""), self.live("setpoint", master)))
        if sp.feed_in_disabled is not None:
            actions.append(Action("feed_in", sp.feed_in_disabled, self._written("feed_in"), r.get("feed_in", ""), self.live("feed_in", master)))
        return actions

    async def apply(self, sp: SlotPlan, master: bool = True, dvcc: tuple[int | None, str] | None = None) -> list[Action]:
        actions = self.actions_for(sp, master, dvcc)
        for a in actions:
            await self._run(a)
        self.last_actions = actions
        return actions

    async def apply_dvcc(self, value: int, reason: str, master: bool = True) -> Action:
        """Tussentijdse DVCC-aanpassing door de regelaar."""
        a = Action("dvcc", value, self._written("dvcc"), reason, self.live("dvcc", master))
        await self._run(a)
        self.last_actions = [x for x in self.last_actions if x.component != "dvcc"] + [a]
        return a

    async def _run(self, a: Action) -> None:
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
