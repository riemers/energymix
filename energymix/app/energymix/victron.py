"""Schrijven naar de Victron GX via MQTT (alleen in live mode)."""

from __future__ import annotations

import json
import logging

import aiomqtt

log = logging.getLogger(__name__)


class Victron:
    def __init__(self, host: str, port: int, portal_id: str, vebus: str, username: str = "", password: str = ""):
        self.host, self.port = host, port
        self.portal_id, self.vebus = portal_id, vebus
        self.username, self.password = username or None, password or None

    @property
    def configured(self) -> bool:
        return bool(self.host and self.portal_id)

    def topic(self, kind: str) -> str:
        p = self.portal_id
        return {
            "ess": f"W/{p}/settings/0/Settings/CGwacs/BatteryLife/State",
            "dvcc": f"W/{p}/settings/0/Settings/SystemSetup/MaxChargeCurrent",
            "feed_in": f"W/{p}/vebus/{self.vebus}/Hub4/DisableFeedIn",
        }[kind]

    async def write(self, kind: str, value: int) -> None:
        async with aiomqtt.Client(self.host, self.port, username=self.username, password=self.password) as c:
            await c.publish(self.topic(kind), json.dumps({"value": value}))
        log.info("MQTT %s <- %s", self.topic(kind), value)
