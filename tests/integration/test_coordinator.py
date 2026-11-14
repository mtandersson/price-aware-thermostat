"""Contract and cache tests for the official Nord Pool service adapter."""

from datetime import datetime, timedelta, timezone

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant.common import MockConfigEntry

from custom_components.price_aware_thermostat.const import (
    CONF_AREA,
    CONF_NORDPOOL_ENTRY,
    DOMAIN,
)
from custom_components.price_aware_thermostat.coordinator import NordPoolPriceCoordinator


async def test_fetches_each_day_once_and_converts_prices(hass: HomeAssistant) -> None:
    """A service response is normalized and cached without repeated requests."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_AREA: "SE4", CONF_NORDPOOL_ENTRY: "nordpool-test-entry"},
    )
    requested: list[str] = []

    async def fake_action(call: ServiceCall) -> dict:
        assert call.domain == "nordpool"
        assert call.service == "get_prices_for_date"
        assert call.data["currency"] == "SEK"
        assert call.data["areas"] == ["SE4"]
        requested.append(call.data["date"])
        start = datetime.fromisoformat(call.data["date"]).replace(tzinfo=timezone.utc)
        return {
            "SE4": [
                {
                    "start": start.isoformat(),
                    "end": (start + timedelta(hours=1)).isoformat(),
                    "price": 240.0,
                }
            ]
        }

    hass.services.async_register(
        "nordpool", "get_prices_for_date",
        fake_action, supports_response=SupportsResponse.ONLY,
    )
    coordinator = NordPoolPriceCoordinator(hass, entry)
    await coordinator.async_refresh()
    initial_calls = len(requested)
    await coordinator.async_refresh()
    assert len(requested) == initial_calls
    assert initial_calls >= 1
    assert coordinator.data
    assert all(round(item.price_sek_per_kwh, 3) == 0.240 for item in coordinator.data)


async def test_missing_prices_do_not_break_local_thermostat(hass: HomeAssistant) -> None:
    """An unavailable price action returns an empty forecast safely."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_AREA: "SE4", CONF_NORDPOOL_ENTRY: "nordpool-test-entry"},
    )

    async def unavailable(call: ServiceCall) -> None:
        raise HomeAssistantError("Nord Pool unavailable")

    hass.services.async_register(
        "nordpool", "get_prices_for_date",
        unavailable, supports_response=SupportsResponse.ONLY,
    )
    coordinator = NordPoolPriceCoordinator(hass, entry)
    await coordinator.async_refresh()
    assert coordinator.data == ()
    await coordinator.async_refresh()
    assert coordinator.data == ()
