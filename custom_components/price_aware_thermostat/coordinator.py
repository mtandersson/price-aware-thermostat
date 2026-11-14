"""Fetch and cache official Nord Pool prices using Home Assistant services."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import CONF_AREA, CONF_NORDPOOL_ENTRY
from .optimizer import PriceInterval, normalize_nordpool_response

_LOGGER = logging.getLogger(__name__)


class NordPoolPriceCoordinator(DataUpdateCoordinator[tuple[PriceInterval, ...]]):
    """Cache calendar-day prices and make inexpensive periodic refresh checks.

    Service calls are made once per successfully fetched day; attempts to fetch
    tomorrow start after 13:00 local time. Errors do not disable the thermostat.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"Price-Aware Thermostat {entry.entry_id}",
            update_interval=timedelta(minutes=15),
        )
        self._source_entry_id = str(entry.data[CONF_NORDPOOL_ENTRY])
        self._area = str(entry.data[CONF_AREA])
        self._by_day: dict[date, tuple[PriceInterval, ...]] = {}
        self._tomorrow_retry_after: datetime | None = None
        self._today_retry_after: datetime | None = None
        self._today_retry_date: date | None = None

    async def _fetch_day(self, day: date) -> tuple[PriceInterval, ...]:
        """Call the official action with an explicit SEK currency."""
        response = await self.hass.services.async_call(
            "nordpool",
            "get_prices_for_date",
            {
                "config_entry": self._source_entry_id,
                "date": day.isoformat(),
                "areas": [self._area],
                "currency": "SEK",
            },
            blocking=True,
            return_response=True,
        )
        if not isinstance(response, dict):
            raise ValueError("Nord Pool returned an invalid response")
        records = normalize_nordpool_response(response, self._area)
        if not records:
            raise ValueError(f"No Nord Pool prices for {day.isoformat()}")
        return tuple(records)

    async def _async_update_data(self) -> tuple[PriceInterval, ...]:
        """Refresh only missing calendar days and publish cached prices."""
        local_now = dt_util.now()
        today = local_now.date()
        tomorrow = today + timedelta(days=1)

        for old_day in tuple(self._by_day):
            if old_day < today or old_day > tomorrow:
                del self._by_day[old_day]

        if self._today_retry_date != today:
            self._today_retry_date = today
            self._today_retry_after = None
            self._tomorrow_retry_after = None

        if today not in self._by_day and (
            self._today_retry_after is None or local_now >= self._today_retry_after
        ):
            self._today_retry_after = local_now + timedelta(hours=1)
            try:
                self._by_day[today] = await self._fetch_day(today)
            except (HomeAssistantError, ValueError, LookupError) as exc:
                _LOGGER.warning(
                    "Could not fetch today's Nord Pool prices; base temperature remains active: %s",
                    exc,
                )

        # Tomorrow's prices are normally published after 13:00 CET/CEST.
        if (
            local_now.hour >= 13
            and tomorrow not in self._by_day
            and (
                self._tomorrow_retry_after is None
                or local_now >= self._tomorrow_retry_after
            )
        ):
            self._tomorrow_retry_after = local_now + timedelta(minutes=30)
            try:
                self._by_day[tomorrow] = await self._fetch_day(tomorrow)
            except (HomeAssistantError, ValueError, LookupError) as exc:
                _LOGGER.debug("Tomorrow's Nord Pool prices are not yet available: %s", exc)

        return tuple(
            sorted(
                (interval for day in self._by_day.values() for interval in day),
                key=lambda interval: interval.start,
            )
        )
