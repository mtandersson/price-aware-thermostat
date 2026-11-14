"""Prove that repeated Home Assistant events do not flood the heater relay."""

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


async def test_relay_command_is_deduplicated_until_ack(hass: HomeAssistant) -> None:
    """Keep an unacknowledged ON from being sent for every sensor update."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Relay retry guard",
        data={
            CONF_SENSOR: "sensor.retry_temperature",
            CONF_HEATER: "switch.retry_heater",
            CONF_NORDPOOL_ENTRY: "fake-nordpool",
            CONF_AREA: "SE4",
            CONF_MIN_TEMP: 5.0,
            CONF_BASE_TEMP: 6.0,
            CONF_MAX_TEMP: 8.0,
        },
        options={CONF_SHADOW: False, CONF_MIN_CYCLE: 0},
    )
    entry.add_to_hass(hass)
    hass.states.async_set(
        "sensor.retry_temperature", "4.0", {"unit_of_measurement": "°C"}
    )
    hass.states.async_set("switch.retry_heater", "off")
    assert await async_setup_component(hass, "switch", {})
    turn_on: list[ServiceCall] = []
    turn_off: list[ServiceCall] = []

    async def on_service(call: ServiceCall) -> None:
        turn_on.append(call)

    async def off_service(call: ServiceCall) -> None:
        turn_off.append(call)

    hass.services.async_register("switch", "turn_on", on_service)
    hass.services.async_register("switch", "turn_off", off_service)

    with patch(
        "custom_components.price_aware_thermostat.coordinator."
        "NordPoolPriceCoordinator._async_update_data",
        return_value=(),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert len(turn_on) == 1

        # The switch still reports OFF: no physical ACK has arrived.
        for temp in ("3.8", "3.9", "4.1"):
            hass.states.async_set(
                "sensor.retry_temperature",
                temp,
                {"unit_of_measurement": "°C"},
            )
            await hass.async_block_till_done()
        assert len(turn_on) == 1
        assert turn_off == []

        # A newer OFF decision cancels an unacknowledged ON even if the relay
        # still reports OFF. This prevents a late ON from being left pending.
        hass.states.async_set(
            "sensor.retry_temperature", "9.0", {"unit_of_measurement": "°C"}
        )
        await hass.async_block_till_done()
        assert len(turn_off) == 1

        # Once the switch reports OFF, the corrective command is acknowledged.
        hass.states.async_set(
            "sensor.retry_temperature", "9.1", {"unit_of_measurement": "°C"}
        )
        await hass.async_block_till_done()
        assert len(turn_off) == 1
        assert await hass.config_entries.async_unload(entry.entry_id)
