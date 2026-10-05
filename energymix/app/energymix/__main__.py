from __future__ import annotations

import asyncio
import logging
import os
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
    cfg = Config.load()
    log = logging.getLogger("energymix")
    log.info("Energymix start in %s mode", "SHADOW" if cfg.shadow else "LIVE")

    store = Store(Path(os.environ.get("ENERGYMIX_DB", "/data/energymix.db")))
    async with aiohttp.ClientSession() as session:
        engine = Engine(cfg, session, store)
        runner = web.AppRunner(create_app(engine))
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 8099))).start()
        await engine.run()


if __name__ == "__main__":
    asyncio.run(main())
