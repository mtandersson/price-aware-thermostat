"""Nord Pool cache behavior across days and temporary price-service failures."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from pytest_homeassistant.common import MockConfigEntry

from custom_components.price_aware_thermostat.const import (
    CONF_AREA,
    CONF_NORDPOOL_ENTRY,
    DOMAIN,
)
from custom_components.price_aware_thermostat.coordinator import NordPoolPriceCoordinator


def _coordinator(hass: HomeAssistant) -> NordPoolPriceCoordinator:
    """Create a price coordinator isolated from physical climate entities."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_AREA: "SE4", CONF_NORDPOOL_ENTRY: "mock-source"},
    )
    return NordPoolPriceCoordinator(hass, entry)


def _response(day: str) -> dict[str, list[dict[str, str | float]]]:
    """Return one valid hourly interval in the official action's format."""
    start = datetime.fromisoformat(day).replace(tzinfo=UTC)
    return {
        "SE4": [{
            "start": start.isoformat(),
            "end": (start + timedelta(hours=1)).isoformat(),
            "price": 120.0,
        }]
    }


async def test_tomorrow_cache_and_day_rollover(hass: HomeAssistant) -> None:
    """Fetch today and tomorrow once; retain tomorrow when the calendar rolls."""
    requested: list[str] = []

    async def price_action(call: ServiceCall) -> dict:
        day = str(call.data["date"])
        requested.append(day)
        return _response(day)

    hass.services.async_register(
        "nordpool", "get_prices_for_date",
        price_action, supports_response=SupportsResponse.ONLY,
    )
    coordinator = _coordinator(hass)
    fake_now = datetime(2026, 11, 14, 14, 0, tzinfo=UTC)
    with patch(
        "custom_components.price_aware_thermostat.coordinator.dt_util.now",
        return_value=fake_now,
    ) as now:
        initial = await coordinator._async_update_data()
        assert len(initial) == 2
        assert requested == ["2026-11-14", "2026-11-15"]
        await coordinator._async_update_data()
        assert requested == ["2026-11-14", "2026-11-15"]

        now.return_value = fake_now + timedelta(days=1)
        following = await coordinator._async_update_data()
        assert len(following) == 2
        assert requested == ["2026-11-14", "2026-11-15", "2026-11-16"]
        assert set(coordinator._by_day) == {
            datetime(2026, 11, 15).date(),
            datetime(2026, 11, 16).date(),
        }


async def test_tomorrow_missing_retries_after_cooldown(hass: HomeAssistant) -> None:
    """An unpublished next-day series does not block the base-temperature path."""
    requested: list[str] = []

    async def price_action(call: ServiceCall) -> dict:
        day = str(call.data["date"])
        requested.append(day)
        if day == "2026-11-15" and requested.count(day) == 1:
            return {"SE4": []}
        return _response(day)

    hass.services.async_register(
        "nordpool", "get_prices_for_date",
        price_action, supports_response=SupportsResponse.ONLY,
    )
    coordinator = _coordinator(hass)
    fake_now = datetime(2026, 11, 14, 14, 0, tzinfo=UTC)
    with patch(
        "custom_components.price_aware_thermostat.coordinator.dt_util.now",
        return_value=fake_now,
    ) as now:
        initial = await coordinator._async_update_data()
        assert len(initial) == 1
        assert requested.count("2026-11-15") == 1

        now.return_value = fake_now + timedelta(minutes=29)
        await coordinator._async_update_data()
        assert requested.count("2026-11-15") == 1

        now.return_value = fake_now + timedelta(minutes=31)
        refreshed = await coordinator._async_update_data()
        assert requested.count("2026-11-15") == 2
        assert len(refreshed) == 2


async def test_unpublished_current_day_uses_price_fallback(
    hass: HomeAssistant,
) -> None:
    """An empty current-day price series does not disable integration startup."""
    async def price_action(call: ServiceCall) -> dict:
        return {"SE4": []}

    hass.services.async_register(
        "nordpool", "get_prices_for_date",
        price_action, supports_response=SupportsResponse.ONLY,
    )
    coordinator = _coordinator(hass)
    with patch(
        "custom_components.price_aware_thermostat.coordinator.dt_util.now",
        return_value=datetime(2026, 11, 14, 10, 0, tzinfo=UTC),
    ):
        assert await coordinator._async_update_data() == ()
        assert len(coordinator._by_day) == 0
