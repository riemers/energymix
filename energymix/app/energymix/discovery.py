"""Entities van bekende integraties herkennen (Envoy, myenergi, Forecast.Solar)."""

from __future__ import annotations

import re

# Bekende namen van entities uit veelgebruikte integraties, voor suggesties
SUGGEST = {
    "pv_power_entity": [r"^sensor\.envoy_.*_current_power_production$", r"^sensor\.envoy_.*_power_production$"],
    "zappi_power_entity": [r"^sensor\..*zappi.*power_ct_internal_load$", r"^sensor\..*zappi.*internal_load$",
                           r"^sensor\..*zappi.*charge_power$"],
    "zappi_plug_entity": [r"^sensor\..*zappi.*plug_status$"],
    "zappi_status_entity": [r"^sensor\..*zappi.*_status$"],
    "zappi_mode_entity": [r"^select\..*zappi.*charge_mode$"],
    "pv_switch_entity": [r"^switch\.envoy_.*_production$"],
    "solar_remaining_entity": [r"^sensor\.energy_production_today_remaining.*$"],
    "solar_tomorrow_entity": [r"^sensor\.energy_production_tomorrow.*$"],
}
EXCLUDE = {"zappi_status_entity": "plug_status"}


def suggest(states: dict, key: str) -> str | None:
    for pattern in SUGGEST.get(key, []):
        rx = re.compile(pattern)
        hits = sorted(e for e in states if rx.match(e) and not (EXCLUDE.get(key) and EXCLUDE[key] in e))
        if hits:
            return hits[0]
    return None


