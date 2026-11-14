"""Configuration validation tests against actual Home Assistant."""

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant.common import MockConfigEntry

from custom_components.price_aware_thermostat.const import (
    CONF_AREA,
    CONF_BASE_TEMP,
    CONF_HEATER,
    CONF_MAX_TEMP,
    CONF_MIN_TEMP,
    CONF_NAME,
    CONF_NORDPOOL_ENTRY,
    CONF_SENSOR,
    DOMAIN,
)


async def test_config_flow_rejects_inverted_temperature_limits(hass: HomeAssistant) -> None:
    """Unsafe minimum, base and maximum order cannot create a controller."""
    source = MockConfigEntry(domain="nordpool", title="Spot prices", data={"areas": ["SE4"]})
    source.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM

    user_input = {
        CONF_NAME: "Test heater",
        CONF_SENSOR: "sensor.temperature",
        CONF_HEATER: "switch.heater",
        CONF_NORDPOOL_ENTRY: source.entry_id,
        CONF_AREA: "SE4",
        CONF_MIN_TEMP: 9.0,
        CONF_BASE_TEMP: 6.0,
        CONF_MAX_TEMP: 8.0,
        "lookahead_minutes": 180,
        "filter_type": "triangle",
        "deadband_sek_per_kwh": 0.2,
        "sensitivity_c_per_sek_per_kwh": 2.0,
        "cold_tolerance": 0.3,
        "hot_tolerance": 0.3,
        "min_cycle_seconds": 300,
    }
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "invalid_temperature_range"


async def test_config_flow_creates_shadow_entry(hass: HomeAssistant) -> None:
    """Valid settings produce an entry with actuation disabled by default."""
    source = MockConfigEntry(domain="nordpool", title="Spot prices", data={"areas": ["SE4"]})
    source.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    settings = {
        CONF_NAME: "Test heater",
        CONF_SENSOR: "sensor.temperature",
        CONF_HEATER: "switch.heater",
        CONF_NORDPOOL_ENTRY: source.entry_id,
        CONF_AREA: "SE4",
        CONF_MIN_TEMP: 5.0,
        CONF_BASE_TEMP: 6.0,
        CONF_MAX_TEMP: 8.0,
        "lookahead_minutes": 180,
        "filter_type": "triangle",
        "deadband_sek_per_kwh": 0.2,
        "sensitivity_c_per_sek_per_kwh": 2.0,
        "cold_tolerance": 0.3,
        "hot_tolerance": 0.3,
        "min_cycle_seconds": 300,
    }
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], settings
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_AREA] == "SE4"
    assert result["options"]["shadow_mode"] is True


async def test_config_flow_rejects_unconfigured_nord_pool_area(
    hass: HomeAssistant,
) -> None:
    """Only market areas present in the chosen Nord Pool entry are accepted."""
    source = MockConfigEntry(
        domain="nordpool", title="Spot prices", data={"areas": ["SE4"]}
    )
    source.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    settings = {
        CONF_NAME: "Test heater",
        CONF_SENSOR: "sensor.temperature",
        CONF_HEATER: "switch.heater",
        CONF_NORDPOOL_ENTRY: source.entry_id,
        CONF_AREA: "SE3",
        CONF_MIN_TEMP: 5.0,
        CONF_BASE_TEMP: 6.0,
        CONF_MAX_TEMP: 8.0,
        "lookahead_minutes": 180,
        "filter_type": "triangle",
        "deadband_sek_per_kwh": 0.2,
        "sensitivity_c_per_sek_per_kwh": 2.0,
        "cold_tolerance": 0.3,
        "hot_tolerance": 0.3,
        "min_cycle_seconds": 300,
    }
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], settings
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "invalid_market_area"
