"""Controlled test-harness checks for simulated relay operation.

These tests register mock switch actions and never reach physical hardware.
"""

from unittest.mock import patch

from homeassistant.core import HomeAssistant
from pytest_homeassistant.common import MockConfigEntry, async_mock_service

from custom_components.price_aware_thermostat.const import (
    CONF_AREA,
    CONF_BASE_TEMP,
    CONF_HEATER,
    CONF_MAX_TEMP,
    CONF_MIN_CYCLE,
    CONF_MIN_TEMP,
    CONF_NORDPOOL_ENTRY,
    CONF_SENSOR,
    CONF_SHADOW,
    DOMAIN,
)


def make_entry(hass: HomeAssistant, *, shadow: bool) -> MockConfigEntry:
    """Create a test thermostat with an explicit test-mode configuration."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Safety test",
        data={
            CONF_SENSOR: "sensor.safety_temperature",
            CONF_HEATER: "switch.safety_heater",
            CONF_NORDPOOL_ENTRY: "fake-nordpool",
            CONF_AREA: "SE4",
            CONF_MIN_TEMP: 5.0,
            CONF_BASE_TEMP: 6.0,
            CONF_MAX_TEMP: 8.0,
        },
        options={CONF_SHADOW: shadow, CONF_MIN_CYCLE: 0},
    )
    entry.add_to_hass(hass)
    return entry


async def test_live_control_turns_on_only_for_valid_cold_sensor(hass: HomeAssistant) -> None:
    """Cold valid sensor may switch on in the mock HA test environment."""
    entry = make_entry(hass, shadow=False)
    hass.states.async_set("sensor.safety_temperature", "4.0")
    hass.states.async_set("switch.safety_heater", "off")
    on = async_mock_service(hass, "switch", "turn_on")
    off = async_mock_service(hass, "switch", "turn_off")

    with patch(
        "custom_components.price_aware_thermostat.coordinator."
        "NordPoolPriceCoordinator._async_update_data",
        return_value=(),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert len(on) >= 1
        assert not off
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_invalid_temperature_forces_off_when_live(hass: HomeAssistant) -> None:
    """A missing temperature sample forces off regardless of minimum cycle time."""
    entry = make_entry(hass, shadow=False)
    hass.states.async_set("sensor.safety_temperature", "unavailable")
    hass.states.async_set("switch.safety_heater", "on")
    on = async_mock_service(hass, "switch", "turn_on")
    off = async_mock_service(hass, "switch", "turn_off")

    with patch(
        "custom_components.price_aware_thermostat.coordinator."
        "NordPoolPriceCoordinator._async_update_data",
        return_value=(),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert not on
        assert len(off) >= 1
        assert await hass.config_entries.async_unload(entry.entry_id)
