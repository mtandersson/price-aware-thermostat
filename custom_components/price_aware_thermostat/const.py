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
CONF_SAFETY_ACK_AT: Final = "safety_acknowledged_at"

# Heat-pump/AC (climate backend) settings.
CONF_HEAT_COMPENSATION: Final = "heat_compensation"
CONF_COOL_COMPENSATION: Final = "cool_compensation"
CONF_WINDOW_SENSORS: Final = "window_contacts"
CONF_WINDOW_OPEN_DELAY: Final = "window_open_delay_seconds"
CONF_WINDOW_CLOSE_DELAY: Final = "window_close_delay_seconds"
CONF_MAX_DEVIATION: Final = "maximum_price_deviation"
# Optional outdoor-temperature lockouts (climate backend). An absent or empty
# sensor/threshold means the lockout is disabled, so no migration is needed.
CONF_OUTDOOR_SENSOR: Final = "outdoor_temperature_sensor"
CONF_COOL_MIN_OUTDOOR: Final = "cool_min_outdoor_temperature"
CONF_HEAT_MAX_OUTDOOR: Final = "heat_max_outdoor_temperature"
CONF_OUTDOOR_HYSTERESIS: Final = "outdoor_lockout_hysteresis"
# Dual-setpoint heat_cool (climate backend with both heat and cool). The
# stored low/high targets live in options like the base target; when absent
# they default to base and base + 2 (clamped, with the minimum gap), so a
# base target of 22 °C gives 22/24 °C.
CONF_HEAT_COOL_GAP: Final = "heat_cool_min_gap"
CONF_CHANGEOVER_MINUTES: Final = "heat_cool_changeover_minutes"
CONF_TARGET_LOW: Final = "target_temp_low"
CONF_TARGET_HIGH: Final = "target_temp_high"
DEFAULT_LOW_OFFSET: Final = 0.0
DEFAULT_HIGH_OFFSET: Final = 2.0

# Limits and deviation that applied before config entry minor version 2.
# Migration pins these on entries that relied on the old defaults, so an
# upgrade never widens an existing entry's limits.
LEGACY_MIN_TEMP: Final = 5.3
LEGACY_MAX_TEMP: Final = 7.5

DEFAULTS: Final[dict[str, float | int | str | bool]] = {
    CONF_BASE_TEMP: 6.0,
    CONF_MIN_TEMP: 5.0,
    CONF_MAX_TEMP: 25.0,
    CONF_MAX_DEVIATION: 0.8,
    CONF_LOOKAHEAD: 180,
    CONF_FILTER: "triangle",
    CONF_DEADBAND: 0.20,
    CONF_SENSITIVITY: 2.0,
    CONF_COLD_TOLERANCE: 0.3,
    CONF_HOT_TOLERANCE: 0.3,
    CONF_MIN_CYCLE: 300,
    CONF_MAX_SENSOR_AGE: 60,
    CONF_SHADOW: True,
    CONF_HEAT_COMPENSATION: 0.0,
    CONF_COOL_COMPENSATION: 0.0,
    CONF_WINDOW_OPEN_DELAY: 120,
    CONF_WINDOW_CLOSE_DELAY: 60,
    CONF_OUTDOOR_HYSTERESIS: 1.0,
    CONF_HEAT_COOL_GAP: 1.0,
    CONF_CHANGEOVER_MINUTES: 30,
}

SETTINGS_URL: Final = (
    "https://github.com/mtandersson/price-aware-thermostat/blob/main/docs/settings.md"
)
