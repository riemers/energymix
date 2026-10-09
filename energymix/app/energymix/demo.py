"""Demo-modus: draait de web-UI met nepdata, zonder HA, Tibber of Victron.

    cd energymix/app && python -m energymix.demo   # http://localhost:8099
"""

from __future__ import annotations

import asyncio
import math
import os
import random
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import aiohttp
from aiohttp import web

from . import helpers
from .config import Car, Config
from .engine import Engine
from .prices import build_slots
from .store import Store
from .web import create_app


def fake_prices(tz: ZoneInfo):
    day = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    pts = []
    for i in range(48 * 4):
        h = (i / 4) % 24
        p = 0.26 + 0.10 * math.sin((h - 15) / 24 * 2 * math.pi) + (0.42 if 18 <= h < 19 else 0) - (0.14 if 12 <= h < 15 else 0)
        if i in range(52, 58):
            p = -0.02
        pts.append((day + timedelta(minutes=15 * i), round(p, 4), None, round(p - 0.15, 4)))
    # Tibber-achtige niveaus t.o.v. het gemiddelde
    avg = sum(x[1] for x in pts) / len(pts)
    def level(v):
        r = v / avg
        return "VERY_CHEAP" if r < 0.6 else "CHEAP" if r < 0.9 else "NORMAL" if r < 1.15 else "EXPENSIVE" if r < 1.4 else "VERY_EXPENSIVE"
    pts = [(t, v, level(v), e) for t, v, _, e in pts]
    return build_slots(pts)


async def main() -> None:
    os.environ.setdefault("ENERGYMIX_SETTINGS", "/tmp/energymix-demo-settings.json")
    cfg = Config(
        pv_switch_entity="switch.pv",
        zappi_plug_entity="sensor.zappi_plug",
        zappi_status_entity="sensor.zappi_status",
        zappi_mode_entity="select.zappi_mode",
        sunchance_entity="sensor.sunchance",
        pv_power_entity="sensor.pv_power",
        grid_power_entity="sensor.grid_power",
        battery_power_entity="sensor.battery_power",
        zappi_power_entity="sensor.zappi_power",
        grid_l1_entity="sensor.grid_l1",
        grid_l2_entity="sensor.grid_l2",
        grid_l3_entity="sensor.grid_l3",
        cars=[
            Car("Witte Koets", 385, "binary_sensor.wk_cable", "device_tracker.wk", "sensor.wk_range"),
            Car("iTesla", 313, "binary_sensor.it_cable", "device_tracker.it", "sensor.it_range"),
        ],
    )
    tz = ZoneInfo(cfg.timezone)
    db = Path(os.environ.get("ENERGYMIX_DB", "/tmp/energymix-demo.db"))
    store = Store(db)
    async with aiohttp.ClientSession() as session:
        engine = Engine(cfg, session, store)

        async def noop(*_a, **_k):
            return None

        engine.ha.set_state = noop  # type: ignore[assignment]
        engine.ha.command = noop  # type: ignore[assignment]

        async def call_service(domain, service, entity_id, data=None):
            st = engine.ha.states.setdefault(entity_id, {"state": ""})
            if service in ("turn_on", "turn_off"):
                st["state"] = "on" if service == "turn_on" else "off"
            elif data:
                st["state"] = str(data.get("value", data.get("option", data.get("time", ""))))

        engine.ha.call_service = call_service  # type: ignore[assignment]
        states = {
            cfg.battery_soc_entity: "58",
            cfg.solar_today_entity: "31",
            cfg.solar_remaining_entity: "22",
            cfg.solar_tomorrow_entity: "27",
            "sensor.sunchance": "65",
            cfg.carcharger_select_entity: "auto",
            cfg.vannacht_entity: "off",
            "sensor.zappi_plug": "EV Connected",
            "sensor.zappi_status": "Paused",
            "select.zappi_mode": "Eco+",
            "switch.pv": "on",
            "binary_sensor.wk_cable": "on",
            "device_tracker.wk": "home",
            "sensor.wk_range": "190",
            "sensor.wk_battery_level": "52",
            "number.wk_charge_limit": "80",
            "sensor.wk_time_to_full_charge": "3.5",
            "sensor.wk_charge_rate": "0",
            "binary_sensor.it_cable": "off",
            "device_tracker.it": "not_home",
            "sensor.it_range": "240",
            "sensor.pv_power": "4200",
            "sensor.grid_power": "-300",
            "sensor.battery_power": "2800",
            "sensor.zappi_power": "0",
            "sensor.grid_l1": "3",
            "sensor.grid_l2": "14",
            "sensor.grid_l3": "2",
            "zone.home": "zoning",
            "input_boolean.energymix_aansturen": "off",
            "input_boolean.energymix_auto_snel_laden": "off",
            "input_boolean.energymix_terugleveren": "on",
            "input_boolean.energymix_accu_van_net_laden": "on",
            "input_number.energymix_accu_doel": "95.0",
            "input_number.energymix_accu_reserve": "30.0",
            "input_number.energymix_auto_minimum": "250.0",
            "input_datetime.energymix_auto_klaar_om": "07:30:00",
            "input_select.energymix_seizoen": "Automatisch",
        }
        engine.ha.states = {k: {"state": v, "attributes": {}} for k, v in states.items()}
        engine.ha.states["zone.home"]["attributes"] = {"latitude": 52.1, "longitude": 5.1}
        for h in helpers.HELPERS:
            if h.entity_id.startswith("input_number"):
                engine.ha.states[h.entity_id]["attributes"] = {k: h.create.get(k) for k in ("min", "max", "step")}
        for eid in ("sensor.pv_power", "sensor.grid_power", "sensor.battery_power", "sensor.zappi_power"):
            engine.ha.states[eid]["attributes"] = {"unit_of_measurement": "W"}
        for eid in ("sensor.grid_l1", "sensor.grid_l2", "sensor.grid_l3"):
            engine.ha.states[eid]["attributes"] = {"unit_of_measurement": "A"}
        # Nep-GX: Lynx (actief) en de BMS van de accu's
        engine.victron.portal_id = "demo"
        import json as _json
        for t, val in [("system/0/ActiveBatteryService", "com.victronenergy.battery/512"),
                       ("system/0/Dc/Battery/Soc", 49.5), ("battery/512/Soc", 49.5),
                       ("battery/512/ProductName", "Lynx Shunt VE.Can"), ("battery/512/Dc/0/Power", 2306),
                       ("battery/1/Soc", 73.0), ("battery/1/CustomName", "Battterij"), ("battery/1/Dc/0/Power", 2037)]:
            engine.victron.handle(f"N/demo/{t}", _json.dumps({"value": val}).encode(), now=float("inf"))
        engine.ha.connected.set()
        engine.prices = fake_prices(tz)
        engine.prices_fetched = float("inf")

        # Wat historie zodat statistiek en seizoenpatroon iets laten zien
        if not store.samples(datetime.now(tz) - timedelta(days=1)):
            now = datetime.now(tz)
            for m in range(0, 7 * 24 * 60, 10):
                t = now - timedelta(minutes=m)
                h = t.hour + t.minute / 60
                soc = 55 + 30 * math.sin((h - 10) / 24 * 2 * math.pi) - (t.day % 3) * 5
                bw = 3000 * math.cos((h - 10) / 24 * 2 * math.pi)
                store.add_sample(t, soc=soc, pv_w=max(0, 5000 * math.sin((h - 7) / 12 * math.pi)), grid_w=200,
                                 battery_w=bw, house_w=500 + 400 * (17 <= h <= 22), zappi_w=0,
                                 inverter_ac_w=(bw / 0.93 if bw > 0 else bw * 0.92) + 40 + 30 * math.sin(t.day))

        async def wiggle():
            while True:
                s = engine.ha.states
                pv = max(0, 4200 + random.uniform(-600, 600))
                s["sensor.pv_power"]["state"] = f"{pv:.0f}"
                charging = s["select.zappi_mode"]["state"] == "Fast"
                s["sensor.zappi_power"]["state"] = f"{(10400 + random.uniform(-200, 200)) if charging else 0:.0f}"
                house = 650 + random.uniform(-150, 250)
                batt = min(3500, pv - house) + random.uniform(-100, 100)
                s["sensor.battery_power"]["state"] = f"{batt:.0f}"
                zw = float(s["sensor.zappi_power"]["state"])
                s["sensor.grid_power"]["state"] = f"{house + batt + zw - pv:.0f}"
                # Victron op L1, oven op L2, auto over 3 fases
                base = (house - pv) / 3 / 230
                s["sensor.grid_l1"]["state"] = f"{base + max(batt, 0) / 230 + zw / 3 / 230:.1f}"
                s["sensor.grid_l2"]["state"] = f"{base + 13 + random.uniform(-0.5, 0.5) + zw / 3 / 230:.1f}"
                s["sensor.grid_l3"]["state"] = f"{base + 1.5 + zw / 3 / 230:.1f}"
                await asyncio.sleep(3)

        asyncio.create_task(wiggle())
        await engine.cycle()
        runner = web.AppRunner(create_app(engine))
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 8099))).start()
        print("Demo op http://localhost:8099")
        asyncio.create_task(engine._fast_loop())
        await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
