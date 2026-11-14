"""Regression tests for manual climate modes and state diagnostics."""

from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant.common import MockConfigEntry, async_mock_service

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


async def test_manual_temperature_and_off_modes_are_safe_in_shadow(
    hass: HomeAssistant,
) -> None:
    """Manual setpoints must not dispatch relay actions while shadowed."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Shadow modes",
        data={
            CONF_SENSOR: "sensor.shadow_temperature",
            CONF_HEATER: "switch.shadow_heater",
            CONF_NORDPOOL_ENTRY: "fake-source",
            CONF_AREA: "SE4",
            CONF_MIN_TEMP: 5.0,
            CONF_BASE_TEMP: 6.0,
            CONF_MAX_TEMP: 9.0,
        },
        options={CONF_SHADOW: True},
    )
    entry.add_to_hass(hass)
    hass.states.async_set(
        "sensor.shadow_temperature", "5.0", {"unit_of_measurement": "°C"}
    )
    hass.states.async_set("switch.shadow_heater", "off")
    on = async_mock_service(hass, "switch", "turn_on")
    off = async_mock_service(hass, "switch", "turn_off")
    with patch(
        "custom_components.price_aware_thermostat.coordinator."
        "NordPoolPriceCoordinator._async_update_data",
        return_value=(),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        entity_id = "climate.shadow_modes"
        assert hass.states.get(entity_id) is not None

        await hass.services.async_call(
            "climate", "set_preset_mode",
            {"entity_id": entity_id, "preset_mode": "fixed"},
            blocking=True,
        )
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.attributes["optimization_reason"] == "fixed"
        assert state.attributes["effective_target_c"] == 6.0

        await hass.services.async_call(
            "climate", "set_temperature",
            {"entity_id": entity_id, "temperature": 7.0},
            blocking=True,
        )
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.attributes["effective_target_c"] == 7.0
        assert entry.options[CONF_BASE_TEMP] == 7.0

        await hass.services.async_call(
            "climate", "set_hvac_mode",
            {"entity_id": entity_id, "hvac_mode": "off"},
            blocking=True,
        )
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.state == "off"
        assert state.attributes["optimization_reason"] == "off"
        assert on == []
        assert off == []
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_set_temperature_rejects_out_of_range(hass: HomeAssistant) -> None:
    """Physical bounds also apply when the setpoint is changed via service."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Bounded thermostat",
        data={
            CONF_SENSOR: "sensor.bounded_temperature",
            CONF_HEATER: "switch.bounded_heater",
            CONF_NORDPOOL_ENTRY: "fake-source",
            CONF_AREA: "SE4",
            CONF_MIN_TEMP: 5.0,
            CONF_BASE_TEMP: 6.0,
            CONF_MAX_TEMP: 8.0,
        },
        options={CONF_SHADOW: True},
    )
    entry.add_to_hass(hass)
    hass.states.async_set(
        "sensor.bounded_temperature", "6.0", {"unit_of_measurement": "°C"}
    )
    hass.states.async_set("switch.bounded_heater", "off")
    with patch(
        "custom_components.price_aware_thermostat.coordinator."
        "NordPoolPriceCoordinator._async_update_data",
        return_value=(),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        with pytest.raises(HomeAssistantError):
            await hass.services.async_call(
                "climate", "set_temperature",
                {"entity_id": "climate.bounded_thermostat", "temperature": 15.0},
                blocking=True,
            )
        assert entry.options.get(CONF_BASE_TEMP) is None
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_adjustment_diagnostics_follow_the_active_mode(hass: HomeAssistant) -> None:
    """Clamp diagnostics must agree with the reported reason and effective target."""
    from datetime import timedelta

    from homeassistant.util import dt as dt_util

    from custom_components.price_aware_thermostat.optimizer import PriceInterval

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Diagnostics modes",
        data={
            CONF_SENSOR: "sensor.diag_temperature",
            CONF_HEATER: "switch.diag_heater",
            CONF_NORDPOOL_ENTRY: "fake-source",
            CONF_AREA: "SE4",
            CONF_MIN_TEMP: 5.0,
            CONF_BASE_TEMP: 6.0,
            CONF_MAX_TEMP: 9.0,
        },
        options={CONF_SHADOW: True},
    )
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.diag_temperature", "6.0", {"unit_of_measurement": "°C"})
    hass.states.async_set("switch.diag_heater", "off")
    start = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    prices = tuple(
        PriceInterval(
            start + timedelta(hours=i), start + timedelta(hours=i + 1), 0.0 if i == 0 else 9.0
        )
        for i in range(6)
    )
    with patch(
        "custom_components.price_aware_thermostat.coordinator."
        "NordPoolPriceCoordinator._async_update_data",
        return_value=prices,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        entity_id = "climate.diagnostics_modes"

        state = hass.states.get(entity_id)
        assert state is not None
        assert state.attributes["optimization_reason"] == "preheat"
        # Relative to the base target: 6.0 + default 0.8 deviation, below the 9.0 limit.
        assert state.attributes["effective_target_c"] == 6.8
        assert state.attributes["adjustment_clamped"] is True
        assert state.attributes["requested_adjustment_c"] > 3.0

        await hass.services.async_call(
            "climate", "set_preset_mode",
            {"entity_id": entity_id, "preset_mode": "fixed"}, blocking=True,
        )
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.attributes["optimization_reason"] == "fixed"
        assert state.attributes["adjustment_clamped"] is False
        assert state.attributes["requested_adjustment_c"] == 0.0

        await hass.services.async_call(
            "climate", "set_preset_mode",
            {"entity_id": entity_id, "preset_mode": "price_aware"}, blocking=True,
        )
        await hass.services.async_call(
            "climate", "set_hvac_mode",
            {"entity_id": entity_id, "hvac_mode": "off"}, blocking=True,
        )
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.attributes["optimization_reason"] == "off"
        assert state.attributes["adjustment_clamped"] is False
        assert state.attributes["requested_adjustment_c"] == 0.0
        assert await hass.config_entries.async_unload(entry.entry_id)
