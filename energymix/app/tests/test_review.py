from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from energymix.store import Store

TZ = ZoneInfo("Europe/Amsterdam")


def test_day_review_compares_plan_with_measured(tmp_path):
    st = Store(tmp_path / "db.sqlite")
    day = date(2026, 11, 10)
    start = datetime(2026, 11, 10, tzinfo=TZ)
    # Plan van net voor middernacht: om 02-04u laden tot 80%
    slots = []
    for i in range(96):
        t = start + timedelta(minutes=15 * i)
        charging = 8 <= i < 16
        slots.append({"start": t.isoformat(), "end": (t + timedelta(minutes=15)).isoformat(), "price": 0.30,
                      "soc": 80.0 if i >= 16 else 20.0, "ess_state": 9 if charging else 10,
                      "dvcc_current": 100 if charging else 150, "setpoint_w": 50})
    st.save_plan({"created_at": (start - timedelta(minutes=5)).isoformat(), "slots": slots,
                  "summary": {"saving_eur": 1.5}}, {})
    # Gemeten: 02-04u laden 6 kW à €0,20, 18-20u ontladen 6 kW à €0,50 (huis 6 kW)
    for m in range(0, 24 * 60):
        t = start + timedelta(minutes=m)
        h = m / 60
        if 2 <= h < 4:
            price, batt, grid, soc = 0.20, 6000, 6000, 20 + (h - 2) * 25
        elif 18 <= h < 20:
            price, batt, grid, soc = 0.50, -6000, 0, 70 - (h - 18) * 25
        else:
            price, batt, grid, soc = 0.30, 0, 0 if h < 18 else 0, 70 if 4 <= h < 18 else (20 if h < 2 else 20)
        st.add_sample(t, soc=soc, grid_w=grid, battery_w=batt, price=price, house_w=0)
    r = st.day_review(TZ, day, 47, eff_d=0.9)
    assert len(r["slots"]) == 96
    s3 = r["slots"][12]  # 03:00
    assert s3["plan_kind"] == "charge" and s3["plan_soc"] == 20.0 and s3["soc"] is not None
    m = r["money"]
    assert abs(m["charged_kwh"] - 12) < 0.3 and abs(m["discharged_kwh"] - 12) < 0.3
    # Zonder accu: 12 kWh om 18-20u à €0,50 = €6; met accu: 12 kWh om 02-04u à €0,20 = €2,40
    assert abs(m["battery_eur"] - 3.6) < 0.1
    assert r["plan"]["saving_eur"] == 1.5
