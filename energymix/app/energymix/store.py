"""SQLite-opslag: prijzen, plannen, beslissingen en metingen (voor statistiek)."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median

from .prices import PriceSlot

SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (start TEXT PRIMARY KEY, end TEXT, price REAL, level TEXT);
CREATE TABLE IF NOT EXISTS plans (ts TEXT PRIMARY KEY, plan TEXT, state TEXT);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT, component TEXT, value TEXT, previous TEXT, reason TEXT, executed INTEGER
);
CREATE INDEX IF NOT EXISTS decisions_ts ON decisions (ts);
CREATE TABLE IF NOT EXISTS car_learned (name TEXT PRIMARY KEY, data TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS samples (
    ts TEXT PRIMARY KEY, soc REAL, pv_w REAL, grid_w REAL, battery_w REAL, house_w REAL, zappi_w REAL, price REAL
);
"""
# Later toegevoegde kolommen (bestaande databases krijgen ze erbij)
SAMPLE_EXTRA = {"inverter_ac_w": "REAL", "ac_to_inv_kwh": "REAL", "inv_to_ac_kwh": "REAL"}
SAMPLE_COLS = ["soc", "pv_w", "grid_w", "battery_w", "house_w", "zappi_w", "price", *SAMPLE_EXTRA]

# Rendement heen en terug: alleen dagen die bijna helemaal gemeten zijn en waarop de accu
# echt gebruikt is, anders zegt de verhouding niets
EFF_MIN_COVERAGE_H = 20
EFF_MIN_KWH = 2.0


def roundtrip_from(e_in: float, e_out: float, d_stored: float) -> float | None:
    """Rendement AC naar AC uit wat de Multi's opnamen (e_in), leverden (e_out) en wat er
    netto in de accu bijkwam (d_stored, kWh uit de SoC).

    Heen en terug elk de wortel van het totaal (s): opgeslagen = e_in*s - e_out/s.
    Oplossen naar s geeft een tweedegraadsvergelijking; het rendement is s².
    """
    if e_in <= 0 or e_out <= 0:
        return None
    s = (d_stored + (d_stored ** 2 + 4 * e_in * e_out) ** 0.5) / (2 * e_in)
    eff = s * s
    return eff if 0.5 <= eff <= 1.0 else None  # daarbuiten klopt de meting niet


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        have = {r["name"] for r in self.db.execute("PRAGMA table_info(samples)")}
        for col, kind in SAMPLE_EXTRA.items():
            if col not in have:
                self.db.execute(f"ALTER TABLE samples ADD COLUMN {col} {kind}")
        self.db.commit()

    # ------------------------------------------------------------- prijzen
    def save_prices(self, slots) -> None:
        self.db.executemany(
            "INSERT OR REPLACE INTO prices VALUES (?,?,?,?)",
            [(s.start.isoformat(), s.end.isoformat(), s.price, s.level) for s in slots],
        )
        self.db.commit()

    def price_history(self, days: int = 8) -> list[PriceSlot]:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        rows = self.db.execute("SELECT * FROM prices ORDER BY start")
        out = []
        for r in rows:
            start = datetime.fromisoformat(r["start"])
            if start.astimezone(timezone.utc).isoformat() < since:
                continue
            out.append(PriceSlot(start, datetime.fromisoformat(r["end"]), r["price"], r["level"]))
        return out

    # ------------------------------------------------------------- plannen
    def save_plan(self, plan: dict, state: dict) -> None:
        self.db.execute("INSERT OR REPLACE INTO plans VALUES (?,?,?)", (plan["created_at"], json.dumps(plan), json.dumps(state)))
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        self.db.execute("DELETE FROM plans WHERE ts < ?", (cutoff,))
        self.db.commit()

    # ------------------------------------------------------------- beslissingen
    def log_decision(self, component: str, value, previous, reason: str, executed: bool) -> None:
        self.db.execute(
            "INSERT INTO decisions (ts, component, value, previous, reason, executed) VALUES (?,?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(), component, str(value), str(previous), reason, int(executed)),
        )
        self.db.commit()

    def decisions(self, limit: int = 100) -> list[dict]:
        rows = self.db.execute("SELECT * FROM decisions ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    # ------------------------------------------------------------- metingen
    def add_sample(self, ts: datetime, **values) -> None:
        cols = SAMPLE_COLS
        self.db.execute(
            f"INSERT OR REPLACE INTO samples (ts, {', '.join(cols)}) VALUES (?, {', '.join('?' * len(cols))})",
            (ts.astimezone(timezone.utc).isoformat(), *(values.get(c) for c in cols)),
        )
        cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        self.db.execute("DELETE FROM samples WHERE ts < ?", (cutoff,))
        self.db.commit()

    def samples(self, since: datetime) -> list[sqlite3.Row]:
        return list(self.db.execute("SELECT * FROM samples WHERE ts >= ? ORDER BY ts", (since.astimezone(timezone.utc).isoformat(),)))

    def house_profile(self, tz, days: int = 14) -> dict[int, float]:
        """Huisverbruik (W) per uur van de dag.

        Robuust: metingen terwijl de auto laadt tellen niet mee (een verkeerd
        afgetrokken Zappi zou de nacht enorm opblazen), negatieve waarden worden 0,
        en de hoogste 10% per uur (oven, waterkoker) valt weg.
        """
        by_hour: dict[int, list[float]] = {}
        for r in self.samples(datetime.now(timezone.utc) - timedelta(days=days)):
            if r["house_w"] is None:
                continue
            if (r["zappi_w"] or 0) > 500:
                continue
            h = datetime.fromisoformat(r["ts"]).astimezone(tz).hour
            by_hour.setdefault(h, []).append(max(0.0, r["house_w"]))
        out = {}
        for h, v in by_hour.items():
            if len(v) < 10:
                continue
            v.sort()
            keep = v[: max(1, int(len(v) * 0.9))]
            out[h] = sum(keep) / len(keep)
        return out

    def today_totals(self, tz) -> dict:
        """kWh van vandaag (lokale tijd) uit de metingen per minuut."""
        local_midnight = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
        rows = self.samples(local_midnight)
        tot = {k: 0.0 for k in ("house", "pv", "car", "battery_in", "battery_out", "grid_in", "grid_out")}
        prev = None
        for r in rows:
            t = datetime.fromisoformat(r["ts"])
            if prev is not None:
                dt_h = (t - prev).total_seconds() / 3600
                if 0 < dt_h <= 15 / 60:  # langere gaten (add-on uit) niet opvullen
                    w = lambda k: (r[k] or 0) * dt_h / 1000  # noqa: E731
                    tot["house"] += max(0.0, w("house_w"))
                    tot["pv"] += max(0.0, w("pv_w"))
                    tot["car"] += max(0.0, w("zappi_w"))
                    b = w("battery_w")
                    tot["battery_in" if b > 0 else "battery_out"] += abs(b)
                    g = w("grid_w")
                    tot["grid_in" if g > 0 else "grid_out"] += abs(g)
            prev = t
        out = {k: round(v, 1) for k, v in tot.items()}
        out["battery_net"] = round(tot["battery_in"] - tot["battery_out"], 1)
        out["since"] = local_midnight.isoformat()
        out["samples"] = len(rows)
        return out

    def battery_stats(self, tz, capacity_kwh: float, days: int = 14) -> dict:
        rows = self.samples(datetime.now(timezone.utc) - timedelta(days=days))
        per_day: dict[str, dict] = {}
        charge_w: list[float] = []
        prev = None
        for r in rows:
            t = datetime.fromisoformat(r["ts"])
            d = t.astimezone(tz).date().isoformat()
            day = per_day.setdefault(d, {"date": d, "soc_min": None, "soc_max": None, "charged_kwh": 0.0, "discharged_kwh": 0.0})
            if r["soc"] is not None:
                day["soc_min"] = r["soc"] if day["soc_min"] is None else min(day["soc_min"], r["soc"])
                day["soc_max"] = r["soc"] if day["soc_max"] is None else max(day["soc_max"], r["soc"])
            bw = r["battery_w"]
            if bw is not None and prev is not None:
                dt_h = (t - prev).total_seconds() / 3600
                if 0 < dt_h < 0.25:
                    if bw > 0:
                        day["charged_kwh"] += bw * dt_h / 1000
                    else:
                        day["discharged_kwh"] += -bw * dt_h / 1000
            if bw is not None and bw > 500 and (r["soc"] or 0) < 85:
                charge_w.append(bw)
            prev = t
        days_list = sorted(per_day.values(), key=lambda x: x["date"])
        for d in days_list:
            d["charged_kwh"] = round(d["charged_kwh"], 1)
            d["discharged_kwh"] = round(d["discharged_kwh"], 1)
        learned = None
        if len(charge_w) >= 30:
            charge_w.sort()
            learned = charge_w[int(len(charge_w) * 0.9)]
        mins = [d["soc_min"] for d in days_list if d["soc_min"] is not None]
        return {
            "days": days_list,
            "lowest_soc": min(mins) if mins else None,
            "median_daily_min_soc": median(mins) if mins else None,
            "learned_charge_w": round(learned) if learned else None,
            "samples": len(rows),
            "efficiency": self.roundtrip_efficiency(tz, capacity_kwh),
        }

    def roundtrip_efficiency(self, tz, capacity_kwh: float, days: int = 60) -> dict:
        """Gemeten rendement heen en terug (AC naar AC) per dag en over de hele periode.

        Per minuut wat de Multi's aan AC opnemen en leveren: uit hun energietellers als die er
        zijn, anders uit het gemeten vermogen. Wat er netto in de accu bijkwam (SoC begin en
        eind van de dag) wordt verrekend, zodat ook dagen zonder volle cyclus meetellen.
        """
        rows = self.samples(datetime.now(timezone.utc) - timedelta(days=days))
        per_day: dict[str, dict] = {}
        prev = None
        for r in rows:
            t = datetime.fromisoformat(r["ts"])
            d = t.astimezone(tz).date().isoformat()
            day = per_day.setdefault(d, {"in": 0.0, "out": 0.0, "hours": 0.0, "soc0": None, "soc1": None})
            if r["soc"] is not None:
                if day["soc0"] is None:
                    day["soc0"] = r["soc"]
                day["soc1"] = r["soc"]
            if prev is not None:
                dt_h = (t - prev[0]).total_seconds() / 3600
                if 0 < dt_h <= 15 / 60:
                    p = prev[1]
                    d_in = d_out = None
                    if None not in (r["ac_to_inv_kwh"], r["inv_to_ac_kwh"], p["ac_to_inv_kwh"], p["inv_to_ac_kwh"]):
                        d_in = r["ac_to_inv_kwh"] - p["ac_to_inv_kwh"]
                        d_out = r["inv_to_ac_kwh"] - p["inv_to_ac_kwh"]
                        limit = 30 * dt_h  # meer dan 30 kW kan niet: teller gereset of verkeerd
                        if not (0 <= d_in <= limit and 0 <= d_out <= limit):
                            prev = (t, r)  # deze minuut overslaan
                            continue
                    elif r["inverter_ac_w"] is not None:
                        kwh = r["inverter_ac_w"] * dt_h / 1000
                        d_in, d_out = max(0.0, kwh), max(0.0, -kwh)
                    if d_in is not None:
                        day["in"] += d_in
                        day["out"] += d_out
                        day["hours"] += dt_h
            prev = (t, r)

        out_days = []
        tot_in = tot_out = tot_stored = 0.0
        for d in sorted(per_day):
            x = per_day[d]
            if x["hours"] < EFF_MIN_COVERAGE_H or x["in"] < EFF_MIN_KWH or x["out"] < EFF_MIN_KWH:
                continue
            if x["soc0"] is None or x["soc1"] is None:
                continue
            stored = capacity_kwh * (x["soc1"] - x["soc0"]) / 100
            eff = roundtrip_from(x["in"], x["out"], stored)
            if eff is None:
                continue
            out_days.append({"date": d, "efficiency": round(eff, 3),
                             "in_kwh": round(x["in"], 1), "out_kwh": round(x["out"], 1)})
            tot_in, tot_out, tot_stored = tot_in + x["in"], tot_out + x["out"], tot_stored + stored
        overall = roundtrip_from(tot_in, tot_out, tot_stored) if out_days else None
        return {"days": out_days, "overall": round(overall, 3) if overall else None}

    # ------------------------------------------------------------- auto's
    def get_car_learned(self, name: str) -> dict | None:
        row = self.db.execute("SELECT data FROM car_learned WHERE name = ?", (name,)).fetchone()
        return json.loads(row["data"]) if row else None

    def save_car_learned(self, name: str, data: dict) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO car_learned VALUES (?,?,?)",
            (name, json.dumps(data), datetime.now(timezone.utc).isoformat()),
        )
        self.db.commit()
