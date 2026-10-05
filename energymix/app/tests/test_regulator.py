from datetime import datetime, timedelta, timezone

from energymix.config import Config
from energymix.helpers import HELPERS, apply_overrides, read_helpers
from energymix.phases import battery_ac_limit_w, expected_others_a
from energymix.planner import SlotPlan, State
from energymix.regulator import RAISE_AFTER, Regulator

NOW = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)
V_BATT = 52


def slot(ess=9, dvcc=150):
    return SlotPlan(NOW, NOW + timedelta(minutes=15), 0.05, ess_state=ess, dvcc_current=dvcc, reasons={"dvcc": "plan"})


def cfg(**kw):
    base = dict(grid_phases=3, grid_phase_max_a=25, grid_phase_margin_a=2, grid_voltage=230,
                victron_phases="1", zappi_phases=3, battery_nominal_voltage=V_BATT, charge_efficiency=1.0,
                dvcc_step_a=10, dvcc_min_charge_current=20, dvcc_max_charge_current=150, zappi_max_w=11000)
    base.update(kw)
    return Config(**base)


def test_not_charging_from_grid_uses_plan_value():
    r = Regulator(cfg())
    assert r.step(slot(ess=10), State(grid_w=5000), 0)[0] == 150


def test_hold_means_zero():
    r = Regulator(cfg())
    assert r.step(slot(dvcc=0), State(grid_w=5000, phase_a=[5, 5, 5]), 0)[0] == 0


def test_one_full_phase_limits_victron_on_that_phase():
    r = Regulator(cfg())
    # L1: 15 A huis + accu laadt 4600 W (=20 A) -> zonder accu 15 A -> 8 A vrij = 1840 W = 35 A DC -> 30 A
    st = State(grid_w=1, battery_w=4600, phase_a=[35, 2, 2])
    a, why = r.step(slot(), st, 0)
    assert a == 30 and "L1 nog 8.0 A vrij" in why and "gemeten per fase" in why


def test_full_phase_elsewhere_does_not_matter_for_single_phase_victron():
    r = Regulator(cfg())
    # Oven op L2 (24 A), Victron op L1: L1 heeft nog 22 A = 5060 W -> 97 A -> 90 A
    a, _ = r.step(slot(), State(grid_w=1, battery_w=0, phase_a=[1, 24, 1]), 0)
    assert a == 90


def test_three_phase_victron_follows_tightest_phase():
    r = Regulator(cfg(victron_phases="1,2,3"))
    # Accu laadt 6900 W over 3 fases (10 A per fase). L3 heeft 18 A huis -> 5 A vrij -> 3 * 5 A * 230 = 3450 W
    a, why = r.step(slot(), State(grid_w=1, battery_w=6900, phase_a=[12, 12, 28]), 0)
    assert a == 60 and "L3" in why  # 3450 / 52 = 66 -> 60


def test_no_room_means_zero_not_minimum():
    r = Regulator(cfg())
    # Auto 16 A + huis 8 A op L1: geen ruimte meer -> 0 A, niet het minimum van 20 A
    a, why = r.step(slot(), State(grid_w=1, battery_w=0, phase_a=[24, 16, 16], zappi_mode="Fast",
                                  zappi_status="Charging", zappi_w=11000), 0)
    assert a == 0 and "fase vol" in why


def test_raises_after_delay_by_half_the_gap():
    r = Regulator(cfg())
    r.st.current_a = 40
    st = State(grid_w=1, battery_w=0, phase_a=[1, 1, 1])  # L1: 22 A vrij -> 97 A -> doel 90
    assert r.step(slot(), st, 0)[0] == 40
    assert r.step(slot(), st, RAISE_AFTER - 1)[0] == 40
    assert r.step(slot(), st, RAISE_AFTER + 1)[0] == 60  # helft van 50, afgerond op 10
    assert r.step(slot(), st, 2 * RAISE_AFTER + 2)[0] == 70
    assert r.step(slot(), st, 3 * RAISE_AFTER + 3)[0] == 80


def test_more_room_than_planned_charges_faster():
    # Jouw situatie: plan 70 A (ingeschat zonder zon), maar gemeten 12.9 A vrij op de krapste fase
    r = Regulator(cfg(victron_phases="1,2,3"))
    r.st.current_a = 70
    st = State(grid_w=1, battery_w=3800, phase_a=[13.7, 14.6, 16.0], zappi_mode="Fast",
               zappi_status="Charging", zappi_w=11400)
    r.step(slot(dvcc=70), st, 0)
    a, why = r.step(slot(dvcc=70), st, RAISE_AFTER + 1)
    assert a > 70 and "max 150 A" in why


def test_zappi_throttling_counts_as_occupied():
    r = Regulator(cfg())
    r.st.current_a = 60
    # Zappi op Fast trekt maar 9 kW (teruggeregeld): 2 kW tekort = 2.9 A per fase telt als bezet
    st = State(grid_w=1, battery_w=0, phase_a=[16, 13, 13], zappi_mode="Fast", zappi_status="Charging", zappi_w=9000)
    a, why = r.step(slot(), st, 0)
    assert r.st.car_throttled and "auto gaat voor" in why
    # L1: 25 - 2 - 16 - 2.9 = 4.1 A = 943 W -> 18 A -> onder minimum -> 0
    assert a == 0


def test_fallback_without_phase_sensors_spreads_evenly():
    r = Regulator(cfg())
    # Geen fasemeting: 3450 W rest (5 A per fase), auto 0
    a, why = r.step(slot(), State(grid_w=3450, battery_w=0), 0)
    assert "aanname gelijk verdeeld" in why
    assert a == 70  # L1: 25-2-5 = 18 A = 4140 W -> 79 A -> 70 A


def test_planner_phase_limit():
    c = cfg()
    # Auto 11 kW over 3 fases (15.9 A) + huis 690 W (1 A/fase): L1 nog 6.06 A = 1393 W
    assert round(battery_ac_limit_w(c, expected_others_a(c, 690, 11000))) == 1393


def test_helpers_override_config():
    class FakeHA:
        def __init__(self, states):
            self.s = states

        def state(self, eid):
            return self.s.get(eid, "")

    ids = {h.key: h.entity_id for h in HELPERS}
    ha = FakeHA({
        ids["master"]: "off", ids["export_enabled"]: "on", ids["battery_target_soc"]: "90.0",
        ids["car_ready_time"]: "06:45:00", ids["season_mode"]: "Winter (nacht goedkoop)",
        ids["export_enabled"].replace("terugleveren", "x"): "unavailable",
    })
    vals = read_helpers(ha)
    assert vals == {"master": False, "export_enabled": True, "battery_target_soc": 90.0,
                    "car_ready_time": "06:45", "season_mode": "night"}
    c = apply_overrides(Config(), vals)
    assert c.export_enabled and c.battery_target_soc == 90 and c.car_min_range_km == 250


def test_solar_export_counts_as_room():
    from energymix.phases import headroom_a

    # L1 levert 6.8 A terug door de zon: dat is extra ruimte (de zekering ziet netto)
    assert headroom_a(cfg(), [-6.8, 5, 0]) == [29.8, 18, 23]
