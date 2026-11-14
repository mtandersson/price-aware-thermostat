"""Constants for the Price-Aware Thermostat integration."""

from typing import Final

DOMAIN: Final = "price_aware_thermostat"

CONF_CONTROL_TYPE: Final = "control_type"
CONF_HVAC_MODES: Final = "controlled_hvac_modes"
CONTROL_SWITCH: Final = "switch"
CONTROL_CLIMATE: Final = "climate"

CONF_NAME: Final = "name"
CONF_HEATER: Final = "heater"
CONF_SENSOR: Final = "temperature_sensor"
CONF_NORDPOOL_ENTRY: Final = "nordpool_config_entry"
CONF_AREA: Final = "market_area"
CONF_BASE_TEMP: Final = "base_temperature"
CONF_MIN_TEMP: Final = "minimum_temperature"
CONF_MAX_TEMP: Final = "maximum_temperature"
CONF_LOOKAHEAD: Final = "lookahead_minutes"
CONF_FILTER: Final = "filter_type"
CONF_DEADBAND: Final = "deadband_sek_per_kwh"
CONF_SENSITIVITY: Final = "sensitivity_c_per_sek_per_kwh"
CONF_COLD_TOLERANCE: Final = "cold_tolerance"
CONF_HOT_TOLERANCE: Final = "hot_tolerance"
CONF_MIN_CYCLE: Final = "min_cycle_seconds"
CONF_MAX_SENSOR_AGE: Final = "max_sensor_age_minutes"
CONF_SHADOW: Final = "shadow_mode"
CONF_SAFETY_ACK: Final = "safety_acknowledged"

DEFAULTS: Final[dict[str, float | int | str | bool]] = {
    CONF_BASE_TEMP: 6.0,
    CONF_MIN_TEMP: 5.3,
    CONF_MAX_TEMP: 7.5,
    CONF_LOOKAHEAD: 180,
    CONF_FILTER: "triangle",
    CONF_DEADBAND: 0.20,
    CONF_SENSITIVITY: 2.0,
    CONF_COLD_TOLERANCE: 0.3,
    CONF_HOT_TOLERANCE: 0.3,
    CONF_MIN_CYCLE: 300,
    CONF_MAX_SENSOR_AGE: 60,
    CONF_SHADOW: True,
}
