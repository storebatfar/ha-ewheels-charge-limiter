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

# Option keys
OPT_TARGET_SOC = "target_soc"
OPT_REARM_HYSTERESIS = "rearm_hysteresis"
OPT_WH_PER_PERCENT = "wh_per_percent"
OPT_CHARGING_POWER_THRESHOLD = "charging_power_threshold"
OPT_IDLE_CLOSE_MINUTES = "idle_close_minutes"
OPT_MAX_SESSION_HOURS = "max_session_hours"
OPT_SOC_STALENESS_HOURS = "soc_staleness_hours"

# Defaults
DEFAULT_TARGET_SOC = 80.0
DEFAULT_REARM_HYSTERESIS = 5.0
DEFAULT_CHARGING_POWER_THRESHOLD = 5.0
DEFAULT_IDLE_CLOSE_MINUTES = 10
DEFAULT_MAX_SESSION_HOURS = 8
DEFAULT_SOC_STALENESS_HOURS = 12
DEFAULT_CHARGER_EFFICIENCY = 0.87

# Calibration tuning
CALIBRATION_EMA_WEIGHT = 0.3
CALIBRATION_MIN_DELTA_PCT = 10.0
CALIBRATION_CLAMP_LOW = 0.5
CALIBRATION_CLAMP_HIGH = 2.0

STORAGE_VERSION = 1


class ChargeState(StrEnum):
    """States of the charge limiter."""

    IDLE = "idle"
    ARMED = "armed"
    CHARGING = "charging"
    COMPLETE = "complete"
    STOPPED = "stopped"
    UNCALIBRATED = "uncalibrated"
    STALLED = "stalled"
