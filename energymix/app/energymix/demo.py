"""Demo-modus: draait de web-UI met nepdata, zonder HA, Tibber of Victron.

    cd energymix/app && python -m energymix.demo   # http://localhost:8099
"""

from __future__ import annotations

import asyncio
import math
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import aiohttp
from aiohttp import web

from .config import Car, Config
from .engine import Engine
from .prices import build_slots
from .store import Store
from .web import create_app


def fake_prices(tz: ZoneInfo):
    day = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    pts = []
    for i in range(48):
        h = i % 24
        p = 0.24 + 0.08 * math.sin((h - 13) / 24 * 2 * math.pi) + (0.06 if 17 <= h <= 20 else 0)
        if i in (13, 14):  # negatief rond de middag
            p = -0.03 if i == 13 else -0.01
        if 2 <= h <= 4:
            p -= 0.12
        pts.append((day + timedelta(hours=i), round(p, 4), None))
    return build_slots(pts)


async def main() -> None:
    cfg = Config(
        pv_switch_entity="switch.pv",
        zappi_plug_entity="sensor.zappi_plug",
        zappi_status_entity="sensor.zappi_status",
        zappi_mode_entity="select.zappi_mode",
        sunchance_entity="sensor.sunchance",
        cars=[Car("Model Y", 400, "binary_sensor.y_cable", "device_tracker.y", "sensor.y_range")],
    )
    tz = ZoneInfo(cfg.timezone)
    store = Store(Path(os.environ.get("ENERGYMIX_DB", "/tmp/energymix-demo.db")))
    async with aiohttp.ClientSession() as session:
        engine = Engine(cfg, session, store)
        engine.ha.states = {
            k: {"state": v}
            for k, v in {
                cfg.battery_soc_entity: "64",
                cfg.solar_today_entity: "38",
                "sensor.sunchance": "70",
                cfg.carcharger_select_entity: "auto",
                cfg.vannacht_entity: "off",
                "sensor.zappi_plug": "EV Connected",
                "sensor.zappi_status": "Paused",
                "select.zappi_mode": "Eco+",
                "switch.pv": "on",
                "binary_sensor.y_cable": "on",
                "device_tracker.y": "home",
                "sensor.y_range": "210",
            }.items()
        }
        engine.ha.connected.set()
        engine.prices = fake_prices(tz)
        engine.prices_fetched = float("inf")
        await engine.cycle()
        runner = web.AppRunner(create_app(engine))
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 8099))).start()
        print("Demo op http://localhost:8099")
        await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
