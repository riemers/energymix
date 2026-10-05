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
CREATE TABLE IF NOT EXISTS samples (
    ts TEXT PRIMARY KEY, soc REAL, pv_w REAL, grid_w REAL, battery_w REAL, house_w REAL, zappi_w REAL, price REAL
);
"""


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

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
        cols = ["soc", "pv_w", "grid_w", "battery_w", "house_w", "zappi_w", "price"]
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
        """Gemiddeld huisverbruik (W) per uur van de dag."""
        by_hour: dict[int, list[float]] = {}
        for r in self.samples(datetime.now(timezone.utc) - timedelta(days=days)):
            if r["house_w"] is None:
                continue
            h = datetime.fromisoformat(r["ts"]).astimezone(tz).hour
            by_hour.setdefault(h, []).append(r["house_w"])
        return {h: sum(v) / len(v) for h, v in by_hour.items() if len(v) >= 10}

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
        }
