"""Privacy-preserving diagnostics for Price-Aware Thermostat.

The download describes the optimization setup and cached price coverage.
It intentionally excludes entity identifiers, config-entry identifiers,
exact room temperatures, device names, and arbitrary options dictionaries.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .config_entry import PriceAwareConfigEntry
from .const import (
    CONF_AREA,
    CONF_FILTER,
    CONF_LOOKAHEAD,
    CONF_MAX_SENSOR_AGE,
    CONF_SHADOW,
    DEFAULTS,
)
from .coordinator import NordPoolPriceCoordinator


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: PriceAwareConfigEntry
) -> dict[str, Any]:
    """Return non-sensitive debug information for this config entry."""
    coordinator: NordPoolPriceCoordinator = entry.runtime_data
    settings = {**entry.data, **entry.options}
    intervals = tuple(coordinator.data or ())
    now = dt_util.utcnow()
    current_interval = next(
        (interval for interval in intervals if interval.start <= now < interval.end),
        None,
    )
    return {
        "version": 1,
        "configuration": {
            "shadow_mode": bool(settings.get(CONF_SHADOW, True)),
            "market_area": str(settings.get(CONF_AREA, "")),
            "filter_type": str(settings.get(CONF_FILTER, DEFAULTS[CONF_FILTER])),
            "lookahead_minutes": int(
                settings.get(CONF_LOOKAHEAD, DEFAULTS[CONF_LOOKAHEAD])
            ),
            "max_sensor_age_minutes": int(
                settings.get(CONF_MAX_SENSOR_AGE, DEFAULTS[CONF_MAX_SENSOR_AGE])
            ),
        },
        "price_cache": {
            "interval_count": len(intervals),
            "current_price_available": current_interval is not None,
            "first_interval_utc": (
                min(interval.start for interval in intervals).isoformat()
                if intervals
                else None
            ),
            "last_interval_utc": (
                max(interval.end for interval in intervals).isoformat()
                if intervals
                else None
            ),
        },
        "status": {
            "last_refresh_success": coordinator.last_update_success,
        },
    }
