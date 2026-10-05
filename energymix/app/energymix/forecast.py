"""Verwachtingen per tijdslot: zonne-opbrengst en huisverbruik.

Forecast.Solar geeft in HA alleen dagtotalen ("nog te gaan vandaag", "morgen").
Die verdelen we over de dag volgens de zonnestand op jouw locatie. Heeft de
entity een `detailedForecast`-attribuut (Solcast), dan gebruiken we dat.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from .prices import PriceSlot


def sun_window(day: datetime, lat: float, lon: float) -> tuple[datetime, datetime]:
    """Benadering van zonsopkomst en -ondergang (UTC) voor een datum."""
    doy = day.timetuple().tm_yday
    decl = math.radians(23.44) * math.sin(2 * math.pi * (284 + doy) / 365)
    x = -math.tan(math.radians(lat)) * math.tan(decl)
    x = max(-1.0, min(1.0, x))
    half_day_h = math.degrees(math.acos(x)) / 15
    noon_utc_h = 12 - lon / 15
    base = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    return base + timedelta(hours=noon_utc_h - half_day_h), base + timedelta(hours=noon_utc_h + half_day_h)


def _weight(t: datetime, rise: datetime, set_: datetime) -> float:
    if t <= rise or t >= set_:
        return 0.0
    f = (t - rise) / (set_ - rise)
    return math.sin(math.pi * f) ** 1.5


def _slot_weight(s_start: datetime, s_end: datetime, rise: datetime, set_: datetime) -> float:
    # Integraal benaderen met 5 punten per slot
    n = 5
    step = (s_end - s_start) / n
    return sum(_weight(s_start + step * (i + 0.5), rise, set_) for i in range(n)) * (s_end - s_start).total_seconds() / 3600 / n


def pv_per_slot(
    slots: list[PriceSlot],
    now: datetime,
    tz,
    remaining_today_kwh: float | None,
    tomorrow_kwh: float | None,
    lat: float = 52.1,
    lon: float = 5.1,
    detailed: Iterable[dict[str, Any]] | None = None,
) -> dict[datetime, float]:
    """Verwachte PV-energie (kWh) per slot-start."""
    out = {s.start: 0.0 for s in slots}
    if detailed:
        pts = []
        for d in detailed:
            try:
                pts.append((datetime.fromisoformat(str(d["period_start"])), float(d.get("pv_estimate", 0))))
            except (KeyError, ValueError, TypeError):
                continue
        pts.sort()
        if pts:
            for i, (t, kw) in enumerate(pts):
                t_end = pts[i + 1][0] if i + 1 < len(pts) else t + timedelta(minutes=30)
                for s in slots:
                    overlap = (min(s.end, t_end) - max(s.start, t, now)).total_seconds()
                    if overlap > 0:
                        out[s.start] += kw * overlap / 3600
            return out

    today_local = now.astimezone(tz).date()
    for day_offset, total in ((0, remaining_today_kwh), (1, tomorrow_kwh)):
        if not total or total <= 0:
            continue
        day = today_local + timedelta(days=day_offset)
        rise, set_ = sun_window(datetime(day.year, day.month, day.day), lat, lon)
        day_slots = [s for s in slots if s.start.astimezone(tz).date() == day and s.end > now]
        weights = {s.start: _slot_weight(max(s.start, now), s.end, rise, set_) for s in day_slots}
        wsum = sum(weights.values())
        if wsum <= 0:
            continue
        for k, w in weights.items():
            out[k] += total * w / wsum
    return out


def house_per_slot(slots: list[PriceSlot], tz, profile_w: dict[int, float], default_w: float) -> dict[datetime, float]:
    """Verwacht huisverbruik (kWh) per slot, op basis van gemiddelde per uur van de dag."""
    out = {}
    for s in slots:
        w = profile_w.get(s.start.astimezone(tz).hour, default_w)
        out[s.start] = w / 1000 * s.minutes / 60
    return out
