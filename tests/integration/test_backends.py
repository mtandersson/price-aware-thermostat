"""Backend selection, migration and shadow-only AC contract tests."""

from unittest.mock import patch

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant.common import MockConfigEntry, async_mock_service

from custom_components.price_aware_thermostat import async_migrate_entry
from custom_components.price_aware_thermostat.backends import (
    backend_from_settings,
    validate_backend,
)
from custom_components.price_aware_thermostat.const import (
    CONF_AREA,
    CONF_BASE_TEMP,
    CONF_CONTROL_TYPE,
    CONF_HEATER,
    CONF_HVAC_MODES,
    CONF_MAX_DEVIATION,
    CONF_MAX_TEMP,
    CONF_MIN_TEMP,
    CONF_NAME,
    CONF_NORDPOOL_ENTRY,
    CONF_SENSOR,
    CONF_SHADOW,
    CONTROL_CLIMATE,
    CONTROL_SWITCH,
    DEFAULTS,
    DOMAIN,
)


def entities(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.room", "21", {"unit_of_measurement": "°C"})
    hass.states.async_set("climate.pump", "off", {
        "hvac_modes": ["heat", "cool", "off"], "min_temp": 10, "max_temp": 31,
        "target_temp_step": 0.5, "temperature": 22, "supported_features": 1,
    })


def settings(source: str = "test-price") -> dict[str, object]:
    return {
        **DEFAULTS, CONF_CONTROL_TYPE: CONTROL_CLIMATE,
        CONF_HEATER: "climate.pump", CONF_SENSOR: "sensor.room",
        CONF_HVAC_MODES: ["heat", "cool"], CONF_NAME: "Room AC",
        CONF_NORDPOOL_ENTRY: source, CONF_AREA: "SE4",
        CONF_MIN_TEMP: 18, CONF_BASE_TEMP: 22, CONF_MAX_TEMP: 26,
    }


async def test_legacy_migration_retains_all_user_choices(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, version=1, unique_id="switch.storage",
        data={CONF_HEATER: "switch.storage", CONF_SENSOR: "sensor.storage"},
        options={CONF_BASE_TEMP: 5.3, CONF_SHADOW: False},
    )
    entry.add_to_hass(hass)
    old_options = dict(entry.options)
    assert await async_migrate_entry(hass, entry)
    assert (entry.version, entry.minor_version) == (2, 2)
    assert entry.data[CONF_CONTROL_TYPE] == CONTROL_SWITCH
    assert entry.unique_id == "switch.storage"
    # User choices are untouched; the old implicit limits are pinned so the
    # wider new defaults never apply to an existing entry.
    expected = {
        **old_options, CONF_MAX_DEVIATION: 0.8, CONF_MIN_TEMP: 5.3, CONF_MAX_TEMP: 7.5,
    }
    assert dict(entry.options) == expected
    assert backend_from_settings({**entry.data, **entry.options}).supports_live_control
    assert await async_migrate_entry(hass, entry)
    assert dict(entry.options) == expected


@pytest.mark.parametrize(
    ("data", "options", "expected"),
    [
        # Missing everywhere: default deviation, explicit limits retained.
        ({CONF_MIN_TEMP: 18, CONF_MAX_TEMP: 26}, {}, 0.8),
        # Explicitly empty (cleared in the old options flow): default.
        ({CONF_MIN_TEMP: 18, CONF_MAX_TEMP: 26, CONF_MAX_DEVIATION: 1.5},
         {CONF_MAX_DEVIATION: None}, 0.8),
        # A chosen value is kept, whether in data or options.
        ({CONF_MIN_TEMP: 18, CONF_MAX_TEMP: 26, CONF_MAX_DEVIATION: 1.5}, {}, 1.5),
        ({CONF_MIN_TEMP: 18, CONF_MAX_TEMP: 26}, {CONF_MAX_DEVIATION: 0.0}, 0.0),
    ],
)
async def test_minor_migration_sets_deviation_and_keeps_limits(
    hass: HomeAssistant, data: dict[str, object], options: dict[str, object], expected: float
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, version=2, minor_version=1,
        data={CONF_CONTROL_TYPE: CONTROL_CLIMATE, **data}, options=options,
    )
    entry.add_to_hass(hass)
    assert await async_migrate_entry(hass, entry)
    merged = {**entry.data, **entry.options}
    assert entry.minor_version == 2
    assert merged[CONF_MAX_DEVIATION] == expected
    assert (merged[CONF_MIN_TEMP], merged[CONF_MAX_TEMP]) == (18, 26)


async def test_current_entries_are_not_migrated_again(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, version=2, minor_version=2, data={}, options={CONF_SHADOW: True},
    )
    entry.add_to_hass(hass)
    assert await async_migrate_entry(hass, entry)
    assert dict(entry.options) == {CONF_SHADOW: True}


async def test_future_migration_is_rejected(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, version=3)
    assert not await async_migrate_entry(hass, entry)


async def test_climate_flow_creates_separate_shadow_entry(hass: HomeAssistant) -> None:
    entities(hass)
    source = MockConfigEntry(domain="nordpool", data={"areas": ["SE4"]})
    source.add_to_hass(hass)
    flow = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    data = settings(source.entry_id)
    data.pop(CONF_SHADOW)
    result = await hass.config_entries.flow.async_configure(flow["flow_id"], data)
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_CONTROL_TYPE] == CONTROL_CLIMATE
    assert result["options"][CONF_SHADOW] is True
    assert result["data"][CONF_HVAC_MODES] == ["heat", "cool"]


@pytest.mark.parametrize(("changes", "error"), [
    ({CONF_HEATER: "switch.missing"}, "invalid_backend"),
    ({CONF_HEATER: "climate.missing"}, "invalid_actuator"),
    ({CONF_SENSOR: "sensor.missing"}, "invalid_room_sensor"),
    ({CONF_HVAC_MODES: ["auto"]}, "invalid_backend"),
    ({CONF_HVAC_MODES: []}, "invalid_backend"),
    ({CONF_CONTROL_TYPE: "unknown"}, "invalid_backend"),
])
async def test_invalid_backend_selection(
    hass: HomeAssistant, changes: dict[str, object], error: str
) -> None:
    entities(hass)
    assert validate_backend(hass, {**settings(), **changes}) == error


@pytest.mark.parametrize(("attributes", "error"), [
    ({"hvac_modes": ["heat", "off"]}, "invalid_hvac_modes"),
    ({"hvac_modes": ["heat", "cool"]}, "invalid_hvac_modes"),
    ({"min_temp": 40}, "invalid_backend_limits"),
    ({"target_temp_step": 0}, "invalid_backend_limits"),
    ({"supported_features": 0}, "invalid_backend_limits"),
    ({"max_temp": float("nan")}, "invalid_backend_limits"),
])
async def test_backend_capability_validation(
    hass: HomeAssistant, attributes: dict[str, object], error: str
) -> None:
    entities(hass)
    state = hass.states.get("climate.pump")
    assert state
    hass.states.async_set("climate.pump", "off", {**state.attributes, **attributes})
    assert validate_backend(hass, settings()) == error


@pytest.mark.parametrize("unit", [None, "%"])
async def test_room_sensor_requires_temperature_units(hass: HomeAssistant, unit: str | None) -> None:
    entities(hass)
    hass.states.async_set("sensor.room", "21", {"unit_of_measurement": unit})
    assert validate_backend(hass, settings()) == "invalid_room_sensor"


async def test_duplicate_and_recursive_backends_rejected(hass: HomeAssistant) -> None:
    entities(hass)
    entry = MockConfigEntry(domain=DOMAIN, version=2, data=settings())
    entry.add_to_hass(hass)
    assert validate_backend(hass, settings()) == "already_configured"
    assert validate_backend(hass, settings(), entry_id=entry.entry_id) is None
    registry = er.async_get(hass)
    wrapper = registry.async_get_or_create("climate", DOMAIN, "wrapper", config_entry=entry)
    assert validate_backend(hass, {**settings(), CONF_HEATER: wrapper.entity_id}) == "recursive_backend"
    assert validate_backend(
        hass, {**settings(), CONF_HEATER: wrapper.entity_id}, entry_id=entry.entry_id
    ) == "recursive_backend"


async def test_climate_shadow_entry_never_dispatches_and_restores_target(
    hass: HomeAssistant,
) -> None:
    entities(hass)
    entry = MockConfigEntry(
        domain=DOMAIN, version=2, title="Room AC", data=settings(),
        options={CONF_SHADOW: True, CONF_BASE_TEMP: 23},
    )
    entry.add_to_hass(hass)
    calls = [async_mock_service(hass, domain, service) for domain, service in [
        ("switch", "turn_on"), ("switch", "turn_off"),
        ("climate", "set_temperature"), ("climate", "set_hvac_mode"),
        ("climate", "turn_on"), ("climate", "turn_off"),
    ]]
    with patch(
        "custom_components.price_aware_thermostat.coordinator."
        "NordPoolPriceCoordinator._async_update_data", return_value=(),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        state = hass.states.get("climate.room_ac")
        assert state
        assert state.state == "off"
        assert state.attributes["hvac_modes"] == ["heat", "cool", "off"]
        assert state.attributes["temperature"] == 23
        assert state.attributes["current_temperature"] == 21
        assert state.attributes["shadow_mode"] is True
        assert state.attributes["live_control_supported"] is True
        assert state.attributes["native_target_c"] is None
        assert state.attributes["optimization_reason"] == "off"
        await hass.services.async_call("climate", "set_temperature", {
            "entity_id": "climate.room_ac", "temperature": 24,
        }, blocking=True)
        await hass.async_block_till_done()
        # Ignore the user call to our wrapper; no service targets the pump.
        assert not any(call.data.get("entity_id") == "climate.pump" for group in calls for call in group)
        assert entry.options[CONF_BASE_TEMP] == 24
        assert await hass.config_entries.async_unload(entry.entry_id)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        restored = hass.states.get("climate.room_ac")
        assert restored and restored.attributes["temperature"] == 24
        assert await hass.config_entries.async_unload(entry.entry_id)
        assert not any(call.data.get("entity_id") == "climate.pump" for group in calls for call in group)


def live_options(**extra: object) -> dict[str, object]:
    return {
        **DEFAULTS, CONF_BASE_TEMP: 22, CONF_MIN_TEMP: 18, CONF_MAX_TEMP: 26,
        CONF_HEATER: "climate.pump", CONF_SENSOR: "sensor.room",
        CONF_HVAC_MODES: ["heat", "cool"], CONF_SHADOW: False, **extra,
    }


async def test_climate_live_control_requires_safety_confirmation(hass: HomeAssistant) -> None:
    entities(hass)
    entry = MockConfigEntry(domain=DOMAIN, version=2, data=settings(), options={CONF_SHADOW: True})
    entry.add_to_hass(hass)
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(flow["flow_id"], live_options())
    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "safety_confirmation_required"
    assert entry.options[CONF_SHADOW] is True


async def test_climate_live_control_can_be_enabled_after_confirmation(hass: HomeAssistant) -> None:
    entities(hass)
    entry = MockConfigEntry(domain=DOMAIN, version=2, data=settings(), options={CONF_SHADOW: True})
    entry.add_to_hass(hass)
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        flow["flow_id"], live_options(safety_acknowledged=True)
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_SHADOW] is False
    assert result["data"]["safety_acknowledged"] is True
    assert result["data"]["safety_acknowledged_at"]


@pytest.mark.parametrize(("changes", "error"), [
    ({"heat_compensation": 11}, "invalid_ac_settings"),
    ({"cool_compensation": -10.5}, "invalid_ac_settings"),
    ({"window_open_delay_seconds": -1}, "invalid_ac_settings"),
    ({"window_close_delay_seconds": 4000}, "invalid_ac_settings"),
    ({"maximum_price_deviation": -0.1}, "invalid_ac_settings"),
    ({"maximum_price_deviation": float("nan")}, "invalid_ac_settings"),
    ({"min_cycle_seconds": 30}, "invalid_ac_settings"),
    ({"min_cycle_seconds": 7200}, "invalid_ac_settings"),
    ({"window_contacts": ["sensor.not_a_contact"]}, "invalid_window_sensor"),
    ({"window_contacts": "binary_sensor.window"}, "invalid_window_sensor"),
])
async def test_ac_only_settings_are_validated(
    hass: HomeAssistant, changes: dict[str, object], error: str
) -> None:
    entities(hass)
    assert validate_backend(hass, {**settings(), **changes}) == error


async def test_window_contacts_clear_and_deviation_is_kept_in_options(hass: HomeAssistant) -> None:
    entities(hass)
    entry = MockConfigEntry(
        domain=DOMAIN, version=2,
        data={**settings(), "maximum_price_deviation": 0.5,
              "window_contacts": ["binary_sensor.window"]},
        options={CONF_SHADOW: True},
    )
    entry.add_to_hass(hass)
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        flow["flow_id"], live_options(**{CONF_SHADOW: True, CONF_MAX_DEVIATION: 1.2})
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    # The deviation is a required field (prefilled from the entry), never cleared.
    assert result["data"]["maximum_price_deviation"] == 1.2
    assert result["data"]["window_contacts"] == []


async def test_climate_options_retarget_preserves_existing_settings(hass: HomeAssistant) -> None:
    entities(hass)
    attrs = dict(hass.states.get("climate.pump").attributes)
    hass.states.async_set("climate.other", "off", attrs)
    entry = MockConfigEntry(domain=DOMAIN, version=2, data=settings(), options={
        CONF_SHADOW: True, CONF_BASE_TEMP: 23, "lookahead_minutes": 240,
    })
    entry.add_to_hass(hass)
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(flow["flow_id"], {
        CONF_HEATER: "climate.other", CONF_SENSOR: "sensor.room", CONF_SHADOW: True,
    })
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_BASE_TEMP] == 23
    assert result["data"]["lookahead_minutes"] == 240
    assert result["data"][CONF_HEATER] == "climate.other"
    assert entry.unique_id == "climate.other"


@pytest.mark.parametrize(("old_shadow", "old_state", "new_shadow"), [
    (False, "off", True), (False, "on", False), (True, "on", True),
    (True, "unavailable", True), (True, "off", False),
])
async def test_switch_retarget_requires_safe_handoff(
    hass: HomeAssistant, old_shadow: bool, old_state: str, new_shadow: bool
) -> None:
    entities(hass)
    hass.states.async_set("switch.old", old_state)
    hass.states.async_set("switch.new", "off")
    data = {**settings(), CONF_CONTROL_TYPE: CONTROL_SWITCH,
            CONF_HEATER: "switch.old", CONF_HVAC_MODES: ["heat"]}
    entry = MockConfigEntry(domain=DOMAIN, version=2, data=data, options={CONF_SHADOW: old_shadow})
    entry.add_to_hass(hass)
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(flow["flow_id"], {
        CONF_HEATER: "switch.new", CONF_SHADOW: new_shadow,
        # The acknowledgement box is only offered while the entry is in shadow.
        **({"safety_acknowledged": True} if old_shadow else {}),
    })
    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "actuator_handoff_required"
    assert entry.options.get(CONF_HEATER, entry.data[CONF_HEATER]) == "switch.old"


async def test_switch_shadow_retarget_frees_previous_actuator(hass: HomeAssistant) -> None:
    entities(hass)
    hass.states.async_set("switch.old", "off")
    hass.states.async_set("switch.new", "off")
    source = MockConfigEntry(domain="nordpool", data={"areas": ["SE4"]})
    source.add_to_hass(hass)
    data = {**settings(source.entry_id), CONF_CONTROL_TYPE: CONTROL_SWITCH,
            CONF_HEATER: "switch.old", CONF_HVAC_MODES: ["heat"]}
    entry = MockConfigEntry(domain=DOMAIN, version=2, unique_id="switch.old",
                            data=data, options={CONF_SHADOW: True})
    entry.add_to_hass(hass)
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(flow["flow_id"], {
        CONF_HEATER: "switch.new", CONF_SHADOW: True,
    })
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.unique_id == "switch.new"
    assert entry.options[CONF_HEATER] == "switch.new"
    flow = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    data.pop(CONF_SHADOW)
    result = await hass.config_entries.flow.async_configure(flow["flow_id"], data)
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_HEATER] == "switch.old"


async def test_loaded_climate_options_reload_rebinds_without_commands(hass: HomeAssistant) -> None:
    entities(hass)
    original = hass.states.get("climate.pump")
    assert original
    hass.states.async_set("climate.other", "off", dict(original.attributes))
    hass.states.async_set("sensor.other", "19", {"unit_of_measurement": "°C"})
    entry = MockConfigEntry(
        domain=DOMAIN, version=2, title="Room AC", unique_id="climate.pump",
        data=settings(), options={CONF_SHADOW: True, CONF_BASE_TEMP: 23},
    )
    entry.add_to_hass(hass)
    calls = [async_mock_service(hass, domain, service) for domain, service in [
        ("switch", "turn_on"), ("switch", "turn_off"),
        ("climate", "set_temperature"), ("climate", "set_hvac_mode"),
        ("climate", "turn_on"), ("climate", "turn_off"),
    ]]
    with patch(
        "custom_components.price_aware_thermostat.coordinator."
        "NordPoolPriceCoordinator._async_update_data", return_value=(),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        registry = er.async_get(hass)
        wrapper = registry.async_get("climate.room_ac")
        assert wrapper
        wrapper_unique_id = wrapper.unique_id
        flow = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(flow["flow_id"], {
            CONF_HEATER: "climate.other", CONF_SENSOR: "sensor.other", CONF_SHADOW: True,
        })
        assert result["type"] == FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done()
        state = hass.states.get("climate.room_ac")
        assert state
        assert state.attributes["backend_entity_id"] == "climate.other"
        assert state.attributes["current_temperature"] == 19
        assert state.attributes["temperature"] == 23
        assert registry.async_get("climate.room_ac").unique_id == wrapper_unique_id
        # Old sources no longer update the reloaded wrapper.
        hass.states.async_set("sensor.room", "10", {"unit_of_measurement": "°C"})
        await hass.async_block_till_done()
        assert hass.states.get("climate.room_ac").attributes["current_temperature"] == 19
        assert all(not group for group in calls)
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_switch_entries_do_not_store_heat_pump_settings(hass: HomeAssistant) -> None:
    entities(hass)
    hass.states.async_set("switch.storage", "off")
    source = MockConfigEntry(domain="nordpool", data={"areas": ["SE4"]})
    source.add_to_hass(hass)
    flow = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    data = {**settings(source.entry_id), CONF_CONTROL_TYPE: CONTROL_SWITCH,
            CONF_HEATER: "switch.storage", CONF_HVAC_MODES: ["heat"],
            "heat_compensation": 2.0, "window_contacts": ["binary_sensor.window"],
            "maximum_price_deviation": 0.5}
    data.pop(CONF_SHADOW)
    result = await hass.config_entries.flow.async_configure(flow["flow_id"], data)
    assert result["type"] == FlowResultType.CREATE_ENTRY
    # The price deviation applies to every control type and is kept.
    assert result["data"]["maximum_price_deviation"] == 0.5
    for key in ("heat_compensation", "window_contacts", CONF_HVAC_MODES):
        assert key not in result["data"]


async def test_live_actuator_change_is_blocked_even_when_acknowledged(
    hass: HomeAssistant,
) -> None:
    """While live, a stored ack never covers a new actuator: retarget is refused."""
    entities(hass)
    hass.states.async_set("switch.old", "off")
    hass.states.async_set("switch.new", "off")
    data = {**settings(), CONF_CONTROL_TYPE: CONTROL_SWITCH,
            CONF_HEATER: "switch.old", CONF_HVAC_MODES: ["heat"]}
    entry = MockConfigEntry(domain=DOMAIN, version=2, data=data, options={
        CONF_SHADOW: False, "safety_acknowledged": True,
    })
    entry.add_to_hass(hass)
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(flow["flow_id"], {
        CONF_HEATER: "switch.new", CONF_SHADOW: False,
    })
    assert result["errors"]["base"] == "actuator_handoff_required"
    assert entry.options.get(CONF_HEATER, entry.data[CONF_HEATER]) == "switch.old"
