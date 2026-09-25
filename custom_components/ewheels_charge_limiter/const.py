"""Constants for the E-Wheels Charge Limiter integration."""

from __future__ import annotations

from enum import StrEnum

DOMAIN = "ewheels_charge_limiter"

# Config entry keys
CONF_PLUG_SWITCH = "plug_switch"
CONF_POWER_ENTITY = "power_entity"
CONF_ENERGY_ENTITY = "energy_entity"
CONF_SOC_ENTITY = "soc_entity"
CONF_CAPACITY_WH = "capacity_wh"

# Config-flow only; never stored on the entry. Lets the user accept a meter
# that lives on a different device from the switch.
CONF_ALLOW_FOREIGN_METER = "allow_foreign_meter"

# Option keys
OPT_TARGET_SOC = "target_soc"
OPT_REARM_HYSTERESIS = "rearm_hysteresis"
# Retired in 2026.10 (superseded by the vase); read only by entry migration.
OPT_WH_PER_PERCENT = "wh_per_percent"
OPT_CHARGING_POWER_THRESHOLD = "charging_power_threshold"
OPT_IDLE_CLOSE_MINUTES = "idle_close_minutes"
OPT_MAX_SESSION_HOURS = "max_session_hours"
OPT_SOC_STALENESS_HOURS = "soc_staleness_hours"
OPT_REST_MINUTES = "rest_minutes"

# Options-flow only; never stored. Drops every remembered charge.
OPT_FORGET_CHARGES = "forget_charges"

# Defaults
DEFAULT_TARGET_SOC = 80.0
DEFAULT_REARM_HYSTERESIS = 5.0
DEFAULT_CHARGING_POWER_THRESHOLD = 5.0
DEFAULT_IDLE_CLOSE_MINUTES = 10
DEFAULT_MAX_SESSION_HOURS = 8
DEFAULT_SOC_STALENESS_HOURS = 12
DEFAULT_REST_MINUTES = 30
DEFAULT_CHARGER_EFFICIENCY = 0.87

# Calibration tuning
CALIBRATION_MIN_DELTA_PCT = 10.0

# The vase: per-band energy model
BAND_CLAMP_LOW = 0.25
BAND_CLAMP_HIGH = 3.0
FIT_PRIOR_WEIGHT = 1.0
FIT_SMOOTHING_WEIGHT = 5.0
BAND_OPTION_KEYS = tuple(f"band_{i}" for i in range(10))
MAX_REMEMBERED_CHARGES = 10

STORAGE_VERSION = 2


class ChargeState(StrEnum):
    """States of the charge limiter."""

    IDLE = "idle"
    ARMED = "armed"
    CHARGING = "charging"
    COMPLETE = "complete"
    STOPPED = "stopped"
    UNCALIBRATED = "uncalibrated"
    STALLED = "stalled"
