"""Privacy and coverage tests for downloadable HA diagnostics."""

import json
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant.common import MockConfigEntry

from custom_components.price_aware_thermostat.const import (
    CONF_AREA,
    CONF_NORDPOOL_ENTRY,
    CONF_SHADOW,
    DOMAIN,
)
from custom_components.price_aware_thermostat.coordinator import NordPoolPriceCoordinator
from custom_components.price_aware_thermostat.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.price_aware_thermostat.optimizer import PriceInterval


async def test_diagnostics_show_price_coverage_without_exposing_entity_ids(
    hass: HomeAssistant,
) -> None:
    """The diagnostics payload must omit personal heater and sensor identifiers."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="My private room name",
        data={
            CONF_NORDPOOL_ENTRY: "secret-config-entry-id",
            CONF_AREA: "SE4",
            "heater": "switch.private_heater",
            "temperature_sensor": "sensor.private_temperature",
        },
        options={CONF_SHADOW: True},
    )
    entry.add_to_hass(hass)
    coordinator = NordPoolPriceCoordinator(hass, entry)
    entry.runtime_data = coordinator
    now = dt_util.utcnow()
    coordinator.async_set_updated_data(
        (
            PriceInterval(
                now - timedelta(minutes=15),
                now + timedelta(minutes=15),
                0.25,
            ),
        )
    )
    result = await async_get_config_entry_diagnostics(hass, entry)
    assert result["version"] == 1
    assert result["configuration"]["shadow_mode"] is True
    assert result["price_cache"]["interval_count"] == 1
    assert result["price_cache"]["current_price_available"] is True
    encoded = json.dumps(result)
    assert "secret-config-entry-id" not in encoded
    assert "private_heater" not in encoded
    assert "private_temperature" not in encoded
    assert "My private room name" not in encoded


async def test_diagnostics_handle_missing_forecast(hass: HomeAssistant) -> None:
    """Empty prices are a reportable condition, not a diagnostics exception."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_NORDPOOL_ENTRY: "test-entry", CONF_AREA: "SE4"},
    )
    entry.add_to_hass(hass)
    coordinator = NordPoolPriceCoordinator(hass, entry)
    entry.runtime_data = coordinator
    result = await async_get_config_entry_diagnostics(hass, entry)
    assert result["price_cache"]["interval_count"] == 0
    assert result["price_cache"]["current_price_available"] is False
    assert result["price_cache"]["first_interval_utc"] is None
