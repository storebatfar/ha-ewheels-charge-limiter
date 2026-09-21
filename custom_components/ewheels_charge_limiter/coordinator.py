"""The charge-limiter state machine.

Event-driven rather than polling: everything hangs off state changes of the
plug switch, the meter, and the state-of-charge sensor.

One principle runs through all of it: fail toward charged. The battery's own
BMS terminates a full charge, so the worst outcome of a mistake here is a 100%
charge - a lost longevity benefit, not a hazard. A flat scooter is the outcome
worth avoiding, so ambiguous cases resolve toward delivering more energy.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN, UnitOfEnergy
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .calibration import seed_wh_per_percent, update_wh_per_percent
from .const import (
    CONF_CAPACITY_WH,
    CONF_ENERGY_ENTITY,
    CONF_PLUG_SWITCH,
    CONF_POWER_ENTITY,
    CONF_SOC_ENTITY,
    DEFAULT_CHARGING_POWER_THRESHOLD,
    DEFAULT_IDLE_CLOSE_MINUTES,
    DEFAULT_MAX_SESSION_HOURS,
    DEFAULT_REARM_HYSTERESIS,
    DEFAULT_SOC_STALENESS_HOURS,
    DEFAULT_TARGET_SOC,
    DOMAIN,
    OPT_CHARGING_POWER_THRESHOLD,
    OPT_IDLE_CLOSE_MINUTES,
    OPT_MAX_SESSION_HOURS,
    OPT_REARM_HYSTERESIS,
    OPT_SOC_STALENESS_HOURS,
    OPT_TARGET_SOC,
    OPT_WH_PER_PERCENT,
    STORAGE_VERSION,
    ChargeState,
)
from .energy_meter import EnergyMeter

_LOGGER = logging.getLogger(__name__)

_INVALID = (None, STATE_UNAVAILABLE, STATE_UNKNOWN)

# States in which the plug is off and a session is not running. A plug event
# arriving while in one of these is either our own doing or a manual restart.
_PLUG_OFF_STATES = (ChargeState.COMPLETE, ChargeState.STOPPED, ChargeState.STALLED)


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
        # The override as last seen in the options, so a later edit can be told
        # apart from the same value simply being present again.
        self._options_wh_per_percent: float | None = entry.options.get(
            OPT_WH_PER_PERCENT
        )

        self._meter = EnergyMeter()
        self._pending_calibration: dict[str, float] | None = None
        self._session_started_at: float | None = None

        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )
        self._cancel_idle: Callable[[], None] | None = None
        self._cancel_cap: Callable[[], None] | None = None
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
    def _idle_close_seconds(self) -> float:
        return (
            float(
                self.entry.options.get(
                    OPT_IDLE_CLOSE_MINUTES, DEFAULT_IDLE_CLOSE_MINUTES
                )
            )
            * 60.0
        )

    @property
    def _max_session_seconds(self) -> float:
        return (
            float(
                self.entry.options.get(
                    OPT_MAX_SESSION_HOURS, DEFAULT_MAX_SESSION_HOURS
                )
            )
            * 3600.0
        )

    @property
    def _staleness_seconds(self) -> float:
        return (
            float(
                self.entry.options.get(
                    OPT_SOC_STALENESS_HOURS, DEFAULT_SOC_STALENESS_HOURS
                )
            )
            * 3600.0
        )

    @property
    def session_delivered_wh(self) -> float:
        return self._meter.delivered_wh

    @property
    def projected_soc(self) -> float | None:
        if self.session_start_soc is None:
            return None
        return self.session_start_soc + self._meter.delivered_wh / self.wh_per_percent

    @property
    def power_entity_id(self) -> str | None:
        """The configured power meter, if there is one."""
        return self._power_entity

    @property
    def charge_power_w(self) -> float | None:
        """What the plug is drawing right now, or None while it is unreadable."""
        if self._power_entity is None:
            return None
        return _as_float(self.hass.states.get(self._power_entity))

    @property
    def plug_is_on(self) -> bool | None:
        """The real plug's state, or None while it cannot be read.

        Read straight from the switch rather than inferred from the state
        machine: armed means watching, which says nothing about the relay.
        """
        state = self.hass.states.get(self._plug_switch)
        if state is None or state.state in _INVALID:
            return None
        return state.state == "on"

    # ---- lifecycle -----------------------------------------------------

    async def async_setup(self) -> None:
        """Restore any in-flight session, subscribe, and settle into a state."""
        if stored := await self._store.async_load():
            self._restore(stored)

        watched = [self._plug_switch, self._soc_entity]
        if self._power_entity:
            watched.append(self._power_entity)
        if self._energy_entity:
            watched.append(self._energy_entity)

        self._unsubscribes.append(
            async_track_state_change_event(self.hass, watched, self._handle_change)
        )
        self._unsubscribes.append(
            self.entry.add_update_listener(self._async_options_updated)
        )

        if not self.enabled:
            return

        if self.state in (ChargeState.CHARGING, ChargeState.UNCALIBRATED):
            # Resume, do not restart. Restarting would zero the watt-hour count
            # and overcharge by however much was already delivered.
            self._start_cap_timer()
            return

        await self._async_arm()

    async def async_shutdown(self) -> None:
        """Flush state and detach. Called on unload and on HA shutdown."""
        self._cancel_timers()
        for unsubscribe in self._unsubscribes:
            unsubscribe()
        self._unsubscribes.clear()
        await self._async_persist()

    def _restore(self, stored: dict[str, Any]) -> None:
        self.state = ChargeState(stored.get("state", ChargeState.IDLE))
        self.enabled = stored.get("enabled", True)
        self.wh_per_percent = stored.get("wh_per_percent", self._seed)
        self.required_wh = stored.get("required_wh")
        self.session_start_soc = stored.get("session_start_soc")
        self._session_started_at = stored.get("session_started_at")
        self._pending_calibration = stored.get("pending_calibration")
        if meter := stored.get("meter"):
            self._meter = EnergyMeter.from_dict(meter)

    @callback
    def _store_data(self) -> dict[str, Any]:
        return {
            "state": str(self.state),
            "enabled": self.enabled,
            "wh_per_percent": self.wh_per_percent,
            "required_wh": self.required_wh,
            "session_start_soc": self.session_start_soc,
            "session_started_at": self._session_started_at,
            "pending_calibration": self._pending_calibration,
            "meter": self._meter.as_dict(),
        }

    async def _async_persist(self) -> None:
        await self._store.async_save(self._store_data())

    @callback
    def _persist_soon(self) -> None:
        """Debounced save for the chatty path - meter readings every few seconds."""
        self._store.async_delay_save(self._store_data, 10)

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
        """Set the target. The options listener applies it, open session included."""
        self.hass.config_entries.async_update_entry(
            self.entry, options={**self.entry.options, OPT_TARGET_SOC: value}
        )

    async def async_set_enabled(self, on: bool) -> None:
        """Master enable. Turning off leaves the plug exactly as it is."""
        self.enabled = on
        if on:
            await self._async_arm()
        else:
            self._cancel_timers()
            self._set_state(ChargeState.IDLE)
            await self._async_persist()

    async def async_set_plug(self, on: bool) -> None:
        """Manual override, authoritative in both directions.

        Only commands the plug; the state listener does the rest, so the
        outcome is identical whether the change came from here, from the
        plug's own entity, or from the physical button on the wall.
        """
        await self._async_switch_plug(on)

    async def _async_options_updated(
        self, _hass: HomeAssistant, _entry: ConfigEntry
    ) -> None:
        """Apply an options change in place, without reloading the entry.

        Every tuning option is read live off the entry, so a reload buys
        nothing. It actively costs: it takes the entities unavailable
        mid-charge and re-enters setup, which is a poor place to be holding a
        running session.
        """
        self._apply_wh_per_percent_option()
        self._restart_cap_timer()
        await self._async_recompute_required_wh()
        self._notify()

    @callback
    def _apply_wh_per_percent_option(self) -> None:
        """Adopt a manual override of the calibration, if one has just been set.

        Only on an actual change: re-applying the value already sitting in the
        options on every unrelated edit would claw back everything the sessions
        since have learned.
        """
        override = self.entry.options.get(OPT_WH_PER_PERCENT)
        if override == self._options_wh_per_percent:
            return
        self._options_wh_per_percent = override
        if override:
            self.wh_per_percent = float(override)

    async def _async_recompute_required_wh(self) -> None:
        """Re-measure an open session against the current target.

        The requirement is fixed at session open, so without this a target
        changed mid-charge would silently do nothing until the next session -
        and the charge would run on to the old target.

        An uncalibrated session is deliberately left alone: it has no starting
        point to measure from, which is exactly why it carries no limit.
        """
        if self.state is not ChargeState.CHARGING or self.session_start_soc is None:
            return

        self.required_wh = (
            self.target_soc - self.session_start_soc
        ) * self.wh_per_percent
        await self._async_persist()
        await self._async_check_target()

    # ---- event handling ------------------------------------------------

    async def _handle_change(self, event: Event[EventStateChangedData]) -> None:
        if not self.enabled:
            # No control while disabled, but the views still refresh: Charge
            # power mirrors a live entity and would otherwise sit frozen at
            # whatever it happened to read when control was turned off.
            self._notify()
            return

        entity_id = event.data["entity_id"]
        new_state = event.data["new_state"]

        if (
            self.state in (ChargeState.CHARGING, ChargeState.UNCALIBRATED)
            and not self._has_live_meter()
        ):
            # With no meter the count can never advance, so the target would
            # never be reached and the plug would stay live indefinitely. The
            # one case that deliberately fails toward undercharged.
            _LOGGER.warning("Lost every meter mid-session; cutting the plug")
            self._cancel_timers()
            self._set_state(ChargeState.STALLED)
            await self._async_switch_plug(False)
            await self._async_persist()
            return

        if entity_id == self._plug_switch:
            await self._async_handle_plug(new_state)
        elif entity_id == self._soc_entity:
            await self._async_handle_soc(_as_float(new_state))
        elif entity_id == self._power_entity:
            await self._async_handle_power(_as_float(new_state), event)
        elif entity_id == self._energy_entity:
            await self._async_handle_energy(new_state)

        self._notify()

    async def _async_handle_plug(self, new_state: Any) -> None:
        """React to the plug switching, whoever switched it."""
        if new_state is None or new_state.state in _INVALID:
            if self.state not in _PLUG_OFF_STATES:
                self._cancel_timers()
                self._set_state(ChargeState.STALLED)
                await self._async_persist()
            return

        if new_state.state == "off":
            if self.state not in (ChargeState.COMPLETE, ChargeState.STALLED):
                await self._async_stop()
        elif new_state.state == "on" and self.state in _PLUG_OFF_STATES:
            await self._async_arm()

    async def _async_handle_soc(self, soc: float | None) -> None:
        """A fresh state-of-charge reading arrived."""
        if soc is None:
            return

        self._apply_pending_calibration(soc)

        if (
            self.state in _PLUG_OFF_STATES
            and soc < self.target_soc - self._rearm_hysteresis
        ):
            await self._async_arm()

    async def _async_handle_power(
        self, power: float | None, event: Event[EventStateChangedData]
    ) -> None:
        if power is None:
            return

        if self.state is ChargeState.ARMED and power > self._power_threshold:
            await self._async_open_session()

        if self.state in (ChargeState.CHARGING, ChargeState.UNCALIBRATED):
            if power <= self._power_threshold:
                # A dip is not an ending; only sustained silence is.
                self._restart_idle_timer()
            elif self._cancel_idle is not None:
                self._cancel_idle()
                self._cancel_idle = None

        if self.state is ChargeState.CHARGING and self._energy_entity is None:
            self._meter.add_power_reading(power, event.time_fired.timestamp())
            self._persist_soon()
            await self._async_check_target()

    async def _async_handle_energy(self, state: Any) -> None:
        total = _as_float(state)
        if total is None or self.state is not ChargeState.CHARGING:
            return

        self._meter.add_energy_reading(_to_wh(state, total))
        self._persist_soon()
        await self._async_check_target()

    # ---- transitions ---------------------------------------------------

    async def _async_arm(self) -> None:
        """Watch for the next charge. Deliberately does not energise the plug.

        Arming is readiness, not an instruction to start charging. Only the
        owner closes the relay - through the Plug switch, the button on the
        plug, or the vendor app - and all three arrive here the same way.
        """
        self._cancel_timers()
        self.required_wh = None
        self.session_start_soc = None
        self._session_started_at = None
        self._set_state(ChargeState.ARMED)
        await self._async_persist()

    async def _async_open_session(self) -> None:
        """Power crossed the threshold: record where we are starting from."""
        soc_state = self.hass.states.get(self._soc_entity)
        soc = _as_float(soc_state)
        age = (
            (dt_util.utcnow() - soc_state.last_updated).total_seconds()
            if soc_state is not None
            else None
        )

        self._session_started_at = dt_util.utcnow().timestamp()
        self._start_cap_timer()

        if soc is None or age is None or age > self._staleness_seconds:
            # A stale reading most likely means the device has been ridden
            # since, so the true charge is lower than recorded. Applying the
            # limit anyway would cut early and leave it short, so don't apply
            # one at all and let the BMS end the charge.
            self._meter.start(None)
            self.required_wh = None
            self.session_start_soc = None
            self._set_state(ChargeState.UNCALIBRATED)
            await self._async_persist()
            return

        if soc >= self.target_soc:
            self._cancel_timers()
            self._set_state(ChargeState.COMPLETE)
            await self._async_switch_plug(False)
            await self._async_persist()
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
        await self._async_persist()

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
        self._cancel_timers()
        self._set_state(ChargeState.COMPLETE)
        await self._async_switch_plug(False)
        await self._async_persist()

    async def _async_stop(self) -> None:
        """Ended by hand. No calibration: the session was cut short."""
        self._cancel_timers()
        self._pending_calibration = None
        self.required_wh = None
        self.session_start_soc = None
        self._session_started_at = None
        self._set_state(ChargeState.STOPPED)
        await self._async_persist()

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

    # ---- timers --------------------------------------------------------

    def _cancel_timers(self) -> None:
        if self._cancel_idle is not None:
            self._cancel_idle()
            self._cancel_idle = None
        if self._cancel_cap is not None:
            self._cancel_cap()
            self._cancel_cap = None

    def _restart_cap_timer(self) -> None:
        """Re-arm the cap so a changed maximum length applies to this session."""
        if self.state not in (ChargeState.CHARGING, ChargeState.UNCALIBRATED):
            return
        if self._cancel_cap is not None:
            self._cancel_cap()
            self._cancel_cap = None
        self._start_cap_timer()

    def _start_cap_timer(self) -> None:
        if self._cancel_cap is not None:
            return
        remaining = self._max_session_seconds
        if self._session_started_at is not None:
            elapsed = dt_util.utcnow().timestamp() - self._session_started_at
            remaining = max(0.0, self._max_session_seconds - elapsed)
        self._cancel_cap = async_call_later(self.hass, remaining, self._on_cap)

    @callback
    def _on_cap(self, _now: Any) -> None:
        self._cancel_cap = None
        self.hass.async_create_task(self._async_cap_fired())

    async def _async_cap_fired(self) -> None:
        _LOGGER.warning("Charge session exceeded its maximum length; cutting the plug")
        self._cancel_timers()
        self._set_state(ChargeState.STALLED)
        await self._async_switch_plug(False)
        await self._async_persist()

    def _restart_idle_timer(self) -> None:
        if self._cancel_idle is not None:
            self._cancel_idle()
        self._cancel_idle = async_call_later(
            self.hass, self._idle_close_seconds, self._on_idle
        )

    @callback
    def _on_idle(self, _now: Any) -> None:
        self._cancel_idle = None
        self.hass.async_create_task(self._async_idle_fired())

    async def _async_idle_fired(self) -> None:
        """Sustained silence means the session really ended."""
        if self.state is ChargeState.UNCALIBRATED:
            # No cutoff was ever computed, so the BMS ended it. Nothing to
            # learn from: the starting charge was never known.
            self._cancel_timers()
            self._set_state(ChargeState.COMPLETE)
            await self._async_switch_plug(False)
            await self._async_persist()
        elif self.state is ChargeState.CHARGING:
            # Interrupted before target, so no calibration. Just re-arm.
            self._pending_calibration = None
            await self._async_arm()
        self._notify()

    # ---- helpers -------------------------------------------------------

    def _has_live_meter(self) -> bool:
        """True while at least one meter is still readable."""
        return any(
            entity_id and _as_float(self.hass.states.get(entity_id)) is not None
            for entity_id in (self._power_entity, self._energy_entity)
        )

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
