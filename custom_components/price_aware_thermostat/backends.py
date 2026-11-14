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
    CONF_HEATER,
    CONF_HVAC_MODES,
    CONF_SENSOR,
    CONTROL_CLIMATE,
    CONTROL_SWITCH,
    DOMAIN,
)
from .control import parse_temperature_celsius


@dataclass(frozen=True)
class ControlBackend:
    """Stable actuator identity and implemented command/mode capabilities.

    Climate mode selection records future intent. Only OFF is implemented for
    its configuration preview; live AC control belongs to issue #23.
    """

    kind: str
    entity_id: str
    configured_modes: tuple[str, ...]

    @property
    def supports_live_control(self) -> bool:
        return self.kind == CONTROL_SWITCH

    @property
    def implemented_modes(self) -> tuple[str, ...]:
        return ("heat", "off") if self.kind == CONTROL_SWITCH else ("off",)


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
    return None
