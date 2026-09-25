"""The E-Wheels Charge Limiter integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import DEFAULT_REST_MINUTES, OPT_REST_MINUTES, OPT_WH_PER_PERCENT
from .coordinator import ChargeLimiterCoordinator

PLATFORMS: list[Platform] = [Platform.NUMBER, Platform.SENSOR, Platform.SWITCH]

type EWheelsConfigEntry = ConfigEntry[ChargeLimiterCoordinator]


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
