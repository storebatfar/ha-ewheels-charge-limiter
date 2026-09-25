"""Entity platforms and config entry lifecycle."""

from __future__ import annotations

import pytest
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockToggleEntity,
    setup_test_component_platform,
)

from custom_components.ewheels_charge_limiter.const import (
    CONF_CAPACITY_WH,
    CONF_ENERGY_ENTITY,
    CONF_PLUG_SWITCH,
    CONF_POWER_ENTITY,
    CONF_SOC_ENTITY,
    DOMAIN,
    OPT_CHARGING_POWER_THRESHOLD,
    OPT_REARM_HYSTERESIS,
    OPT_REST_MINUTES,
    OPT_TARGET_SOC,
    OPT_WH_PER_PERCENT,
)

PLUG = "switch.plug"
POWER = "sensor.plug_power"
ENERGY = "sensor.plug_energy"
SOC = "sensor.scooter_battery"


async def _setup(hass: HomeAssistant, power: bool = True, **options) -> MockConfigEntry:
    """Bring up a real switch entity for the plug, then the integration."""
    setup_test_component_platform(hass, SWITCH_DOMAIN, [MockToggleEntity("Plug", "on")])
    assert await async_setup_component(
        hass, SWITCH_DOMAIN, {SWITCH_DOMAIN: {"platform": "test"}}
    )
    await hass.async_block_till_done()

    hass.states.async_set(POWER, "0", {"unit_of_measurement": "W"})
    hass.states.async_set(ENERGY, "0", {"unit_of_measurement": "kWh"})
    hass.states.async_set(SOC, "40", {"unit_of_measurement": "%"})

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="plug-1",
        title="Scooter",
        data={
            CONF_PLUG_SWITCH: PLUG,
            **({CONF_POWER_ENTITY: POWER} if power else {}),
            CONF_ENERGY_ENTITY: ENERGY,
            CONF_SOC_ENTITY: SOC,
            CONF_CAPACITY_WH: 720,
        },
        options={
            OPT_TARGET_SOC: 80.0,
            OPT_REARM_HYSTERESIS: 5.0,
            OPT_CHARGING_POWER_THRESHOLD: 5.0,
            **options,
        },
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_status_sensor_reports_armed(hass: HomeAssistant):
    await _setup(hass)
    assert hass.states.get("sensor.scooter_status").state == "armed"


async def test_session_energy_sensor_exists_and_is_zero(hass: HomeAssistant):
    await _setup(hass)
    assert hass.states.get("sensor.scooter_session_energy").state == "0.0"


async def test_wh_per_percent_sensor_reports_the_seed(hass: HomeAssistant):
    await _setup(hass)
    state = hass.states.get("sensor.scooter_wh_per_percent")
    assert float(state.state) == pytest.approx(8.276, abs=0.01)


async def test_charge_power_sensor_mirrors_the_plug(hass: HomeAssistant):
    await _setup(hass)
    assert hass.states.get("sensor.scooter_charge_power").state == "0.0"

    hass.states.async_set(POWER, "120", {"unit_of_measurement": "W"})
    await hass.async_block_till_done()
    assert hass.states.get("sensor.scooter_charge_power").state == "120.0"


async def test_charge_power_sensor_is_absent_without_a_power_meter(
    hass: HomeAssistant,
):
    """An energy-only plug has no instantaneous reading to mirror."""
    await _setup(hass, power=False)
    assert hass.states.get("sensor.scooter_charge_power") is None


async def test_charge_power_keeps_tracking_while_control_is_disabled(
    hass: HomeAssistant,
):
    """It reports the plug, not the limiter, so turning control off cannot stale it."""
    await _setup(hass)
    await hass.services.async_call(
        SWITCH_DOMAIN,
        "turn_off",
        {"entity_id": "switch.scooter_enabled"},
        blocking=True,
    )
    await hass.async_block_till_done()

    hass.states.async_set(POWER, "75", {"unit_of_measurement": "W"})
    await hass.async_block_till_done()
    assert hass.states.get("sensor.scooter_charge_power").state == "75.0"


async def test_status_sensor_follows_the_coordinator(hass: HomeAssistant):
    await _setup(hass)
    hass.states.async_set(POWER, "120", {"unit_of_measurement": "W"})
    await hass.async_block_till_done()
    assert hass.states.get("sensor.scooter_status").state == "charging"


async def test_plug_switch_reflects_the_real_plug(hass: HomeAssistant):
    await _setup(hass)
    assert hass.states.get("switch.scooter_plug").state == "on"

    # An external change: the button on the plug itself.
    await hass.services.async_call(
        SWITCH_DOMAIN, "turn_off", {"entity_id": PLUG}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get("switch.scooter_plug").state == "off"


async def test_plug_switch_stays_off_while_armed_over_a_dead_plug(
    hass: HomeAssistant,
):
    """Armed no longer implies energised, so the switch must follow the relay."""
    await _setup(hass)
    await hass.services.async_call(
        SWITCH_DOMAIN, "turn_off", {"entity_id": PLUG}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get("sensor.scooter_status").state == "stopped"

    # A low reading re-arms the limiter, but must not claim the plug is on.
    hass.states.async_set(SOC, "60", {"unit_of_measurement": "%"})
    await hass.async_block_till_done()

    assert hass.states.get("sensor.scooter_status").state == "armed"
    assert hass.states.get("switch.scooter_plug").state == "off"


async def test_turning_the_plug_switch_off_cuts_the_real_plug(hass: HomeAssistant):
    await _setup(hass)
    await hass.services.async_call(
        SWITCH_DOMAIN, "turn_off", {"entity_id": "switch.scooter_plug"}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get(PLUG).state == "off"
    assert hass.states.get("sensor.scooter_status").state == "stopped"


async def test_disabling_the_integration_leaves_the_plug_alone(hass: HomeAssistant):
    await _setup(hass)
    await hass.services.async_call(
        SWITCH_DOMAIN,
        "turn_off",
        {"entity_id": "switch.scooter_enabled"},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert hass.states.get("sensor.scooter_status").state == "idle"
    assert hass.states.get(PLUG).state == "on"


async def test_target_number_updates_the_option(hass: HomeAssistant):
    entry = await _setup(hass)
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": "number.scooter_target_charge", "value": 90},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert entry.options[OPT_TARGET_SOC] == 90.0
    assert hass.states.get("number.scooter_target_charge").state == "90.0"


async def test_unloading_makes_the_entities_unavailable(hass: HomeAssistant):
    """Unload leaves restored placeholders; only removal deletes them."""
    entry = await _setup(hass)
    assert hass.states.get("sensor.scooter_status").state == "armed"

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.scooter_status").state == "unavailable"


async def test_removing_the_entry_deletes_the_entities(hass: HomeAssistant):
    entry = await _setup(hass)
    assert hass.states.get("sensor.scooter_status") is not None

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.scooter_status") is None


async def test_changing_an_option_applies_without_reloading(hass: HomeAssistant):
    """Reloading would take every entity unavailable in the middle of a charge."""
    entry = await _setup(hass)
    coordinator = entry.runtime_data

    hass.config_entries.async_update_entry(
        entry, options={**entry.options, OPT_TARGET_SOC: 70.0}
    )
    await hass.async_block_till_done()

    assert hass.states.get("number.scooter_target_charge").state == "70.0"
    assert entry.runtime_data is coordinator


async def test_a_version_1_entry_is_migrated(hass: HomeAssistant):
    setup_test_component_platform(hass, SWITCH_DOMAIN, [MockToggleEntity("Plug", "on")])
    assert await async_setup_component(
        hass, SWITCH_DOMAIN, {SWITCH_DOMAIN: {"platform": "test"}}
    )
    hass.states.async_set(POWER, "0", {"unit_of_measurement": "W"})
    hass.states.async_set(ENERGY, "0", {"unit_of_measurement": "kWh"})
    hass.states.async_set(SOC, "40", {"unit_of_measurement": "%"})
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        unique_id="plug-1",
        title="Scooter",
        data={
            CONF_PLUG_SWITCH: PLUG,
            CONF_POWER_ENTITY: POWER,
            CONF_ENERGY_ENTITY: ENERGY,
            CONF_SOC_ENTITY: SOC,
            CONF_CAPACITY_WH: 720,
        },
        options={OPT_TARGET_SOC: 90.0, OPT_WH_PER_PERCENT: 6.0},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.version == 2
    assert OPT_WH_PER_PERCENT not in entry.options
    assert entry.options[OPT_REST_MINUTES] == 30
    assert entry.options[OPT_TARGET_SOC] == 90.0


SHAPED = {"band_4": 5.0, "band_5": 5.0, "band_6": 10.0, "band_7": 10.0}


async def test_wh_per_percent_is_the_average_cost_of_the_next_charge(
    hass: HomeAssistant,
):
    await _setup(hass, **SHAPED)
    # SoC 40 -> target 80: (50 + 50 + 100 + 100) / 40 points
    assert float(hass.states.get("sensor.scooter_wh_per_percent").state) == (
        pytest.approx(7.5)
    )


async def test_wh_per_percent_at_or_above_target_shows_the_target_band(
    hass: HomeAssistant,
):
    await _setup(hass, **SHAPED)
    hass.states.async_set(SOC, "85", {"unit_of_measurement": "%"})
    await hass.async_block_till_done()
    # target 80 lies in band 8, which is untyped: the seed
    assert float(hass.states.get("sensor.scooter_wh_per_percent").state) == (
        pytest.approx(720 / 100 / 0.87, abs=0.001)
    )


async def test_wh_per_percent_exposes_the_vase(hass: HomeAssistant):
    await _setup(hass, **SHAPED)
    attributes = hass.states.get("sensor.scooter_wh_per_percent").attributes
    assert attributes["bands"][4] == 5.0
    assert attributes["bands"][6] == 10.0
    assert attributes["typed_bands"] == [4, 5, 6, 7]
    assert attributes["remembered_charges"] == 0
    assert attributes["default_prior"] == pytest.approx(8.276, abs=0.001)
