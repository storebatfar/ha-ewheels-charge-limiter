"""Binary sensor platform: whether the pack is already at its target."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
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
    """Set up the target-reached sensor."""
    coordinator: ChargeLimiterCoordinator = entry.runtime_data
    async_add_entities([TargetReachedSensor(coordinator)])


class TargetReachedSensor(ChargeLimiterEntity, BinarySensorEntity):
    """On when a fresh reading is at or above the target.

    The same test a new charge applies before refusing to start, so a
    dashboard can show that pressing start would do nothing - and offer a
    charge to full instead.
    """

    def __init__(self, coordinator: ChargeLimiterCoordinator) -> None:
        super().__init__(coordinator, "target_reached")

    @property
    def is_on(self) -> bool:
        return self.coordinator.target_reached
