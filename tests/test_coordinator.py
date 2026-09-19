"""Coordinator state machine."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant, ServiceCall
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ewheels_charge_limiter.const import (
    CONF_CAPACITY_WH,
    CONF_ENERGY_ENTITY,
    CONF_PLUG_DEVICE,
    CONF_PLUG_SWITCH,
    CONF_POWER_ENTITY,
    CONF_SOC_ENTITY,
    DOMAIN,
    OPT_CHARGING_POWER_THRESHOLD,
    OPT_REARM_HYSTERESIS,
    OPT_TARGET_SOC,
    ChargeState,
)
from custom_components.ewheels_charge_limiter.coordinator import (
    ChargeLimiterCoordinator,
)

PLUG = "switch.plug"
POWER = "sensor.plug_power"
ENERGY = "sensor.plug_energy"
SOC = "sensor.scooter_battery"


def _register_switch_services(hass: HomeAssistant) -> None:
    """A switch domain that actually moves the state, like the real thing."""

    async def handle(call: ServiceCall) -> None:
        target = call.data.get("entity_id")
        entity_ids = [target] if isinstance(target, str) else list(target or [])
        for entity_id in entity_ids:
            hass.states.async_set(
                entity_id, "on" if call.service == "turn_on" else "off"
            )

    hass.services.async_register("switch", "turn_on", handle)
    hass.services.async_register("switch", "turn_off", handle)


def _entry(hass: HomeAssistant, **options) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="plug-1",
        data={
            CONF_PLUG_DEVICE: "plug-1",
            CONF_PLUG_SWITCH: PLUG,
            CONF_POWER_ENTITY: POWER,
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
    return entry


async def _coordinator(hass: HomeAssistant, **options) -> ChargeLimiterCoordinator:
    _register_switch_services(hass)
    hass.states.async_set(PLUG, "on")
    hass.states.async_set(POWER, "0", {"unit_of_measurement": "W"})
    hass.states.async_set(ENERGY, "0", {"unit_of_measurement": "kWh"})
    hass.states.async_set(SOC, "40", {"unit_of_measurement": "%"})
    coordinator = ChargeLimiterCoordinator(hass, _entry(hass, **options))
    await coordinator.async_setup()
    await hass.async_block_till_done()
    return coordinator


async def test_starts_armed_with_the_plug_on(hass: HomeAssistant):
    coordinator = await _coordinator(hass)
    assert coordinator.state is ChargeState.ARMED


async def test_power_above_threshold_opens_a_session(hass: HomeAssistant):
    coordinator = await _coordinator(hass)
    hass.states.async_set(POWER, "120", {"unit_of_measurement": "W"})
    await hass.async_block_till_done()
    assert coordinator.state is ChargeState.CHARGING


async def test_session_records_the_soc_it_started_from(hass: HomeAssistant):
    coordinator = await _coordinator(hass)
    hass.states.async_set(POWER, "120", {"unit_of_measurement": "W"})
    await hass.async_block_till_done()
    # 40% -> 80% at the seeded 8.276 Wh/% is ~331 Wh
    assert coordinator.required_wh == pytest.approx(331.0, abs=1.0)


async def test_reaching_the_target_cuts_the_plug(hass: HomeAssistant):
    coordinator = await _coordinator(hass)
    hass.states.async_set(POWER, "120", {"unit_of_measurement": "W"})
    await hass.async_block_till_done()

    # Energy sensor jumps past the requirement: 0.4 kWh = 400 Wh > 331 Wh
    hass.states.async_set(ENERGY, "0.4", {"unit_of_measurement": "kWh"})
    await hass.async_block_till_done()

    assert coordinator.state is ChargeState.COMPLETE
    assert hass.states.get(PLUG).state == "off"


async def test_already_above_target_completes_without_energising(hass: HomeAssistant):
    coordinator = await _coordinator(hass)
    hass.states.async_set(SOC, "85", {"unit_of_measurement": "%"})
    await hass.async_block_till_done()
    hass.states.async_set(POWER, "120", {"unit_of_measurement": "W"})
    await hass.async_block_till_done()
    assert coordinator.state is ChargeState.COMPLETE


async def test_a_fresh_low_reading_rearms(hass: HomeAssistant):
    coordinator = await _coordinator(hass)
    hass.states.async_set(POWER, "120", {"unit_of_measurement": "W"})
    await hass.async_block_till_done()
    hass.states.async_set(ENERGY, "0.4", {"unit_of_measurement": "kWh"})
    await hass.async_block_till_done()
    assert coordinator.state is ChargeState.COMPLETE

    hass.states.async_set(SOC, "60", {"unit_of_measurement": "%"})
    await hass.async_block_till_done()
    assert coordinator.state is ChargeState.ARMED
    assert hass.states.get(PLUG).state == "on"


async def test_a_reading_within_hysteresis_does_not_rearm(hass: HomeAssistant):
    coordinator = await _coordinator(hass)
    hass.states.async_set(POWER, "120", {"unit_of_measurement": "W"})
    await hass.async_block_till_done()
    hass.states.async_set(ENERGY, "0.4", {"unit_of_measurement": "kWh"})
    await hass.async_block_till_done()

    # 78% is within 5 points of the 80% target.
    hass.states.async_set(SOC, "78", {"unit_of_measurement": "%"})
    await hass.async_block_till_done()
    assert coordinator.state is ChargeState.COMPLETE
