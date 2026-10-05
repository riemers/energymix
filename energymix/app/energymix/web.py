"""Web-API + de React-frontend (via HA Ingress of rechtstreeks op poort 8099)."""

from __future__ import annotations

import re
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from aiohttp import web

from . import helpers
from .discovery import suggest
from .engine import Engine

STATIC = Path(__file__).parent / "static"

# Velden die je in het dashboard onder Instellingen kunt aanpassen
EDITABLE = [
    # (sleutel, label, soort, groep)
    ("battery_soc_source", "Accumonitor (SoC)", "battery", "Accu"),
    ("battery_soc_entity", "Accu SoC uit HA (alleen terugval)", "entity", "Accu"),
    ("battery_power_entity", "Accu vermogen (+ = laden)", "entity", "Accu"),
    ("battery_capacity_kwh", "Bruikbare capaciteit (kWh)", "number", "Accu"),
    ("dvcc_max_charge_current", "DVCC max (A)", "number", "Accu"),
    ("dvcc_step_a", "DVCC stapgrootte (A)", "number", "Accu"),
    ("pv_power_entity", "Zonnepanelen vermogen", "entity", "Zon"),
    ("pv_switch_entity", "Zonnepanelen schakelaar", "entity", "Zon"),
    ("solar_remaining_entity", "Prognose rest van vandaag (kWh)", "entity", "Zon"),
    ("solar_tomorrow_entity", "Prognose morgen (kWh)", "entity", "Zon"),
    ("sunchance_entity", "Zonkans (%)", "entity", "Zon"),
    ("grid_power_entity", "Net vermogen (+ = afname)", "entity", "Net"),
    ("house_power_entity", "Huisverbruik (leeg = berekenen)", "entity", "Net"),
    ("grid_l1_entity", "Fase L1 (W of A)", "entity", "Net"),
    ("grid_l2_entity", "Fase L2 (W of A)", "entity", "Net"),
    ("grid_l3_entity", "Fase L3 (W of A)", "entity", "Net"),
    ("grid_phase_max_a", "Zekering per fase (A)", "number", "Net"),
    ("grid_phase_margin_a", "Marge per fase (A)", "number", "Net"),
    ("victron_phases", "Victron laadt op fase(s): auto, 1 of 1,2,3", "text", "Net"),
    ("zappi_phases", "Zappi fases (1 of 3)", "number", "Auto"),
    ("export_max_w", "Max terugleveren (W)", "number", "Net"),
    ("export_price", "Terugleverprijs", "select:total,energy", "Net"),
    ("zappi_power_entity", "Zappi vermogen", "entity", "Auto"),
    ("zappi_mode_entity", "Zappi modus", "entity", "Auto"),
    ("zappi_status_entity", "Zappi status", "entity", "Auto"),
    ("zappi_plug_entity", "Zappi stekker", "entity", "Auto"),
    ("zappi_max_w", "Zappi max vermogen (W)", "number", "Auto"),
    ("charge_speed_km_per_hour", "Laadsnelheid auto (km per uur)", "number", "Auto"),
    ("eco_battery_soc_min", "Ochtend-eco: accu minimaal (%)", "number", "Auto"),
    ("eco_solar_min", "Ochtend-eco: zon vandaag minimaal (kWh)", "number", "Auto"),
    ("eco_sunchance_min", "Ochtend-eco: zonkans minimaal (%)", "number", "Auto"),
    ("eco_morning_start_hour", "Ochtend-eco vanaf (uur)", "number", "Auto"),
    ("eco_morning_end_hour", "Ochtend-eco tot (uur)", "number", "Auto"),
    ("arbitrage_min_spread", "Min. winst laden van net (€/kWh)", "number", "Strategie"),
    ("export_min_spread", "Min. winst terugleveren (€/kWh)", "number", "Strategie"),
    ("roundtrip_efficiency", "Rendement accu heen en terug", "number", "Strategie"),
]


# Wat er gebruikt wordt als een entity leeg blijft
FALLBACK = {
    "grid_power_entity": ("grid_w", "som van de fases via Victron MQTT"),
    "battery_power_entity": ("battery_w", "Victron MQTT"),
    "pv_power_entity": ("pv_w", "Envoy automatisch, Victron PV-omvormer als reserve"),
    "grid_l1_entity": ("phases", "Victron MQTT"),
    "grid_l2_entity": ("phases", "Victron MQTT"),
    "grid_l3_entity": ("phases", "Victron MQTT"),
    "house_power_entity": (None, "berekend: zon + net - accu - Zappi"),
}


def entity_age(st: dict | None) -> float | None:
    """Seconden sinds de entity voor het laatst is bijgewerkt."""
    ts = (st or {}).get("last_updated") or (st or {}).get("last_changed")
    if not ts:
        return None
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(str(ts))).total_seconds()
    except ValueError:
        return None


# Sensoren die altijd blijven veranderen: staan ze lang stil, dan klopt er iets niet
AGE_CHECK = {"battery_soc_entity", "battery_power_entity", "grid_power_entity",
             "grid_l1_entity", "grid_l2_entity", "grid_l3_entity"}

def create_app(engine: Engine) -> web.Application:
    app = web.Application()

    def status_body() -> dict:
        cfg = engine.cfg
        return {
            "shadow": cfg.shadow or not engine.master,
            "master": engine.master,
            "control": asdict(cfg.control),
            "timezone": cfg.timezone,
            "cheap_price": cfg.cheap_price,
            "force_fast_price": cfg.force_fast_price,
            "battery_capacity_kwh": cfg.battery_capacity_kwh,
            "battery_target_soc": cfg.battery_target_soc,
            "battery_reserve_soc": cfg.battery_reserve_soc,
            "ha_connected": engine.ha.connected.is_set(),
            "errors": engine.errors,
            "story": engine.story,
            "helpers": helper_body(),
            "state": asdict(engine.state) if engine.state else None,
            "plan": engine.plan.to_dict() if engine.plan else None,
            "actions": [a.to_dict() for a in engine.executor.last_actions],
            "live": engine.live(),
            "today": engine.store.today_totals(engine.tz),
        }

    def helper_body() -> list[dict]:
        out = []
        for h in helpers.HELPERS:
            exists = h.entity_id in engine.ha.states
            value = engine.helper_values.get(h.key) if exists else (
                engine.master if h.key == "master" else getattr(engine.cfg, h.key, None)
            )
            attrs = engine.ha.attributes(h.entity_id)
            out.append({
                "key": h.key, "entity_id": h.entity_id, "label": h.label, "exists": exists, "value": value,
                "min": attrs.get("min"), "max": attrs.get("max"), "step": attrs.get("step"),
            })
        return out

    async def status(_req: web.Request) -> web.Response:
        return web.json_response(status_body())

    async def live(_req: web.Request) -> web.Response:
        return web.json_response(engine.live())

    async def decisions(req: web.Request) -> web.Response:
        limit = min(int(req.query.get("limit", 100)), 1000)
        return web.json_response(engine.store.decisions(limit))

    async def stats(_req: web.Request) -> web.Response:
        s = dict(engine.battery_stats())
        cfg, st = engine.cfg, engine.state
        charge_w = s.get("learned_charge_w") or cfg.dvcc_max_charge_current * cfg.battery_nominal_voltage * cfg.charge_efficiency
        s["charge_w_used"] = round(charge_w)
        s["charge_w_source"] = "gemeten" if s.get("learned_charge_w") else "berekend uit DVCC max"
        if st and st.soc is not None:
            need = max(0.0, cfg.battery_target_soc - st.soc) / 100 * cfg.battery_capacity_kwh
            s["to_target_kwh"] = round(need, 1)
            s["to_target_hours"] = round(need * 1000 / charge_w, 2) if charge_w else None
        return web.json_response(s)

    async def replan(_req: web.Request) -> web.Response:
        await engine.refresh_prices(force=True)
        await engine.cycle()
        return web.json_response(status_body())

    async def get_settings(_req: web.Request) -> web.Response:
        cfg = engine.base_cfg
        fields = []
        for k, label, kind, group in EDITABLE:
            val = getattr(cfg, k)
            f = {"key": k, "label": label, "kind": kind, "group": group, "value": val,
                 "state": engine.ha.state(val) if kind == "entity" and val else None,
                 "unit": engine.ha.attributes(val).get("unit_of_measurement") if kind == "entity" and val else None,
                 "suggestion": None, "fallback": None, "age_s": None, "options": None}
            if kind == "entity" and val and k in AGE_CHECK:
                f["age_s"] = entity_age(engine.ha.states.get(val))
            if k == "battery_soc_entity":
                v_soc = engine.victron.soc(engine.base_cfg.battery_soc_source)
                if v_soc is not None:
                    f["fallback"] = {"label": f"niet gebruikt: de Victron-monitor zegt {v_soc:.1f}% en gaat voor", "active": True}
            if kind == "battery":
                f["options"] = [{"source": "system", "name": "Actieve monitor van de GX", "soc": engine.victron.soc("system"),
                                 "power_w": None, "active": False}, *engine.victron.batteries()]
            if kind == "entity" and not val:
                sug = suggest(engine.ha.states, k)
                if sug:
                    f["suggestion"] = {"entity_id": sug, "state": engine.ha.state(sug),
                                       "unit": engine.ha.attributes(sug).get("unit_of_measurement")}
                if k in FALLBACK:
                    src_key, label_fb = FALLBACK[k]
                    active = src_key is None or src_key in engine.sources
                    f["fallback"] = {"label": label_fb, "active": active}
            if k == "victron_phases" and str(val).strip().lower() == "auto":
                f["fallback"] = {"label": f"nu: fase {engine.cfg.victron_phases} (uit de Multi's)", "active": True}
            fields.append(f)
        return web.json_response({"fields": fields, "control": asdict(cfg.control), "cars": [asdict(c) for c in cfg.cars]})

    async def post_settings(req: web.Request) -> web.Response:
        body = await req.json()
        allowed = {k for k, *_ in EDITABLE}
        changes = {k: v for k, v in (body or {}).items() if k in allowed}
        engine.base_cfg.update(changes)
        engine.cfg = engine.effective(helpers.apply_overrides(engine.base_cfg, engine.helper_values))
        await engine.cycle()
        return await get_settings(req)

    async def post_helper(req: web.Request) -> web.Response:
        body = await req.json()
        key, value = body.get("key"), body.get("value")
        if key not in helpers.BY_KEY:
            raise web.HTTPBadRequest(text="onbekende helper")
        h = helpers.BY_KEY[key]
        if h.entity_id in engine.ha.states:
            await helpers.set_helper(engine.ha, key, value)
            engine.ha.states.setdefault(h.entity_id, {})["state"] = (
                ("on" if value else "off") if h.entity_id.startswith("input_boolean") else str(value)
            )
        elif key != "master":
            engine.base_cfg.update({key: value})
        await engine.cycle()
        return web.json_response(status_body())

    async def entities(req: web.Request) -> web.Response:
        q = req.query.get("q", "").lower()
        domain = req.query.get("domain", "")
        out = []
        for eid, st in engine.ha.states.items():
            if domain and not eid.startswith(domain + "."):
                continue
            name = str((st.get("attributes") or {}).get("friendly_name", ""))
            if q and q not in eid.lower() and q not in name.lower():
                continue
            out.append({"entity_id": eid, "name": name, "state": st.get("state"),
                        "unit": (st.get("attributes") or {}).get("unit_of_measurement")})
            if len(out) >= 40:
                break
        return web.json_response(out)

    async def index(_req: web.Request) -> web.StreamResponse:
        f = STATIC / "index.html"
        if not f.exists():
            return web.Response(text="Frontend niet gebouwd (cd frontend && npm run build)", status=503)
        return web.FileResponse(f, headers={"Cache-Control": "no-cache"})

    async def icon(_req: web.Request) -> web.StreamResponse:
        return web.FileResponse(STATIC / "icon.png")

    app.router.add_get("/api/status", status)
    app.router.add_get("/api/live", live)
    app.router.add_get("/api/decisions", decisions)
    app.router.add_get("/api/stats", stats)
    app.router.add_post("/api/replan", replan)
    app.router.add_get("/api/settings", get_settings)
    app.router.add_post("/api/settings", post_settings)
    app.router.add_post("/api/helper", post_helper)
    app.router.add_get("/api/entities", entities)
    app.router.add_get("/", index)
    app.router.add_get("/icon.png", icon)
    if (STATIC / "assets").exists():
        app.router.add_static("/assets", STATIC / "assets")
    return app
