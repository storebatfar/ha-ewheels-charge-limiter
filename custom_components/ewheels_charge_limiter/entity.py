"""Shared base entity."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .coordinator import ChargeLimiterCoordinator


class ChargeLimiterEntity(Entity):
    """Base for every entity in this integration.

    Holds no logic: entities are views over the coordinator, pushed to on
    change rather than polled.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, coordinator: ChargeLimiterCoordinator, key: str) -> None:
        self.coordinator = coordinator
        self._attr_translation_key = key
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.entry_id)},
            name=coordinator.entry.title,
            manufacturer="storebatfar",
            model="Charge Limiter",
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to coordinator pushes."""
        self.async_on_remove(self.coordinator.add_listener(self.async_write_ha_state))
