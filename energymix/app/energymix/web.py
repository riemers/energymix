"""Web-API + de React-frontend (via HA Ingress of rechtstreeks op poort 8099)."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from aiohttp import web

from .engine import Engine

STATIC = Path(__file__).parent / "static"


def create_app(engine: Engine) -> web.Application:
    app = web.Application()

    async def status(_req: web.Request) -> web.Response:
        cfg = engine.cfg
        return web.json_response(
            {
                "shadow": cfg.shadow,
                "control": asdict(cfg.control),
                "timezone": cfg.timezone,
                "cheap_price": cfg.cheap_price,
                "force_fast_price": cfg.force_fast_price,
                "ha_connected": engine.ha.connected.is_set(),
                "errors": engine.errors,
                "state": asdict(engine.state) if engine.state else None,
                "plan": engine.plan.to_dict() if engine.plan else None,
                "actions": [a.to_dict() for a in engine.executor.last_actions],
            }
        )

    async def decisions(req: web.Request) -> web.Response:
        limit = min(int(req.query.get("limit", 100)), 1000)
        return web.json_response(engine.store.decisions(limit))

    async def replan(_req: web.Request) -> web.Response:
        await engine.refresh_prices(force=True)
        await engine.cycle()
        return await status(_req)

    async def index(_req: web.Request) -> web.StreamResponse:
        f = STATIC / "index.html"
        if not f.exists():
            return web.Response(text="Frontend niet gebouwd (cd frontend && npm run build)", status=503)
        return web.FileResponse(f, headers={"Cache-Control": "no-cache"})

    app.router.add_get("/api/status", status)
    app.router.add_get("/api/decisions", decisions)
    app.router.add_post("/api/replan", replan)
    app.router.add_get("/", index)

    async def icon(_req: web.Request) -> web.StreamResponse:
        return web.FileResponse(STATIC / "icon.png")

    app.router.add_get("/icon.png", icon)
    if (STATIC / "assets").exists():
        app.router.add_static("/assets", STATIC / "assets")
    return app
