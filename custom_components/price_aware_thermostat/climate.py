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
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.util import dt as dt_util

from .ac_control import (
    AcMode,
    BackendLimits,
    Command,
    CommandMemory,
    Intent,
    Observation,
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
    CONF_DEADBAND,
    CONF_FILTER,
    CONF_HEAT_COMPENSATION,
    CONF_HOT_TOLERANCE,
    CONF_LOOKAHEAD,
    CONF_MAX_DEVIATION,
    CONF_MAX_SENSOR_AGE,
    CONF_MAX_TEMP,
    CONF_MIN_CYCLE,
    CONF_MIN_TEMP,
    CONF_SENSITIVITY,
    CONF_SENSOR,
    CONF_SHADOW,
    CONF_WINDOW_CLOSE_DELAY,
    CONF_WINDOW_OPEN_DELAY,
    CONF_WINDOW_SENSORS,
    CONTROL_CLIMATE,
    DEFAULTS,
)
from .control import (
    desired_heater_state,
    is_temperature_sample_fresh,
    parse_temperature_celsius,
)
from .coordinator import NordPoolPriceCoordinator
from .optimizer import Filter, OptimizationResult, OptimizerConfig, optimize
from .relay_guard import RelayCommandGuard

_LOGGER = logging.getLogger(__name__)
PRESET_PRICE_AWARE = "price_aware"
PRESET_FIXED = "fixed"

# Serialize climate entity service calls; scheduled evaluations are also
# coalesced per entity and guarded by an async lock.
PARALLEL_UPDATES = 1


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

    def _max_deviation(self) -> float | None:
        """Optional deviation limit around the base target (none for the switch type)."""
        return None

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
        self._memory = CommandMemory(off_since=_monotonic())
        self._demand_active = False
        self._first_evaluation = True
        self._intent = Intent("off", None, "startup")
        self._plan = Plan(None, "startup")
        self._observed = Observation(False)
        self._in_sync = False
        self._window_status = "not_configured"
        self._reported_action: HVACAction | None = None

    # -- hooks used by the shared optimizer code -------------------------------
    def _optimizer_mode(self) -> Literal["heat", "cool"]:
        return "cool" if self._attr_hvac_mode == HVACMode.COOL else "heat"

    def _max_deviation(self) -> float | None:
        value = {**self._entry.data, **self._entry.options}.get(CONF_MAX_DEVIATION)
        return None if value is None else float(value)

    def _tracked_entities(self) -> list[str]:
        return [self._sensor, self._heater, *self._windows]

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
        try:
            target = float(attrs["temperature"])
            target = target if isfinite(target) else None
        except (KeyError, TypeError, ValueError):
            target = None
        try:
            limits: BackendLimits | None = BackendLimits(
                float(attrs["min_temp"]),
                float(attrs["max_temp"]),
                float(attrs.get("target_temp_step", 1.0)),
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
            if command.kind == "set_mode":
                data["hvac_mode"] = command.mode
                await self.hass.services.async_call(
                    "climate", "set_hvac_mode", data, blocking=True
                )
            else:
                data["temperature"] = command.target
                await self.hass.services.async_call(
                    "climate", "set_temperature", data, blocking=True
                )
        except HomeAssistantError:
            _LOGGER.exception("Could not command the climate backend")

    def _snapshot(self) -> tuple[Any, ...]:
        return (
            self._attr_native_current_temperature, self._attr_hvac_action, self._last_result,
            self._sensor_fresh, self._intent, self._plan.status, self._observed,
            self._window_status, self._in_sync,
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
            self._first_evaluation = False
            self._intent = intent
            plan = plan_next_command(
                intent, observed, self._memory, limits, mono, float(self._min_cycle)
            )
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
            self._attr_hvac_action = (
                HVACAction.OFF
                if self._attr_hvac_mode == HVACMode.OFF
                else self._reported_action
            )
            # Shadow mode computes the proposal but calls no climate or switch service.
            if plan.command is not None and not self._shadow and not self._unloading:
                await self._async_dispatch(plan.command, mono)
            if previous != self._snapshot():
                self.async_write_ha_state()

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
                else "restart_pause" if self._plan.status == "restart_pause"
                else None
            ),
            "window_status": self._window_status,
            "planned_heating": None,
        }
