"""UI configuration for Price-Aware Thermostat."""

from __future__ import annotations

from typing import Any

import probatio as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_AREA,
    CONF_BASE_TEMP,
    CONF_COLD_TOLERANCE,
    CONF_DEADBAND,
    CONF_FILTER,
    CONF_HEATER,
    CONF_HOT_TOLERANCE,
    CONF_LOOKAHEAD,
    CONF_MAX_TEMP,
    CONF_MIN_CYCLE,
    CONF_MIN_TEMP,
    CONF_NAME,
    CONF_NORDPOOL_ENTRY,
    CONF_SAFETY_ACK,
    CONF_SENSITIVITY,
    CONF_SENSOR,
    CONF_SHADOW,
    DEFAULTS,
    DOMAIN,
)


def _number(minimum: float, maximum: float, step: float = 0.1) -> selector.NumberSelector:
    """Create a bounded numeric UI selector."""
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=minimum, max=maximum, step=step,
            mode=selector.NumberSelectorMode.BOX,
        )
    )


def _settings_schema(initial: dict[str, Any], *, options: bool) -> vol.Schema:
    """Build the shared optimizer/thermostat settings."""
    config: dict[vol.Marker, Any] = {
        vol.Required(
            CONF_BASE_TEMP, default=initial.get(CONF_BASE_TEMP, DEFAULTS[CONF_BASE_TEMP])
        ): _number(-20, 40),
        vol.Required(
            CONF_MIN_TEMP, default=initial.get(CONF_MIN_TEMP, DEFAULTS[CONF_MIN_TEMP])
        ): _number(-20, 40),
        vol.Required(
            CONF_MAX_TEMP, default=initial.get(CONF_MAX_TEMP, DEFAULTS[CONF_MAX_TEMP])
        ): _number(-20, 40),
        vol.Required(
            CONF_LOOKAHEAD, default=initial.get(CONF_LOOKAHEAD, DEFAULTS[CONF_LOOKAHEAD])
        ): _number(15, 1440, 15),
        vol.Required(
            CONF_FILTER, default=initial.get(CONF_FILTER, DEFAULTS[CONF_FILTER])
        ): selector.SelectSelector(
            selector.SelectSelectorConfig(options=["rectangle", "triangle"])
        ),
        vol.Required(
            CONF_DEADBAND, default=initial.get(CONF_DEADBAND, DEFAULTS[CONF_DEADBAND])
        ): _number(0, 20, 0.05),
        vol.Required(
            CONF_SENSITIVITY, default=initial.get(CONF_SENSITIVITY, DEFAULTS[CONF_SENSITIVITY])
        ): _number(0, 20, 0.1),
        vol.Required(
            CONF_COLD_TOLERANCE,
            default=initial.get(CONF_COLD_TOLERANCE, DEFAULTS[CONF_COLD_TOLERANCE]),
        ): _number(0, 5),
        vol.Required(
            CONF_HOT_TOLERANCE,
            default=initial.get(CONF_HOT_TOLERANCE, DEFAULTS[CONF_HOT_TOLERANCE]),
        ): _number(0, 5),
        vol.Required(
            CONF_MIN_CYCLE, default=initial.get(CONF_MIN_CYCLE, DEFAULTS[CONF_MIN_CYCLE])
        ): _number(0, 3600, 30),
    }
    if options:
        config[vol.Required(CONF_SHADOW, default=initial.get(CONF_SHADOW, True))] = (
            selector.BooleanSelector()
        )
        config[vol.Optional(CONF_SAFETY_ACK, default=False)] = (
            selector.BooleanSelector()
        )
    return vol.Schema(config)


def _invalid_settings(settings: dict[str, Any]) -> bool:
    """Validate cross-field limits independent of UI widgets."""
    try:
        minimum = float(settings[CONF_MIN_TEMP])
        base = float(settings[CONF_BASE_TEMP])
        maximum = float(settings[CONF_MAX_TEMP])
        return not minimum <= base <= maximum
    except (KeyError, ValueError, TypeError):
        return True


class PriceAwareThermostatConfigFlow(ConfigFlow, domain=DOMAIN):
    """Configure an isolated price-aware thermostat."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set up a temperature sensor, heater switch, and Nord Pool source."""
        errors: dict[str, str] = {}
        if user_input is not None:
            source = self.hass.config_entries.async_get_entry(
                user_input[CONF_NORDPOOL_ENTRY]
            )
            area = str(user_input[CONF_AREA]).strip().upper()
            if source is None or source.domain != "nordpool":
                errors["base"] = "invalid_price_source"
            elif not area or area not in {
                str(known_area).upper() for known_area in source.data.get("areas", ())
            }:
                errors["base"] = "invalid_market_area"
            elif _invalid_settings(user_input):
                errors["base"] = "invalid_temperature_range"
            elif user_input[CONF_HEATER] == user_input[CONF_SENSOR]:
                errors["base"] = "invalid_entities"
            else:
                # Prevent duplicate instances of this integration controlling
                # the same heater, including when installed in shadow mode.
                await self.async_set_unique_id(user_input[CONF_HEATER])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input[CONF_NAME],
                    data={**user_input, CONF_AREA: area},
                    options={CONF_SHADOW: True},
                )

        schema = {
            vol.Required(CONF_NAME, default="Price-Aware Thermostat"): selector.TextSelector(),
            vol.Required(CONF_SENSOR): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="temperature")
            ),
            vol.Required(CONF_HEATER): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="switch")
            ),
            vol.Required(CONF_NORDPOOL_ENTRY): selector.ConfigEntrySelector(
                selector.ConfigEntrySelectorConfig(integration="nordpool")
            ),
            vol.Required(CONF_AREA, default="SE4"): selector.TextSelector(),
        }
        schema.update(_settings_schema(user_input or {}, options=False).schema)
        return self.async_show_form(
            step_id="user", data_schema=vol.Schema(schema), errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> PriceAwareThermostatOptionsFlow:
        """Offer a settings flow with a safe default of shadow mode."""
        return PriceAwareThermostatOptionsFlow()


class PriceAwareThermostatOptionsFlow(OptionsFlowWithReload):
    """Manage settings and require safety confirmation before relay control."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Update configurable settings."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if _invalid_settings(user_input):
                errors["base"] = "invalid_temperature_range"
            elif not user_input[CONF_SHADOW] and not user_input.get(CONF_SAFETY_ACK):
                errors["base"] = "safety_confirmation_required"
            else:
                # Confirmation is one-shot, not a persistent setting.
                user_input.pop(CONF_SAFETY_ACK, None)
                return self.async_create_entry(data=user_input)

        values = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(
            step_id="init",
            data_schema=_settings_schema(values, options=True),
            errors=errors,
        )
