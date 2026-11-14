"""Price source in the options flow and the price sensor on the thermostat device."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.price_aware_thermostat.const import (
    CONF_AREA,
    CONF_BASE_TEMP,
    CONF_COLD_TOLERANCE,
    CONF_CONTROL_TYPE,
    CONF_DEADBAND,
    CONF_FILTER,
    CONF_HEATER,
    CONF_HOT_TOLERANCE,
    CONF_LOOKAHEAD,
    CONF_MAX_DEVIATION,
    CONF_MAX_SENSOR_AGE,
    CONF_MAX_TEMP,
    CONF_MIN_CYCLE,
    CONF_MIN_TEMP,
    CONF_NAME,
    CONF_NORDPOOL_ENTRY,
    CONF_SENSITIVITY,
    CONF_SENSOR,
    CONF_SHADOW,
    CONTROL_SWITCH,
    DEFAULTS,
    DOMAIN,
)
from custom_components.price_aware_thermostat.optimizer import PriceInterval

UPDATE = (
    "custom_components.price_aware_thermostat.coordinator."
    "NordPoolPriceCoordinator._async_update_data"
)
SENSOR = "sensor.house_electricity_price"


@pytest.fixture(autouse=True)
def entities(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.room", "21", {"unit_of_measurement": "°C"})
    hass.states.async_set("switch.heater", "off")


@pytest.fixture
def services(hass: HomeAssistant) -> list[list]:
    return [async_mock_service(hass, "switch", s) for s in ("turn_on", "turn_off")]


def _sources(hass: HomeAssistant) -> tuple[MockConfigEntry, MockConfigEntry]:
    old = MockConfigEntry(domain="nordpool", title="Old prices", data={"areas": ["SE4"]})
    new = MockConfigEntry(domain="nordpool", title="New prices", data={"areas": ["SE3"]})
    old.add_to_hass(hass)
    new.add_to_hass(hass)
    return old, new


def _entry(hass: HomeAssistant, source_id: str, *, shadow: bool = True) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, version=2, minor_version=2, title="House",
        unique_id="switch.heater",
        data={
            **DEFAULTS, CONF_NAME: "House", CONF_CONTROL_TYPE: CONTROL_SWITCH,
            CONF_SENSOR: "sensor.room", CONF_HEATER: "switch.heater",
            CONF_NORDPOOL_ENTRY: source_id, CONF_AREA: "SE4",
            CONF_BASE_TEMP: 21.0,
        },
        options={CONF_SHADOW: shadow},
    )
    entry.add_to_hass(hass)
    return entry


def _form(source_id: str, area: str, *, shadow: bool) -> dict[str, object]:
    return {
        CONF_NORDPOOL_ENTRY: source_id, CONF_AREA: area,
        CONF_BASE_TEMP: 21.0, CONF_MIN_TEMP: 5.0, CONF_MAX_TEMP: 25.0,
        CONF_MAX_DEVIATION: 0.8, CONF_LOOKAHEAD: 180, CONF_FILTER: "triangle",
        CONF_DEADBAND: 0.2, CONF_SENSITIVITY: 2.0, CONF_COLD_TOLERANCE: 0.3,
        CONF_HOT_TOLERANCE: 0.3, CONF_MIN_CYCLE: 300, CONF_MAX_SENSOR_AGE: 60,
        CONF_SENSOR: "sensor.room", CONF_HEATER: "switch.heater",
        CONF_SHADOW: shadow,
    }


def _prices(*values: float) -> tuple[PriceInterval, ...]:
    start = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    return tuple(
        PriceInterval(start + timedelta(hours=i), start + timedelta(hours=i + 1), v)
        for i, v in enumerate(values)
    )


async def test_options_show_and_change_price_source_while_live(
    hass: HomeAssistant, services: list[list]
) -> None:
    old, new = _sources(hass)
    entry = _entry(hass, old.entry_id, shadow=False)
    with patch(UPDATE, return_value=()):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        for calls in services:
            calls.clear()
        flow = await hass.config_entries.options.async_init(entry.entry_id)
        defaults = {
            str(key): key.default() for key in flow["data_schema"].schema
            if str(key) in (CONF_NORDPOOL_ENTRY, CONF_AREA)
        }
        assert defaults == {CONF_NORDPOOL_ENTRY: old.entry_id, CONF_AREA: "SE4"}
        result = await hass.config_entries.options.async_configure(
            # Already live and same actuator: no safety acknowledgement is asked.
            flow["flow_id"], _form(new.entry_id, " se3 ", shadow=False)
        )
        await hass.async_block_till_done()
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.data[CONF_NORDPOOL_ENTRY] == new.entry_id
    assert entry.data[CONF_AREA] == "SE3"
    assert CONF_NORDPOOL_ENTRY not in entry.options
    assert CONF_AREA not in entry.options
    assert entry.state is ConfigEntryState.LOADED
    assert not any(services)


@pytest.mark.parametrize(
    ("source", "area", "error"),
    [("missing", "SE4", "invalid_price_source"), ("new", "SE4", "invalid_market_area"),
     ("old", "", "invalid_market_area")],
)
async def test_options_validate_a_changed_price_source(
    hass: HomeAssistant, source: str, area: str, error: str
) -> None:
    old, new = _sources(hass)
    entry = _entry(hass, old.entry_id)
    ids = {"old": old.entry_id, "new": new.entry_id, "missing": "missing"}
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        flow["flow_id"], _form(ids[source], area, shadow=True)
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": error}
    assert entry.data[CONF_NORDPOOL_ENTRY] == old.entry_id


async def test_options_keep_an_unchanged_broken_source(hass: HomeAssistant) -> None:
    """A deleted source is a repair; it must not block other option changes."""
    entry = _entry(hass, "deleted-source")
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        flow["flow_id"], _form("deleted-source", "SE4", shadow=True)
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.data[CONF_NORDPOOL_ENTRY] == "deleted-source"


async def test_price_sensor_shows_source_and_shares_the_device(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-01-15 10:01:00+00:00")
    old, _new = _sources(hass)
    entry = _entry(hass, old.entry_id)
    with patch(UPDATE, return_value=_prices(0.5, 1.0, 1.0, 1.0, 2.0, 2.0)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        state = hass.states.get(SENSOR)
        assert state is not None
        assert float(state.state) == 0.5
        assert state.attributes["unit_of_measurement"] == "SEK/kWh"
        assert state.attributes["price_source_entry_id"] == old.entry_id
        assert state.attributes["price_source_title"] == "Old prices"
        assert state.attributes["market_area"] == "SE4"
        assert state.attributes["future_weighted_price_sek_per_kwh"] is not None
        assert state.attributes["today_prices_cached"] is False

        registry = er.async_get(hass)
        sensor_device = registry.async_get(SENSOR).device_id
        assert sensor_device is not None
        assert registry.async_get("climate.house").device_id == sensor_device
        device = dr.async_get(hass).async_get(sensor_device)
        assert device is not None
        assert device.name == "House"

        # Bounded churn: an unchanged price is not rewritten on clock ticks.
        updated = state.last_updated
        freezer.tick(timedelta(minutes=5))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        same = hass.states.get(SENSOR)
        assert same is not None
        assert same.last_updated == updated

        # The next interval's price shows up on the next aligned tick.
        freezer.move_to("2026-01-15 11:05:00+00:00")
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        moved = hass.states.get(SENSOR)
        assert moved is not None
        assert float(moved.state) == 1.0
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_price_sensor_without_prices_or_source(hass: HomeAssistant) -> None:
    entry = _entry(hass, "deleted-source")
    with patch(UPDATE, return_value=()):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    state = hass.states.get(SENSOR)
    assert state is not None
    assert state.state == "unknown"
    assert state.attributes["price_source_title"] is None
    assert state.attributes["future_weighted_price_sek_per_kwh"] is None
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_price_sensor_tolerates_invalid_settings(hass: HomeAssistant) -> None:
    old, _new = _sources(hass)
    entry = _entry(hass, old.entry_id)
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_LOOKAHEAD: 5}
    )
    with patch(UPDATE, return_value=_prices(0.5, 1.0)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    state = hass.states.get(SENSOR)
    assert state is not None
    assert state.state == "unknown"
    assert await hass.config_entries.async_unload(entry.entry_id)
