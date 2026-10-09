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


def _cycle_day(st, day, ac_w, soc, counters=None):
    """Eén dag per minuut: ac_w(m) en soc(m) per minuut, optioneel tellers (in, out) per minuut."""
    for m in range(24 * 60):
        kw = {}
        if counters:
            kw["ac_to_inv_kwh"], kw["inv_to_ac_kwh"] = counters(m)
        st.add_sample(day + timedelta(minutes=m), soc=soc(m), inverter_ac_w=ac_w(m), **kw)


def test_roundtrip_efficiency_from_power_with_soc_change(tmp_path):
    st = Store(tmp_path / "db.sqlite")
    day = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=2)
    # 8 uur laden met 3 kW AC, 8 uur leveren met 2 kW AC; heen 0,93, terug 0,9 → 0,837 totaal.
    # Netto in de accu: 24*0,93 - 16/0,9 = 4,54 kWh op 47 kWh
    stored = 24 * 0.93 - 16 / 0.9
    _cycle_day(st, day, lambda m: 3000 if m < 480 else (-2000 if m < 960 else 0),
               lambda m: 40 + stored / 47 * 100 * m / 1439)
    eff = st.roundtrip_efficiency(TZ, 47)
    assert len(eff["days"]) == 1
    assert 0.82 <= eff["days"][0]["efficiency"] <= 0.85
    assert eff["overall"] == eff["days"][0]["efficiency"]


def test_roundtrip_efficiency_prefers_counters_and_skips_reset(tmp_path):
    st = Store(tmp_path / "db.sqlite")
    day = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=2)

    def counters(m):
        # 10 kWh in, 8,8 kWh uit, gelijk over de dag; halverwege een reset van de tellers
        base = 0 if m >= 700 else 500
        return base + 10 * m / 1440, base + 8.8 * m / 1440

    # Vermogen zegt iets heel anders: de tellers gaan voor
    _cycle_day(st, day, lambda m: 5000, lambda m: 50.0, counters)
    eff = st.roundtrip_efficiency(TZ, 47)
    assert eff["days"][0]["efficiency"] == 0.88


def test_roundtrip_efficiency_needs_real_use(tmp_path):
    st = Store(tmp_path / "db.sqlite")
    day = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=2)
    _cycle_day(st, day, lambda m: 30, lambda m: 50.0)  # alleen eigenverbruik
    assert st.roundtrip_efficiency(TZ, 47) == {"days": [], "overall": None}
