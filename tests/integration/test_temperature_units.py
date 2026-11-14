"""End-to-end safety checks for sensor units in a shadow thermostat."""

from unittest.mock import patch

from homeassistant.core import HomeAssistant
from pytest_homeassistant.common import MockConfigEntry

from custom_components.price_aware_thermostat.const import (
    CONF_AREA,
    CONF_BASE_TEMP,
    CONF_HEATER,
    CONF_MAX_TEMP,
    CONF_MIN_TEMP,
    CONF_NORDPOOL_ENTRY,
    CONF_SENSOR,
    CONF_SHADOW,
    DOMAIN,
)


async def test_fahrenheit_sensor_is_normalized_before_heating_decision(
    hass: HomeAssistant,
) -> None:
    """41 °F is 5 °C, not 41 °C; the unit must affect the thermostat decision."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Temperature units",
        data={
            CONF_SENSOR: "sensor.unit_test",
            CONF_HEATER: "switch.unit_heater",
            CONF_NORDPOOL_ENTRY: "fake-entry",
            CONF_AREA: "SE4",
            CONF_MIN_TEMP: 3.0,
            CONF_BASE_TEMP: 6.0,
            CONF_MAX_TEMP: 8.0,
        },
        options={CONF_SHADOW: True},
    )
    entry.add_to_hass(hass)
    hass.states.async_set(
        "sensor.unit_test", "41.0", {"unit_of_measurement": "°F"}
    )
    hass.states.async_set("switch.unit_heater", "off")
    with patch(
        "custom_components.price_aware_thermostat.coordinator."
        "NordPoolPriceCoordinator._async_update_data",
        return_value=(),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        state = hass.states.get("climate.temperature_units")
        assert state is not None
        assert state.attributes["current_temperature"] == 5.0
        assert state.attributes["planned_heating"] is True
        assert state.attributes["shadow_mode"] is True

        hass.states.async_set(
            "sensor.unit_test", "41.0", {"unit_of_measurement": "unexpected"}
        )
        await hass.async_block_till_done()
        invalid = hass.states.get("climate.temperature_units")
        assert invalid is not None
        assert invalid.attributes.get("current_temperature") is None
        assert invalid.attributes["planned_heating"] is None
        assert invalid.attributes["temperature_sample_fresh"] is False
        assert await hass.config_entries.async_unload(entry.entry_id)
