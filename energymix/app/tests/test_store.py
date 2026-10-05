from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from energymix.store import Store

TZ = ZoneInfo("Europe/Amsterdam")


def test_house_profile_ignores_car_charging_and_spikes(tmp_path):
    st = Store(tmp_path / "db.sqlite")
    base = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(days=1)
    for m in range(60):
        # Normaal 500 W; een kwartier laadt de auto (huis ten onrechte 11 kW); af en toe een piek
        charging = 20 <= m < 35
        house = 11500 if charging else (4000 if m % 30 == 0 else 500)
        st.add_sample(base + timedelta(minutes=m), house_w=house, zappi_w=11000 if charging else 0)
    prof = st.house_profile(TZ)
    assert 450 <= prof[base.astimezone(TZ).hour] <= 600
