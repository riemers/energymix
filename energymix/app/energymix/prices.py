"""Prijsslots op basis van echte tijdstempels.

De Node-RED-flow ging uit van `prices[uur]`. Dat klopt niet op dagen met 23 of
25 uur (zomer-/wintertijd) en niet bij kwartierprijzen. Hier heeft elk slot een
eigen start en eind, en zoeken we altijd op tijd.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable


@dataclass(frozen=True)
class PriceSlot:
    start: datetime
    end: datetime
    price: float  # €/kWh incl. belastingen (Tibber `total`)
    level: str | None = None
    energy: float | None = None  # kale energieprijs (Tibber `energy`)

    @property
    def minutes(self) -> float:
        return (self.end - self.start).total_seconds() / 60

    def contains(self, t: datetime) -> bool:
        return self.start <= t < self.end


def parse_tibber(price_info: dict[str, Any]) -> list[PriceSlot]:
    """Zet Tibber `priceInfo` (today + tomorrow) om naar gesorteerde slots.

    De lengte van een slot is de afstand tot het volgende slot; het laatste slot
    krijgt dezelfde lengte als het slot ervoor (of 60 min als er maar één is).
    """
    raw = [*(price_info.get("today") or []), *(price_info.get("tomorrow") or [])]
    points: list[tuple] = []
    for p in raw:
        try:
            start = datetime.fromisoformat(p["startsAt"])
            price = float(p["total"])
        except (KeyError, TypeError, ValueError):
            continue
        energy = p.get("energy")
        points.append((start, price, p.get("level"), float(energy) if energy is not None else None))
    points.sort(key=lambda x: x[0])
    return build_slots(points)


def build_slots(points: Iterable[tuple]) -> list[PriceSlot]:
    """Punten zijn (start, prijs, level) of (start, prijs, level, energieprijs)."""
    pts = list(points)
    slots: list[PriceSlot] = []
    for i, (start, price, level, *rest) in enumerate(pts):
        if i + 1 < len(pts):
            end = pts[i + 1][0]
        elif i > 0:
            end = start + (start - pts[i - 1][0])
        else:
            end = start + timedelta(hours=1)
        slots.append(PriceSlot(start, end, price, level, rest[0] if rest else None))
    return slots


def slot_at(slots: list[PriceSlot], t: datetime) -> PriceSlot | None:
    for s in slots:
        if s.contains(t):
            return s
    return None


def cheapest_window(
    slots: list[PriceSlot], minutes: float, not_before: datetime, not_after: datetime | None = None
) -> list[PriceSlot] | None:
    """Goedkoopste aaneengesloten blok van minimaal `minutes` minuten.

    Kandidaten beginnen bij het slot waarin `not_before` valt en eindigen
    uiterlijk op `not_after`. Prijs wordt gewogen naar slotduur, zodat het ook
    werkt met een mix van uur- en kwartierslots.
    """
    cands = [s for s in slots if s.end > not_before and (not_after is None or s.end <= not_after)]
    best: list[PriceSlot] | None = None
    best_cost = float("inf")
    for i in range(len(cands)):
        total_min = 0.0
        cost = 0.0
        for j in range(i, len(cands)):
            if j > i and cands[j].start != cands[j - 1].end:
                break  # gat in de data
            total_min += cands[j].minutes
            cost += cands[j].price * cands[j].minutes
            if total_min >= minutes:
                if cost / total_min < best_cost - 1e-12:
                    best_cost = cost / total_min
                    best = cands[i : j + 1]
                break
    return best


def cheapest_slots_per_day(slots: list[PriceSlot], minutes: float, tz) -> set[datetime]:
    """Per kalenderdag de goedkoopste slots (niet aaneengesloten) tot `minutes`.

    Vervangt de `ps-strategy-lowest-price` node (00:00-00:00, mag splitsen).
    Geeft de starttijden van de gekozen slots terug.
    """
    by_day: dict[Any, list[PriceSlot]] = {}
    for s in slots:
        by_day.setdefault(s.start.astimezone(tz).date(), []).append(s)
    chosen: set[datetime] = set()
    for day_slots in by_day.values():
        total = 0.0
        for s in sorted(day_slots, key=lambda x: (x.price, x.start)):
            if total >= minutes:
                break
            chosen.add(s.start)
            total += s.minutes
    return chosen
