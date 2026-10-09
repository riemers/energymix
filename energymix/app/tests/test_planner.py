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
        battery_margin_pct=0,  # de marge heeft eigen tests
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
    # Niet van het net laden; bewaren (laadstroom 0) mag wel
    assert plan.summary["grid_charge_kwh"] == 0
    assert all(s.dvcc_current == 0 for s in plan.slots if s.ess_state == ESS_KEEP_CHARGED)


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


def test_solar_gives_room_for_battery_next_to_car():
    from energymix.phases import battery_ac_limit_w, expected_others_a

    c = cfg(victron_phases="1,2,3")
    without = battery_ac_limit_w(c, expected_others_a(c, 900, 11400))
    with_sun = battery_ac_limit_w(c, expected_others_a(c, 900, 11400, 5800))
    assert with_sun - without > 5000  # 5.8 kW zon = ~8 A per fase extra ruimte


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


def test_car_charges_missing_km_in_cheapest_hours():
    # 400 - 270 = 130 km / 65 km/u = 2 uur, in de goedkoopste uren (morgen 03-05u)
    prices = hourly(at(0, day=6), [0.30] * 48)
    prices = [s if not (3 <= s.start.astimezone(TZ).hour < 5 and s.start.day == 7) else s.__class__(s.start, s.end, 0.18) for s in prices]
    st = State(soc=50, zappi_plug="EV Connected", carcharger_mode="auto", cars=car("270"))
    plan = make_plan(cfg(), prices, st, at(22))
    fast = [s for s in plan.slots if s.zappi_mode == "Fast"]
    assert [s.start for s in fast] == [at(3, day=7), at(4, day=7)]
    assert "130 km (2u00)" in fast[0].reasons["zappi"]
    assert plan.car.full_at == at(5, day=7)


def test_partial_last_slot():
    # 100 km = 1u32: twee uren, het tweede maar 32 minuten
    prices = hourly(at(0), [0.10, 0.11] + [0.30] * 22)
    st = State(zappi_plug="EV Connected", carcharger_mode="auto", cars=car("300"))
    plan = make_plan(cfg(), prices, st, at(0))
    h = by_hour(plan)
    assert h[0].zappi_mode == h[1].zappi_mode == "Fast" and h[2].zappi_mode != "Fast"
    assert abs(h[1].car_kwh - 11 * (100 / 65 * 60 - 60) / 60) < 0.01
    assert plan.car.full_at.replace(second=0, microsecond=0) == at(1, 32)


def test_full_car_does_not_charge():
    prices = hourly(at(0), [0.05] * 24)
    st = State(zappi_plug="EV Connected", carcharger_mode="auto", cars=car("400"))
    plan = make_plan(cfg(), prices, st, at(1))
    assert not any(s.zappi_mode == "Fast" for s in plan.slots)


def test_vannacht_means_full_before_ready_hour():
    prices = hourly(at(0), [0.30] * 48)
    st = State(zappi_plug="EV Connected", carcharger_mode="auto", vannacht=True, cars=car("340"))
    plan = make_plan(cfg(force_fast_price=0.0, car_opportunistic_price=0.0), prices, st, at(22))
    fast = [s for s in plan.slots if s.zappi_mode == "Fast"]
    assert fast and fast[-1].end <= at(8, day=7)
    assert plan.car.full_at is not None


def test_morning_eco_first_then_cheap_hours():
    # Accu 75%, 40 kWh zon verwacht, auto mist 100 km, om 14:00 goedkoop
    prices = hourly(at(0), [0.30] * 14 + [0.14] + [0.30] * 9)
    st = State(soc=75, solar_today_kwh=40, sunchance=70, zappi_plug="EV Connected",
               carcharger_mode="auto", cars=car("300"))
    plan = make_plan(cfg(), prices, st, at(7))
    h = by_hour(plan)
    # 07-12u Eco met Victron all loads
    assert all(h[x].zappi_mode == "Eco" and h[x].feed_in_disabled == 0 for x in range(7, 12))
    assert "ochtend-eco" in h[7].reasons["zappi"]
    # 5u x 3.7 kW = 18.5 kWh = 92 km; rest (8 km) Fast in het goedkope uur
    assert round(plan.car.eco_km) == 92
    assert h[14].zappi_mode == "Fast" and h[14].car_kwh < 2
    assert h[12].zappi_mode == "Eco+"
    kinds = [s["kinds"] for s in plan.car.sessions]
    assert kinds == [["eco"], ["goedkoopst"]]
    # Tijdens eco levert de accu aan de auto: nooit bewaren of van het net laden
    assert all(h[x].ess_state != 9 for x in range(7, 12))


def test_no_morning_eco_when_battery_low_or_little_sun():
    prices = hourly(at(0), [0.30] * 14 + [0.14] + [0.30] * 9)
    for soc, sun in ((50, 40), (75, 20)):
        st = State(soc=soc, solar_today_kwh=sun, sunchance=70, zappi_plug="EV Connected",
                   carcharger_mode="auto", cars=car("300"))
        plan = make_plan(cfg(), prices, st, at(7))
        assert not any(s.zappi_mode == "Eco" for s in plan.slots)
        assert by_hour(plan)[14].zappi_mode == "Fast"
        assert all(s.feed_in_disabled == 1 for s in plan.slots)


def test_no_morning_eco_in_winter_pattern():
    prices = hourly(at(0), [0.30] * 24)
    st = State(soc=80, solar_today_kwh=40, zappi_plug="EV Connected", carcharger_mode="auto", cars=car("300"))
    plan = make_plan(cfg(season_mode="night"), prices, st, at(7))
    assert not any(s.zappi_mode == "Eco" for s in plan.slots)


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


def test_car_sessions_show_when_and_how_far():
    prices = hourly(at(0), [0.30] * 10 + [0.12] * 2 + [0.30] * 12)
    st = State(zappi_plug="EV Connected", carcharger_mode="auto", cars=car("270"))
    plan = make_plan(cfg(), prices, st, at(8))
    [sess] = plan.car.sessions
    assert sess["mode"] == "Fast" and sess["kinds"] == ["goedkoopst"]
    assert sess["start"] == at(10).isoformat() and sess["end"] == at(12).isoformat()
    assert sess["range_start_km"] == 270 and sess["range_end_km"] == 400


def test_car_takes_solar_first_battery_does_not_rise():
    # Zonnige middag (4 kW), auto laadt 11 kW: alle zon gaat naar de auto, accu stijgt niet
    prices = hourly(at(0), [0.30] * 11 + [0.12] * 5 + [0.30] * 8)
    pv = {s.start: (4.0 if 11 <= s.start.astimezone(TZ).hour < 16 else 0.0) for s in prices}
    st = State(soc=50, zappi_plug="EV Connected", carcharger_mode="auto", cars=car("75"))
    plan = make_plan(cfg(house_load_default_w=500, grid_charge_enabled=False), prices, st, at(11), Forecast(pv_kwh=pv))
    h = by_hour(plan)
    assert all(h[x].zappi_mode == "Fast" for x in range(11, 16))
    socs = [h[x].soc for x in range(11, 16)]
    assert max(socs) <= 50.0 + 0.1  # accu laadt niet terwijl de auto laadt
    # Zonder auto zou de zon de accu wel vullen
    st2 = State(soc=50)
    plan2 = make_plan(cfg(house_load_default_w=500, grid_charge_enabled=False), prices, st2, at(11), Forecast(pv_kwh=pv))
    assert by_hour(plan2)[15].soc > 60


def quarters(day, prices):
    return build_slots((day + timedelta(minutes=15 * i), p, None) for i, p in enumerate(prices))


def test_no_short_fragment_in_running_quarter():
    # 10:45-11:00 net goedkoop (0.29), 11:00 duurder, 12:00-17:00 het echte goedkope blok
    p = [0.40] * 43 + [0.29] + [0.31] * 4 + [0.20] * 20 + [0.40] * 28
    prices = quarters(at(0), p)
    st = State(zappi_plug="EV Connected", carcharger_mode="auto", cars=car("330"))  # 70 km = 1u05
    plan = make_plan(cfg(), prices, st, at(10, 51))
    fast = [s for s in plan.slots if s.zappi_mode == "Fast"]
    assert fast[0].start >= at(12) and all(b.start == a.end for a, b in zip(fast, fast[1:]))
    assert plan.now.zappi_mode == "Eco+"


def test_one_block_not_scattered():
    # Goedkoop om 02u en om 05u, duur ertussen: één blok, geen twee losse uren
    prices = hourly(at(0), [0.30, 0.30, 0.10, 0.25, 0.30, 0.10, 0.26] + [0.30] * 17)
    st = State(zappi_plug="EV Connected", carcharger_mode="auto", cars=car("270"))  # 130 km = 2u
    plan = make_plan(cfg(), prices, st, at(0, 0))
    hours = [s.start.astimezone(TZ).hour for s in plan.slots if s.zappi_mode == "Fast"]
    assert hours in ([2, 3], [5, 6])


def test_keeps_charging_when_later_block_barely_cheaper():
    prices = hourly(at(0), [0.200, 0.200, 0.200, 0.195, 0.195] + [0.30] * 19)
    st = State(zappi_plug="EV Connected", zappi_mode="Fast", zappi_status="Charging",
               carcharger_mode="auto", cars=car("270"))
    plan = make_plan(cfg(), prices, st, at(0, 10))
    assert plan.now.zappi_mode == "Fast"  # 0.5 ct verschil: gewoon doorladen
    # Is het later echt goedkoper (> 1 ct), dan wel stoppen
    prices = hourly(at(0), [0.200, 0.200, 0.300, 0.150, 0.150] + [0.30] * 19)
    plan = make_plan(cfg(), prices, st, at(0, 10))
    assert plan.now.zappi_mode == "Eco+"


def test_boost_fast_now_until_full_even_if_expensive():
    prices = hourly(at(0), [0.50] * 12 + [0.05] * 12)  # nu duur, later goedkoop
    st = State(zappi_plug="EV Connected", carcharger_mode="auto", cars=car("270"))  # 130 km = 2u
    plan = make_plan(cfg(car_boost=True), prices, st, at(8, 30))
    h = by_hour(plan)
    assert plan.now.zappi_mode == "Fast" and "snel laden" in plan.now.reasons["zappi"]
    assert h[9].zappi_mode == "Fast" and h[10].zappi_mode == "Fast"
    assert h[11].zappi_mode == "Eco+" and h[12].zappi_mode == "Eco+"  # vol: niet nog eens goedkoop laden
    assert plan.car.full_at == at(10, 30)
    assert plan.car.sessions[0]["kinds"] == ["snel"]


def test_uses_learned_max_and_car_time_to_full_while_charging():
    prices = hourly(at(0), [0.30] * 8 + [0.20] * 4 + [0.10] * 4 + [0.30] * 8)
    cs = CarState("Auto A", 400, cable="on", location="home", range_km="200", kwh_per_km=0.2,
                  learned_max_km=330, time_to_full_min=None)
    st = State(zappi_plug="EV Connected", carcharger_mode="auto", cars=[cs])
    plan = make_plan(cfg(), prices, st, at(8))
    assert plan.car.max_range_km == 330 and plan.car.need_km == 130  # geleerd i.p.v. 400
    # Laadt al op Fast en de auto zegt "nog 1u": dat gaat voor de berekende 2u
    cs.time_to_full_min = 60
    st = State(zappi_plug="EV Connected", zappi_mode="Fast", zappi_status="Charging",
               carcharger_mode="auto", cars=[cs])
    plan = make_plan(cfg(), prices, st, at(8))
    assert plan.car.time_source == "auto" and plan.car.need_minutes == 60
    fast = [s for s in plan.slots if s.zappi_mode == "Fast"]
    assert sum((s.end - max(s.start, at(8))).total_seconds() for s in fast) == 3600
    assert "volgens de auto" in fast[0].reasons["zappi"]


def test_no_tiny_grid_charge_near_target():
    # 94.5% bij doel 95%: er past nog maar ~0.2 kWh in; dat niet op 20 A "afronden"
    prices = hourly(at(0), [0.05] * 2 + [0.45] * 22)
    plan = make_plan(cfg(house_load_default_w=1500), prices, State(soc=94.5), at(0, 0))
    for s in plan.slots:
        assert s.ess_state != 9 or s.dvcc_current == 0 or s.grid_charge_kwh * 0.93 / 1 * 1000 / 52 >= 19.5


def test_planning_uses_full_victron_charge_power_not_learned():
    # Geleerd vermogen (3 kW, gemeten met zon) mag de planning niet afknijpen
    prices = hourly(at(0), [0.05] * 2 + [0.45] * 22)
    plan = make_plan(cfg(house_load_default_w=1500, victron_phases="1,2,3"), prices, State(soc=30), at(0, 0),
                     Forecast(battery_charge_w=3000))
    assert plan.now.ess_state == 9 and plan.now.dvcc_current >= 120


def test_evening_plugin_waits_for_cheaper_day_tomorrow():
    # 20:00 ingestoken. Vannacht 0.21, morgen 12-15u 0.14: automatisch wacht hij tot morgen
    p = [0.30] * 20 + [0.30] * 2 + [0.21] * 9 + [0.30] * 5 + [0.14] * 3 + [0.30] * 9
    prices = hourly(at(0), p)  # 48 uur vanaf 6 okt 00:00
    st = State(zappi_plug="EV Connected", carcharger_mode="auto", cars=car("270"))  # 2 uur laden
    plan = make_plan(cfg(), prices, st, at(20))
    fast = [s.start for s in plan.slots if s.zappi_mode == "Fast"]
    assert fast[0] == at(12, day=7)
    kinds = {o["kind"]: o["avg_price"] for o in plan.car.window_options}
    assert kinds == {"night": 0.21, "day": 0.14}
    # Zelf "'s nachts" gekozen: dan toch vannacht
    plan = make_plan(cfg(car_window="night"), prices, st, at(20))
    fast = [s.start.astimezone(TZ).hour for s in plan.slots if s.zappi_mode == "Fast"]
    assert all(h >= 22 or h < 7 for h in fast) and len(fast) == 2
    assert "'s nachts" in next(s for s in plan.slots if s.zappi_mode == "Fast").reasons["zappi"]


def test_hold_in_blocks_not_scattered_quarters():
    # Nacht net iets goedkoper met kleine verschillen per kwartier, dure avond, geen zon:
    # bewaren mag, maar alleen in blokken van minstens een uur
    import random

    rnd = random.Random(3)
    night = [0.30 + rnd.random() * 0.03 for _ in range(28)]  # 00:00-07:00
    day = [0.60] * 16 + [0.33] * 52  # 07:00-24:00, duur 07-11u
    prices = quarters(at(0), night + day)
    plan = make_plan(
        cfg(house_load_default_w=1500, grid_charge_enabled=False, hold_enabled=True), prices, State(soc=40), at(0, 5)
    )
    held = [s.dvcc_current == 0 and s.ess_state == ESS_KEEP_CHARGED for s in plan.slots]
    assert any(held)
    runs, cur = [], 0
    for h in held + [False]:
        if h:
            cur += 1
        elif cur:
            runs.append(cur)
            cur = 0
    assert min(runs) >= 4, runs


def test_no_night_hold_when_battery_easily_lasts():
    # Accu 84% van 47 kWh, huis 600 W, wat zon morgen, dure avond: gewoon de accu gebruiken
    prices = quarters(at(22, day=5), [0.32] * 8 + [0.30] * 28 + [0.33] * 44 + [0.61] * 16 + [0.33] * 8)
    pv = {s.start: (1.0 if 10 <= s.start.astimezone(TZ).hour < 16 else 0.0) for s in prices}
    plan = make_plan(cfg(house_load_default_w=600, grid_charge_enabled=False), prices, State(soc=84), at(22, 5, day=5),
                     Forecast(pv_kwh=pv))
    assert not any(s.dvcc_current == 0 and s.ess_state == ESS_KEEP_CHARGED for s in plan.slots)
    assert plan.summary["empty_at"] is None


def test_battery_runway_without_car_has_margin():
    # Geen zon, huis 600 W, accu 84% van 47 kWh: ruim 2 dagen; krap eerder, ruim later
    prices = quarters(at(22, day=5), [0.30] * 104)
    plan = make_plan(cfg(house_load_default_w=600), prices, State(soc=84), at(22, 5, day=5))
    r = plan.summary["runway"]
    exp, early, late = (datetime.fromisoformat(r[k]) for k in ("expected", "early", "late"))
    assert early < exp < late
    hours = (exp - at(22, 5, day=5)).total_seconds() / 3600
    assert 45 < hours < 60


def test_battery_runway_sun_refills():
    prices = quarters(at(22, day=5), [0.30] * 104)
    pv = {s.start: (2.0 if 10 <= s.start.astimezone(TZ).hour < 16 else 0.0) for s in prices}
    plan = make_plan(cfg(house_load_default_w=600), prices, State(soc=50), at(22, 5, day=5), Forecast(pv_kwh=pv))
    assert plan.summary["runway"]["expected"] is None


def _reserve_case(reserve):
    prices = quarters(at(15, day=5), [0.40] * 36 + [0.36] * 16 + [0.33] * 58 + [0.566] * 8 + [0.35] * 18)
    pv = {s.start: (0.6 if 10 <= s.start.astimezone(TZ).hour < 16 and s.start.day == 6 else 0.0) for s in prices}
    return make_plan(cfg(house_load_default_w=600, export_enabled=True, battery_reserve_soc=reserve, grid_charge_enabled=False, hold_enabled=True),
                     prices, State(soc=80), at(15, 5, day=5), Forecast(pv_kwh=pv))


def test_night_hold_for_export_at_reserve_counts_as_export():
    # Reserve 60%: terugleveren stopt op de reserve. 's Nachts bewaren (huis €0,33 van het net)
    # om morgen meer terug te leveren (€0,566) levert per kWh precies op wat het terugleveren
    # zelf oplevert (€0,24, boven export_min_spread). Loont terugleveren, dan loont dit bewaren ook.
    plan = _reserve_case(60)
    holds = [b for b in plan.battery_sessions if b["kind"] == "hold"]
    assert holds and all("meer terugleveren" in b["reason"] and "+€0,24/kWh" in b["reason"] for b in holds)


def test_hold_for_export_is_labelled_as_export():
    plan = _reserve_case(55)
    holds = [b for b in plan.battery_sessions if b["kind"] == "hold"]
    assert all("meer terugleveren" in b["reason"] for b in holds)


def test_hold_off_by_default_but_shows_what_it_would_save():
    import random

    rnd = random.Random(3)
    night = [0.30 + rnd.random() * 0.03 for _ in range(28)]
    prices = quarters(at(0), night + [0.60] * 16 + [0.33] * 52)
    plan = make_plan(cfg(house_load_default_w=1500, grid_charge_enabled=False), prices, State(soc=40), at(0, 5))
    assert not any(s.dvcc_current == 0 for s in plan.slots)
    assert plan.summary["hold_value_eur"] > 0.3
    assert plan.summary["hold_windows"]
    on = make_plan(cfg(house_load_default_w=1500, grid_charge_enabled=False, hold_enabled=True), prices, State(soc=40), at(0, 5))
    assert any(s.dvcc_current == 0 for s in on.slots)
    assert on.summary["hold_value_eur"] == plan.summary["hold_value_eur"]



AUTUMN = [0.23, 0.22, 0.215, 0.21, 0.215, 0.225, 0.26, 0.30, 0.31, 0.28, 0.26, 0.25,
          0.245, 0.245, 0.25, 0.26, 0.28, 0.32, 0.34, 0.33, 0.30, 0.27, 0.25, 0.24]


def test_autumn_small_spread_explains_why_no_grid_charge():
    # Herfst: nacht €0,21, avondpiek €0,34. Na laadverlies een paar cent winst, onder de drempel
    prices = hourly(at(0), AUTUMN * 2)
    plan = make_plan(cfg(house_load_default_w=600, arbitrage_min_spread=0.08), prices, State(soc=18), at(14))
    assert plan.summary["grid_charge_kwh"] == 0
    assert any("voor eigen gebruik, minder dan de drempel €0,08/kWh: niet geladen" in n for n in plan.notes)


def test_autumn_charges_at_night_and_keeps_it_for_the_evening_peak():
    # Accu bijna leeg, weinig zon. 's Nachts laden alleen loont niet (de accu loopt in de
    # goedkope ochtend weer leeg); laden én bewaren tot de avondpiek wel.
    prices = hourly(at(0), AUTUMN * 2)
    c = cfg(house_load_default_w=600, arbitrage_min_spread=0.04, hold_enabled=True)
    plan = make_plan(c, prices, State(soc=18), at(14))
    h = by_hour(plan, day=7)
    charges = [b for b in plan.battery_sessions if b["kind"] == "charge"]
    assert charges and all(b["start"].startswith("2026-10-07T0") for b in charges)
    assert h[5].ess_state == ESS_KEEP_CHARGED and h[5].dvcc_current == 0  # bewaren in de goedkope ochtend
    assert h[18].ess_state == ESS_OPTIMIZED and h[18].soc < h[17].soc  # accu dekt de piek
    assert plan.summary["saving_eur"] > 0
    assert not any("niet geladen" in n for n in plan.notes)
    # Zonder bewaren niet de moeite (onder de drempel), maar de schakelaar laat zien wat het scheelt
    plan = make_plan(cfg(house_load_default_w=600, arbitrage_min_spread=0.04), prices, State(soc=18), at(14))
    assert plan.summary["grid_charge_kwh"] == 0
    assert plan.summary["hold_value_eur"] > 0.1


def test_without_hold_note_says_what_hold_would_save():
    prices = hourly(at(0), AUTUMN * 2)
    plan = make_plan(cfg(house_load_default_w=600, arbitrage_min_spread=0.04), prices, State(soc=18), at(14))
    assert plan.summary["grid_charge_kwh"] == 0
    assert any('met "Accu bewaren" aan scheelt' in n for n in plan.notes)


def test_week_end_value_fills_battery_after_cheap_windy_spell():
    # Windig: de komende 36 uur allemaal rond €0,20. Vorige week kostte stroom normaal €0,30.
    prices = hourly(at(0), [0.21, 0.20, 0.19, 0.19, 0.20, 0.21] * 8)
    history = hourly(at(0) - timedelta(days=7), [0.30] * 24 * 7) + prices
    base = cfg(house_load_default_w=500, arbitrage_min_spread=0.03)
    careful = make_plan(base, prices, State(soc=15), at(1), history=history)
    week = make_plan(cfg(house_load_default_w=500, arbitrage_min_spread=0.03, battery_end_value="week"),
                     prices, State(soc=15), at(1), history=history)
    assert careful.summary["grid_charge_kwh"] < 1
    assert week.summary["grid_charge_kwh"] > 10
    assert "afgelopen week" in week.summary["end_value_why"]


def test_week_end_value_waits_for_tomorrows_prices_and_picks_cheapest_day():
    history = hourly(at(0) - timedelta(days=7), [0.30] * 24 * 7)
    c = cfg(house_load_default_w=500, arbitrage_min_spread=0.03, battery_end_value="week")
    # 10:00, alleen vandaag bekend: niet vol laden, morgen kan goedkoper zijn
    today = hourly(at(0), [0.21] * 24)
    plan = make_plan(c, today, State(soc=15), at(10), history=history + today)
    assert plan.summary["grid_charge_kwh"] < 3
    # 14:00, morgen nog goedkoper bekend: vandaag hooguit wat nodig is, vol laden morgen
    both = hourly(at(0), [0.21] * 24 + [0.15] * 24)
    plan = make_plan(c, both, State(soc=15), at(14), history=history + both)
    today_kwh = sum(s.grid_charge_kwh for s in plan.slots if s.start.astimezone(TZ).day == 6)
    tomorrow_kwh = sum(s.grid_charge_kwh for s in plan.slots if s.start.astimezone(TZ).day == 7)
    assert tomorrow_kwh > 10 and today_kwh < 3


def test_uses_victron_ess_minimum_as_floor():
    prices = hourly(at(0), AUTUMN * 2)
    plan = make_plan(cfg(house_load_default_w=600), prices, State(soc=8, min_soc=5), at(14))
    assert min(s.soc for s in plan.slots) == 5.0
    # Al onder het minimum: dat zegt hij ook zo
    plan = make_plan(cfg(house_load_default_w=600, hold_enabled=True), prices, State(soc=8), at(14))
    assert "nu al op of onder het minimum (8%, minimum 10%)" in plan.summary["empty_why"]


def test_margin_charges_buffer_but_battery_may_go_lower():
    # Laag en drempel te hoog voor gewoon laden: zonder marge laadt hij niets en zakt hij naar 5%.
    # Met marge laadt hij vooraf in de goedkoopste uren, zodat er in de dure uren ±15% in zit.
    prices = hourly(at(0), AUTUMN * 2)
    st = State(soc=12, min_soc=5)
    kw = dict(house_load_default_w=900, arbitrage_min_spread=0.5)
    base = make_plan(cfg(**kw), prices, st, at(14))
    assert sum(s.grid_charge_kwh for s in base.slots) == 0
    assert min(s.soc for s in base.slots) == 5.0
    marg = make_plan(cfg(**kw, battery_margin_pct=10), prices, st, at(14))
    charged = [s for s in marg.slots if s.grid_charge_kwh > 0]
    assert charged and all("marge" in s.reasons["ess"] for s in charged)
    # In de dure avond van vandaag blijft er een buffer; zonder marge was hij daar leeg
    evening = [s for s in marg.slots if s.start.astimezone(TZ).day == 6 and 17 <= s.start.astimezone(TZ).hour <= 21]
    assert min(s.soc for s in evening) >= 14.5
    assert marg.summary["margin_charge_kwh"] > 0 and not marg.summary["grid_charge_skip"]
    # Geen "bewaren" op de marge: in het echt mag hij onder 15% komen
    assert not any(s.dvcc_current == 0 for s in marg.slots)
