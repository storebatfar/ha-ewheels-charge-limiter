"""Reconfiguring an entry: swapping the plug, the meters, or the battery."""

from __future__ import annotations

import pytest
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
    CONF_FORGET_LEARNING,
    CONF_PLUG_SWITCH,
    CONF_POWER_ENTITY,
    CONF_SOC_ENTITY,
    DOMAIN,
    OPT_TARGET_SOC,
    ChargeState,
)

PLUG = "switch.plug"
POWER = "sensor.plug_power"
ENERGY = "sensor.plug_energy"
SOC = "sensor.scooter_battery"

NEW_PLUG = "switch.shelly"
NEW_POWER = "sensor.shelly_power"
NEW_ENERGY = "sensor.shelly_energy"

STORAGE_KEY = f"{DOMAIN}.limiter-1"


def _seed_states(hass: HomeAssistant) -> None:
    for plug, power, energy in ((PLUG, POWER, ENERGY), (NEW_PLUG, NEW_POWER, NEW_ENERGY)):
        hass.states.async_set(plug, "off")
        hass.states.async_set(power, "0", {"unit_of_measurement": "W"})
        hass.states.async_set(energy, "0", {"unit_of_measurement": "kWh"})
    hass.states.async_set(SOC, "40", {"unit_of_measurement": "%"})


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="limiter-1",
        unique_id=PLUG,
        title="Scooter",
        version=2,
        data={
            CONF_PLUG_SWITCH: PLUG,
            CONF_POWER_ENTITY: POWER,
            CONF_ENERGY_ENTITY: ENERGY,
            CONF_SOC_ENTITY: SOC,
            CONF_CAPACITY_WH: 720,
        },
        options={OPT_TARGET_SOC: 90.0},
    )
    entry.add_to_hass(hass)
    return entry


async def _loaded_entry(hass: HomeAssistant) -> MockConfigEntry:
    _seed_states(hass)
    entry = _entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _shelly(**overrides) -> dict:
    return {
        CONF_PLUG_SWITCH: NEW_PLUG,
        CONF_POWER_ENTITY: NEW_POWER,
        CONF_ENERGY_ENTITY: NEW_ENERGY,
        CONF_SOC_ENTITY: SOC,
        CONF_CAPACITY_WH: 720,
        **overrides,
    }


def _suggested(result, key: str):
    for marker in result["data_schema"].schema:
        if marker == key:
            return (marker.description or {}).get("suggested_value")
    raise AssertionError(f"{key} is not on the form")


async def test_the_form_is_prefilled_with_the_current_setup(hass: HomeAssistant):
    entry = await _loaded_entry(hass)

    result = await entry.start_reconfigure_flow(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    assert _suggested(result, CONF_PLUG_SWITCH) == PLUG
    assert _suggested(result, CONF_POWER_ENTITY) == POWER
    assert _suggested(result, CONF_ENERGY_ENTITY) == ENERGY
    assert _suggested(result, CONF_SOC_ENTITY) == SOC
    assert _suggested(result, CONF_CAPACITY_WH) == 720


async def test_the_name_is_not_offered(hass: HomeAssistant):
    """The title names every entity; renaming belongs to HA's own rename."""
    entry = await _loaded_entry(hass)

    result = await entry.start_reconfigure_flow(hass)

    assert "name" not in [str(marker) for marker in result["data_schema"].schema]


async def test_swapping_the_plug_updates_and_reloads_the_entry(hass: HomeAssistant):
    entry = await _loaded_entry(hass)

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _shelly()
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_PLUG_SWITCH] == NEW_PLUG
    assert entry.data[CONF_POWER_ENTITY] == NEW_POWER
    assert entry.data[CONF_ENERGY_ENTITY] == NEW_ENERGY
    assert entry.unique_id == NEW_PLUG
    assert entry.title == "Scooter"
    assert entry.options[OPT_TARGET_SOC] == 90.0
    # Reloaded, so the running coordinator watches the new plug.
    assert entry.runtime_data.plug_entity_id == NEW_PLUG
    assert entry.runtime_data.power_entity_id == NEW_POWER


async def test_the_flow_only_checkbox_is_not_stored(hass: HomeAssistant):
    entry = await _loaded_entry(hass)

    result = await entry.start_reconfigure_flow(hass)
    await hass.config_entries.flow.async_configure(
        result["flow_id"], _shelly(**{CONF_FORGET_LEARNING: True})
    )
    await hass.async_block_till_done()

    assert CONF_FORGET_LEARNING not in entry.data


async def test_a_meter_can_be_removed(hass: HomeAssistant):
    """Dropping the power sensor must not leave the old one behind."""
    entry = await _loaded_entry(hass)
    payload = _shelly()
    del payload[CONF_POWER_ENTITY]

    result = await entry.start_reconfigure_flow(hass)
    await hass.config_entries.flow.async_configure(result["flow_id"], payload)
    await hass.async_block_till_done()

    assert CONF_POWER_ENTITY not in entry.data


async def test_no_meter_is_rejected(hass: HomeAssistant):
    entry = await _loaded_entry(hass)
    payload = _shelly()
    del payload[CONF_POWER_ENTITY]
    del payload[CONF_ENERGY_ENTITY]

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], payload)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "no_meter"}
    assert entry.data[CONF_PLUG_SWITCH] == PLUG


async def _on_device(hass: HomeAssistant, device_key: str, platform: str, object_id: str,
                     device_class: str | None = None) -> str:
    config_entry = MockConfigEntry(domain="demo")
    config_entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=config_entry.entry_id, identifiers={("demo", device_key)}
    )
    return er.async_get(hass).async_get_or_create(
        platform, "demo", object_id, device_id=device.id,
        original_device_class=device_class,
    ).entity_id


async def test_a_meter_on_another_device_is_flagged_then_can_be_overridden(
    hass: HomeAssistant,
):
    entry = await _loaded_entry(hass)
    switch = await _on_device(hass, "shelly", Platform.SWITCH, "shelly")
    foreign = await _on_device(
        hass, "other", Platform.SENSOR, "other_power", SensorDeviceClass.POWER
    )
    payload = _shelly(**{CONF_PLUG_SWITCH: switch, CONF_POWER_ENTITY: foreign})
    del payload[CONF_ENERGY_ENTITY]

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], payload)
    assert result["errors"] == {"base": "meter_not_on_plug"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**payload, CONF_ALLOW_FOREIGN_METER: True}
    )
    await hass.async_block_till_done()
    assert result["reason"] == "reconfigure_successful"
    assert CONF_ALLOW_FOREIGN_METER not in entry.data


async def test_a_plug_already_used_by_another_entry_is_refused(hass: HomeAssistant):
    entry = await _loaded_entry(hass)
    MockConfigEntry(
        domain=DOMAIN, unique_id=NEW_PLUG, data={CONF_PLUG_SWITCH: NEW_PLUG}
    ).add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _shelly()
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_PLUG_SWITCH] == PLUG


async def test_keeping_the_same_plug_is_not_a_collision(hass: HomeAssistant):
    """Changing only the battery must not trip over the entry's own identity."""
    entry = await _loaded_entry(hass)

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        _shelly(**{CONF_PLUG_SWITCH: PLUG, CONF_POWER_ENTITY: POWER,
                   CONF_ENERGY_ENTITY: ENERGY, CONF_CAPACITY_WH: 800}),
    )
    await hass.async_block_till_done()

    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_CAPACITY_WH] == 800
    assert entry.unique_id == PLUG


async def test_it_refuses_while_a_charge_is_running(hass: HomeAssistant):
    """Swapping meters mid-session would count from the wrong baseline."""
    entry = await _loaded_entry(hass)
    entry.runtime_data.state = ChargeState.CHARGING

    result = await entry.start_reconfigure_flow(hass)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "session_open"


async def test_it_refuses_during_an_uncalibrated_charge_too(hass: HomeAssistant):
    entry = await _loaded_entry(hass)
    entry.runtime_data.state = ChargeState.UNCALIBRATED

    result = await entry.start_reconfigure_flow(hass)

    assert result["reason"] == "session_open"


async def test_changing_the_meter_forgets_what_was_learned(hass: HomeAssistant):
    """Learned charges are in the old meter's units."""
    entry = await _loaded_entry(hass)
    await entry.runtime_data.async_record_charge(40, 80, 250)
    entry.runtime_data._pending_calibration = {"start_soc": 40.0, "delivered_wh": 9.0}
    assert entry.runtime_data.remembered_charges == 1

    result = await entry.start_reconfigure_flow(hass)
    await hass.config_entries.flow.async_configure(
        result["flow_id"], _shelly(**{CONF_FORGET_LEARNING: True})
    )
    await hass.async_block_till_done()

    assert entry.runtime_data.remembered_charges == 0
    assert entry.runtime_data._pending_calibration is None


async def test_forgetting_is_the_default(hass: HomeAssistant):
    entry = await _loaded_entry(hass)
    await entry.runtime_data.async_record_charge(40, 80, 250)

    result = await entry.start_reconfigure_flow(hass)
    await hass.config_entries.flow.async_configure(result["flow_id"], _shelly())
    await hass.async_block_till_done()

    assert entry.runtime_data.remembered_charges == 0


async def test_learning_can_be_kept_on_request(hass: HomeAssistant):
    """For a correction that doesn't change the units, e.g. a mis-picked twin."""
    entry = await _loaded_entry(hass)
    await entry.runtime_data.async_record_charge(40, 80, 250)

    result = await entry.start_reconfigure_flow(hass)
    await hass.config_entries.flow.async_configure(
        result["flow_id"], _shelly(**{CONF_FORGET_LEARNING: False})
    )
    await hass.async_block_till_done()

    assert entry.runtime_data.remembered_charges == 1


async def test_learning_survives_when_the_meters_are_unchanged(hass: HomeAssistant):
    """Same meters, same units: the box is ticked but there is nothing to forget."""
    entry = await _loaded_entry(hass)
    await entry.runtime_data.async_record_charge(40, 80, 250)

    result = await entry.start_reconfigure_flow(hass)
    await hass.config_entries.flow.async_configure(
        result["flow_id"],
        _shelly(**{CONF_PLUG_SWITCH: PLUG, CONF_POWER_ENTITY: POWER,
                   CONF_ENERGY_ENTITY: ENERGY, CONF_CAPACITY_WH: 800,
                   CONF_FORGET_LEARNING: True}),
    )
    await hass.async_block_till_done()

    assert entry.runtime_data.remembered_charges == 1


SEED = 720 / 100 / 0.87  # the capacity-derived starting value


async def test_changing_the_meter_resets_a_carried_starting_value(
    hass: HomeAssistant,
):
    """A default prior carried over from an old learned value is in the old
    meter's units too - clearing the charges but keeping it would leave every
    band at the old meter's scale."""
    entry = await _loaded_entry(hass)
    entry.runtime_data._default_prior = 6.0

    result = await entry.start_reconfigure_flow(hass)
    await hass.config_entries.flow.async_configure(result["flow_id"], _shelly())
    await hass.async_block_till_done()

    assert entry.runtime_data.default_prior == pytest.approx(SEED)
    assert entry.runtime_data.bands == pytest.approx([SEED] * 10)


async def test_the_reset_follows_a_capacity_changed_at_the_same_time(
    hass: HomeAssistant,
):
    entry = await _loaded_entry(hass)
    entry.runtime_data._default_prior = 6.0

    result = await entry.start_reconfigure_flow(hass)
    await hass.config_entries.flow.async_configure(
        result["flow_id"], _shelly(**{CONF_CAPACITY_WH: 800})
    )
    await hass.async_block_till_done()

    assert entry.runtime_data.default_prior == pytest.approx(800 / 100 / 0.87)


async def test_an_unchanged_meter_keeps_the_starting_value(hass: HomeAssistant):
    entry = await _loaded_entry(hass)
    entry.runtime_data._default_prior = 6.0

    result = await entry.start_reconfigure_flow(hass)
    await hass.config_entries.flow.async_configure(
        result["flow_id"],
        _shelly(**{CONF_PLUG_SWITCH: PLUG, CONF_POWER_ENTITY: POWER,
                   CONF_ENERGY_ENTITY: ENERGY, CONF_CAPACITY_WH: 720}),
    )
    await hass.async_block_till_done()

    assert entry.runtime_data.default_prior == pytest.approx(6.0)


async def test_keeping_learning_keeps_the_starting_value(hass: HomeAssistant):
    entry = await _loaded_entry(hass)
    entry.runtime_data._default_prior = 6.0

    result = await entry.start_reconfigure_flow(hass)
    await hass.config_entries.flow.async_configure(
        result["flow_id"], _shelly(**{CONF_FORGET_LEARNING: False})
    )
    await hass.async_block_till_done()

    assert entry.runtime_data.default_prior == pytest.approx(6.0)


async def test_the_options_forget_still_keeps_the_starting_value(
    hass: HomeAssistant,
):
    """Forgetting remembered charges from the options is a different act: same
    meter, so the starting value is still valid."""
    entry = await _loaded_entry(hass)
    entry.runtime_data._default_prior = 6.0
    await entry.runtime_data.async_record_charge(40, 80, 250)

    await entry.runtime_data.async_forget_charges()

    assert entry.runtime_data.default_prior == pytest.approx(6.0)


async def test_an_unloaded_entry_resets_the_starting_value_in_storage(
    hass: HomeAssistant, hass_storage
):
    _seed_states(hass)
    hass_storage[STORAGE_KEY] = {
        "version": 2,
        "minor_version": 1,
        "key": STORAGE_KEY,
        "data": {"state": "armed", "enabled": True, "default_prior": 6.0,
                 "charges": [], "pending_calibration": None},
    }
    entry = _entry(hass)

    result = await entry.start_reconfigure_flow(hass)
    await hass.config_entries.flow.async_configure(result["flow_id"], _shelly())
    await hass.async_block_till_done()

    assert entry.runtime_data.default_prior == pytest.approx(SEED)


async def test_an_unloaded_entry_forgets_from_storage(
    hass: HomeAssistant, hass_storage
):
    """No coordinator to ask, so the stored learning is cleared directly."""
    _seed_states(hass)
    hass_storage[STORAGE_KEY] = {
        "version": 2,
        "minor_version": 1,
        "key": STORAGE_KEY,
        "data": {
            "state": "armed",
            "enabled": True,
            "charges": [
                {"start_soc": 40.0, "end_soc": 80.0, "energy_wh": 250.0,
                 "recorded_at": 0.0, "source": "manual"}
            ],
            "pending_calibration": {"start_soc": 40.0, "delivered_wh": 9.0},
        },
    }
    entry = _entry(hass)  # added, never set up

    result = await entry.start_reconfigure_flow(hass)
    await hass.config_entries.flow.async_configure(result["flow_id"], _shelly())
    await hass.async_block_till_done()

    # The reload sets it up, from storage that no longer holds the old charges.
    assert entry.runtime_data.remembered_charges == 0
    assert entry.runtime_data._pending_calibration is None
