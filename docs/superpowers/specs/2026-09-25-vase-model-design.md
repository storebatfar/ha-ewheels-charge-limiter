# Per-band energy model ("the vase") and calibration fixes — design

Release: **v2026.10**. Status: approved in conversation 2026-09-25, awaiting spec review.

## Why

Three real charges on the E2S V3 GT Pro, from three start points, all landed at a
reported 96–97 % against targets of 90–98:

| Start | End (settled) | Wall Wh | Wh per reported point |
|---|---|---|---|
| 88 | 97 | 82.8 | 8.3 |
| 65 | 96 | 207.0 | 6.5 |
| 45 | 97 | 270.1 | 5.2 |

Metering is sound (the plug's own statistics integrate to 270.2 Wh against the
integration's 270.1) and the cut is always exact against the computed
requirement. The fault is the model: one scalar Wh-per-percent cannot describe a
pack whose reported percent costs roughly 3× more energy near the top than in the
middle. With one scalar, the right value depends on where the charge starts, and
the start changes day to day.

Four calibration bugs were found at the same time. They are fixed in the same
release because they all live in the code path this design rewrites.

## Goal and success criteria

Land on the target from any start point.

- After a few sessions, landing error within about ±2 reported points for starts
  from 30 to 80 and targets from 80 to 95.
- No session's learning is silently lost or corrupted by a stale, early, or
  mismatched reading.
- Upgrading needs no action from the user and loses nothing.

Unchanged constraint: the scooter stops reporting while charging, so the
integration stays open-loop. It counts energy at the plug rather than watching
the state of charge.

## Out of scope

- ev_smart_charging configuration and the SoC helper sensor for it.
- An "estimated charge time" sensor.
- Live closed-loop control.

---

## 1. The model

**Bands.** Ten values `b[0..9]`. `b[i]` is the wall Wh needed per reported
percentage point within the band `[10i, 10i + 10)`. Band 9 covers `[90, 100]`.

**Energy between two points.** For `a ≤ t`:

```
energy(a, t) = Σ_i b[i] × overlap([a, t], [10i, 10i + 10))
```

`energy(a, t) = 0` when `t ≤ a`.

**Inverse.** `soc_after(a, wh)` walks bands upward from `a`, consuming `wh`, and
returns the point reached, capped at 100. It is used for Projected charge.

**Starting points (priors).** Each band has a prior `p[i]`:
- the user's typed value for that band, if one is set;
- otherwise the *default prior*: the value carried over at upgrade (see §4), or
  on a fresh install the capacity seed `capacity_wh / 100 / 0.87`.

**Remembered charges.** Up to **10** charges, each
`{start_soc, end_soc, energy_wh, recorded_at, source}` with `source` one of
`auto` or `manual`. A new charge beyond 10 drops the oldest. A charge is only
remembered if `end_soc − start_soc ≥ 10` (`CALIBRATION_MIN_DELTA_PCT`, unchanged).

**Fit.** Whenever the remembered charges or priors change, the bands are
recomputed by minimising

```
Σ_s (A_s · b − E_s)²  +  λp Σ_i (b[i] − p[i])²  +  λs Σ_{i<9} (b[i] − b[i+1])²
```

where `A_s[i] = overlap([start_s, end_s], band i)` and `E_s` is the charge's
energy. This is solved in closed form from the normal equations

```
(AᵀA + λp·I + λs·L) b = Aᵀy + λp·p
```

with `L` the path-graph Laplacian over the ten bands. The system is symmetric
positive definite whenever `λp > 0`, so it always has a unique solution. It is
solved by Gaussian elimination with partial pivoting in plain Python, with no
new dependencies.

- `λp = 1.0` (`FIT_PRIOR_WEIGHT`) — weak, so priors only dominate where no
  remembered charge covers a band.
- `λs = 5.0` (`FIT_SMOOTHING_WEIGHT`) — chosen by simulation on the three real
  charges. At 5 the fit reproduces all three within 2 %; at 25 the top band is
  underfitted by 7 %.
- With no remembered charges, the fit returns the priors plus smoothing.

After solving, each band is clamped to `[0.25, 3.0] × capacity seed`
(`BAND_CLAMP_LOW`, `BAND_CLAMP_HIGH`). The previous `[0.5, 2.0]` clamp is
removed: this pack's middle bands measure about 2.5 Wh/pt, below the old floor.

**Reference fit** of the three charges above, with default prior 5.758:

```
band:   0-10 10-20 20-30 30-40 40-50 50-60 60-70 70-80 80-90 90-100
Wh/pt:   5.0   4.8   4.4   3.8   2.8   2.4   3.6   5.6   7.6   9.4
```

**Removed:** the single learned `wh_per_percent`, `CALIBRATION_EMA_WEIGHT`, and
`calibration.py`. The seed function moves to `vase.py`.

## 2. Using the model in a session

- **Session open:** `required_wh = energy(start_soc, target)`. If
  `start_soc ≥ target` the session completes without energising, as today.
- **Target change mid-session:** recompute `required_wh` the same way, then
  check against delivered energy, as today.
- **Refit mid-session:** saving either options step, the action, or forgetting
  charges while a session is open recomputes `required_wh` from the new bands,
  through the same path as a target change. That means it can cut immediately if
  the new bands say the target is already reached.
- **Projected charge:** `soc_after(session_start_soc, delivered_wh)`.

## 3. Accepting a reading after a charge

When a session completes, the *pending note* becomes
`{start_soc, delivered_wh, cut_at}`, where `cut_at` is the UTC timestamp of the
cut.

A state-of-charge event after the cut is classified as follows. The event's
**old state** is now inspected, so the SoC event path receives the whole event,
not just the new value.

| Classification | Condition |
|---|---|
| Stale | old state was `unavailable`, `unknown`, or missing |
| Early | real, but `now − cut_at < rest_minutes` |
| Settled | real, and `now − cut_at ≥ rest_minutes` |

`rest_minutes` is a new option, **Wait before learning (minutes)**, default 30.

| Reading | Projected charge | Pending note | Learns |
|---|---|---|---|
| Stale | kept | kept | no |
| Early | cleared | kept | no |
| Settled, `end − start ≥ 10` | cleared | consumed and removed | yes: the charge is remembered, then the model is refitted |
| Settled, `end − start < 10` | cleared | **kept** | no |

"Projected charge cleared" means `session_start_soc` is set to `None`, so the
sensor reads unknown and a real reading takes over.

**The pending note is also removed** when a new session opens (bug 4), and when a
session is stopped by hand (as today).

The existing re-arm check on a low reading is unchanged, and runs after the
classification above.

Bugs fixed by this section:

1. A stale re-send (`unavailable → last value`, as happens when the ESPHome node
   reconnects or HA restarts) consumed the pending note, and a rejected reading
   also discarded it.
2. No rest guard: the first reading after the cut was used however soon it came,
   while the pack was still surface-charged.
3. Projected charge outlived a real reading, and was even recomputed with newly
   learned values.
4. A new session did not clear an older pending note, so a later reading could
   pair the old note with a different charge.

## 4. Storage and upgrade

**Store format 1 → 2.**

Format 2 contains the existing session fields, plus:
- `default_prior` — float;
- `charges` — the list of remembered charges (§1);
- `bands` — the last fitted ten values (a cache; it can always be recomputed);
- `pending_calibration` — now includes `cut_at`.

Migration from format 1:
- `default_prior = stored wh_per_percent` (5.758 on the live install); if absent,
  the capacity seed.
- `charges = []`.
- A pending calibration without `cut_at` gets `cut_at = 0`. That treats it as
  long settled, so the first real reading after upgrade can use it.
- An in-flight session resumes exactly as today.

**Config entry options.** Config entry version 1 → 2:
- `wh_per_percent` is removed. It was already superseded by learning.
- `rest_minutes` is added, default 30.
- Typed band values are stored as `band_0` … `band_9`. They are absent when not
  typed.

## 5. What the user sees

**Options flow** becomes a menu with two entries.

1. **Settings** — the existing fields minus "Learned Wh per percent", plus
   "Wait before learning (minutes)".
2. **Vase values** — ten optional number fields labelled `0–10 %` … `90–100 %`.
   - A blank field means not typed.
   - Current learned values are shown in the step description only. They are
     **not** pre-filled into the fields, because saving would then silently turn
     every learned value into a typed one.
   - A **Forget remembered charges** checkbox. It is acted on during the flow and
     never stored.

Saving either step refits immediately, with no reload. The existing in-place
options listener is kept.

**Entities.**
- **Wh per percent** keeps its unique ID and name. Its state is
  `energy(latest_soc, target) / (target − latest_soc)`, i.e. the average cost per
  point of the next charge. When the latest reading is unavailable or already at
  or above target, the state is the value of the band containing the target.
  Attributes:
  - `bands` — ten values, rounded to 3 decimals;
  - `typed_bands` — a list of band indices;
  - `remembered_charges` — an integer;
  - `default_prior`.
- **Projected charge** uses `soc_after` (§2) and is cleared per §3.
- All other entities are unchanged.

**Action `ewheels_charge_limiter.record_charge`.**

| Field | Type |
|---|---|
| `config_entry_id` | config-entry selector for this integration |
| `start_soc` | 0–100 |
| `end_soc` | 0–100 |
| `energy_wh` | greater than 0 |

It validates with the same ≥10-point rule and raises `ServiceValidationError` on
failure. On success it remembers the charge with `source: manual` and refits.

After release it is called once with the three charges in the table under *Why*.

## 6. Code layout

- **`vase.py` (new)** — pure functions with no HA imports: `seed_wh_per_percent`,
  `overlap`, `energy_between`, `soc_after`, `fit_bands`. Unit-tested on its own.
- **`calibration.py`** — deleted.
- **`coordinator.py`** — state machine only; uses `vase.py`. SoC handling receives
  the full event.
- **`__init__.py`** — registers the action; handles config entry migration.
- **`services.yaml` (new)**, **`strings.json`**, **`translations/en.json`** — the
  action, the menu, the band fields, and the new option. The misleading "leave
  blank to keep learning" label goes away with the field.
- **`README.md`** — the Self-calibration section is rewritten around the vase,
  and the options, entities, and action are documented.

## 7. Testing

Test-first: every behaviour gets a failing test before code.

- **`vase.py`:**
  - partial-band overlap;
  - `energy_between` across band edges;
  - `soc_after` as the inverse of `energy_between`, capped at 100;
  - the fit reproduces the three real charges within 2 %;
  - uncovered bands stay close to prior and smooth;
  - clamps apply;
  - no charges returns the priors.
- **Coordinator:**
  - required energy and projection come from the bands;
  - a target change recomputes from the bands;
  - each bug has a reproducing test — stale re-send keeps the note; an early
    reading clears the projection and keeps the note; a settled reading learns;
    a small delta keeps the note; a new session clears the note;
  - history caps at 10, dropping the oldest.
- **Migration:** a format-1 store carrying `wh_per_percent: 5.758` loads with
  default prior 5.758, no charges, and a resumed session.
- **Options flow:**
  - the menu;
  - the settings step;
  - the vase step — blank versus typed values, and forget clears the charges;
  - saving refits without a reload.
- **Action:** validation errors; success remembers the charge and refits.
- The existing 69 tests stay green, changed only where they asserted the scalar.

## 8. Release

1. Bump `manifest.json` to `2026.10`. The house scheme is `vYYYY.N`, counting up;
   the previous release's jump from .3 to .9 was a mistake, and versions cannot
   go backwards.
2. Tag `v2026.10`, push, and create the GitHub Release by hand, since HACS reads
   Releases rather than tags.
3. The user updates in HACS and restarts.
4. Import the three charges with `record_charge`.

## Risks

- **Bands below 40 are extrapolated.** No real charge has started there yet, so
  their values come from smoothing against neighbours until charges cover them.
- **40–60 fits very low** (about 2.5 Wh/pt). If the 45 % start reading was
  depressed by a ride shortly before, part of that is an artefact. Later charges
  outweigh it, and "Forget remembered charges" is the escape hatch.
- **The reported percent is itself only whole numbers.** Each charge carries
  about ±1 point of noise at each end. Remembering 10 charges averages this out.
