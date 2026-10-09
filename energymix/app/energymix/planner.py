"""De planner: één plek die beslist.

Puur en zonder I/O: krijgt prijzen, een momentopname van de toestand, de
verwachtingen (zon, huisverbruik) en de config, en geeft per tijdslot terug wat
elk apparaat moet doen, mét de reden.

Volgorde:
1. Auto (heeft altijd voorrang): wat onder het minimum zit moet vóór de
   vertrektijd in de goedkoopste slots; de rest tot vol alleen als het goedkoop is.
2. Accu: simulatie van het laadniveau per slot (zon, huis, auto-eco). Daarna
   greedy: steeds de ene actie (in slot X van het net laden, of in slot Y
   terugleveren) die na verliezen en slijtage het meest oplevert, tot er niets
   meer te winnen valt. Zo komen "niet laden als de zon het toch vult",
   "terugleveren bij een piek en later goedkoop bijladen" en "reserve houden"
   uit dezelfde rekensom.
3. Vertalen naar apparaat-instellingen (ESS-state, DVCC, setpoint, feed-in,
   Zappi-modus, PV-schakelaar), elk met een leesbare reden.
"""

from __future__ import annotations

import copy
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, time, timedelta
from statistics import mean
from zoneinfo import ZoneInfo

from .config import Config
from .phases import battery_ac_limit_w, expected_others_a
from .prices import PriceSlot

ESS_KEEP_CHARGED = 9  # Victron BatteryLife state: "Keep batteries charged"
ESS_OPTIMIZED = 10  # BatteryLife state: "Optimized (without BatteryLife)"

UNAVAILABLE = {"", "unknown", "unavailable", "none", "null"}
EPS = 0.01  # kWh


# --------------------------------------------------------------------------- toestand


@dataclass
class CarState:
    name: str
    max_range_km: float
    cable: str = ""
    location: str = ""
    range_km: str = ""
    kwh_per_km: float = 0.17
    soc: float | None = None  # accu % van de auto
    charge_limit: float | None = None  # laadlimiet %
    time_to_full_min: float | None = None  # volgens de auto
    charge_rate_kmh: float | None = None  # volgens de auto
    learned_max_km: float | None = None
    learned_speed_kmh: float | None = None

    @property
    def max_km(self) -> float:
        return self.learned_max_km or self.max_range_km

    @property
    def connected(self) -> bool:
        return self.cable.strip().lower() in {"connected", "on", "true", "plugged", "charging"}

    @property
    def home(self) -> bool:
        return self.location.strip().lower() in {"home", "thuis"}

    @property
    def range_value(self) -> float | None:
        return _num(self.range_km)


@dataclass
class State:
    soc: float | None = None
    solar_today_kwh: float | None = None
    solar_remaining_kwh: float | None = None
    solar_tomorrow_kwh: float | None = None
    sunchance: float | None = None
    carcharger_mode: str = "auto"  # auto | fast | slow | ecoa
    vannacht: bool = False
    zappi_plug: str = ""
    zappi_status: str = ""
    zappi_mode: str = ""
    cars: list[CarState] = field(default_factory=list)
    # live vermogens (W)
    pv_w: float | None = None
    grid_w: float | None = None
    phase_a: list[float | None] = field(default_factory=list)  # gemeten stroom per fase
    battery_w: float | None = None
    house_w: float | None = None
    zappi_w: float | None = None

    @property
    def car_plugged(self) -> bool:
        return self.zappi_plug.strip() not in UNAVAILABLE and self.zappi_plug != "EV Disconnected"

    @property
    def zappi_charging(self) -> bool:
        return self.zappi_status.strip().lower() in {"charging", "boosting"}

    @property
    def car_full(self) -> bool:
        return self.zappi_status.strip().lower() == "complete"


@dataclass
class Forecast:
    pv_kwh: dict[datetime, float] = field(default_factory=dict)
    house_kwh: dict[datetime, float] = field(default_factory=dict)
    battery_charge_w: float | None = None  # geleerd laadvermogen bij max DVCC


# --------------------------------------------------------------------------- uitvoer


@dataclass
class SlotPlan:
    start: datetime
    end: datetime
    price: float
    sell_price: float = 0.0
    level: str | None = None  # Tibber: VERY_CHEAP, CHEAP, NORMAL, EXPENSIVE, VERY_EXPENSIVE
    pv_on: bool | None = None
    ess_state: int | None = None
    dvcc_current: int | None = None
    setpoint_w: int | None = None
    zappi_mode: str | None = None
    feed_in_disabled: int | None = None  # Hub4/DisableFeedIn: 1 = alleen critical loads
    # verwachting
    soc: float | None = None  # % aan het eind van het slot
    pv_kwh: float = 0.0
    house_kwh: float = 0.0
    car_kwh: float = 0.0
    grid_charge_kwh: float = 0.0
    export_kwh: float = 0.0
    import_kwh: float = 0.0
    car_range_km: float | None = None  # verwachte actieradius aan het eind van het slot
    car_eco_kwh: float = 0.0  # auto op Eco uit accu/zon (niet van het net)
    reasons: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["start"] = self.start.isoformat()
        d["end"] = self.end.isoformat()
        return d


@dataclass
class CarPlan:
    name: str | None = None
    range_km: float | None = None
    need_min_kwh: float = 0.0
    need_full_kwh: float = 0.0
    planned_kwh: float = 0.0
    deadline: datetime | None = None
    full_at: datetime | None = None
    max_range_km: float | None = None
    kwh_per_km: float = 0.17
    need_km: float = 0.0
    need_minutes: float = 0.0
    eco_km: float = 0.0
    eco_reason: str = ""
    boost: bool = False
    max_source: str = "ingesteld"
    charge_limit: float | None = None
    speed_kmh: float | None = None
    speed_source: str = "ingesteld"
    time_source: str = "berekend"
    window_mode: str = "auto"
    window_options: list[dict] = field(default_factory=list)
    sessions: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("deadline", "full_at"):
            d[k] = d[k].isoformat() if d[k] else None
        return d


@dataclass
class Plan:
    created_at: datetime
    slots: list[SlotPlan]
    car: CarPlan
    season: dict
    summary: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    battery_sessions: list[dict] = field(default_factory=list)

    @property
    def now(self) -> SlotPlan | None:
        for s in self.slots:
            if s.start <= self.created_at < s.end:
                return s
        return None

    # compat met v0.1
    @property
    def charge_window(self):
        fast = [s for s in self.slots if s.zappi_mode == "Fast"]
        return (fast[0].start, fast[-1].end) if fast else None

    def to_dict(self) -> dict:
        cw = self.charge_window
        return {
            "created_at": self.created_at.isoformat(),
            "car": self.car.to_dict(),
            "active_car": self.car.name,
            "charge_window": [t.isoformat() for t in cw] if cw else None,
            "season": self.season,
            "summary": self.summary,
            "notes": self.notes,
            "car_sessions": self.car.sessions,
            "battery_sessions": self.battery_sessions,
            "slots": [s.to_dict() for s in self.slots],
        }


# --------------------------------------------------------------------------- hulp


def _num(v) -> float | None:
    try:
        if v is None or str(v).strip().lower() in UNAVAILABLE:
            return None
        return float(str(v).replace(",", "."))
    except ValueError:
        return None


def _fmt(t: datetime, tz: ZoneInfo, now: datetime | None = None) -> str:
    local = t.astimezone(tz)
    hm = local.strftime("%H:%M")
    if now is not None:
        days = (local.date() - now.astimezone(tz).date()).days
        if days == 1:
            return f"{hm} (morgen)"
        if days > 1:
            return f"{hm} (+{days}d)"
    return hm


def _hours(s) -> float:
    return (s.end - s.start).total_seconds() / 3600


def sell_price(cfg: Config, s: PriceSlot) -> float:
    if cfg.export_price == "energy" and s.energy is not None:
        return s.energy
    return s.price


def next_time(now: datetime, tz: ZoneInfo, hhmm: str) -> datetime:
    try:
        h, m = (int(x) for x in hhmm.split(":")[:2])
    except ValueError:
        h, m = 7, 30
    local = now.astimezone(tz)
    t = datetime.combine(local.date(), time(h, m), tzinfo=tz)
    if t <= now:
        t = datetime.combine(local.date() + timedelta(days=1), time(h, m), tzinfo=tz)
    return t


def detect_season(history: list[PriceSlot], tz: ZoneInfo, now: datetime, days: int = 7) -> dict:
    """Zomer- of winterpatroon: is de middag (11-16u) of de nacht (0-6u) goedkoper?

    Kijkt naar de prijzen van de afgelopen `days` dagen plus wat er al bekend is.
    """
    since = now - timedelta(days=days)
    day, night = [], []
    for s in history:
        if s.start < since:
            continue
        h = s.start.astimezone(tz).hour
        if 11 <= h < 16:
            day.append(s.price)
        elif 0 <= h < 6:
            night.append(s.price)
    if not day or not night:
        return {"detected": "unknown", "day_avg": None, "night_avg": None}
    d, n = mean(day), mean(night)
    detected = "day" if d < n - 0.01 else "night" if n < d - 0.01 else "neutral"
    return {"detected": detected, "day_avg": round(d, 4), "night_avg": round(n, 4)}


# --------------------------------------------------------------------------- planner


def make_plan(
    cfg: Config,
    prices: list[PriceSlot],
    state: State,
    now: datetime,
    fc: Forecast | None = None,
    history: list[PriceSlot] | None = None,
) -> Plan:
    tz = ZoneInfo(cfg.timezone)
    fc = fc or Forecast()
    horizon_end = now + timedelta(hours=cfg.horizon_hours)
    slots = [s for s in prices if s.end > now and s.start < horizon_end]
    notes: list[str] = []
    if not slots:
        notes.append("Geen prijsdata voor de komende periode")

    season = detect_season(history or prices, tz, now)
    season["mode"] = cfg.season_mode
    season["effective"] = cfg.season_mode if cfg.season_mode in ("day", "night") else season["detected"]

    plans = [SlotPlan(s.start, s.end, s.price, sell_price(cfg, s), level=s.level) for s in slots]
    for sp, s in zip(plans, slots):
        sp.pv_kwh = fc.pv_kwh.get(s.start, 0.0)
        sp.house_kwh = fc.house_kwh.get(s.start, cfg.house_load_default_w / 1000 * _hours(s))
        # Eerste slot loopt al: alleen het resterende deel telt
        if s.start < now:
            frac = (s.end - now) / (s.end - s.start)
            sp.house_kwh *= frac

    # 1. PV-curtailment
    if cfg.pv_switch_entity:
        for sp in plans:
            sp.pv_on = sp.price >= 0
            sp.reasons["pv"] = (
                f"negatieve prijs €{sp.price:.4f}: panelen uit" if sp.price < 0 else "prijs ≥ 0: panelen aan"
            )
            if sp.price < 0:
                sp.pv_kwh = 0.0

    # 2. Auto
    car = _plan_car(cfg, tz, now, slots, plans, state, notes, season)

    # 3. Accu
    summary: dict = {}
    if cfg.has_battery and state.soc is not None:
        # Wat zou bewaren opleveren? Reken de andere keuze ook door, zodat de schakelaar
        # kan laten zien of het om €5 of om 45 cent gaat.
        alt_plans = copy.deepcopy(plans)
        alt = _plan_battery(cfg, tz, now, slots, alt_plans, state, fc, season, car, allow_hold=not cfg.hold_enabled)
        summary = _plan_battery(cfg, tz, now, slots, plans, state, fc, season, car, allow_hold=cfg.hold_enabled)
        on, off = (summary, alt) if cfg.hold_enabled else (alt, summary)
        on_plans = plans if cfg.hold_enabled else alt_plans
        summary["hold_value_eur"] = round(max(0.0, on.get("saving_eur", 0) - off.get("saving_eur", 0)), 2)
        summary["hold_windows"] = [
            {"start": b["start"], "end": b["end"]} for b in _battery_sessions(on_plans) if b["kind"] == "hold"
        ] if summary["hold_value_eur"] >= 0.01 else []
        summary["runway"] = battery_runway(cfg, tz, now, plans, state.soc)
        if summary.get("hold_slots") and summary.get("empty_why"):
            notes.append(f"Accu bewaren: {summary['empty_why']}")
        if summary.get("grid_charge_skip"):
            notes.append(f"Accu: {summary['grid_charge_skip']}")
        if not cfg.hold_enabled and summary["hold_value_eur"] >= 0.05:
            # Zonder bewaren loopt geladen stroom in de goedkope uren meteen weer weg
            notes.append(
                f"Accu: met \"Accu bewaren\" aan scheelt deze planning €{summary['hold_value_eur']:.2f} "
                "(goedkoop laden en vasthouden tot de dure uren)"
            )
    elif cfg.has_battery:
        notes.append("Accu-SoC onbekend: accu niet gepland")

    # Victron loads: alleen open als accu de auto mag laden (eco) of bij terugleveren
    for sp in plans:
        if sp.feed_in_disabled is None:
            sp.feed_in_disabled = 1
            sp.reasons.setdefault("feed_in", "standaard: alleen critical loads")

    # Nederlandse notatie in de redenen: €0,162 i.p.v. €0.162
    for sp in plans:
        sp.reasons = {k: _nl(v) for k, v in sp.reasons.items()}
    speed_eff = (
        car.need_km / (car.need_minutes / 60) if car.need_minutes > 0.5 and car.need_km > 0.5
        else (car.speed_kmh or cfg.charge_speed_km_per_hour)
    )
    car.sessions = _car_sessions(plans, car, state, speed_eff, cfg.zappi_max_w / 1000)
    return Plan(now, plans, car, season, summary, [_nl(n) for n in notes], _battery_sessions(plans))


ECO_KW = 3.7  # geschat laadvermogen op Eco (zon/accu)
MIN_EXPORT_KWH = 0.5  # minder terugleveren in een slot is de moeite niet
HOLD_MIN_MINUTES = 60  # bewaren altijd in blokken van minstens een uur, geen losse kwartiertjes
HOLD_STICKY_EUR = 0.03
CHARGE_STEP_KWH = 1.0  # laden van het net per portie afwegen  # liever een bewaar-blok verlengen dan een los blok erbij


def _car_sessions(plans: list[SlotPlan], car: CarPlan, state: State, speed: float = 65.0, fast_kw: float = 11.0) -> list[dict]:
    """Laadsessies van de auto: aaneengesloten slots met dezelfde Zappi-modus."""
    if not state.car_plugged:
        return []
    km = car.range_km
    out: list[dict] = []
    cur: dict | None = None
    for sp in plans:
        mode = sp.zappi_mode if sp.zappi_mode in ("Fast", "Eco") else None
        kwh = sp.car_kwh if mode == "Fast" else (sp.car_eco_kwh if mode == "Eco" else 0.0)
        km_before = km
        if km is not None and car.max_range_km:
            # Fast: laadsnelheid in km/u (zoals Node-RED); Eco: via kWh per km
            gain = (kwh / fast_kw * speed) if mode == "Fast" and fast_kw else kwh / car.kwh_per_km
            gain = min(gain, max(0.0, car.max_range_km - km))
            km = km + gain
            sp.car_range_km = round(km)
        if mode and kwh > EPS:
            why = sp.reasons.get("zappi", "")
            kind = (
                "vannacht" if why.startswith("vannacht") else
                "goedkoopst" if why.startswith("goedkoopste") else
                "eco" if why.startswith("ochtend-eco") else
                "snel" if why.startswith("snel laden") else "handmatig"
            )
            if cur and cur["mode"] == mode and cur["end"] == sp.start.isoformat():
                cur["end"] = sp.end.isoformat()
                cur["kwh"] += kwh
                cur["cost"] += kwh * sp.price
                if kind not in cur["kinds"]:
                    cur["kinds"].append(kind)
            else:
                cur = {"mode": mode, "kinds": [kind], "start": sp.start.isoformat(), "end": sp.end.isoformat(),
                       "kwh": kwh, "cost": kwh * sp.price,
                       "range_start_km": round(km_before) if km_before is not None else None, "reason": why}
                out.append(cur)
            cur["range_end_km"] = sp.car_range_km
    for c in out:
        c["kwh"] = round(c["kwh"], 1)
        c["avg_price"] = round(c["cost"] / c["kwh"], 4) if c["kwh"] else None
        c["cost"] = round(c["cost"], 2)
    return out


def _battery_sessions(plans: list[SlotPlan]) -> list[dict]:
    """Accu-acties als blokken: laden van het net, bewaren, terugleveren."""
    out: list[dict] = []
    cur: dict | None = None
    for sp in plans:
        if sp.setpoint_w is not None and sp.setpoint_w < 0:
            kind = "export"
        elif sp.ess_state == ESS_KEEP_CHARGED and sp.dvcc_current == 0:
            kind = "hold"
        elif sp.ess_state == ESS_KEEP_CHARGED:
            kind = "charge"
        else:
            cur = None
            continue
        if cur and cur["kind"] == kind and cur["end"] == sp.start.isoformat():
            cur["end"] = sp.end.isoformat()
            cur["kwh"] += sp.export_kwh if kind == "export" else sp.grid_charge_kwh
            cur["soc_end"] = sp.soc
        else:
            cur = {"kind": kind, "start": sp.start.isoformat(), "end": sp.end.isoformat(),
                   "kwh": sp.export_kwh if kind == "export" else sp.grid_charge_kwh, "soc_end": sp.soc,
                   "reason": sp.reasons.get("setpoint" if kind == "export" else "ess", "")}
            out.append(cur)
    for b in out:
        b["kwh"] = round(b["kwh"], 1)
    return out


_EUR_RE = re.compile(r"(€-?\d+)\.(\d+)")


def _nl(text: str) -> str:
    return _EUR_RE.sub(r"\1,\2", text)


# --------------------------------------------------------------------------- auto


def _active_car(state: State) -> CarState | None:
    for car in state.cars:
        if car.connected and car.home and car.range_value is not None:
            return car
    return None


def _plan_car(cfg, tz, now, slots, plans, state: State, notes, season: dict | None = None) -> CarPlan:
    cp = CarPlan()
    if not cfg.zappi_mode_entity:
        return cp
    mode = state.carcharger_mode

    if mode == "ecoa":
        for sp in plans:
            sp.feed_in_disabled = 0
            sp.reasons["feed_in"] = "EcoA: accu mag de auto laden (all loads)"

    if not state.car_plugged:
        for sp in plans:
            sp.reasons["zappi"] = "geen auto aangesloten"
        return cp

    if cfg.car_boost:
        mode = "boost"
    manual = {"fast": ("Fast", "laadmodus fast"), "slow": ("Eco", "laadmodus slow"), "ecoa": ("Eco", "laadmodus EcoA")}
    if mode in manual:
        m, why = manual[mode]
        for sp in plans:
            sp.zappi_mode, sp.reasons["zappi"] = m, why
            if m == "Fast":
                sp.car_kwh = cfg.zappi_max_w / 1000 * _hours(sp)
        return cp
    if mode not in ("auto", "boost"):
        for sp in plans:
            sp.reasons["zappi"] = f"onbekende laadmodus '{mode}': niets doen"
        return cp

    # Zoals in Node-RED: wat mist er tot de max-actieradius, en hoe lang duurt dat
    # bij de laadsnelheid (km per uur)? Die tijd in de goedkoopste uren.
    active = _active_car(state)
    deadline = next_time(now, tz, cfg.car_ready_time) if state.vannacht else None
    cp.deadline = deadline
    speed = max(1.0, cfg.charge_speed_km_per_hour)

    if active:
        rng = active.range_value or 0.0
        cp.name, cp.range_km = active.name, rng
        kpk = active.kwh_per_km or 0.17
        cp.max_range_km, cp.kwh_per_km = active.max_km, kpk
        cp.max_source = "geleerd" if active.learned_max_km else "ingesteld"
        cp.charge_limit = active.charge_limit
        if active.learned_speed_kmh:
            speed = active.learned_speed_kmh
            cp.speed_source = "gemeten"
        cp.speed_kmh = speed
        cp.need_km = max(0.0, active.max_km - rng)
        cp.need_minutes = min(720.0, cp.need_km / speed * 60)
        # Laadt hij al op Fast? Dan weet de auto zelf het best hoe lang het nog duurt
        if (active.time_to_full_min is not None and state.zappi_mode == "Fast" and state.zappi_charging
                and cp.need_km > 0.5):
            cp.need_minutes = min(720.0, active.time_to_full_min)
            cp.time_source = "auto"
    else:
        cp.need_minutes = float(cfg.default_charge_minutes)
        cp.need_km = cp.need_minutes / 60 * speed
        notes.append("Aangesloten auto niet herkend (kabel/locatie/actieradius): standaard laadduur")
    if state.car_full:
        cp.need_km = cp.need_minutes = 0.0
    cp.need_full_kwh = cp.need_km * cp.kwh_per_km

    if mode == "boost":
        # Handmatig "nu snel laden": Fast vanaf nu tot vol, boven alle planning.
        # Onbekende auto: Fast tot de Zappi "Complete" meldt.
        left = 0.0 if state.car_full else (cp.need_minutes if active else float("inf"))
        for sp in plans:
            mins = min(_eff_minutes(sp, now), left)
            if mins > 0.5:
                sp.zappi_mode = "Fast"
                sp.car_kwh = cfg.zappi_max_w / 1000 * mins / 60
                cp.planned_kwh += sp.car_kwh
                sp.reasons["zappi"] = "snel laden aangezet: nu Fast tot de auto vol is"
                left -= mins
                if active and left <= 0.5:
                    cp.full_at = max(sp.start, now) + timedelta(minutes=mins)
            else:
                sp.zappi_mode = "Eco+"
                sp.reasons["zappi"] = "snel laden: auto vol, daarna Eco+"
        cp.boost = True
        return cp

    # a. Ochtend-eco: accu (al behoorlijk vol) + een echte zonnedag -> auto op Eco,
    #    Victron op all loads. Wat dat oplevert gaat af van de Fast-laadtijd.
    eco_ok, eco_why = _eco_conditions(cfg, state, season or {})
    cp.eco_reason = eco_why
    eco_km = 0.0
    if eco_ok and cp.need_km > 0.5:
        today = now.astimezone(tz).date()
        for sp in plans:
            local = sp.start.astimezone(tz)
            if local.date() != today or not cfg.eco_morning_start_hour <= local.hour < cfg.eco_morning_end_hour:
                continue
            if eco_km >= cp.need_km - 0.5:
                break
            kwh = ECO_KW * _hours(sp) * (((sp.end - now) / (sp.end - sp.start)) if sp.start < now else 1)
            kwh = min(kwh, (cp.need_km - eco_km) * cp.kwh_per_km)
            sp.zappi_mode = "Eco"
            sp.car_eco_kwh = kwh
            sp.feed_in_disabled = 0
            sp.reasons["zappi"] = sp.reasons["feed_in"] = f"ochtend-eco: {eco_why}"
            eco_km += kwh / cp.kwh_per_km
    cp.eco_km = eco_km

    # b. Rest in de goedkoopste uren (Fast van het net)
    base = [sp for sp in plans if (deadline is None or sp.start < deadline) and sp.zappi_mode != "Eco"]
    rest_km = max(0.0, cp.need_km - eco_km)
    left = (cp.need_minutes * rest_km / cp.need_km) if cp.need_km else 0.0
    # Vergelijk: goedkoopste blok 's nachts vs overdag (binnen de bekende prijzen)
    if left > 0.5:
        for kind in ("night", "day"):
            w = _best_window([sp for sp in base if _part_of_day(sp, tz) == kind], left, now)
            if w:
                starts = sorted(w[0])
                last = max(starts)
                end = next(sp for sp in base if sp.start == last)
                end_t = max(end.start, now) + timedelta(minutes=w[0][last])
                cp.window_options.append({
                    "kind": kind, "start": starts[0].isoformat(), "end": end_t.isoformat(), "avg_price": round(w[1], 4),
                })
    cp.window_mode = cfg.car_window
    cands = base if cfg.car_window not in ("night", "day") else [sp for sp in base if _part_of_day(sp, tz) == cfg.car_window]
    chosen: dict[datetime, float] = {}  # slot -> minuten laden
    if left > 0.5:
        # Eén aaneengesloten blok (zoals Node-RED): geen losse kwartieren, geen gependel
        best = _best_window(cands, left, now)
        if best and state.zappi_mode == "Fast" and state.zappi_charging:
            # Laadt hij al? Dan doorladen, tenzij een later blok echt goedkoper is
            from_now = _best_window(cands, left, now, start_now=True)
            if from_now and from_now[1] <= best[1] + CAR_STICKY_EUR:
                best = from_now
        if best:
            chosen = best[0]
            left = 0.0
        else:
            # Past niet als één blok (bv. gat in de data of eco ertussen): goedkoopste slots
            for sp in sorted(cands, key=lambda s: (s.price, s.start)):
                if left <= 0.5:
                    break
                mins = min(_eff_minutes(sp, now), left)
                if mins <= 0:
                    continue
                chosen[sp.start] = mins
                left -= mins
    if left > 0.5:
        where = f"vóór {_fmt(deadline, tz, now)}" if deadline else "binnen de bekende prijzen"
        notes.append(f"Auto: {left:.0f} min laden past niet {where}")

    need_txt = (
        f"{rest_km:.0f} km ({_dur(cp.need_minutes * rest_km / cp.need_km if cp.need_km else 0)}"
        + (", volgens de auto" if cp.time_source == "auto" else "") + ")"
        + (f", na {eco_km:.0f} km eco" if eco_km > 0.5 else "")
    )
    for sp in plans:
        if sp.start not in chosen:
            continue
        mins = chosen[sp.start]
        sp.zappi_mode = "Fast"
        start_at = max(sp.start, now)
        sp.car_kwh = cfg.zappi_max_w / 1000 * mins / 60
        cp.planned_kwh += sp.car_kwh
        sp.reasons["zappi"] = (
            f"vannacht: vol vóór {_fmt(deadline, tz, now)}, goedkoopste blok voor {need_txt} (€{sp.price:.3f})"
            if deadline
            else f"goedkoopste blok {WINDOW_TXT.get(cfg.car_window, '')}voor {need_txt} (€{sp.price:.3f})"
        )
        cp.full_at = start_at + timedelta(minutes=mins)
    if not chosen and eco_km > 0.5 and eco_km >= cp.need_km - 0.5:
        last = max((sp for sp in plans if sp.zappi_mode == "Eco"), key=lambda x: x.end)
        cp.full_at = last.end
    # Overige slots: Eco+ (alleen zonne-overschot)
    for sp in plans:
        if sp.zappi_mode is None:
            sp.zappi_mode = "Eco+"
            sp.reasons["zappi"] = "Eco+: alleen zonne-overschot" + ("" if cp.need_km > 0.5 else " (auto vol)")
    return cp


WINDOW_TXT = {"night": "'s nachts ", "day": "overdag "}
CAR_STICKY_EUR = 0.01  # doorladen tenzij een later blok meer dan 1 ct/kWh goedkoper is


NIGHT_START, DAY_START = 22, 7  # 's nachts 22-07, overdag 07-19 (19-22 telt als avond)


def _part_of_day(sp: SlotPlan, tz) -> str:
    h = sp.start.astimezone(tz).hour
    if h >= NIGHT_START or h < DAY_START:
        return "night"
    if h < 19:
        return "day"
    return "evening"


def _eff_minutes(sp: SlotPlan, now: datetime) -> float:
    """Minuten van het slot die nog komen (het lopende slot is deels voorbij)."""
    return max(0.0, (sp.end - max(sp.start, now)).total_seconds() / 60)


def _best_window(cands: list[SlotPlan], need_min: float, now: datetime, start_now: bool = False):
    """Goedkoopste aaneengesloten blok van `need_min` minuten.

    Geeft ({slot_start: minuten}, gemiddelde prijs) of None. Met start_now alleen
    het blok dat in het eerste slot begint.
    """
    best = None
    starts = range(1) if start_now else range(len(cands))
    for i in starts:
        if i >= len(cands):
            break
        used: dict[datetime, float] = {}
        left, cost = need_min, 0.0
        for j in range(i, len(cands)):
            if j > i and cands[j].start != cands[j - 1].end:
                break
            m = min(_eff_minutes(cands[j], now), left)
            if m <= 0:
                if j == i:
                    break
                continue
            used[cands[j].start] = m
            cost += cands[j].price * m
            left -= m
            if left <= 0.5:
                avg = cost / need_min
                if best is None or avg < best[1] - 1e-9:
                    best = (used, avg)
                break
    return best


def _dur(minutes: float) -> str:
    m = int(round(minutes))
    return f"{m} min" if m < 60 else f"{m // 60}u{m % 60:02d}"


def _eco_conditions(cfg, state: State, season: dict) -> tuple[bool, str]:
    """Mag de auto vandaag 's ochtends op Eco uit de accu laden?

    Alleen als de accu al behoorlijk vol is én het een echte zonnedag wordt:
    dan is de accu later op de dag weer vol (of goedkoop bij te laden).
    """
    if season.get("effective") == "night":
        return False, "winterpatroon: geen ochtend-eco"
    if state.car_full:
        return False, "auto vol"
    soc = state.soc
    if soc is None or soc <= cfg.eco_battery_soc_min:
        return False, f"accu {soc or 0:.0f}% ≤ {cfg.eco_battery_soc_min:.0f}%"
    solar = state.solar_today_kwh or 0
    if solar < cfg.eco_solar_min:
        return False, f"zon vandaag {solar:.0f} kWh < {cfg.eco_solar_min:.0f} kWh"
    if state.sunchance is not None and state.sunchance < cfg.eco_sunchance_min:
        return False, f"zonkans {state.sunchance:.0f}% < {cfg.eco_sunchance_min:.0f}%"
    return True, f"accu {soc:.0f}% en zon vandaag {solar:.0f} kWh: auto uit accu/zon, Victron all loads"


# --------------------------------------------------------------------------- accu


RUNWAY_DAYS = 7
# (huis x, zon x): verwacht, krap (meer verbruik, minder zon) en ruim
RUNWAY_CASES = {"expected": (1.0, 1.0), "early": (1.2, 0.7), "late": (0.85, 1.3)}


def battery_runway(cfg, tz, now: datetime, plans: list[SlotPlan], soc: float) -> dict:
    """Wanneer is de accu leeg (ESS-minimum) als er geen auto laadt en Energymix niets stuurt?

    Alleen huis en zon: de zon laadt bij, het huis trekt eraf. Binnen de planning met de
    prognose; daarna herhaalt hij het laatst bekende dagpatroon (verbruik en zon per uur).
    Met marge: krap = 20% meer verbruik en 30% minder zon, ruim = 15% minder en 30% meer zon.
    """
    cap = cfg.battery_capacity_kwh
    if not plans or not cap:
        return {}
    eff_c = cfg.charge_efficiency if 0 < cfg.charge_efficiency <= 1 else 0.93
    eff_rt = cfg.roundtrip_efficiency if 0 < cfg.roundtrip_efficiency <= 1 else 0.85
    eff_d = min(1.0, eff_rt / eff_c)
    floor_e = cap * 0.10

    # Stappen: (start, uren, huis kW, zon kW); na de planning het laatst bekende patroon per uur
    steps = []
    pattern: dict[int, tuple[float, float]] = {}
    for sp in plans:
        h = _hours(sp)
        start = max(sp.start, now)
        hrs = (sp.end - start).total_seconds() / 3600
        if h <= 0 or hrs <= 0:
            continue
        house_kw, pv_kw = sp.house_kwh / h, sp.pv_kwh / h
        steps.append((start, hrs, house_kw, pv_kw))
        pattern[sp.start.astimezone(tz).hour] = (house_kw, pv_kw)
    t = plans[-1].end
    end = now + timedelta(days=RUNWAY_DAYS)
    default = (cfg.house_load_default_w / 1000, 0.0)
    while t < end:
        house_kw, pv_kw = pattern.get(t.astimezone(tz).hour, default)
        steps.append((t, 1.0, house_kw, pv_kw))
        t += timedelta(hours=1)

    out: dict = {"basis_until": plans[-1].end.isoformat()}
    for name, (fh, fp) in RUNWAY_CASES.items():
        e = cap * soc / 100
        at_ = None
        if e <= floor_e:
            at_ = now
        for start, hrs, house_kw, pv_kw in steps:
            if at_ is not None:
                break
            net_kw = pv_kw * fp - house_kw * fh
            if net_kw >= 0:
                e = max(e, min(cap, e + net_kw * hrs * eff_c))  # zon laadt bij tot vol
                continue
            drain = -net_kw / eff_d  # kWh uit de accu per uur
            left_h = (e - floor_e) / drain
            if left_h <= hrs:
                at_ = start + timedelta(hours=left_h)
            e -= drain * hrs
        out[name] = at_.isoformat() if at_ else None
    return out


@dataclass
class _Sim:
    soc_e: list[float]
    cost: list[float]
    imp: list[float]
    exp: list[float]
    terminal_e: float


def _plan_battery(cfg, tz, now, slots, plans: list[SlotPlan], state: State, fc: Forecast, season, car: CarPlan,
                  allow_hold: bool = True) -> dict:
    cap = cfg.battery_capacity_kwh
    eff_c = cfg.charge_efficiency if 0 < cfg.charge_efficiency <= 1 else 0.93
    eff_rt = cfg.roundtrip_efficiency if 0 < cfg.roundtrip_efficiency <= 1 else 0.85
    eff_d = min(1.0, eff_rt / eff_c)
    floor_e = cap * 0.10  # Victron ESS minimum, voor het huis
    reserve_e = cap * cfg.battery_reserve_soc / 100
    target_e = cap * cfg.battery_target_soc / 100
    e0 = cap * state.soc / 100
    # Laadvermogen van de accu: wat de Victron kan (DVCC max). Het "geleerde" vermogen uit de
    # statistiek is meestal gemeten terwijl de zon laadde en dus te laag om mee te plannen.
    charge_w = cfg.dvcc_max_charge_current * cfg.battery_nominal_voltage * eff_c
    n = len(plans)

    # Laadvermogen per slot: begrensd door de aansluiting als de auto laadt
    charge_cap = []
    for sp in plans:
        h = _hours(sp)
        car_w = sp.car_kwh / h * 1000 if h else 0
        house_w = sp.house_kwh / h * 1000 if h else 0
        pv_w = sp.pv_kwh / h * 1000 if h else 0
        ac_limit = battery_ac_limit_w(cfg, expected_others_a(cfg, house_w, car_w, pv_w))
        charge_cap.append(max(0.0, min(charge_w, ac_limit * eff_c)) / 1000 * h)
    max_e = [cap if sp.price < 0 else target_e for sp in plans]
    export_cap = [
        (cfg.export_max_w / 1000 * _hours(sp)) if (cfg.export_enabled and sp.car_kwh <= EPS and sp.zappi_mode != "Fast") else 0.0
        for sp in plans
    ]
    # Waarde van energie die aan het eind nog in de accu zit: wat die later bespaart
    # (goedkope prijs, na ontlaadverlies). Voorzichtig gerekend, anders lijkt "bewaren"
    # tot na de planning altijd winst en gaat hij 's nachts zonder reden bewaren.
    q = sorted(sp.price for sp in plans) or [0.2]
    terminal_value = q[len(q) // 4] * eff_d

    # Ochtend-eco (door de autoplanning gekozen): accu/zon → auto
    eco = [sp.car_eco_kwh for sp in plans]

    gc = [0.0] * n
    ex = [0.0] * n
    hold = [False] * n  # accu niet ontladen: huis van het net (bewaren voor later)
    spill = [min(sp.sell_price, sl.energy if sl.energy is not None else sp.price - cfg.energy_tax_eur)
             for sp, sl in zip(plans, slots)]

    def simulate() -> _Sim:
        e = e0
        soc_e, cost, imp_l, exp_l = [], [], [], []
        for i, sp in enumerate(plans):
            # Laadt de auto, dan pakt hij de zon eerst; de rest komt van het net
            # (feed-in dicht: de accu levert niet aan de auto). Pas wat daarna aan
            # zon over is gaat naar huis en accu.
            pv_car = min(sp.pv_kwh, sp.car_kwh)
            imp = sp.car_kwh - pv_car
            exp = 0.0
            net = sp.pv_kwh - pv_car - sp.house_kwh - eco[i]
            ch_cap = charge_cap[i] if sp.car_kwh > EPS else charge_w / 1000 * _hours(sp)
            used = 0.0
            if net >= 0:
                room = max(0.0, (max(max_e[i], e) - e) / eff_c)
                ch = min(net, room, ch_cap)
                e += ch * eff_c
                used = ch
                exp += net - ch
            else:
                need = -net
                if hold[i] or gc[i] > EPS:
                    imp += need  # accu bewaart (of laadt): huis van het net
                else:
                    avail = max(0.0, (e - floor_e) * eff_d)
                    dis = min(need, avail)
                    e -= dis / eff_d
                    imp += need - dis
            g = min(gc[i], max(0.0, (max_e[i] - e) / eff_c), max(0.0, charge_cap[i] - used))
            e += g * eff_c
            imp += g
            x = min(ex[i], max(0.0, (e - reserve_e) * eff_d), export_cap[i])
            e -= x / eff_d
            exp += x
            # Zonnestroom die over loopt omdat de accu vol is: kale prijs (niet op rekenen);
            # bewust terugleveren: de ingestelde terugleverprijs
            c = imp * sp.price - (exp - x) * spill[i] - x * sp.sell_price
            soc_e.append(e)
            cost.append(c)
            imp_l.append(imp)
            exp_l.append(exp)
        return _Sim(soc_e, cost, imp_l, exp_l, e)

    def total(sim: _Sim) -> float:
        return sum(sim.cost) - sim.terminal_e * terminal_value

    base = simulate()
    base_total = total(base)
    # Wanneer raakt de accu leeg als we niets doen? En waarom (verbruik vs zon tot dan)?
    empty_idx = next((i for i in range(n) if base.soc_e[i] <= floor_e + 0.05 and plans[i].house_kwh > plans[i].pv_kwh), None)
    upto = empty_idx + 1 if empty_idx is not None else n
    why_empty = ""
    if empty_idx is not None:
        why_empty = (
            f"; zonder bewaren is de accu om {_fmt(plans[empty_idx].start, tz, now)} op {floor_e / cap * 100:.0f}% "
            f"(tot dan huis {sum(p.house_kwh for p in plans[:upto]):.0f} kWh"
            + (f", auto eco {sum(eco[:upto]):.0f} kWh" if sum(eco[:upto]) > 0.5 else "")
            + f", zon {sum(p.pv_kwh for p in plans[:upto]):.0f} kWh)"
        )
    cur = base
    cur_total = base_total
    charge_why: dict[int, str] = {}
    export_why: dict[int, str] = {}
    hold_why: dict[int, str] = {}

    step_charge = [min(charge_cap[i], charge_w / 1000 * _hours(plans[i])) for i in range(n)]
    # Laden in kleine porties: vaak loont alleen de eerste kWh (die de avondpiek dekt) en de rest
    # niet (die vervangt goedkopere nachtstroom). Met een heel slot tegelijk middelde dat weg.
    # Minstens de minimale laadstroom, anders valt het later weg als "mini-actie".
    min_e = [cfg.dvcc_min_charge_current * cfg.battery_nominal_voltage * eff_c / 1000 * _hours(sp) for sp in plans]
    step_small = [min(step_charge[i], max(CHARGE_STEP_KWH, min_e[i] * 1.05)) for i in range(n)]
    # Alleen zinvolle kandidaten proberen: laden in de goedkoopste helft, leveren in de duurste
    by_price = sorted(p.price for p in plans)
    by_sell = sorted(p.sell_price for p in plans)
    cheap_limit = by_price[len(by_price) // 2] if by_price else 0
    sell_limit = by_sell[(len(by_sell) * 2) // 3] if by_sell else 0
    # Tijdens ochtend-eco levert de accu aan de auto: dan niet bewaren of van het net laden
    charge_idx = [i for i in range(n) if (plans[i].price <= cheap_limit or plans[i].price < 0) and eco[i] <= EPS]
    export_idx = [i for i in range(n) if export_cap[i] > EPS and plans[i].sell_price >= sell_limit]
    hold_ok = set(i for i in charge_idx if plans[i].house_kwh > plans[i].pv_kwh + EPS)
    hold_blocks: list[list[int]] = []
    for i in range(n):
        block, j = [], i
        while j < n and j in hold_ok and (not block or plans[j].start == plans[block[-1]].end):
            block.append(j)
            if (plans[j].end - plans[i].start).total_seconds() >= HOLD_MIN_MINUTES * 60:
                hold_blocks.append(block)
                break
            j += 1
    def via_export(sim: _Sim, skip, applied: float) -> bool:
        """Komt de winst vooral uit extra terugleveren later? Dan is het eigenlijk
        inkopen om te verkopen, en geldt de (hogere) drempel voor terugleveren."""
        extra = sum(sim.exp[k] - cur.exp[k] for k in range(n) if k not in skip)
        return extra > 0.5 * applied * eff_c * eff_d

    # Laden én bewaren in één keer. De greedy hieronder probeert één actie tegelijk, en
    # dan levert "'s nachts goedkoop laden" niets op: de accu loopt in de goedkope uren
    # erna (de ochtend) gewoon weer leeg. Pas samen met "niet ontladen zolang het goedkoop
    # is" komt de energie bij de dure piek terecht. Daarom eerst een eenvoudige regel
    # doorrekenen: B kWh in de goedkoopste uren laden, en de accu alleen gebruiken boven
    # prijs d. Alle combinaties van B en d, de beste is het startpunt voor de greedy.
    seed_best = None  # (per_kwh, gain, gc, hold, sim)
    if (cfg.grid_charge_enabled and cap) and charge_idx:
        cheap_first = sorted(charge_idx, key=lambda k: (plans[k].price, k))
        room_e = max(0.0, (target_e - floor_e) / eff_c)
        budgets = [1.0, 2.0, 3.0] + [room_e * f for f in (0.1, 0.2, 0.3, 0.4, 0.5, 0.65, 0.8, 1.0)]
        levels = sorted({p.price for p in plans})
        cutoffs = [None]
        if allow_hold:
            cutoffs += sorted({levels[int(len(levels) * f)] for f in (0.3, 0.4, 0.5, 0.6, 0.7, 0.8)})
        min_run = timedelta(minutes=HOLD_MIN_MINUTES)
        for d in cutoffs:
            hold_try = [False] * n
            if d is not None:
                for k in range(n):
                    hold_try[k] = plans[k].price < d and plans[k].house_kwh > plans[k].pv_kwh + EPS and eco[k] <= EPS
                # Alleen blokken van minstens een uur
                k = 0
                while k < n:
                    if not hold_try[k]:
                        k += 1
                        continue
                    j = k
                    while j + 1 < n and hold_try[j + 1] and plans[j + 1].start == plans[j].end:
                        j += 1
                    if plans[j].end - plans[k].start < min_run:
                        for m in range(k, j + 1):
                            hold_try[m] = False
                    k = j + 1
            for budget in budgets:
                gc_try = [0.0] * n
                left = budget
                for k in cheap_first:
                    if left <= EPS:
                        break
                    gc_try[k] = min(step_charge[k], left)
                    left -= gc_try[k]
                gc[:], hold[:] = gc_try, hold_try
                sim = simulate()
                touched = [k for k in range(n) if gc_try[k] > EPS or hold_try[k]]
                applied = sum(max(0.0, sim.imp[k] - cur.imp[k]) for k in touched)
                gain = cur_total - total(sim)
                if applied > EPS and gain > 0.01:
                    per_kwh = gain / applied
                    threshold = cfg.arbitrage_min_spread
                    if via_export(sim, set(touched), applied):
                        threshold = max(threshold, cfg.export_min_spread)
                    if per_kwh > threshold and (seed_best is None or gain > seed_best[1]):
                        seed_best = (per_kwh, gain, gc_try, hold_try, sim)
                    elif seed_best is None or seed_best[0] <= threshold:
                        # Niet goed genoeg: onthouden voor de uitleg waarom er niet geladen wordt
                        if seed_best is None or per_kwh > seed_best[0]:
                            seed_best = (per_kwh, gain, None, None, None)
        gc[:], hold[:] = [0.0] * n, [False] * n
        if seed_best and seed_best[2] is not None:
            per_kwh, _, gc_try, hold_try, sim = seed_best
            gc[:], hold[:] = gc_try, hold_try
            # Waar wordt de energie gebruikt? Het slot dat het meest goedkoper wordt.
            touched = {k for k in range(n) if gc[k] > EPS or hold[k]}
            diffs = [(cur.cost[j] - sim.cost[j], j) for j in range(n) if j not in touched]
            j = max(diffs)[1] if diffs else None
            use = f"bespaart om {_fmt(plans[j].start, tz, now)} (€{plans[j].price:.3f})" if j is not None else "bespaart later"
            for k in range(n):
                if gc[k] > EPS:
                    charge_why[k] = (
                        f"negatieve prijs €{plans[k].price:.4f}: accu laden" if plans[k].price < 0
                        else f"laden à €{plans[k].price:.3f}, {use}: +€{per_kwh:.2f}/kWh"
                    )
                elif hold[k]:
                    hold_why[k] = f"accu bewaren: huis nu van het net (€{plans[k].price:.3f}), {use}: +€{per_kwh:.2f}/kWh"
            cur, cur_total = sim, total(sim)

    for _ in range(300):
        best = None
        for i in charge_idx:
            sp = plans[i]
            # Laden van het net
            if (cfg.grid_charge_enabled or sp.price < 0) and gc[i] < step_charge[i] - EPS:
                # Negatieve prijs: meteen vol; anders in porties (de eerste portie minstens de minimale stroom)
                delta = step_charge[i] - gc[i] if sp.price < 0 else min(step_charge[i] - gc[i], step_small[i])
                gc[i] += delta
                sim = simulate()
                gc[i] -= delta
                gain = cur_total - total(sim)
                applied = sim.imp[i] - cur.imp[i]
                if applied > EPS:
                    per_kwh = gain / applied
                    threshold = 0.0 if sp.price < 0 else cfg.arbitrage_min_spread
                    if sp.price >= 0 and via_export(sim, (i,), applied):
                        threshold = max(threshold, cfg.export_min_spread)
                    if per_kwh > threshold and (best is None or per_kwh > best[0]):
                        best = (per_kwh, "gc", i, delta, sim, per_kwh)
        for block in hold_blocks if allow_hold else []:
            # Bewaren: een heel blok (min. een uur) het huis van het net, accu sparen.
            # Per blok i.p.v. per kwartier, anders kiest hij losse goedkope kwartiertjes.
            new_slots = [k for k in block if not hold[k]]
            if not new_slots or any(gc[k] > EPS for k in block):
                continue
            for k in new_slots:
                hold[k] = True
            sim = simulate()
            for k in new_slots:
                hold[k] = False
            gain = cur_total - total(sim)
            applied = sum(sim.imp[k] - cur.imp[k] for k in new_slots)
            if applied > EPS:
                per_kwh = gain / applied
                # Sluit het aan op een blok dat al bewaart, dan liever dat verlengen:
                # één lang blok i.p.v. een paar losse (scheelt geschakel)
                touches = (block[0] > 0 and hold[block[0] - 1]) or (block[-1] + 1 < n and hold[block[-1] + 1]) or any(
                    hold[k] for k in block
                )
                rank = per_kwh + (HOLD_STICKY_EUR if touches else 0.0)
                threshold = cfg.arbitrage_min_spread
                if via_export(sim, block, applied):
                    threshold = max(threshold, cfg.export_min_spread)
                if per_kwh > threshold and (best is None or rank > best[0]):
                    best = (rank, "hold", new_slots, 0.0, sim, per_kwh)
        for i in export_idx:
            # Terugleveren
            if ex[i] < export_cap[i] - EPS:
                delta = export_cap[i] - ex[i]
                ex[i] += delta
                sim = simulate()
                ex[i] -= delta
                gain = cur_total - total(sim)
                applied = sim.exp[i] - cur.exp[i]
                if applied > EPS:
                    per_kwh = gain / applied
                    if per_kwh > cfg.export_min_spread and (best is None or per_kwh > best[0]):
                        best = (per_kwh, "ex", i, delta, sim, per_kwh)
        if best is None:
            break
        _, kind, i, delta, sim, per_kwh = best
        # Waar zit het effect? Bij laden: het slot dat het meest goedkoper wordt;
        # bij leveren: het slot dat duurder wordt (daar wordt later bijgekocht)
        held = i if kind == "hold" else [i]
        diffs = [(cur.cost[j] - sim.cost[j], j) for j in range(n) if j not in held]
        if kind in ("gc", "hold"):
            _, j = max(diffs) if diffs else (0, i)
        else:
            d, j = min(diffs) if diffs else (0, i)
            if d > -EPS:
                j = None
        exp_gain = [(sim.exp[k] - cur.exp[k], k) for k in range(n) if k not in held]
        sells_later = kind in ("gc", "hold") and via_export(sim, held, sum(sim.imp[k] - cur.imp[k] for k in held))
        if sells_later:
            j = max(exp_gain)[1]
            use = f"meer terugleveren om {_fmt(plans[j].start, tz, now)} (€{plans[j].sell_price:.3f})"
        elif kind in ("gc", "hold") and j is not None:
            use = f"accu dekt om {_fmt(plans[j].start, tz, now)} (€{plans[j].price:.3f})"
        if kind == "hold":
            for k in held:
                hold[k] = True
                hold_why[k] = (
                    f"accu bewaren: huis nu van het net (€{plans[k].price:.3f}), {use}: +€{per_kwh:.2f}/kWh"
                    + ("" if sells_later else why_empty)
                )
        elif kind == "gc":
            gc[i] += delta
            if plans[i].price < 0:
                charge_why[i] = f"negatieve prijs €{plans[i].price:.4f}: accu laden"
            else:
                charge_why[i] = (
                    f"laden à €{plans[i].price:.3f}, "
                    + (use if sells_later else f"bespaart om {_fmt(plans[j].start, tz, now)} (€{plans[j].price:.3f})")
                    + f": +€{per_kwh:.2f}/kWh"
                )
        else:
            ex[i] += delta
            later = (
                f"later bijkopen om {_fmt(plans[j].start, tz, now)} à €{plans[j].price:.3f}"
                if j is not None
                else f"later goedkoop bijladen (≈€{terminal_value:.3f})"
            )
            export_why[i] = f"terugleveren à €{plans[i].sell_price:.3f}, {later}: +€{per_kwh:.2f}/kWh"
        cur, cur_total = sim, total(sim)

    # Geen mini-acties: een kwartier laden onder de minimale laadstroom, of een
    # restje terugleveren, is de moeite niet en geeft alleen geschakel.
    v_nom = cfg.battery_nominal_voltage
    for _ in range(n):
        tiny = None
        for i in range(n):
            h = _hours(plans[i])
            g = min(gc[i], cur.imp[i])
            if g > EPS and h and g * eff_c / h * 1000 / v_nom < cfg.dvcc_min_charge_current:
                tiny = ("gc", i)
                break
            if ex[i] > EPS and 0 < cur.exp[i] and ex[i] < MIN_EXPORT_KWH:
                tiny = ("ex", i)
                break
        if not tiny:
            break
        kind, i = tiny
        if kind == "gc":
            gc[i] = 0.0
            charge_why.pop(i, None)
        else:
            ex[i] = 0.0
            export_why.pop(i, None)
        cur = simulate()
        cur_total = total(cur)

    # Niet van het net geladen? Zeg dan wat het beste laadmoment zou opleveren tegenover de
    # drempel, anders lijkt het in de herfst (kleine verschillen dag/nacht) alsof hij niets doet.
    skip_why = ""
    if cfg.grid_charge_enabled and not any(gc[i] > EPS for i in range(n)):
        tried = None
        for i in charge_idx:
            if plans[i].price < 0 or step_charge[i] <= EPS:
                continue
            gc[i] = step_small[i]
            sim = simulate()
            gc[i] = 0.0
            applied = sim.imp[i] - cur.imp[i]
            if applied > EPS:
                per_kwh = (cur_total - total(sim)) / applied
                if tried is None or per_kwh > tried[0]:
                    tried = (per_kwh, i)
        if seed_best and seed_best[2] is None and seed_best[0] > 0 and (not tried or seed_best[0] >= tried[0]):
            skip_why = (
                f"laden in de goedkoopste uren{' en bewaren voor de piek' if allow_hold else ''} levert na verliezen "
                f"+€{seed_best[0]:.3f}/kWh op voor eigen gebruik, minder dan de drempel €{cfg.arbitrage_min_spread:.2f}/kWh: niet geladen"
            )
        elif tried and tried[0] > 0:
            per_kwh, i = tried
            skip_why = (
                f"van het net laden om {_fmt(plans[i].start, tz, now)} (€{plans[i].price:.3f}) levert na verliezen "
                f"+€{per_kwh:.3f}/kWh op voor eigen gebruik, minder dan de drempel €{cfg.arbitrage_min_spread:.2f}/kWh: niet geladen"
            )

    # Vertalen naar instellingen
    v = cfg.battery_nominal_voltage
    for i, sp in enumerate(plans):
        h = _hours(sp)
        sp.soc = round(cur.soc_e[i] / cap * 100, 1)
        sp.import_kwh = round(cur.imp[i], 3)
        sp.export_kwh = round(cur.exp[i], 3)
        g = min(gc[i], cur.imp[i])
        sp.grid_charge_kwh = round(g, 3)
        if g > EPS:
            sp.ess_state = ESS_KEEP_CHARGED
            sp.reasons["ess"] = charge_why.get(i, "laden van het net")
            amps = g * eff_c / h * 1000 / v if h else cfg.dvcc_max_charge_current
            step = max(1, cfg.dvcc_step_a)
            amps = round(amps / step) * step
            sp.dvcc_current = int(max(cfg.dvcc_min_charge_current, min(cfg.dvcc_max_charge_current, amps)))
            sp.reasons["dvcc"] = f"{g:.1f} kWh in dit slot: {sp.dvcc_current} A" + (
                " (beperkt: auto laadt)" if sp.car_kwh > EPS else ""
            )
        elif hold[i]:
            # "Keep batteries charged" met laadstroom 0: niet laden, niet ontladen
            sp.ess_state = ESS_KEEP_CHARGED
            sp.dvcc_current = 0
            sp.reasons["ess"] = hold_why.get(i, "accu bewaren voor later")
            sp.reasons["dvcc"] = "0 A: accu bewaren (niet van het net laden)"
        else:
            sp.ess_state = ESS_OPTIMIZED
            sp.dvcc_current = cfg.dvcc_max_charge_current
            sp.reasons["dvcc"] = "standaard max laadstroom (zon mag vol laden)"
            if cur.soc_e[i] >= target_e - 0.2 and sp.pv_kwh > sp.house_kwh:
                sp.reasons["ess"] = "zelfverbruik, accu (bijna) op doel"
            else:
                sp.reasons["ess"] = f"zelfverbruik (€{sp.price:.3f})"
        if ex[i] > EPS and cur.exp[i] > EPS:
            w = min(cfg.export_max_w, ex[i] / h * 1000) if h else cfg.export_max_w
            sp.setpoint_w = -int(round(w / 100) * 100)
            sp.feed_in_disabled = 0
            sp.reasons["setpoint"] = export_why.get(i, "terugleveren")
            sp.reasons["feed_in"] = "terugleveren: accu mag naar het net"
        else:
            sp.setpoint_w = int(cfg.grid_setpoint_default_w)
            sp.reasons["setpoint"] = "normaal (geen terugleveren)"

    if state.soc is not None and cap:
        lowest = min(range(n), key=lambda k: cur.soc_e[k]) if n else None
        summary = {
            "cost_eur": round(cur_total + cur.terminal_e * terminal_value, 2),
            "baseline_cost_eur": round(base_total + base.terminal_e * terminal_value, 2),
            "saving_eur": round(base_total - cur_total, 2),
            "grid_charge_kwh": round(sum(min(gc[i], cur.imp[i]) for i in range(n)), 1),
            "hold_slots": sum(1 for i in range(n) if hold[i]),
            "export_kwh": round(sum(ex[i] for i in range(n) if cur.exp[i] > EPS), 1),
            "soc_end": round(cur.terminal_e / cap * 100, 1),
            "soc_min": round(cur.soc_e[lowest] / cap * 100, 1) if lowest is not None else None,
            "soc_min_at": plans[lowest].start.isoformat() if lowest is not None else None,
            "empty_at": plans[empty_idx].start.isoformat() if empty_idx is not None else None,
            "empty_why": why_empty.lstrip("; "),
            "grid_charge_skip": skip_why,
            "house_kwh_24h": round(sum(p.house_kwh for p in plans if p.start < now + timedelta(hours=24)), 1),
            "pv_kwh_24h": round(sum(p.pv_kwh for p in plans if p.start < now + timedelta(hours=24)), 1),
        }
        full = next((plans[i].end for i in range(n) if cur.soc_e[i] >= target_e - 0.2), None)
        summary["target_reached_at"] = full.isoformat() if full else None
        return summary
    return {}

