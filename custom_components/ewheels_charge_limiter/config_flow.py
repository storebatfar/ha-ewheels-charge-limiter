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
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_CAPACITY_WH,
    CONF_ENERGY_ENTITY,
    CONF_PLUG_DEVICE,
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
from .plug_resolver import resolve_plug_entities

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME, default="Scooter"): selector.TextSelector(),
        vol.Required(CONF_PLUG_DEVICE): selector.DeviceSelector(),
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


def _entities_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Schema for the fallback step, pre-filled with whatever we resolved."""
    return vol.Schema(
        {
            vol.Required(
                CONF_PLUG_SWITCH,
                default=defaults.get(CONF_PLUG_SWITCH, vol.UNDEFINED),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="switch")
            ),
            vol.Optional(
                CONF_POWER_ENTITY,
                default=defaults.get(CONF_POWER_ENTITY, vol.UNDEFINED),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="power")
            ),
            vol.Optional(
                CONF_ENERGY_ENTITY,
                default=defaults.get(CONF_ENERGY_ENTITY, vol.UNDEFINED),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="energy")
            ),
        }
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
    """Handle a config flow."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the plug, the state-of-charge sensor and the capacity."""
        if user_input is None:
            return self.async_show_form(step_id="user", data_schema=USER_SCHEMA)

        await self.async_set_unique_id(user_input[CONF_PLUG_DEVICE])
        self._abort_if_unique_id_configured()

        self._data = dict(user_input)

        resolved = resolve_plug_entities(self.hass, user_input[CONF_PLUG_DEVICE])
        self._data[CONF_PLUG_SWITCH] = resolved.switch
        self._data[CONF_POWER_ENTITY] = resolved.power
        self._data[CONF_ENERGY_ENTITY] = resolved.energy

        if not resolved.is_complete:
            return await self.async_step_entities()

        return self._create()

    async def async_step_entities(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for entities the resolver could not pin down."""
        if user_input is None:
            defaults = {
                key: value
                for key, value in self._data.items()
                if key in (CONF_PLUG_SWITCH, CONF_POWER_ENTITY, CONF_ENERGY_ENTITY)
                and value is not None
            }
            return self.async_show_form(
                step_id="entities", data_schema=_entities_schema(defaults)
            )

        if not user_input.get(CONF_POWER_ENTITY) and not user_input.get(
            CONF_ENERGY_ENTITY
        ):
            return self.async_show_form(
                step_id="entities",
                data_schema=_entities_schema(user_input),
                errors={"base": "no_meter"},
            )

        self._data[CONF_PLUG_SWITCH] = user_input[CONF_PLUG_SWITCH]
        self._data[CONF_POWER_ENTITY] = user_input.get(CONF_POWER_ENTITY)
        self._data[CONF_ENERGY_ENTITY] = user_input.get(CONF_ENERGY_ENTITY)
        return self._create()

    def _create(self) -> ConfigFlowResult:
        return self.async_create_entry(
            title=self._data[CONF_NAME],
            data=self._data,
            options=_default_options(),
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
