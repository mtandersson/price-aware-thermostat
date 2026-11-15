"""Recoverable configuration errors and safety boundaries through HA's flow manager."""

from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant.common import MockConfigEntry

from custom_components.price_aware_thermostat.config_flow import _invalid_settings
from custom_components.price_aware_thermostat.const import (
    CONF_AREA,
    CONF_BASE_TEMP,
    CONF_CONTROL_TYPE,
    CONF_HEATER,
    CONF_MAX_TEMP,
    CONF_MIN_TEMP,
    CONF_NAME,
    CONF_NORDPOOL_ENTRY,
    CONF_SENSOR,
    CONF_SHADOW,
    CONTROL_SWITCH,
    DEFAULTS,
    DOMAIN,
)


@pytest.fixture
def settings(hass: HomeAssistant) -> dict:
    source = MockConfigEntry(domain="nordpool", data={"areas": ["SE4"]})
    source.add_to_hass(hass)
    hass.states.async_set("sensor.quality_room", "6", {"unit_of_measurement": "°C"})
    hass.states.async_set("switch.quality_heater", "off")
    defaults = {key: value for key, value in DEFAULTS.items() if key not in {
        CONF_SHADOW, "heat_compensation", "cool_compensation",
        "window_open_delay_seconds", "window_close_delay_seconds",
        "outdoor_lockout_hysteresis", "heat_cool_min_gap", "heat_cool_changeover_minutes",
    }}
    return {
        **defaults, CONF_CONTROL_TYPE: CONTROL_SWITCH, CONF_NAME: "Quality room",
        CONF_SENSOR: "sensor.quality_room", CONF_HEATER: "switch.quality_heater",
        CONF_NORDPOOL_ENTRY: source.entry_id, CONF_AREA: "SE4",
    }


@pytest.mark.parametrize("bad_source", ["missing", "wrong_domain"])
async def test_user_source_errors_can_be_corrected(
    hass: HomeAssistant, settings: dict, bad_source: str,
) -> None:
    other = MockConfigEntry(domain="other", data={"areas": ["SE4"]})
    other.add_to_hass(hass)
    flow = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    rejected = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {**settings, CONF_NORDPOOL_ENTRY:
                         other.entry_id if bad_source == "wrong_domain" else "missing"},
    )
    assert rejected["errors"] == {"base": "invalid_price_source"}
    with patch(f"custom_components.{DOMAIN}.async_setup_entry", return_value=True):
        accepted = await hass.config_entries.flow.async_configure(flow["flow_id"], settings)
        assert accepted["type"] is FlowResultType.CREATE_ENTRY
        assert accepted["options"][CONF_SHADOW] is True


async def test_backend_error_can_be_corrected(hass: HomeAssistant, settings: dict) -> None:
    flow = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {**settings, CONF_SENSOR: "sensor.missing"}
    )
    assert result["errors"] == {"base": "invalid_room_sensor"}
    with patch(f"custom_components.{DOMAIN}.async_setup_entry", return_value=True):
        accepted = await hass.config_entries.flow.async_configure(flow["flow_id"], settings)
        assert accepted["type"] is FlowResultType.CREATE_ENTRY


async def test_options_backend_error_retains_configuration(
    hass: HomeAssistant, settings: dict,
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, version=2, data=settings,
                            options={CONF_SHADOW: True})
    entry.add_to_hass(hass)
    options = {key: value for key, value in settings.items() if key not in {
        CONF_NAME, CONF_NORDPOOL_ENTRY, CONF_AREA, CONF_CONTROL_TYPE,
    }}
    before = dict(entry.options)
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    rejected = await hass.config_entries.options.async_configure(
        flow["flow_id"], {**options, CONF_SENSOR: "sensor.missing", CONF_SHADOW: True}
    )
    assert rejected["errors"] == {"base": "invalid_room_sensor"}
    assert dict(entry.options) == before
    accepted = await hass.config_entries.options.async_configure(
        flow["flow_id"], {**options, CONF_SHADOW: True}
    )
    assert accepted["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.parametrize("invalid", [{}, {CONF_MIN_TEMP: None}, {
    CONF_MIN_TEMP: "bad", CONF_BASE_TEMP: 6, CONF_MAX_TEMP: 8,
}])
def test_incomplete_temperature_settings_are_rejected(invalid: dict) -> None:
    """Defensive validation rejects corrupt/incomplete input before storage."""
    assert _invalid_settings(invalid)


async def test_ac_options_retain_explicit_optional_values(hass: HomeAssistant) -> None:
    """Saving explicit bounds/contacts must not silently clear either field."""
    from tests.integration.test_backends import entities, settings

    entities(hass)
    data = settings()
    entry = MockConfigEntry(domain=DOMAIN, version=2, data=data,
                            options={CONF_SHADOW: True})
    entry.add_to_hass(hass)
    hass.states.async_set("binary_sensor.window", "off")
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    values = {key: value for key, value in data.items() if key not in {
        CONF_NAME, CONF_NORDPOOL_ENTRY, CONF_AREA, CONF_CONTROL_TYPE,
    }}
    result = await hass.config_entries.options.async_configure(flow["flow_id"], {
        **values, CONF_SHADOW: True, "maximum_price_deviation": 1.5,
        "window_contacts": ["binary_sensor.window"],
    })
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["maximum_price_deviation"] == 1.5
    assert entry.options["window_contacts"] == ["binary_sensor.window"]
