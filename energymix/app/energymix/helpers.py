"""HA-helpers die Energymix zelf aanmaakt, zodat je vanuit HA kunt bijsturen.

Het zijn gewone helpers (zoals je ze via Instellingen → Helpers maakt): ze
blijven bewaard, je kunt ze op je dashboard zetten en in automations
gebruiken. Energymix leest ze elke cyclus en gebruikt ze boven de config.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from .config import Config
from .ha import HomeAssistant

log = logging.getLogger(__name__)

SEASON_OPTIONS = {
    "Automatisch": "auto",
    "Zomer (middag goedkoop)": "day",
    "Winter (nacht goedkoop)": "night",
}


@dataclass
class Helper:
    entity_id: str
    key: str  # config-veld, of "master" voor de hoofdschakelaar
    create: dict
    read: Callable[[str], Any]
    label: str


def _bool(s: str) -> bool:
    return s == "on"


def _float(s: str) -> float | None:
    try:
        return float(s)
    except ValueError:
        return None


def _time(s: str) -> str | None:
    return s[:5] if len(s) >= 5 and s[2] == ":" else None


def _season(s: str) -> str | None:
    return SEASON_OPTIONS.get(s)


HELPERS: list[Helper] = [
    Helper(
        "input_boolean.energymix_aansturen", "master",
        {"type": "input_boolean/create", "name": "Energymix aansturen", "icon": "mdi:power"},
        _bool, "Aansturen (hoofdschakelaar)",
    ),
    Helper(
        "input_boolean.energymix_auto_snel_laden", "car_boost",
        {"type": "input_boolean/create", "name": "Energymix auto snel laden", "icon": "mdi:car-electric"},
        _bool, "Auto nu snel laden (tot vol)",
    ),
    Helper(
        "input_boolean.energymix_terugleveren", "export_enabled",
        {"type": "input_boolean/create", "name": "Energymix terugleveren", "icon": "mdi:transmission-tower-export"},
        _bool, "Terugleveren bij pieken",
    ),
    Helper(
        "input_boolean.energymix_accu_van_net_laden", "grid_charge_enabled",
        {"type": "input_boolean/create", "name": "Energymix accu van net laden", "icon": "mdi:battery-charging-high", "initial": True},
        _bool, "Accu goedkoop van net laden",
    ),
    Helper(
        "input_number.energymix_accu_doel", "battery_target_soc",
        {"type": "input_number/create", "name": "Energymix accu doel", "min": 50, "max": 100, "step": 5,
         "unit_of_measurement": "%", "mode": "slider", "icon": "mdi:battery-arrow-up", "initial": 95},
        _float, "Accu laden tot",
    ),
    Helper(
        "input_number.energymix_accu_reserve", "battery_reserve_soc",
        {"type": "input_number/create", "name": "Energymix accu reserve", "min": 10, "max": 80, "step": 5,
         "unit_of_measurement": "%", "mode": "slider", "icon": "mdi:battery-lock", "initial": 30},
        _float, "Reserve bij terugleveren",
    ),
    Helper(
        "input_datetime.energymix_auto_klaar_om", "car_ready_time",
        {"type": "input_datetime/create", "name": "Energymix auto klaar om", "has_date": False, "has_time": True,
         "icon": "mdi:clock-check", "initial": "07:30:00"},
        _time, "Vannacht: auto vol om",
    ),
    Helper(
        "input_select.energymix_seizoen", "season_mode",
        {"type": "input_select/create", "name": "Energymix seizoen", "options": list(SEASON_OPTIONS),
         "icon": "mdi:weather-sunny-alert", "initial": "Automatisch"},
        _season, "Seizoenpatroon",
    ),
]

BY_KEY = {h.key: h for h in HELPERS}


async def ensure_helpers(ha: HomeAssistant) -> list[str]:
    """Maak ontbrekende helpers aan. Geeft de aangemaakte entity_ids terug."""
    created = []
    for h in HELPERS:
        if h.entity_id in ha.states:
            continue
        try:
            await ha.command(h.create)
            created.append(h.entity_id)
            log.info("Helper aangemaakt: %s", h.entity_id)
        except Exception as e:  # noqa: BLE001
            log.warning("Helper %s aanmaken mislukt: %s", h.entity_id, e)
    return created


def read_helpers(ha: HomeAssistant) -> dict[str, Any]:
    """Huidige helper-waarden als config-overrides (alleen wat bestaat en geldig is)."""
    out: dict[str, Any] = {}
    for h in HELPERS:
        raw = ha.state(h.entity_id)
        if raw in ("", "unknown", "unavailable"):
            continue
        val = h.read(raw)
        if val is not None:
            out[h.key] = val
    return out


def apply_overrides(cfg: Config, values: dict[str, Any]) -> Config:
    """Kopie van de config met de helper-waarden erin."""
    from dataclasses import replace

    fields = {k: v for k, v in values.items() if k != "master" and hasattr(cfg, k)}
    return replace(cfg, **fields)


async def set_helper(ha: HomeAssistant, key: str, value: Any) -> None:
    """Zet een helper vanuit het dashboard."""
    h = BY_KEY[key]
    domain = h.entity_id.split(".")[0]
    if domain == "input_boolean":
        await ha.call_service(domain, "turn_on" if value else "turn_off", h.entity_id)
    elif domain == "input_number":
        await ha.call_service(domain, "set_value", h.entity_id, {"value": float(value)})
    elif domain == "input_datetime":
        await ha.call_service(domain, "set_datetime", h.entity_id, {"time": f"{str(value)[:5]}:00"})
    elif domain == "input_select":
        option = next((k for k, v in SEASON_OPTIONS.items() if v == value), value)
        await ha.call_service(domain, "select_option", h.entity_id, {"option": option})
