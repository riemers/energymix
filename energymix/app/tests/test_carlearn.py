from datetime import datetime, timedelta, timezone

from energymix.carlearn import CarLearner, car_base, discover, parse_time_to_full
from energymix.config import Car
from energymix.store import Store

CAR = Car("Witte Koets", 385, "binary_sensor.witte_koets_charge_cable", "device_tracker.witte_koets_location",
          "sensor.witte_koets_battery_range")


def test_discovers_tesla_entities_from_name():
    assert car_base(CAR) == "witte_koets"
    states = {e: {} for e in ["sensor.witte_koets_battery_level", "number.witte_koets_charge_limit",
                              "sensor.witte_koets_time_to_full_charge", "sensor.witte_koets_charge_rate",
                              "sensor.itesla_battery_level"]}
    assert discover(states, CAR) == {
        "battery_level": "sensor.witte_koets_battery_level",
        "charge_limit": "number.witte_koets_charge_limit",
        "time_to_full": "sensor.witte_koets_time_to_full_charge",
        "charge_rate": "sensor.witte_koets_charge_rate",
    }


def test_time_to_full_hours_or_timestamp():
    now = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)
    assert parse_time_to_full("3.5", {"unit_of_measurement": "h"}, now) == 210
    assert parse_time_to_full((now + timedelta(hours=3, minutes=30)).isoformat(), {}, now) == 210
    assert parse_time_to_full("unavailable", {}, now) is None


def test_learns_max_range_at_charge_limit_and_persists(tmp_path):
    store = Store(tmp_path / "t.db")
    lr = CarLearner(store)
    # 240 km bij 60%, laadlimiet 80% -> 320 km "vol"
    r = lr.update("Witte Koets", 240, 60, 80, None, charging_fast=False, complete=False, now=0)
    assert round(r.max_range_km) == 320
    # Te snel opnieuw: genegeerd
    r = lr.update("Witte Koets", 100, 60, 80, None, charging_fast=False, complete=False, now=10)
    assert round(r.max_range_km) == 320
    # Bewaard: nieuwe learner leest het terug
    assert round(CarLearner(store).get("Witte Koets").max_range_km) == 320


def test_learns_speed_from_charge_rate_or_range_increase():
    lr = CarLearner(None)
    for i in range(3):
        r = lr.update("A", 200, None, None, 62.0, charging_fast=True, complete=False, now=i * 400)
    assert round(r.speed_kmh) == 62
    lr2 = CarLearner(None)
    lr2.update("B", 100, None, None, None, charging_fast=True, complete=False, now=0)
    r = lr2.update("B", 130, None, None, None, charging_fast=True, complete=False, now=1800)  # 30 km in 30 min
    assert round(r.speed_kmh) == 60
