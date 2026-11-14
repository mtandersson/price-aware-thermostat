"""Prevent event storms from creating unbounded climate evaluation tasks."""

from unittest.mock import patch

from homeassistant.core import HomeAssistant
from pytest_homeassistant.common import MockConfigEntry

from custom_components.price_aware_thermostat.climate import PriceAwareThermostat
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


async def test_sensor_event_burst_coalesces_into_single_worker(
    hass: HomeAssistant,
) -> None:
    """A rapid burst must not allocate one evaluation task per event."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Coalesced thermostat",
        data={
            CONF_SENSOR: "sensor.coalesced_temperature",
            CONF_HEATER: "switch.coalesced_heater",
            CONF_NORDPOOL_ENTRY: "test-price-source",
            CONF_AREA: "SE4",
            CONF_MIN_TEMP: 5.0,
            CONF_BASE_TEMP: 6.0,
            CONF_MAX_TEMP: 8.0,
        },
        options={CONF_SHADOW: True},
    )
    entry.add_to_hass(hass)
    hass.states.async_set(
        "sensor.coalesced_temperature", "5.0",
        {"unit_of_measurement": "°C"},
    )
    hass.states.async_set("switch.coalesced_heater", "off")

    with (
        patch(
            "custom_components.price_aware_thermostat.coordinator."
            "NordPoolPriceCoordinator._async_update_data",
            return_value=(),
        ),
        patch.object(PriceAwareThermostat, "_async_evaluate", autospec=True) as evaluate,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        evaluate.reset_mock()

        for index in range(100):
            hass.states.async_set(
                "sensor.coalesced_temperature",
                str(4.0 + index * 0.01),
                {"unit_of_measurement": "°C"},
            )
        await hass.async_block_till_done()
        assert 1 <= evaluate.await_count <= 2

        entity = evaluate.await_args_list[-1].args[0]
        assert entity._evaluation_requested is False
        assert entity._evaluation_task is not None
        assert entity._evaluation_task.done()

        assert await hass.config_entries.async_unload(entry.entry_id)
        assert entity._unloading is True
        calls_before = evaluate.await_count
        entity._queue_evaluation()
        await hass.async_block_till_done()
        assert evaluate.await_count == calls_before
