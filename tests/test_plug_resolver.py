"""Resolving plug entities from a device."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ewheels_charge_limiter.plug_resolver import (
    resolve_plug_entities,
)


async def _make_device(
    hass: HomeAssistant, entities: list[tuple[str, str, str | None]]
) -> str:
    """Create a device with (platform, object_id, device_class) entities."""
    entry = MockConfigEntry(domain="demo")
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("demo", "plug-1")},
    )
    registry = er.async_get(hass)
    for platform, object_id, device_class in entities:
        registry.async_get_or_create(
            platform,
            "demo",
            object_id,
            device_id=device.id,
            original_device_class=device_class,
        )
    return device.id


async def test_resolves_a_well_behaved_plug(hass: HomeAssistant):
    device_id = await _make_device(
        hass,
        [
            (Platform.SWITCH, "plug", None),
            (Platform.SENSOR, "power", SensorDeviceClass.POWER),
            (Platform.SENSOR, "energy", SensorDeviceClass.ENERGY),
        ],
    )
    result = resolve_plug_entities(hass, device_id)
    assert result.switch == "switch.demo_plug"
    assert result.power == "sensor.demo_power"
    assert result.energy == "sensor.demo_energy"
    assert result.is_complete


async def test_power_only_plug_is_complete(hass: HomeAssistant):
    device_id = await _make_device(
        hass,
        [
            (Platform.SWITCH, "plug", None),
            (Platform.SENSOR, "power", SensorDeviceClass.POWER),
        ],
    )
    result = resolve_plug_entities(hass, device_id)
    assert result.energy is None
    assert result.is_complete


async def test_two_power_sensors_are_ambiguous(hass: HomeAssistant):
    device_id = await _make_device(
        hass,
        [
            (Platform.SWITCH, "plug", None),
            (Platform.SENSOR, "power_a", SensorDeviceClass.POWER),
            (Platform.SENSOR, "power_b", SensorDeviceClass.POWER),
        ],
    )
    result = resolve_plug_entities(hass, device_id)
    assert result.power is None
    assert result.power_ambiguous
    assert not result.is_complete


async def test_switchless_energy_monitor_is_incomplete(hass: HomeAssistant):
    device_id = await _make_device(
        hass, [(Platform.SENSOR, "power", SensorDeviceClass.POWER)]
    )
    result = resolve_plug_entities(hass, device_id)
    assert result.switch is None
    assert not result.is_complete


async def test_switch_only_plug_is_incomplete(hass: HomeAssistant):
    device_id = await _make_device(hass, [(Platform.SWITCH, "plug", None)])
    result = resolve_plug_entities(hass, device_id)
    assert result.switch == "switch.demo_plug"
    assert not result.is_complete


async def test_unknown_device_resolves_to_nothing(hass: HomeAssistant):
    result = resolve_plug_entities(hass, "does-not-exist")
    assert not result.is_complete
    assert result.switch is None
