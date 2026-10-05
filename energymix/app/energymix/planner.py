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

    plans = [SlotPlan(s.start, s.end, s.price, sell_price(cfg, s)) for s in slots]
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
    car = _plan_car(cfg, tz, now, slots, plans, state, notes)

    # 3. Accu
    summary: dict = {}
    if cfg.has_battery and state.soc is not None:
        summary = _plan_battery(cfg, tz, now, slots, plans, state, fc, season, car)
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
    return Plan(now, plans, car, season, summary, [_nl(n) for n in notes])


_EUR_RE = re.compile(r"(€-?\d+)\.(\d+)")


def _nl(text: str) -> str:
    return _EUR_RE.sub(r"\1,\2", text)


# --------------------------------------------------------------------------- auto


def _active_car(state: State) -> CarState | None:
    for car in state.cars:
        if car.connected and car.home and car.range_value is not None:
            return car
    return None


def _plan_car(cfg, tz, now, slots, plans, state: State, notes) -> CarPlan:
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

    manual = {"fast": ("Fast", "laadmodus fast"), "slow": ("Eco", "laadmodus slow"), "ecoa": ("Eco", "laadmodus EcoA")}
    if mode in manual:
        m, why = manual[mode]
        for sp in plans:
            sp.zappi_mode, sp.reasons["zappi"] = m, why
            if m == "Fast":
                sp.car_kwh = cfg.zappi_max_w / 1000 * _hours(sp)
        return cp
    if mode != "auto":
        for sp in plans:
            sp.reasons["zappi"] = f"onbekende laadmodus '{mode}': niets doen"
        return cp

    active = _active_car(state)
    deadline = next_time(now, tz, cfg.car_ready_time)
    if state.vannacht:
        ready = now.astimezone(tz).replace(hour=cfg.vannacht_ready_hour, minute=0, second=0, microsecond=0)
        if ready <= now:
            ready += timedelta(days=1)
        deadline = min(deadline, ready)
    cp.deadline = deadline

    if active:
        rng = active.range_value or 0.0
        cp.name, cp.range_km = active.name, rng
        kpk = active.kwh_per_km or 0.17
        cp.need_full_kwh = max(0.0, active.max_range_km - rng) * kpk
        min_km = min(cfg.car_min_range_km, active.max_range_km)
        cp.need_min_kwh = max(0.0, min_km - rng) * kpk
        if state.vannacht:
            cp.need_min_kwh = cp.need_full_kwh  # "vannacht": vol vóór vertrek
    else:
        # Auto aangesloten maar onbekend welke: standaard laadduur als minimum
        cp.need_full_kwh = cfg.default_charge_minutes / 60 * cfg.zappi_max_w / 1000
        cp.need_min_kwh = 0.0
        notes.append("Aangesloten auto niet herkend (kabel/locatie/actieradius): standaard laadduur")
    if state.car_full:
        cp.need_full_kwh = cp.need_min_kwh = 0.0

    per_slot = {sp.start: cfg.zappi_max_w / 1000 * _hours(sp) for sp in plans}
    chosen: dict[datetime, str] = {}

    # a. Moet: minimum vóór de deadline, goedkoopste slots
    remaining = cp.need_min_kwh
    must_cands = sorted((sp for sp in plans if sp.end <= deadline or sp.start < deadline), key=lambda s: (s.price, s.start))
    for sp in must_cands:
        if remaining <= EPS:
            break
        chosen[sp.start] = (
            f"onder {cfg.car_min_range_km:.0f} km: moet vóór {_fmt(deadline, tz, now)}, goedkoopste slot (€{sp.price:.3f})"
            if not state.vannacht
            else f"vannacht: vol vóór {_fmt(deadline, tz, now)}, goedkoopste slot (€{sp.price:.3f})"
        )
        remaining -= per_slot[sp.start]
    if remaining > EPS:
        notes.append(f"Auto: {remaining:.1f} kWh van het minimum past niet vóór {_fmt(deadline, tz, now)}")

    # b. Mag: tot vol, alleen goedkoop
    optional = cp.need_full_kwh - sum(per_slot[k] for k in chosen)
    for sp in sorted(plans, key=lambda s: (s.price, s.start)):
        if optional <= EPS:
            break
        if sp.start in chosen:
            continue
        limit = max(cfg.car_opportunistic_price, cfg.force_fast_price)
        if sp.price > limit:
            break
        chosen[sp.start] = f"goedkoop bijladen tot vol (€{sp.price:.3f} ≤ €{limit:.2f})"
        optional -= per_slot[sp.start]

    energy_left = cp.need_full_kwh
    for sp in plans:
        if sp.start in chosen and energy_left > EPS:
            sp.zappi_mode = "Fast"
            sp.reasons["zappi"] = chosen[sp.start]
            sp.car_kwh = min(per_slot[sp.start], energy_left)
            energy_left -= sp.car_kwh
            cp.planned_kwh += sp.car_kwh
            if energy_left <= EPS and cp.need_full_kwh > 0:
                cp.full_at = sp.end
    return cp


def _morning_eco(cfg, tz, sp: SlotPlan, state: State, car: CarPlan, battery_need_kwh: float, house_rest_kwh: float, season) -> tuple[bool, str]:
    hour = sp.start.astimezone(tz).hour
    if season.get("effective") == "night":
        return False, "winterpatroon: geen ochtend-eco"
    if not cfg.eco_morning_start_hour <= hour < cfg.eco_morning_end_hour:
        return False, f"geen ochtend-eco buiten {cfg.eco_morning_start_hour}–{cfg.eco_morning_end_hour}u"
    if state.car_full or car.need_full_kwh - car.planned_kwh <= EPS:
        return False, "auto (straks) vol"
    soc = state.soc or 0
    if soc <= cfg.eco_battery_soc_min:
        return False, f"SoC {soc:.0f}% te laag voor ochtend-eco"
    if state.sunchance is not None and state.sunchance < cfg.eco_sunchance_min:
        return False, f"zonkans {state.sunchance:.0f}% te laag voor ochtend-eco"
    solar = state.solar_remaining_kwh if state.solar_remaining_kwh is not None else (state.solar_today_kwh or 0)
    car_gap = car.need_full_kwh - car.planned_kwh
    needed = car_gap + battery_need_kwh + house_rest_kwh
    if solar < needed:
        return False, f"zon nog {solar:.0f} kWh < {needed:.0f} kWh nodig (auto {car_gap:.0f} + accu {battery_need_kwh:.0f} + huis {house_rest_kwh:.0f})"
    return True, f"ochtend-eco uit accu: zon nog {solar:.0f} kWh vult auto ({car_gap:.0f}) + accu ({battery_need_kwh:.0f}) weer aan"


# --------------------------------------------------------------------------- accu


@dataclass
class _Sim:
    soc_e: list[float]
    cost: list[float]
    imp: list[float]
    exp: list[float]
    terminal_e: float


def _plan_battery(cfg, tz, now, slots, plans: list[SlotPlan], state: State, fc: Forecast, season, car: CarPlan) -> dict:
    cap = cfg.battery_capacity_kwh
    eff_c = cfg.charge_efficiency if 0 < cfg.charge_efficiency <= 1 else 0.93
    eff_rt = cfg.roundtrip_efficiency if 0 < cfg.roundtrip_efficiency <= 1 else 0.85
    eff_d = min(1.0, eff_rt / eff_c)
    floor_e = cap * 0.10  # Victron ESS minimum, voor het huis
    reserve_e = cap * cfg.battery_reserve_soc / 100
    target_e = cap * cfg.battery_target_soc / 100
    e0 = cap * state.soc / 100
    charge_w = fc.battery_charge_w or cfg.dvcc_max_charge_current * cfg.battery_nominal_voltage * eff_c
    n = len(plans)

    # Laadvermogen per slot: begrensd door de aansluiting als de auto laadt
    charge_cap = []
    for sp in plans:
        h = _hours(sp)
        car_w = sp.car_kwh / h * 1000 if h else 0
        house_w = sp.house_kwh / h * 1000 if h else 0
        ac_limit = battery_ac_limit_w(cfg, expected_others_a(cfg, house_w, car_w))
        charge_cap.append(max(0.0, min(charge_w, ac_limit * eff_c)) / 1000 * h)
    max_e = [cap if sp.price < 0 else target_e for sp in plans]
    export_cap = [
        (cfg.export_max_w / 1000 * _hours(sp)) if (cfg.export_enabled and sp.car_kwh <= EPS and sp.zappi_mode != "Fast") else 0.0
        for sp in plans
    ]
    # Waarde van energie die aan het eind nog in de accu zit: wat bijladen dan kost
    q = sorted(sp.price for sp in plans) or [0.2]
    terminal_value = q[len(q) // 4] / eff_c

    # Ochtend-eco: accu → auto (geschat 3.7 kW zolang de auto niet vol is)
    eco = [0.0] * n
    house_rest = 0.0
    batt_need = max(0.0, target_e - e0)
    for i, sp in enumerate(plans):
        if sp.zappi_mode is None and cfg.zappi_mode_entity and state.car_plugged and state.carcharger_mode == "auto":
            fast_ahead = any(o.zappi_mode == "Fast" and o.start > sp.start for o in plans[i:] if o.start.date() == sp.start.date())
            house_rest = sum(o.house_kwh for o in plans[i:] if o.start.astimezone(tz).date() == sp.start.astimezone(tz).date() and 7 <= o.start.astimezone(tz).hour < 20)
            ok, why = (False, "wacht op Fast-laadslot") if fast_ahead else _morning_eco(
                cfg, tz, sp, state, car, batt_need, house_rest, season
            )
            if ok and sp.start.astimezone(tz).date() == now.astimezone(tz).date():
                sp.zappi_mode = "Eco"
                sp.feed_in_disabled = 0
                sp.reasons["zappi"] = sp.reasons["feed_in"] = why
                eco[i] = 3.7 * _hours(sp)
            else:
                sp.zappi_mode = "Eco+"
                sp.reasons["zappi"] = f"buiten laadslots ({why})"

    gc = [0.0] * n
    ex = [0.0] * n
    hold = [False] * n  # accu niet ontladen: huis van het net (bewaren voor later)
    spill = [min(sp.sell_price, sl.energy if sl.energy is not None else sp.price - cfg.energy_tax_eur)
             for sp, sl in zip(plans, slots)]

    def simulate() -> _Sim:
        e = e0
        soc_e, cost, imp_l, exp_l = [], [], [], []
        for i, sp in enumerate(plans):
            imp = sp.car_kwh  # auto laadt uit het net (feed-in dicht)
            exp = 0.0
            net = sp.pv_kwh - sp.house_kwh - eco[i]
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
    cur = base
    cur_total = base_total
    charge_why: dict[int, str] = {}
    export_why: dict[int, str] = {}
    hold_why: dict[int, str] = {}

    step_charge = [min(charge_cap[i], charge_w / 1000 * _hours(plans[i])) for i in range(n)]
    # Alleen zinvolle kandidaten proberen: laden in de goedkoopste helft, leveren in de duurste
    by_price = sorted(p.price for p in plans)
    by_sell = sorted(p.sell_price for p in plans)
    cheap_limit = by_price[len(by_price) // 2] if by_price else 0
    sell_limit = by_sell[(len(by_sell) * 2) // 3] if by_sell else 0
    charge_idx = [i for i in range(n) if plans[i].price <= cheap_limit or plans[i].price < 0]
    export_idx = [i for i in range(n) if export_cap[i] > EPS and plans[i].sell_price >= sell_limit]
    for _ in range(300):
        best = None
        for i in charge_idx:
            sp = plans[i]
            # Laden van het net
            if (cfg.grid_charge_enabled or sp.price < 0) and gc[i] < step_charge[i] - EPS:
                delta = step_charge[i] - gc[i]
                gc[i] += delta
                sim = simulate()
                gc[i] -= delta
                gain = cur_total - total(sim)
                applied = sim.imp[i] - cur.imp[i]
                if applied > EPS:
                    per_kwh = gain / applied
                    threshold = 0.0 if sp.price < 0 else cfg.arbitrage_min_spread
                    if per_kwh > threshold and (best is None or per_kwh > best[0]):
                        best = (per_kwh, "gc", i, delta, sim)
        for i in charge_idx:
            # Bewaren: in een goedkoop slot het huis van het net, accu sparen
            sp = plans[i]
            if not hold[i] and gc[i] <= EPS and sp.house_kwh + eco[i] > sp.pv_kwh + EPS:
                hold[i] = True
                sim = simulate()
                hold[i] = False
                gain = cur_total - total(sim)
                applied = sim.imp[i] - cur.imp[i]
                if applied > EPS:
                    per_kwh = gain / applied
                    if per_kwh > cfg.arbitrage_min_spread and (best is None or per_kwh > best[0]):
                        best = (per_kwh, "hold", i, 0.0, sim)
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
                        best = (per_kwh, "ex", i, delta, sim)
        if best is None:
            break
        per_kwh, kind, i, delta, sim = best
        # Waar zit het effect? Bij laden: het slot dat het meest goedkoper wordt;
        # bij leveren: het slot dat duurder wordt (daar wordt later bijgekocht)
        diffs = [(cur.cost[j] - sim.cost[j], j) for j in range(n) if j != i]
        if kind in ("gc", "hold"):
            _, j = max(diffs) if diffs else (0, i)
        else:
            d, j = min(diffs) if diffs else (0, i)
            if d > -EPS:
                j = None
        if kind == "hold":
            hold[i] = True
            hold_why[i] = (
                f"accu bewaren: huis nu van het net (€{plans[i].price:.3f}), accu dekt om {_fmt(plans[j].start, tz, now)} "
                f"(€{plans[j].price:.3f}): +€{per_kwh:.2f}/kWh"
            )
        elif kind == "gc":
            gc[i] += delta
            if plans[i].price < 0:
                charge_why[i] = f"negatieve prijs €{plans[i].price:.4f}: accu laden"
            else:
                charge_why[i] = (
                    f"laden à €{plans[i].price:.3f}, bespaart om {_fmt(plans[j].start, tz, now)} (€{plans[j].price:.3f}): "
                    f"+€{per_kwh:.2f}/kWh"
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
        }
        full = next((plans[i].end for i in range(n) if cur.soc_e[i] >= target_e - 0.2), None)
        summary["target_reached_at"] = full.isoformat() if full else None
        return summary
    return {}

