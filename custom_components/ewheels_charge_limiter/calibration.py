"""Learning the wall watt-hours required per percent of state of charge.

A single learned number absorbs both charger losses and any non-linearity in
how the device reports percent. On cheap BMS firmware the second error is
usually the larger one, which is why this is not modelled as "efficiency".
"""

from __future__ import annotations

from .const import (
    CALIBRATION_CLAMP_HIGH,
    CALIBRATION_CLAMP_LOW,
    CALIBRATION_EMA_WEIGHT,
    CALIBRATION_MIN_DELTA_PCT,
    DEFAULT_CHARGER_EFFICIENCY,
)


def seed_wh_per_percent(
    capacity_wh: float, efficiency: float = DEFAULT_CHARGER_EFFICIENCY
) -> float:
    """Initial estimate, before any session has been observed."""
    return capacity_wh / 100.0 / efficiency


def update_wh_per_percent(
    current: float,
    seed: float,
    delivered_wh: float,
    start_soc: float,
    end_soc: float,
) -> float:
    """Fold one completed session into the estimate.

    Returns ``current`` unchanged when the session carries too little signal.
    A negative delta means the device was ridden before it reported, and is
    rejected by the same minimum-delta guard.
    """
    delta = end_soc - start_soc
    if delta < CALIBRATION_MIN_DELTA_PCT:
        return current

    observed = delivered_wh / delta
    blended = (
        1.0 - CALIBRATION_EMA_WEIGHT
    ) * current + CALIBRATION_EMA_WEIGHT * observed
    return min(
        max(blended, seed * CALIBRATION_CLAMP_LOW), seed * CALIBRATION_CLAMP_HIGH
    )
