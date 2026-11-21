"""Voorlopige prijzen voor morgen via de EnergyZero-integratie in Home Assistant.

De beurs (EPEX) publiceert de prijzen van morgen rond 13:00. De Tibber-app laat ze dan al zien, maar
de openbare Tibber-API geeft ze vaak pas tussen 13:30 en 13:45. EnergyZero heeft ze eerder. Dat zijn
kale beursprijzen (incl. btw); Tibber rekent daar een vrijwel vaste opslag bovenop (energiebelasting +
inkoopvergoeding). Die opslag leren we uit de uren van vandaag die in beide bronnen staan.
"""

from __future__ import annotations

import logging
import statistics
from datetime import datetime, timedelta
from typing import Any

from .prices import PriceSlot

log = logging.getLogger(__name__)

PROVISIONAL = "VOORLOPIG"  # level van slots die (nog) niet van Tibber komen
MIN_OVERLAP_HOURS = 6
MAX_SPREAD_EUR = 0.02  # opslag mag per uur niet meer schommelen dan dit, anders niet vertrouwen


def parse_response(resp: Any) -> dict[datetime, float]:
    """`energyzero.get_energy_prices` (incl_vat) -> {start uur: €/kWh}."""
    prices = (resp or {}).get("response", resp or {}).get("prices") or []
    out: dict[datetime, float] = {}
    for p in prices:
        try:
            start = datetime.fromisoformat(str(p.get("start") or p["timestamp"]).replace(" ", "T"))
            out[start] = float(p["price"])
        except (KeyError, TypeError, ValueError):
            continue
    return out


def tibber_markup(tibber: list[PriceSlot], spot: dict[datetime, float]) -> float | None:
    """Mediaan van (Tibber-totaal - EnergyZero) per uur, over de uren die in beide zitten."""
    diffs = []
    for hour, sp in spot.items():
        end = hour + timedelta(hours=1)
        parts = [s for s in tibber if hour <= s.start < end and s.level != PROVISIONAL]
        minutes = sum(s.minutes for s in parts)
        if minutes < 59:
            continue
        avg = sum(s.price * s.minutes for s in parts) / minutes
        diffs.append(avg - sp)
    if len(diffs) < MIN_OVERLAP_HOURS:
        return None
    med = statistics.median(diffs)
    if max(abs(d - med) for d in diffs) > MAX_SPREAD_EUR:
        return None
    return med


def provisional_slots(tibber: list[PriceSlot], spot: dict[datetime, float]) -> list[PriceSlot]:
    """Uurslots na de laatste Tibber-prijs, op basis van EnergyZero + geleerde opslag."""
    if not tibber:
        return []
    markup = tibber_markup(tibber, spot)
    if markup is None:
        return []
    last = tibber[-1].end
    return [
        PriceSlot(h, h + timedelta(hours=1), round(p + markup, 4), PROVISIONAL)
        for h, p in sorted(spot.items())
        if h >= last
    ]
