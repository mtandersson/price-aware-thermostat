"""Nord Pool source reconfiguration through the real HA flow manager."""

from datetime import date
from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant.common import MockConfigEntry, async_mock_service

from custom_components.price_aware_thermostat.const import (
    CONF_AREA,
    CONF_CONTROL_TYPE,
    CONF_HEATER,
    CONF_HVAC_MODES,
    CONF_NAME,
    CONF_NORDPOOL_ENTRY,
    CONF_SENSOR,
    CONF_SHADOW,
    CONTROL_CLIMATE,
    CONTROL_SWITCH,
    DEFAULTS,
    DOMAIN,
)

UPDATE = (
    "custom_components.price_aware_thermostat.coordinator."
    "NordPoolPriceCoordinator._async_update_data"
)


@pytest.fixture(autouse=True)
def entities(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.room", "21", {"unit_of_measurement": "°C"})
    hass.states.async_set("switch.heater", "off")
    hass.states.async_set("climate.pump", "off", {
        "hvac_modes": ["heat", "cool", "off"], "min_temp": 10, "max_temp": 31,
        "target_temp_step": 0.5, "temperature": 22, "supported_features": 1,
    })


@pytest.fixture
def services(hass: HomeAssistant) -> list[list]:
    return [async_mock_service(hass, domain, service) for domain, service in [
        ("switch", "turn_on"), ("switch", "turn_off"),
        ("climate", "set_temperature"), ("climate", "set_hvac_mode"),
        ("climate", "turn_on"), ("climate", "turn_off"),
    ]]


def _sources(hass: HomeAssistant) -> tuple[MockConfigEntry, MockConfigEntry]:
    old = MockConfigEntry(domain="nordpool", title="Old", data={"areas": ["SE4"]})
    new = MockConfigEntry(domain="nordpool", title="New", data={"areas": ["SE3", "NO1"]})
    old.add_to_hass(hass)
    new.add_to_hass(hass)
    return old, new


def _entry(
    hass: HomeAssistant, source: MockConfigEntry, kind: str, *, shadow: bool = True
) -> MockConfigEntry:
    data = {
        **DEFAULTS, CONF_NAME: "Test", CONF_CONTROL_TYPE: kind,
        CONF_SENSOR: "sensor.room",
        CONF_HEATER: "switch.heater" if kind == CONTROL_SWITCH else "climate.pump",
        CONF_NORDPOOL_ENTRY: source.entry_id, CONF_AREA: "SE4",
    }
    if kind == CONTROL_CLIMATE:
        data[CONF_HVAC_MODES] = ["heat", "cool"]
    entry = MockConfigEntry(
        domain=DOMAIN, version=2, title="Test", data=data,
        options={CONF_SHADOW: shadow}, unique_id=str(data[CONF_HEATER]),
    )
    entry.add_to_hass(hass)
    return entry


async def _start(hass: HomeAssistant, entry: MockConfigEntry):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    return await entry.start_reconfigure_flow(hass)


@pytest.mark.parametrize("kind", [CONTROL_SWITCH, CONTROL_CLIMATE])
@pytest.mark.parametrize("area", ["no1", "NO1", " No1 "])
async def test_reconfigure_changes_source_and_area(
    hass: HomeAssistant, services: list[list], kind: str, area: str
) -> None:
    old, new = _sources(hass)
    entry = _entry(hass, old, kind)
    before = dict(entry.data)
    with patch(UPDATE, return_value=()):
        result = await _start(hass, entry)
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "reconfigure"
        assert {str(key) for key in result["data_schema"].schema} == {
            CONF_NORDPOOL_ENTRY, CONF_AREA,
        }
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_NORDPOOL_ENTRY: new.entry_id, CONF_AREA: area}
        )
        await hass.async_block_till_done()
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "reconfigure_successful"
        assert entry.state is ConfigEntryState.LOADED
        assert entry.data[CONF_NORDPOOL_ENTRY] == new.entry_id
        assert entry.data[CONF_AREA] == "NO1"
        # Reload rebuilt the coordinator from the updated entry data.
        coordinator = entry.runtime_data
        assert coordinator._source_entry_id == new.entry_id
        assert coordinator._area == "NO1"
        # Nothing else changed.
        for key in before.keys() - {CONF_NORDPOOL_ENTRY, CONF_AREA}:
            assert entry.data[key] == before[key]
        assert entry.unique_id == before[CONF_HEATER]
        assert entry.options == {CONF_SHADOW: True}
        assert await hass.config_entries.async_unload(entry.entry_id)
    assert not any(services)


async def test_reconfigure_uses_new_source_for_price_requests(hass: HomeAssistant) -> None:
    old, new = _sources(hass)
    entry = _entry(hass, old, CONTROL_SWITCH)
    calls = async_mock_service(hass, "nordpool", "get_prices_for_date", response={})
    with patch(UPDATE, return_value=()):
        result = await _start(hass, entry)
        await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_NORDPOOL_ENTRY: new.entry_id, CONF_AREA: "SE3"}
        )
        await hass.async_block_till_done()
        coordinator = entry.runtime_data
    with pytest.raises(ValueError):  # the empty mock response is not a price series
        await coordinator._fetch_day(date(2026, 1, 1))
    assert calls[-1].data["config_entry"] == new.entry_id
    assert calls[-1].data["areas"] == ["SE3"]


@pytest.mark.parametrize(
    ("kind", "case", "error"),
    [
        (CONTROL_SWITCH, "unknown", "invalid_price_source"),
        (CONTROL_CLIMATE, "wrong_domain", "invalid_price_source"),
        (CONTROL_SWITCH, "area", "invalid_market_area"),
        (CONTROL_CLIMATE, "blank", "invalid_market_area"),
    ],
)
async def test_reconfigure_rejects_invalid_source(
    hass: HomeAssistant, services: list[list], kind: str, case: str, error: str
) -> None:
    old, new = _sources(hass)
    other = MockConfigEntry(domain="other", data={"areas": ["SE3"]})
    other.add_to_hass(hass)
    entry = _entry(hass, old, kind)
    before = dict(entry.data)
    user_input = {
        "unknown": {CONF_NORDPOOL_ENTRY: "missing", CONF_AREA: "SE3"},
        "wrong_domain": {CONF_NORDPOOL_ENTRY: other.entry_id, CONF_AREA: "SE3"},
        "area": {CONF_NORDPOOL_ENTRY: new.entry_id, CONF_AREA: "SE4"},
        "blank": {CONF_NORDPOOL_ENTRY: new.entry_id, CONF_AREA: "  "},
    }[case]
    with patch(UPDATE, return_value=()):
        result = await _start(hass, entry)
        coordinator = entry.runtime_data
        result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input)
        await hass.async_block_till_done()
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": error}
        assert dict(entry.data) == before
        assert entry.runtime_data is coordinator
        # The form can be corrected and resubmitted.
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_NORDPOOL_ENTRY: new.entry_id, CONF_AREA: "se3"}
        )
        assert result["type"] is FlowResultType.ABORT
        assert entry.data[CONF_AREA] == "SE3"
    assert not any(services)


@pytest.mark.parametrize("kind", [CONTROL_SWITCH, CONTROL_CLIMATE])
async def test_reconfigure_allowed_while_live_without_actuation(
    hass: HomeAssistant, services: list[list], kind: str
) -> None:
    """A price source never commands the actuator, so live entries may change it."""
    old, new = _sources(hass)
    entry = _entry(hass, old, kind, shadow=False)
    with patch(UPDATE, return_value=()):
        result = await _start(hass, entry)
        assert result["type"] is FlowResultType.FORM
        for call_list in services:
            call_list.clear()
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_NORDPOOL_ENTRY: new.entry_id, CONF_AREA: " no1 "}
        )
        await hass.async_block_till_done()
        assert result["reason"] == "reconfigure_successful"
        assert entry.data[CONF_NORDPOOL_ENTRY] == new.entry_id
        assert entry.data[CONF_AREA] == "NO1"
        assert entry.options == {CONF_SHADOW: False}
        assert entry.state is ConfigEntryState.LOADED
    assert not any(services)


async def test_reconfigure_defaults_to_shadow_when_unset(hass: HomeAssistant) -> None:
    old, new = _sources(hass)
    entry = _entry(hass, old, CONTROL_SWITCH)
    hass.config_entries.async_update_entry(
        entry, data={k: v for k, v in entry.data.items() if k != CONF_SHADOW}, options={}
    )
    assert CONF_SHADOW not in entry.data
    with patch(UPDATE, return_value=()):
        result = await _start(hass, entry)
        assert result["type"] is FlowResultType.FORM
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_NORDPOOL_ENTRY: new.entry_id, CONF_AREA: "SE3"}
        )
        assert result["reason"] == "reconfigure_successful"
        assert entry.data[CONF_AREA] == "SE3"


async def test_reconfigure_source_without_areas_is_invalid_area(
    hass: HomeAssistant,
) -> None:
    old, _new = _sources(hass)
    bare = MockConfigEntry(domain="nordpool", data={})
    bare.add_to_hass(hass)
    entry = _entry(hass, old, CONTROL_SWITCH)
    with patch(UPDATE, return_value=()):
        result = await _start(hass, entry)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_NORDPOOL_ENTRY: bare.entry_id, CONF_AREA: "SE3"}
        )
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": "invalid_market_area"}
        assert entry.data[CONF_AREA] == "SE4"
