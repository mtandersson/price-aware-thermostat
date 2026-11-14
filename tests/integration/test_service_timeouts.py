"""A stalled external action must not hold the thermostat worker indefinitely."""

import asyncio
from datetime import datetime
from unittest.mock import patch

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from pytest_homeassistant.common import MockConfigEntry, async_mock_service

from custom_components.price_aware_thermostat.ac_control import Command
from custom_components.price_aware_thermostat.const import (
    CONF_AREA,
    CONF_HEATER,
    CONF_MIN_CYCLE,
    CONF_NAME,
    CONF_NORDPOOL_ENTRY,
    CONF_SENSOR,
    CONF_SHADOW,
    DEFAULTS,
    DOMAIN,
)
from custom_components.price_aware_thermostat.coordinator import NordPoolPriceCoordinator
from tests.integration.ac_support import WRAPPER, ac_env


async def test_stalled_price_service_is_cancelled_and_retried_conservatively(
    hass: HomeAssistant,
) -> None:
    source = MockConfigEntry(domain="nordpool", data={"areas": ["SE4"]})
    source.add_to_hass(hass)
    entry = MockConfigEntry(domain=DOMAIN,
                            data={CONF_NORDPOOL_ENTRY: source.entry_id, CONF_AREA: "SE4"})
    entry.add_to_hass(hass)
    cancelled = asyncio.Event()
    attempts = 0

    async def stalled(call: ServiceCall) -> dict:
        nonlocal attempts
        attempts += 1
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    hass.services.async_register("nordpool", "get_prices_for_date", stalled,
                                 supports_response=SupportsResponse.ONLY)
    coordinator = NordPoolPriceCoordinator(hass, entry)
    with (
        patch(f"custom_components.{DOMAIN}.coordinator.PRICE_FETCH_TIMEOUT_SECONDS", 0.01),
        patch(f"custom_components.{DOMAIN}.coordinator.dt_util.now",
              return_value=datetime.fromisoformat("2026-11-14T10:00:00+01:00")),
    ):
        assert await coordinator._async_update_data() == ()
        assert cancelled.is_set()
        assert coordinator.today_failure == "service_error"
        assert await coordinator._async_update_data() == ()
        assert attempts == 1


async def test_stalled_switch_does_not_block_corrective_off(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, version=2, title="Timeout room", data={
        **DEFAULTS, CONF_NAME: "Timeout room", CONF_NORDPOOL_ENTRY: "missing",
        CONF_AREA: "SE4", CONF_SENSOR: "sensor.timeout_room", CONF_HEATER: "switch.timeout_heat",
    }, options={CONF_SHADOW: False, CONF_MIN_CYCLE: 0})
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.timeout_room", "4", {"unit_of_measurement": "°C"})
    hass.states.async_set("switch.timeout_heat", "off")
    cancelled = asyncio.Event()
    attempts = 0

    async def stalled(call: ServiceCall) -> None:
        nonlocal attempts
        attempts += 1
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    # Ensure the platform has registered its actions before overriding the fake handler.
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "switch", {})
    hass.services.async_register("switch", "turn_on", stalled)
    off = async_mock_service(hass, "switch", "turn_off")
    with patch(f"custom_components.{DOMAIN}.climate.ACTUATOR_TIMEOUT_SECONDS", 0.01):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert cancelled.is_set()
        hass.states.async_set("sensor.timeout_room", "3.9", {"unit_of_measurement": "°C"})
        await hass.async_block_till_done()
        assert attempts == 1
        await hass.services.async_call("climate", "set_hvac_mode", {
            "entity_id": "climate.timeout_room", "hvac_mode": "off",
        }, blocking=True)
        await hass.async_block_till_done()
        assert len(off) == 1
        assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize("command", [Command("set_mode", "heat"),
                                    Command("set_temperature", "heat", 24.0)])
async def test_stalled_climate_command_is_cancelled(
    hass: HomeAssistant, command: Command,
) -> None:
    async with ac_env(hass, shadow=True) as env:
        cancelled = asyncio.Event()

        async def stalled(*args, **kwargs) -> None:
            try:
                await asyncio.Future()
            finally:
                cancelled.set()

        method = "async_set_hvac_mode" if command.kind == "set_mode" else "async_set_temperature"
        wrapper = hass.data["climate"].get_entity(WRAPPER)
        with (
            patch.object(env.pump, method, side_effect=stalled),
            patch(f"custom_components.{DOMAIN}.climate.ACTUATOR_TIMEOUT_SECONDS", 0.01),
        ):
            # Exercise the backend dispatch boundary, bypassing shadow's no-call guard.
            await wrapper._async_dispatch(command, env.clock.now)
        assert cancelled.is_set()
        # The evaluation lock/worker remains usable after the failed backend call.
        await env.set_mode(HVACMode.OFF)
