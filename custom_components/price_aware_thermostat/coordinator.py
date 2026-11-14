"""Fetch and cache official Nord Pool prices using Home Assistant services."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import CONF_AREA, CONF_NORDPOOL_ENTRY, DOMAIN
from .optimizer import PriceInterval, normalize_nordpool_response

if TYPE_CHECKING:
    from .config_entry import PriceAwareConfigEntry

_LOGGER = logging.getLogger(__name__)


FAILURE_SERVICE_ERROR = "service_error"
FAILURE_INVALID_RESPONSE = "invalid_response"
FAILURE_AREA_MISSING = "area_missing"
FAILURE_NO_PRICES = "no_prices"


class PriceFetchError(ValueError):
    """A price fetch failed for a coarse, privacy-safe reason."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


def _failure_kind(exc: Exception) -> str:
    """Map an exception to a coarse failure kind without exposing its text."""
    if isinstance(exc, PriceFetchError):
        return exc.kind
    if isinstance(exc, HomeAssistantError):
        return FAILURE_SERVICE_ERROR
    return FAILURE_INVALID_RESPONSE


class NordPoolPriceCoordinator(DataUpdateCoordinator[tuple[PriceInterval, ...]]):
    """Cache calendar-day prices and make inexpensive periodic refresh checks.

    Service calls are made once per successfully fetched day; attempts to fetch
    tomorrow start after 13:00 local time. Errors do not disable the thermostat.
    """

    def __init__(self, hass: HomeAssistant, entry: PriceAwareConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"Price-Aware Thermostat {entry.entry_id}",
            update_interval=timedelta(minutes=15),
            config_entry=entry,
        )
        self._entry = entry
        self._source_entry_id = str(entry.data[CONF_NORDPOOL_ENTRY])
        self._area = str(entry.data[CONF_AREA])
        self._by_day: dict[date, tuple[PriceInterval, ...]] = {}
        self._tomorrow_retry_after: datetime | None = None
        self._today_retry_after: datetime | None = None
        self._today_retry_date: date | None = None
        self._today_prices_unavailable = False
        self._last_success: datetime | None = None
        self._today_failure: str | None = None
        self._tomorrow_failure: str | None = None

    @property
    def last_success(self) -> datetime | None:
        """When prices were last fetched successfully."""
        return self._last_success

    @property
    def today_failure(self) -> str | None:
        """Coarse failure kind of the latest current-day fetch, if it failed."""
        return self._today_failure

    @property
    def tomorrow_failure(self) -> str | None:
        """Coarse failure kind of the latest next-day fetch, if it failed."""
        return self._tomorrow_failure

    @property
    def today_retry_after(self) -> datetime | None:
        """Earliest next current-day attempt while today's prices are missing."""
        if self.today_cached:
            return None
        return self._today_retry_after

    @property
    def tomorrow_retry_after(self) -> datetime | None:
        """Earliest next next-day attempt while tomorrow's prices are missing."""
        if self.tomorrow_cached:
            return None
        return self._tomorrow_retry_after

    @property
    def today_cached(self) -> bool:
        """Whether the local current day's prices are cached."""
        return dt_util.now().date() in self._by_day

    @property
    def tomorrow_cached(self) -> bool:
        """Whether the next local day's prices are cached."""
        return dt_util.now().date() + timedelta(days=1) in self._by_day

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
            raise PriceFetchError(
                FAILURE_INVALID_RESPONSE, "Nord Pool returned an invalid response"
            )
        if self._area not in response:
            raise PriceFetchError(
                FAILURE_AREA_MISSING, "Nord Pool response lacks the configured area"
            )
        records = normalize_nordpool_response(response, self._area)
        if not records:
            raise PriceFetchError(
                FAILURE_NO_PRICES, f"No Nord Pool prices for {day.isoformat()}"
            )
        ordered = sorted(records, key=lambda interval: interval.start)
        for earlier, later in zip(ordered, ordered[1:], strict=False):
            if earlier.end > later.start:
                raise PriceFetchError(
                    FAILURE_INVALID_RESPONSE, "Nord Pool returned overlapping intervals"
                )
        return tuple(ordered)

    async def _async_update_data(self) -> tuple[PriceInterval, ...]:
        """Refresh only missing calendar days and publish cached prices."""
        self._check_price_source()
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
            # A failure recorded for yesterday's "tomorrow" no longer applies today.
            self._tomorrow_failure = None

        if today not in self._by_day and (
            self._today_retry_after is None or local_now >= self._today_retry_after
        ):
            self._today_retry_after = local_now + timedelta(hours=1)
            try:
                self._by_day[today] = await self._fetch_day(today)
            except (HomeAssistantError, ValueError, LookupError) as exc:
                self._today_failure = _failure_kind(exc)
                if not self._today_prices_unavailable:
                    _LOGGER.warning(
                        "Nord Pool prices unavailable; using the base temperature: %s",
                        exc,
                    )
                    self._today_prices_unavailable = True
            else:
                self._today_failure = None
                self._last_success = local_now
                if self._today_prices_unavailable:
                    _LOGGER.info("Nord Pool price data available again")
                    self._today_prices_unavailable = False

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
                self._tomorrow_failure = _failure_kind(exc)
                _LOGGER.debug("Tomorrow's Nord Pool prices are not yet available: %s", exc)
            else:
                self._tomorrow_failure = None
                self._last_success = local_now

        return tuple(
            sorted(
                (interval for day in self._by_day.values() for interval in day),
                key=lambda interval: interval.start,
            )
        )

    def _check_price_source(self) -> None:
        """Surface deleted/misconfigured sources without stopping local control.

        Transient API failures and unpublished prices require no user repair.
        Keep this issue across unloads so an ignored issue stays ignored; HA
        removes entry-bound issues when the thermostat entry is removed.
        """
        source = self.hass.config_entries.async_get_entry(self._source_entry_id)
        issue_id = f"price_source_{self._entry.entry_id}"
        if source is not None and source.domain == "nordpool" and self._area in {
            str(area).upper() for area in source.data.get("areas", ())
        }:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            return
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
            config_entry_id=self._entry.entry_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="invalid_price_source",
        )
