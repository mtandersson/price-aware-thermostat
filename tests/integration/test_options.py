"""Test guarded options updates using the real Home Assistant flow manager."""

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant.common import MockConfigEntry

from custom_components.price_aware_thermostat.const import (
    CONF_BASE_TEMP,
    CONF_COLD_TOLERANCE,
    CONF_DEADBAND,
    CONF_FILTER,
    CONF_HOT_TOLERANCE,
    CONF_LOOKAHEAD,
    CONF_MAX_SENSOR_AGE,
    CONF_MAX_TEMP,
    CONF_MIN_CYCLE,
    CONF_MIN_TEMP,
    CONF_SAFETY_ACK,
    CONF_SENSITIVITY,
    CONF_SHADOW,
    DOMAIN,
)


def _settings(*, shadow: bool, acknowledged: bool) -> dict[str, object]:
    """Return complete, valid options input for the UI."""
    return {
        CONF_BASE_TEMP: 6.0,
        CONF_MIN_TEMP: 5.0,
        CONF_MAX_TEMP: 8.0,
        CONF_LOOKAHEAD: 180,
        CONF_FILTER: "triangle",
        CONF_DEADBAND: 0.2,
        CONF_SENSITIVITY: 2.0,
        CONF_COLD_TOLERANCE: 0.3,
        CONF_HOT_TOLERANCE: 0.3,
        CONF_MIN_CYCLE: 300,
        CONF_MAX_SENSOR_AGE: 60,
        CONF_SHADOW: shadow,
        CONF_SAFETY_ACK: acknowledged,
    }


async def test_options_rejects_live_actuation_without_ack(hass: HomeAssistant) -> None:
    """An absent safety confirmation must not enable physical control."""
    entry = MockConfigEntry(domain=DOMAIN, title="Test thermostat", data={})
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.FORM
    rejected = await hass.config_entries.options.async_configure(
        result["flow_id"], _settings(shadow=False, acknowledged=False)
    )
    assert rejected["type"] == FlowResultType.FORM
    assert rejected["errors"]["base"] == "safety_confirmation_required"
    assert entry.options.get(CONF_SHADOW, True) is True


async def test_options_accepts_safe_shadow_mode(hass: HomeAssistant) -> None:
    """A user may adjust settings without authorizing a switch write."""
    entry = MockConfigEntry(domain=DOMAIN, title="Test thermostat", data={})
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    accepted = await hass.config_entries.options.async_configure(
        result["flow_id"], _settings(shadow=True, acknowledged=False)
    )
    assert accepted["type"] == FlowResultType.CREATE_ENTRY
    assert accepted["data"][CONF_SHADOW] is True
    assert CONF_SAFETY_ACK not in accepted["data"]


async def test_options_rejects_invalid_minimum(hass: HomeAssistant) -> None:
    """Unsafe limits must be rejected even with a safety confirmation."""
    entry = MockConfigEntry(domain=DOMAIN, title="Test thermostat", data={})
    entry.add_to_hass(hass)
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    settings = _settings(shadow=True, acknowledged=True)
    settings[CONF_MIN_TEMP] = 9.0
    rejected = await hass.config_entries.options.async_configure(
        flow["flow_id"], settings
    )
    assert rejected["type"] == FlowResultType.FORM
    assert rejected["errors"]["base"] == "invalid_temperature_range"


async def test_options_accepts_live_mode_only_with_confirmation(
    hass: HomeAssistant,
) -> None:
    """Explicit safety confirmation is required to leave shadow mode."""
    entry = MockConfigEntry(domain=DOMAIN, title="Test thermostat", data={})
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    accepted = await hass.config_entries.options.async_configure(
        result["flow_id"], _settings(shadow=False, acknowledged=True)
    )
    assert accepted["type"] == FlowResultType.CREATE_ENTRY
    assert accepted["data"][CONF_SHADOW] is False
    assert CONF_SAFETY_ACK not in accepted["data"]
