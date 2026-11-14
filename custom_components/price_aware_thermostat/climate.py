"""A price-aware on/off climate entity, starting safely in shadow mode."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from math import isfinite
from typing import Any

from homeassistant.components.climate import (
    ATTR_TEMPERATURE,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
    RestoreClimate,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfTemperature,
)
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import (
    CONF_BASE_TEMP,
    CONF_COLD_TOLERANCE,
    CONF_DEADBAND,
    CONF_FILTER,
    CONF_HEATER,
    CONF_HOT_TOLERANCE,
    CONF_LOOKAHEAD,
    CONF_MAX_SENSOR_AGE,
    CONF_MAX_TEMP,
    CONF_MIN_CYCLE,
    CONF_MIN_TEMP,
    CONF_SENSITIVITY,
    CONF_SENSOR,
    CONF_SHADOW,
    DEFAULTS,
)
from .control import (
    desired_heater_state,
    is_temperature_sample_fresh,
    parse_temperature_celsius,
)
from .coordinator import NordPoolPriceCoordinator
from .optimizer import Filter, OptimizationResult, OptimizerConfig, optimize

_LOGGER = logging.getLogger(__name__)
PRESET_PRICE_AWARE = "price_aware"
PRESET_FIXED = "fixed"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Register one climate entity per integration entry."""
    coordinator: NordPoolPriceCoordinator = entry.runtime_data
    async_add_entities([PriceAwareThermostat(entry, coordinator)])


class PriceAwareThermostat(RestoreClimate):
    """Local thermostat that can use future electricity prices to shift heat."""

    _attr_should_poll = False
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.PRESET_MODE
    )
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]
    _attr_preset_modes = [PRESET_PRICE_AWARE, PRESET_FIXED]
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_native_target_temperature_step = 0.1

    def __init__(
        self, entry: ConfigEntry, coordinator: NordPoolPriceCoordinator
    ) -> None:
        """Configure the thermostat without writing to any switches."""
        self._entry = entry
        self._coordinator = coordinator
        settings = {**entry.data, **entry.options}
        self._sensor = str(settings[CONF_SENSOR])
        self._heater = str(settings[CONF_HEATER])
        self._shadow = bool(settings.get(CONF_SHADOW, True))
        self._min_cycle = int(settings.get(CONF_MIN_CYCLE, DEFAULTS[CONF_MIN_CYCLE]))
        self._max_sensor_age = timedelta(
            minutes=int(settings.get(CONF_MAX_SENSOR_AGE, DEFAULTS[CONF_MAX_SENSOR_AGE]))
        )
        self._cold_tolerance = float(
            settings.get(CONF_COLD_TOLERANCE, DEFAULTS[CONF_COLD_TOLERANCE])
        )
        self._hot_tolerance = float(
            settings.get(CONF_HOT_TOLERANCE, DEFAULTS[CONF_HOT_TOLERANCE])
        )
        self._attr_unique_id = entry.entry_id
        self._attr_name = entry.title
        self._attr_min_temp = float(settings.get(CONF_MIN_TEMP, DEFAULTS[CONF_MIN_TEMP]))
        self._attr_max_temp = float(settings.get(CONF_MAX_TEMP, DEFAULTS[CONF_MAX_TEMP]))
        self._attr_native_target_temperature = float(
            settings.get(CONF_BASE_TEMP, DEFAULTS[CONF_BASE_TEMP])
        )
        self._attr_hvac_mode = HVACMode.HEAT
        self._attr_preset_mode = PRESET_PRICE_AWARE
        self._attr_hvac_action = HVACAction.IDLE
        self._last_result: OptimizationResult | None = None
        self._planned_heating: bool | None = None
        self._sensor_fresh = False
        self._control_lock = asyncio.Lock()

    @property
    def _base_target(self) -> float:
        """Read the active base target with a typed startup fallback."""
        configured = self._attr_native_target_temperature
        return configured if configured is not None else float(DEFAULTS[CONF_BASE_TEMP])

    def _calculate(self) -> OptimizationResult:
        """Calculate an effective temperature; errors never actuate a heater."""
        settings = {**self._entry.data, **self._entry.options}
        raw_filter = settings.get(CONF_FILTER, DEFAULTS[CONF_FILTER])
        if raw_filter == "rectangle":
            filter_type: Filter = "rectangle"
        elif raw_filter == "triangle":
            filter_type = "triangle"
        else:
            raise ValueError("Unsupported price filter")
        config = OptimizerConfig(
            base_temperature=self._base_target,
            minimum_temperature=self._attr_min_temp,
            maximum_temperature=self._attr_max_temp,
            lookahead_minutes=int(settings.get(CONF_LOOKAHEAD, DEFAULTS[CONF_LOOKAHEAD])),
            filter_type=filter_type,
            deadband_sek_per_kwh=float(
                settings.get(CONF_DEADBAND, DEFAULTS[CONF_DEADBAND])
            ),
            sensitivity_c_per_sek_per_kwh=float(
                settings.get(CONF_SENSITIVITY, DEFAULTS[CONF_SENSITIVITY])
            ),
        )
        return optimize(dt_util.utcnow(), self._coordinator.data or (), config)

    @property
    def extra_state_attributes(self) -> dict[str, str | float | bool | None]:
        """Expose the reason for decisions without excessive state data."""
        result = self._last_result
        return {
            "shadow_mode": self._shadow,
            "temperature_sample_fresh": self._sensor_fresh,
            "effective_target_c": (
                result.effective_target_c if result else self._attr_native_target_temperature
            ),
            "price_now_sek_per_kwh": result.current_price_sek_per_kwh if result else None,
            "weighted_future_price_sek_per_kwh": (
                result.future_weighted_price_sek_per_kwh if result else None
            ),
            "price_difference_sek_per_kwh": (
                result.price_difference_sek_per_kwh if result else None
            ),
            "future_coverage_fraction": result.future_coverage_fraction if result else 0.0,
            "optimization_reason": result.reason if result else "price_unavailable",
            "planned_heating": self._planned_heating,
        }

    @callback
    def _queue_evaluation(self, *_args: Any) -> None:
        """Schedule one async evaluation, serialized by a lock."""
        self.hass.async_create_task(self._async_evaluate())

    @callback
    def _state_changed(self, event: Event[EventStateChangedData]) -> None:
        """Re-evaluate on sensor and switch state changes."""
        self._queue_evaluation()

    async def async_added_to_hass(self) -> None:
        """Subscribe to updates; restore only explicit HVAC/preset choices."""
        await super().async_added_to_hass()
        old = await self.async_get_last_state()
        if old is not None:
            if old.state == HVACMode.OFF:
                self._attr_hvac_mode = HVACMode.OFF
            if old.attributes.get("preset_mode") == PRESET_FIXED:
                self._attr_preset_mode = PRESET_FIXED

        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self._sensor, self._heater], self._state_changed
            )
        )
        self.async_on_remove(
            async_track_time_interval(
                self.hass, self._queue_evaluation, timedelta(minutes=1)
            )
        )
        self.async_on_remove(self._coordinator.async_add_listener(self._queue_evaluation))
        await self._async_evaluate()

    async def _async_evaluate(self) -> None:
        """Evaluate temperature and synchronize only when live control is enabled."""
        async with self._control_lock:
            previous = (
                self._attr_native_current_temperature,
                self._attr_hvac_action,
                self._last_result,
                self._planned_heating,
                self._sensor_fresh,
            )
            temp_state = self.hass.states.get(self._sensor)
            heater_state = self.hass.states.get(self._heater)
            self._attr_native_current_temperature = None
            self._sensor_fresh = (
                temp_state is not None
                and is_temperature_sample_fresh(
                    getattr(temp_state, "last_reported", None),
                    dt_util.utcnow(),
                    self._max_sensor_age,
                )
            )
            if self._sensor_fresh and temp_state is not None and temp_state.state not in (
                STATE_UNKNOWN,
                STATE_UNAVAILABLE,
            ):
                unit = temp_state.attributes.get("unit_of_measurement")
                self._attr_native_current_temperature = parse_temperature_celsius(
                    temp_state.state, unit if isinstance(unit, str) else None
                )
            self._sensor_fresh = (
                self._sensor_fresh and self._attr_native_current_temperature is not None
            )

            try:
                self._last_result = self._calculate()
            except ValueError:
                _LOGGER.warning("Invalid price data; using the configured base temperature")
                self._last_result = OptimizationResult(
                    effective_target_c=self._base_target,
                    reason="price_unavailable",
                    current_price_sek_per_kwh=None,
                    future_weighted_price_sek_per_kwh=None,
                    price_difference_sek_per_kwh=None,
                    future_coverage_fraction=0.0,
                )
            effective_target = (
                self._last_result.effective_target_c
                if self._attr_preset_mode == PRESET_PRICE_AWARE
                else self._base_target
            )
            currently_on = heater_state is not None and heater_state.state == STATE_ON
            self._attr_hvac_action = (
                HVACAction.HEATING if currently_on else HVACAction.IDLE
            )
            if self._attr_hvac_mode == HVACMode.OFF:
                desired: bool | None = False
            else:
                desired = desired_heater_state(
                    self._attr_native_current_temperature,
                    effective_target,
                    currently_on,
                    self._cold_tolerance,
                    self._hot_tolerance,
                )
            self._planned_heating = desired

            # In shadow mode *no switch services are called*, even if the sensor
            # is missing or the user explicitly selects HVAC off.
            if not self._shadow and heater_state is not None and heater_state.state in (
                STATE_ON, STATE_OFF,
            ):
                # No temperature reading: stop heating rather than applying a
                # possibly stale actuator state. Hardware frost protection must
                # remain an independent safety layer.
                if desired is None:
                    desired = False
                if desired != currently_on:
                    forced_off = (
                        self._attr_hvac_mode == HVACMode.OFF
                        or self._attr_native_current_temperature is None
                    )
                    age = dt_util.utcnow() - heater_state.last_changed
                    if forced_off or age >= timedelta(seconds=self._min_cycle):
                        service = "turn_on" if desired else "turn_off"
                        try:
                            await self.hass.services.async_call(
                                "switch", service,
                                {"entity_id": self._heater},
                                blocking=True,
                            )
                        except HomeAssistantError:
                            _LOGGER.exception("Could not change the heating relay")
            updated = (
                self._attr_native_current_temperature,
                self._attr_hvac_action,
                self._last_result,
                self._planned_heating,
                self._sensor_fresh,
            )
            if previous != updated:
                self.async_write_ha_state()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Update the base temperature, preserving its configured limits."""
        value = kwargs.get(ATTR_TEMPERATURE)
        if value is None:
            return
        temperature = float(value)
        if not isfinite(temperature) or not self._attr_min_temp <= temperature <= self._attr_max_temp:
            raise HomeAssistantError("Temperature is outside the configured limits")
        self._attr_native_target_temperature = temperature
        # A user-set base target persists over Home Assistant restarts.
        self.hass.config_entries.async_update_entry(
            self._entry,
            options={**self._entry.options, CONF_BASE_TEMP: temperature},
        )
        await self._async_evaluate()
        self.async_write_ha_state()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Select heating or turn off only on explicit user action."""
        if hvac_mode not in self._attr_hvac_modes:
            raise HomeAssistantError("Unsupported HVAC mode")
        self._attr_hvac_mode = hvac_mode
        await self._async_evaluate()
        self.async_write_ha_state()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Switch between a price-aware setpoint and a fixed setpoint."""
        if preset_mode not in self._attr_preset_modes:
            raise HomeAssistantError("Unsupported thermostat preset")
        self._attr_preset_mode = preset_mode
        await self._async_evaluate()
        self.async_write_ha_state()
