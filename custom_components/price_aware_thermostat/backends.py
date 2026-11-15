"""Backend configuration contract; pricing never sends actuator commands."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any

from homeassistant.components.climate import ClimateEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_CONTROL_TYPE,
    CONF_COOL_COMPENSATION,
    CONF_COOL_MIN_OUTDOOR,
    CONF_HEAT_COMPENSATION,
    CONF_HEAT_MAX_OUTDOOR,
    CONF_HEATER,
    CONF_HVAC_MODES,
    CONF_MAX_DEVIATION,
    CONF_MIN_CYCLE,
    CONF_OUTDOOR_HYSTERESIS,
    CONF_OUTDOOR_SENSOR,
    CONF_SENSOR,
    CONF_WINDOW_CLOSE_DELAY,
    CONF_WINDOW_OPEN_DELAY,
    CONF_WINDOW_SENSORS,
    CONTROL_CLIMATE,
    CONTROL_SWITCH,
    DOMAIN,
)
from .control import parse_temperature_celsius


@dataclass(frozen=True)
class ControlBackend:
    """Stable actuator identity and implemented command/mode capabilities.

    A switch backend is a heating relay. A climate backend is a heat pump/AC
    commanded with HVAC-mode and temperature service calls; the controlled
    modes are the configured heat/cool subset plus off.
    """

    kind: str
    entity_id: str
    configured_modes: tuple[str, ...]

    @property
    def supports_live_control(self) -> bool:
        return True

    @property
    def implemented_modes(self) -> tuple[str, ...]:
        if self.kind == CONTROL_SWITCH:
            return ("heat", "off")
        return (*self.configured_modes, "off")


def backend_from_settings(settings: dict[str, Any]) -> ControlBackend:
    """Interpret legacy switch settings without changing their stored identity."""
    kind = settings.get(CONF_CONTROL_TYPE, CONTROL_SWITCH)
    entity_id = settings.get(CONF_HEATER, "")
    if kind not in (CONTROL_SWITCH, CONTROL_CLIMATE) or not isinstance(entity_id, str):
        raise ValueError("Invalid control backend")
    if not entity_id.startswith(kind + "."):
        raise ValueError("Actuator domain does not match control type")
    modes = settings.get(CONF_HVAC_MODES, ["heat"])
    if not isinstance(modes, list) or not modes or any(m not in ("heat", "cool") for m in modes):
        raise ValueError("Invalid controlled HVAC modes")
    if kind == CONTROL_SWITCH and modes != ["heat"]:
        raise ValueError("Switch backend only supports heating")
    return ControlBackend(kind, entity_id, tuple(dict.fromkeys(modes)))


def validate_ac_settings(settings: dict[str, Any]) -> str | None:
    """Validate heat-pump/AC-only settings independently of UI widgets."""

    def number(key: str, default: float) -> float | None:
        value = settings.get(key, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value) if isfinite(float(value)) else None

    for key in (CONF_HEAT_COMPENSATION, CONF_COOL_COMPENSATION):
        value = number(key, 0.0)
        if value is None or abs(value) > 10:
            return "invalid_ac_settings"
    # The restart pause is the compressor's rest period in software: refuse to
    # configure it away entirely.
    pause = number(CONF_MIN_CYCLE, 300.0)
    if pause is None or not 60 <= pause <= 3600:
        return "invalid_ac_settings"
    for key in (CONF_WINDOW_OPEN_DELAY, CONF_WINDOW_CLOSE_DELAY):
        value = number(key, 0.0)
        if value is None or not 0 <= value <= 3600:
            return "invalid_ac_settings"
    if settings.get(CONF_MAX_DEVIATION) is not None:
        value = number(CONF_MAX_DEVIATION, 0.0)
        if value is None or not 0 <= value <= 20:
            return "invalid_ac_settings"
    for key in (CONF_COOL_MIN_OUTDOOR, CONF_HEAT_MAX_OUTDOOR):
        # Empty (None) disables the lockout.
        if settings.get(key) is not None:
            value = number(key, 0.0)
            if value is None or not -40 <= value <= 40:
                return "invalid_ac_settings"
    hysteresis = number(CONF_OUTDOOR_HYSTERESIS, 1.0)
    if hysteresis is None or not 0 <= hysteresis <= 10:
        return "invalid_ac_settings"
    contacts = settings.get(CONF_WINDOW_SENSORS, [])
    if not isinstance(contacts, list) or any(
        not isinstance(c, str) or not c.startswith("binary_sensor.") for c in contacts
    ):
        return "invalid_window_sensor"
    return None


def validate_backend(
    hass: HomeAssistant, settings: dict[str, Any], *, entry_id: str | None = None
) -> str | None:
    """Validate selected entities and ownership at the configuration boundary."""
    try:
        backend = backend_from_settings(settings)
    except ValueError:
        return "invalid_backend"
    sensor_id = settings.get(CONF_SENSOR, "")
    sensor = hass.states.get(sensor_id) if isinstance(sensor_id, str) else None
    if sensor is None or not sensor.entity_id.startswith("sensor."):
        return "invalid_room_sensor"
    if parse_temperature_celsius(sensor.state, sensor.attributes.get("unit_of_measurement")) is None:
        return "invalid_room_sensor"
    entries = hass.config_entries.async_entries(DOMAIN)
    registry_entity = er.async_get(hass).async_get(backend.entity_id)
    if registry_entity is not None and registry_entity.platform == DOMAIN:
        return "recursive_backend"
    for other in entries:
        if other.entry_id != entry_id:
            values = {**other.data, **other.options}
            if values.get(CONF_HEATER) == backend.entity_id:
                return "already_configured"
    actuator = hass.states.get(backend.entity_id)
    if actuator is None or actuator.state in ("unknown", "unavailable"):
        return "invalid_actuator"
    if backend.kind == CONTROL_SWITCH:
        return None if actuator.state in ("on", "off") else "invalid_actuator"
    available_modes = actuator.attributes.get("hvac_modes", [])
    if (
        not isinstance(available_modes, (list, tuple))
        or "off" not in available_modes
        or any(mode not in available_modes for mode in backend.configured_modes)
    ):
        return "invalid_hvac_modes"
    try:
        if not int(actuator.attributes.get("supported_features", 0)) & ClimateEntityFeature.TARGET_TEMPERATURE:
            return "invalid_backend_limits"
        minimum = float(actuator.attributes["min_temp"])
        maximum = float(actuator.attributes["max_temp"])
        step = float(actuator.attributes.get("target_temp_step", 1.0))
        if not all(isfinite(v) for v in (minimum, maximum, step)) or minimum >= maximum or step <= 0:
            return "invalid_backend_limits"
    except (KeyError, TypeError, ValueError, OverflowError):
        return "invalid_backend_limits"
    outdoor_id = settings.get(CONF_OUTDOOR_SENSOR)
    if outdoor_id not in (None, ""):
        outdoor = hass.states.get(outdoor_id) if isinstance(outdoor_id, str) else None
        if outdoor is None or not outdoor.entity_id.startswith("sensor."):
            return "invalid_outdoor_sensor"
        # A temporarily unavailable sensor is accepted (it fails open at run
        # time); a reading that is not a temperature in a known unit is not.
        if outdoor.state not in ("unknown", "unavailable") and parse_temperature_celsius(
            outdoor.state, outdoor.attributes.get("unit_of_measurement")
        ) is None:
            return "invalid_outdoor_sensor"
    return validate_ac_settings(settings)
