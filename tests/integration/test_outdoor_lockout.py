"""Outdoor-temperature lockouts for the heat-pump/AC control type."""

from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.setup import async_setup_component
from pytest_homeassistant.common import MockConfigEntry, async_mock_service

from custom_components.price_aware_thermostat import control
from custom_components.price_aware_thermostat.ac_control import OutdoorLockout
from custom_components.price_aware_thermostat.backends import validate_backend
from custom_components.price_aware_thermostat.const import (
    CONF_COOL_MIN_OUTDOOR,
    CONF_HEAT_MAX_OUTDOOR,
    CONF_OUTDOOR_HYSTERESIS,
    CONF_OUTDOOR_SENSOR,
    CONF_SHADOW,
    DOMAIN,
)
from tests.integration.ac_support import ac_env
from tests.integration.test_backends import entities, live_options, settings

OUTDOOR = "sensor.outdoor"


def lockouts(**overrides: Any) -> dict[str, Any]:
    return {
        CONF_OUTDOOR_SENSOR: OUTDOOR, CONF_COOL_MIN_OUTDOOR: 15.0,
        CONF_HEAT_MAX_OUTDOOR: 15.0, CONF_OUTDOOR_HYSTERESIS: 1.0, **overrides,
    }


async def set_outdoor(hass: HomeAssistant, value: str, unit: str = "°C") -> None:
    hass.states.async_set(OUTDOOR, value, {"unit_of_measurement": unit})
    await hass.async_block_till_done()


# ------------------------------------------------------------------ pure logic
def test_cool_lockout_engages_below_and_releases_above_hysteresis() -> None:
    lock = OutdoorLockout(cool_min=15.0, hysteresis=1.0)
    lock.update(15.0)
    assert not lock.locks("cool")  # at the threshold cooling is allowed
    lock.update(14.9)
    assert lock.locks("cool")
    lock.update(15.9)
    assert lock.locks("cool")  # inside the hysteresis band
    lock.update(16.0)
    assert not lock.locks("cool")
    assert not lock.locks("heat") and not lock.locks("off")


def test_heat_lockout_engages_above_and_releases_below_hysteresis() -> None:
    lock = OutdoorLockout(heat_max=15.0, hysteresis=1.0)
    lock.update(15.0)
    assert not lock.locks("heat")
    lock.update(15.1)
    assert lock.locks("heat")
    lock.update(14.1)
    assert lock.locks("heat")
    lock.update(14.0)
    assert not lock.locks("heat")
    assert not lock.locks("cool")


def test_missing_reading_and_disabled_thresholds_fail_open() -> None:
    lock = OutdoorLockout(cool_min=15.0, heat_max=15.0)
    lock.update(30.0)
    assert lock.heat_locked
    lock.update(None)
    assert not lock.heat_locked and not lock.cool_locked
    lock.update(float("nan"))
    assert not lock.heat_locked
    disabled = OutdoorLockout()
    disabled.update(-30.0)
    disabled.update(40.0)
    assert not disabled.heat_locked and not disabled.cool_locked
    with pytest.raises(ValueError):
        OutdoorLockout(hysteresis=-1.0)


# ----------------------------------------------------------------- integration
async def test_heat_lockout_turns_the_pump_off_and_releases_with_hysteresis(
    hass: HomeAssistant,
) -> None:
    hass.states.async_set(OUTDOOR, "10", {"unit_of_measurement": "°C"})
    async with ac_env(hass, data=lockouts()) as env:
        await env.set_mode("heat")
        assert env.calls()[0] == ("mode", "heat")
        assert env.attrs["outdoor_temperature_c"] == 10.0
        assert env.attrs["outdoor_lockout"] == "none"
        await set_outdoor(hass, "16")
        assert env.calls()[-1] == ("mode", "off")
        attrs = env.attrs
        assert env.state().state == "heat"  # the wrapper mode is untouched
        assert attrs["intent_reason"] == "outdoor_lockout"
        assert attrs["pause_reason"] == "outdoor_lockout"
        assert attrs["outdoor_lockout"] == "heat"
        assert attrs["proposed_backend_mode"] == "off"
        count = len(env.calls())
        await set_outdoor(hass, "14.5")  # inside the band: still locked
        await env.advance(3600)
        assert len(env.calls()) == count
        assert env.attrs["outdoor_lockout"] == "heat"
        await set_outdoor(hass, "14.0")
        assert env.attrs["outdoor_lockout"] == "none"
        assert env.calls()[count:] == [("mode", "heat")]


async def test_cool_lockout_respects_the_restart_pause_on_release(hass: HomeAssistant) -> None:
    hass.states.async_set(OUTDOOR, "10", {"unit_of_measurement": "°F"})  # -12 °C
    async with ac_env(hass, room="26.0", min_cycle=300, data=lockouts()) as env:
        await env.advance(400)
        await env.set_mode("cool")
        assert env.calls() == []
        assert env.attrs["outdoor_lockout"] == "cool"
        assert env.attrs["pause_reason"] == "outdoor_lockout"
        await set_outdoor(hass, "15.5")
        assert env.attrs["outdoor_lockout"] == "cool"
        await set_outdoor(hass, "16")
        assert env.calls()[:1] == [("mode", "cool")]


async def test_only_the_affected_direction_is_locked(hass: HomeAssistant) -> None:
    # 10 °C outside: cooling is locked, heating is not.
    hass.states.async_set(OUTDOOR, "10", {"unit_of_measurement": "°C"})
    async with ac_env(hass, data=lockouts()) as env:
        await env.set_mode("heat")
        assert env.calls()[0] == ("mode", "heat")
        assert env.attrs["outdoor_lockout"] == "none"
        assert env.attrs["outdoor_cool_locked"] is True
        assert env.attrs["outdoor_heat_locked"] is False
        await env.set_room("26.0")
        await env.set_mode("cool")
        assert env.attrs["intent_reason"] == "outdoor_lockout"
        assert env.attrs["outdoor_lockout"] == "cool"
        await env.set_mode("off")
        assert env.attrs["outdoor_lockout"] == "none"


@pytest.mark.parametrize("fault", ["missing", "unavailable", "invalid", "stale"])
async def test_a_faulty_outdoor_sensor_never_locks(hass: HomeAssistant, fault: str) -> None:
    hass.states.async_set(OUTDOOR, "30", {"unit_of_measurement": "°C"})
    async with ac_env(hass, data=lockouts()) as env:
        await env.set_mode("heat")
        assert env.attrs["outdoor_lockout"] == "heat"
        assert env.calls() == []
        real = control.is_temperature_sample_fresh
        if fault == "missing":
            hass.states.async_remove(OUTDOOR)
        elif fault == "unavailable":
            await set_outdoor(hass, "unavailable")
        elif fault == "invalid":
            await set_outdoor(hass, "30", "%")
        outdoor = hass.states.get(OUTDOOR)
        stale_at = outdoor.last_reported if fault == "stale" and outdoor else None
        with patch(
            "custom_components.price_aware_thermostat.climate.is_temperature_sample_fresh",
            side_effect=lambda seen, now, age: seen != stale_at and real(seen, now, age),
        ):
            await env.advance(61)
        attrs = env.attrs
        assert attrs["outdoor_lockout"] == "none"
        assert attrs["outdoor_temperature_c"] is None
        expected = "unavailable" if fault == "missing" else fault
        assert attrs["outdoor_sensor_status"] == expected
        assert env.calls()[0] == ("mode", "heat")


async def test_lockout_state_is_restored_after_a_restart(hass: HomeAssistant) -> None:
    hass.states.async_set(OUTDOOR, "14.5", {"unit_of_measurement": "°C"})  # in the band
    restored = ("heat", {"outdoor_heat_locked": True, "outdoor_cool_locked": True})
    async with ac_env(hass, data=lockouts(), restore=restored) as env:
        attrs = env.attrs
        assert attrs["outdoor_heat_locked"] is True
        # 14.5 °C is below the cooling limit, so that lock holds either way.
        assert attrs["outdoor_cool_locked"] is True
        assert attrs["intent_reason"] == "outdoor_lockout"
        assert env.calls() == []


async def test_restore_ignores_a_lockout_that_is_no_longer_configured(
    hass: HomeAssistant,
) -> None:
    hass.states.async_set(OUTDOOR, "14.5", {"unit_of_measurement": "°C"})
    restored = ("heat", {"outdoor_heat_locked": True, "outdoor_cool_locked": True})
    data = lockouts(**{CONF_HEAT_MAX_OUTDOOR: None, CONF_COOL_MIN_OUTDOOR: None})
    async with ac_env(hass, data=data, restore=restored) as env:
        assert env.attrs["outdoor_heat_locked"] is False
        assert env.attrs["outdoor_cool_locked"] is False


async def test_without_a_sensor_nothing_is_locked(hass: HomeAssistant) -> None:
    async with ac_env(hass) as env:
        await env.set_mode("heat")
        attrs = env.attrs
        assert attrs["outdoor_sensor_status"] == "not_configured"
        assert attrs["outdoor_lockout"] == "none"
        assert env.calls()[0] == ("mode", "heat")


async def test_shadow_mode_lockouts_only_change_proposals(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, "switch", {})
    switch_calls = [async_mock_service(hass, "switch", s) for s in ("turn_on", "turn_off")]
    hass.states.async_set(OUTDOOR, "10", {"unit_of_measurement": "°C"})
    async with ac_env(hass, shadow=True, pump_mode=HVACMode.HEAT, data=lockouts()) as env:
        await env.set_mode("heat")
        assert env.attrs["proposed_backend_mode"] == "heat"
        await set_outdoor(hass, "20")
        assert env.attrs["proposed_backend_mode"] == "off"
        assert env.attrs["outdoor_lockout"] == "heat"
        await env.set_room("26.0")
        await env.set_mode("cool")
        await env.advance(3600)
        await set_outdoor(hass, "5")
        await env.advance(3600)
        assert env.calls() == []
        assert all(not group for group in switch_calls)


async def test_switch_type_ignores_outdoor_settings(hass: HomeAssistant) -> None:
    source = MockConfigEntry(domain="nordpool", data={"areas": ["SE4"]})
    source.add_to_hass(hass)
    hass.states.async_set("sensor.room", "6", {"unit_of_measurement": "°C"})
    hass.states.async_set("switch.heater", "off")
    hass.states.async_set(OUTDOOR, "30", {"unit_of_measurement": "°C"})
    flow = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    with patch(f"custom_components.{DOMAIN}.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(flow["flow_id"], {
            "control_type": "switch", "name": "Shed", "temperature_sensor": "sensor.room",
            "heater": "switch.heater", "nordpool_config_entry": source.entry_id,
            "market_area": "SE4", **lockouts(),
        })
    assert result["type"] is FlowResultType.CREATE_ENTRY
    for key in lockouts():
        assert key not in result["data"]
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    options = await hass.config_entries.options.async_init(entry.entry_id)
    fields = {str(key) for key in options["data_schema"].schema}
    assert not fields & set(lockouts())


# ------------------------------------------------------------------ validation
@pytest.mark.parametrize(("changes", "state", "error"), [
    ({CONF_COOL_MIN_OUTDOOR: -41}, None, "invalid_ac_settings"),
    ({CONF_HEAT_MAX_OUTDOOR: 40.5}, None, "invalid_ac_settings"),
    ({CONF_HEAT_MAX_OUTDOOR: "warm"}, None, "invalid_ac_settings"),
    ({CONF_OUTDOOR_HYSTERESIS: 10.5}, None, "invalid_ac_settings"),
    ({CONF_OUTDOOR_HYSTERESIS: -0.1}, None, "invalid_ac_settings"),
    ({CONF_OUTDOOR_SENSOR: "sensor.nowhere"}, None, "invalid_outdoor_sensor"),
    ({CONF_OUTDOOR_SENSOR: "binary_sensor.door"}, ("on", None), "invalid_outdoor_sensor"),
    ({}, ("cold", "°C"), "invalid_outdoor_sensor"),
    ({}, ("12", "%"), "invalid_outdoor_sensor"),
    ({}, ("unavailable", None), None),
    ({}, ("12", "°C"), None),
    ({CONF_OUTDOOR_SENSOR: None, CONF_COOL_MIN_OUTDOOR: None}, None, None),
])
async def test_outdoor_settings_are_validated(
    hass: HomeAssistant, changes: dict[str, Any],
    state: tuple[str, str | None] | None, error: str | None,
) -> None:
    entities(hass)
    sensor = str(changes.get(CONF_OUTDOOR_SENSOR) or OUTDOOR)
    hass.states.async_set(OUTDOOR, "12", {"unit_of_measurement": "°C"})
    if state is not None:
        attrs = {"unit_of_measurement": state[1]} if state[1] else {}
        hass.states.async_set(sensor, state[0], attrs)
    assert validate_backend(hass, {**settings(), **lockouts(), **changes}) == error


async def test_options_flow_validates_saves_and_clears_lockouts(hass: HomeAssistant) -> None:
    entities(hass)
    hass.states.async_set(OUTDOOR, "12", {"unit_of_measurement": "°C"})
    entry = MockConfigEntry(
        domain=DOMAIN, version=2, data=settings(), options={CONF_SHADOW: True}
    )
    entry.add_to_hass(hass)
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    hass.states.async_set("sensor.humidity", "40", {"unit_of_measurement": "%"})
    rejected = await hass.config_entries.options.async_configure(
        flow["flow_id"],
        live_options(**{CONF_SHADOW: True, **lockouts(**{CONF_OUTDOOR_SENSOR: "sensor.humidity"})}),
    )
    assert rejected["errors"] == {"base": "invalid_outdoor_sensor"}
    saved = await hass.config_entries.options.async_configure(
        flow["flow_id"], live_options(**{CONF_SHADOW: True, **lockouts()})
    )
    assert saved["type"] is FlowResultType.CREATE_ENTRY
    assert saved["data"][CONF_OUTDOOR_SENSOR] == OUTDOOR
    assert saved["data"][CONF_COOL_MIN_OUTDOOR] == 15.0
    # Emptied optional fields are stored as disabled, not silently kept.
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    cleared = await hass.config_entries.options.async_configure(
        flow["flow_id"], live_options(**{CONF_SHADOW: True})
    )
    assert cleared["type"] is FlowResultType.CREATE_ENTRY
    for key in (CONF_OUTDOOR_SENSOR, CONF_COOL_MIN_OUTDOOR, CONF_HEAT_MAX_OUTDOOR):
        assert cleared["data"][key] is None
