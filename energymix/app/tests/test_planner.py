from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from energymix.config import Car, Config
from energymix.planner import ESS_KEEP_CHARGED, ESS_OPTIMIZED, CarState, State, make_plan
from energymix.prices import build_slots, cheapest_window, parse_tibber

TZ = ZoneInfo("Europe/Amsterdam")


def cfg(**kw) -> Config:
    base = dict(
        pv_switch_entity="switch.pv",
        zappi_mode_entity="select.zappi_mode",
        zappi_plug_entity="sensor.zappi_plug",
        zappi_status_entity="sensor.zappi_status",
        cars=[Car("Auto A", 400, "b.cable", "d.loc", "s.range")],
    )
    base.update(kw)
    return Config(**base)


def hourly(day: datetime, prices: list[float]):
    return build_slots((day + timedelta(hours=i), p, None) for i, p in enumerate(prices))


def at(h, m=0, day=6):
    return datetime(2026, 10, day, h, m, tzinfo=TZ)


def test_negative_price_turns_pv_off_and_charges_battery():
    prices = hourly(at(0), [0.20] * 12 + [-0.05, -0.02] + [0.20] * 10)
    plan = make_plan(cfg(), prices, State(soc=50, solar_today_kwh=10), at(12, 5))
    now = plan.now
    assert now.pv_on is False
    assert now.ess_state == ESS_KEEP_CHARGED
    # 23.5 kWh in 2 uur / 0.93 / 52 V ≈ 243 A -> begrensd op 150
    assert now.dvcc_current == 150


def test_dvcc_spreads_charge_over_negative_block():
    prices = hourly(at(0), [0.20] * 10 + [-0.01] * 6 + [0.20] * 8)
    plan = make_plan(cfg(), prices, State(soc=90, solar_today_kwh=10), at(10, 1))
    # 4.7 kWh / 6u / 0.93 / 52 V ≈ 16 A -> minimum 20
    assert plan.now.dvcc_current == 20


def test_cheap_waits_for_negative_later_today():
    prices = hourly(at(0), [0.05] * 13 + [-0.01] + [0.2] * 10)
    plan = make_plan(cfg(), prices, State(soc=40, solar_today_kwh=0), at(8))
    assert plan.now.ess_state == ESS_OPTIMIZED
    assert "wacht op negatief" in plan.now.reasons["ess"]


def test_cheap_skips_grid_when_solar_enough():
    prices = hourly(at(0), [0.05] * 24)
    # nodig: 50% van 47 = 23.5 kWh, zon 30 kWh
    plan = make_plan(cfg(), prices, State(soc=50, solar_today_kwh=30), at(3))
    assert plan.now.ess_state == ESS_OPTIMIZED
    plan = make_plan(cfg(), prices, State(soc=50, solar_today_kwh=10), at(3))
    assert plan.now.ess_state == ESS_KEEP_CHARGED


def test_lowest_price_block_when_soc_and_sun_low():
    prices = hourly(at(0), [0.30] * 2 + [0.20] * 6 + [0.30] * 16)
    c = cfg(lowest_price_ess_minutes=360)
    plan = make_plan(c, prices, State(soc=20, solar_today_kwh=5), at(3))
    assert plan.now.ess_state == ESS_KEEP_CHARGED
    plan = make_plan(c, prices, State(soc=20, solar_today_kwh=5), at(9))
    assert plan.now.ess_state == ESS_OPTIMIZED


def car_state(range_km="300"):
    return [CarState("Auto A", 400, cable="on", location="home", range_km=range_km)]


def test_auto_picks_cheapest_window():
    prices = hourly(at(0), [0.30] * 2 + [0.25, 0.20, 0.21] + [0.30] * 19)
    st = State(soc=50, zappi_plug="EV Connected", carcharger_mode="auto", cars=car_state("300"))
    # 100 km / 65 km/u = 92 min -> 2 slots
    plan = make_plan(cfg(), prices, st, at(0, 30))
    assert plan.charge_minutes == 92
    assert plan.charge_window == (at(3), at(5))
    by_hour = {s.start.hour: s for s in plan.slots}
    assert by_hour[3].zappi_mode == "Fast"
    assert by_hour[1].zappi_mode == "Eco+"


def test_force_fast_below_threshold():
    prices = hourly(at(0), [0.14] + [0.30] * 23)
    st = State(zappi_plug="EV Connected", carcharger_mode="auto", cars=car_state("390"))
    plan = make_plan(cfg(), prices, st, at(0, 10))
    assert plan.now.zappi_mode == "Fast"


def test_no_morning_eco_while_fast_window_still_ahead():
    # Goedkoop venster om 10:00; om 07:00 dus geen eco maar wachten op Fast
    prices = hourly(at(0), [0.30] * 10 + [0.20, 0.20] + [0.30] * 12)
    st = State(
        soc=80, solar_today_kwh=60, sunchance=70, zappi_plug="EV Connected",
        carcharger_mode="auto", cars=car_state("300"),
    )
    plan = make_plan(cfg(), prices, st, at(7))
    assert plan.charge_window == (at(10), at(12))
    assert plan.now.zappi_mode == "Eco+"
    assert plan.now.feed_in_disabled == 1
    assert "wacht op Fast" in plan.now.reasons["zappi"]


def test_too_few_slots_falls_back_to_eco_plus():
    st = State(zappi_plug="EV Connected", carcharger_mode="auto", cars=car_state("300"))
    plan = make_plan(cfg(force_fast_price=0.0), hourly(at(0), [0.30] * 24), st, at(23))
    assert plan.charge_window is None
    assert plan.now.zappi_mode == "Eco+"


def test_morning_eco_when_window_passed():
    prices = hourly(at(0), [0.30] * 24)
    st = State(
        soc=80, solar_today_kwh=60, sunchance=70, zappi_plug="EV Connected",
        carcharger_mode="auto", cars=car_state("300"),
    )
    c = cfg(force_fast_price=0.0)
    plan = make_plan(c, prices, st, at(7))
    # alle prijzen gelijk -> venster is het eerste mogelijke blok (nu) -> Fast
    assert plan.now.zappi_mode == "Fast"
    later = [s for s in plan.slots if s.start >= plan.charge_window[1] and s.start.hour < 12]
    assert later and all(s.zappi_mode == "Eco" and s.feed_in_disabled == 0 for s in later)


def test_ecoa_enables_all_loads_and_no_car_means_no_zappi():
    prices = hourly(at(0), [0.30] * 24)
    plan = make_plan(cfg(), prices, State(carcharger_mode="ecoa", zappi_plug="EV Disconnected"), at(9))
    assert plan.now.zappi_mode is None
    assert plan.now.feed_in_disabled == 0


def test_vannacht_limits_window():
    # Goedkoop om 10:00 morgen, maar met vannacht moet hij om 08:00 klaar zijn
    prices = hourly(at(0), [0.30] * 48)
    prices = [s if s.start != at(10, day=7) else s.__class__(s.start, s.end, 0.16) for s in prices]
    st = State(zappi_plug="EV Connected", carcharger_mode="auto", vannacht=True, cars=car_state("340"))
    plan = make_plan(cfg(), prices, st, at(22))
    assert plan.charge_window[1] <= at(8, day=7)
    st.vannacht = False
    plan = make_plan(cfg(), prices, st, at(22))
    assert plan.charge_window[0] == at(10, day=7)


def test_dst_day_has_25_slots_and_correct_current_slot():
    # 25 okt 2026: wintertijd, 25 uur
    start = datetime(2026, 10, 25, 0, 0, tzinfo=TZ)
    raw = []
    t = start.astimezone(ZoneInfo("UTC"))
    for i in range(25):
        raw.append({"startsAt": (t + timedelta(hours=i)).astimezone(TZ).isoformat(), "total": 0.30 if i != 4 else -0.10})
    slots = parse_tibber({"today": raw})
    assert len(slots) == 25
    # Slot i=4 begint om 03:00 lokale (winter)tijd, niet om 04:00
    neg = next(s for s in slots if s.price < 0)
    assert neg.start.astimezone(TZ).hour == 3
    now = neg.start + timedelta(minutes=10)
    plan = make_plan(cfg(), slots, State(soc=50), now)
    assert plan.now.pv_on is False


def test_quarter_hour_prices():
    slots = build_slots((at(0) + timedelta(minutes=15 * i), 0.30 if i not in (8, 9) else 0.10, None) for i in range(96))
    assert slots[0].minutes == 15
    w = cheapest_window(slots, 30, at(0))
    assert w[0].start == at(2) and w[-1].end == at(2, 30)
