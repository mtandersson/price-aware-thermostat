"""A price-aware on/off climate entity, starting safely in shadow mode."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta
from math import isfinite
from typing import Any, Literal

from homeassistant.components.climate import (
    ATTR_TEMPERATURE,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
    RestoreClimate,
)
from homeassistant.const import (
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfTemperature,
)
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import TemperatureConverter, TemperatureDeltaConverter

from .ac_control import (
    AcMode,
    BackendLimits,
    Command,
    CommandMemory,
    Intent,
    Observation,
    OutdoorLockout,
    Plan,
    WindowGuard,
    aggregate_contacts,
    demand,
    native_target,
    plan_next_command,
)
from .backends import backend_from_settings
from .config_entry import PriceAwareConfigEntry
from .const import (
    CONF_BASE_TEMP,
    CONF_COLD_TOLERANCE,
    CONF_COOL_COMPENSATION,
    CONF_COOL_MIN_OUTDOOR,
    CONF_DEADBAND,
    CONF_FILTER,
    CONF_HEAT_COMPENSATION,
    CONF_HEAT_MAX_OUTDOOR,
    CONF_HOT_TOLERANCE,
    CONF_LOOKAHEAD,
    CONF_MAX_DEVIATION,
    CONF_MAX_SENSOR_AGE,
    CONF_MAX_TEMP,
    CONF_MIN_CYCLE,
    CONF_MIN_TEMP,
    CONF_OUTDOOR_HYSTERESIS,
    CONF_OUTDOOR_SENSOR,
    CONF_SENSITIVITY,
    CONF_SENSOR,
    CONF_SHADOW,
    CONF_WINDOW_CLOSE_DELAY,
    CONF_WINDOW_OPEN_DELAY,
    CONF_WINDOW_SENSORS,
    CONTROL_CLIMATE,
    DEFAULTS,
    DOMAIN,
)
from .control import (
    desired_heater_state,
    is_temperature_sample_fresh,
    parse_temperature_celsius,
)
from .coordinator import NordPoolPriceCoordinator
from .optimizer import Filter, OptimizationResult, OptimizerConfig, optimize
from .relay_guard import RelayCommandGuard
from .sensor import device_info

ACTUATOR_TIMEOUT_SECONDS = 10

_LOGGER = logging.getLogger(__name__)
PRESET_PRICE_AWARE = "price_aware"
PRESET_FIXED = "fixed"

# Serialize climate entity service calls; scheduled evaluations are also
# coalesced per entity and guarded by an async lock.
PARALLEL_UPDATES = 1


# After a Home Assistant start the room sensor often loads after the wrapper.
# A missing reading within this grace period does not turn a running pump off
# (it would cost a compressor off/on cycle on every restart); afterwards it is
# fail-closed as usual.
STARTUP_GRACE_SECONDS = 120.0


def _monotonic() -> float:
    """Clock for restart pauses, window delays and retries (patched in tests)."""
    return time.monotonic()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PriceAwareConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Register one climate entity per integration entry."""
    coordinator: NordPoolPriceCoordinator = entry.runtime_data
    backend = backend_from_settings({**entry.data, **entry.options})
    entity_class = (
        ClimateBackendThermostat if backend.kind == CONTROL_CLIMATE else PriceAwareThermostat
    )
    async_add_entities([entity_class(entry, coordinator)])


class PriceAwareThermostat(RestoreClimate):
    """Local thermostat that can use future electricity prices to shift heat."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.PRESET_MODE
    )
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]
    _attr_preset_modes = [PRESET_PRICE_AWARE, PRESET_FIXED]
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_native_target_temperature_step = 0.1

    def __init__(
        self, entry: PriceAwareConfigEntry, coordinator: NordPoolPriceCoordinator
    ) -> None:
        """Configure the thermostat without writing to any switches."""
        self._entry = entry
        self._coordinator = coordinator
        settings = {**entry.data, **entry.options}
        self._backend = backend_from_settings(settings)
        self._attr_hvac_modes = [HVACMode(mode) for mode in self._backend.implemented_modes]
        self._sensor = str(settings[CONF_SENSOR])
        self._heater = self._backend.entity_id
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
        # The thermostat is the device's main entity: it carries the device name
        # (the entry title), and the price sensor shares the same device.
        self._attr_name = None
        self._attr_device_info = device_info(entry)
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
        self._relay_guard = RelayCommandGuard()
        self._evaluation_task: asyncio.Task[None] | None = None
        self._evaluation_requested = False
        self._unloading = False

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
            hvac_mode=self._optimizer_mode(),
            max_deviation_c=self._max_deviation(),
        )
        return optimize(dt_util.utcnow(), self._coordinator.data or (), config)

    def _optimizer_mode(self) -> Literal["heat", "cool"]:
        """Price-adjustment direction; the switch controller only heats."""
        return "heat"

    def _max_deviation(self) -> float:
        """Price-adjustment limit around the user's base target, for every control type.

        An entry without a value (only possible before migration) uses the default.
        """
        value = {**self._entry.data, **self._entry.options}.get(CONF_MAX_DEVIATION)
        return float(DEFAULTS[CONF_MAX_DEVIATION] if value is None else value)

    def _safe_calculate(self) -> OptimizationResult:
        """Calculate the price-adjusted target, falling back to the base target."""
        try:
            return self._calculate()
        except ValueError:
            _LOGGER.warning("Invalid price data; using the configured base temperature")
            return OptimizationResult(
                effective_target_c=self._base_target,
                reason="price_unavailable",
                current_price_sek_per_kwh=None,
                future_weighted_price_sek_per_kwh=None,
                price_difference_sek_per_kwh=None,
                future_coverage_fraction=0.0,
            )

    def _read_temperature(self) -> None:
        """Read the room sensor; stale, unavailable or invalid readings become None."""
        temp_state = self.hass.states.get(self._sensor)
        self._attr_native_current_temperature = None
        self._sensor_fresh = temp_state is not None and is_temperature_sample_fresh(
            getattr(temp_state, "last_reported", None),
            dt_util.utcnow(),
            self._max_sensor_age,
        )
        if (
            self._sensor_fresh
            and temp_state is not None
            and temp_state.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE)
        ):
            unit = temp_state.attributes.get("unit_of_measurement")
            self._attr_native_current_temperature = parse_temperature_celsius(
                temp_state.state, unit if isinstance(unit, str) else None
            )
        self._sensor_fresh = (
            self._sensor_fresh and self._attr_native_current_temperature is not None
        )

    def _tracked_entities(self) -> list[str]:
        """Entities whose state changes trigger a re-evaluation."""
        return [self._sensor, self._heater]

    @property
    def extra_state_attributes(self) -> dict[str, str | float | bool | None]:
        """Expose the reason for decisions without excessive state data."""
        result = self._last_result
        return {
            "shadow_mode": self._shadow,
            "temperature_sample_fresh": self._sensor_fresh,
            "effective_target_c": (
                result.effective_target_c
                if result and self._attr_preset_mode == PRESET_PRICE_AWARE
                else self._base_target
            ),
            "price_now_sek_per_kwh": result.current_price_sek_per_kwh if result else None,
            "weighted_future_price_sek_per_kwh": (
                result.future_weighted_price_sek_per_kwh if result else None
            ),
            "price_difference_sek_per_kwh": (
                result.price_difference_sek_per_kwh if result else None
            ),
            "future_coverage_fraction": result.future_coverage_fraction if result else 0.0,
            "requested_adjustment_c": (
                result.requested_adjustment_c
                if result and self._attr_preset_mode == PRESET_PRICE_AWARE
                and self._attr_hvac_mode != HVACMode.OFF
                else 0.0
            ),
            "adjustment_clamped": (
                result.clamped
                if result and self._attr_preset_mode == PRESET_PRICE_AWARE
                and self._attr_hvac_mode != HVACMode.OFF
                else False
            ),
            "optimization_reason": (
                "off"
                if self._attr_hvac_mode == HVACMode.OFF
                else "fixed"
                if self._attr_preset_mode == PRESET_FIXED
                else result.reason
                if result
                else "price_unavailable"
            ),
            "planned_heating": self._planned_heating,
        }

    @callback
    def _queue_evaluation(self, *_args: Any) -> None:
        """Coalesce event bursts into one lifecycle-bound evaluation worker."""
        if self._unloading:
            return
        self._evaluation_requested = True
        if self._evaluation_task is None or self._evaluation_task.done():
            self._evaluation_task = self.hass.async_create_task(
                self._async_process_evaluations()
            )

    async def _async_process_evaluations(self) -> None:
        """Recalculate once per burst, with one follow-up for in-flight events."""
        while self._evaluation_requested and not self._unloading:
            self._evaluation_requested = False
            await self._async_evaluate()

    @callback
    def _cancel_evaluation_task(self) -> None:
        """Cancel queued work when Home Assistant removes the climate entity."""
        self._unloading = True
        self._evaluation_requested = False
        if self._evaluation_task is not None and not self._evaluation_task.done():
            self._evaluation_task.cancel()

    @callback
    def _state_changed(self, event: Event[EventStateChangedData]) -> None:
        """Re-evaluate on sensor and switch state changes."""
        self._queue_evaluation()

    async def async_added_to_hass(self) -> None:
        """Subscribe to updates; restore only explicit HVAC/preset choices."""
        await super().async_added_to_hass()
        old = await self.async_get_last_state()
        if old is not None:
            if old.state in {str(mode) for mode in self._attr_hvac_modes}:
                self._attr_hvac_mode = HVACMode(old.state)
            if old.attributes.get("preset_mode") == PRESET_FIXED:
                self._attr_preset_mode = PRESET_FIXED

        self.async_on_remove(self._cancel_evaluation_task)
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, self._tracked_entities(), self._state_changed
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
            heater_state = self.hass.states.get(self._heater)
            self._read_temperature()
            self._last_result = self._safe_calculate()
            effective_target = (
                self._last_result.effective_target_c
                if self._attr_preset_mode == PRESET_PRICE_AWARE
                else self._base_target
            )
            currently_on = heater_state is not None and heater_state.state == STATE_ON
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
            if self._shadow:
                # Shadow shows the wrapper's own proposal, never the relay state
                # (issue #41).
                self._attr_hvac_action = (
                    HVACAction.OFF if self._attr_hvac_mode == HVACMode.OFF
                    else HVACAction.HEATING if desired
                    else HVACAction.IDLE
                )
            elif currently_on:
                # Live mirrors the relay; a relay still on is never called "off".
                self._attr_hvac_action = HVACAction.HEATING
            else:
                self._attr_hvac_action = (
                    HVACAction.OFF if self._attr_hvac_mode == HVACMode.OFF
                    else HVACAction.IDLE
                )

            # In shadow mode *no switch services are called*, even if the sensor
            # is missing or the user explicitly selects HVAC off.
            if (
                self._backend.supports_live_control
                and not self._shadow
                and not self._unloading
                and heater_state is not None
                and heater_state.state in (STATE_ON, STATE_OFF)
            ):
                # A successful service call is not a physical acknowledgment.
                # Only an observed relay state clears a pending command.
                self._relay_guard.acknowledge(currently_on)
                if desired is None:
                    desired = False
                now_monotonic = time.monotonic()
                if self._relay_guard.should_dispatch(
                    desired, currently_on, now_monotonic
                ):
                    forced_off = (
                        self._attr_hvac_mode == HVACMode.OFF
                        or self._attr_native_current_temperature is None
                    )
                    corrective_off = (
                        self._relay_guard.pending_state is True and not desired
                    )
                    age = dt_util.utcnow() - heater_state.last_changed
                    if forced_off or corrective_off or age >= timedelta(
                        seconds=self._min_cycle
                    ):
                        service = "turn_on" if desired else "turn_off"
                        # Record before awaiting so failed or interrupted calls
                        # also have a retry cooldown.
                        self._relay_guard.record_attempt(desired, now_monotonic)
                        try:
                            async with asyncio.timeout(ACTUATOR_TIMEOUT_SECONDS):
                                await self.hass.services.async_call(
                                    "switch", service,
                                    {"entity_id": self._heater},
                                    blocking=True,
                                )
                        except (HomeAssistantError, TimeoutError):
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
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="temperature_out_of_range",
                translation_placeholders={
                    "min_temp": str(self._attr_min_temp),
                    "max_temp": str(self._attr_max_temp),
                },
            )
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
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unsupported_hvac_mode",
                translation_placeholders={"hvac_mode": str(hvac_mode)},
            )
        self._attr_hvac_mode = hvac_mode
        await self._async_evaluate()
        self.async_write_ha_state()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Switch between a price-aware setpoint and a fixed setpoint."""
        if preset_mode not in self._attr_preset_modes:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unsupported_preset_mode",
                translation_placeholders={"preset_mode": str(preset_mode)},
            )
        self._attr_preset_mode = preset_mode
        await self._async_evaluate()
        self.async_write_ha_state()


class ClimateBackendThermostat(PriceAwareThermostat):
    """Regulate a room from an external sensor by commanding a heat pump/AC climate.

    The room sensor is the demand source. The wrapper sends HVAC-mode and
    temperature commands to the underlying climate; it never toggles an
    electrical relay. While live it is the authoritative writer for that
    backend. In shadow mode the same plan is computed and exposed, but no
    service is ever called.
    """

    def __init__(
        self, entry: PriceAwareConfigEntry, coordinator: NordPoolPriceCoordinator
    ) -> None:
        super().__init__(entry, coordinator)
        settings = {**entry.data, **entry.options}
        # A fresh wrapper starts off; a restored explicit choice may replace it.
        self._attr_hvac_mode = HVACMode.OFF
        self._windows = tuple(str(c) for c in settings.get(CONF_WINDOW_SENSORS, []) or [])
        self._window_guard = WindowGuard(
            open_delay=float(
                settings.get(CONF_WINDOW_OPEN_DELAY, DEFAULTS[CONF_WINDOW_OPEN_DELAY])
            ),
            close_delay=float(
                settings.get(CONF_WINDOW_CLOSE_DELAY, DEFAULTS[CONF_WINDOW_CLOSE_DELAY])
            ),
        )
        # Restart protection: the pause counts from the first observed off state.
        self._started_at = _monotonic()
        self._memory = CommandMemory(off_since=self._started_at)
        self._demand_active = False
        self._first_evaluation = True
        self._intent = Intent("off", None, "startup")
        self._plan = Plan(None, "startup")
        self._observed = Observation(False)
        self._in_sync = False
        self._window_status = "not_configured"
        self._reported_action: HVACAction | None = None
        # Optional outdoor lockouts; an absent sensor or threshold disables them.
        outdoor = settings.get(CONF_OUTDOOR_SENSOR)
        self._outdoor_sensor = outdoor if isinstance(outdoor, str) and outdoor else None

        def threshold(key: str) -> float | None:
            value = settings.get(key)
            return None if value is None or isinstance(value, bool) else float(value)

        self._lockout = OutdoorLockout(
            cool_min=threshold(CONF_COOL_MIN_OUTDOOR),
            heat_max=threshold(CONF_HEAT_MAX_OUTDOOR),
            hysteresis=float(
                settings.get(CONF_OUTDOOR_HYSTERESIS, DEFAULTS[CONF_OUTDOOR_HYSTERESIS])
            ),
        )
        self._outdoor_c: float | None = None
        self._outdoor_status = "not_configured"
        # A restored lock is held until the first valid reading or the startup
        # grace period, so a sensor that loads late cannot release it.
        self._hold_restored_lock = False

    # -- hooks used by the shared optimizer code -------------------------------
    def _optimizer_mode(self) -> Literal["heat", "cool"]:
        return "cool" if self._attr_hvac_mode == HVACMode.COOL else "heat"

    def _tracked_entities(self) -> list[str]:
        outdoor = [self._outdoor_sensor] if self._outdoor_sensor else []
        return [self._sensor, self._heater, *self._windows, *outdoor]

    async def async_added_to_hass(self) -> None:
        """Restore the outdoor lockout state so a restart does not flap it."""
        old = await self.async_get_last_state()
        if old is not None:
            self._lockout.heat_locked = (
                self._lockout.heat_max is not None
                and old.attributes.get("outdoor_heat_locked") is True
            )
            self._lockout.cool_locked = (
                self._lockout.cool_min is not None
                and old.attributes.get("outdoor_cool_locked") is True
            )
            self._hold_restored_lock = self._lockout.heat_locked or self._lockout.cool_locked
        await super().async_added_to_hass()

    def _update_outdoor(self) -> None:
        """Read the outdoor sensor and update the lockouts; faults fail open."""
        if self._outdoor_sensor is None:
            self._outdoor_c = None
            self._outdoor_status = "not_configured"
            self._lockout.update(None)
            return
        state = self.hass.states.get(self._outdoor_sensor)
        value: float | None = None
        if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            status = "unavailable"
        elif not is_temperature_sample_fresh(
            getattr(state, "last_reported", None), dt_util.utcnow(), self._max_sensor_age
        ):
            status = "stale"
        else:
            unit = state.attributes.get("unit_of_measurement")
            value = parse_temperature_celsius(state.state, unit if isinstance(unit, str) else None)
            status = "ok" if value is not None else "invalid"
        if status != "ok" and status != self._outdoor_status:
            _LOGGER.warning(
                "Outdoor temperature sensor %s is %s; outdoor lockouts are released",
                self._outdoor_sensor, status,
            )
        self._outdoor_status = status
        self._outdoor_c = value
        if value is not None or _monotonic() - self._started_at >= STARTUP_GRACE_SECONDS:
            self._hold_restored_lock = False
        if not self._hold_restored_lock:
            self._lockout.update(value)

    def _outdoor_lockout(self) -> str:
        """The lockout affecting the wrapper's current direction (none/heat/cool)."""
        if self._attr_hvac_mode == HVACMode.HEAT and self._lockout.heat_locked:
            return "heat"
        if self._attr_hvac_mode == HVACMode.COOL and self._lockout.cool_locked:
            return "cool"
        return "none"

    # -- units -----------------------------------------------------------------
    # Home Assistant shows climate limits and targets, and expects service
    # temperatures, in the system unit. All internal values are Celsius.
    def _to_celsius(self, value: float) -> float:
        return TemperatureConverter.convert(
            value, self.hass.config.units.temperature_unit, UnitOfTemperature.CELSIUS
        )

    def _from_celsius(self, value: float) -> float:
        return round(
            TemperatureConverter.convert(
                value, UnitOfTemperature.CELSIUS, self.hass.config.units.temperature_unit
            ),
            4,
        )

    # -- observation -----------------------------------------------------------
    def _observe(self) -> tuple[Observation, BackendLimits | None]:
        """Read what the underlying climate reports; never assume a command worked."""
        state = self.hass.states.get(self._heater)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            self._reported_action = None
            return Observation(False), None
        attrs = state.attributes
        modes = attrs.get("hvac_modes")
        supported = tuple(str(m) for m in modes) if isinstance(modes, (list, tuple)) else ()
        target: float | None
        unit = self.hass.config.units.temperature_unit
        try:
            raw_target = float(attrs["temperature"])
            target = self._to_celsius(raw_target) if isfinite(raw_target) else None
        except (KeyError, TypeError, ValueError):
            target = None
        # Home Assistant displays limits and targets rounded to its display unit
        # (whole degrees Fahrenheit, tenths of a degree Celsius). That rounding is
        # added to the acknowledgment tolerance. Whole-degree Fahrenheit can round a
        # limit outward, and a write beyond the real limit is rejected, so targets
        # also keep half a display unit inside the limits there. Celsius limits are
        # displayed exactly enough that the grid and range are left untouched.
        fahrenheit = unit == UnitOfTemperature.FAHRENHEIT
        half_unit = 0.5 if fahrenheit else 0.05
        readback = TemperatureDeltaConverter.convert(half_unit, unit, UnitOfTemperature.CELSIUS)
        try:
            limits: BackendLimits | None = BackendLimits(
                self._to_celsius(float(attrs["min_temp"])),
                self._to_celsius(float(attrs["max_temp"])),
                TemperatureDeltaConverter.convert(
                    float(attrs.get("target_temp_step", 1.0)), unit, UnitOfTemperature.CELSIUS
                ),
                readback,
                readback if fahrenheit else 0.0,
            )
        except (KeyError, TypeError, ValueError):
            limits = None
        try:
            self._reported_action = HVACAction(str(attrs.get("hvac_action")))
        except ValueError:
            self._reported_action = None
        return Observation(True, state.state, target, supported), limits

    def _window_reading(self) -> bool | None:
        readings: list[bool | None] = []
        for entity_id in self._windows:
            state = self.hass.states.get(entity_id)
            if state is not None and state.state == STATE_ON:
                readings.append(True)
            elif state is not None and state.state == STATE_OFF:
                readings.append(False)
            else:
                readings.append(None)  # missing/unavailable/unknown contact
        return aggregate_contacts(readings)

    def _compensation(self, mode: AcMode) -> float:
        settings = {**self._entry.data, **self._entry.options}
        key = CONF_COOL_COMPENSATION if mode == "cool" else CONF_HEAT_COMPENSATION
        return float(settings.get(key, DEFAULTS[key]))

    # -- decision --------------------------------------------------------------
    def _decide(
        self, observed: Observation, limits: BackendLimits | None, mono: float
    ) -> Intent:
        """Combine mode, windows, sensor health, price and hysteresis into an intent."""
        self._update_outdoor()
        reading = self._window_reading()
        paused = self._window_guard.update(reading, mono) if self._windows else False
        if not self._windows:
            self._window_status = "not_configured"
        elif paused:
            self._window_status = "open_paused"
        elif reading is None:
            self._window_status = "unknown"
        elif reading:
            self._window_status = "open_pending"
        else:
            self._window_status = "closed"

        mode = self._attr_hvac_mode
        if mode == HVACMode.OFF:
            self._demand_active = False
            return Intent("off", None, "wrapper_off")
        wanted: AcMode = "cool" if mode == HVACMode.COOL else "heat"
        if paused:
            # Reconsideration after closure starts from a fresh hysteresis state.
            self._demand_active = False
            return Intent("off", None, "window_open")
        if self._lockout.locks(wanted):
            # Only the locked direction is affected; the wrapper mode is kept.
            self._demand_active = False
            return Intent("off", None, "outdoor_lockout")
        current = self._attr_native_current_temperature
        if current is None:
            self._demand_active = False
            return Intent("off", None, "room_temperature_unavailable")
        if self._first_evaluation and observed.mode == wanted:
            # After a restart a pump already running in this mode keeps its demand.
            self._demand_active = True
        effective = (
            self._last_result.effective_target_c
            if self._last_result is not None and self._attr_preset_mode == PRESET_PRICE_AWARE
            else self._base_target
        )
        wants = demand(
            wanted, current, effective, self._demand_active,
            self._cold_tolerance, self._hot_tolerance,
        )
        self._demand_active = bool(wants)
        if not wants:
            return Intent("off", None, "no_demand")
        target: float | None = None
        if limits is not None:
            target = native_target(effective, self._compensation(wanted), limits)
        return Intent(wanted, target, "demand")

    async def _async_dispatch(self, command: Command, mono: float) -> None:
        """Send one backend command; the observed state, not success, acknowledges it."""
        # Record before awaiting so failed or interrupted calls are also throttled.
        self._memory.record(command, mono)
        data: dict[str, Any] = {"entity_id": self._heater}
        try:
            async with asyncio.timeout(ACTUATOR_TIMEOUT_SECONDS):
                if command.kind == "set_mode":
                    data["hvac_mode"] = command.mode
                    await self.hass.services.async_call(
                        "climate", "set_hvac_mode", data, blocking=True
                    )
                else:
                    assert command.target is not None
                    data["temperature"] = self._from_celsius(command.target)
                    await self.hass.services.async_call(
                        "climate", "set_temperature", data, blocking=True
                    )
        except Exception:
            # Any backend failure (including non-HomeAssistantError exceptions from
            # a third-party integration) is logged and bounded by the retry rules;
            # it must not kill the evaluation worker or leave state unpublished.
            # Cancellation is a BaseException and still propagates.
            _LOGGER.exception("Could not command the climate backend")

    def _snapshot(self) -> tuple[Any, ...]:
        return (
            self._attr_native_current_temperature, self._attr_hvac_action, self._last_result,
            self._sensor_fresh, self._intent, self._plan.status, self._observed,
            self._window_status, self._in_sync, self._reported_action,
            self._outdoor_c, self._outdoor_status,
            self._lockout.heat_locked, self._lockout.cool_locked,
        )

    async def _async_evaluate(self) -> None:
        """Evaluate demand and, only when live, reconcile the backend."""
        async with self._control_lock:
            previous = self._snapshot()
            mono = _monotonic()
            self._read_temperature()
            self._last_result = self._safe_calculate()
            observed, limits = self._observe()
            self._observed = observed
            intent = self._decide(observed, limits, mono)
            if (
                self._first_evaluation
                and observed.available
                and self._attr_native_current_temperature is not None
            ):
                # Only an evaluation that could see both the backend and the room
                # may decide whether a running pump keeps its demand.
                self._first_evaluation = False
            self._intent = intent
            plan = plan_next_command(
                intent, observed, self._memory, limits, mono, float(self._min_cycle)
            )
            if (
                intent.reason == "room_temperature_unavailable"
                and mono - self._started_at < STARTUP_GRACE_SECONDS
            ):
                plan = Plan(None, "startup_grace")
            self._plan = plan
            self._in_sync = observed.available and observed.mode == intent.mode and (
                intent.mode == "off"
                or (
                    limits is not None
                    and observed.target is not None
                    and intent.native_target is not None
                    and abs(observed.target - intent.native_target) <= limits.tolerance
                )
            )
            self._attr_hvac_action = self._wrapper_action(observed)
            # Shadow mode computes the proposal but calls no climate or switch service.
            if plan.command is not None and not self._shadow and not self._unloading:
                await self._async_dispatch(plan.command, mono)
            if previous != self._snapshot():
                self.async_write_ha_state()

    def _wrapper_action(self, observed: Observation) -> HVACAction | None:
        """The wrapper's hvac_action.

        In shadow mode nothing is commanded, so the action is the wrapper's own
        proposal and never the backend's report (issue #41): off when the wrapper
        is off, heating/cooling only while the proposal has demand, otherwise
        idle (no demand, window pause, outdoor lockout, sensor fault). Live, it
        mirrors the backend and only claims "off" when the backend really is off.
        """
        if self._shadow:
            if self._attr_hvac_mode == HVACMode.OFF:
                return HVACAction.OFF
            if self._intent.mode == "heat":
                return HVACAction.HEATING
            if self._intent.mode == "cool":
                return HVACAction.COOLING
            return HVACAction.IDLE
        if (
            self._attr_hvac_mode == HVACMode.OFF
            and observed.available
            and observed.mode == "off"
        ):
            return HVACAction.OFF
        return self._reported_action

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Change the wrapper mode; a changed direction never reuses stale demand."""
        if hvac_mode in self._attr_hvac_modes and hvac_mode != self._attr_hvac_mode:
            self._demand_active = False
        await super().async_set_hvac_mode(hvac_mode)

    @property
    def extra_state_attributes(self) -> dict[str, str | float | bool | None]:
        result = self._last_result
        active = (
            result is not None
            and self._attr_preset_mode == PRESET_PRICE_AWARE
            and self._attr_hvac_mode != HVACMode.OFF
        )
        mode: AcMode = (
            "cool" if self._attr_hvac_mode == HVACMode.COOL
            else "heat" if self._attr_hvac_mode == HVACMode.HEAT else "off"
        )
        return {
            "control_type": self._backend.kind,
            "backend_entity_id": self._backend.entity_id,
            "configured_hvac_modes": ",".join(self._backend.configured_modes),
            "shadow_mode": self._shadow,
            "live_control_supported": True,
            "temperature_sample_fresh": self._sensor_fresh,
            "base_target_c": self._base_target,
            "effective_target_c": (
                result.effective_target_c if result is not None and active else self._base_target
            ),
            "price_now_sek_per_kwh": result.current_price_sek_per_kwh if result else None,
            "weighted_future_price_sek_per_kwh": (
                result.future_weighted_price_sek_per_kwh if result else None
            ),
            "price_difference_sek_per_kwh": (
                result.price_difference_sek_per_kwh if result else None
            ),
            "future_coverage_fraction": result.future_coverage_fraction if result else 0.0,
            "requested_adjustment_c": result.requested_adjustment_c if result and active else 0.0,
            "adjustment_clamped": result.clamped if result and active else False,
            "optimization_reason": (
                "off" if self._attr_hvac_mode == HVACMode.OFF
                else "fixed" if self._attr_preset_mode == PRESET_FIXED
                else result.reason if result else "price_unavailable"
            ),
            "proposed_backend_mode": self._intent.mode,
            "proposed_native_target_c": self._intent.native_target,
            "native_target_c": self._intent.native_target,
            "intent_reason": self._intent.reason,
            "compensation_c": self._compensation(mode) if mode != "off" else None,
            "observed_backend_mode": self._observed.mode,
            "observed_native_target_c": self._observed.target,
            # What the backend reports; not evidence that a compressor is running.
            "backend_reported_hvac_action": (
                self._reported_action.value if self._reported_action else None
            ),
            "backend_available": self._observed.available,
            "backend_in_sync": self._in_sync,
            "command_status": self._plan.status,
            "pause_reason": (
                "window_open" if self._window_status == "open_paused"
                else "outdoor_lockout" if self._intent.reason == "outdoor_lockout"
                else "restart_pause" if self._plan.status == "restart_pause"
                else None
            ),
            "window_status": self._window_status,
            "outdoor_temperature_c": self._outdoor_c,
            "outdoor_sensor_status": self._outdoor_status,
            "outdoor_lockout": self._outdoor_lockout(),
            "outdoor_heat_locked": self._lockout.heat_locked,
            "outdoor_cool_locked": self._lockout.cool_locked,
            "planned_heating": None,
        }
