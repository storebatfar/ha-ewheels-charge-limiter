# Per-band energy model ("the vase") Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single learned Wh-per-percent with ten 10-point bands refitted from the last ten charges, and fix the four calibration bugs on the same code path, shipped as v2026.10.

**Architecture:**
- A new pure-maths module, `vase.py`, holds band energy, its inverse, and a regularised least-squares fit. It has no Home Assistant imports.
- The coordinator keeps the state machine. It stores remembered charges and refits after every change. It classifies post-charge readings as stale, early or settled before learning from them.
- Options move to a two-step menu. A `record_charge` action lets charges be added by hand.

**Tech Stack:**
- Python 3.13 and Home Assistant custom integration APIs (floor 2026.2.0).
- pytest with `pytest-homeassistant-custom-component` 0.13.316, and ruff.
- No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-25-vase-model-design.md`. Read it alongside this plan.

## Global Constraints

- Home Assistant floor: `"homeassistant": "2026.2.0"` in `hacs.json`, unchanged.
- No new runtime dependencies. The solver is plain Python.
- Bands: 10 bands of 10 reported points. Band 9 covers `[90, 100]`.
- Fit weights: `FIT_PRIOR_WEIGHT = 1.0`, `FIT_SMOOTHING_WEIGHT = 5.0`. Smoothing acts on corrections `d = b − p`, not on values.
- Clamp: each band is kept within `[0.25, 3.0] × capacity seed`, where the seed is `capacity_wh / 100 / 0.87`.
- Remembered charges: at most 10, and a charge is only kept if `end − start ≥ 10`.
- Rest before learning: option `rest_minutes`, default 30.
- Store version 2. Config entry version 2.
- Version `2026.10`. The house scheme is `vYYYY.N`, counting up.
- Run tests as `.venv/bin/python -m pytest -q` and lint as `.venv/bin/python -m ruff check custom_components tests`. CI runs `ruff check` only. Two files already have pre-existing `ruff format` drift; do not reformat them.
- `gh` defaults to a work account. Run `gh auth switch --user storebatfar` before any `gh` command on this repo.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.

## Review Focus

1. **A reading exactly at 100, a target of 100, or a start at or above 100.** Energy must use band 9, and `soc_after` must cap at 100 and never loop or divide by zero. Tested in Task 1.
2. **A typed band value outside the clamp**, for example 50 Wh/pt on a 720 Wh pack. It is clamped to the ceiling, not rejected and not left unclamped. Tested in Tasks 1 and 2.
3. **HA restarting inside the 30-minute rest window.** The persisted `cut_at` must survive the restart, the stale re-send on startup must be ignored, and the next real reading must teach. Tested in Task 3.
4. **The next morning's pre-charge poll arriving while a note is still pending**, for example a reading below the start. It must not teach, and the note must be cleared when the new session opens. Tested in Task 3.
5. **`record_charge` while a session is open.** Refitting can lower the requirement below what is already delivered, and the plug must then be cut immediately. Tested in Task 7.

---

## File structure

| File | Change | Responsibility |
|---|---|---|
| `custom_components/ewheels_charge_limiter/vase.py` | Create | Band maths and fit. Pure, with no HA imports. |
| `custom_components/ewheels_charge_limiter/calibration.py` | Delete | Replaced by `vase.py`. |
| `custom_components/ewheels_charge_limiter/const.py` | Modify | New option keys and tuning constants. EMA constants removed. |
| `custom_components/ewheels_charge_limiter/coordinator.py` | Modify | Uses the vase, stores charges, format-2 store with migration, reading classification. |
| `custom_components/ewheels_charge_limiter/sensor.py` | Modify | Wh per percent semantics and attributes. |
| `custom_components/ewheels_charge_limiter/config_flow.py` | Modify | Entry version 2, options menu, vase step. |
| `custom_components/ewheels_charge_limiter/__init__.py` | Modify | Entry migration, `record_charge` action. |
| `custom_components/ewheels_charge_limiter/services.yaml` | Create | Action definition. |
| `custom_components/ewheels_charge_limiter/strings.json` and `translations/en.json` | Modify | Menu, steps, action. The two files stay byte-identical. |
| `custom_components/ewheels_charge_limiter/manifest.json` | Modify | Version `2026.10`. |
| `README.md` | Modify | Self-calibration, options, entities, action. |
| `tests/test_vase.py` | Create | Maths tests. |
| `tests/test_calibration.py` | Delete | Its subject is removed. |
| `tests/test_coordinator.py`, `tests/test_entities.py`, `tests/test_config_flow.py` | Modify | New behaviours and scalar-coupled tests. |

---

### Task 1: The vase maths

**Files:**
- Create: `custom_components/ewheels_charge_limiter/vase.py`
- Modify: `custom_components/ewheels_charge_limiter/const.py` (append constants)
- Test: `tests/test_vase.py`

**Interfaces:**
- Consumes: `DEFAULT_CHARGER_EFFICIENCY` from `const.py`, which already exists.
- Produces, for later tasks:
  - `BAND_COUNT: int = 10`, `BAND_WIDTH: float = 10.0`
  - `seed_wh_per_percent(capacity_wh: float, efficiency: float = DEFAULT_CHARGER_EFFICIENCY) -> float`
  - `overlap(start: float, end: float, band: int) -> float`
  - `energy_between(bands: Sequence[float], start: float, end: float) -> float`
  - `band_at(bands: Sequence[float], soc: float) -> float`
  - `soc_after(bands: Sequence[float], start: float, wh: float) -> float`
  - `fit_bands(charges: Sequence[tuple[float, float, float]], priors: Sequence[float], floor: float, ceiling: float, prior_weight: float = FIT_PRIOR_WEIGHT, smoothing_weight: float = FIT_SMOOTHING_WEIGHT) -> list[float]`
  - In `const.py`: `FIT_PRIOR_WEIGHT`, `FIT_SMOOTHING_WEIGHT`, `BAND_CLAMP_LOW`, `BAND_CLAMP_HIGH`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_vase.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_vase.py -q`
Expected: collection error, `ImportError: cannot import name 'BAND_CLAMP_HIGH'`.

- [ ] **Step 3: Add the constants**

Append to `const.py`, directly below the `# Calibration tuning` block:

```python
# The vase: per-band energy model
BAND_CLAMP_LOW = 0.25
BAND_CLAMP_HIGH = 3.0
FIT_PRIOR_WEIGHT = 1.0
FIT_SMOOTHING_WEIGHT = 5.0
```

- [ ] **Step 4: Write `vase.py`**

```python
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
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_vase.py -q`
Expected: all pass. Then run `.venv/bin/python -m pytest -q`. The rest of the suite is untouched, so all 69 existing tests still pass.

- [ ] **Step 6: Lint and commit**

```bash
.venv/bin/python -m ruff check custom_components tests
git add custom_components/ewheels_charge_limiter/vase.py custom_components/ewheels_charge_limiter/const.py tests/test_vase.py
git commit -m "feat: per-band energy maths and fit

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Run the coordinator on the vase, with a format-2 store

**Files:**
- Modify: `custom_components/ewheels_charge_limiter/coordinator.py`
- Modify: `custom_components/ewheels_charge_limiter/const.py`
- Modify: `custom_components/ewheels_charge_limiter/sensor.py` (the `wh_per_percent` value_fn only)
- Delete: `custom_components/ewheels_charge_limiter/calibration.py`, `tests/test_calibration.py`
- Test: `tests/test_coordinator.py`

**Interfaces:**
- Consumes: everything Task 1 produces.
- Produces:
  - Coordinator attributes and properties:
    - `bands: list[float]`
    - `priors -> list[float]`
    - `typed_bands -> list[int]`
    - `remembered_charges -> int`
    - `default_prior -> float`
    - `next_charge_wh_per_percent -> float`
  - Coordinator methods:
    - `async_record_charge(start_soc: float, end_soc: float, energy_wh: float) -> None`, which raises `ValueError` if the span is under 10 points or the energy is not positive
    - `async_forget_charges() -> None`
    - `_remember_charge(start_soc, end_soc, energy_wh, source: str) -> bool`
    - `_refit() -> None`
    - `_async_after_refit() -> None`
  - Module-level `migrate_stored_data(old_major_version: int, data: dict) -> dict`
  - Constants: `BAND_OPTION_KEYS`, `MAX_REMEMBERED_CHARGES = 10`, `STORAGE_VERSION = 2`

- [ ] **Step 1: Write the failing tests**

In `tests/test_coordinator.py`, drop `OPT_WH_PER_PERCENT` from the const import block. Delete the two tests that exercise the retired option: `test_a_new_wh_per_percent_option_is_applied` and `test_an_unchanged_wh_per_percent_option_does_not_undo_calibration`.

Replace the body of `test_manual_stop_records_no_calibration` with:

```python
async def test_manual_stop_records_no_calibration(hass: HomeAssistant):
    coordinator = await _coordinator(hass)
    hass.states.async_set(POWER, "120", {"unit_of_measurement": "W"})
    await hass.async_block_till_done()
    hass.states.async_set(ENERGY, "0.3", {"unit_of_measurement": "kWh"})
    await hass.async_block_till_done()

    await coordinator.async_set_plug(False)
    await hass.async_block_till_done()
    hass.states.async_set(SOC, "75", {"unit_of_measurement": "%"})
    await hass.async_block_till_done()

    assert coordinator.remembered_charges == 0
```

Add `from custom_components.ewheels_charge_limiter.const import MAX_REMEMBERED_CHARGES` to the imports. Then append:

```python
# 40-50 and 50-60 at 5 Wh/pt, 60-70 and 70-80 at 10 Wh/pt; everything else seed.
SHAPED = {"band_4": 5.0, "band_5": 5.0, "band_6": 10.0, "band_7": 10.0}


async def test_required_energy_follows_the_bands(hass: HomeAssistant):
    coordinator = await _coordinator(hass, **SHAPED)
    hass.states.async_set(POWER, "120", {"unit_of_measurement": "W"})
    await hass.async_block_till_done()
    # 40 -> 80: 10*5 + 10*5 + 10*10 + 10*10
    assert coordinator.required_wh == pytest.approx(300.0)


async def test_projected_charge_walks_the_bands(hass: HomeAssistant):
    coordinator = await _coordinator(hass, **SHAPED)
    hass.states.async_set(POWER, "120", {"unit_of_measurement": "W"})
    await hass.async_block_till_done()
    hass.states.async_set(ENERGY, "0.15", {"unit_of_measurement": "kWh"})
    await hass.async_block_till_done()
    # 100 Wh carries 40 -> 60 at 5 Wh/pt, the last 50 Wh buys 5 points at 10
    assert coordinator.projected_soc == pytest.approx(65.0)


async def test_typing_a_band_mid_session_recomputes_the_requirement(
    hass: HomeAssistant,
):
    coordinator = await _coordinator(hass)
    hass.states.async_set(POWER, "120", {"unit_of_measurement": "W"})
    await hass.async_block_till_done()

    hass.config_entries.async_update_entry(
        coordinator.entry, options={**coordinator.entry.options, "band_7": 20.0}
    )
    await hass.async_block_till_done()

    seed = 720 / 100 / 0.87
    assert coordinator.required_wh == pytest.approx(30 * seed + 10 * 20.0)


async def test_a_typed_band_above_the_ceiling_is_clamped(hass: HomeAssistant):
    coordinator = await _coordinator(hass, band_0=500.0)
    assert coordinator.bands[0] == pytest.approx(3.0 * 720 / 100 / 0.87)


async def test_a_completed_charge_is_remembered(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
):
    coordinator = await _coordinator(hass)
    hass.states.async_set(POWER, "120", {"unit_of_measurement": "W"})
    await hass.async_block_till_done()
    hass.states.async_set(ENERGY, "0.4", {"unit_of_measurement": "kWh"})
    await hass.async_block_till_done()
    assert coordinator.state is ChargeState.COMPLETE

    freezer.tick(timedelta(minutes=31))
    hass.states.async_set(SOC, "85", {"unit_of_measurement": "%"})
    await hass.async_block_till_done()

    assert coordinator.remembered_charges == 1
    assert coordinator._charges[-1]["end_soc"] == 85.0
    assert coordinator.bands != pytest.approx([720 / 100 / 0.87] * 10)


async def test_remembered_charges_cap_at_ten_dropping_the_oldest(
    hass: HomeAssistant,
):
    coordinator = await _coordinator(hass)
    for start in range(11):
        await coordinator.async_record_charge(float(start), start + 40.0, 300.0)
    assert coordinator.remembered_charges == MAX_REMEMBERED_CHARGES
    assert coordinator._charges[0]["start_soc"] == 1.0


async def test_record_charge_rejects_a_short_span(hass: HomeAssistant):
    coordinator = await _coordinator(hass)
    with pytest.raises(ValueError):
        await coordinator.async_record_charge(50.0, 55.0, 40.0)
    assert coordinator.remembered_charges == 0


async def test_forgetting_charges_returns_to_the_priors(hass: HomeAssistant):
    coordinator = await _coordinator(hass)
    await coordinator.async_record_charge(40.0, 80.0, 200.0)
    await coordinator.async_forget_charges()
    assert coordinator.remembered_charges == 0
    assert coordinator.bands == pytest.approx([720 / 100 / 0.87] * 10)


async def test_remembered_charges_survive_a_reload(hass: HomeAssistant, hass_storage):
    coordinator = await _coordinator(hass)
    await coordinator.async_record_charge(40.0, 80.0, 200.0)
    bands = list(coordinator.bands)
    await coordinator.async_shutdown()

    revived = ChargeLimiterCoordinator(hass, coordinator.entry)
    await revived.async_setup()
    assert revived.remembered_charges == 1
    assert revived.bands == pytest.approx(bands)


async def test_a_format_1_store_is_migrated(hass: HomeAssistant, hass_storage):
    _register_switch_services(hass)
    hass.states.async_set(PLUG, "off")
    hass.states.async_set(POWER, "0", {"unit_of_measurement": "W"})
    hass.states.async_set(ENERGY, "0", {"unit_of_measurement": "kWh"})
    hass.states.async_set(SOC, "97", {"unit_of_measurement": "%"})
    entry = _entry(hass)
    key = f"{DOMAIN}.{entry.entry_id}"
    hass_storage[key] = {
        "version": 1,
        "minor_version": 1,
        "key": key,
        "data": {
            "state": "complete",
            "enabled": True,
            "wh_per_percent": 5.758,
            "required_wh": None,
            "session_start_soc": None,
            "session_started_at": None,
            "pending_calibration": {"start_soc": 45.0, "delivered_wh": 270.1},
            "meter": None,
        },
    }

    coordinator = ChargeLimiterCoordinator(hass, entry)
    await coordinator.async_setup()

    assert coordinator.default_prior == pytest.approx(5.758)
    assert coordinator.bands == pytest.approx([5.758] * 10)
    assert coordinator.remembered_charges == 0
    assert coordinator._pending_calibration["cut_at"] == 0.0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_coordinator.py -q`
Expected: collection error, because `MAX_REMEMBERED_CHARGES` does not exist yet.

- [ ] **Step 3: Update `const.py`**

- Delete the `CALIBRATION_EMA_WEIGHT`, `CALIBRATION_CLAMP_LOW` and `CALIBRATION_CLAMP_HIGH` lines. Keep `CALIBRATION_MIN_DELTA_PCT = 10.0`.
- Replace `STORAGE_VERSION = 1` with `STORAGE_VERSION = 2`.
- Change the `OPT_WH_PER_PERCENT` line to:

```python
# Retired in 2026.10 (superseded by the vase); read only by entry migration.
OPT_WH_PER_PERCENT = "wh_per_percent"
```

- Append under the vase block:

```python
BAND_OPTION_KEYS = tuple(f"band_{i}" for i in range(10))
MAX_REMEMBERED_CHARGES = 10
```

- [ ] **Step 4: Update `coordinator.py`**

Replace the calibration import:

```python
from .calibration import seed_wh_per_percent, update_wh_per_percent
```

with:

```python
from .vase import (
    BAND_COUNT,
    band_at,
    energy_between,
    fit_bands,
    seed_wh_per_percent,
    soc_after,
)
```

In the `.const` import list:
- remove `OPT_WH_PER_PERCENT`;
- add `BAND_CLAMP_HIGH`, `BAND_CLAMP_LOW`, `BAND_OPTION_KEYS`, `CALIBRATION_MIN_DELTA_PCT` and `MAX_REMEMBERED_CHARGES`.

Add these after `_to_wh`:

```python
def migrate_stored_data(old_major_version: int, data: dict[str, Any]) -> dict[str, Any]:
    """Upgrade stored data from an older format.

    Format 1 carried one learned Wh-per-percent. It becomes the default prior
    for every band nobody has typed a value for, so nothing learned is lost.
    A pending calibration from before the upgrade gets cut_at 0: treated as
    long settled, so the first real reading after the upgrade can still use it.
    """
    if old_major_version == 1:
        data = dict(data)
        data["default_prior"] = data.pop("wh_per_percent", None)
        data.setdefault("charges", [])
        if (pending := data.get("pending_calibration")) is not None:
            data["pending_calibration"] = {"cut_at": 0.0, **pending}
    return data


class _LimiterStore(Store[dict[str, Any]]):
    """Store that knows how to upgrade its own older formats."""

    async def _async_migrate_func(
        self,
        old_major_version: int,
        old_minor_version: int,
        old_data: dict[str, Any],
    ) -> dict[str, Any]:
        return migrate_stored_data(old_major_version, old_data)
```

In `__init__`, replace the `self._seed` / `self.wh_per_percent` / `self._options_wh_per_percent` block with:

```python
        self._seed = seed_wh_per_percent(self._capacity_wh)
        self._default_prior: float = self._seed
        self._charges: list[dict[str, Any]] = []
        self.bands: list[float] = [self._seed] * BAND_COUNT
```

Then replace `Store(` with `_LimiterStore(` in the store construction.

Replace `projected_soc` and add the new properties right after it:

```python
    @property
    def projected_soc(self) -> float | None:
        if self.session_start_soc is None:
            return None
        return soc_after(self.bands, self.session_start_soc, self._meter.delivered_wh)

    @property
    def priors(self) -> list[float]:
        """Each band's starting point: its typed value, else the default."""
        return [
            float(value)
            if (value := self.entry.options.get(key)) is not None
            else self._default_prior
            for key in BAND_OPTION_KEYS
        ]

    @property
    def typed_bands(self) -> list[int]:
        return [
            i
            for i, key in enumerate(BAND_OPTION_KEYS)
            if self.entry.options.get(key) is not None
        ]

    @property
    def remembered_charges(self) -> int:
        return len(self._charges)

    @property
    def default_prior(self) -> float:
        return self._default_prior

    @property
    def next_charge_wh_per_percent(self) -> float:
        """Average cost per point of charging from the latest reading to target."""
        target = self.target_soc
        soc = _as_float(self.hass.states.get(self._soc_entity))
        if soc is None or soc >= target:
            return band_at(self.bands, target)
        return energy_between(self.bands, soc, target) / (target - soc)
```

In `async_setup`, add `self._refit()` immediately after the `if stored := ...: self._restore(stored)` line.

Replace `_restore` and `_store_data`:

```python
    def _restore(self, stored: dict[str, Any]) -> None:
        self.state = ChargeState(stored.get("state", ChargeState.IDLE))
        self.enabled = stored.get("enabled", True)
        self._default_prior = float(stored.get("default_prior") or self._seed)
        self._charges = list(stored.get("charges", []))
        self.required_wh = stored.get("required_wh")
        self.session_start_soc = stored.get("session_start_soc")
        self._session_started_at = stored.get("session_started_at")
        self._pending_calibration = stored.get("pending_calibration")
        if meter := stored.get("meter"):
            self._meter = EnergyMeter.from_dict(meter)

    @callback
    def _store_data(self) -> dict[str, Any]:
        return {
            "state": str(self.state),
            "enabled": self.enabled,
            "default_prior": self._default_prior,
            "charges": self._charges,
            "bands": self.bands,
            "required_wh": self.required_wh,
            "session_start_soc": self.session_start_soc,
            "session_started_at": self._session_started_at,
            "pending_calibration": self._pending_calibration,
            "meter": self._meter.as_dict(),
        }
```

In `_async_options_updated`, replace the line `self._apply_wh_per_percent_option()` with `self._refit()`. Then delete the whole `_apply_wh_per_percent_option` method.

In `_async_recompute_required_wh`, replace the `self.required_wh = (...) * self.wh_per_percent` assignment with:

```python
        self.required_wh = energy_between(
            self.bands, self.session_start_soc, self.target_soc
        )
```

In `_async_open_session`, replace `self.required_wh = (self.target_soc - soc) * self.wh_per_percent` with:

```python
        self.required_wh = energy_between(self.bands, soc, self.target_soc)
```

Add these commands after `async_set_plug`:

```python
    async def async_record_charge(
        self, start_soc: float, end_soc: float, energy_wh: float
    ) -> None:
        """Remember a charge measured some other way, then refit."""
        if not self._remember_charge(start_soc, end_soc, energy_wh, source="manual"):
            raise ValueError(
                "A charge must span at least "
                f"{CALIBRATION_MIN_DELTA_PCT:g} points and deliver energy"
            )
        await self._async_after_refit()

    async def async_forget_charges(self) -> None:
        """Drop every remembered charge; the bands fall back to their priors."""
        self._charges.clear()
        self._refit()
        await self._async_after_refit()
```

Replace `_apply_pending_calibration` with:

```python
    def _apply_pending_calibration(self, soc: float) -> None:
        if self._pending_calibration is None:
            return
        pending = self._pending_calibration
        self._pending_calibration = None
        self._remember_charge(
            pending["start_soc"], soc, pending["delivered_wh"], source="auto"
        )
```

Task 3 replaces this method outright. In this task it is only rewired to feed charges.

In `_async_handle_soc`, add `await self._async_persist()` directly after the `self._apply_pending_calibration(soc)` line, so learning is never lost to a restart.

Add these helpers in the `# ---- helpers ----` section:

```python
    @callback
    def _remember_charge(
        self, start_soc: float, end_soc: float, energy_wh: float, source: str
    ) -> bool:
        """Keep a charge for the fit. False if it carries too little signal."""
        if end_soc - start_soc < CALIBRATION_MIN_DELTA_PCT or energy_wh <= 0:
            return False
        self._charges.append(
            {
                "start_soc": float(start_soc),
                "end_soc": float(end_soc),
                "energy_wh": float(energy_wh),
                "recorded_at": dt_util.utcnow().timestamp(),
                "source": source,
            }
        )
        del self._charges[:-MAX_REMEMBERED_CHARGES]
        self._refit()
        return True

    @callback
    def _refit(self) -> None:
        self.bands = fit_bands(
            [(c["start_soc"], c["end_soc"], c["energy_wh"]) for c in self._charges],
            self.priors,
            floor=BAND_CLAMP_LOW * self._seed,
            ceiling=BAND_CLAMP_HIGH * self._seed,
        )

    async def _async_after_refit(self) -> None:
        """Persist, and re-measure an open session against the new bands."""
        await self._async_persist()
        await self._async_recompute_required_wh()
        self._notify()
```

- [ ] **Step 5: Update the sensor value and delete the old module**

In `sensor.py`, change the `wh_per_percent` description's `value_fn` to:

```python
        value_fn=lambda c: round(c.next_charge_wh_per_percent, 3),
```

Then remove the old module and its tests:

```bash
git rm custom_components/ewheels_charge_limiter/calibration.py tests/test_calibration.py
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass. The existing tests expecting 331 Wh and 414 Wh still hold, because with no charges and no typed bands the bands are exactly the seed. `test_wh_per_percent_sensor_reports_the_seed` still reads the seed.

- [ ] **Step 7: Lint and commit**

```bash
.venv/bin/python -m ruff check custom_components tests
git add -A custom_components tests
git commit -m "feat: run the coordinator on the vase, with remembered charges

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Only learn from real, settled readings (bugs 1–4)

**Files:**
- Modify: `custom_components/ewheels_charge_limiter/coordinator.py`
- Modify: `custom_components/ewheels_charge_limiter/const.py`
- Test: `tests/test_coordinator.py`

**Interfaces:**
- Consumes: `_remember_charge`, `_charges` and `_pending_calibration` from Task 2.
- Produces:
  - `OPT_REST_MINUTES = "rest_minutes"` and `DEFAULT_REST_MINUTES = 30` in `const.py`
  - A pending note shaped `{"start_soc", "delivered_wh", "cut_at"}`
  - `_async_handle_soc(event)`, which now takes the whole state-changed event

- [ ] **Step 1: Write the failing tests**

Add `from custom_components.ewheels_charge_limiter.const import OPT_REST_MINUTES` to the imports in `tests/test_coordinator.py`, then append:

```python
async def _complete_a_session(hass: HomeAssistant, coordinator) -> None:
    """40% -> target 80% at the flat seed; 400 Wh delivered, plug cut."""
    hass.states.async_set(POWER, "120", {"unit_of_measurement": "W"})
    await hass.async_block_till_done()
    hass.states.async_set(ENERGY, "0.4", {"unit_of_measurement": "kWh"})
    await hass.async_block_till_done()
    assert coordinator.state is ChargeState.COMPLETE


async def _report_soc(hass: HomeAssistant, value: str) -> None:
    hass.states.async_set(SOC, value, {"unit_of_measurement": "%"})
    await hass.async_block_till_done()


async def test_a_stale_resend_does_not_consume_the_note(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
):
    """Bug 1: the ESPHome node replays its last value after a reconnect."""
    coordinator = await _coordinator(hass)
    await _complete_a_session(hass, coordinator)
    freezer.tick(timedelta(minutes=31))

    await _report_soc(hass, STATE_UNAVAILABLE)
    await _report_soc(hass, "40")  # old news
    assert coordinator._pending_calibration is not None
    assert coordinator.remembered_charges == 0

    await _report_soc(hass, "85")
    assert coordinator.remembered_charges == 1
    assert coordinator._pending_calibration is None


async def test_an_early_reading_clears_the_projection_but_keeps_the_note(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
):
    """Bugs 2 and 3: a real reading beats the guess, but foam teaches nothing."""
    coordinator = await _coordinator(hass)
    await _complete_a_session(hass, coordinator)
    assert coordinator.projected_soc is not None

    freezer.tick(timedelta(minutes=5))
    await _report_soc(hass, "85")
    assert coordinator.projected_soc is None
    assert coordinator._pending_calibration is not None
    assert coordinator.remembered_charges == 0

    freezer.tick(timedelta(minutes=30))
    await _report_soc(hass, "84")
    assert coordinator.remembered_charges == 1
    assert coordinator._charges[-1]["end_soc"] == 84.0


async def test_the_rest_time_is_an_option(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
):
    coordinator = await _coordinator(hass, **{OPT_REST_MINUTES: 5})
    await _complete_a_session(hass, coordinator)
    freezer.tick(timedelta(minutes=6))
    await _report_soc(hass, "85")
    assert coordinator.remembered_charges == 1


async def test_a_too_small_rise_keeps_the_note(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
):
    """Bug 1b: a silly answer is thrown away, the note is not."""
    coordinator = await _coordinator(hass)
    await _complete_a_session(hass, coordinator)
    freezer.tick(timedelta(minutes=31))

    await _report_soc(hass, "45")  # only 5 points above the start
    assert coordinator._pending_calibration is not None
    assert coordinator.remembered_charges == 0

    await _report_soc(hass, "85")
    assert coordinator.remembered_charges == 1


async def test_a_new_session_clears_an_old_note(hass: HomeAssistant):
    """Bug 4: a note only ever belongs to its own charge."""
    coordinator = await _coordinator(hass)
    await _complete_a_session(hass, coordinator)
    assert coordinator._pending_calibration is not None

    await coordinator.async_set_plug(True)
    await hass.async_block_till_done()
    hass.states.async_set(POWER, "0", {"unit_of_measurement": "W"})
    await hass.async_block_till_done()
    hass.states.async_set(POWER, "120", {"unit_of_measurement": "W"})
    await hass.async_block_till_done()

    assert coordinator._pending_calibration is None


async def test_a_next_morning_precharge_poll_does_not_teach(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
):
    """Ridden overnight without a post-charge poll: below the start is no signal."""
    coordinator = await _coordinator(hass)
    await _complete_a_session(hass, coordinator)
    freezer.tick(timedelta(hours=12))

    await _report_soc(hass, "30")
    assert coordinator.remembered_charges == 0
    assert coordinator._pending_calibration is not None
    assert coordinator.state is ChargeState.ARMED

    hass.states.async_set(POWER, "0", {"unit_of_measurement": "W"})
    await hass.async_block_till_done()
    hass.states.async_set(POWER, "120", {"unit_of_measurement": "W"})
    await hass.async_block_till_done()
    assert coordinator._pending_calibration is None
    assert coordinator.remembered_charges == 0


async def test_a_reading_mid_charge_keeps_the_projection(hass: HomeAssistant):
    coordinator = await _coordinator(hass)
    hass.states.async_set(POWER, "120", {"unit_of_measurement": "W"})
    await hass.async_block_till_done()
    await _report_soc(hass, "41")
    assert coordinator.session_start_soc == 40.0
    assert coordinator.projected_soc is not None


async def test_a_restart_inside_the_rest_window_still_learns(
    hass: HomeAssistant, hass_storage, freezer: FrozenDateTimeFactory
):
    coordinator = await _coordinator(hass)
    await _complete_a_session(hass, coordinator)
    await coordinator.async_shutdown()

    revived = ChargeLimiterCoordinator(hass, coordinator.entry)
    await revived.async_setup()
    freezer.tick(timedelta(minutes=31))

    await _report_soc(hass, STATE_UNAVAILABLE)
    await _report_soc(hass, "40")  # the replay on startup
    assert revived._pending_calibration is not None

    await _report_soc(hass, "85")
    assert revived.remembered_charges == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_coordinator.py -q`
Expected: collection error, because `OPT_REST_MINUTES` does not exist yet.

- [ ] **Step 3: Add the option constants**

In `const.py`, add `OPT_REST_MINUTES = "rest_minutes"` to the option keys and `DEFAULT_REST_MINUTES = 30` to the defaults.

- [ ] **Step 4: Update `coordinator.py`**

Add `DEFAULT_REST_MINUTES` and `OPT_REST_MINUTES` to the `.const` import list. Then add this property with the other option properties:

```python
    @property
    def _rest_seconds(self) -> float:
        return (
            float(self.entry.options.get(OPT_REST_MINUTES, DEFAULT_REST_MINUTES))
            * 60.0
        )
```

In `_handle_change`, change the SoC dispatch line to:

```python
        elif entity_id == self._soc_entity:
            await self._async_handle_soc(event)
```

Replace `_async_handle_soc` entirely:

```python
    async def _async_handle_soc(self, event: Event[EventStateChangedData]) -> None:
        """A state-of-charge reading arrived."""
        soc = _as_float(event.data["new_state"])
        if soc is None:
            return

        old_state = event.data["old_state"]
        if old_state is not None and old_state.state not in _INVALID:
            # Only a change between two numbers is news. A jump from
            # unavailable is the node replaying what it last knew after a
            # reconnect or an HA restart - usually the pre-charge value.
            await self._async_take_real_reading(soc)

        if (
            self.state in _PLUG_OFF_STATES
            and soc < self.target_soc - self._rearm_hysteresis
        ):
            await self._async_arm()

    async def _async_take_real_reading(self, soc: float) -> None:
        """A real reading beats the projection; a settled one can teach."""
        changed = False
        if self.session_start_soc is not None and self.state not in (
            ChargeState.CHARGING,
            ChargeState.UNCALIBRATED,
        ):
            self.session_start_soc = None
            changed = True

        pending = self._pending_calibration
        if (
            pending is not None
            and dt_util.utcnow().timestamp() - pending.get("cut_at", 0.0)
            >= self._rest_seconds
            and self._remember_charge(
                pending["start_soc"], soc, pending["delivered_wh"], source="auto"
            )
        ):
            # Removed only once it has taught something. Too soon, or too
            # small a rise, and it waits for better news instead.
            self._pending_calibration = None
            changed = True

        if changed:
            await self._async_persist()
```

Delete the `_apply_pending_calibration` method.

In `_async_complete`, change the note to include the cut time:

```python
            self._pending_calibration = {
                "start_soc": self.session_start_soc,
                "delivered_wh": self._meter.delivered_wh,
                "cut_at": dt_util.utcnow().timestamp(),
            }
```

At the top of `_async_open_session`, before `soc_state = ...`, add:

```python
        # A note belongs to its own charge. Left in place, a later reading
        # could pair it with this one and teach something false.
        self._pending_calibration = None
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass. `test_a_fresh_low_reading_rearms_without_energising` still passes: the unsettled reading keeps the note and the re-arm path is unchanged.

- [ ] **Step 6: Lint and commit**

```bash
.venv/bin/python -m ruff check custom_components tests
git add -A custom_components tests
git commit -m "fix: learn only from real, settled readings

A replayed value after a reconnect, a reading taken before the pack has
settled, and a too-small rise no longer consume the calibration note; a
real reading clears the projection; a new session drops any older note.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Config entry version 2

**Files:**
- Modify: `custom_components/ewheels_charge_limiter/__init__.py`
- Modify: `custom_components/ewheels_charge_limiter/config_flow.py` (`VERSION` and `_default_options`)
- Test: `tests/test_entities.py`, `tests/test_config_flow.py`

**Interfaces:**
- Consumes: `OPT_WH_PER_PERCENT`, `OPT_REST_MINUTES` and `DEFAULT_REST_MINUTES` from `const.py`.
- Produces: `async_migrate_entry(hass, entry) -> bool` in `__init__.py`, and the config flow's `VERSION = 2`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_entities.py`, add `OPT_REST_MINUTES` and `OPT_WH_PER_PERCENT` to the const import, then append:

```python
async def test_a_version_1_entry_is_migrated(hass: HomeAssistant):
    setup_test_component_platform(hass, SWITCH_DOMAIN, [MockToggleEntity("Plug", "on")])
    assert await async_setup_component(
        hass, SWITCH_DOMAIN, {SWITCH_DOMAIN: {"platform": "test"}}
    )
    hass.states.async_set(POWER, "0", {"unit_of_measurement": "W"})
    hass.states.async_set(ENERGY, "0", {"unit_of_measurement": "kWh"})
    hass.states.async_set(SOC, "40", {"unit_of_measurement": "%"})
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        unique_id="plug-1",
        title="Scooter",
        data={
            CONF_PLUG_SWITCH: PLUG,
            CONF_POWER_ENTITY: POWER,
            CONF_ENERGY_ENTITY: ENERGY,
            CONF_SOC_ENTITY: SOC,
            CONF_CAPACITY_WH: 720,
        },
        options={OPT_TARGET_SOC: 90.0, OPT_WH_PER_PERCENT: 6.0},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.version == 2
    assert OPT_WH_PER_PERCENT not in entry.options
    assert entry.options[OPT_REST_MINUTES] == 30
    assert entry.options[OPT_TARGET_SOC] == 90.0
```

In `tests/test_config_flow.py`, add `OPT_REST_MINUTES` to the import. In `test_happy_path_creates_an_entry`, after the existing assertions on the created entry, add:

```python
    assert result["result"].version == 2
    assert result["result"].options[OPT_REST_MINUTES] == 30
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_entities.py tests/test_config_flow.py -q`
Expected: `test_a_version_1_entry_is_migrated` fails because the entry is left at version 1 with `wh_per_percent` still present. The happy path fails with `version == 1`.

- [ ] **Step 3: Implement**

In `config_flow.py`:
- set `VERSION = 2`;
- add `OPT_REST_MINUTES` and `DEFAULT_REST_MINUTES` to the const import;
- add `OPT_REST_MINUTES: DEFAULT_REST_MINUTES,` to the dict returned by `_default_options()`.

In `__init__.py`, add the imports:

```python
from .const import DEFAULT_REST_MINUTES, OPT_REST_MINUTES, OPT_WH_PER_PERCENT
```

and the function:

```python
async def async_migrate_entry(hass: HomeAssistant, entry: EWheelsConfigEntry) -> bool:
    """Upgrade an entry's options to the current version."""
    if entry.version == 1:
        # The single learned Wh-per-percent is superseded by the vase; the
        # stored learning migrates separately, with the coordinator's store.
        options = {k: v for k, v in entry.options.items() if k != OPT_WH_PER_PERCENT}
        options.setdefault(OPT_REST_MINUTES, DEFAULT_REST_MINUTES)
        hass.config_entries.async_update_entry(entry, options=options, version=2)
    return True
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/python -m ruff check custom_components tests
git add -A custom_components tests
git commit -m "feat: config entry version 2 retires the scalar option

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Wh per percent shows the next charge's cost and exposes the bands

**Files:**
- Modify: `custom_components/ewheels_charge_limiter/sensor.py`
- Test: `tests/test_entities.py`

**Interfaces:**
- Consumes: `next_charge_wh_per_percent`, `bands`, `typed_bands`, `remembered_charges` and `default_prior` from Task 2.
- Produces: the `attrs_fn` field on `ChargeLimiterSensorDescription`, and `extra_state_attributes` on `ChargeLimiterSensor`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_entities.py`, change `_setup`'s signature to `async def _setup(hass: HomeAssistant, power: bool = True, **options) -> MockConfigEntry:`. Change its `options={...}` dict so it ends with `**options,` after the three existing keys. Then append:

```python
SHAPED = {"band_4": 5.0, "band_5": 5.0, "band_6": 10.0, "band_7": 10.0}


async def test_wh_per_percent_is_the_average_cost_of_the_next_charge(
    hass: HomeAssistant,
):
    await _setup(hass, **SHAPED)
    # SoC 40 -> target 80: (50 + 50 + 100 + 100) / 40 points
    assert float(hass.states.get("sensor.scooter_wh_per_percent").state) == (
        pytest.approx(7.5)
    )


async def test_wh_per_percent_at_or_above_target_shows_the_target_band(
    hass: HomeAssistant,
):
    await _setup(hass, **SHAPED)
    hass.states.async_set(SOC, "85", {"unit_of_measurement": "%"})
    await hass.async_block_till_done()
    # target 80 lies in band 8, which is untyped: the seed
    assert float(hass.states.get("sensor.scooter_wh_per_percent").state) == (
        pytest.approx(720 / 100 / 0.87, abs=0.001)
    )


async def test_wh_per_percent_exposes_the_vase(hass: HomeAssistant):
    await _setup(hass, **SHAPED)
    attributes = hass.states.get("sensor.scooter_wh_per_percent").attributes
    assert attributes["bands"][4] == 5.0
    assert attributes["bands"][6] == 10.0
    assert attributes["typed_bands"] == [4, 5, 6, 7]
    assert attributes["remembered_charges"] == 0
    assert attributes["default_prior"] == pytest.approx(8.276, abs=0.001)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_entities.py -q`
Expected: `test_wh_per_percent_exposes_the_vase` fails with `KeyError: 'bands'`. The value tests already pass from Task 2, and they stay in as coverage.

- [ ] **Step 3: Implement**

In `sensor.py`:
- add `from typing import Any`;
- add the field `attrs_fn: Callable[[ChargeLimiterCoordinator], dict[str, Any]] | None = None` to `ChargeLimiterSensorDescription`;
- give the `wh_per_percent` description:

```python
        attrs_fn=lambda c: {
            "bands": [round(b, 3) for b in c.bands],
            "typed_bands": c.typed_bands,
            "remembered_charges": c.remembered_charges,
            "default_prior": round(c.default_prior, 3),
        },
```

- add to `ChargeLimiterSensor`:

```python
    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/python -m ruff check custom_components tests
git add -A custom_components tests
git commit -m "feat: Wh per percent shows the next charge's cost and the bands

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Options menu with a Vase values step

**Files:**
- Modify: `custom_components/ewheels_charge_limiter/config_flow.py`
- Modify: `custom_components/ewheels_charge_limiter/const.py`
- Modify: `custom_components/ewheels_charge_limiter/strings.json` and `translations/en.json`
- Test: `tests/test_config_flow.py`, `tests/test_entities.py`

**Interfaces:**
- Consumes: `BAND_OPTION_KEYS`, `OPT_REST_MINUTES` and `DEFAULT_REST_MINUTES` from `const.py`; `bands` and `async_forget_charges()` from the coordinator.
- Produces: options steps `init` (a menu), `settings` and `vase`, and the constant `OPT_FORGET_CHARGES = "forget_charges"`, which is used in the flow only and never stored.

- [ ] **Step 1: Write the failing tests**

In `tests/test_config_flow.py`, replace `test_options_flow_updates_the_target` with:

```python
async def test_options_open_on_a_menu(hass: HomeAssistant):
    entry = MockConfigEntry(domain=DOMAIN, unique_id=PLUG, data={CONF_PLUG_SWITCH: PLUG})
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["settings", "vase"]


async def test_options_flow_updates_the_target(hass: HomeAssistant):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=PLUG,
        data={CONF_PLUG_SWITCH: PLUG},
        options={OPT_TARGET_SOC: 80.0},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "settings"}
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {OPT_TARGET_SOC: 90.0}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][OPT_TARGET_SOC] == 90.0
    assert result["data"][OPT_REST_MINUTES] == 30


async def test_vase_step_stores_typed_bands_and_drops_cleared_ones(
    hass: HomeAssistant,
):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=PLUG,
        data={CONF_PLUG_SWITCH: PLUG},
        options={OPT_TARGET_SOC: 80.0, "band_3": 4.0},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "vase"}
    )
    assert result["type"] is FlowResultType.FORM  # works on an unloaded entry

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"band_0": 5.0}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["band_0"] == 5.0
    assert "band_3" not in result["data"]
    assert result["data"][OPT_TARGET_SOC] == 80.0
    assert "forget_charges" not in result["data"]
```

In `tests/test_entities.py`, append:

```python
async def test_saving_vase_values_refits_without_a_reload(hass: HomeAssistant):
    entry = await _setup(hass)
    coordinator = entry.runtime_data

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "vase"}
    )
    await hass.config_entries.options.async_configure(
        result["flow_id"], {"band_9": 12.0}
    )
    await hass.async_block_till_done()

    assert entry.runtime_data is coordinator
    assert coordinator.bands[9] == pytest.approx(12.0)


async def test_forget_in_the_vase_step_clears_remembered_charges(
    hass: HomeAssistant,
):
    entry = await _setup(hass)
    coordinator = entry.runtime_data
    await coordinator.async_record_charge(40.0, 80.0, 200.0)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "vase"}
    )
    await hass.config_entries.options.async_configure(
        result["flow_id"], {"forget_charges": True}
    )
    await hass.async_block_till_done()

    assert coordinator.remembered_charges == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_config_flow.py tests/test_entities.py -q`
Expected: the menu test fails with `result["type"]` being `FORM`, and the vase tests fail with `unknown step`.

- [ ] **Step 3: Implement the flow**

In `const.py`, add under the option keys:

```python
# Options-flow only; never stored. Drops every remembered charge.
OPT_FORGET_CHARGES = "forget_charges"
```

In `config_flow.py`:
- add `BAND_OPTION_KEYS`, `DEFAULT_REST_MINUTES`, `OPT_FORGET_CHARGES` and `OPT_REST_MINUTES` to the const import;
- remove `OPT_WH_PER_PERCENT` from it;
- replace the whole `EWheelsChargeLimiterOptionsFlow` class with the one below.

```python
class EWheelsChargeLimiterOptionsFlow(OptionsFlow):
    """Options: the tuning settings, and the vase's per-band starting points."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(step_id="init", menu_options=["settings", "vase"])

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit the tuning options."""
        if user_input is not None:
            return self.async_create_entry(
                data={**self.config_entry.options, **user_input}
            )

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    OPT_TARGET_SOC,
                    default=options.get(OPT_TARGET_SOC, DEFAULT_TARGET_SOC),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=50, max=100, step=1, unit_of_measurement="%"
                    )
                ),
                vol.Required(
                    OPT_REARM_HYSTERESIS,
                    default=options.get(
                        OPT_REARM_HYSTERESIS, DEFAULT_REARM_HYSTERESIS
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=50, step=1)
                ),
                vol.Required(
                    OPT_CHARGING_POWER_THRESHOLD,
                    default=options.get(
                        OPT_CHARGING_POWER_THRESHOLD,
                        DEFAULT_CHARGING_POWER_THRESHOLD,
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=100, step=1)
                ),
                vol.Required(
                    OPT_IDLE_CLOSE_MINUTES,
                    default=options.get(
                        OPT_IDLE_CLOSE_MINUTES, DEFAULT_IDLE_CLOSE_MINUTES
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=120, step=1)
                ),
                vol.Required(
                    OPT_MAX_SESSION_HOURS,
                    default=options.get(
                        OPT_MAX_SESSION_HOURS, DEFAULT_MAX_SESSION_HOURS
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=48, step=1)
                ),
                vol.Required(
                    OPT_SOC_STALENESS_HOURS,
                    default=options.get(
                        OPT_SOC_STALENESS_HOURS, DEFAULT_SOC_STALENESS_HOURS
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=168, step=1)
                ),
                vol.Required(
                    OPT_REST_MINUTES,
                    default=options.get(OPT_REST_MINUTES, DEFAULT_REST_MINUTES),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0, max=240, step=1)
                ),
            }
        )
        return self.async_show_form(step_id="settings", data_schema=schema)

    async def async_step_vase(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Type per-band starting points, or forget remembered charges.

        Fields are pre-filled with typed values only, never learned ones:
        pre-filling learned values would turn every one of them into a typed
        value the moment the form is saved.
        """
        coordinator = getattr(self.config_entry, "runtime_data", None)

        if user_input is not None:
            forget = user_input.pop(OPT_FORGET_CHARGES, False)
            options = {
                key: value
                for key, value in self.config_entry.options.items()
                if key not in BAND_OPTION_KEYS
            }
            for key in BAND_OPTION_KEYS:
                if user_input.get(key) is not None:
                    options[key] = float(user_input[key])
            if forget and coordinator is not None:
                await coordinator.async_forget_charges()
            return self.async_create_entry(data=options)

        options = self.config_entry.options
        fields: dict[Any, Any] = {
            vol.Optional(
                key, description={"suggested_value": options.get(key)}
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0.1,
                    max=1000,
                    step=0.01,
                    unit_of_measurement="Wh",
                    mode=selector.NumberSelectorMode.BOX,
                )
            )
            for key in BAND_OPTION_KEYS
        }
        fields[vol.Optional(OPT_FORGET_CHARGES, default=False)] = (
            selector.BooleanSelector()
        )
        learned = (
            " · ".join(
                f"{i * 10}–{i * 10 + 10} %: {value:.2f}"
                for i, value in enumerate(coordinator.bands)
            )
            if coordinator is not None
            else "not loaded yet"
        )
        return self.async_show_form(
            step_id="vase",
            data_schema=vol.Schema(fields),
            description_placeholders={"learned": learned},
        )
```

- [ ] **Step 4: Update strings**

In **both** `strings.json` and `translations/en.json`, replace the entire `"options"` object with the block below, using the same 2-space JSON style:

```json
  "options": {
    "step": {
      "init": {
        "title": "Options",
        "menu_options": {
          "settings": "Settings",
          "vase": "Vase values (Wh per point)"
        }
      },
      "settings": {
        "title": "Settings",
        "data": {
          "target_soc": "Target state of charge (%)",
          "rearm_hysteresis": "Re-arm hysteresis (%)",
          "charging_power_threshold": "Charging power threshold (W)",
          "idle_close_minutes": "Close session after idle (minutes)",
          "max_session_hours": "Maximum session length (hours)",
          "soc_staleness_hours": "Treat a reading as stale after (hours)",
          "rest_minutes": "Wait before learning (minutes)"
        },
        "data_description": {
          "rest_minutes": "A battery reads high straight after charging. Readings taken sooner than this after the plug is cut are shown but not learned from."
        }
      },
      "vase": {
        "title": "Vase values",
        "description": "Wall watt-hours needed per reported percentage point, for each 10-point band. A typed value is that band's starting point; learning keeps refining it from remembered charges. Leave a band blank to use the default.\n\nLearned now: {learned}",
        "data": {
          "band_0": "0–10 %",
          "band_1": "10–20 %",
          "band_2": "20–30 %",
          "band_3": "30–40 %",
          "band_4": "40–50 %",
          "band_5": "50–60 %",
          "band_6": "60–70 %",
          "band_7": "70–80 %",
          "band_8": "80–90 %",
          "band_9": "90–100 %",
          "forget_charges": "Forget remembered charges"
        },
        "data_description": {
          "forget_charges": "Drops every remembered charge, so the bands fall back to their starting points. Use it if a charge taught something wrong."
        }
      }
    }
  },
```

Then confirm the two files are still identical:

```bash
cmp custom_components/ewheels_charge_limiter/strings.json custom_components/ewheels_charge_limiter/translations/en.json && echo identical
.venv/bin/python -c "import json;json.load(open('custom_components/ewheels_charge_limiter/strings.json'))"
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 6: Lint and commit**

```bash
.venv/bin/python -m ruff check custom_components tests
git add -A custom_components tests
git commit -m "feat: options menu with per-band vase values and forget

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: The record_charge action

**Files:**
- Modify: `custom_components/ewheels_charge_limiter/__init__.py`
- Modify: `custom_components/ewheels_charge_limiter/const.py`
- Create: `custom_components/ewheels_charge_limiter/services.yaml`
- Modify: `strings.json` and `translations/en.json`
- Test: `tests/test_entities.py`

**Interfaces:**
- Consumes: `async_record_charge()` from Task 2, which raises `ValueError`.
- Produces: the action `ewheels_charge_limiter.record_charge` with fields `config_entry_id`, `start_soc`, `end_soc` and `energy_wh`, and the constants `SERVICE_RECORD_CHARGE`, `ATTR_START_SOC`, `ATTR_END_SOC` and `ATTR_ENERGY_WH`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_entities.py`, add `from homeassistant.exceptions import ServiceValidationError`, then append:

```python
async def _record(hass: HomeAssistant, entry_id: str, start, end, energy) -> None:
    await hass.services.async_call(
        DOMAIN,
        "record_charge",
        {
            "config_entry_id": entry_id,
            "start_soc": start,
            "end_soc": end,
            "energy_wh": energy,
        },
        blocking=True,
    )


async def test_record_charge_remembers_and_refits(hass: HomeAssistant):
    entry = await _setup(hass)
    await _record(hass, entry.entry_id, 45, 97, 270.1)
    coordinator = entry.runtime_data
    assert coordinator.remembered_charges == 1
    assert coordinator.bands != pytest.approx([720 / 100 / 0.87] * 10)


async def test_record_charge_rejects_a_short_span(hass: HomeAssistant):
    entry = await _setup(hass)
    with pytest.raises(ServiceValidationError):
        await _record(hass, entry.entry_id, 50, 55, 40)


async def test_record_charge_rejects_an_unknown_entry(hass: HomeAssistant):
    await _setup(hass)
    with pytest.raises(ServiceValidationError):
        await _record(hass, "not-an-entry", 40, 80, 200)


async def test_record_charge_mid_session_can_cut_the_plug(hass: HomeAssistant):
    """A refit that lowers the requirement below what's delivered cuts now."""
    entry = await _setup(hass)
    hass.states.async_set(POWER, "120", {"unit_of_measurement": "W"})
    await hass.async_block_till_done()
    hass.states.async_set(ENERGY, "0.25", {"unit_of_measurement": "kWh"})
    await hass.async_block_till_done()
    assert hass.states.get("sensor.scooter_status").state == "charging"

    await _record(hass, entry.entry_id, 40, 80, 200)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.scooter_status").state == "complete"
    assert hass.states.get(PLUG).state == "off"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_entities.py -q`
Expected: `ServiceNotFound` for `ewheels_charge_limiter.record_charge`.

- [ ] **Step 3: Implement**

In `const.py`, append:

```python
# Actions
SERVICE_RECORD_CHARGE = "record_charge"
ATTR_START_SOC = "start_soc"
ATTR_END_SOC = "end_soc"
ATTR_ENERGY_WH = "energy_wh"
```

Replace `__init__.py` with:

```python
"""The E-Wheels Charge Limiter integration."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import ATTR_CONFIG_ENTRY_ID, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import (
    ATTR_END_SOC,
    ATTR_ENERGY_WH,
    ATTR_START_SOC,
    DEFAULT_REST_MINUTES,
    DOMAIN,
    OPT_REST_MINUTES,
    OPT_WH_PER_PERCENT,
    SERVICE_RECORD_CHARGE,
)
from .coordinator import ChargeLimiterCoordinator

PLATFORMS: list[Platform] = [Platform.NUMBER, Platform.SENSOR, Platform.SWITCH]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

_SOC = vol.All(vol.Coerce(float), vol.Range(min=0, max=100))

RECORD_CHARGE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Required(ATTR_START_SOC): _SOC,
        vol.Required(ATTR_END_SOC): _SOC,
        vol.Required(ATTR_ENERGY_WH): vol.All(
            vol.Coerce(float), vol.Range(min=0, min_included=False)
        ),
    }
)

type EWheelsConfigEntry = ConfigEntry[ChargeLimiterCoordinator]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the actions, once for all entries."""

    async def record_charge(call: ServiceCall) -> None:
        entry = hass.config_entries.async_get_entry(call.data[ATTR_CONFIG_ENTRY_ID])
        if (
            entry is None
            or entry.domain != DOMAIN
            or entry.state is not ConfigEntryState.LOADED
        ):
            raise ServiceValidationError(
                "That is not a loaded E-Wheels Charge Limiter entry"
            )
        try:
            await entry.runtime_data.async_record_charge(
                call.data[ATTR_START_SOC],
                call.data[ATTR_END_SOC],
                call.data[ATTR_ENERGY_WH],
            )
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err

    hass.services.async_register(
        DOMAIN, SERVICE_RECORD_CHARGE, record_charge, schema=RECORD_CHARGE_SCHEMA
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: EWheelsConfigEntry) -> bool:
    """Set up one configured plug."""
    coordinator = ChargeLimiterCoordinator(hass, entry)
    await coordinator.async_setup()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: EWheelsConfigEntry) -> bool:
    """Tear down."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_shutdown()
    return unloaded


async def async_migrate_entry(hass: HomeAssistant, entry: EWheelsConfigEntry) -> bool:
    """Upgrade an entry's options to the current version."""
    if entry.version == 1:
        # The single learned Wh-per-percent is superseded by the vase; the
        # stored learning migrates separately, with the coordinator's store.
        options = {k: v for k, v in entry.options.items() if k != OPT_WH_PER_PERCENT}
        options.setdefault(OPT_REST_MINUTES, DEFAULT_REST_MINUTES)
        hass.config_entries.async_update_entry(entry, options=options, version=2)
    return True
```

Create `services.yaml`:

```yaml
record_charge:
  fields:
    config_entry_id:
      required: true
      selector:
        config_entry:
          integration: ewheels_charge_limiter
    start_soc:
      required: true
      selector:
        number:
          min: 0
          max: 100
          step: 1
          unit_of_measurement: "%"
    end_soc:
      required: true
      selector:
        number:
          min: 0
          max: 100
          step: 1
          unit_of_measurement: "%"
    energy_wh:
      required: true
      selector:
        number:
          min: 0.1
          max: 20000
          step: 0.1
          unit_of_measurement: Wh
          mode: box
```

In **both** `strings.json` and `translations/en.json`, add a top-level `"services"` object after `"entity"`:

```json
  "services": {
    "record_charge": {
      "name": "Record a charge",
      "description": "Remember a charge for the vase fit: where it started, where it settled, and how many wall watt-hours it took. The bands are refitted straight away.",
      "fields": {
        "config_entry_id": {
          "name": "Charge limiter",
          "description": "Which configured plug the charge belongs to."
        },
        "start_soc": {
          "name": "Start",
          "description": "State of charge before charging, read with the pack at rest."
        },
        "end_soc": {
          "name": "End",
          "description": "State of charge after charging, once the pack has settled. At least 10 points above the start."
        },
        "energy_wh": {
          "name": "Energy",
          "description": "Wall watt-hours the charge took."
        }
      }
    }
  }
```

Remember the comma after the closing `}` of `"entity"`. Then run the same `cmp` and `json.load` check as in Task 6.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/python -m ruff check custom_components tests
git add -A custom_components tests
git commit -m "feat: record_charge action

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Documentation

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: nothing.
- Produces: nothing.

- [ ] **Step 1: Replace the Self-calibration section**

Replace everything from `## Self-calibration` up to, but not including, `## Fail toward charged` with:

```markdown
## Self-calibration: the vase

A pack's reported percent is not linear in energy. On the scooter this was
written for, a point near the top costs about three times the wall energy of
one in the middle. Picture a vase that is narrow at the bottom and wide at the
top: the same cup of water raises the level a lot low down and very little near
the brim.

So instead of one Wh-per-percent, the integration keeps **ten 10-point bands**,
each with its own wall watt-hours per reported point. The energy needed for a
charge is the sum across the bands it passes through, which is why it lands on
target whether it starts at 30 % or 75 %.

Each band starts from a **starting point**: a value you typed for it under
*Vase values*, or otherwise the default seeded from the configured capacity.
After every completed charge, the integration remembers where it started, where
it settled and how much energy it took, keeping the **last ten charges**. It
then refits all ten bands to explain them together, gently pulled toward the
starting points and toward neighbouring bands. Charges from different start
points are what reveal the vase's shape.

It only learns from a reading it can trust:

- **Real news only.** A value replayed after a reconnect or a restart is ignored.
- **Settled.** The pack reads high straight after charging, so readings sooner
  than *Wait before learning* (30 minutes by default) are shown but not learned
  from.
- **Enough signal.** A charge must rise at least 10 points.

A reading that fails the last two tests leaves the charge waiting for a better
one. A new charge starting throws away any charge still waiting.
```

- [ ] **Step 2: Update the entities and options tables, and document the action**

- In the entities table, replace the row `| \`sensor\` Wh per percent | The learned calibration |` with:
  `| \`sensor\` Wh per percent | Average wall Wh per point from the latest reading to the target; attributes list all ten bands, which are typed, and how many charges are remembered |`
- In the options table, replace the row `| Learned Wh per percent | learned; blank keeps learning |` with these two rows:
  - `| Wait before learning | 30 min |`
  - `| Vase values (per 10-point band) | blank: the default starting point |`
- Directly before `## Not included`, add:

```markdown
## Actions

`ewheels_charge_limiter.record_charge` remembers a charge measured some other
way: start %, settled end %, and wall watt-hours. It uses the same rules as an
automatic charge, so the end must be at least 10 points above the start. The
bands are refitted straight away. It is useful for seeding the vase from
charges you already know, or for putting back one whose learning was lost.
```

- [ ] **Step 3: Verify and commit**

```bash
grep -n "Wh per percent\|Wait before learning\|record_charge\|## Self-calibration" README.md
.venv/bin/python -m pytest -q && .venv/bin/python -m ruff check custom_components tests
git add README.md
git commit -m "docs: document the vase, its options, and record_charge

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Release v2026.10 and seed the live install

**Files:**
- Modify: `custom_components/ewheels_charge_limiter/manifest.json`

**Interfaces:**
- Consumes: the whole branch.
- Produces: the GitHub Release `v2026.10`, and the live bands seeded from three charges.

This task publishes to GitHub and changes the live Home Assistant. **Confirm with the user before Step 3.**

- [ ] **Step 1: Bump the version**

Change `"version": "2026.9"` to `"version": "2026.10"` in `manifest.json`.

- [ ] **Step 2: Verify and commit**

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check custom_components tests
git add custom_components/ewheels_charge_limiter/manifest.json
git commit -m "chore: release 2026.10

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 3: Merge, tag, push and publish (after the user confirms)**

```bash
git checkout main && git merge --ff-only feat/vase-model
git tag -a v2026.10 -m "v2026.10"
gh auth switch --user storebatfar
git push origin main && git push origin v2026.10
gh release create v2026.10 --verify-tag --title "v2026.10 — the vase: per-band learning and calibration fixes" --notes-file <release-notes>
gh run list --limit 4
git branch -d feat/vase-model
```

The release notes cover:
- the vase;
- the four fixes, in plain words;
- the new options and the action;
- a note that the upgrade migrates the learned value.

Match the style of the v2026.9 release.

- [ ] **Step 4: The user updates in HACS and restarts Home Assistant**

Wait for their confirmation.

- [ ] **Step 5: Verify the migration on the live install**

Read `sensor.lobehjul_christine_wh_per_percent`. Expected:
- attribute `default_prior` = 5.758;
- `bands` all equal to 5.758;
- `remembered_charges` = 0.

- [ ] **Step 6: Seed the three charges**

Find the entry ID with `ha_get_integration(query="ewheels_charge_limiter")`. Then call `ewheels_charge_limiter.record_charge` three times:
- `(88, 97, 82.8)`
- `(65, 96, 207.0)`
- `(45, 97, 270.1)`

Expected: `remembered_charges` = 3, and `bands` ≈ `[4.96, 4.80, 4.44, 3.83, 2.83, 2.39, 3.59, 5.56, 7.62, 9.37]`, matching the Task 1 reference fit.

- [ ] **Step 7: Update project memory**

Record the release, the live bands, and the new protocol in `project_charge-limiter-calibration.md`. The protocol: charge, wait 30 minutes, then read. The integration now enforces the wait.
