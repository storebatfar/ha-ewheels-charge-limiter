"""Sensor platform."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import ChargeState
from .coordinator import ChargeLimiterCoordinator
from .entity import ChargeLimiterEntity


@dataclass(frozen=True, kw_only=True)
class ChargeLimiterSensorDescription(SensorEntityDescription):
    """Describes one sensor."""

    value_fn: Callable[[ChargeLimiterCoordinator], float | str | None]


SENSORS: tuple[ChargeLimiterSensorDescription, ...] = (
    ChargeLimiterSensorDescription(
        key="status",
        translation_key="status",
        device_class=SensorDeviceClass.ENUM,
        options=[state.value for state in ChargeState],
        value_fn=lambda c: str(c.state),
    ),
    ChargeLimiterSensorDescription(
        key="session_energy",
        translation_key="session_energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda c: round(c.session_delivered_wh, 1),
    ),
    ChargeLimiterSensorDescription(
        key="projected_soc",
        translation_key="projected_soc",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        value_fn=lambda c: (
            None if c.projected_soc is None else round(c.projected_soc, 1)
        ),
    ),
    ChargeLimiterSensorDescription(
        key="wh_per_percent",
        translation_key="wh_per_percent",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda c: round(c.wh_per_percent, 3),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensors."""
    coordinator: ChargeLimiterCoordinator = entry.runtime_data
    async_add_entities(
        ChargeLimiterSensor(coordinator, description) for description in SENSORS
    )


class ChargeLimiterSensor(ChargeLimiterEntity, SensorEntity):
    """A read-only view of one coordinator value."""

    entity_description: ChargeLimiterSensorDescription

    def __init__(
        self,
        coordinator: ChargeLimiterCoordinator,
        description: ChargeLimiterSensorDescription,
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | str | None:
        return self.entity_description.value_fn(self.coordinator)
