"""Config and options flow."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ewheels_charge_limiter.const import (
    CONF_ALLOW_FOREIGN_METER,
    CONF_CAPACITY_WH,
    CONF_ENERGY_ENTITY,
    CONF_PLUG_SWITCH,
    CONF_POWER_ENTITY,
    CONF_SOC_ENTITY,
    DEFAULT_TARGET_SOC,
    DOMAIN,
    OPT_TARGET_SOC,
)

PLUG = "switch.plug"
POWER = "sensor.plug_power"
ENERGY = "sensor.plug_energy"
SOC = "sensor.scooter_battery"


def _seed_states(hass: HomeAssistant) -> None:
    hass.states.async_set(PLUG, "on")
    hass.states.async_set(POWER, "0", {"device_class": "power"})
    hass.states.async_set(ENERGY, "0", {"device_class": "energy"})
    hass.states.async_set(SOC, "55", {"device_class": "battery"})


async def test_happy_path_creates_an_entry(hass: HomeAssistant):
    _seed_states(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "name": "Scooter",
            CONF_PLUG_SWITCH: PLUG,
            CONF_POWER_ENTITY: POWER,
            CONF_ENERGY_ENTITY: ENERGY,
            CONF_SOC_ENTITY: SOC,
            CONF_CAPACITY_WH: 720,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Scooter"
    assert result["data"][CONF_PLUG_SWITCH] == PLUG
    assert result["options"][OPT_TARGET_SOC] == DEFAULT_TARGET_SOC


async def test_an_energy_only_plug_is_accepted(hass: HomeAssistant):
    """Either meter alone is enough; both is merely better."""
    _seed_states(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "name": "Scooter",
            CONF_PLUG_SWITCH: PLUG,
            CONF_ENERGY_ENTITY: ENERGY,
            CONF_SOC_ENTITY: SOC,
            CONF_CAPACITY_WH: 720,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"].get(CONF_POWER_ENTITY) is None


async def test_a_plug_with_no_meter_is_rejected(hass: HomeAssistant):
    _seed_states(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "name": "Scooter",
            CONF_PLUG_SWITCH: PLUG,
            CONF_SOC_ENTITY: SOC,
            CONF_CAPACITY_WH: 720,
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "no_meter"}


async def test_the_same_switch_cannot_be_configured_twice(hass: HomeAssistant):
    _seed_states(hass)
    MockConfigEntry(
        domain=DOMAIN, unique_id=PLUG, data={CONF_PLUG_SWITCH: PLUG}
    ).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "name": "Scooter",
            CONF_PLUG_SWITCH: PLUG,
            CONF_POWER_ENTITY: POWER,
            CONF_SOC_ENTITY: SOC,
            CONF_CAPACITY_WH: 720,
        },
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_options_flow_updates_the_target(hass: HomeAssistant):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=PLUG,
        data={CONF_PLUG_SWITCH: PLUG},
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


async def _on_device(
    hass: HomeAssistant,
    device_key: str,
    platform: str,
    object_id: str,
    device_class: str | None = None,
) -> str:
    """Create an entity attached to a device and return its entity_id."""
    config_entry = MockConfigEntry(domain="demo")
    config_entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=config_entry.entry_id, identifiers={("demo", device_key)}
    )
    registered = er.async_get(hass).async_get_or_create(
        platform,
        "demo",
        object_id,
        device_id=device.id,
        original_device_class=device_class,
    )
    return registered.entity_id


async def test_a_meter_on_another_device_is_flagged(hass: HomeAssistant):
    """Counting a different device's watt-hours would cut at a random point."""
    switch = await _on_device(hass, "plug", Platform.SWITCH, "plug")
    foreign = await _on_device(
        hass, "other", Platform.SENSOR, "other_power", SensorDeviceClass.POWER
    )
    hass.states.async_set(SOC, "55", {"device_class": "battery"})

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "name": "Scooter",
            CONF_PLUG_SWITCH: switch,
            CONF_POWER_ENTITY: foreign,
            CONF_SOC_ENTITY: SOC,
            CONF_CAPACITY_WH: 720,
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "meter_not_on_plug"}


async def test_the_override_accepts_a_foreign_meter(hass: HomeAssistant):
    """A separate clamp meter is legitimate, so the warning is not a block."""
    switch = await _on_device(hass, "plug", Platform.SWITCH, "plug")
    foreign = await _on_device(
        hass, "other", Platform.SENSOR, "other_power", SensorDeviceClass.POWER
    )
    hass.states.async_set(SOC, "55", {"device_class": "battery"})

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    payload = {
        "name": "Scooter",
        CONF_PLUG_SWITCH: switch,
        CONF_POWER_ENTITY: foreign,
        CONF_SOC_ENTITY: SOC,
        CONF_CAPACITY_WH: 720,
    }
    result = await hass.config_entries.flow.async_configure(result["flow_id"], payload)
    assert result["errors"] == {"base": "meter_not_on_plug"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**payload, CONF_ALLOW_FOREIGN_METER: True}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    # The override is a flow-time decision, not configuration.
    assert CONF_ALLOW_FOREIGN_METER not in result["data"]


async def test_a_meter_on_the_same_device_is_accepted(hass: HomeAssistant):
    switch = await _on_device(hass, "plug", Platform.SWITCH, "plug")
    power = await _on_device(
        hass, "plug", Platform.SENSOR, "plug_watts", SensorDeviceClass.POWER
    )
    hass.states.async_set(SOC, "55", {"device_class": "battery"})

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "name": "Scooter",
            CONF_PLUG_SWITCH: switch,
            CONF_POWER_ENTITY: power,
            CONF_SOC_ENTITY: SOC,
            CONF_CAPACITY_WH: 720,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
