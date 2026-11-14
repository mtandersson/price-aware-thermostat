"""Lifecycle and relay-service failure tests using isolated Home Assistant entities."""

from unittest.mock import patch

from homeassistant.components.climate import HVACMode
from homeassistant.core import HomeAssistant, ServiceCall, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.setup import async_setup_component
from pytest_homeassistant.common import MockConfigEntry, async_mock_service, mock_restore_cache

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


def _entry(hass: HomeAssistant, *, title: str, shadow: bool = True) -> MockConfigEntry:
    """Create an isolated instance with no physical switch."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=title,
        data={
            CONF_SENSOR: "sensor.lifecycle_temperature",
            CONF_HEATER: "switch.lifecycle_heater",
            CONF_NORDPOOL_ENTRY: "mock-nordpool-source",
            CONF_AREA: "SE4",
            CONF_MIN_TEMP: 5.0,
            CONF_BASE_TEMP: 6.0,
            CONF_MAX_TEMP: 8.0,
        },
        options={CONF_SHADOW: shadow, CONF_MIN_CYCLE: 0},
    )
    entry.add_to_hass(hass)
    hass.states.async_set(
        "sensor.lifecycle_temperature", "6.0", {"unit_of_measurement": "°C"}
    )
    hass.states.async_set("switch.lifecycle_heater", "off")
    return entry


async def test_setpoint_survives_config_entry_reload(hass: HomeAssistant) -> None:
    """A manual setpoint is persisted into entry options and survives reload."""
    entry = _entry(hass, title="Reloadable heater")
    on = async_mock_service(hass, "switch", "turn_on")
    off = async_mock_service(hass, "switch", "turn_off")
    with patch(
        "custom_components.price_aware_thermostat.coordinator."
        "NordPoolPriceCoordinator._async_update_data",
        return_value=(),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await hass.services.async_call(
            "climate",
            "set_temperature",
            {"entity_id": "climate.reloadable_heater", "temperature": 7.0},
            blocking=True,
        )
        assert entry.options[CONF_BASE_TEMP] == 7.0
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        state = hass.states.get("climate.reloadable_heater")
        assert state is not None
        assert state.attributes["temperature"] == 7.0
        assert state.attributes["shadow_mode"] is True
        assert on == []
        assert off == []
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_restores_explicit_off_and_fixed_modes_in_shadow(
    hass: HomeAssistant,
) -> None:
    """A restart restores the previous user mode without actuating a relay."""
    mock_restore_cache(
        hass,
        (
            State(
                "climate.restored_heater",
                HVACMode.OFF,
                {"preset_mode": "fixed", "temperature": 7.0},
            ),
        ),
    )
    entry = _entry(hass, title="Restored heater")
    on = async_mock_service(hass, "switch", "turn_on")
    off = async_mock_service(hass, "switch", "turn_off")
    with patch(
        "custom_components.price_aware_thermostat.coordinator."
        "NordPoolPriceCoordinator._async_update_data",
        return_value=(),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        state = hass.states.get("climate.restored_heater")
        assert state is not None
        assert state.state == HVACMode.OFF
        assert state.attributes["preset_mode"] == "fixed"
        assert state.attributes["optimization_reason"] == "off"
        assert state.attributes["shadow_mode"] is True
        assert on == []
        assert off == []
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_failed_relay_call_is_throttled_and_does_not_abort_setup(
    hass: HomeAssistant,
) -> None:
    """A rejected service action cannot produce a tight loop of heater calls."""
    entry = _entry(hass, title="Failing heater", shadow=False)
    hass.states.async_set(
        "sensor.lifecycle_temperature", "4.0", {"unit_of_measurement": "°C"}
    )
    assert await async_setup_component(hass, "switch", {})
    calls: list[ServiceCall] = []

    async def fail_turn_on(call: ServiceCall) -> None:
        calls.append(call)
        raise HomeAssistantError("Simulated actuator unavailable")

    hass.services.async_register("switch", "turn_on", fail_turn_on)
    with patch(
        "custom_components.price_aware_thermostat.coordinator."
        "NordPoolPriceCoordinator._async_update_data",
        return_value=(),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert len(calls) == 1

        for temp in ("3.9", "3.8", "3.7"):
            hass.states.async_set(
                "sensor.lifecycle_temperature", temp, {"unit_of_measurement": "°C"}
            )
            await hass.async_block_till_done()
        assert len(calls) == 1
        assert await hass.config_entries.async_unload(entry.entry_id)
