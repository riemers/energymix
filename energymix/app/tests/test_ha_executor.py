import asyncio
from datetime import datetime, timedelta, timezone

import aiohttp
from aiohttp import web

from energymix.config import Config, Control
from energymix.executor import Executor
from energymix.ha import HomeAssistant
from energymix.planner import SlotPlan
from energymix.store import Store


async def fake_ha(calls: list):
    async def ws_handler(req):
        ws = web.WebSocketResponse()
        await ws.prepare(req)
        await ws.send_json({"type": "auth_required"})
        auth = await ws.receive_json()
        assert auth["access_token"] == "tok"
        await ws.send_json({"type": "auth_ok"})
        async for msg in ws:
            m = msg.json()
            if m["type"] == "get_states":
                res = [{"entity_id": "switch.pv", "state": "on"}, {"entity_id": "select.zappi", "state": "Eco+"}]
                await ws.send_json({"id": m["id"], "type": "result", "success": True, "result": res})
            elif m["type"] == "subscribe_events":
                await ws.send_json({"id": m["id"], "type": "result", "success": True, "result": None})
                await ws.send_json({"type": "event", "event": {"data": {
                    "entity_id": "select.zappi", "new_state": {"entity_id": "select.zappi", "state": "Fast"}}}})
            elif m["type"] == "call_service":
                calls.append(m)
                await ws.send_json({"id": m["id"], "type": "result", "success": True, "result": {}})
        return ws

    app = web.Application()
    app.router.add_get("/api/websocket", ws_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}"


def slot(**kw):
    now = datetime.now(timezone.utc)
    return SlotPlan(now, now + timedelta(hours=1), -0.01, **kw, reasons={"pv": "neg", "zappi": "goedkoop"})


async def test_ha_client_and_executor(tmp_path):
    calls: list = []
    runner, url = await fake_ha(calls)
    async with aiohttp.ClientSession() as session:
        ha = HomeAssistant(session, url, "tok")
        changes = []
        ha.on_change(lambda e, s: changes.append((e, s["state"])))
        task = asyncio.create_task(ha.run())
        await asyncio.wait_for(ha.connected.wait(), 5)
        await asyncio.sleep(0.1)
        assert ha.state("switch.pv") == "on"
        assert ha.state("select.zappi") == "Fast"
        assert changes == [("select.zappi", "Fast")]

        store = Store(tmp_path / "t.db")
        cfg = Config(pv_switch_entity="switch.pv", zappi_mode_entity="select.zappi")

        # Shadow: niets aanroepen, wel loggen
        ex = Executor(cfg, ha, None, store)
        acts = await ex.apply(slot(pv_on=False, zappi_mode="Fast"))
        assert not calls
        assert {a.component: a.executed for a in acts} == {"pv": False, "zappi": False}
        assert len(store.decisions()) == 2

        # Live pv: alleen wat afwijkt (zappi staat al op Fast)
        cfg.control = Control(pv=True, zappi=True)
        acts = await ex.apply(slot(pv_on=False, zappi_mode="Fast"))
        assert [c["service"] for c in calls] == ["turn_off"]
        assert calls[0]["target"] == {"entity_id": "switch.pv"}
        task.cancel()
    await runner.cleanup()


def test_supervisor_token_from_s6_env(tmp_path, monkeypatch):
    from energymix import ha as ha_mod

    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    monkeypatch.delenv("HASSIO_TOKEN", raising=False)
    monkeypatch.setattr(ha_mod, "S6_ENV", tmp_path)
    assert ha_mod.supervisor_token() == ""
    (tmp_path / "SUPERVISOR_TOKEN").write_text("abc\n")
    assert ha_mod.supervisor_token() == "abc"
    monkeypatch.setenv("SUPERVISOR_TOKEN", "env")
    assert ha_mod.supervisor_token() == "env"


async def test_restore_defaults_only_for_live_components(tmp_path):
    class FakeVictron:
        configured = True

        def __init__(self):
            self.writes = []

        async def write(self, kind, value):
            self.writes.append((kind, value))

    v = FakeVictron()
    cfg = Config(control=Control(ess=True, setpoint=True, dvcc=False), grid_setpoint_default_w=50)
    ex = Executor(cfg, None, v, Store(tmp_path / "t.db"))
    ex._last_written["setpoint"] = (-4000, 0)  # was aan het terugleveren
    await ex.restore_defaults("test")
    assert sorted(v.writes) == [("ess", 10), ("setpoint", 50)]  # dvcc niet live: niet aanraken


async def test_master_off_restores_once(tmp_path):
    from energymix.engine import Engine

    e = Engine(Config(control=Control(ess=True)), None, Store(tmp_path / "t.db"))
    calls = []

    async def fake_restore(why):
        calls.append(why)

    e.executor.restore_defaults = fake_restore
    e.ha.connected.set()
    e.base_cfg.create_helpers = False
    master = "input_boolean.energymix_aansturen"
    e.ha.states = {master: {"state": "off"}}
    await e._helpers()  # eerste keer uit: niets terugzetten (was al uit)
    assert calls == []
    e.ha.states[master]["state"] = "on"
    await e._helpers()
    e.ha.states[master]["state"] = "off"
    await e._helpers()
    assert calls == ["Aansturen uitgezet"]
