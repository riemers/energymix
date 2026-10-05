from __future__ import annotations

import asyncio
import logging
import os
import signal
from pathlib import Path

import aiohttp
from aiohttp import web

from .config import Config
from .engine import Engine
from .store import Store
from .web import create_app


async def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Elke dashboard-refresh loggen is ruis; fouten komen nog wel door
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    cfg = Config.load()
    log = logging.getLogger("energymix")
    log.info("Energymix start in %s mode", "SHADOW" if cfg.shadow else "LIVE")

    store = Store(Path(os.environ.get("ENERGYMIX_DB", "/data/energymix.db")))
    async with aiohttp.ClientSession() as session:
        engine = Engine(cfg, session, store)
        runner = web.AppRunner(create_app(engine))
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 8099))).start()
        # Stoppen/herstarten van de add-on (SIGTERM): eerst de Victron netjes terugzetten
        task = asyncio.current_task()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, task.cancel)
        try:
            await engine.run()
        except asyncio.CancelledError:
            pass
        finally:
            await engine.shutdown()
            await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
