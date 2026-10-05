"""Auto's leren kennen uit de Tesla-gegevens in HA.

- Max actieradius: actieradius / accu% x laadlimiet (bv. 80%), gemiddeld over
  metingen. Meldt de Zappi "Complete", dan telt de actieradius van dat moment
  ook als meting.
- Laadsnelheid (km per uur): uit de "charge rate" van de Tesla tijdens Fast
  laden, of gemeten uit de stijging van de actieradius.
- Laadtijd: tijdens Fast laden de "time to full charge" van de auto zelf.

Geleerde waarden worden in de database bewaard zodat ze een herstart overleven.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

EMA = 0.25  # gewicht van een nieuwe meting
MIN_SOC_FOR_ESTIMATE = 20  # onder 20% is km/% te onnauwkeurig
SAVE_EVERY = 600  # s
LEARN_EVERY = 300  # s


def car_base(car) -> str:
    """'sensor.witte_koets_battery_range' -> 'witte_koets'."""
    eid = car.range_entity or car.cable_entity or ""
    obj = eid.split(".", 1)[-1]
    for suffix in ("_battery_range", "_range", "_charge_cable", "_charger"):
        if obj.endswith(suffix):
            return obj[: -len(suffix)]
    return obj


CANDIDATES = {
    "battery_level": ["sensor.{b}_battery_level", "sensor.{b}_battery", "sensor.{b}_battery_level_percent"],
    "charge_limit": ["number.{b}_charge_limit", "sensor.{b}_charge_limit", "number.{b}_charge_limit_soc"],
    "time_to_full": ["sensor.{b}_time_to_full_charge", "sensor.{b}_time_charge_complete", "sensor.{b}_time_to_full"],
    "charge_rate": ["sensor.{b}_charge_rate", "sensor.{b}_charging_rate"],
}


def discover(states: dict[str, Any], car) -> dict[str, str]:
    """Zoek de Tesla-entities bij een auto (expliciet ingesteld gaat voor)."""
    b = car_base(car)
    out = {}
    for key, patterns in CANDIDATES.items():
        explicit = getattr(car, f"{key}_entity", "") or ""
        if explicit:
            out[key] = explicit
            continue
        for p in patterns:
            eid = p.format(b=b)
            if eid in states:
                out[key] = eid
                break
    return out


def parse_time_to_full(state: str, attrs: dict, now: datetime) -> float | None:
    """Minuten tot vol. Accepteert uren (getal) of een tijdstip (timestamp-sensor)."""
    if state in ("", "unknown", "unavailable", "none", None):
        return None
    try:
        hours = float(state)
        unit = str(attrs.get("unit_of_measurement", "h")).lower()
        return hours if unit in ("min", "minutes") else hours * 60
    except ValueError:
        pass
    try:
        t = datetime.fromisoformat(str(state))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return max(0.0, (t - now).total_seconds() / 60)
    except ValueError:
        return None


@dataclass
class Learned:
    max_range_km: float | None = None
    max_n: int = 0
    speed_kmh: float | None = None
    speed_n: int = 0

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def ema(old: float | None, new: float) -> float:
    return new if old is None else old * (1 - EMA) + new * EMA


class CarLearner:
    def __init__(self, store):
        self.store = store
        self.learned: dict[str, Learned] = {}
        self._anchor: dict[str, tuple[float, float]] = {}  # naam -> (tijd, km) tijdens laden
        self._saved: dict[str, float] = {}
        self._complete_seen: dict[str, bool] = {}
        self._last: dict[str, float] = {}

    def get(self, name: str) -> Learned:
        if name not in self.learned:
            raw = self.store.get_car_learned(name) if self.store else None
            self.learned[name] = Learned(**raw) if raw else Learned()
        return self.learned[name]

    def update(self, name: str, range_km: float | None, soc: float | None, limit: float | None,
               rate_kmh: float | None, charging_fast: bool, complete: bool, now: float | None = None) -> Learned:
        now = now if now is not None else time.time()
        lr = self.get(name)
        # Hooguit eens per 5 minuten leren (de snelle lus draait elke 30 s)
        if now - self._last.get(name, float("-inf")) < LEARN_EVERY and not (complete and not self._complete_seen.get(name)):
            return lr
        self._last[name] = now
        changed = False
        # Max actieradius bij de laadlimiet
        if range_km and soc and soc >= MIN_SOC_FOR_ESTIMATE:
            est = range_km / soc * (limit or 100)
            if 50 < est < 1200:
                lr.max_range_km = ema(lr.max_range_km, est)
                lr.max_n += 1
                changed = True
        elif complete and range_km and not self._complete_seen.get(name):
            lr.max_range_km = ema(lr.max_range_km, range_km)
            lr.max_n += 1
            changed = True
        self._complete_seen[name] = complete
        # Laadsnelheid
        if charging_fast:
            if rate_kmh and 5 < rate_kmh < 200:
                lr.speed_kmh = ema(lr.speed_kmh, rate_kmh)
                lr.speed_n += 1
                changed = True
            elif range_km:
                t0, k0 = self._anchor.setdefault(name, (now, range_km))
                dt_h = (now - t0) / 3600
                if range_km - k0 >= 10 and dt_h >= 1 / 6:
                    lr.speed_kmh = ema(lr.speed_kmh, (range_km - k0) / dt_h)
                    lr.speed_n += 1
                    changed = True
                    self._anchor[name] = (now, range_km)
        else:
            self._anchor.pop(name, None)
        if changed and self.store and now - self._saved.get(name, float("-inf")) >= SAVE_EVERY:
            self.store.save_car_learned(name, lr.to_dict())
            self._saved[name] = now
        return lr
