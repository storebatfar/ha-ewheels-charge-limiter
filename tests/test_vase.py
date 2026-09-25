"""The per-band energy model."""

from __future__ import annotations

import pytest

from custom_components.ewheels_charge_limiter.const import (
    BAND_CLAMP_HIGH,
    BAND_CLAMP_LOW,
)
from custom_components.ewheels_charge_limiter.vase import (
    band_at,
    energy_between,
    fit_bands,
    overlap,
    seed_wh_per_percent,
    soc_after,
)

SEED = seed_wh_per_percent(720)
FLOOR = BAND_CLAMP_LOW * SEED
CEILING = BAND_CLAMP_HIGH * SEED
STEPPED = [float(i + 1) for i in range(10)]  # 1, 2, ... 10 Wh/pt

# The three real charges of 2026-09-21..25, settled end readings.
REAL = [(88.0, 97.0, 82.8), (65.0, 96.0, 207.0), (45.0, 97.0, 270.1)]


def test_seed_is_capacity_per_percent_over_efficiency():
    assert SEED == pytest.approx(720 / 100 / 0.87)


def test_overlap_counts_only_the_part_inside_the_band():
    assert overlap(15, 25, 1) == pytest.approx(5)
    assert overlap(15, 25, 2) == pytest.approx(5)
    assert overlap(15, 25, 3) == 0


def test_energy_between_crosses_band_edges():
    # 15-20 in band 1 (2 Wh/pt) + 20-25 in band 2 (3 Wh/pt)
    assert energy_between(STEPPED, 15, 25) == pytest.approx(5 * 2 + 5 * 3)


def test_energy_between_is_zero_unless_the_end_is_above_the_start():
    assert energy_between(STEPPED, 50, 50) == 0
    assert energy_between(STEPPED, 60, 50) == 0


def test_the_top_band_runs_to_100():
    assert energy_between(STEPPED, 95, 100) == pytest.approx(5 * 10)


def test_band_at_picks_the_band_and_clamps_the_ends():
    assert band_at(STEPPED, 45) == 5
    assert band_at(STEPPED, 100) == 10
    assert band_at(STEPPED, -3) == 1


@pytest.mark.parametrize(("start", "end"), [(45, 90), (12.5, 37.5), (0, 100)])
def test_soc_after_inverts_energy_between(start, end):
    assert soc_after(STEPPED, start, energy_between(STEPPED, start, end)) == (
        pytest.approx(end)
    )


def test_soc_after_caps_at_100_and_never_loops():
    assert soc_after(STEPPED, 95, 10_000) == 100
    assert soc_after(STEPPED, 100, 50) == 100
    assert soc_after(STEPPED, 140, 50) == 100
    assert soc_after(STEPPED, 50, 0) == 50


def test_no_charges_returns_typed_priors_exactly_even_a_step():
    priors = [5.0] * 5 + [7.0] * 5
    assert fit_bands([], priors, FLOOR, CEILING) == pytest.approx(priors)


def test_the_fit_replays_the_three_real_charges_within_three_percent():
    bands = fit_bands(REAL, [5.758] * 10, FLOOR, CEILING)
    for start, end, energy in REAL:
        assert energy_between(bands, start, end) == pytest.approx(energy, rel=0.03)


def test_the_fit_reproduces_the_reference_shape():
    bands = fit_bands(REAL, [5.758] * 10, FLOOR, CEILING)
    assert bands == pytest.approx(
        [4.957, 4.796, 4.444, 3.828, 2.827, 2.391, 3.587, 5.56, 7.616, 9.369],
        abs=0.01,
    )


def test_the_fit_clamps_to_floor_and_ceiling():
    assert fit_bands([], [50.0] * 10, FLOOR, CEILING) == pytest.approx(
        [CEILING] * 10
    )
    assert fit_bands([], [0.1] * 10, FLOOR, CEILING) == pytest.approx([FLOOR] * 10)
