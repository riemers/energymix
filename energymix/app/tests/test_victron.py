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
