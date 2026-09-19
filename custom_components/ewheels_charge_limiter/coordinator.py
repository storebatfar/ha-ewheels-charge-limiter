"""The charge-limiter state machine.

Event-driven rather than polling: everything hangs off state changes of the
plug switch, the meter, and the state-of-charge sensor.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN, UnitOfEnergy
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event

from .calibration import seed_wh_per_percent, update_wh_per_percent
from .const import (
    CONF_CAPACITY_WH,
    CONF_ENERGY_ENTITY,
    CONF_PLUG_SWITCH,
    CONF_POWER_ENTITY,
    CONF_SOC_ENTITY,
    DEFAULT_CHARGING_POWER_THRESHOLD,
    DEFAULT_REARM_HYSTERESIS,
    DEFAULT_TARGET_SOC,
    OPT_CHARGING_POWER_THRESHOLD,
    OPT_REARM_HYSTERESIS,
    OPT_TARGET_SOC,
    OPT_WH_PER_PERCENT,
    ChargeState,
)
from .energy_meter import EnergyMeter

_LOGGER = logging.getLogger(__name__)

_INVALID = (None, STATE_UNAVAILABLE, STATE_UNKNOWN)


def _as_float(state: Any) -> float | None:
    """Parse a state object's value, or None if it is not a number."""
    if state is None or state.state in _INVALID:
        return None
    try:
        return float(state.state)
    except (TypeError, ValueError):
        return None


def _to_wh(state: Any, value: float) -> float:
    """Normalise an energy reading to watt-hours."""
    if state.attributes.get("unit_of_measurement") == UnitOfEnergy.KILO_WATT_HOUR:
        return value * 1000.0
    return value


class ChargeLimiterCoordinator:
    """Owns the state machine for one configured plug."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry

        self._plug_switch: str = entry.data[CONF_PLUG_SWITCH]
        self._power_entity: str | None = entry.data.get(CONF_POWER_ENTITY)
        self._energy_entity: str | None = entry.data.get(CONF_ENERGY_ENTITY)
        self._soc_entity: str = entry.data[CONF_SOC_ENTITY]
        self._capacity_wh: float = float(entry.data[CONF_CAPACITY_WH])

        self.state: ChargeState = ChargeState.IDLE
        self.enabled: bool = True
        self.required_wh: float | None = None
        self.session_start_soc: float | None = None

        self._seed = seed_wh_per_percent(self._capacity_wh)
        self.wh_per_percent: float = float(
            entry.options.get(OPT_WH_PER_PERCENT) or self._seed
        )

        self._meter = EnergyMeter()
        self._pending_calibration: dict[str, float] | None = None
        self._unsubscribes: list[Callable[[], None]] = []
        self._listeners: list[Callable[[], None]] = []

    # ---- options -------------------------------------------------------

    @property
    def target_soc(self) -> float:
        return float(self.entry.options.get(OPT_TARGET_SOC, DEFAULT_TARGET_SOC))

    @property
    def _rearm_hysteresis(self) -> float:
        return float(
            self.entry.options.get(OPT_REARM_HYSTERESIS, DEFAULT_REARM_HYSTERESIS)
        )

    @property
    def _power_threshold(self) -> float:
        return float(
            self.entry.options.get(
                OPT_CHARGING_POWER_THRESHOLD, DEFAULT_CHARGING_POWER_THRESHOLD
            )
        )

    @property
    def session_delivered_wh(self) -> float:
        return self._meter.delivered_wh

    @property
    def projected_soc(self) -> float | None:
        if self.session_start_soc is None:
            return None
        return self.session_start_soc + self._meter.delivered_wh / self.wh_per_percent

    # ---- lifecycle -----------------------------------------------------

    async def async_setup(self) -> None:
        """Subscribe to the entities we watch and settle into a state."""
        watched = [self._plug_switch, self._soc_entity]
        if self._power_entity:
            watched.append(self._power_entity)
        if self._energy_entity:
            watched.append(self._energy_entity)

        self._unsubscribes.append(
            async_track_state_change_event(self.hass, watched, self._handle_change)
        )

        if self.enabled:
            await self._async_arm()

    async def async_shutdown(self) -> None:
        for unsubscribe in self._unsubscribes:
            unsubscribe()
        self._unsubscribes.clear()

    @callback
    def add_listener(self, update: Callable[[], None]) -> Callable[[], None]:
        """Register an entity for push updates."""
        self._listeners.append(update)

        def remove() -> None:
            self._listeners.remove(update)

        return remove

    @callback
    def _notify(self) -> None:
        for update in self._listeners:
            update()

    # ---- commands ------------------------------------------------------

    async def async_set_target(self, value: float) -> None:
        self.hass.config_entries.async_update_entry(
            self.entry, options={**self.entry.options, OPT_TARGET_SOC: value}
        )
        self._notify()

    async def async_set_enabled(self, on: bool) -> None:
        """Master enable. Turning off leaves the plug exactly as it is."""
        self.enabled = on
        if on:
            await self._async_arm()
        else:
            self._set_state(ChargeState.IDLE)

    # ---- event handling ------------------------------------------------

    async def _handle_change(self, event: Event[EventStateChangedData]) -> None:
        if not self.enabled:
            return

        entity_id = event.data["entity_id"]
        new_state = event.data["new_state"]

        if entity_id == self._soc_entity:
            await self._async_handle_soc(_as_float(new_state))
        elif entity_id == self._power_entity:
            await self._async_handle_power(_as_float(new_state), event)
        elif entity_id == self._energy_entity:
            await self._async_handle_energy(new_state)

        self._notify()

    async def _async_handle_soc(self, soc: float | None) -> None:
        """A fresh state-of-charge reading arrived."""
        if soc is None:
            return

        self._apply_pending_calibration(soc)

        if self.state in (
            ChargeState.COMPLETE,
            ChargeState.STOPPED,
            ChargeState.STALLED,
        ) and soc < self.target_soc - self._rearm_hysteresis:
            await self._async_arm()

    async def _async_handle_power(
        self, power: float | None, event: Event[EventStateChangedData]
    ) -> None:
        if power is None:
            return

        if self.state is ChargeState.ARMED and power > self._power_threshold:
            await self._async_open_session()

        if self.state is ChargeState.CHARGING and self._energy_entity is None:
            self._meter.add_power_reading(power, event.time_fired.timestamp())
            await self._async_check_target()

    async def _async_handle_energy(self, state: Any) -> None:
        total = _as_float(state)
        if total is None or self.state is not ChargeState.CHARGING:
            return

        self._meter.add_energy_reading(_to_wh(state, total))
        await self._async_check_target()

    # ---- transitions ---------------------------------------------------

    async def _async_arm(self) -> None:
        self.required_wh = None
        self.session_start_soc = None
        self._set_state(ChargeState.ARMED)
        await self._async_switch_plug(True)

    async def _async_open_session(self) -> None:
        """Power crossed the threshold: record where we are starting from."""
        soc = _as_float(self.hass.states.get(self._soc_entity))
        if soc is None:
            self._set_state(ChargeState.UNCALIBRATED)
            return

        if soc >= self.target_soc:
            self._set_state(ChargeState.COMPLETE)
            await self._async_switch_plug(False)
            return

        self.session_start_soc = soc
        self.required_wh = (self.target_soc - soc) * self.wh_per_percent

        energy_state = (
            self.hass.states.get(self._energy_entity) if self._energy_entity else None
        )
        baseline = _as_float(energy_state)
        if baseline is not None and energy_state is not None:
            baseline = _to_wh(energy_state, baseline)

        self._meter.start(baseline)
        self._set_state(ChargeState.CHARGING)

    async def _async_check_target(self) -> None:
        if self.required_wh is None:
            return
        if self._meter.delivered_wh >= self.required_wh:
            await self._async_complete()

    async def _async_complete(self) -> None:
        if self.session_start_soc is not None:
            self._pending_calibration = {
                "start_soc": self.session_start_soc,
                "delivered_wh": self._meter.delivered_wh,
            }
        self._set_state(ChargeState.COMPLETE)
        await self._async_switch_plug(False)

    def _apply_pending_calibration(self, soc: float) -> None:
        if self._pending_calibration is None:
            return
        self.wh_per_percent = update_wh_per_percent(
            current=self.wh_per_percent,
            seed=self._seed,
            delivered_wh=self._pending_calibration["delivered_wh"],
            start_soc=self._pending_calibration["start_soc"],
            end_soc=soc,
        )
        self._pending_calibration = None

    # ---- helpers -------------------------------------------------------

    @callback
    def _set_state(self, state: ChargeState) -> None:
        if state is not self.state:
            _LOGGER.debug("%s -> %s", self.state, state)
        self.state = state
        self._notify()

    async def _async_switch_plug(self, on: bool) -> None:
        await self.hass.services.async_call(
            "switch",
            "turn_on" if on else "turn_off",
            {"entity_id": self._plug_switch},
            blocking=True,
        )
