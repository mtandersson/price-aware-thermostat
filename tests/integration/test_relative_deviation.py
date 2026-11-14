"""Price adjustment is relative to the user's target; min/max are safety limits."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.util import dt as dt_util
from pytest_homeassistant.common import MockConfigEntry

from custom_components.price_aware_thermostat.const import (
    CONF_AREA,
    CONF_BASE_TEMP,
    CONF_HEATER,
    CONF_MAX_DEVIATION,
    CONF_MAX_TEMP,
    CONF_MIN_TEMP,
    CONF_NORDPOOL_ENTRY,
    CONF_SENSOR,
    CONF_SHADOW,
    DEFAULTS,
    DOMAIN,
)
from custom_components.price_aware_thermostat.optimizer import PriceInterval

ENTITY = "climate.relative"
SOURCE = {CONF_NORDPOOL_ENTRY: "fake-source", CONF_AREA: "SE4"}


def _prices(first: float, rest: float) -> tuple[PriceInterval, ...]:
    start = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    return tuple(
        PriceInterval(
            start + timedelta(hours=i), start + timedelta(hours=i + 1), first if i == 0 else rest
        )
        for i in range(6)
    )


async def _setup(
    hass: HomeAssistant, prices: tuple[PriceInterval, ...], **settings: object
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, version=2, minor_version=2, title="Relative",
        data={
            CONF_SENSOR: "sensor.rel_temperature", CONF_HEATER: "switch.rel_heater",
            CONF_NORDPOOL_ENTRY: "fake-source", CONF_AREA: "SE4",
            CONF_MIN_TEMP: 5.0, CONF_BASE_TEMP: 6.0, CONF_MAX_TEMP: 25.0,
            CONF_MAX_DEVIATION: 0.8, **settings,
        },
        options={CONF_SHADOW: True},
    )
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.rel_temperature", "20.0", {"unit_of_measurement": "°C"})
    hass.states.async_set("switch.rel_heater", "off")
    with patch(
        "custom_components.price_aware_thermostat.coordinator."
        "NordPoolPriceCoordinator._async_update_data",
        return_value=prices,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def _set_target(hass: HomeAssistant, value: float) -> float:
    await hass.services.async_call(
        "climate", "set_temperature", {"entity_id": ENTITY, "temperature": value},
        blocking=True,
    )
    state = hass.states.get(ENTITY)
    assert state is not None
    return float(state.attributes["effective_target_c"])


@pytest.mark.parametrize(("first", "rest", "offset"), [(0.0, 9.0, 0.8), (9.0, 0.0, -0.8)])
async def test_ui_target_is_followed_with_bounded_price_offset(
    hass: HomeAssistant, first: float, rest: float, offset: float
) -> None:
    entry = await _setup(hass, _prices(first, rest))
    for target in (20.0, 22.5, 7.0):
        assert await _set_target(hass, target) == pytest.approx(target + offset)
    assert entry.options[CONF_BASE_TEMP] == 7.0
    assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize(
    ("first", "rest", "limit", "expected"),
    [(0.0, 9.0, {CONF_MAX_TEMP: 20.5}, 20.5), (9.0, 0.0, {CONF_MIN_TEMP: 19.7}, 19.7)],
)
async def test_absolute_limits_clamp_the_relative_offset(
    hass: HomeAssistant, first: float, rest: float, limit: dict[str, float], expected: float
) -> None:
    entry = await _setup(hass, _prices(first, rest), **limit)
    assert await _set_target(hass, 20.0) == expected
    state = hass.states.get(ENTITY)
    assert state is not None
    assert state.attributes["adjustment_clamped"] is True
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_zero_deviation_disables_price_adjustment(hass: HomeAssistant) -> None:
    entry = await _setup(hass, _prices(0.0, 9.0), **{CONF_MAX_DEVIATION: 0.0})
    assert await _set_target(hass, 21.0) == 21.0
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_missing_deviation_at_runtime_uses_default(hass: HomeAssistant) -> None:
    entry = await _setup(hass, _prices(0.0, 9.0), **{CONF_MAX_DEVIATION: None})
    assert await _set_target(hass, 21.0) == pytest.approx(21.0 + 0.8)
    assert await hass.config_entries.async_unload(entry.entry_id)


def _options(**extra: object) -> dict[str, object]:
    values = {
        key: DEFAULTS[key] for key in (
            CONF_BASE_TEMP, CONF_MIN_TEMP, CONF_MAX_TEMP, CONF_MAX_DEVIATION,
            "lookahead_minutes", "price_filter", "deadband_sek_per_kwh",
            "sensitivity_c_per_sek_per_kwh", "cold_tolerance", "hot_tolerance",
            "minimum_cycle_seconds", "max_sensor_age_minutes",
        ) if key in DEFAULTS
    }
    return {**values, CONF_SHADOW: True, **extra}


@pytest.mark.parametrize("deviation", [None, -0.1, 20.1, "x"])
async def test_options_reject_invalid_or_empty_deviation(
    hass: HomeAssistant, deviation: object
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, version=2, minor_version=2,
        data={**SOURCE, CONF_MIN_TEMP: 5.0, CONF_BASE_TEMP: 6.0, CONF_MAX_TEMP: 25.0},
        options={CONF_SHADOW: True},
    )
    entry.add_to_hass(hass)
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    try:
        result = await hass.config_entries.options.async_configure(
            flow["flow_id"], _options(**{CONF_MAX_DEVIATION: deviation})
        )
    except Exception:  # noqa: BLE001 - schema rejection is equally acceptable
        return
    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "invalid_temperature_range"


async def test_options_prefill_default_deviation_for_legacy_values(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, version=2, minor_version=2,
        data={**SOURCE, CONF_MIN_TEMP: 5.0, CONF_BASE_TEMP: 6.0, CONF_MAX_TEMP: 25.0,
              CONF_MAX_DEVIATION: None},
        options={CONF_SHADOW: True},
    )
    entry.add_to_hass(hass)
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    defaults = {str(key): key.default() for key in flow["data_schema"].schema
                if str(key) == CONF_MAX_DEVIATION}
    assert defaults == {CONF_MAX_DEVIATION: 0.8}
    result = await hass.config_entries.options.async_configure(
        flow["flow_id"], _options(**{CONF_MAX_DEVIATION: 1.5})
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_MAX_DEVIATION] == 1.5
