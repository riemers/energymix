"""Het "verhaal": in een paar zinnen wat er nu gebeurt en wat er komt."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from .planner import ESS_KEEP_CHARGED, Plan, State


def _t(iso_or_dt, tz: ZoneInfo, now: datetime) -> str:
    t = iso_or_dt if isinstance(iso_or_dt, datetime) else datetime.fromisoformat(iso_or_dt)
    local = t.astimezone(tz)
    days = (local.date() - now.astimezone(tz).date()).days
    hm = local.strftime("%H:%M")
    return hm if days <= 0 else f"morgen {hm}" if days == 1 else local.strftime("%a %H:%M")


def _eur(v: float) -> str:
    return f"€{v:.2f}".replace(".", ",")


def price_word(p: float, slots) -> str:
    prices = sorted(s.price for s in slots) or [p]
    if p < 0:
        return "negatief"
    rank = sum(1 for x in prices if x < p) / len(prices)
    return "goedkoop" if rank < 0.25 else "duur" if rank > 0.75 else "gemiddeld"


def tell(plan: Plan | None, state: State | None, tz: ZoneInfo) -> list[str]:
    if not plan or not plan.slots or not state:
        return ["Nog geen plan: wacht op prijzen en Home Assistant."]
    now = plan.created_at
    cur = plan.now or plan.slots[0]
    out: list[str] = []

    out.append(f"Stroom kost nu {_eur(cur.price)} ({price_word(cur.price, plan.slots)}).")

    # Accu nu
    if state.soc is not None:
        if cur.ess_state == ESS_KEEP_CHARGED:
            out.append(f"De accu ({state.soc:.0f}%) laadt van het net: {cur.reasons.get('ess', '')}.")
        elif cur.setpoint_w is not None and cur.setpoint_w < 0:
            out.append(f"De accu ({state.soc:.0f}%) levert {abs(cur.setpoint_w) / 1000:.1f} kW terug: {cur.reasons.get('setpoint', '')}.")
        else:
            out.append(f"De accu ({state.soc:.0f}%) dekt het huis.")

    # Auto
    car = plan.car
    if car.boost:
        out.append("Snel laden staat aan: de auto laadt nu op Fast tot hij vol is.")
    if car.name:
        if cur.zappi_mode == "Fast":
            out.append(f"De {car.name} laadt nu ({cur.reasons.get('zappi', '')}).")
        else:
            nxt = next((s for s in plan.slots if s.zappi_mode == "Fast" and s.start > now), None)
            if nxt:
                out.append(f"De {car.name} ({car.range_km:.0f} km) laadt vanaf {_t(nxt.start, tz, now)} à {_eur(nxt.price)}.")
            elif car.need_km < 1:
                out.append(f"De {car.name} ({car.range_km:.0f} km) is vol.")
        if car.full_at:
            out.append(f"Verwacht vol om {_t(car.full_at, tz, now)}.")
    elif state.car_plugged:
        out.append("Er hangt een auto aan de lader, maar welke is niet herkend.")

    # Komende accu-acties
    upcoming = [s for s in plan.slots if s.start > cur.start]
    ch = next((s for s in upcoming if s.ess_state == ESS_KEEP_CHARGED), None)
    ex = next((s for s in upcoming if s.setpoint_w is not None and s.setpoint_w < 0), None)
    if ch and cur.ess_state != ESS_KEEP_CHARGED:
        out.append(f"Om {_t(ch.start, tz, now)} wordt de accu goedkoop bijgeladen ({_eur(ch.price)}).")
    if ex:
        out.append(f"Om {_t(ex.start, tz, now)} is de prijs {_eur(ex.price)}: dan levert de accu terug.")
    s = plan.summary
    if s.get("saving_eur", 0) >= 0.05:
        out.append(f"Dat scheelt naar verwachting {_eur(s['saving_eur'])} ten opzichte van niets doen.")
    return out
