"""Number platform: the target state of charge."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import ChargeLimiterCoordinator
from .entity import ChargeLimiterEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the target number."""
    coordinator: ChargeLimiterCoordinator = entry.runtime_data
    async_add_entities([TargetNumber(coordinator)])


class TargetNumber(ChargeLimiterEntity, NumberEntity):
    """Target state of charge, mirroring the option of the same name."""

    _attr_native_min_value = 50
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator: ChargeLimiterCoordinator) -> None:
        super().__init__(coordinator, "target")

    @property
    def native_value(self) -> float:
        return self.coordinator.target_soc

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_target(value)
