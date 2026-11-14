"""Contract and cache tests for the official Nord Pool service adapter."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from homeassistant.core import HomeAssistant
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
    requested = []

    async def fake_action(domain, service, data, **kwargs):
        assert domain == "nordpool"
        assert service == "get_prices_for_date"
        assert data["currency"] == "SEK"
        assert data["areas"] == ["SE4"]
        assert kwargs["return_response"] is True
        requested.append(data["date"])
        start = datetime.fromisoformat(data["date"]).replace(tzinfo=timezone.utc)
        return {
            "SE4": [
                {
                    "start": start.isoformat(),
                    "end": (start + timedelta(hours=1)).isoformat(),
                    "price": 240.0,
                }
            ]
        }

    coordinator = NordPoolPriceCoordinator(hass, entry)
    with patch.object(hass.services, "async_call", side_effect=fake_action):
        await coordinator.async_refresh()
        initial_calls = len(requested)
        await coordinator.async_refresh()
    assert len(requested) == initial_calls
    assert initial_calls >= 1
    assert coordinator.data
    assert all(round(item.price_sek_per_kwh, 3) == 0.240 for item in coordinator.data)


async def test_missing_prices_do_not_break_local_thermostat(hass: HomeAssistant) -> None:
    """An unavailable price service returns an empty price forecast safely."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_AREA: "SE4", CONF_NORDPOOL_ENTRY: "nordpool-test-entry"},
    )
    coordinator = NordPoolPriceCoordinator(hass, entry)
    with patch.object(
        hass.services,
        "async_call",
        side_effect=HomeAssistantError("Nord Pool unavailable"),
    ):
        await coordinator.async_refresh()
        assert coordinator.data == ()
        await coordinator.async_refresh()
        assert coordinator.data == ()
