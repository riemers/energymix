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
ECHO_SECONDS = 20  # zo lang na schrijven nog niet teruglezen: de GX moet de nieuwe waarde eerst melden
READBACK = ("ess", "dvcc", "setpoint")  # instellingen die we van de Victron terug kunnen lezen


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
        self.overrides: list[dict] = []  # keren dat iets anders onze Victron-instelling veranderde

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
            actions.append(Action("ess", sp.ess_state, self._actual("ess"), r.get("ess", ""), self.live("ess", master)))
        if sp.dvcc_current is not None:
            value, why = dvcc if dvcc and dvcc[0] is not None else (sp.dvcc_current, r.get("dvcc", ""))
            actions.append(Action("dvcc", value, self._actual("dvcc"), why, self.live("dvcc", master)))
        if sp.setpoint_w is not None:
            actions.append(Action("setpoint", sp.setpoint_w, self._actual("setpoint"), r.get("setpoint", ""), self.live("setpoint", master)))
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
        a = Action("dvcc", value, self._actual("dvcc"), reason, self.live("dvcc", master))
        await self._run(a)
        self.last_actions = [x for x in self.last_actions if x.component != "dvcc"] + [a]
        return a

    async def enforce(self, master: bool = True) -> list[Action]:
        """Tussendoor: staat de Victron nog op wat wij willen? Zo niet, direct terugzetten.

        Iets anders (bv. een Node-RED flow) kan de ESS-stand of laadstroom veranderen.
        Zonder deze controle bleef de add-on denken dat de accu laadt tot het volgende
        kwartier of zelfs een uur later.
        """
        done = []
        for a in list(self.last_actions):
            if a.component not in READBACK or not a.live or not master or self._echo_pending(a.component):
                continue
            actual = self._readback(a.component)
            if actual is None or _same(actual, a.desired):
                continue
            why = f"teruggezet, Victron stond op {_fmt(actual)} (iets anders veranderde het): {a.reason}"
            b = Action(a.component, a.desired, actual, why, a.live)
            await self._run(b)
            self.last_actions = [b if x is a else x for x in self.last_actions]
            done.append(b)
        return done

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

    async def restore_defaults(self, why: str) -> list[Action]:
        """Zet de Victron terug naar veilige standaardwaarden (alleen onderdelen die live mochten).

        Gebruikt bij het uitzetten van "Aansturen" en bij stoppen van de add-on, zodat
        de Victron niet blijft terugleveren, bewaren of van het net laden.
        """
        c = self.cfg
        defaults = {
            "ess": 10,  # zelfverbruik
            "dvcc": c.dvcc_max_charge_current,
            "setpoint": int(c.grid_setpoint_default_w),
            "feed_in": 1,  # alleen critical loads
        }
        done = []
        for comp, value in defaults.items():
            if not getattr(c.control, comp, False):
                continue
            a = Action(comp, value, self._written(comp), f"standaardwaarde terug: {why}", True)
            self._last_written.pop(comp, None)  # altijd schrijven
            await self._run(a)
            done.append(a)
        return done

    def _written(self, kind: str):
        v = self._last_written.get(kind)
        return v[0] if v else None

    def _readback(self, kind: str) -> float | None:
        getter = getattr(self.victron, "setting", None)
        return getter(kind) if getter and kind in READBACK else None

    def _actual(self, kind: str):
        """Wat de Victron nu heeft: teruggelezen, anders wat we het laatst schreven."""
        v = self._readback(kind)
        return v if v is not None else self._written(kind)

    def _echo_pending(self, kind: str) -> bool:
        last = self._last_written.get(kind)
        return bool(last and time.time() - last[1] < ECHO_SECONDS)

    def _note_override(self, kind: str, found: float, desired: object) -> None:
        log.warning("Victron %s stond op %s in plaats van %s: iets anders (bv. Node-RED) heeft het veranderd, "
                    "zet het terug", kind, _fmt(found), desired)
        self.overrides = [*self.overrides, {"ts": time.time(), "component": kind, "found": _fmt(found),
                                            "desired": desired}][-20:]

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
        last = self._last_written.get(a.component)
        actual = None if self._echo_pending(a.component) else self._readback(a.component)
        if actual is not None:
            # Teruggelezen: alleen schrijven als de Victron iets anders heeft staan
            if _same(actual, a.desired):
                return False
            if last and last[0] == a.desired:
                self._note_override(a.component, actual, a.desired)
        elif last and last[0] == a.desired and time.time() - last[1] < REASSERT_SECONDS:
            # Geen terugkoppeling: schrijven bij wijziging of eens per uur
            return False
        if not self.victron.configured:
            raise RuntimeError("MQTT/Victron niet geconfigureerd")
        await self.victron.write(a.component, int(a.desired))
        self._last_written[a.component] = (a.desired, time.time())
        return True


def _same(actual: float, desired: object) -> bool:
    try:
        return abs(float(actual) - float(desired)) < 0.5  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False


def _fmt(v: float) -> object:
    return int(v) if float(v).is_integer() else v
