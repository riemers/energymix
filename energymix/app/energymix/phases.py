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
    """Vrije stroom per fase (A) gegeven wat er al op elke fase loopt.

    Zon die terugleverd telt mee als ruimte (de zekering ziet alleen de netto
    stroom). Valt de zon weg, dan regelt de Zappi zichzelf terug en verlaagt de
    regelaar direct de accu-laadstroom.
    """
    return [cfg.grid_phase_max_a - cfg.grid_phase_margin_a - a for a in others_a]


def battery_ac_limit_w(cfg: Config, others_a: list[float]) -> float:
    """Maximaal AC-vermogen voor laden van de accu, begrensd door de krapste Victron-fase."""
    vp = victron_phase_idx(cfg)
    free = headroom_a(cfg, others_a)
    per_phase = max(0.0, min(free[p] for p in vp))
    return per_phase * cfg.grid_voltage * len(vp)


def expected_others_a(cfg: Config, house_w: float, car_w: float, pv_w: float = 0.0) -> list[float]:
    """Verwachte stroom per fase voor de planner: huis en zon gelijk verdeeld, auto over zijn fases.

    Zon levert op dezelfde fases terug en geeft dus ruimte (zoals de zekering het ziet).
    """
    n = max(1, cfg.grid_phases)
    v = cfg.grid_voltage
    out = [(house_w - pv_w) / n / v] * n
    zp = zappi_phase_idx(cfg)
    for p in zp:
        out[p] += car_w / len(zp) / v
    return out


# Continu vermogen per Multi bij 25 °C (Victron-datasheet MultiPlus-II 48V, 230V)
MULTI_CONTINUOUS_W = {"3000": 2400, "5000": 4000}


def inverter_units(cfg: Config) -> int:
    """Aantal Multi's: één per fase waarop de Victron zit."""
    return max(1, len(victron_phase_idx(cfg)))


def inverter_limit_w(cfg: Config) -> float:
    """Wat de Multi's samen mogen leveren als ze terugleveren: huis + net samen.

    Een percentage van het continu vermogen, zodat ze niet op 100% draaien (slechter rendement,
    warm, en bij een warme ruimte zakt het continu vermogen al naar 3700 W per stuk).
    """
    per = MULTI_CONTINUOUS_W.get(str(cfg.victron_multi_va), MULTI_CONTINUOUS_W["5000"])
    pct = cfg.inverter_load_pct if 0 < cfg.inverter_load_pct <= 100 else 80
    return inverter_units(cfg) * per * pct / 100


def export_grid_cap_w(cfg: Config) -> float:
    """Losse grens op teruglevering aan het net (0 = geen, dan alleen de omvormergrens)."""
    lim = inverter_limit_w(cfg)
    return min(lim, cfg.export_max_w) if cfg.export_max_w and cfg.export_max_w > 0 else lim
