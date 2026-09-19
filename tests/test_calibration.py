"""Calibration maths."""

from __future__ import annotations

import pytest

from custom_components.ewheels_charge_limiter.calibration import (
    seed_wh_per_percent,
    update_wh_per_percent,
)


def test_seed_accounts_for_charger_efficiency():
    # 720 Wh pack: 7.2 Wh per percent at the battery, ~8.28 at the wall.
    assert seed_wh_per_percent(720.0) == pytest.approx(8.276, abs=0.01)


def test_update_blends_observation_with_current_estimate():
    # Observed: 500 Wh delivered for 50 points => 10.0 Wh/%.
    # EMA at weight 0.3 from a current of 8.0 => 8.0*0.7 + 10.0*0.3 = 8.6
    result = update_wh_per_percent(
        current=8.0, seed=8.0, delivered_wh=500.0, start_soc=30.0, end_soc=80.0
    )
    assert result == pytest.approx(8.6)


def test_update_ignores_sessions_below_the_minimum_delta():
    # Only 5 points of change: too little signal, estimate must not move.
    result = update_wh_per_percent(
        current=8.0, seed=8.0, delivered_wh=50.0, start_soc=75.0, end_soc=80.0
    )
    assert result == 8.0


def test_update_discards_a_negative_delta():
    """Device was ridden before reporting; end below start is meaningless."""
    result = update_wh_per_percent(
        current=8.0, seed=8.0, delivered_wh=500.0, start_soc=80.0, end_soc=30.0
    )
    assert result == 8.0


def test_update_clamps_an_absurd_observation_high():
    # 5000 Wh for 50 points => 100 Wh/%, far beyond plausible. Clamp at 2x seed.
    result = update_wh_per_percent(
        current=8.0, seed=8.0, delivered_wh=5000.0, start_soc=30.0, end_soc=80.0
    )
    assert result == pytest.approx(16.0)


def test_update_damps_an_absurdly_low_observation():
    """A single bad reading cannot drag the estimate far; the EMA sees to that.

    The low clamp is unreachable in one step: from 8.0 the most a single
    update can remove is 30%, landing at 5.6, well above the 4.0 floor.
    """
    result = update_wh_per_percent(
        current=8.0, seed=8.0, delivered_wh=1.0, start_soc=30.0, end_soc=80.0
    )
    assert result == pytest.approx(5.606)


def test_repeated_absurd_observations_are_clamped_at_the_floor():
    value = 8.0
    for _ in range(50):
        value = update_wh_per_percent(
            current=value, seed=8.0, delivered_wh=1.0, start_soc=30.0, end_soc=80.0
        )
    assert value == pytest.approx(4.0)


def test_repeated_updates_converge_toward_the_observation():
    value = 8.0
    for _ in range(20):
        value = update_wh_per_percent(
            current=value, seed=8.0, delivered_wh=450.0, start_soc=30.0, end_soc=80.0
        )
    # 450 Wh over 50 points => 9.0 Wh/%
    assert value == pytest.approx(9.0, abs=0.05)
