"""Snelle regeling van de accu-laadstroom (DVCC) als de accu van het net laadt.

De planner zegt per kwartier hoeveel de accu mag laden. Maar of dat past hangt
af van wat het huis en de auto op dat moment trekken. Deze regelaar kijkt elke
`regulator_seconds` naar de ruimte op de aansluiting:

    ruimte = aansluiting - marge - (netafname - wat de accu nu zelf laadt)

en zet de DVCC-stroom daarop, met:
- stappen van `dvcc_step_a` (geen gefriemel per ampère),
- direct omlaag, maar pas omhoog als er `RAISE_AFTER` seconden lang ruimte is,
- de auto heeft voorrang: staat de Zappi op Fast maar trekt hij minder dan
  verwacht, dan regelt hij zichzelf terug (CT-beveiliging). Dat tekort tellen
  we als bezet, zodat de accu niet "de vrijgekomen ruimte" gaat opeten.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .config import Config
from .planner import SlotPlan, State

RAISE_AFTER = 120  # s ruimte nodig voordat we de stroom verhogen
THROTTLE_FRACTION = 0.85  # Zappi trekt < 85% van max op Fast = teruggeregeld


@dataclass
class RegulatorState:
    current_a: int | None = None
    target_a: int | None = None
    headroom_w: float | None = None
    car_throttled: bool = False
    reason: str = ""
    raise_since: float | None = None
    history: list[tuple[float, int]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "current_a": self.current_a,
            "target_a": self.target_a,
            "headroom_w": round(self.headroom_w) if self.headroom_w is not None else None,
            "car_throttled": self.car_throttled,
            "reason": self.reason,
        }


class Regulator:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.st = RegulatorState()

    def step(self, slot: SlotPlan | None, state: State, now: float | None = None) -> tuple[int | None, str]:
        """Gewenste DVCC-stroom nu, of None als de planner-waarde gewoon geldt."""
        cfg, st = self.cfg, self.st
        now = now if now is not None else time.time()
        if slot is None or slot.dvcc_current is None:
            return None, ""
        plan_a = slot.dvcc_current

        # Alleen regelen tijdens laden van het net; anders mag de zon vol laden
        if slot.ess_state != 9 or state.grid_w is None:
            st.current_a, st.target_a, st.raise_since = plan_a, plan_a, None
            st.car_throttled = False
            st.headroom_w = None
            st.reason = slot.reasons.get("dvcc", "")
            return plan_a, st.reason

        v = cfg.battery_nominal_voltage
        batt_in = max(0.0, state.battery_w or 0.0)
        others = (state.grid_w or 0.0) - batt_in  # alles behalve de accu
        reserve_car = 0.0
        st.car_throttled = False
        if state.zappi_mode == "Fast" and state.zappi_charging and state.zappi_w is not None:
            if state.zappi_w < cfg.zappi_max_w * THROTTLE_FRACTION:
                # Zappi regelt terug: wat hij zou willen trekken telt als bezet
                st.car_throttled = True
                reserve_car = cfg.zappi_max_w - state.zappi_w
        headroom = cfg.grid_max_import_w - cfg.grid_margin_w - others - reserve_car
        st.headroom_w = headroom

        step = max(1, cfg.dvcc_step_a)
        possible_a = max(0.0, headroom) / v
        target = int(min(plan_a, possible_a) // step * step)
        target = max(cfg.dvcc_min_charge_current, min(cfg.dvcc_max_charge_current, target))
        st.target_a = target

        cur = st.current_a if st.current_a is not None else target
        why = f"ruimte {headroom / 1000:.1f} kW" + (" (Zappi regelt terug, auto gaat voor)" if st.car_throttled else "")
        if target < cur:
            new = target
            st.raise_since = None
            why = f"omlaag: {why}"
        elif target >= cur + step:
            if st.raise_since is None:
                st.raise_since = now
            if now - st.raise_since >= RAISE_AFTER:
                new = cur + step  # rustig omhoog, één stap per keer
                st.raise_since = now
                why = f"omhoog: {why}"
            else:
                new = cur
                why = f"wacht {RAISE_AFTER - int(now - st.raise_since)}s voor verhogen: {why}"
        else:
            new = cur
            st.raise_since = None
        st.current_a = new
        st.reason = f"{new} A (plan {plan_a} A), {why}"
        return new, st.reason
