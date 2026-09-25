"""Config and options flow for the E-Wheels Charge Limiter."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector

from .const import (
    BAND_OPTION_KEYS,
    CONF_ALLOW_FOREIGN_METER,
    CONF_CAPACITY_WH,
    CONF_ENERGY_ENTITY,
    CONF_PLUG_SWITCH,
    CONF_POWER_ENTITY,
    CONF_SOC_ENTITY,
    DEFAULT_CHARGING_POWER_THRESHOLD,
    DEFAULT_IDLE_CLOSE_MINUTES,
    DEFAULT_MAX_SESSION_HOURS,
    DEFAULT_REARM_HYSTERESIS,
    DEFAULT_REST_MINUTES,
    DEFAULT_SOC_STALENESS_HOURS,
    DEFAULT_TARGET_SOC,
    DOMAIN,
    OPT_CHARGING_POWER_THRESHOLD,
    OPT_FORGET_CHARGES,
    OPT_IDLE_CLOSE_MINUTES,
    OPT_MAX_SESSION_HOURS,
    OPT_REARM_HYSTERESIS,
    OPT_REST_MINUTES,
    OPT_SOC_STALENESS_HOURS,
    OPT_TARGET_SOC,
)

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME, default="Scooter"): selector.TextSelector(),
        vol.Required(CONF_PLUG_SWITCH): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="switch")
        ),
        vol.Optional(CONF_POWER_ENTITY): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor", device_class="power")
        ),
        vol.Optional(CONF_ENERGY_ENTITY): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor", device_class="energy")
        ),
        vol.Required(CONF_SOC_ENTITY): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor", device_class="battery")
        ),
        vol.Required(CONF_CAPACITY_WH, default=720): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=50,
                max=20000,
                step=10,
                unit_of_measurement="Wh",
                mode=selector.NumberSelectorMode.BOX,
            )
        ),
    }
)


def _device_of(hass: HomeAssistant, entity_id: str | None) -> str | None:
    """The device an entity belongs to, or None if it has none."""
    if not entity_id:
        return None
    entry = er.async_get(hass).async_get(entity_id)
    return entry.device_id if entry else None


def _meter_on_another_device(
    hass: HomeAssistant,
    plug_switch: str,
    power: str | None,
    energy: str | None,
) -> bool:
    """True when a chosen meter demonstrably belongs to a different device.

    A meter that is not measuring the plug it switches counts watt-hours that
    have nothing to do with the charger, so the cutoff fires at an arbitrary
    point. Only flagged when both devices are known and differ: template and
    helper sensors have no device, and a separate clamp meter is a legitimate
    setup, so an unknown device is never treated as wrong.
    """
    switch_device = _device_of(hass, plug_switch)
    if switch_device is None:
        return False

    return any(
        (meter_device := _device_of(hass, meter)) is not None
        and meter_device != switch_device
        for meter in (power, energy)
    )


def _default_options() -> dict[str, Any]:
    return {
        OPT_TARGET_SOC: DEFAULT_TARGET_SOC,
        OPT_REARM_HYSTERESIS: DEFAULT_REARM_HYSTERESIS,
        OPT_CHARGING_POWER_THRESHOLD: DEFAULT_CHARGING_POWER_THRESHOLD,
        OPT_IDLE_CLOSE_MINUTES: DEFAULT_IDLE_CLOSE_MINUTES,
        OPT_MAX_SESSION_HOURS: DEFAULT_MAX_SESSION_HOURS,
        OPT_SOC_STALENESS_HOURS: DEFAULT_SOC_STALENESS_HOURS,
        OPT_REST_MINUTES: DEFAULT_REST_MINUTES,
    }


class EWheelsChargeLimiterConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow.

    Everything is asked for explicitly on one screen. An earlier design picked
    a plug *device* and inferred its entities, but that made the device a
    redundant extra concept and, worse, hid which switch had been chosen to
    cut mains. Naming the entities outright is shorter and honest.
    """

    VERSION = 2

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the plug entities, the battery, and its capacity."""
        errors: dict[str, str] = {}
        offer_override = False

        if user_input is not None:
            power = user_input.get(CONF_POWER_ENTITY)
            energy = user_input.get(CONF_ENERGY_ENTITY)

            if not power and not energy:
                # Without a meter there is nothing to count, so the watt-hour
                # projection could never terminate.
                errors["base"] = "no_meter"
            elif not user_input.get(CONF_ALLOW_FOREIGN_METER) and (
                _meter_on_another_device(
                    self.hass, user_input[CONF_PLUG_SWITCH], power, energy
                )
            ):
                errors["base"] = "meter_not_on_plug"
                offer_override = True
            else:
                # The switch is what we actually control, so it is the natural
                # identity for this entry.
                await self.async_set_unique_id(user_input[CONF_PLUG_SWITCH])
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=user_input[CONF_NAME],
                    data={
                        key: value
                        for key, value in user_input.items()
                        if key != CONF_ALLOW_FOREIGN_METER
                    },
                    options=_default_options(),
                )

        schema = USER_SCHEMA
        if offer_override:
            # Only surfaced once the mismatch has been pointed out, so the
            # normal path stays a plain form.
            schema = schema.extend(
                {
                    vol.Optional(
                        CONF_ALLOW_FOREIGN_METER, default=False
                    ): selector.BooleanSelector()
                }
            )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, user_input or {}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        entry: ConfigEntry,
    ) -> EWheelsChargeLimiterOptionsFlow:
        """Return the options flow."""
        return EWheelsChargeLimiterOptionsFlow()


class EWheelsChargeLimiterOptionsFlow(OptionsFlow):
    """Options: the tuning settings, and the vase's per-band starting points."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(step_id="init", menu_options=["settings", "vase"])

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit the tuning options."""
        if user_input is not None:
            return self.async_create_entry(
                data={**self.config_entry.options, **user_input}
            )

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    OPT_TARGET_SOC,
                    default=options.get(OPT_TARGET_SOC, DEFAULT_TARGET_SOC),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=50, max=100, step=1, unit_of_measurement="%"
                    )
                ),
                vol.Required(
                    OPT_REARM_HYSTERESIS,
                    default=options.get(
                        OPT_REARM_HYSTERESIS, DEFAULT_REARM_HYSTERESIS
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=50, step=1)
                ),
                vol.Required(
                    OPT_CHARGING_POWER_THRESHOLD,
                    default=options.get(
                        OPT_CHARGING_POWER_THRESHOLD,
                        DEFAULT_CHARGING_POWER_THRESHOLD,
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=100, step=1)
                ),
                vol.Required(
                    OPT_IDLE_CLOSE_MINUTES,
                    default=options.get(
                        OPT_IDLE_CLOSE_MINUTES, DEFAULT_IDLE_CLOSE_MINUTES
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=120, step=1)
                ),
                vol.Required(
                    OPT_MAX_SESSION_HOURS,
                    default=options.get(
                        OPT_MAX_SESSION_HOURS, DEFAULT_MAX_SESSION_HOURS
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=48, step=1)
                ),
                vol.Required(
                    OPT_SOC_STALENESS_HOURS,
                    default=options.get(
                        OPT_SOC_STALENESS_HOURS, DEFAULT_SOC_STALENESS_HOURS
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=168, step=1)
                ),
                vol.Required(
                    OPT_REST_MINUTES,
                    default=options.get(OPT_REST_MINUTES, DEFAULT_REST_MINUTES),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0, max=240, step=1)
                ),
            }
        )
        return self.async_show_form(step_id="settings", data_schema=schema)

    async def async_step_vase(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Type per-band starting points, or forget remembered charges.

        Fields are pre-filled with typed values only, never learned ones:
        pre-filling learned values would turn every one of them into a typed
        value the moment the form is saved.
        """
        coordinator = getattr(self.config_entry, "runtime_data", None)

        if user_input is not None:
            forget = user_input.pop(OPT_FORGET_CHARGES, False)
            options = {
                key: value
                for key, value in self.config_entry.options.items()
                if key not in BAND_OPTION_KEYS
            }
            for key in BAND_OPTION_KEYS:
                if user_input.get(key) is not None:
                    options[key] = float(user_input[key])
            if forget and coordinator is not None:
                await coordinator.async_forget_charges()
            return self.async_create_entry(data=options)

        options = self.config_entry.options
        fields: dict[Any, Any] = {
            vol.Optional(
                key, description={"suggested_value": options.get(key)}
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0.1,
                    max=1000,
                    step=0.01,
                    unit_of_measurement="Wh",
                    mode=selector.NumberSelectorMode.BOX,
                )
            )
            for key in BAND_OPTION_KEYS
        }
        fields[vol.Optional(OPT_FORGET_CHARGES, default=False)] = (
            selector.BooleanSelector()
        )
        learned = (
            " · ".join(
                f"{i * 10}–{i * 10 + 10} %: {value:.2f}"
                for i, value in enumerate(coordinator.bands)
            )
            if coordinator is not None
            else "not loaded yet"
        )
        return self.async_show_form(
            step_id="vase",
            data_schema=vol.Schema(fields),
            description_placeholders={"learned": learned},
        )
