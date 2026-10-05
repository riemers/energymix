from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from energymix.config import Car, Config
from energymix.forecast import pv_per_slot, sun_window
from energymix.planner import ESS_KEEP_CHARGED, ESS_OPTIMIZED, CarState, Forecast, State, detect_season, make_plan
from energymix.prices import build_slots, cheapest_window, parse_tibber

TZ = ZoneInfo("Europe/Amsterdam")


def cfg(**kw) -> Config:
    base = dict(
        pv_switch_entity="switch.pv",
        zappi_mode_entity="select.zappi_mode",
        zappi_plug_entity="sensor.zappi_plug",
        zappi_status_entity="sensor.zappi_status",
        cars=[Car("Auto A", 400, "b.cable", "d.loc", "s.range", kwh_per_km=0.2)],
        house_load_default_w=0,
    )
    base.update(kw)
    return Config(**base)


def hourly(day: datetime, prices: list[float]):
    return build_slots((day + timedelta(hours=i), p, None) for i, p in enumerate(prices))


def at(h, m=0, day=6):
    return datetime(2026, 10, day, h, m, tzinfo=TZ)


def car(range_km="300"):
    return [CarState("Auto A", 400, cable="on", location="home", range_km=range_km, kwh_per_km=0.2)]


def by_hour(plan, day=6):
    return {s.start.astimezone(TZ).hour: s for s in plan.slots if s.start.astimezone(TZ).day == day}


# ------------------------------------------------------------------ PV


def test_negative_price_turns_pv_off_and_charges_battery():
    prices = hourly(at(0), [0.20] * 12 + [-0.05, -0.02] + [0.20] * 10)
    plan = make_plan(cfg(), prices, State(soc=50), at(12, 5))
    assert plan.now.pv_on is False
    assert plan.now.ess_state == ESS_KEEP_CHARGED
    assert "negatieve prijs" in plan.now.reasons["ess"]


# ------------------------------------------------------------------ accu


def test_charges_cheap_when_used_later_at_high_price():
    # Goedkoop 02-04u, duur 18-22u; huis verbruikt 2 kW
    prices = hourly(at(0), [0.25, 0.25, 0.05, 0.05, 0.25] + [0.25] * 13 + [0.45] * 4 + [0.25] * 2)
    plan = make_plan(cfg(house_load_default_w=2000), prices, State(soc=20), at(0, 30))
    h = by_hour(plan)
    assert h[2].ess_state == ESS_KEEP_CHARGED
    assert "bespaart" in h[2].reasons["ess"]
    assert h[10].ess_state == ESS_OPTIMIZED
    assert plan.summary["saving_eur"] > 0


def test_no_grid_charge_when_spread_too_small():
    prices = hourly(at(0), [0.20] * 3 + [0.18] * 3 + [0.22] * 18)
    plan = make_plan(cfg(house_load_default_w=1000), prices, State(soc=20), at(0, 30))
    assert all(s.ess_state == ESS_OPTIMIZED for s in plan.slots)


def test_no_grid_charge_when_sun_fills_battery():
    # Goedkoop om 03u, maar overdag ruim zon die de accu toch vult
    prices = hourly(at(0), [0.25] * 3 + [0.10] + [0.25] * 14 + [0.40] * 6)
    pv = {s.start: (6.0 if 9 <= s.start.astimezone(TZ).hour < 16 else 0.0) for s in prices}
    fc = Forecast(pv_kwh=pv)
    plan = make_plan(cfg(house_load_default_w=500), prices, State(soc=60), at(0, 30), fc)
    assert by_hour(plan)[3].ess_state == ESS_OPTIMIZED
    # Zonder zon wél laden
    plan = make_plan(cfg(house_load_default_w=500), prices, State(soc=60), at(0, 30))
    assert by_hour(plan)[3].ess_state == ESS_KEEP_CHARGED


def test_grid_charge_switch_off():
    prices = hourly(at(0), [0.25, 0.05, 0.05] + [0.45] * 21)
    plan = make_plan(cfg(grid_charge_enabled=False, house_load_default_w=2000), prices, State(soc=20), at(0, 30))
    assert all(s.ess_state == ESS_OPTIMIZED for s in plan.slots)


def test_export_on_big_spread_keeps_reserve():
    prices = hourly(at(0), [0.25] * 18 + [0.80, 0.80] + [0.30] * 4)
    c = cfg(export_enabled=True, battery_reserve_soc=40, house_load_default_w=0)
    plan = make_plan(c, prices, State(soc=90), at(17, 10))
    h = by_hour(plan)
    assert h[18].setpoint_w < 0 and h[18].feed_in_disabled == 0
    assert "terugleveren" in h[18].reasons["setpoint"]
    assert min(s.soc for s in plan.slots) >= 40 - 0.5
    # Niet bij een klein verschil
    prices = hourly(at(0), [0.25] * 18 + [0.33, 0.33] + [0.30] * 4)
    plan = make_plan(c, prices, State(soc=90), at(17, 10))
    assert all((s.setpoint_w or 0) >= 0 for s in plan.slots)


def test_no_export_while_car_charges():
    prices = hourly(at(0), [0.10] + [0.80] * 3 + [0.30] * 20)
    c = cfg(export_enabled=True, house_load_default_w=0, force_fast_price=0.9)
    st = State(soc=90, zappi_plug="EV Connected", carcharger_mode="fast", cars=car("200"))
    plan = make_plan(c, prices, st, at(1, 5))
    assert all((s.setpoint_w or 0) >= 0 for s in plan.slots)


def test_export_disabled_by_default():
    prices = hourly(at(0), [0.25] * 18 + [0.90] + [0.20] * 5)
    plan = make_plan(cfg(), prices, State(soc=95), at(17, 10))
    assert all((s.setpoint_w or 0) >= 0 for s in plan.slots)


def test_battery_charge_limited_by_grid_when_car_charges():
    prices = hourly(at(0), [0.02] * 4 + [0.45] * 20)
    c = cfg(grid_max_import_w=17000, grid_margin_w=1500, zappi_max_w=11000, house_load_default_w=500)
    st = State(soc=20, zappi_plug="EV Connected", carcharger_mode="auto", cars=car("100"))
    plan = make_plan(c, prices, st, at(0, 0))
    s = plan.now
    assert s.zappi_mode == "Fast"
    assert s.ess_state == ESS_KEEP_CHARGED
    # Ruimte 17000-1500-11000-500 = 4000 W ≈ 77 A -> naar beneden op 10 A
    assert s.dvcc_current <= 80


# ------------------------------------------------------------------ auto


def test_car_below_minimum_charges_cheapest_before_deadline():
    # Om 22:00, deadline 07:30. Goedkoopste uren 03-05u.
    prices = hourly(at(0, day=6), [0.30] * 48)
    prices = [s if not (3 <= s.start.astimezone(TZ).hour < 5 and s.start.day == 7) else s.__class__(s.start, s.end, 0.18) for s in prices]
    st = State(soc=50, zappi_plug="EV Connected", carcharger_mode="auto", cars=car("200"))
    plan = make_plan(cfg(force_fast_price=0.0, car_opportunistic_price=0.0), prices, st, at(22))
    fast = [s for s in plan.slots if s.zappi_mode == "Fast"]
    # 50 km * 0.2 = 10 kWh = 1 uur bij 11 kW
    assert len(fast) == 1 and fast[0].start == at(3, day=7)
    assert "onder 250 km" in fast[0].reasons["zappi"]


def test_car_above_minimum_only_charges_when_cheap():
    prices = hourly(at(0), [0.30] * 10 + [0.12] * 2 + [0.30] * 12)
    st = State(zappi_plug="EV Connected", carcharger_mode="auto", cars=car("300"))
    plan = make_plan(cfg(force_fast_price=0.0, car_opportunistic_price=0.15), prices, st, at(8))
    fast = [s.start.astimezone(TZ).hour for s in plan.slots if s.zappi_mode == "Fast"]
    assert fast == [10, 11]
    assert "goedkoop bijladen" in by_hour(plan)[10].reasons["zappi"]
    plan = make_plan(cfg(force_fast_price=0.0, car_opportunistic_price=0.10), prices, st, at(8))
    assert not any(s.zappi_mode == "Fast" for s in plan.slots)


def test_vannacht_means_full_before_ready_hour():
    prices = hourly(at(0), [0.30] * 48)
    st = State(zappi_plug="EV Connected", carcharger_mode="auto", vannacht=True, cars=car("340"))
    plan = make_plan(cfg(force_fast_price=0.0, car_opportunistic_price=0.0), prices, st, at(22))
    fast = [s for s in plan.slots if s.zappi_mode == "Fast"]
    assert fast and fast[-1].end <= at(8, day=7)
    assert plan.car.full_at is not None


def test_morning_eco_when_sun_refills():
    prices = hourly(at(0), [0.30] * 24)
    st = State(
        soc=80, solar_remaining_kwh=60, sunchance=70, zappi_plug="EV Connected",
        carcharger_mode="auto", cars=car("350"),
    )
    plan = make_plan(cfg(force_fast_price=0.0, car_opportunistic_price=0.0), prices, st, at(8))
    assert plan.now.zappi_mode == "Eco"
    assert plan.now.feed_in_disabled == 0
    # Te weinig zon: geen eco
    st.solar_remaining_kwh = 5
    plan = make_plan(cfg(force_fast_price=0.0, car_opportunistic_price=0.0), prices, st, at(8))
    assert plan.now.zappi_mode == "Eco+"
    assert "zon nog" in plan.now.reasons["zappi"]


def test_ecoa_enables_all_loads_and_no_car_means_no_zappi():
    prices = hourly(at(0), [0.30] * 24)
    plan = make_plan(cfg(), prices, State(carcharger_mode="ecoa", zappi_plug="EV Disconnected"), at(9))
    assert plan.now.zappi_mode is None
    assert plan.now.feed_in_disabled == 0


# ------------------------------------------------------------------ seizoen / prognose


def test_season_detection():
    summer = hourly(at(0), [0.30] * 11 + [0.10] * 5 + [0.30] * 8)
    winter = hourly(at(0), [0.12] * 6 + [0.30] * 18)
    assert detect_season(summer, TZ, at(23))["detected"] == "day"
    assert detect_season(winter, TZ, at(23))["detected"] == "night"


def test_pv_forecast_distributes_over_daylight():
    slots = hourly(at(0), [0.2] * 48)
    pv = pv_per_slot(slots, at(0), TZ, 20, 10, 52.1, 5.1)
    today = {s.start.astimezone(TZ).hour: pv[s.start] for s in slots if s.start.day == 6}
    assert abs(sum(today.values()) - 20) < 0.01
    assert today[13] > today[9] > today[2] == 0
    rise, set_ = sun_window(datetime(2026, 10, 6), 52.1, 5.1)
    assert 5 < rise.hour < 7 and 16 < set_.hour < 18  # UTC


# ------------------------------------------------------------------ prijzen


def test_dst_day_has_25_slots():
    start = datetime(2026, 10, 25, 0, 0, tzinfo=TZ).astimezone(ZoneInfo("UTC"))
    raw = [{"startsAt": (start + timedelta(hours=i)).astimezone(TZ).isoformat(), "total": 0.30 if i != 4 else -0.10, "energy": 0.1}
           for i in range(25)]
    slots = parse_tibber({"today": raw})
    assert len(slots) == 25 and slots[0].energy == 0.1
    neg = next(s for s in slots if s.price < 0)
    assert neg.start.astimezone(TZ).hour == 3
    plan = make_plan(cfg(), slots, State(soc=50), neg.start + timedelta(minutes=10))
    assert plan.now.pv_on is False


def test_quarter_hour_prices():
    slots = build_slots((at(0) + timedelta(minutes=15 * i), 0.30 if i not in (8, 9) else 0.10, None) for i in range(96))
    assert slots[0].minutes == 15
    w = cheapest_window(slots, 30, at(0))
    assert w[0].start == at(2) and w[-1].end == at(2, 30)
    plan = make_plan(cfg(), slots, State(soc=50), at(1))
    assert len(plan.slots) == 92
