"""Pure thermostat decision logic, with no external service calls."""

from __future__ import annotations

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
