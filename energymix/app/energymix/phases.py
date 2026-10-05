"""Rekenen per fase.

Met 3x25A kan één fase vol zitten (bv. een oven op L2) terwijl het totaal nog
ruim lijkt. Dan mag de accu op die fase niet meer laden, en kan ook de auto
(3-fase) niet vol vermogen trekken. Alles hier werkt dus per fase en de krapste
fase waar de Victron op zit bepaalt hoeveel de accu mag.
"""

from __future__ import annotations

from .config import Config


def victron_phase_idx(cfg: Config) -> list[int]:
    out = []
    for part in str(cfg.victron_phases).replace(" ", "").split(","):
        if part.isdigit() and 1 <= int(part) <= cfg.grid_phases:
            out.append(int(part) - 1)
    return out or [0]


def zappi_phase_idx(cfg: Config) -> list[int]:
    return list(range(min(cfg.zappi_phases, cfg.grid_phases))) if cfg.zappi_phases >= 1 else []


def headroom_a(cfg: Config, others_a: list[float]) -> list[float]:
    """Vrije stroom per fase (A) gegeven wat er al op elke fase loopt."""
    return [cfg.grid_phase_max_a - cfg.grid_phase_margin_a - a for a in others_a]


def battery_ac_limit_w(cfg: Config, others_a: list[float]) -> float:
    """Maximaal AC-vermogen voor laden van de accu, begrensd door de krapste Victron-fase."""
    vp = victron_phase_idx(cfg)
    free = headroom_a(cfg, others_a)
    per_phase = max(0.0, min(free[p] for p in vp))
    return per_phase * cfg.grid_voltage * len(vp)


def expected_others_a(cfg: Config, house_w: float, car_w: float) -> list[float]:
    """Verwachte stroom per fase voor de planner: huis gelijk verdeeld, auto over zijn fases."""
    n = max(1, cfg.grid_phases)
    v = cfg.grid_voltage
    out = [house_w / n / v] * n
    zp = zappi_phase_idx(cfg)
    for p in zp:
        out[p] += car_w / len(zp) / v
    return out
