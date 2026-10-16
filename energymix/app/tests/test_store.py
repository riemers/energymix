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
    eff = st.roundtrip_efficiency(TZ, 47)
    assert eff["days"] == [] and eff["overall"] is None
    assert eff["checked"][0]["reason"] == "te weinig geladen of ontladen"


def test_roundtrip_efficiency_falls_back_to_power_when_counters_miss_a_path(tmp_path):
    st = Store(tmp_path / "db.sqlite")
    day = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=2)
    stored = 24 * 0.93 - 16 / 0.9
    # Tellers zien maar een derde van het laden (zon via de AC-uitgang telde niet mee): >100%
    _cycle_day(st, day, lambda m: 3000 if m < 480 else (-2000 if m < 960 else 0),
               lambda m: 40 + stored / 47 * 100 * m / 1439,
               lambda m: (min(m, 480) * 1 / 60, max(0, min(m, 960) - 480) * 2 / 60))
    eff = st.roundtrip_efficiency(TZ, 47)
    assert 0.82 <= eff["days"][0]["efficiency"] <= 0.85
    assert eff["checked"][-1]["source"] == "vermogen"


def test_learned_capacity_from_charge_and_discharge(tmp_path):
    st = Store(tmp_path / "db.sqlite")
    t = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(days=2)
    cap, soc = 52.0, 20.0
    # 's nachts laden met 5 kW (in de accu 0,97 ervan naar SoC), daarna ontladen met 2 kW (1/0,97)
    for _ in range(5 * 60):
        soc = min(100.0, soc + 5 * 0.97 / 60 / cap * 100)
        st.add_sample(t, soc=round(soc), battery_w=5000 if soc < 100 else 300)
        t += timedelta(minutes=1)
    for _ in range(12 * 60):
        soc -= 2 / 0.97 / 60 / cap * 100
        st.add_sample(t, soc=round(soc), battery_w=-2000)
        t += timedelta(minutes=1)
    c = st.learned_capacity()
    assert c["runs"] == 2
    assert c["charge_kwh"] > cap > c["discharge_kwh"]
    assert abs(c["kwh"] - cap) < 2


def test_learned_capacity_needs_big_swings(tmp_path):
    st = Store(tmp_path / "db.sqlite")
    t = datetime.now(timezone.utc) - timedelta(days=1)
    for m in range(120):
        st.add_sample(t + timedelta(minutes=m), soc=50 + m / 20, battery_w=1500)
    assert st.learned_capacity()["kwh"] is None


def test_house_profile_fills_missing_hours_and_reports_skips(tmp_path):
    st = Store(tmp_path / "db.sqlite")
    base = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    for h in range(8):
        for m in range(15):
            st.add_sample(base + timedelta(hours=h, minutes=m), house_w=1000 + 100 * h, zappi_w=0)
    st.add_sample(base + timedelta(hours=9), house_w=9000, zappi_w=7000)
    st.add_sample(base + timedelta(hours=10), house_w=None)
    info = st.house_profile_info(TZ)
    assert info["hours_measured"] == 8
    assert len(info["profile"]) == 24
    assert info["profile"][20] == info["avg_w"] == 1350
    assert info["skipped_car"] == 1 and info["skipped_no_value"] == 1


def test_roundtrip_cycles_close_when_soc_returns(tmp_path):
    from energymix.store import roundtrip_cycles

    st = Store(tmp_path / "db.sqlite")
    t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(days=3)
    # 50% → 80% laden met 3 kW AC (0,93 erin), dan terug naar 50% leveren (0,9 eruit), over middernacht heen
    soc, m = 50.0, 0
    for phase, ac, dc in (("up", 3000, 2790), ("down", -2000, -2222)):
        while (soc < 80 if phase == "up" else soc > 50):
            st.add_sample(t0 + timedelta(minutes=m), soc=soc, inverter_ac_w=ac, battery_w=dc)
            soc += dc / 60 / 1000 / 47 * 100
            m += 1
    st.add_sample(t0 + timedelta(minutes=m), soc=50.0, inverter_ac_w=0, battery_w=0)
    cyc = roundtrip_cycles(st.samples(t0 - timedelta(minutes=1)), 47)
    assert cyc["count"] == 1
    c = cyc["list"][0]
    assert c["soc_min"] == 50 and c["soc_max"] == 80
    assert 0.82 <= c["efficiency"] <= 0.85


def test_power_curve_per_kw_and_standby(tmp_path):
    from energymix.store import power_curve

    st = Store(tmp_path / "db.sqlite")
    t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(days=1)
    rows = [(3000, 2850)] * 30 + [(8000, 7200)] * 30 + [(-3000, -3200)] * 30 + [(60, -10)] * 30 + [(3000, 900)] * 3
    for i, (ac, dc) in enumerate(rows):
        st.add_sample(t0 + timedelta(minutes=i), soc=50, inverter_ac_w=ac, battery_w=dc)
    pc = power_curve(st.samples(t0))
    by = {b["kw"]: b for b in pc["bins"]}
    assert by[3]["charge"] == 0.95 and by[8]["charge"] == 0.9  # uitschieter (900 W DC) telt niet
    assert by[3]["discharge"] == round(3000 / 3200, 3) and by[3]["roundtrip"] is not None
    assert pc["standby_w"] == 70
