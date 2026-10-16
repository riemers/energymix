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


# Een cyclus: de accu loopt minstens zoveel procentpunt weg en komt dan terug op dezelfde stand
CYCLE_MIN_SWING = 15
CYCLE_SOC_TOL = 0.5
CYCLE_MAX_DAYS = 7


def _interval_kwh(r, p, dt_h: float) -> tuple[tuple[float, float] | None, tuple[float, float] | None]:
    """AC erin en eruit (kWh) tussen twee metingen: (uit de tellers, uit het vermogen)."""
    c = None
    if None not in (r["ac_to_inv_kwh"], r["inv_to_ac_kwh"], p["ac_to_inv_kwh"], p["inv_to_ac_kwh"]):
        d_in, d_out = r["ac_to_inv_kwh"] - p["ac_to_inv_kwh"], r["inv_to_ac_kwh"] - p["inv_to_ac_kwh"]
        if 0 <= d_in <= 30 * dt_h and 0 <= d_out <= 30 * dt_h:
            c = (d_in, d_out)
    w = None
    if r["inverter_ac_w"] is not None:
        kwh = r["inverter_ac_w"] * dt_h / 1000
        w = (max(0.0, kwh), max(0.0, -kwh))
    return c, w


def roundtrip_cycles(rows, capacity_kwh: float, keep: int = 12) -> dict:
    """Rendement per volledige cyclus: van een SoC weg (minstens CYCLE_MIN_SWING procentpunt) en
    terug naar diezelfde SoC, ook over meerdere dagen. Dan is er netto niets opgeslagen en hangt
    de uitkomst nauwelijks af van een SoC-fout. Een gat in de metingen begint opnieuw.
    """
    cycles = []
    start = None  # (tijd, soc, [c_in, c_out, c_ok], [p_in, p_out, p_ok], soc_min, soc_max)
    prev = None
    for r in rows:
        t = datetime.fromisoformat(r["ts"])
        soc = r["soc"]
        if soc is None:
            prev = None
            start = None
            continue
        if prev is not None and start is not None:
            dt_h = (t - prev[0]).total_seconds() / 3600
            if not 0 < dt_h <= 15 / 60 or t - start["t"] > timedelta(days=CYCLE_MAX_DAYS):
                start = None
            else:
                c, w = _interval_kwh(r, prev[1], dt_h)
                for key, v in (("c", c), ("p", w)):
                    if v is None:
                        start[key][2] = False
                    else:
                        start[key][0] += v[0]
                        start[key][1] += v[1]
                start["lo"], start["hi"] = min(start["lo"], soc), max(start["hi"], soc)
                swing = max(start["soc"] - start["lo"], start["hi"] - start["soc"])
                if swing >= CYCLE_MIN_SWING and abs(soc - start["soc"]) <= CYCLE_SOC_TOL:
                    stored = capacity_kwh * (soc - start["soc"]) / 100
                    best = None
                    for key in ("c", "p"):
                        e_in, e_out, ok = start[key]
                        if ok and e_in >= EFF_MIN_KWH and e_out >= EFF_MIN_KWH:
                            eff = roundtrip_from(e_in, e_out, stored)
                            if eff is not None:
                                best = (eff, e_in, e_out, "tellers" if key == "c" else "vermogen")
                                break
                    if best:
                        cycles.append({"start": start["t"].isoformat(), "end": t.isoformat(),
                                       "soc": round(start["soc"]), "soc_min": round(start["lo"]),
                                       "soc_max": round(start["hi"]), "efficiency": round(best[0], 3),
                                       "in_kwh": round(best[1], 1), "out_kwh": round(best[2], 1), "source": best[3]})
                    start = None
        if start is None:
            start = {"t": t, "soc": soc, "lo": soc, "hi": soc, "c": [0.0, 0.0, True], "p": [0.0, 0.0, True]}
        prev = (t, r)
    tot_in = sum(c["in_kwh"] for c in cycles)
    tot_out = sum(c["out_kwh"] for c in cycles)
    overall = tot_out / tot_in if tot_in else None
    return {"list": cycles[-keep:], "count": len(cycles), "overall": round(overall, 3) if overall else None}


# Rendement per vermogen: per minuut AC (Multi's) tegen DC (accumonitor)
CURVE_MIN_W = 500
CURVE_MIN_SAMPLES = 20
STANDBY_MAX_W = 100


def power_curve(rows) -> dict:
    """Laad- en ontlaadrendement per vermogensklasse (per kW AC) en het eigenverbruik.

    Laden: DC naar de accu gedeeld door AC erin. Ontladen: AC eruit gedeeld door DC uit de accu.
    Per klasse energiegewogen (som DC / som AC). Losse minuten met een onmogelijke verhouding
    (vermogen net veranderd tussen twee metingen) tellen niet mee.
    Eigenverbruik: wat de Multi's opnemen terwijl de accu (bijna) stilstaat.
    """
    bins: dict[int, dict] = {}
    idle: list[float] = []
    for r in rows:
        ac, dc = r["inverter_ac_w"], r["battery_w"]
        if ac is None or dc is None:
            continue
        if abs(dc) < STANDBY_MAX_W and abs(ac) < 2 * STANDBY_MAX_W:
            idle.append(ac - dc)
            continue
        if abs(ac) < CURVE_MIN_W:
            continue
        kw = int(abs(ac) / 1000 + 0.5)
        b = bins.setdefault(kw, {"c_ac": 0.0, "c_dc": 0.0, "n_c": 0, "d_ac": 0.0, "d_dc": 0.0, "n_d": 0})
        if ac > 0 and dc > 0 and 0.6 <= dc / ac <= 1.0:
            b["c_ac"] += ac
            b["c_dc"] += dc
            b["n_c"] += 1
        elif ac < 0 and dc < 0 and 0.6 <= ac / dc <= 1.0:
            b["d_ac"] -= ac
            b["d_dc"] -= dc
            b["n_d"] += 1
    out = []
    for kw in sorted(bins):
        b = bins[kw]
        ch = b["c_dc"] / b["c_ac"] if b["n_c"] >= CURVE_MIN_SAMPLES else None
        dis = b["d_ac"] / b["d_dc"] if b["n_d"] >= CURVE_MIN_SAMPLES else None
        if ch is None and dis is None:
            continue
        out.append({"kw": kw, "charge": round(ch, 3) if ch else None, "discharge": round(dis, 3) if dis else None,
                    "roundtrip": round(ch * dis, 3) if ch and dis else None, "n_charge": b["n_c"], "n_discharge": b["n_d"]})
    standby = median(idle) if len(idle) >= CURVE_MIN_SAMPLES else None
    return {"bins": out, "standby_w": round(standby) if standby is not None else None, "standby_samples": len(idle)}


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
        """Huisverbruik (W) per uur van de dag (zie house_profile_info)."""
        return self.house_profile_info(tz, days)["profile"]

    def house_profile_info(self, tz, days: int = 14) -> dict:
        """Huisverbruik (W) per uur van de dag, plus hoeveel metingen meetelden.

        Robuust: metingen terwijl de auto laadt tellen niet mee (een verkeerd
        afgetrokken Zappi zou de nacht enorm opblazen), negatieve waarden worden 0,
        en de hoogste 10% per uur (oven, waterkoker) valt weg. Uren zonder genoeg
        metingen krijgen het gemiddelde van de gemeten uren, als er minstens 6 zijn.
        """
        by_hour: dict[int, list[float]] = {}
        total = no_value = car = 0
        for r in self.samples(datetime.now(timezone.utc) - timedelta(days=days)):
            total += 1
            if r["house_w"] is None:
                no_value += 1
                continue
            if (r["zappi_w"] or 0) > 500:
                car += 1
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
        measured = len(out)
        if 6 <= measured < 24:
            avg = sum(out.values()) / measured
            out = {h: out.get(h, avg) for h in range(24)}
        return {
            "profile": out, "hours_measured": measured, "samples": total,
            "skipped_no_value": no_value, "skipped_car": car,
            "avg_w": round(sum(out.values()) / len(out)) if out else None,
        }

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
            "capacity": self.learned_capacity(),
        }

    def roundtrip_efficiency(self, tz, capacity_kwh: float, days: int = 60) -> dict:
        """Gemeten rendement heen en terug (AC naar AC) per dag en over de hele periode.

        Per minuut wat de Multi's aan AC opnemen en leveren, op twee manieren: uit hun
        energietellers en uit het gemeten AC-vermogen. De tellers zijn nauwkeuriger, maar
        missen soms een pad of gaan na een herstart terug naar nul; geven ze geen geloofwaardig
        rendement, dan telt het vermogen. Wat er netto in de accu bijkwam (SoC begin en eind
        van de dag) wordt verrekend, zodat ook dagen zonder volle cyclus meetellen.
        `checked` zegt per recente dag wat er gemeten is en waarom hij (niet) meetelt.
        """
        rows = self.samples(datetime.now(timezone.utc) - timedelta(days=days))
        per_day: dict[str, dict] = {}
        prev = None
        for r in rows:
            t = datetime.fromisoformat(r["ts"])
            d = t.astimezone(tz).date().isoformat()
            day = per_day.setdefault(d, {"c": [0.0, 0.0, 0.0], "p": [0.0, 0.0, 0.0], "soc0": None, "soc1": None})
            if r["soc"] is not None:
                if day["soc0"] is None:
                    day["soc0"] = r["soc"]
                day["soc1"] = r["soc"]
            if prev is not None:
                dt_h = (t - prev[0]).total_seconds() / 3600
                if 0 < dt_h <= 15 / 60:
                    p = prev[1]
                    if None not in (r["ac_to_inv_kwh"], r["inv_to_ac_kwh"], p["ac_to_inv_kwh"], p["inv_to_ac_kwh"]):
                        d_in = r["ac_to_inv_kwh"] - p["ac_to_inv_kwh"]
                        d_out = r["inv_to_ac_kwh"] - p["inv_to_ac_kwh"]
                        limit = 30 * dt_h  # meer dan 30 kW kan niet: teller gereset of verkeerd
                        if 0 <= d_in <= limit and 0 <= d_out <= limit:
                            c = day["c"]
                            c[0], c[1], c[2] = c[0] + d_in, c[1] + d_out, c[2] + dt_h
                    if r["inverter_ac_w"] is not None:
                        kwh = r["inverter_ac_w"] * dt_h / 1000
                        pw = day["p"]
                        pw[0], pw[1], pw[2] = pw[0] + max(0.0, kwh), pw[1] + max(0.0, -kwh), pw[2] + dt_h
            prev = (t, r)

        def judge(e_in: float, e_out: float, hours: float, stored: float | None) -> tuple[float | None, str]:
            if hours < EFF_MIN_COVERAGE_H:
                return None, f"maar {hours:.1f} uur gemeten"
            if e_in < EFF_MIN_KWH or e_out < EFF_MIN_KWH:
                return None, "te weinig geladen of ontladen"
            if stored is None:
                return None, "geen accustand"
            eff = roundtrip_from(e_in, e_out, stored)
            if eff is None:
                s = (stored + (stored ** 2 + 4 * e_in * e_out) ** 0.5) / (2 * e_in)
                return None, f"uitkomst {s * s:.0%} niet geloofwaardig"
            return eff, ""

        out_days, checked = [], []
        tot_in = tot_out = tot_stored = 0.0
        for d in sorted(per_day):
            x = per_day[d]
            stored = (capacity_kwh * (x["soc1"] - x["soc0"]) / 100) if x["soc0"] is not None and x["soc1"] is not None else None
            eff, why, used = None, "geen AC-metingen van de Multi's", None
            for src in ("c", "p"):
                e_in, e_out, hours = x[src]
                if hours <= 0:
                    continue
                eff, why = judge(e_in, e_out, hours, stored)
                used = (src, e_in, e_out, hours)
                if eff is not None:
                    break
            if used:
                src, e_in, e_out, hours = used
                checked.append({"date": d, "source": "tellers" if src == "c" else "vermogen", "hours": round(hours, 1),
                                "in_kwh": round(e_in, 1), "out_kwh": round(e_out, 1),
                                "stored_kwh": round(stored, 1) if stored is not None else None,
                                "efficiency": round(eff, 3) if eff is not None else None, "reason": why})
            else:
                checked.append({"date": d, "source": None, "hours": 0, "in_kwh": 0, "out_kwh": 0,
                                "stored_kwh": None, "efficiency": None, "reason": why})
            if eff is None:
                continue
            out_days.append({"date": d, "efficiency": round(eff, 3), "in_kwh": round(e_in, 1), "out_kwh": round(e_out, 1)})
            tot_in, tot_out, tot_stored = tot_in + e_in, tot_out + e_out, tot_stored + stored
        overall = roundtrip_from(tot_in, tot_out, tot_stored) if out_days else None
        return {"days": out_days, "overall": round(overall, 3) if overall else None, "checked": checked[-7:],
                "cycles": roundtrip_cycles(rows, capacity_kwh), "power_curve": power_curve(rows)}

    def learned_capacity(self, days: int = 30, min_swing: float = 20.0) -> dict:
        """Echte accucapaciteit uit metingen: hoeveel kWh er in of uit ging per procent SoC.

        Neemt stukken waarin de SoC minstens `min_swing` procentpunt in één richting liep
        (laden of ontladen), telt het gemeten accuvermogen op en deelt door die SoC-sprong.
        Laden geeft door verlies in de accu een iets te hoge waarde, ontladen een iets te
        lage; het gemiddelde van beide ligt dicht bij de werkelijkheid. Blijft de SoC op 100%
        hangen terwijl er nog wat stroom loopt, dan telt dat niet mee.
        """
        est: dict[int, list[float]] = {1: [], -1: []}
        run = None  # [soc0, soc_ext, e, e_ext, dir]
        prev_t = None

        def close():
            if run is None:
                return
            soc0, ext, _, e_ext, d = run
            ds = ext - soc0
            if d and abs(ds) >= min_swing and e_ext * d > 0:
                est[d].append(abs(e_ext) / (abs(ds) / 100))

        for r in self.samples(datetime.now(timezone.utc) - timedelta(days=days)):
            soc, bw = r["soc"], r["battery_w"]
            if soc is None or bw is None:
                continue
            t = datetime.fromisoformat(r["ts"])
            dt_h = (t - prev_t).total_seconds() / 3600 if prev_t else None
            prev_t = t
            if run is None or dt_h is None or not 0 < dt_h <= 15 / 60:
                close()
                run = [soc, soc, 0.0, 0.0, 0]
                continue
            run[2] += bw * dt_h / 1000
            if not run[4] and abs(soc - run[0]) >= 1:
                run[4] = 1 if soc > run[0] else -1
            d = run[4]
            if not d:
                run[1], run[3] = soc, run[2]
            elif (soc - run[1]) * d > 0:
                run[1], run[3] = soc, run[2]
            elif (run[1] - soc) * d >= 2:  # SoC loopt terug: dit stuk is klaar
                close()
                run = [soc, soc, 0.0, 0.0, 0]
        close()
        ch, dis = est[1], est[-1]
        if len(ch) + len(dis) < 2:
            return {"kwh": None, "runs": len(ch) + len(dis)}
        parts = [median(x) for x in (ch, dis) if x]
        return {
            "kwh": round(sum(parts) / len(parts), 1),
            "runs": len(ch) + len(dis),
            "charge_kwh": round(median(ch), 1) if ch else None,
            "discharge_kwh": round(median(dis), 1) if dis else None,
        }

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
