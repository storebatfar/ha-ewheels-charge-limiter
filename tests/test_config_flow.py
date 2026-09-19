"""Config and options flow."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ewheels_charge_limiter.const import (
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
