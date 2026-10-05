"""SQLite-opslag: prijzen, plannen en beslissingen (voor terugkijken)."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (start TEXT PRIMARY KEY, end TEXT, price REAL, level TEXT);
CREATE TABLE IF NOT EXISTS plans (ts TEXT PRIMARY KEY, plan TEXT, state TEXT);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT, component TEXT, value TEXT, previous TEXT, reason TEXT, executed INTEGER
);
CREATE INDEX IF NOT EXISTS decisions_ts ON decisions (ts);
"""


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def save_prices(self, slots) -> None:
        self.db.executemany(
            "INSERT OR REPLACE INTO prices VALUES (?,?,?,?)",
            [(s.start.isoformat(), s.end.isoformat(), s.price, s.level) for s in slots],
        )
        self.db.commit()

    def save_plan(self, plan: dict, state: dict) -> None:
        self.db.execute("INSERT OR REPLACE INTO plans VALUES (?,?,?)", (plan["created_at"], json.dumps(plan), json.dumps(state)))
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        self.db.execute("DELETE FROM plans WHERE ts < ?", (cutoff,))
        self.db.commit()

    def log_decision(self, component: str, value, previous, reason: str, executed: bool) -> None:
        self.db.execute(
            "INSERT INTO decisions (ts, component, value, previous, reason, executed) VALUES (?,?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(), component, str(value), str(previous), reason, int(executed)),
        )
        self.db.commit()

    def decisions(self, limit: int = 100) -> list[dict]:
        rows = self.db.execute("SELECT * FROM decisions ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]
