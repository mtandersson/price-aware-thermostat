"""Pure thermostat decision logic, with no external service calls."""

from __future__ import annotations

from datetime import datetime, timedelta
from math import isfinite


def desired_heater_state(
    current_c: float | None,
    target_c: float,
    currently_on: bool,
    cold_tolerance: float,
    hot_tolerance: float,
) -> bool | None:
    """Return desired heating state, or None for an invalid temperature reading.

    Hysteresis uses the current relay state to avoid oscillating at the target.
    An invalid sensor must be handled explicitly by the Home Assistant adapter.
    """
    if current_c is None or not isfinite(current_c) or not isfinite(target_c):
        return None
    if cold_tolerance < 0 or hot_tolerance < 0:
        raise ValueError("Thermostat tolerances must be nonnegative")
    if currently_on:
        return current_c < target_c + hot_tolerance
    return current_c <= target_c - cold_tolerance


def is_temperature_sample_fresh(
    last_reported: datetime | None,
    now: datetime,
    max_age: timedelta,
) -> bool:
    """Accept only recently reported temperature readings.

    `last_reported` (not `last_changed`) allows sensors that periodically
    report the same temperature. A missing timestamp is not evidence of
    a healthy sensor. This check guards software logic; hardware thermal
    safeguards remain mandatory.
    """
    if last_reported is None or last_reported.tzinfo is None or now.tzinfo is None:
        return False
    if max_age <= timedelta(0):
        raise ValueError("Maximum sensor age must be positive")
    age = now - last_reported
    return timedelta(0) <= age <= max_age


def parse_temperature_celsius(raw: str, unit: str | None) -> float | None:
    """Validate and normalize a measured temperature to Celsius.

    The climate entity advertises Celsius as its native temperature unit.
    Never interpret a Fahrenheit or Kelvin reading as Celsius, and do not
    guess the units of an unlabelled reading. This is deliberately independent
    of Home Assistant imports so the safety rule is easily unit tested.
    """
    try:
        value = float(raw)
    except (ValueError, TypeError, OverflowError):
        return None
    if not isfinite(value):
        return None
    if unit == "°C":
        normalized = value
    elif unit == "°F":
        normalized = (value - 32.0) * (5.0 / 9.0)
    elif unit == "K":
        normalized = value - 273.15
    else:
        return None
    return normalized if isfinite(normalized) else None
