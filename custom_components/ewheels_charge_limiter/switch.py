"""Switch platform: the master enable and the manual plug control."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import ChargeLimiterCoordinator
from .entity import ChargeLimiterEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the switches."""
    coordinator: ChargeLimiterCoordinator = entry.runtime_data
    async_add_entities([EnabledSwitch(coordinator), PlugSwitch(coordinator)])


class EnabledSwitch(ChargeLimiterEntity, SwitchEntity):
    """Master enable. Turning off stops control and leaves the plug as-is."""

    def __init__(self, coordinator: ChargeLimiterCoordinator) -> None:
        super().__init__(coordinator, "enabled")

    @property
    def is_on(self) -> bool:
        return self.coordinator.enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_enabled(False)


class PlugSwitch(ChargeLimiterEntity, SwitchEntity):
    """Manual on/off for the plug, mirroring its real state.

    Turning this on starts a session regardless of the current charge, which
    deliberately bypasses the target. "Turn the plug on" should mean exactly
    that, otherwise the control feels broken. Nothing else energises the plug:
    the limiter only ever cuts.
    """

    def __init__(self, coordinator: ChargeLimiterCoordinator) -> None:
        super().__init__(coordinator, "plug")

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.plug_is_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_plug(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_plug(False)
