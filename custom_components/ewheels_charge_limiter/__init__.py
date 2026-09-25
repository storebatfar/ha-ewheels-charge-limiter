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
