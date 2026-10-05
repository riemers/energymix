"""Snelle regeling van de accu-laadstroom (DVCC) als de accu van het net laadt.

De planner zegt per kwartier hoeveel de accu mag laden. Maar of dat past hangt
af van wat het huis en de auto op dat moment trekken. Deze regelaar kijkt elke
`regulator_seconds` per fase naar de ruimte op de aansluiting:

    vrij(fase) = zekering - marge - (stroom op die fase - eigen deel van de accu)

De krapste fase waar de Victron op laadt bepaalt de laadstroom. Zo gaat het
ook goed als iemand op één fase veel verbruikt. Daarbij:
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
from .phases import battery_ac_limit_w, headroom_a, victron_phase_idx, zappi_phase_idx
from .planner import SlotPlan, State

RAISE_AFTER = 120  # s ruimte nodig voordat we de stroom verhogen
THROTTLE_FRACTION = 0.85  # Zappi trekt < 85% van max op Fast = teruggeregeld


@dataclass
class RegulatorState:
    current_a: int | None = None
    target_a: int | None = None
    headroom_w: float | None = None
    car_throttled: bool = False
    phase_free_a: list[float] = field(default_factory=list)
    basis: str = ""
    reason: str = ""
    raise_since: float | None = None
    history: list[tuple[float, int]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "current_a": self.current_a,
            "target_a": self.target_a,
            "headroom_w": round(self.headroom_w) if self.headroom_w is not None else None,
            "car_throttled": self.car_throttled,
            "phase_free_a": self.phase_free_a,
            "basis": self.basis,
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
        if plan_a == 0:
            # Accu bewaren: bewust 0 A, niets te regelen
            st.current_a, st.target_a, st.raise_since, st.headroom_w, st.phase_free_a = 0, 0, None, None, []
            st.car_throttled = False
            st.reason = slot.reasons.get("dvcc", "")
            return 0, st.reason

        # Alleen regelen tijdens laden van het net; anders mag de zon vol laden
        has_phases = len(state.phase_a) == max(1, cfg.grid_phases) and all(x is not None for x in state.phase_a)
        if slot.ess_state != 9 or (state.grid_w is None and not has_phases):
            st.current_a, st.target_a, st.raise_since = plan_a, plan_a, None
            st.car_throttled = False
            st.headroom_w = None
            st.phase_free_a = []
            st.reason = slot.reasons.get("dvcc", "")
            return plan_a, st.reason

        v = cfg.battery_nominal_voltage
        eff_c = cfg.charge_efficiency if 0 < cfg.charge_efficiency <= 1 else 0.93
        n = max(1, cfg.grid_phases)
        vp, zp = victron_phase_idx(cfg), zappi_phase_idx(cfg)
        batt_ac = max(0.0, state.battery_w or 0.0) / eff_c  # wat de accu nu zelf van het net trekt

        # Stroom per fase zonder de accu zelf
        if has_phases:
            others = [float(x) for x in state.phase_a]  # type: ignore[arg-type]
            for p in vp:
                others[p] -= batt_ac / len(vp) / cfg.grid_voltage
            basis = "gemeten per fase"
        else:
            # Geen fasemeting: rest gelijk verdeeld, auto over zijn eigen fases
            car_w = max(0.0, state.zappi_w or 0.0)
            rest = (state.grid_w or 0.0) - batt_ac - car_w
            others = [rest / n / cfg.grid_voltage] * n
            for p in zp:
                others[p] += car_w / len(zp) / cfg.grid_voltage
            basis = "geen fasemeting: aanname gelijk verdeeld"

        st.car_throttled = False
        if state.zappi_mode == "Fast" and state.zappi_charging and state.zappi_w is not None and zp:
            if state.zappi_w < cfg.zappi_max_w * THROTTLE_FRACTION:
                # Zappi regelt terug: wat hij zou willen trekken telt als bezet op zijn fases
                st.car_throttled = True
                deficit = cfg.zappi_max_w - state.zappi_w
                for p in zp:
                    others[p] += deficit / len(zp) / cfg.grid_voltage

        st.phase_free_a = [round(x, 1) for x in headroom_a(cfg, others)]
        ac_limit = battery_ac_limit_w(cfg, others)
        headroom = ac_limit * eff_c
        st.headroom_w = headroom
        st.basis = basis
        tight = min(vp, key=lambda p: st.phase_free_a[p])

        step = max(1, cfg.dvcc_step_a)
        possible_a = max(0.0, headroom) / v
        target = int(min(plan_a, possible_a) // step * step)
        if possible_a < cfg.dvcc_min_charge_current:
            target = 0  # fase vol: niet laden, ook niet op het minimum
        else:
            target = max(cfg.dvcc_min_charge_current, min(cfg.dvcc_max_charge_current, target))
        st.target_a = target

        cur = st.current_a if st.current_a is not None else target
        why = f"L{tight + 1} nog {st.phase_free_a[tight]:.1f} A vrij ({basis})" + (
            ", Zappi regelt terug: auto gaat voor" if st.car_throttled else ""
        )
        if target == 0:
            why = f"fase vol, accu laadt niet: {why}"
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
