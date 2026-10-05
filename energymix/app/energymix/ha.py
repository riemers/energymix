"""Home Assistant websocket-client.

Binnen de add-on via de Supervisor-proxy (SUPERVISOR_TOKEN), daarbuiten via
`ha_url` + `ha_token` uit de opties (handig om lokaal te ontwikkelen).
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import os
from typing import Any, Awaitable, Callable

import aiohttp

log = logging.getLogger(__name__)


class HomeAssistant:
    def __init__(self, session: aiohttp.ClientSession, url: str = "", token: str = ""):
        self._session = session
        sup = os.environ.get("SUPERVISOR_TOKEN", "")
        self.url = (url.rstrip("/") + "/api/websocket") if url else "http://supervisor/core/websocket"
        self.url = self.url.replace("https://", "wss://").replace("http://", "ws://")
        self.token = token or sup
        self.states: dict[str, dict[str, Any]] = {}
        self.connected = asyncio.Event()
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future] = {}
        self._listeners: list[Callable[[str, dict], Awaitable[None] | None]] = []

    def on_change(self, cb: Callable[[str, dict], Awaitable[None] | None]) -> None:
        self._listeners.append(cb)

    def state(self, entity_id: str) -> str:
        if not entity_id:
            return ""
        return str((self.states.get(entity_id) or {}).get("state", ""))

    async def run(self) -> None:
        backoff = 2
        while True:
            try:
                await self._connect()
                backoff = 2
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("HA websocket weg (%s), opnieuw over %ss", e, backoff)
            self.connected.clear()
            for f in self._pending.values():
                if not f.done():
                    f.set_exception(ConnectionError("HA verbinding verbroken"))
            self._pending.clear()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

    async def _connect(self) -> None:
        async with self._session.ws_connect(self.url, heartbeat=30, max_msg_size=0) as ws:
            self._ws = ws
            msg = await ws.receive_json()
            if msg.get("type") == "auth_required":
                await ws.send_json({"type": "auth", "access_token": self.token})
                msg = await ws.receive_json()
            if msg.get("type") != "auth_ok":
                raise PermissionError(f"HA auth mislukt: {msg}")
            reader = asyncio.create_task(self._reader(ws))
            try:
                states = await self._call({"type": "get_states"})
                self.states = {s["entity_id"]: s for s in states}
                await self._call({"type": "subscribe_events", "event_type": "state_changed"})
                log.info("Verbonden met Home Assistant (%d entities)", len(self.states))
                self.connected.set()
                await reader
            finally:
                reader.cancel()
                self._ws = None

    async def _reader(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        async for raw in ws:
            if raw.type != aiohttp.WSMsgType.TEXT:
                break
            msg = raw.json()
            if msg.get("type") == "result":
                fut = self._pending.pop(msg.get("id"), None)
                if fut and not fut.done():
                    if msg.get("success"):
                        fut.set_result(msg.get("result"))
                    else:
                        fut.set_exception(RuntimeError(str(msg.get("error"))))
            elif msg.get("type") == "event":
                data = msg["event"].get("data", {})
                eid, new = data.get("entity_id"), data.get("new_state")
                if not eid:
                    continue
                old = self.state(eid)
                if new is None:
                    self.states.pop(eid, None)
                else:
                    self.states[eid] = new
                if (new or {}).get("state") != old:
                    for cb in self._listeners:
                        res = cb(eid, new or {})
                        if asyncio.iscoroutine(res):
                            asyncio.create_task(res)

    async def _call(self, payload: dict) -> Any:
        if not self._ws:
            raise ConnectionError("Niet verbonden met HA")
        i = next(self._ids)
        fut = asyncio.get_running_loop().create_future()
        self._pending[i] = fut
        await self._ws.send_json({"id": i, **payload})
        return await asyncio.wait_for(fut, 30)

    async def call_service(self, domain: str, service: str, entity_id: str, data: dict | None = None) -> None:
        await self._call(
            {
                "type": "call_service",
                "domain": domain,
                "service": service,
                "target": {"entity_id": entity_id},
                "service_data": data or {},
            }
        )
