"""The vase: how much wall energy each reported percentage point costs.

A pack's reported percent is not linear in energy: on the scooter this was
written for, a point near the top costs about three times what one in the
middle does. So instead of one Wh-per-percent, ten 10-point bands each carry
their own, fitted jointly from remembered charges.

Pure functions only, no Home Assistant, so the maths can be tested on its own.
"""

from __future__ import annotations

from collections.abc import Sequence

from .const import DEFAULT_CHARGER_EFFICIENCY, FIT_PRIOR_WEIGHT, FIT_SMOOTHING_WEIGHT

BAND_COUNT = 10
BAND_WIDTH = 10.0


def seed_wh_per_percent(
    capacity_wh: float, efficiency: float = DEFAULT_CHARGER_EFFICIENCY
) -> float:
    """Initial estimate, before any charge has been observed."""
    return capacity_wh / 100.0 / efficiency


def overlap(start: float, end: float, band: int) -> float:
    """Points of [start, end] that fall inside one band."""
    low = band * BAND_WIDTH
    high = low + BAND_WIDTH
    return max(0.0, min(end, high) - max(start, low))


def energy_between(bands: Sequence[float], start: float, end: float) -> float:
    """Wall Wh to go from start to end. Zero unless end is above start."""
    return sum(bands[i] * overlap(start, end, i) for i in range(BAND_COUNT))


def band_at(bands: Sequence[float], soc: float) -> float:
    """The value of the band containing soc, clamped to the first and last."""
    index = int(max(0.0, soc) // BAND_WIDTH)
    return bands[min(index, BAND_COUNT - 1)]


def soc_after(bands: Sequence[float], start: float, wh: float) -> float:
    """The point reached from start after wh wall watt-hours, capped at 100."""
    soc = min(max(start, 0.0), 100.0)
    remaining = max(wh, 0.0)
    for _ in range(BAND_COUNT):
        if soc >= 100.0 or remaining <= 0.0:
            break
        index = min(int(soc // BAND_WIDTH), BAND_COUNT - 1)
        band_top = (index + 1) * BAND_WIDTH
        cost = bands[index] * (band_top - soc)
        if cost >= remaining:
            return soc + remaining / bands[index]
        remaining -= cost
        soc = band_top
    return min(soc, 100.0)


def _solve(matrix: list[list[float]], rhs: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting. The caller guarantees SPD."""
    size = len(rhs)
    rows = [list(matrix[r]) + [rhs[r]] for r in range(size)]
    for col in range(size):
        pivot = max(range(col, size), key=lambda r: abs(rows[r][col]))
        rows[col], rows[pivot] = rows[pivot], rows[col]
        for r in range(size):
            if r != col and rows[r][col] != 0.0:
                factor = rows[r][col] / rows[col][col]
                rows[r] = [a - factor * b for a, b in zip(rows[r], rows[col])]
    return [rows[r][size] / rows[r][r] for r in range(size)]


def fit_bands(
    charges: Sequence[tuple[float, float, float]],
    priors: Sequence[float],
    floor: float,
    ceiling: float,
    prior_weight: float = FIT_PRIOR_WEIGHT,
    smoothing_weight: float = FIT_SMOOTHING_WEIGHT,
) -> list[float]:
    """Fit the ten bands to remembered (start, end, energy) charges.

    Solves for a correction d to each prior, b = p + d. The penalties keep
    corrections small where no charge covers a band, and keep neighbouring
    corrections alike. Smoothing the corrections rather than the values is
    deliberate: a typed prior then survives exactly where there is no data,
    instead of being blended into its neighbours.
    """
    size = BAND_COUNT
    matrix = [[0.0] * size for _ in range(size)]
    rhs = [0.0] * size
    for start, end, energy in charges:
        row = [overlap(start, end, i) for i in range(size)]
        residual = energy - sum(row[i] * priors[i] for i in range(size))
        for i in range(size):
            rhs[i] += row[i] * residual
            for j in range(size):
                matrix[i][j] += row[i] * row[j]
    for i in range(size):
        matrix[i][i] += prior_weight
    for i in range(size - 1):
        matrix[i][i] += smoothing_weight
        matrix[i + 1][i + 1] += smoothing_weight
        matrix[i][i + 1] -= smoothing_weight
        matrix[i + 1][i] -= smoothing_weight
    corrections = _solve(matrix, rhs)
    return [
        min(max(prior + correction, floor), ceiling)
        for prior, correction in zip(priors, corrections)
    ]
