"""De planner: één plek die beslist.

Puur en zonder I/O: krijgt prijzen, een momentopname van de toestand en de
config, en geeft per tijdslot terug wat elk apparaat moet doen, mét de reden.
Daardoor is hij te testen en kun je vragen "wat had hij gisteren gedaan?".

Prioriteit (hoog naar laag):
    negatieve prijs > goedkoop laden accu > Lowest Price blok > zelfverbruik
en voor de Zappi:
    handmatige modus (fast/slow/ecoa) > force-fast prijs > laadvenster
    > ochtend-eco vanuit de accu > Eco+
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .config import Config
from .prices import PriceSlot, cheapest_slots_per_day, cheapest_window

ESS_KEEP_CHARGED = 9  # Victron BatteryLife state: "Keep batteries charged"
ESS_OPTIMIZED = 10  # BatteryLife state: "Optimized (without BatteryLife)"

UNAVAILABLE = {"", "unknown", "unavailable", "none", "null"}


@dataclass
class CarState:
    name: str
    max_range_km: float
    cable: str = ""
    location: str = ""
    range_km: str = ""

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
    sunchance: float | None = None
    carcharger_mode: str = "auto"  # auto | fast | slow | ecoa
    vannacht: bool = False
    zappi_plug: str = ""
    zappi_status: str = ""
    zappi_mode: str = ""
    cars: list[CarState] = field(default_factory=list)

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
class SlotPlan:
    start: datetime
    end: datetime
    price: float
    pv_on: bool | None = None
    ess_state: int | None = None
    dvcc_current: int | None = None
    zappi_mode: str | None = None
    feed_in_disabled: int | None = None  # Hub4/DisableFeedIn: 1 = alleen critical loads
    reasons: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["start"] = self.start.isoformat()
        d["end"] = self.end.isoformat()
        return d


@dataclass
class Plan:
    created_at: datetime
    slots: list[SlotPlan]
    active_car: str | None
    charge_minutes: int | None
    charge_window: tuple[datetime, datetime] | None
    notes: list[str] = field(default_factory=list)

    @property
    def now(self) -> SlotPlan | None:
        for s in self.slots:
            if s.start <= self.created_at < s.end:
                return s
        return None

    def to_dict(self) -> dict:
        return {
            "created_at": self.created_at.isoformat(),
            "active_car": self.active_car,
            "charge_minutes": self.charge_minutes,
            "charge_window": [t.isoformat() for t in self.charge_window] if self.charge_window else None,
            "notes": self.notes,
            "slots": [s.to_dict() for s in self.slots],
        }


def _num(v) -> float | None:
    try:
        if v is None or str(v).strip().lower() in UNAVAILABLE:
            return None
        return float(str(v).replace(",", "."))
    except ValueError:
        return None


def _fmt(t: datetime, tz: ZoneInfo) -> str:
    return t.astimezone(tz).strftime("%H:%M")


def make_plan(cfg: Config, prices: list[PriceSlot], state: State, now: datetime) -> Plan:
    tz = ZoneInfo(cfg.timezone)
    horizon_end = now + timedelta(hours=cfg.horizon_hours)
    slots = [s for s in prices if s.end > now and s.start < horizon_end]
    notes: list[str] = []
    if not slots:
        notes.append("Geen prijsdata voor de komende periode")

    # --- Accu: Lowest Price blok (vervangt ps-strategy-lowest-price) ---
    lowest_price_slots: set[datetime] = set()
    if (
        cfg.has_battery
        and state.soc is not None
        and state.soc < cfg.lowest_price_ess_soc_below
        and (state.solar_today_kwh or 0) < cfg.lowest_price_ess_solar_below
    ):
        lowest_price_slots = cheapest_slots_per_day(prices, cfg.lowest_price_ess_minutes, tz)
        notes.append(
            f"Lowest Price actief: SoC {state.soc:.0f}% < {cfg.lowest_price_ess_soc_below:.0f}% "
            f"en zon {state.solar_today_kwh or 0:.0f} kWh < {cfg.lowest_price_ess_solar_below:.0f} kWh"
        )

    # --- Auto: laadduur en laadvenster ---
    active_car, charge_minutes = _charge_need(cfg, state)
    window: list[PriceSlot] | None = None
    zappi_configured = bool(cfg.zappi_mode_entity)
    if zappi_configured and state.carcharger_mode == "auto" and state.car_plugged:
        not_after = None
        if state.vannacht:
            ready = now.astimezone(tz).replace(hour=cfg.vannacht_ready_hour, minute=0, second=0, microsecond=0)
            if ready <= now:
                ready += timedelta(days=1)
            not_after = ready
        window = cheapest_window(prices, charge_minutes, now, not_after)
        if window is None:
            notes.append(f"Te weinig slots voor {charge_minutes} min laden")
    window_range = (window[0].start, window[-1].end) if window else None

    plans = [
        _plan_slot(cfg, tz, s, slots, state, lowest_price_slots, window_range)
        for s in slots
    ]
    return Plan(now, plans, active_car, charge_minutes if active_car or zappi_configured else None, window_range, notes)


def _charge_need(cfg: Config, state: State) -> tuple[str | None, int]:
    for car in state.cars:
        rng = car.range_value
        if car.connected and car.home and rng is not None:
            missing = max(0.0, car.max_range_km - rng)
            minutes = round(missing / cfg.charge_speed_km_per_hour * 60)
            return car.name, max(30, min(minutes, 720))
    return None, cfg.default_charge_minutes


def _plan_slot(
    cfg: Config,
    tz: ZoneInfo,
    slot: PriceSlot,
    slots: list[PriceSlot],
    state: State,
    lowest_price_slots: set[datetime],
    window: tuple[datetime, datetime] | None,
) -> SlotPlan:
    p = slot.price
    sp = SlotPlan(slot.start, slot.end, p)
    r = sp.reasons

    # --- PV-curtailment ---
    if cfg.pv_switch_entity:
        sp.pv_on = p >= 0
        r["pv"] = f"negatieve prijs €{p:.4f}: panelen uit" if p < 0 else "prijs ≥ 0: panelen aan"

    # --- Accu (ESS BatteryLife state + DVCC) ---
    if cfg.has_battery:
        sp.ess_state, r["ess"] = _ess(cfg, tz, slot, slots, state, lowest_price_slots)
        sp.dvcc_current, r["dvcc"] = _dvcc(cfg, slot, slots, state)

    # --- Zappi + Victron feed-in ---
    sp.feed_in_disabled = 1
    r["feed_in"] = "standaard: alleen critical loads"
    if cfg.zappi_mode_entity:
        _zappi(cfg, tz, sp, slot, state, window)
    return sp


def _ess(cfg, tz, slot, slots, state, lowest_price_slots) -> tuple[int, str]:
    p = slot.price
    if p < 0:
        return ESS_KEEP_CHARGED, f"negatieve prijs €{p:.4f}: accu laden"

    if p < cfg.cheap_price:
        day = slot.start.astimezone(tz).date()
        neg_ahead = next(
            (s for s in slots if s.start > slot.start and s.start.astimezone(tz).date() == day and s.price < 0),
            None,
        )
        if neg_ahead:
            return ESS_OPTIMIZED, f"goedkoop, maar wacht op negatief om {_fmt(neg_ahead.start, tz)} (€{neg_ahead.price:.4f})"
        if slot.start in lowest_price_slots:
            return ESS_KEEP_CHARGED, f"goedkoop €{p:.4f} en Lowest Price blok"
        if cfg.solar_skip_grid_charge and not state.zappi_charging and state.soc is not None:
            needed = max(0.0, (100 - state.soc) / 100 * cfg.battery_capacity_kwh)
            solar = state.solar_today_kwh or 0
            if solar >= needed * cfg.solar_skip_grid_charge_buffer:
                return ESS_OPTIMIZED, f"goedkoop, maar zon {solar:.0f} kWh ≥ nodig {needed:.1f} kWh"
        why = "auto laadt" if state.zappi_charging else "zon onvoldoende"
        return ESS_KEEP_CHARGED, f"goedkoop €{p:.4f} < €{cfg.cheap_price:.2f}, {why}: accu laden"

    if slot.start in lowest_price_slots:
        return ESS_KEEP_CHARGED, "Lowest Price blok (lage SoC, weinig zon)"
    return ESS_OPTIMIZED, f"€{p:.4f} ≥ €{cfg.cheap_price:.2f}: zelfverbruik"


def _dvcc(cfg, slot, slots, state) -> tuple[int, str]:
    if slot.price >= 0 or state.soc is None:
        return cfg.dvcc_max_charge_current, "standaard max laadstroom"

    # Lengte van het negatieve blok vanaf dit slot
    block_min = 0.0
    started = False
    for s in slots:
        if s.start == slot.start:
            started = True
        if started:
            if s.price >= 0:
                break
            block_min += s.minutes
    hours = max(block_min / 60, 0.25)

    to_full = max(0.0, (100 - state.soc) / 100 * cfg.battery_capacity_kwh)
    if to_full <= 0.02:
        return cfg.dvcc_min_charge_current, "accu vol: minimale laadstroom"
    eff = cfg.charge_efficiency if 0 < cfg.charge_efficiency <= 1 else 0.93
    amps = to_full / hours / eff * 1000 / cfg.battery_nominal_voltage
    current = round(max(cfg.dvcc_min_charge_current, min(cfg.dvcc_max_charge_current, amps)))
    return current, f"negatief blok {hours:.1f}u, nog {to_full:.1f} kWh tot vol: {current} A"


def _zappi(cfg, tz, sp: SlotPlan, slot: PriceSlot, state: State, window) -> None:
    r = sp.reasons
    mode = state.carcharger_mode

    if mode == "ecoa":
        sp.feed_in_disabled = 0
        r["feed_in"] = "EcoA: accu mag de auto laden (all loads)"

    if not state.car_plugged:
        r["zappi"] = "geen auto aangesloten"
        return

    if mode == "fast":
        sp.zappi_mode, r["zappi"] = "Fast", "laadmodus fast"
        return
    if mode == "slow":
        sp.zappi_mode, r["zappi"] = "Eco", "laadmodus slow"
        return
    if mode == "ecoa":
        sp.zappi_mode, r["zappi"] = "Eco", "laadmodus EcoA"
        return
    if mode != "auto":
        r["zappi"] = f"onbekende laadmodus '{mode}': niets doen"
        return

    p = slot.price
    if p <= cfg.force_fast_price:
        sp.zappi_mode, r["zappi"] = "Fast", f"prijs €{p:.4f} ≤ €{cfg.force_fast_price:.2f}: direct laden"
        return
    if window and window[0] <= slot.start < window[1]:
        sp.zappi_mode, r["zappi"] = "Fast", f"goedkoopste venster {_fmt(window[0], tz)}–{_fmt(window[1], tz)}"
        return

    eco_ok, eco_reason = _morning_eco(cfg, tz, slot, state, window)
    if eco_ok:
        sp.zappi_mode = "Eco"
        sp.feed_in_disabled = 0
        r["zappi"] = r["feed_in"] = eco_reason
        return
    sp.zappi_mode = "Eco+"
    r["zappi"] = f"buiten laadvenster ({eco_reason})"


def _morning_eco(cfg, tz, slot: PriceSlot, state: State, window) -> tuple[bool, str]:
    hour = slot.start.astimezone(tz).hour
    if window and slot.start < window[1]:
        return False, f"wacht op Fast {_fmt(window[0], tz)}–{_fmt(window[1], tz)}"
    if not cfg.eco_morning_start_hour <= hour < cfg.eco_morning_end_hour:
        return False, f"geen ochtend-eco buiten {cfg.eco_morning_start_hour}–{cfg.eco_morning_end_hour}u"
    if state.car_full:
        return False, "auto vol"
    soc = state.soc or 0
    if soc <= cfg.eco_battery_soc_min:
        return False, f"SoC {soc:.0f}% te laag voor ochtend-eco"
    solar = state.solar_today_kwh or 0
    if solar <= cfg.eco_solar_min:
        return False, f"zon forecast {solar:.0f} kWh te laag voor ochtend-eco"
    if state.sunchance is not None and state.sunchance < cfg.eco_sunchance_min:
        return False, f"zonkans {state.sunchance:.0f}% te laag voor ochtend-eco"
    return True, f"ochtend-eco vanuit accu: zon {solar:.0f} kWh, SoC {soc:.0f}%"
