"""Victron GX via MQTT: live waarden lezen en (in live mode) instellingen schrijven.

Lezen: de GX publiceert zijn waarden op `N/<portal>/...` zolang er elke ~30 s
een keepalive op `R/<portal>/keepalive` komt. Zo krijgen we zonder extra
HA-entities het netvermogen per fase, het accuvermogen en het aantal fases van
de Multi's.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

import aiomqtt

log = logging.getLogger(__name__)

KEEPALIVE_SECONDS = 30
STALE_SECONDS = 120  # oudere waarden negeren


class Victron:
    def __init__(self, host: str, port: int, portal_id: str, vebus: str, username: str = "", password: str = ""):
        self.host, self.port = host, port
        self.portal_id, self.vebus = portal_id, vebus
        self.username, self.password = username or None, password or None
        self.values: dict[str, tuple[float, float]] = {}  # pad -> (waarde, tijd)
        self.texts: dict[str, str] = {}  # pad -> tekst (namen, actieve accu-service)
        self.connected = False

    @property
    def configured(self) -> bool:
        return bool(self.host and self.portal_id)

    def topic(self, kind: str) -> str:
        p = self.portal_id
        return {
            "ess": f"W/{p}/settings/0/Settings/CGwacs/BatteryLife/State",
            "dvcc": f"W/{p}/settings/0/Settings/SystemSetup/MaxChargeCurrent",
            "feed_in": f"W/{p}/vebus/{self.vebus}/Hub4/DisableFeedIn",
            "setpoint": f"W/{p}/settings/0/Settings/CGwacs/AcPowerSetPoint",
        }[kind]

    async def write(self, kind: str, value: int) -> None:
        async with aiomqtt.Client(self.host, self.port, username=self.username, password=self.password) as c:
            await c.publish(self.topic(kind), json.dumps({"value": value}))
        log.info("MQTT %s <- %s", self.topic(kind), value)

    # ------------------------------------------------------------- lezen
    def _watch(self) -> list[str]:
        p = self.portal_id
        return [
            f"N/{p}/system/0/Ac/Grid/+/Power",
            f"N/{p}/system/0/Dc/Battery/Power",
            f"N/{p}/system/0/Dc/Battery/Soc",
            f"N/{p}/system/0/ActiveBatteryService",
            f"N/{p}/system/0/Ac/PvOnGrid/+/Power",
            f"N/{p}/system/0/Ac/PvOnOutput/+/Power",
            f"N/{p}/vebus/{self.vebus}/Ac/NumberOfPhases",
            # Alle accu's/monitoren die de GX kent (Lynx Shunt, BMS, ...)
            f"N/{p}/battery/+/Soc",
            f"N/{p}/battery/+/Dc/0/Power",
            f"N/{p}/battery/+/ProductName",
            f"N/{p}/battery/+/CustomName",
        ]

    def handle(self, topic: str, payload: bytes, now: float | None = None) -> None:
        prefix = f"N/{self.portal_id}/"
        if not topic.startswith(prefix):
            return
        try:
            val = json.loads(payload).get("value")
        except (ValueError, AttributeError):
            return
        path = topic[len(prefix):]
        if isinstance(val, bool):
            return
        if isinstance(val, (int, float)):
            self.values[path] = (float(val), now if now is not None else time.time())
        elif isinstance(val, str):
            self.texts[path] = val

    def get(self, path: str, now: float | None = None) -> float | None:
        v = self.values.get(path)
        if not v or (now if now is not None else time.time()) - v[1] > STALE_SECONDS:
            return None
        return v[0]

    def grid_phase_w(self) -> list[float | None]:
        return [self.get(f"system/0/Ac/Grid/L{i}/Power") for i in (1, 2, 3)]

    def battery_w(self) -> float | None:
        return self.get("system/0/Dc/Battery/Power")  # Victron: positief = laden

    def soc(self, source: str = "system") -> float | None:
        """SoC van de actieve accumonitor ("system") of van een specifieke ("battery/512")."""
        if source and source != "system":
            return self.get(f"{source}/Soc")
        return self.get("system/0/Dc/Battery/Soc")

    def pv_w(self) -> float | None:
        vals = [self.get(f"system/0/Ac/{k}/L{i}/Power") for k in ("PvOnGrid", "PvOnOutput") for i in (1, 2, 3)]
        vals = [v for v in vals if v is not None]
        return sum(vals) if vals else None

    def batteries(self) -> list[dict]:
        """Alle accu-services die de GX meldt, met naam, SoC en of het de actieve monitor is."""
        active = self.texts.get("system/0/ActiveBatteryService", "")  # bv. "com.victronenergy.battery/512"
        insts = sorted({p.split("/")[1] for p in [*self.values, *self.texts] if p.startswith("battery/")})
        out = []
        for inst in insts:
            key = f"battery/{inst}"
            name = self.texts.get(f"{key}/CustomName") or self.texts.get(f"{key}/ProductName") or f"Accu {inst}"
            out.append({
                "source": key, "name": name, "soc": self.get(f"{key}/Soc"),
                "power_w": self.get(f"{key}/Dc/0/Power"), "active": active.endswith(f"battery/{inst}"),
            })
        return out

    def vebus_phases(self) -> int | None:
        v = self.values.get(f"vebus/{self.vebus}/Ac/NumberOfPhases")
        return int(v[0]) if v else None

    async def run(self) -> None:
        """Abonneer op de live waarden; blijft herverbinden."""
        if not self.configured:
            return
        backoff = 5
        while True:
            try:
                async with aiomqtt.Client(self.host, self.port, username=self.username, password=self.password) as c:
                    for t in self._watch():
                        await c.subscribe(t)
                    self.connected = True
                    backoff = 5
                    log.info("Victron MQTT verbonden (%s)", self.host)
                    ka = asyncio.create_task(self._keepalive(c))
                    try:
                        async for msg in c.messages:
                            self.handle(str(msg.topic), msg.payload if isinstance(msg.payload, bytes) else str(msg.payload).encode())
                    finally:
                        ka.cancel()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("Victron MQTT weg (%s), opnieuw over %ss", e, backoff)
            self.connected = False
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 120)

    async def _keepalive(self, c: aiomqtt.Client) -> None:
        while True:
            await c.publish(f"R/{self.portal_id}/keepalive", "")
            await asyncio.sleep(KEEPALIVE_SECONDS)
