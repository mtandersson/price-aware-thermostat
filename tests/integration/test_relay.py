"""Controlled test-harness checks for simulated relay operation.

These tests register mock switch actions and never reach physical hardware.
"""

from unittest.mock import patch

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.setup import async_setup_component
from pytest_homeassistant.common import MockConfigEntry

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
    hass.states.async_set("sensor.safety_temperature", "4.0", {"unit_of_measurement": "°C"})
    hass.states.async_set("switch.safety_heater", "off")
    assert await async_setup_component(hass, "switch", {})
    on: list[ServiceCall] = []
    off: list[ServiceCall] = []

    async def on_service(call: ServiceCall) -> None:
        on.append(call)

    async def off_service(call: ServiceCall) -> None:
        off.append(call)

    hass.services.async_register("switch", "turn_on", on_service)
    hass.services.async_register("switch", "turn_off", off_service)

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
    assert await async_setup_component(hass, "switch", {})
    on: list[ServiceCall] = []
    off: list[ServiceCall] = []

    async def on_service(call: ServiceCall) -> None:
        on.append(call)

    async def off_service(call: ServiceCall) -> None:
        off.append(call)

    hass.services.async_register("switch", "turn_on", on_service)
    hass.services.async_register("switch", "turn_off", off_service)

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


async def _switch_action(
    hass: HomeAssistant, *, shadow: bool, temperature: str, relay: str,
    mode: str | None = None,
) -> str:
    """Set up a switch wrapper and return its hvac_action."""
    entry = make_entry(hass, shadow=shadow)
    hass.states.async_set(
        "sensor.safety_temperature", temperature, {"unit_of_measurement": "°C"}
    )
    hass.states.async_set("switch.safety_heater", relay)

    async def ignore(call: ServiceCall) -> None:
        return None

    hass.services.async_register("switch", "turn_on", ignore)
    hass.services.async_register("switch", "turn_off", ignore)
    with patch(
        "custom_components.price_aware_thermostat.coordinator."
        "NordPoolPriceCoordinator._async_update_data",
        return_value=(),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        if mode is not None:
            await hass.services.async_call(
                "climate", "set_hvac_mode",
                {"entity_id": "climate.safety_test", "hvac_mode": mode}, blocking=True,
            )
            await hass.async_block_till_done()
        state = hass.states.get("climate.safety_test")
        assert state is not None
        action = str(state.attributes["hvac_action"])
        assert await hass.config_entries.async_unload(entry.entry_id)
    return action


async def test_shadow_switch_action_follows_the_plan_not_the_relay(
    hass: HomeAssistant,
) -> None:
    # Cold room, relay off: the plan heats, so the shadow action is heating.
    assert await _switch_action(hass, shadow=True, temperature="4.0", relay="off") == "heating"


async def test_shadow_switch_without_demand_is_idle_while_relay_is_on(
    hass: HomeAssistant,
) -> None:
    assert await _switch_action(hass, shadow=True, temperature="9.0", relay="on") == "idle"


async def test_shadow_switch_off_is_off_while_relay_is_on(hass: HomeAssistant) -> None:
    action = await _switch_action(
        hass, shadow=True, temperature="4.0", relay="on", mode="off"
    )
    assert action == "off"


async def test_live_switch_action_mirrors_the_relay(hass: HomeAssistant) -> None:
    # The turn_off call is ignored, so the relay is still observed on.
    assert await _switch_action(hass, shadow=False, temperature="9.0", relay="on") == "heating"


async def test_live_switch_off_with_relay_still_on_is_not_called_off(
    hass: HomeAssistant,
) -> None:
    on = await _switch_action(hass, shadow=False, temperature="4.0", relay="on", mode="off")
    assert on == "heating"


async def test_live_switch_off_with_relay_off_is_off(hass: HomeAssistant) -> None:
    off = await _switch_action(hass, shadow=False, temperature="4.0", relay="off", mode="off")
    assert off == "off"
