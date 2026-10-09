import json

from energymix.config import Config
from energymix.phases import victron_phase_idx
from energymix.victron import STALE_SECONDS, Victron


def v():
    return Victron("gx", 1883, "abc123", "276")


def msg(val):
    return json.dumps({"value": val}).encode()


def test_reads_grid_phases_battery_and_vebus_phases():
    x = v()
    x.handle("N/abc123/system/0/Ac/Grid/L1/Power", msg(2300.0), now=100)
    x.handle("N/abc123/system/0/Ac/Grid/L2/Power", msg(-460), now=100)
    x.handle("N/abc123/system/0/Ac/Grid/L3/Power", msg(None), now=100)  # geen waarde: negeren
    x.handle("N/abc123/system/0/Dc/Battery/Power", msg(3100), now=100)
    x.handle("N/abc123/vebus/276/Ac/NumberOfPhases", msg(3), now=100)
    x.handle("N/other/system/0/Dc/Battery/Power", msg(1), now=100)  # ander portal-ID
    x.handle("N/abc123/system/0/Dc/Battery/Soc", b"geen json", now=100)
    assert [x.get(f"system/0/Ac/Grid/L{i}/Power", now=110) for i in (1, 2, 3)] == [2300.0, -460.0, None]
    assert x.get("system/0/Dc/Battery/Power", now=110) == 3100
    assert x.vebus_phases() == 3


def test_stale_values_are_ignored():
    x = v()
    x.handle("N/abc123/system/0/Dc/Battery/Power", msg(3100), now=0)
    assert x.get("system/0/Dc/Battery/Power", now=STALE_SECONDS + 1) is None


def test_victron_phases_parsing():
    assert victron_phase_idx(Config(victron_phases="1,2,3")) == [0, 1, 2]
    assert victron_phase_idx(Config(victron_phases="2")) == [1]
    assert victron_phase_idx(Config(victron_phases="auto")) == [0]  # pas na MQTT ingevuld


def test_engine_prefers_victron_soc_over_stale_ha_sensor(tmp_path):
    from energymix.engine import Engine
    from energymix.store import Store

    e = Engine(Config(mqtt_host="gx", victron_portal_id="p"), None, Store(tmp_path / "t.db"))
    e.ha.states = {"sensor.victron_battery_soc": {"state": "70.0", "attributes": {}}}
    assert e.collect().soc == 70.0 and e.sources["soc"] == "HA"
    e.victron.handle("N/p/system/0/Dc/Battery/Soc", msg(49.5))
    assert e.collect().soc == 49.5 and e.sources["soc"] == "Victron MQTT"


def test_lists_batteries_and_picks_chosen_monitor():
    x = v()
    x.handle("N/abc123/system/0/ActiveBatteryService", msg("com.victronenergy.battery/512"))
    x.handle("N/abc123/system/0/Dc/Battery/Soc", msg(49.5))
    x.handle("N/abc123/battery/512/Soc", msg(49.5))
    x.handle("N/abc123/battery/512/ProductName", msg("Lynx Smart BMS"))
    x.handle("N/abc123/battery/1/Soc", msg(73.0))
    x.handle("N/abc123/battery/1/CustomName", msg("Battterij"))
    bats = {b["source"]: b for b in x.batteries()}
    assert bats["battery/512"]["name"] == "Lynx Smart BMS" and bats["battery/512"]["active"]
    assert bats["battery/1"]["name"] == "Battterij" and not bats["battery/1"]["active"]
    assert x.soc() == 49.5 and x.soc("battery/1") == 73.0


def test_pv_from_victron_sums_phases():
    x = v()
    for i, w in enumerate((1135, 1054, 1146), start=1):
        x.handle(f"N/abc123/system/0/Ac/PvOnGrid/L{i}/Power", msg(w))
    assert x.pv_w() == 3335


def test_envoy_leads_victron_pv_is_backup(tmp_path):
    from energymix.engine import Engine
    from energymix.store import Store

    e = Engine(Config(mqtt_host="gx", victron_portal_id="p"), None, Store(tmp_path / "t.db"))
    for i, w in enumerate((1135, 1054, 1146), start=1):
        e.victron.handle(f"N/p/system/0/Ac/PvOnGrid/L{i}/Power", msg(w))
    envoy = "sensor.envoy_122252019205_power_production"
    # Geen entity ingesteld: Envoy wordt zelf gevonden en gaat voor
    e.ha.states = {envoy: {"state": "3400", "attributes": {"unit_of_measurement": "W"}}}
    assert e.collect().pv_w == 3400 and e.sources["pv_w"].startswith("Envoy")
    # Envoy weg: Victron als reserve
    e.ha.states[envoy]["state"] = "unavailable"
    assert e.collect().pv_w == 3335 and "Envoy niet beschikbaar" in e.sources["pv_w"]


def test_inverter_ac_power_and_energy_counters():
    x = v()
    x.connected = True
    x.handle("N/abc123/vebus/276/Ac/ActiveIn/P", msg(4000), now=100)
    x.handle("N/abc123/vebus/276/Ac/Out/P", msg(1000), now=100)
    for name, val in [("AcIn1ToInverter", 120.5), ("AcOutToInverter", 3.5), ("InverterToAcOut", 98.0),
                      ("AcIn1ToAcOut", 900.0)]:
        x.handle(f"N/abc123/vebus/276/Energy/{name}", msg(val), now=100)
    assert x.get("vebus/276/Ac/ActiveIn/P", now=110) - x.get("vebus/276/Ac/Out/P", now=110) == 3000
    assert x.inverter_energy_kwh() == (124.0, 98.0)  # doorlopen van net naar AC-uit telt niet
    x.connected = False
    assert x.inverter_energy_kwh() == (None, None)
