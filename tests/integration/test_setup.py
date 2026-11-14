"""Integration-level tests using Home Assistant's current pytest helpers."""

from datetime import timedelta
from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant.common import MockConfigEntry, async_mock_service

from custom_components.price_aware_thermostat.const import (
    CONF_AREA,
    CONF_BASE_TEMP,
    CONF_HEATER,
    CONF_MAX_TEMP,
    CONF_MIN_TEMP,
    CONF_NAME,
    CONF_NORDPOOL_ENTRY,
    CONF_SENSOR,
    CONF_SHADOW,
    DOMAIN,
)
from custom_components.price_aware_thermostat.optimizer import PriceInterval


async def test_shadow_mode_never_writes_to_switch(hass: HomeAssistant) -> None:
    """A price calculation must not turn the heater on in shadow mode."""
    now = dt_util.utcnow()
    intervals = (
        PriceInterval(now - timedelta(minutes=15), now + timedelta(minutes=15), 0.1),
        PriceInterval(now + timedelta(minutes=15), now + timedelta(minutes=30), 1.0),
        PriceInterval(now + timedelta(minutes=30), now + timedelta(minutes=45), 1.0),
        PriceInterval(now + timedelta(minutes=45), now + timedelta(minutes=60), 1.0),
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test storage",
        data={
            CONF_NAME: "Test storage",
            CONF_SENSOR: "sensor.storage_temp",
            CONF_HEATER: "switch.storage_heat",
            CONF_NORDPOOL_ENTRY: "not-a-real-entry",
            CONF_AREA: "SE4",
            CONF_MIN_TEMP: 5.0,
            CONF_BASE_TEMP: 6.0,
            CONF_MAX_TEMP: 8.0,
        },
        options={CONF_SHADOW: True},
    )
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.storage_temp", "4.0", {"unit_of_measurement": "°C"})
    hass.states.async_set("switch.storage_heat", "off")
    on = async_mock_service(hass, "switch", "turn_on")
    off = async_mock_service(hass, "switch", "turn_off")

    with patch(
        "custom_components.price_aware_thermostat.coordinator."
        "NordPoolPriceCoordinator._async_update_data",
        return_value=intervals,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        state = hass.states.get("climate.test_storage")
        assert state is not None
        assert state.attributes["shadow_mode"] is True
        assert state.attributes["planned_heating"] is True
        assert on == []
        assert off == []
        hass.states.async_set("sensor.storage_temp", "3.0", {"unit_of_measurement": "°C"})
        await hass.async_block_till_done()
        assert on == []
        assert off == []
        assert await hass.config_entries.async_unload(entry.entry_id)
