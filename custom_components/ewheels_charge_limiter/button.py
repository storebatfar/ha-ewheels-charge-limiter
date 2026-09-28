"""Button platform: charge to full, once."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
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
    """Set up the charge-to-full button."""
    coordinator: ChargeLimiterCoordinator = entry.runtime_data
    async_add_entities([ChargeToFullButton(coordinator)])


class ChargeToFullButton(ChargeLimiterEntity, ButtonEntity):
    """Switch the charger on for one charge that ignores the target."""

    def __init__(self, coordinator: ChargeLimiterCoordinator) -> None:
        super().__init__(coordinator, "charge_to_full")

    async def async_press(self) -> None:
        await self.coordinator.async_charge_to_full()
