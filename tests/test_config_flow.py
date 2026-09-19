"""Config and options flow."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ewheels_charge_limiter.const import (
    CONF_CAPACITY_WH,
    CONF_ENERGY_ENTITY,
    CONF_PLUG_DEVICE,
    CONF_PLUG_SWITCH,
    CONF_POWER_ENTITY,
    CONF_SOC_ENTITY,
    DEFAULT_TARGET_SOC,
    DOMAIN,
    OPT_TARGET_SOC,
)


async def _make_plug(hass: HomeAssistant, sensors: list[tuple[str, str]]) -> str:
    entry = MockConfigEntry(domain="demo")
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("demo", "plug-1")}
    )
    registry = er.async_get(hass)
    registry.async_get_or_create(Platform.SWITCH, "demo", "plug", device_id=device.id)
    for object_id, device_class in sensors:
        registry.async_get_or_create(
            Platform.SENSOR,
            "demo",
            object_id,
            device_id=device.id,
            original_device_class=device_class,
        )
    return device.id


async def test_happy_path_creates_an_entry(hass: HomeAssistant):
    device_id = await _make_plug(
        hass,
        [("power", SensorDeviceClass.POWER), ("energy", SensorDeviceClass.ENERGY)],
    )
    hass.states.async_set("sensor.scooter_battery", "55", {"device_class": "battery"})

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "name": "Scooter",
            CONF_PLUG_DEVICE: device_id,
            CONF_SOC_ENTITY: "sensor.scooter_battery",
            CONF_CAPACITY_WH: 720,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Scooter"
    assert result["data"][CONF_PLUG_SWITCH] == "switch.demo_plug"
    assert result["data"][CONF_POWER_ENTITY] == "sensor.demo_power"
    assert result["data"][CONF_ENERGY_ENTITY] == "sensor.demo_energy"
    assert result["options"][OPT_TARGET_SOC] == DEFAULT_TARGET_SOC


async def test_ambiguous_plug_falls_through_to_the_entities_step(hass: HomeAssistant):
    device_id = await _make_plug(
        hass,
        [("power_a", SensorDeviceClass.POWER), ("power_b", SensorDeviceClass.POWER)],
    )
    hass.states.async_set("sensor.scooter_battery", "55", {"device_class": "battery"})

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "name": "Scooter",
            CONF_PLUG_DEVICE: device_id,
            CONF_SOC_ENTITY: "sensor.scooter_battery",
            CONF_CAPACITY_WH: 720,
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "entities"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_PLUG_SWITCH: "switch.demo_plug",
            CONF_POWER_ENTITY: "sensor.demo_power_a",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_POWER_ENTITY] == "sensor.demo_power_a"


async def test_entities_step_rejects_a_plug_with_no_meter(hass: HomeAssistant):
    device_id = await _make_plug(hass, [])
    hass.states.async_set("sensor.scooter_battery", "55", {"device_class": "battery"})

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "name": "Scooter",
            CONF_PLUG_DEVICE: device_id,
            CONF_SOC_ENTITY: "sensor.scooter_battery",
            CONF_CAPACITY_WH: 720,
        },
    )
    assert result["step_id"] == "entities"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PLUG_SWITCH: "switch.demo_plug"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "no_meter"}


async def test_the_same_plug_cannot_be_configured_twice(hass: HomeAssistant):
    device_id = await _make_plug(hass, [("power", SensorDeviceClass.POWER)])
    hass.states.async_set("sensor.scooter_battery", "55", {"device_class": "battery"})

    MockConfigEntry(
        domain=DOMAIN, unique_id=device_id, data={CONF_PLUG_DEVICE: device_id}
    ).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "name": "Scooter",
            CONF_PLUG_DEVICE: device_id,
            CONF_SOC_ENTITY: "sensor.scooter_battery",
            CONF_CAPACITY_WH: 720,
        },
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_options_flow_updates_the_target(hass: HomeAssistant):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="plug-1",
        data={CONF_PLUG_DEVICE: "plug-1"},
        options={OPT_TARGET_SOC: 80.0},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {OPT_TARGET_SOC: 90.0}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][OPT_TARGET_SOC] == 90.0
