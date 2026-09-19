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
    }


class EWheelsChargeLimiterConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow.

    Everything is asked for explicitly on one screen. An earlier design picked
    a plug *device* and inferred its entities, but that made the device a
    redundant extra concept and, worse, hid which switch had been chosen to
    cut mains. Naming the entities outright is shorter and honest.
    """

    VERSION = 1

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
    """Handle the options flow."""

    async def async_step_init(
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
                vol.Optional(
                    OPT_WH_PER_PERCENT,
                    description={"suggested_value": options.get(OPT_WH_PER_PERCENT)},
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0.1,
                        max=1000,
                        step=0.1,
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
