"""Add-on configuratie.

Alle persoonlijke gegevens (tokens, IP's, serienummers, auto's) komen uit de
add-on-opties (`/data/options.json`), nooit uit de code.

Instellingen die je in het dashboard aanpast (entities, drempels) worden
bewaard in `/data/settings.json` en gaan boven de add-on-opties. Geheimen
(tokens, wachtwoorden) zijn alleen via de add-on-opties te zetten.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

OPTIONS_PATH = Path(os.environ.get("ENERGYMIX_OPTIONS", "/data/options.json"))
SETTINGS_PATH = Path(os.environ.get("ENERGYMIX_SETTINGS", "/data/settings.json"))

SECRET_KEYS = {"tibber_token", "ha_token", "mqtt_password", "mqtt_username", "ha_url"}


@dataclass
class Car:
    name: str
    max_range_km: float
    cable_entity: str
    location_entity: str
    range_entity: str
    kwh_per_km: float = 0.17


@dataclass
class Control:
    """Welke onderdelen de add-on echt mag aansturen. Alles uit = shadow mode."""

    pv: bool = False
    ess: bool = False
    dvcc: bool = False
    zappi: bool = False
    feed_in: bool = False
    setpoint: bool = False  # Victron grid setpoint (terugleveren)

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

    # Entities: bediening en toestand
    carcharger_select_entity: str = "input_select.carcharger"
    vannacht_entity: str = "input_boolean.vannacht"
    battery_soc_entity: str = "sensor.victron_battery_soc"
    solar_today_entity: str = "sensor.energy_production_today"
    solar_remaining_entity: str = "sensor.energy_production_today_remaining"
    solar_tomorrow_entity: str = "sensor.energy_production_tomorrow"
    sunchance_entity: str = ""
    pv_switch_entity: str = ""
    zappi_plug_entity: str = ""
    zappi_status_entity: str = ""
    zappi_mode_entity: str = ""
    cars: list[Car] = field(default_factory=list)

    # Entities: live vermogens (W). Leeg = niet tonen / afleiden.
    pv_power_entity: str = ""
    grid_power_entity: str = ""  # positief = afname van het net
    battery_power_entity: str = ""  # positief = laden
    house_power_entity: str = ""  # leeg = afleiden uit de rest
    zappi_power_entity: str = ""

    # Aansluiting en apparaten
    grid_max_import_w: float = 17000  # bv. 3x25A
    grid_margin_w: float = 1500
    zappi_max_w: float = 11000
    export_max_w: float = 4000
    grid_setpoint_default_w: float = 50

    # Accu
    has_battery: bool = True
    battery_capacity_kwh: float = 47.0
    battery_nominal_voltage: float = 52.0
    charge_efficiency: float = 0.93
    roundtrip_efficiency: float = 0.85
    dvcc_max_charge_current: int = 150
    dvcc_min_charge_current: int = 20
    dvcc_step_a: int = 10
    battery_target_soc: float = 95
    battery_reserve_soc: float = 30
    house_load_default_w: float = 600

    # Prijzen en strategie
    cheap_price: float = 0.10
    force_fast_price: float = 0.15
    arbitrage_min_spread: float = 0.08  # €/kWh winst na verliezen om van het net te laden
    export_min_spread: float = 0.15  # €/kWh winst na verliezen om terug te leveren
    export_price: str = "total"  # total (salderen) of energy (alleen kale prijs)
    energy_tax_eur: float = 0.11  # belasting+btw per kWh, als Tibber geen kale prijs geeft
    export_enabled: bool = False
    grid_charge_enabled: bool = True
    season_mode: str = "auto"  # auto | day | night

    # Auto
    car_min_range_km: float = 250
    car_ready_time: str = "07:30"
    car_opportunistic_price: float = 0.15  # boven het minimum alleen laden onder deze prijs
    charge_speed_km_per_hour: float = 65
    default_charge_minutes: int = 360
    vannacht_ready_hour: int = 8
    eco_battery_soc_min: float = 60
    eco_solar_min: float = 50
    eco_sunchance_min: float = 50
    eco_morning_start_hour: int = 5
    eco_morning_end_hour: int = 12

    # Gedrag
    interval_minutes: int = 15
    regulator_seconds: int = 30
    horizon_hours: int = 36
    create_helpers: bool = True
    control: Control = field(default_factory=Control)

    # Oud (v0.1), nog geaccepteerd zodat bestaande configs blijven werken
    solar_skip_grid_charge: bool = True
    solar_skip_grid_charge_buffer: float = 1.0
    lowest_price_ess_minutes: int = 360
    lowest_price_ess_soc_below: float = 30
    lowest_price_ess_solar_below: float = 20

    @property
    def shadow(self) -> bool:
        return not self.control.any

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Config":
        known = {f.name for f in fields(cls)}
        data = {k: v for k, v in raw.items() if k in known and v is not None}
        data["cars"] = [Car(**c) for c in raw.get("cars") or []]
        data["control"] = Control(**(raw.get("control") or {}))
        return cls(**data)

    @classmethod
    def load(cls, path: Path = OPTIONS_PATH, settings: Path = SETTINGS_PATH) -> "Config":
        raw: dict[str, Any] = json.loads(path.read_text()) if path.exists() else {}
        if settings.exists():
            raw.update({k: v for k, v in json.loads(settings.read_text()).items() if k not in SECRET_KEYS})
        return cls.from_dict(raw)

    def to_public_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for k in SECRET_KEYS:
            d.pop(k, None)
        d["tibber_configured"] = bool(self.tibber_token)
        return d

    def update(self, changes: dict[str, Any], settings: Path = SETTINGS_PATH) -> None:
        """Pas instellingen aan vanuit het dashboard en bewaar ze."""
        known = {f.name for f in fields(self)} - SECRET_KEYS
        clean = {k: v for k, v in changes.items() if k in known}
        current = json.loads(settings.read_text()) if settings.exists() else {}
        current.update(clean)
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(json.dumps(current, indent=2, ensure_ascii=False))
        merged = Config.from_dict({**asdict(self), **clean})
        for f in fields(self):
            setattr(self, f.name, getattr(merged, f.name))
