"""Privacy-preserving diagnostics for Price-Aware Thermostat.

The download describes the optimization setup and cached price coverage.
It intentionally excludes entity identifiers, config-entry identifiers,
exact room temperatures, device names, and arbitrary options dictionaries.
"""

from __future__ import annotations

from datetime import UTC, datetime
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


def _iso(value: datetime | None) -> str | None:
    """Format a timestamp as UTC ISO 8601, or None when unset."""
    return value.astimezone(UTC).isoformat() if value is not None else None


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
        "version": 2,
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
        "price_freshness": {
            "last_success_utc": _iso(coordinator.last_success),
            "today_cached": coordinator.today_cached,
            "tomorrow_cached": coordinator.tomorrow_cached,
            "today_failure": coordinator.today_failure,
            "tomorrow_failure": coordinator.tomorrow_failure,
            "today_retry_after_utc": _iso(coordinator.today_retry_after),
            "tomorrow_retry_after_utc": _iso(coordinator.tomorrow_retry_after),
        },
    }
