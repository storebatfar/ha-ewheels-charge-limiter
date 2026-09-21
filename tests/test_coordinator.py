"""Coordinator state machine."""

from __future__ import annotations

from datetime import timedelta

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.ewheels_charge_limiter.const import (
    CONF_CAPACITY_WH,
    CONF_ENERGY_ENTITY,
    CONF_PLUG_SWITCH,
    CONF_POWER_ENTITY,
    CONF_SOC_ENTITY,
    DOMAIN,
    OPT_CHARGING_POWER_THRESHOLD,
    OPT_MAX_SESSION_HOURS,
    OPT_REARM_HYSTERESIS,
    OPT_TARGET_SOC,
    OPT_WH_PER_PERCENT,
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


async def _coordinator(
    hass: HomeAssistant, plug: str = "on", **options
) -> ChargeLimiterCoordinator:
    _register_switch_services(hass)
    hass.states.async_set(PLUG, plug)
    hass.states.async_set(POWER, "0", {"unit_of_measurement": "W"})
    hass.states.async_set(ENERGY, "0", {"unit_of_measurement": "kWh"})
    hass.states.async_set(SOC, "40", {"unit_of_measurement": "%"})
    coordinator = ChargeLimiterCoordinator(hass, _entry(hass, **options))
    await coordinator.async_setup()
    await hass.async_block_till_done()
    return coordinator


async def test_starts_armed(hass: HomeAssistant):
    coordinator = await _coordinator(hass)
    assert coordinator.state is ChargeState.ARMED


async def test_setup_does_not_energise_the_plug(hass: HomeAssistant):
    """Arming means watching for the next charge, not starting one."""
    coordinator = await _coordinator(hass, plug="off")
    assert coordinator.state is ChargeState.ARMED
    assert hass.states.get(PLUG).state == "off"


async def test_enabling_does_not_energise_the_plug(hass: HomeAssistant):
    coordinator = await _coordinator(hass, plug="off")
    await coordinator.async_set_enabled(False)
    await hass.async_block_till_done()

    await coordinator.async_set_enabled(True)
    await hass.async_block_till_done()

    assert coordinator.state is ChargeState.ARMED
    assert hass.states.get(PLUG).state == "off"


async def test_a_target_change_does_not_energise_the_plug(hass: HomeAssistant):
    coordinator = await _coordinator(hass)
    await coordinator.async_set_plug(False)
    await hass.async_block_till_done()
    assert hass.states.get(PLUG).state == "off"

    await coordinator.async_set_target(95.0)
    await hass.async_block_till_done()

    assert hass.states.get(PLUG).state == "off"


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


async def test_a_fresh_low_reading_rearms_without_energising(hass: HomeAssistant):
    """Ready to limit the next charge, but starting one is the owner's call."""
    coordinator = await _coordinator(hass)
    hass.states.async_set(POWER, "120", {"unit_of_measurement": "W"})
    await hass.async_block_till_done()
    hass.states.async_set(ENERGY, "0.4", {"unit_of_measurement": "kWh"})
    await hass.async_block_till_done()
    assert coordinator.state is ChargeState.COMPLETE
    assert hass.states.get(PLUG).state == "off"

    hass.states.async_set(SOC, "60", {"unit_of_measurement": "%"})
    await hass.async_block_till_done()
    assert coordinator.state is ChargeState.ARMED
    assert hass.states.get(PLUG).state == "off"


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


async def test_manual_on_arms_even_above_target(hass: HomeAssistant):
    """The override is an instruction, not a suggestion."""
    coordinator = await _coordinator(hass)
    hass.states.async_set(SOC, "95", {"unit_of_measurement": "%"})
    await hass.async_block_till_done()

    await coordinator.async_set_plug(False)
    await hass.async_block_till_done()
    assert coordinator.state is ChargeState.STOPPED

    await coordinator.async_set_plug(True)
    await hass.async_block_till_done()
    assert coordinator.state is ChargeState.ARMED
    assert hass.states.get(PLUG).state == "on"


async def test_manual_off_stops_an_open_session(hass: HomeAssistant):
    coordinator = await _coordinator(hass)
    hass.states.async_set(POWER, "120", {"unit_of_measurement": "W"})
    await hass.async_block_till_done()
    assert coordinator.state is ChargeState.CHARGING

    await coordinator.async_set_plug(False)
    await hass.async_block_till_done()
    assert coordinator.state is ChargeState.STOPPED
    assert hass.states.get(PLUG).state == "off"


async def test_manual_stop_records_no_calibration(hass: HomeAssistant):
    coordinator = await _coordinator(hass)
    before = coordinator.wh_per_percent
    hass.states.async_set(POWER, "120", {"unit_of_measurement": "W"})
    await hass.async_block_till_done()
    hass.states.async_set(ENERGY, "0.3", {"unit_of_measurement": "kWh"})
    await hass.async_block_till_done()

    await coordinator.async_set_plug(False)
    await hass.async_block_till_done()
    hass.states.async_set(SOC, "75", {"unit_of_measurement": "%"})
    await hass.async_block_till_done()

    assert coordinator.wh_per_percent == before


async def test_an_external_plug_change_drives_the_same_transition(
    hass: HomeAssistant,
):
    """Someone pressed the button on the plug itself."""
    coordinator = await _coordinator(hass)
    hass.states.async_set(PLUG, "off")
    await hass.async_block_till_done()
    assert coordinator.state is ChargeState.STOPPED


async def test_a_stale_soc_reading_disables_the_limit(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
):
    coordinator = await _coordinator(hass, soc_staleness_hours=12)
    freezer.tick(timedelta(hours=13))

    hass.states.async_set(POWER, "120", {"unit_of_measurement": "W"})
    await hass.async_block_till_done()

    assert coordinator.state is ChargeState.UNCALIBRATED
    assert coordinator.required_wh is None
    assert hass.states.get(PLUG).state == "on"


async def test_an_idle_period_closes_a_session(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
):
    coordinator = await _coordinator(hass, idle_close_minutes=10)
    hass.states.async_set(POWER, "120", {"unit_of_measurement": "W"})
    await hass.async_block_till_done()

    hass.states.async_set(POWER, "0", {"unit_of_measurement": "W"})
    await hass.async_block_till_done()
    assert coordinator.state is ChargeState.CHARGING  # a dip is not an ending

    freezer.tick(timedelta(minutes=11))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    assert coordinator.state is ChargeState.ARMED


async def test_an_idle_period_completes_an_uncalibrated_session(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
):
    coordinator = await _coordinator(
        hass, soc_staleness_hours=12, idle_close_minutes=10
    )
    freezer.tick(timedelta(hours=13))
    hass.states.async_set(POWER, "120", {"unit_of_measurement": "W"})
    await hass.async_block_till_done()
    assert coordinator.state is ChargeState.UNCALIBRATED

    hass.states.async_set(POWER, "0", {"unit_of_measurement": "W"})
    await hass.async_block_till_done()
    freezer.tick(timedelta(minutes=11))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()

    assert coordinator.state is ChargeState.COMPLETE
    assert hass.states.get(PLUG).state == "off"


async def test_the_max_session_cap_stalls_and_cuts(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
):
    coordinator = await _coordinator(hass, max_session_hours=8)
    hass.states.async_set(POWER, "120", {"unit_of_measurement": "W"})
    await hass.async_block_till_done()

    freezer.tick(timedelta(hours=9))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()

    assert coordinator.state is ChargeState.STALLED
    assert hass.states.get(PLUG).state == "off"


async def test_losing_every_meter_stalls_and_cuts(hass: HomeAssistant):
    """Blind mid-session: continuing could mean never cutting at all."""
    coordinator = await _coordinator(hass)
    hass.states.async_set(POWER, "120", {"unit_of_measurement": "W"})
    await hass.async_block_till_done()
    assert coordinator.state is ChargeState.CHARGING

    hass.states.async_set(POWER, STATE_UNAVAILABLE)
    hass.states.async_set(ENERGY, STATE_UNAVAILABLE)
    await hass.async_block_till_done()

    assert coordinator.state is ChargeState.STALLED
    assert hass.states.get(PLUG).state == "off"


async def test_losing_one_meter_keeps_going(hass: HomeAssistant):
    """The energy sensor alone is enough to keep counting."""
    coordinator = await _coordinator(hass)
    hass.states.async_set(POWER, "120", {"unit_of_measurement": "W"})
    await hass.async_block_till_done()

    hass.states.async_set(POWER, STATE_UNAVAILABLE)
    await hass.async_block_till_done()

    assert coordinator.state is ChargeState.CHARGING


async def test_raising_the_target_mid_session_extends_the_requirement(
    hass: HomeAssistant,
):
    coordinator = await _coordinator(hass)
    hass.states.async_set(POWER, "120", {"unit_of_measurement": "W"})
    await hass.async_block_till_done()
    assert coordinator.required_wh == pytest.approx(331.0, abs=1.0)

    await coordinator.async_set_target(90.0)
    await hass.async_block_till_done()

    # 40% -> 90% at the seeded 8.276 Wh/% is ~414 Wh
    assert coordinator.required_wh == pytest.approx(414.0, abs=1.0)
    assert coordinator.state is ChargeState.CHARGING


async def test_lowering_the_target_below_what_is_delivered_cuts_immediately(
    hass: HomeAssistant,
):
    coordinator = await _coordinator(hass)
    hass.states.async_set(POWER, "120", {"unit_of_measurement": "W"})
    await hass.async_block_till_done()
    hass.states.async_set(ENERGY, "0.2", {"unit_of_measurement": "kWh"})
    await hass.async_block_till_done()
    assert coordinator.state is ChargeState.CHARGING

    # 40% -> 60% is ~166 Wh, and 200 Wh are already in the battery.
    await coordinator.async_set_target(60.0)
    await hass.async_block_till_done()

    assert coordinator.state is ChargeState.COMPLETE
    assert hass.states.get(PLUG).state == "off"


async def test_lowering_the_target_below_the_starting_charge_cuts_immediately(
    hass: HomeAssistant,
):
    """A negative requirement is still a requirement that has been met."""
    coordinator = await _coordinator(hass)
    hass.states.async_set(POWER, "120", {"unit_of_measurement": "W"})
    await hass.async_block_till_done()

    await coordinator.async_set_target(30.0)
    await hass.async_block_till_done()

    assert coordinator.state is ChargeState.COMPLETE
    assert hass.states.get(PLUG).state == "off"


async def test_a_target_change_does_not_limit_an_uncalibrated_session(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
):
    """There is no starting point to measure from, so there is nothing to apply."""
    coordinator = await _coordinator(hass, soc_staleness_hours=12)
    freezer.tick(timedelta(hours=13))
    hass.states.async_set(POWER, "120", {"unit_of_measurement": "W"})
    await hass.async_block_till_done()
    assert coordinator.state is ChargeState.UNCALIBRATED

    await coordinator.async_set_target(90.0)
    await hass.async_block_till_done()

    assert coordinator.required_wh is None
    assert coordinator.state is ChargeState.UNCALIBRATED


async def test_shortening_the_max_session_applies_to_the_open_session(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
):
    coordinator = await _coordinator(hass, max_session_hours=8)
    hass.states.async_set(POWER, "120", {"unit_of_measurement": "W"})
    await hass.async_block_till_done()

    hass.config_entries.async_update_entry(
        coordinator.entry,
        options={**coordinator.entry.options, OPT_MAX_SESSION_HOURS: 1},
    )
    await hass.async_block_till_done()

    freezer.tick(timedelta(hours=2))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()

    assert coordinator.state is ChargeState.STALLED


async def test_a_new_wh_per_percent_option_is_applied(hass: HomeAssistant):
    coordinator = await _coordinator(hass)
    hass.states.async_set(POWER, "120", {"unit_of_measurement": "W"})
    await hass.async_block_till_done()

    hass.config_entries.async_update_entry(
        coordinator.entry,
        options={**coordinator.entry.options, OPT_WH_PER_PERCENT: 10.0},
    )
    await hass.async_block_till_done()

    assert coordinator.wh_per_percent == pytest.approx(10.0)
    # The open session is re-measured against it: 40% -> 80% at 10 Wh/%.
    assert coordinator.required_wh == pytest.approx(400.0)


async def test_an_unchanged_wh_per_percent_option_does_not_undo_calibration(
    hass: HomeAssistant,
):
    """A stale override must not claw back what later sessions learned."""
    coordinator = await _coordinator(hass, wh_per_percent=10.0)
    coordinator.wh_per_percent = 9.0

    await coordinator.async_set_target(85.0)
    await hass.async_block_till_done()

    assert coordinator.wh_per_percent == pytest.approx(9.0)


async def test_a_session_survives_a_reload(hass: HomeAssistant, hass_storage):
    coordinator = await _coordinator(hass)
    hass.states.async_set(POWER, "120", {"unit_of_measurement": "W"})
    await hass.async_block_till_done()
    hass.states.async_set(ENERGY, "0.2", {"unit_of_measurement": "kWh"})
    await hass.async_block_till_done()
    assert coordinator.session_delivered_wh == pytest.approx(200.0)

    await coordinator.async_shutdown()

    revived = ChargeLimiterCoordinator(hass, coordinator.entry)
    await revived.async_setup()
    await hass.async_block_till_done()

    assert revived.state is ChargeState.CHARGING
    assert revived.session_delivered_wh == pytest.approx(200.0)
    assert revived.session_start_soc == pytest.approx(40.0)
