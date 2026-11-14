"""Coordinator behaviour with realistic synthetic Nord Pool responses."""

from datetime import date, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from pytest_homeassistant.common import MockConfigEntry

from custom_components.price_aware_thermostat.const import (
    CONF_AREA,
    CONF_NORDPOOL_ENTRY,
    DOMAIN,
)
from custom_components.price_aware_thermostat.coordinator import NordPoolPriceCoordinator
from tests.integration.nordpool_fixtures import STOCKHOLM, day_response

QUARTER = timedelta(minutes=15)
HOUR = timedelta(hours=1)
NOW_PATH = "custom_components.price_aware_thermostat.coordinator.dt_util.now"


def _setup(hass: HomeAssistant, handler: Any) -> NordPoolPriceCoordinator:
    hass.services.async_register(
        "nordpool", "get_prices_for_date",
        handler, supports_response=SupportsResponse.ONLY,
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_AREA: "SE4", CONF_NORDPOOL_ENTRY: "mock-source"},
    )
    return NordPoolPriceCoordinator(hass, entry)


@pytest.mark.parametrize(
    ("day", "step", "count", "hours"),
    [
        (date(2026, 11, 14), QUARTER, 96, 24),
        (date(2026, 11, 14), HOUR, 24, 24),
        (date(2026, 3, 29), QUARTER, 92, 23),
        (date(2026, 3, 29), HOUR, 23, 23),
        (date(2026, 10, 25), QUARTER, 100, 25),
        (date(2026, 10, 25), HOUR, 25, 25),
    ],
)
async def test_full_day_fixtures_normalize_contiguously(
    hass: HomeAssistant, day: date, step: timedelta, count: int, hours: int
) -> None:
    """Quarter-hour, hourly and DST days normalize to gap-free intervals."""
    async def action(call: ServiceCall) -> dict:
        assert call.data["areas"] == ["SE4"]
        return day_response(date.fromisoformat(call.data["date"]), step)

    coordinator = _setup(hass, action)
    # 12:00 local on the day, before tomorrow's 13:00 fetch window.
    local_now = datetime(day.year, day.month, day.day, 12, tzinfo=STOCKHOLM)
    with patch(NOW_PATH, return_value=local_now):
        data = await coordinator._async_update_data()
    assert len(data) == count
    assert sum((i.end - i.start for i in data), timedelta()) == timedelta(hours=hours)
    midnight = datetime(day.year, day.month, day.day, tzinfo=STOCKHOLM)
    assert data[0].start == midnight
    next_midnight = datetime(day.year, day.month, day.day, tzinfo=STOCKHOLM) + timedelta(days=1)
    assert data[-1].end == next_midnight
    for earlier, later in zip(data, data[1:], strict=False):
        assert earlier.end == later.start
    # SE3 prices in the response are ignored; SE4 prices become SEK/kWh.
    assert data[0].price_sek_per_kwh == pytest.approx(0.4)


async def test_dst_day_boundary_with_tomorrow_fetch(hass: HomeAssistant) -> None:
    """The day before spring DST caches a 23-hour tomorrow after 13:00."""
    requested: list[str] = []

    async def action(call: ServiceCall) -> dict:
        requested.append(call.data["date"])
        return day_response(date.fromisoformat(call.data["date"]), QUARTER)

    coordinator = _setup(hass, action)
    with patch(NOW_PATH, return_value=datetime(2026, 3, 28, 14, tzinfo=STOCKHOLM)):
        data = await coordinator._async_update_data()
    assert requested == ["2026-03-28", "2026-03-29"]
    assert len(data) == 96 + 92
    for earlier, later in zip(data, data[1:], strict=False):
        assert earlier.end == later.start


async def test_cached_tomorrow_becomes_today_across_local_midnight(
    hass: HomeAssistant,
) -> None:
    """Cached tomorrow prices serve the new day without another request."""
    requested: list[str] = []

    async def action(call: ServiceCall) -> dict:
        requested.append(call.data["date"])
        return day_response(date.fromisoformat(call.data["date"]), QUARTER)

    coordinator = _setup(hass, action)
    with patch(
        NOW_PATH, return_value=datetime(2026, 11, 14, 23, 50, tzinfo=STOCKHOLM)
    ) as now:
        await coordinator._async_update_data()
        assert requested == ["2026-11-14", "2026-11-15"]
        now.return_value = datetime(2026, 11, 15, 0, 5, tzinfo=STOCKHOLM)
        data = await coordinator._async_update_data()
        # Before 13:00 nothing new is requested; yesterday's rows are dropped.
        assert requested == ["2026-11-14", "2026-11-15"]
        assert coordinator.today_cached is True
        assert coordinator.tomorrow_cached is False
        assert len(data) == 96
        assert data[0].start == datetime(2026, 11, 15, tzinfo=STOCKHOLM)


async def test_response_without_configured_area_falls_back(
    hass: HomeAssistant,
) -> None:
    """A response containing only other areas yields no prices, not an error."""
    async def action(call: ServiceCall) -> dict:
        response = day_response(date.fromisoformat(call.data["date"]), HOUR)
        return {"SE3": response["SE3"]}

    coordinator = _setup(hass, action)
    with patch(NOW_PATH, return_value=datetime(2026, 11, 14, 10, tzinfo=STOCKHOLM)):
        assert await coordinator._async_update_data() == ()
    assert coordinator.today_failure == "area_missing"


async def test_overlapping_intervals_are_rejected(hass: HomeAssistant) -> None:
    """Overlapping price rows never enter the cache."""
    async def action(call: ServiceCall) -> dict:
        records = day_response(date.fromisoformat(call.data["date"]), HOUR)["SE4"]
        records[5] = {**records[5], "end": records[6]["end"]}
        return {"SE4": records}

    coordinator = _setup(hass, action)
    with patch(NOW_PATH, return_value=datetime(2026, 11, 14, 10, tzinfo=STOCKHOLM)):
        assert await coordinator._async_update_data() == ()
    assert coordinator.today_failure == "invalid_response"
    assert coordinator.today_cached is False


async def test_shuffled_response_is_cached_in_time_order(hass: HomeAssistant) -> None:
    """Unordered but valid rows are sorted before caching."""
    async def action(call: ServiceCall) -> dict:
        records = day_response(date.fromisoformat(call.data["date"]), HOUR)["SE4"]
        return {"SE4": list(reversed(records))}

    coordinator = _setup(hass, action)
    with patch(NOW_PATH, return_value=datetime(2026, 11, 14, 10, tzinfo=STOCKHOLM)):
        data = await coordinator._async_update_data()
    assert [i.start for i in data] == sorted(i.start for i in data)
    assert len(data) == 24


@pytest.mark.parametrize(
    "bad_row",
    [
        {"start": "2026-11-14T00:00:00+01:00", "end": "2026-11-14T00:00:00+01:00", "price": 1},
        {"start": "2026-11-14T01:00:00+01:00", "end": "2026-11-14T00:00:00+01:00", "price": 1},
        {"start": "2026-11-14T00:00:00", "end": "2026-11-14T01:00:00", "price": 1},
        {"start": "not a time", "end": "2026-11-14T01:00:00+01:00", "price": 1},
        {"start": "2026-11-14T00:00:00+01:00", "end": "2026-11-14T01:00:00+01:00", "price": None},
    ],
)
async def test_invalid_intervals_are_rejected(
    hass: HomeAssistant, bad_row: dict[str, Any]
) -> None:
    """Zero/negative duration, naive times and unparsable rows are invalid."""
    async def action(call: ServiceCall) -> dict:
        return {"SE4": [bad_row]}

    coordinator = _setup(hass, action)
    with patch(NOW_PATH, return_value=datetime(2026, 11, 14, 10, tzinfo=STOCKHOLM)):
        assert await coordinator._async_update_data() == ()
    assert coordinator.today_failure == "invalid_response"
