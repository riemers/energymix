from datetime import datetime, timedelta, timezone

from energymix.config import Config
from energymix.helpers import HELPERS, apply_overrides, read_helpers
from energymix.planner import SlotPlan, State
from energymix.regulator import RAISE_AFTER, Regulator

NOW = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)


def slot(ess=9, dvcc=150):
    return SlotPlan(NOW, NOW + timedelta(minutes=15), 0.05, ess_state=ess, dvcc_current=dvcc, reasons={"dvcc": "plan"})


def cfg():
    return Config(grid_max_import_w=17000, grid_margin_w=1500, battery_nominal_voltage=52, dvcc_step_a=10,
                  dvcc_min_charge_current=20, dvcc_max_charge_current=150, zappi_max_w=11000)


def test_not_charging_from_grid_uses_plan_value():
    r = Regulator(cfg())
    assert r.step(slot(ess=10), State(grid_w=5000), 0)[0] == 150


def test_limits_to_headroom_in_steps():
    r = Regulator(cfg())
    # Huis 2 kW + accu 3 kW: andere lasten 2 kW -> ruimte 13.5 kW -> 259 A -> plan 150
    a, _ = r.step(slot(), State(grid_w=5000, battery_w=3000), 0)
    assert a == 150
    # Auto gaat laden: 11 kW + huis 2 kW -> ruimte 2.5 kW = 48 A -> 40 A, direct omlaag
    a, why = r.step(slot(), State(grid_w=13000 + 7800, battery_w=7800, zappi_mode="Fast", zappi_status="Charging", zappi_w=11000), 30)
    assert a == 40 and why.startswith("40 A") and "omlaag" in why


def test_raises_slowly_one_step_after_delay():
    r = Regulator(cfg())
    r.st.current_a = 40
    st = State(grid_w=2000, battery_w=0)
    assert r.step(slot(), st, 0)[0] == 40
    assert r.step(slot(), st, RAISE_AFTER - 1)[0] == 40
    assert r.step(slot(), st, RAISE_AFTER + 1)[0] == 50


def test_zappi_throttling_counts_as_occupied():
    r = Regulator(cfg())
    r.st.current_a = 60
    # Zappi op Fast trekt maar 9 kW (teruggeregeld): 2 kW tekort telt als bezet
    st = State(grid_w=9000 + 1500 + 3120, battery_w=3120, zappi_mode="Fast", zappi_status="Charging", zappi_w=9000)
    a, why = r.step(slot(), st, 0)
    assert r.st.car_throttled and "auto gaat voor" in why
    # ruimte = 17000 - 1500 - 10500 - 2000 = 3000 W -> 57 A -> 50 A
    assert a == 50


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
        ids["car_min_range_km"]: "unavailable",
    })
    vals = read_helpers(ha)
    assert vals == {"master": False, "export_enabled": True, "battery_target_soc": 90.0,
                    "car_ready_time": "06:45", "season_mode": "night"}
    c = apply_overrides(Config(), vals)
    assert c.export_enabled and c.battery_target_soc == 90 and c.car_min_range_km == 250
