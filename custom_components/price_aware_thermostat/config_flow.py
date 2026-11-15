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
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector
from homeassistant.util import dt as dt_util

from .backends import validate_backend
from .const import (
    CONF_AREA,
    CONF_BASE_TEMP,
    CONF_CHANGEOVER_MINUTES,
    CONF_COLD_TOLERANCE,
    CONF_CONTROL_TYPE,
    CONF_COOL_COMPENSATION,
    CONF_COOL_MIN_OUTDOOR,
    CONF_DEADBAND,
    CONF_FILTER,
    CONF_HEAT_COMPENSATION,
    CONF_HEAT_COOL_GAP,
    CONF_HEAT_MAX_OUTDOOR,
    CONF_HEATER,
    CONF_HOT_TOLERANCE,
    CONF_HVAC_MODES,
    CONF_LOOKAHEAD,
    CONF_MAX_DEVIATION,
    CONF_MAX_SENSOR_AGE,
    CONF_MAX_TEMP,
    CONF_MIN_CYCLE,
    CONF_MIN_TEMP,
    CONF_NAME,
    CONF_NORDPOOL_ENTRY,
    CONF_OUTDOOR_HYSTERESIS,
    CONF_OUTDOOR_SENSOR,
    CONF_SAFETY_ACK,
    CONF_SAFETY_ACK_AT,
    CONF_SENSITIVITY,
    CONF_SENSOR,
    CONF_SHADOW,
    CONF_WINDOW_CLOSE_DELAY,
    CONF_WINDOW_OPEN_DELAY,
    CONF_WINDOW_SENSORS,
    CONTROL_CLIMATE,
    CONTROL_SWITCH,
    DEFAULTS,
    DOMAIN,
    SETTINGS_URL,
)


def _number(minimum: float, maximum: float, step: float = 0.1) -> selector.NumberSelector:
    """Create a bounded numeric UI selector."""
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=minimum, max=maximum, step=step,
            mode=selector.NumberSelectorMode.BOX,
        )
    )


def _price_source_error(hass: HomeAssistant, user_input: dict[str, Any]) -> str | None:
    """Validate the Nord Pool entry and market area the same way in every flow."""
    source = hass.config_entries.async_get_entry(str(user_input[CONF_NORDPOOL_ENTRY]))
    area = _area(user_input)
    if source is None or source.domain != "nordpool":
        return "invalid_price_source"
    if not area or area not in {
        str(known_area).upper() for known_area in source.data.get("areas", ())
    }:
        return "invalid_market_area"
    return None


def _area(user_input: dict[str, Any]) -> str:
    """Normalize a market area code."""
    return str(user_input[CONF_AREA]).strip().upper()


def _price_source_schema(current: dict[str, Any]) -> dict[vol.Marker, Any]:
    """The Nord Pool price source and market area fields."""
    return {
        vol.Required(
            CONF_NORDPOOL_ENTRY, default=current.get(CONF_NORDPOOL_ENTRY)
        ): selector.ConfigEntrySelector(
            selector.ConfigEntrySelectorConfig(integration="nordpool")
        ),
        vol.Required(CONF_AREA, default=current.get(CONF_AREA, "")): selector.TextSelector(),
    }


def _deviation_default(initial: dict[str, Any]) -> float:
    """Prefill the deviation, treating a missing or empty value as the default."""
    value = initial.get(CONF_MAX_DEVIATION)
    return float(DEFAULTS[CONF_MAX_DEVIATION] if value is None else value)


def _settings_schema(
    initial: dict[str, Any], *, options: bool, min_cycle_floor: float = 0,
    ask_ack: bool = True,
) -> vol.Schema:
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
        # Required with a default: price adjustment is always relative to the
        # user's target. 0 disables price adjustment; it cannot be left empty.
        vol.Required(
            CONF_MAX_DEVIATION,
            default=_deviation_default(initial),
        ): _number(0, 20, 0.1),
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
        ): _number(min_cycle_floor, 3600, 30),
        vol.Required(
            CONF_MAX_SENSOR_AGE,
            default=initial.get(CONF_MAX_SENSOR_AGE, DEFAULTS[CONF_MAX_SENSOR_AGE]),
        ): _number(5, 360, 5),
    }
    if options:
        config[vol.Required(CONF_SHADOW, default=initial.get(CONF_SHADOW, True))] = (
            selector.BooleanSelector()
        )
        if ask_ack:
            config[vol.Optional(CONF_SAFETY_ACK, default=False)] = (
                selector.BooleanSelector()
            )
    return vol.Schema(config)


_AC_KEYS = (
    CONF_HEAT_COMPENSATION,
    CONF_COOL_COMPENSATION,
    CONF_WINDOW_SENSORS,
    CONF_WINDOW_OPEN_DELAY,
    CONF_WINDOW_CLOSE_DELAY,
    CONF_HVAC_MODES,
    CONF_OUTDOOR_SENSOR,
    CONF_COOL_MIN_OUTDOOR,
    CONF_HEAT_MAX_OUTDOOR,
    CONF_OUTDOOR_HYSTERESIS,
    CONF_HEAT_COOL_GAP,
    CONF_CHANGEOVER_MINUTES,
)

# Clearable optional AC fields and the value that means "cleared".
_CLEARABLE: dict[str, Any] = {
    CONF_WINDOW_SENSORS: [],
    CONF_OUTDOOR_SENSOR: None,
    CONF_COOL_MIN_OUTDOOR: None,
    CONF_HEAT_MAX_OUTDOOR: None,
}


def _suggested(initial: dict[str, Any], key: str) -> vol.Marker:
    """An optional, clearable field prefilled with its current value."""
    value = initial.get(key)
    return vol.Optional(
        key, description={"suggested_value": None if value in (None, [], "") else value}
    )


def _ac_schema(initial: dict[str, Any]) -> dict[vol.Marker, Any]:
    """Settings that only apply to the heat-pump/AC control type."""
    # Optional, clearable fields use a suggested value (UI prefill) rather than a
    # default: a default would silently refill a field the user emptied.
    return {
        vol.Optional(
            CONF_HEAT_COMPENSATION,
            default=initial.get(CONF_HEAT_COMPENSATION, DEFAULTS[CONF_HEAT_COMPENSATION]),
        ): _number(-10, 10, 0.5),
        vol.Optional(
            CONF_COOL_COMPENSATION,
            default=initial.get(CONF_COOL_COMPENSATION, DEFAULTS[CONF_COOL_COMPENSATION]),
        ): _number(-10, 10, 0.5),
        _suggested(initial, CONF_WINDOW_SENSORS): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="binary_sensor", multiple=True)
        ),
        vol.Optional(
            CONF_WINDOW_OPEN_DELAY,
            default=initial.get(CONF_WINDOW_OPEN_DELAY, DEFAULTS[CONF_WINDOW_OPEN_DELAY]),
        ): _number(0, 3600, 15),
        vol.Optional(
            CONF_WINDOW_CLOSE_DELAY,
            default=initial.get(CONF_WINDOW_CLOSE_DELAY, DEFAULTS[CONF_WINDOW_CLOSE_DELAY]),
        ): _number(0, 3600, 15),
        _suggested(initial, CONF_OUTDOOR_SENSOR): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor", device_class="temperature")
        ),
        _suggested(initial, CONF_COOL_MIN_OUTDOOR): _number(-40, 40, 0.5),
        _suggested(initial, CONF_HEAT_MAX_OUTDOOR): _number(-40, 40, 0.5),
        vol.Optional(
            CONF_OUTDOOR_HYSTERESIS,
            default=initial.get(CONF_OUTDOOR_HYSTERESIS, DEFAULTS[CONF_OUTDOOR_HYSTERESIS]),
        ): _number(0, 10, 0.1),
        # heat_cool (both heat and cool controlled): setpoint gap and changeover.
        vol.Optional(
            CONF_HEAT_COOL_GAP,
            default=initial.get(CONF_HEAT_COOL_GAP, DEFAULTS[CONF_HEAT_COOL_GAP]),
        ): _number(0.1, 10, 0.1),
        vol.Optional(
            CONF_CHANGEOVER_MINUTES,
            default=initial.get(CONF_CHANGEOVER_MINUTES, DEFAULTS[CONF_CHANGEOVER_MINUTES]),
        ): _number(0, 240, 5),
    }


def _invalid_settings(settings: dict[str, Any]) -> bool:
    """Validate cross-field limits independent of UI widgets."""
    try:
        minimum = float(settings[CONF_MIN_TEMP])
        base = float(settings[CONF_BASE_TEMP])
        maximum = float(settings[CONF_MAX_TEMP])
        raw = settings.get(CONF_MAX_DEVIATION, DEFAULTS[CONF_MAX_DEVIATION])
        if raw is None or isinstance(raw, bool):
            return True
        deviation = float(raw)
        return not (minimum <= base <= maximum and 0 <= deviation <= 20)
    except (KeyError, ValueError, TypeError):
        return True


class PriceAwareThermostatConfigFlow(ConfigFlow, domain=DOMAIN):
    """Configure an isolated price-aware thermostat."""

    VERSION = 2
    # Minor 2: maximum_price_deviation is always set (default 0.8 °C).
    # Minor 3: a custom climate registry name lives on the device's name_by_user.
    MINOR_VERSION = 3

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set up a temperature sensor, heater switch, and Nord Pool source."""
        errors: dict[str, str] = {}
        if user_input is not None:
            area = _area(user_input)
            if error := _price_source_error(self.hass, user_input):
                errors["base"] = error
            elif _invalid_settings(user_input):
                errors["base"] = "invalid_temperature_range"
            elif error := validate_backend(self.hass, user_input):
                errors["base"] = error
            else:
                # Prevent duplicate instances of this integration controlling
                # the same heater, including when installed in shadow mode.
                await self.async_set_unique_id(user_input[CONF_HEATER])
                self._abort_if_unique_id_configured()
                kind = user_input.get(CONF_CONTROL_TYPE, CONTROL_SWITCH)
                stored = {**user_input, CONF_AREA: area, CONF_CONTROL_TYPE: kind}
                if kind != CONTROL_CLIMATE:
                    # Heat-pump/AC settings do not apply to the switch type.
                    for key in _AC_KEYS:
                        stored.pop(key, None)
                return self.async_create_entry(
                    title=user_input[CONF_NAME], data=stored, options={CONF_SHADOW: True},
                )

        schema = {
            vol.Required(CONF_CONTROL_TYPE, default=CONTROL_SWITCH): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[CONTROL_SWITCH, CONTROL_CLIMATE], translation_key="control_type"
                )
            ),
            vol.Optional(CONF_HVAC_MODES, default=["heat"]): selector.SelectSelector(
                selector.SelectSelectorConfig(options=["heat", "cool"], multiple=True)
            ),
            vol.Required(CONF_NAME, default="Price-Aware Thermostat"): selector.TextSelector(),
            vol.Required(CONF_SENSOR): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="temperature")
            ),
            vol.Required(CONF_HEATER): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["switch", "climate"])
            ),
            vol.Required(CONF_NORDPOOL_ENTRY): selector.ConfigEntrySelector(
                selector.ConfigEntrySelectorConfig(integration="nordpool")
            ),
            vol.Required(CONF_AREA, default="SE4"): selector.TextSelector(),
        }
        schema.update(_settings_schema(user_input or {}, options=False).schema)
        schema.update(_ac_schema(user_input or {}))
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

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change only the Nord Pool source and market area.

        Allowed in live mode as well: the price source never commands the
        actuator, and a failing source falls back to the base target.
        """
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            if error := _price_source_error(self.hass, user_input):
                errors["base"] = error
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_NORDPOOL_ENTRY: user_input[CONF_NORDPOOL_ENTRY],
                        CONF_AREA: _area(user_input),
                    },
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(_price_source_schema(user_input or dict(entry.data))),
            errors=errors,
        )


class PriceAwareThermostatOptionsFlow(OptionsFlowWithReload):
    """Manage settings and require safety confirmation before relay control."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Update configurable settings."""
        errors: dict[str, str] = {}
        values = {**self.config_entry.data, **self.config_entry.options}
        climate_backend = values.get(CONF_CONTROL_TYPE) == CONTROL_CLIMATE
        already_live = not values.get(CONF_SHADOW, True)
        if user_input is not None:
            merged = {**values, **user_input}
            # The acknowledgement covers one live actuator. It is asked on the
            # shadow -> live transition and again if the actuator changes;
            # an entry already live (including one saved before it was
            # persisted, when every live save forced the tick) is acknowledged.
            needs_ack = not merged[CONF_SHADOW] and not (
                already_live and merged.get(CONF_HEATER) == values.get(CONF_HEATER)
            )
            source_changed = (
                merged.get(CONF_NORDPOOL_ENTRY) != values.get(CONF_NORDPOOL_ENTRY)
                or (CONF_AREA in merged and _area(merged) != values.get(CONF_AREA))
            )
            # Only a changed source is validated: an unchanged, broken source is
            # already surfaced as a repair and must not block e.g. enabling shadow.
            if source_changed and (error := _price_source_error(self.hass, merged)):
                errors["base"] = error
            elif _invalid_settings(merged):
                errors["base"] = "invalid_temperature_range"
            elif CONF_HEATER in merged and (error := validate_backend(
                self.hass, merged, entry_id=self.config_entry.entry_id
            )):
                errors["base"] = error
            elif merged.get(CONF_HEATER) != values.get(CONF_HEATER) and (
                not values.get(CONF_SHADOW, True)
                or not merged.get(CONF_SHADOW, True)
                or (not climate_backend and (
                    (old := self.hass.states.get(values[CONF_HEATER])) is None
                    or old.state != "off"
                ))
            ):
                errors["base"] = "actuator_handoff_required"
            elif needs_ack and not user_input.get(CONF_SAFETY_ACK):
                errors["base"] = "safety_confirmation_required"
            else:
                # Retain persisted settings omitted by the form, including base target.
                updated = {**self.config_entry.options, **user_input}
                updated.pop(CONF_SAFETY_ACK, None)
                # The price source lives in entry data (shared with reconfigure),
                # never in options, so there is a single source of truth.
                updated.pop(CONF_NORDPOOL_ENTRY, None)
                updated.pop(CONF_AREA, None)
                if source_changed:
                    self.hass.config_entries.async_update_entry(
                        self.config_entry,
                        data={
                            **self.config_entry.data,
                            CONF_NORDPOOL_ENTRY: merged[CONF_NORDPOOL_ENTRY],
                            CONF_AREA: _area(merged),
                        },
                    )
                if updated[CONF_SHADOW]:
                    # Back in shadow: the next live switch must ask again.
                    updated.pop(CONF_SAFETY_ACK_AT, None)
                elif needs_ack:
                    updated[CONF_SAFETY_ACK] = True
                    updated[CONF_SAFETY_ACK_AT] = dt_util.utcnow().isoformat()
                else:
                    # Already live: keep (or, for legacy entries, record) the ack.
                    updated[CONF_SAFETY_ACK] = True
                if climate_backend:
                    # An omitted optional field means "cleared"; write it explicitly so
                    # a value saved at setup (in entry data) cannot silently persist.
                    for key, cleared in _CLEARABLE.items():
                        if key not in user_input:
                            updated[key] = cleared
                if updated.get(CONF_HEATER, values.get(CONF_HEATER)) != values.get(CONF_HEATER):
                    # Entry/entity identity remains stable; actuator deduplication
                    # must follow a deliberately changed physical target.
                    self.hass.config_entries.async_update_entry(
                        self.config_entry, unique_id=updated[CONF_HEATER]
                    )
                return self.async_create_entry(data=updated)

        # Entries always carry a price source; guard for hand-built minimal entries.
        schema = (
            _price_source_schema({**values, **(user_input or {})})
            if CONF_NORDPOOL_ENTRY in values else {}
        )
        schema.update(_settings_schema(
            values, options=True, min_cycle_floor=60 if climate_backend else 0,
            ask_ack=not already_live,
        ).schema)
        if CONF_HEATER in values:
            kind = values.get(CONF_CONTROL_TYPE, CONTROL_SWITCH)
            schema.update({
                vol.Required(CONF_SENSOR, default=values[CONF_SENSOR]): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor", device_class="temperature")
                ),
                vol.Required(CONF_HEATER, default=values[CONF_HEATER]): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=kind)
                ),
            })
            if climate_backend:
                schema[vol.Required(CONF_HVAC_MODES, default=values.get(CONF_HVAC_MODES, ["heat"]))] = (
                    selector.SelectSelector(
                        selector.SelectSelectorConfig(options=["heat", "cool"], multiple=True)
                    )
                )
                schema.update(_ac_schema(values))
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(schema),
            errors=errors,
            description_placeholders={"settings_url": SETTINGS_URL},
        )
