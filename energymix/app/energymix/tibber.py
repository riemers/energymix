from __future__ import annotations

import logging

import aiohttp

from .prices import PriceSlot, parse_tibber

log = logging.getLogger(__name__)

URL = "https://api.tibber.com/v1-beta/gql"

QUERY = """
query ($res: PriceInfoResolution) {
  viewer { homes { currentSubscription { priceInfo(resolution: $res) {
    today { total energy startsAt level }
    tomorrow { total energy startsAt level }
  } } } }
}
"""


async def fetch_prices(session: aiohttp.ClientSession, token: str, resolution: str = "HOURLY") -> list[PriceSlot]:
    async with session.post(
        URL,
        json={"query": QUERY, "variables": {"res": resolution}},
        headers={"Authorization": f"Bearer {token}"},
        timeout=aiohttp.ClientTimeout(total=30),
    ) as resp:
        resp.raise_for_status()
        body = await resp.json()
    if body.get("errors"):
        raise RuntimeError(f"Tibber: {body['errors']}")
    homes = body["data"]["viewer"]["homes"]
    info = next(
        (h["currentSubscription"]["priceInfo"] for h in homes if h.get("currentSubscription")),
        None,
    )
    if not info:
        raise RuntimeError("Tibber: geen actief abonnement gevonden")
    slots = parse_tibber(info)
    log.info("Tibber: %d prijsslots opgehaald", len(slots))
    return slots
