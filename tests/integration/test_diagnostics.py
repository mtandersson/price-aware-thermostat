"""Privacy and coverage tests for downloadable HA diagnostics."""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
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
    assert result["version"] == 2
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
    assert result["price_freshness"]["last_success_utc"] is None
    assert result["price_freshness"]["today_cached"] is False


NOW_PATH = "custom_components.price_aware_thermostat.coordinator.dt_util.now"


def _hourly(day: str) -> list[dict[str, str | float]]:
    start = datetime.fromisoformat(day).replace(tzinfo=UTC)
    return [{
        "start": start.isoformat(),
        "end": (start + timedelta(hours=1)).isoformat(),
        "price": 250.0,
    }]


def _scripted_entry(
    hass: HomeAssistant, outcome: dict
) -> tuple[MockConfigEntry, NordPoolPriceCoordinator]:
    """Create an entry whose price action returns ``outcome["next"]``."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="My private room name",
        data={
            CONF_NORDPOOL_ENTRY: "secret-config-entry-id",
            CONF_AREA: "SE4",
            "heater": "switch.private_heater",
            "temperature_sensor": "sensor.private_temperature",
        },
    )
    entry.add_to_hass(hass)
    coordinator = NordPoolPriceCoordinator(hass, entry)
    entry.runtime_data = coordinator

    async def action(call: ServiceCall) -> dict:
        result = outcome["next"]
        if isinstance(result, Exception):
            raise result
        if callable(result):
            return result(str(call.data["date"]))
        return result

    hass.services.async_register(
        "nordpool", "get_prices_for_date",
        action, supports_response=SupportsResponse.ONLY,
    )
    return entry, coordinator


async def test_freshness_after_failure_kinds_and_recovery(hass: HomeAssistant) -> None:
    """Each failure kind is reported coarsely, then cleared on recovery."""
    outcome: dict = {"next": None}
    entry, coordinator = _scripted_entry(hass, outcome)
    clock = datetime(2026, 11, 14, 10, 0, tzinfo=UTC)
    failures = [
        (HomeAssistantError("secret-config-entry-id exploded"), "service_error"),
        ({"SE3": []}, "area_missing"),
        ({"SE4": []}, "no_prices"),
        ({"SE4": "garbage"}, "invalid_response"),
        ({"SE4": ["garbage"]}, "invalid_response"),
    ]
    with patch(NOW_PATH, return_value=clock) as now:
        for index, (failure, kind) in enumerate(failures):
            outcome["next"] = failure
            now.return_value = clock + timedelta(hours=2 * index)
            await coordinator._async_update_data()
            fresh = (await async_get_config_entry_diagnostics(hass, entry))[
                "price_freshness"
            ]
            assert fresh["today_failure"] == kind
            assert fresh["today_cached"] is False
            assert fresh["last_success_utc"] is None
            assert fresh["today_retry_after_utc"] == (
                now.return_value + timedelta(hours=1)
            ).astimezone(UTC).isoformat()
            assert "secret-config-entry-id" not in json.dumps(fresh)

        outcome["next"] = lambda day: {"SE4": _hourly(day)}
        recovered_at = clock + timedelta(hours=20)
        now.return_value = recovered_at
        await coordinator._async_update_data()
        result = await async_get_config_entry_diagnostics(hass, entry)
    fresh = result["price_freshness"]
    assert fresh["today_failure"] is None
    assert fresh["today_cached"] is True
    assert fresh["today_retry_after_utc"] is None
    assert fresh["last_success_utc"] == recovered_at.isoformat()
    assert result["version"] == 2
    encoded = json.dumps(result)
    for secret in ("secret-config-entry-id", "private_heater", "private_temperature"):
        assert secret not in encoded


async def test_non_dict_response_is_invalid(hass: HomeAssistant) -> None:
    """A non-mapping response (bypassing the registry) is reported as invalid_response."""
    outcome: dict = {"next": None}
    entry, coordinator = _scripted_entry(hass, outcome)

    fake_hass = MagicMock()
    fake_hass.services.async_call = AsyncMock(return_value=[])
    with (
        patch.object(coordinator, "hass", fake_hass),
        patch(NOW_PATH, return_value=datetime(2026, 11, 14, 10, tzinfo=UTC)),
    ):
        await coordinator._async_update_data()
    fresh = (await async_get_config_entry_diagnostics(hass, entry))["price_freshness"]
    assert fresh["today_failure"] == "invalid_response"


async def test_freshness_tracks_tomorrow_separately(hass: HomeAssistant) -> None:
    """An unpublished tomorrow is reported without affecting today's status."""
    outcome: dict = {"next": None}
    entry, coordinator = _scripted_entry(hass, outcome)

    def respond(day: str) -> dict:
        return {"SE4": _hourly(day) if day == "2026-11-14" else []}

    outcome["next"] = respond
    clock = datetime(2026, 11, 14, 14, 0, tzinfo=UTC)
    with patch(NOW_PATH, return_value=clock) as now:
        await coordinator._async_update_data()
        fresh = (await async_get_config_entry_diagnostics(hass, entry))[
            "price_freshness"
        ]
        assert fresh["today_cached"] is True
        assert fresh["tomorrow_cached"] is False
        assert fresh["today_failure"] is None
        assert fresh["tomorrow_failure"] == "no_prices"
        assert fresh["tomorrow_retry_after_utc"] == (
            (clock + timedelta(minutes=30)).isoformat()
        )
        outcome["next"] = lambda day: {"SE4": _hourly(day)}
        now.return_value = clock + timedelta(minutes=31)
        await coordinator._async_update_data()
        fresh = (await async_get_config_entry_diagnostics(hass, entry))[
            "price_freshness"
        ]
    assert fresh["tomorrow_cached"] is True
    assert fresh["tomorrow_failure"] is None
    assert fresh["tomorrow_retry_after_utc"] is None
