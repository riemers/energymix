"""Add-on configuratie.

Alle persoonlijke gegevens (tokens, IP's, serienummers, auto's) komen uit de
add-on-opties (`/data/options.json`), nooit uit de code.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

OPTIONS_PATH = Path(os.environ.get("ENERGYMIX_OPTIONS", "/data/options.json"))


@dataclass
class Car:
    name: str
    max_range_km: float
    cable_entity: str
    location_entity: str
    range_entity: str


@dataclass
class Control:
    """Welke onderdelen de add-on echt mag aansturen. Alles uit = shadow mode."""

    pv: bool = False
    ess: bool = False
    dvcc: bool = False
    zappi: bool = False
    feed_in: bool = False

    @property
    def any(self) -> bool:
        return any(getattr(self, f.name) for f in fields(self))


@dataclass
class Config:
    # Verbindingen
    tibber_token: str = ""
    price_resolution: str = "HOURLY"  # HOURLY of QUARTER_HOURLY
    ha_url: str = ""  # leeg = via Supervisor
    ha_token: str = ""  # leeg = SUPERVISOR_TOKEN
    mqtt_host: str = ""
    mqtt_port: int = 1883
    mqtt_username: str = ""
    mqtt_password: str = ""
    victron_portal_id: str = ""
    victron_vebus_instance: str = "276"
    timezone: str = "Europe/Amsterdam"

    # Entities
    carcharger_select_entity: str = "input_select.carcharger"
    vannacht_entity: str = "input_boolean.vannacht"
    battery_soc_entity: str = "sensor.victron_battery_soc"
    solar_today_entity: str = "sensor.energy_production_today"
    sunchance_entity: str = ""
    pv_switch_entity: str = ""
    zappi_plug_entity: str = ""
    zappi_status_entity: str = ""
    zappi_mode_entity: str = ""
    cars: list[Car] = field(default_factory=list)

    # Accu
    has_battery: bool = True
    battery_capacity_kwh: float = 47.0
    battery_nominal_voltage: float = 52.0
    charge_efficiency: float = 0.93
    dvcc_max_charge_current: int = 150
    dvcc_min_charge_current: int = 20

    # Drempels
    cheap_price: float = 0.10
    force_fast_price: float = 0.15
    solar_skip_grid_charge: bool = True
    solar_skip_grid_charge_buffer: float = 1.0
    lowest_price_ess_minutes: int = 360
    lowest_price_ess_soc_below: float = 30
    lowest_price_ess_solar_below: float = 20
    charge_speed_km_per_hour: float = 65
    default_charge_minutes: int = 360
    vannacht_ready_hour: int = 8  # auto vol vóór dit uur als "vannacht" aan staat
    eco_battery_soc_min: float = 60
    eco_solar_min: float = 50
    eco_sunchance_min: float = 50
    eco_morning_start_hour: int = 5
    eco_morning_end_hour: int = 12

    # Gedrag
    interval_minutes: int = 15
    horizon_hours: int = 36
    control: Control = field(default_factory=Control)

    @property
    def shadow(self) -> bool:
        return not self.control.any

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Config":
        known = {f.name for f in fields(cls)}
        data = {k: v for k, v in raw.items() if k in known}
        data["cars"] = [Car(**c) for c in raw.get("cars") or []]
        data["control"] = Control(**(raw.get("control") or {}))
        return cls(**data)

    @classmethod
    def load(cls, path: Path = OPTIONS_PATH) -> "Config":
        if path.exists():
            return cls.from_dict(json.loads(path.read_text()))
        return cls()
