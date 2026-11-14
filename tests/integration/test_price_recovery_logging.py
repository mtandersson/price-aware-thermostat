"""Verify that missing Nord Pool data logs one outage and one recovery event."""

import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant.common import MockConfigEntry

from custom_components.price_aware_thermostat.const import CONF_AREA, CONF_NORDPOOL_ENTRY, DOMAIN
from custom_components.price_aware_thermostat.coordinator import NordPoolPriceCoordinator


async def test_price_outage_and_recovery_are_logged_once(
    hass: HomeAssistant, caplog
) -> None:
    """Repeated failed fetches are throttled without generating log spam."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_AREA: "SE4", CONF_NORDPOOL_ENTRY: "fake-entry"},
    )
    entry.add_to_hass(hass)
    coordinator = NordPoolPriceCoordinator(hass, entry)
    status = {"unavailable": True}

    async def price_action(call: ServiceCall) -> dict:
        if status["unavailable"]:
            raise HomeAssistantError("Test price service is down")
        start = datetime.fromisoformat(str(call.data["date"])).replace(tzinfo=UTC)
        return {
            "SE4": [{
                "start": start.isoformat(),
                "end": (start + timedelta(hours=1)).isoformat(),
                "price": 300,
            }],
        }

    hass.services.async_register(
        "nordpool", "get_prices_for_date",
        price_action, supports_response=SupportsResponse.ONLY,
    )
    clock = datetime(2026, 11, 14, 7, 0, tzinfo=UTC)
    caplog.set_level(
        logging.INFO,
        logger="custom_components.price_aware_thermostat.coordinator",
    )
    with patch(
        "custom_components.price_aware_thermostat.coordinator.dt_util.now",
        return_value=clock,
    ) as current:
        assert await coordinator._async_update_data() == ()
        current.return_value = clock + timedelta(hours=1, minutes=1)
        assert await coordinator._async_update_data() == ()
        warnings = [
            record for record in caplog.records
            if "Nord Pool prices unavailable" in record.message
        ]
        assert len(warnings) == 1

        status["unavailable"] = False
        current.return_value = clock + timedelta(hours=2, minutes=2)
        assert len(await coordinator._async_update_data()) == 1
        current.return_value = clock + timedelta(hours=2, minutes=3)
        assert len(await coordinator._async_update_data()) == 1

    recovered = [
        record for record in caplog.records
        if "Nord Pool price data available again" in record.message
    ]
    assert len(recovered) == 1
    assert coordinator._today_prices_unavailable is False
