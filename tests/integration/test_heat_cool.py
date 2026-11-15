"""Dual-setpoint heat_cool mode for the heat-pump/AC control type."""

import time
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.components.climate import ClimateEntityFeature, HVACMode
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from custom_components.price_aware_thermostat.backends import validate_ac_settings
from custom_components.price_aware_thermostat.const import (
    CONF_CHANGEOVER_MINUTES,
    CONF_COOL_MIN_OUTDOOR,
    CONF_HEAT_COOL_GAP,
    CONF_HEAT_MAX_OUTDOOR,
    CONF_HVAC_MODES,
    CONF_MAX_TEMP,
    CONF_MIN_TEMP,
    CONF_OUTDOOR_SENSOR,
    CONF_TARGET_HIGH,
    CONF_TARGET_LOW,
)
from tests.integration.ac_support import WRAPPER, AcEnv, ac_env
from tests.integration.test_ac_climate import WINDOW, hourly_prices, window_data

OUTDOOR = "sensor.outdoor"
RANGE = ClimateEntityFeature.TARGET_TEMPERATURE_RANGE


async def set_range(env: AcEnv, **data: Any) -> None:
    await env.hass.services.async_call(
        "climate", "set_temperature", {"entity_id": WRAPPER, **data}, blocking=True
    )
    await env.hass.async_block_till_done()


async def set_outdoor(hass: HomeAssistant, value: str) -> None:
    hass.states.async_set(OUTDOOR, value, {"unit_of_measurement": "°C"})
    await hass.async_block_till_done()


# ---------------------------------------------------------------- availability
async def test_heat_cool_is_offered_only_with_both_directions(hass: HomeAssistant) -> None:
    async with ac_env(hass) as env:
        attrs = env.attrs
        assert attrs["hvac_modes"] == ["heat", "cool", "heat_cool", "off"]
        assert attrs["supported_features"] & RANGE
        assert attrs["heat_cool_available"] is True
        # Not in heat_cool: no range on the card, single target as before.
        assert attrs["target_temp_low"] is None and attrs["temperature"] == 22.0
        await env.set_mode("heat_cool")
        attrs = env.attrs
        assert attrs["temperature"] is None
        # Defaults: low = base (22), high = base + 2 (24).
        assert (attrs["target_temp_low"], attrs["target_temp_high"]) == (22.0, 24.0)


@pytest.mark.parametrize("modes", [["heat"], ["cool"]])
async def test_single_direction_entries_are_unchanged(
    hass: HomeAssistant, modes: list[str]
) -> None:
    async with ac_env(hass, data={CONF_HVAC_MODES: modes}) as env:
        attrs = env.attrs
        assert attrs["hvac_modes"] == [*modes, "off"]
        assert not attrs["supported_features"] & RANGE
        assert attrs["heat_cool_available"] is False
        assert attrs["active_direction"] is None
        with pytest.raises(ServiceValidationError):
            await env.set_mode("heat_cool")
        await env.set_target(21.0)
        assert env.state().attributes["temperature"] == 21.0


def test_settings_validation() -> None:
    assert validate_ac_settings({CONF_HEAT_COOL_GAP: 0.0}) == "invalid_ac_settings"
    assert validate_ac_settings({CONF_CHANGEOVER_MINUTES: -1}) == "invalid_ac_settings"
    narrow = {CONF_HVAC_MODES: ["heat", "cool"], CONF_MIN_TEMP: 20, CONF_MAX_TEMP: 20.5}
    assert validate_ac_settings(narrow) == "invalid_ac_settings"
    assert validate_ac_settings({**narrow, CONF_HVAC_MODES: ["heat"]}) is None
    assert validate_ac_settings({CONF_HEAT_COOL_GAP: 2.0, CONF_CHANGEOVER_MINUTES: 0}) is None


# ---------------------------------------------------------------- range service
async def test_range_validation_clamping_and_persistence(hass: HomeAssistant) -> None:
    async with ac_env(hass, room="23.0") as env:
        await env.set_mode("heat_cool")
        await set_range(env, target_temp_low=20.5, target_temp_high=25.0)
        attrs = env.attrs
        assert (attrs["target_temp_low"], attrs["target_temp_high"]) == (20.5, 25.0)
        assert env.entry.options[CONF_TARGET_LOW] == 20.5
        assert env.entry.options[CONF_TARGET_HIGH] == 25.0
        with pytest.raises(ServiceValidationError) as narrow:
            await set_range(env, target_temp_low=24.5, target_temp_high=25.0)
        assert narrow.value.translation_key == "range_too_narrow"
        with pytest.raises(ServiceValidationError):
            await set_range(env, target_temp_low=17.0, target_temp_high=25.0)
        entity = hass.data["climate"].get_entity(WRAPPER)
        # One setpoint alone (a direct entity call) keeps the other.
        await entity.async_set_temperature(target_temp_high=26.5)
        assert env.attrs["target_temp_low"] == 20.5
        with pytest.raises(ServiceValidationError):
            await entity.async_set_temperature(target_temp_low=float("nan"))
        # A single temperature recentres the range, clamped to the limits.
        await set_range(env, temperature=23.0)
        assert (env.attrs["target_temp_low"], env.attrs["target_temp_high"]) == (20.0, 26.0)
        await set_range(env, temperature=31.5)
        assert (env.attrs["target_temp_low"], env.attrs["target_temp_high"]) == (26.0, 32.0)
        # hvac_mode in the same call is applied first.
        await set_range(env, hvac_mode="heat", temperature=21.0)
        assert env.state().state == "heat"
        assert env.attrs["temperature"] == 21.0
        await set_range(env, target_temp_low=21.0, target_temp_high=23.0, temperature=21.5)
        assert env.attrs["temperature"] == 21.5
        assert env.entry.options[CONF_TARGET_LOW] == 21.0


async def test_range_restores_after_restart(hass: HomeAssistant) -> None:
    async with ac_env(
        hass, room="23.0",
        options={CONF_TARGET_LOW: 21.0, CONF_TARGET_HIGH: 25.5},
        restore=("heat_cool", {}),
    ) as env:
        assert env.state().state == "heat_cool"
        attrs = env.attrs
        assert (attrs["target_temp_low"], attrs["target_temp_high"]) == (21.0, 25.5)


async def test_invalid_stored_range_falls_back_to_defaults(hass: HomeAssistant) -> None:
    async with ac_env(
        hass, room="23.0", data={CONF_MAX_TEMP: 22.5},
        options={CONF_TARGET_LOW: 22.0, CONF_TARGET_HIGH: 22.2},
        restore=("heat_cool", {}),
    ) as env:
        attrs = env.attrs
        # base 22 with max 22.5: high is clamped, then the gap pushes low down.
        assert (attrs["target_temp_low"], attrs["target_temp_high"]) == (21.5, 22.5)


# ---------------------------------------------------------------- direction
async def test_heats_below_low_idles_between_and_cools_after_changeover(
    hass: HomeAssistant,
) -> None:
    async with ac_env(hass, room="21.5") as env:
        await env.set_mode("heat_cool")
        assert env.calls() == [("mode", "heat")]  # the pump already targets 22
        assert env.attrs["active_direction"] == "heat"
        await env.set_room("23.0")
        assert env.calls()[-1] == ("mode", "off")
        assert env.attrs["active_direction"] == "idle"
        assert env.attrs["intent_reason"] == "no_demand"
        count = len(env.calls())
        await env.set_room("24.5")
        assert len(env.calls()) == count  # within the 30 min changeover
        assert env.attrs["pause_reason"] == "changeover_wait"
        assert env.attrs["heat_cool_status"] == "changeover_wait"
        await env.advance(30 * 60)
        assert env.calls()[count:] == [("mode", "cool"), ("temp", 24.0)]
        assert env.attrs["active_direction"] == "cool"
        assert env.attrs["effective_target_c"] == 24.0
        assert env.attrs["compensation_c"] == 0.0


async def test_restart_keeps_a_running_direction(hass: HomeAssistant) -> None:
    async with ac_env(
        hass, room="24.1", pump_mode=HVACMode.COOL, pump_target=24.0,
        restore=("heat_cool", {}),
    ) as env:
        assert env.calls() == []
        assert env.attrs["active_direction"] == "cool"


async def test_leaving_heat_cool_returns_to_single_mode(hass: HomeAssistant) -> None:
    async with ac_env(hass, room="21.0") as env:
        await env.set_mode("heat_cool")
        await env.set_mode("heat")
        attrs = env.attrs
        assert attrs["effective_target_low_c"] is None
        assert attrs["temperature"] == 22.0


# ---------------------------------------------------------------- guards
async def test_hot_room_with_cooling_locked_stays_idle(hass: HomeAssistant) -> None:
    hass.states.async_set(OUTDOOR, "10", {"unit_of_measurement": "°C"})
    data = {CONF_OUTDOOR_SENSOR: OUTDOOR, CONF_COOL_MIN_OUTDOOR: 15.0}
    async with ac_env(hass, room="27.0", data=data) as env:
        await env.set_mode("heat_cool")
        await env.advance(3600)
        assert env.calls() == []  # neither heats nor cools
        attrs = env.attrs
        assert attrs["pause_reason"] == "outdoor_lockout"
        assert attrs["outdoor_lockout"] == "cool"
        assert attrs["proposed_backend_mode"] == "off"
        await set_outdoor(hass, "16.0")
        assert env.calls() == [("mode", "cool"), ("temp", 24.0)]


async def test_cold_room_with_heating_locked_stays_idle(hass: HomeAssistant) -> None:
    hass.states.async_set(OUTDOOR, "20", {"unit_of_measurement": "°C"})
    data = {
        CONF_OUTDOOR_SENSOR: OUTDOOR, CONF_HEAT_MAX_OUTDOOR: 15.0, CONF_COOL_MIN_OUTDOOR: 25.0,
    }
    async with ac_env(hass, room="19.0", data=data) as env:
        await env.set_mode("heat_cool")
        assert env.calls() == []
        assert env.attrs["pause_reason"] == "outdoor_lockout"
        assert env.attrs["outdoor_lockout"] == "both"
        await set_outdoor(hass, "30.0")
        assert env.attrs["outdoor_lockout"] == "heat"
        assert env.calls() == []
        await set_outdoor(hass, "10.0")
        assert env.attrs["outdoor_lockout"] == "cool"
        assert env.calls() == [("mode", "heat")]


async def test_heating_stops_when_locked_while_running(hass: HomeAssistant) -> None:
    hass.states.async_set(OUTDOOR, "10", {"unit_of_measurement": "°C"})
    data = {CONF_OUTDOOR_SENSOR: OUTDOOR, CONF_HEAT_MAX_OUTDOOR: 15.0, CONF_COOL_MIN_OUTDOOR: 0.0}
    async with ac_env(hass, room="20.0", data=data) as env:
        await env.set_mode("heat_cool")
        await set_outdoor(hass, "40.0")
        assert env.calls()[-1] == ("mode", "off")
        assert env.attrs["outdoor_lockout"] == "heat"
        hass.states.async_set(OUTDOOR, "-5", {"unit_of_measurement": "°C"})
        await hass.async_block_till_done()
        assert env.attrs["outdoor_lockout"] == "cool"


async def test_window_pause_applies_in_heat_cool(hass: HomeAssistant) -> None:
    async with ac_env(hass, room="21.0", data=window_data(0, 0)) as env:
        await env.set_mode("heat_cool")
        assert env.calls()[0] == ("mode", "heat")
        hass.states.async_set(WINDOW, "on")
        await hass.async_block_till_done()
        await env.tick()
        assert env.calls()[-1] == ("mode", "off")
        assert env.attrs["pause_reason"] == "window_open"


async def test_room_sensor_fault_stops_heat_cool(hass: HomeAssistant) -> None:
    async with ac_env(hass, room="21.0") as env:
        await env.set_mode("heat_cool")
        await env.advance(600)
        await env.set_room("unavailable")
        assert env.calls()[-1] == ("mode", "off")
        assert env.attrs["intent_reason"] == "room_temperature_unavailable"


# ---------------------------------------------------------------- price
async def test_cheap_price_narrows_the_range_but_keeps_the_gap(hass: HomeAssistant) -> None:
    async with ac_env(hass, room="23.0", prices=hourly_prices(0.0, 9.0)) as env:
        await env.set_mode("heat_cool")
        attrs = env.attrs
        assert (attrs["effective_target_low_c"], attrs["effective_target_high_c"]) == (22.5, 23.5)
        assert attrs["adjustment_clamped"] is True
        assert attrs["effective_target_c"] is None
        await env.set_preset("fixed")
        attrs = env.attrs
        assert (attrs["effective_target_low_c"], attrs["effective_target_high_c"]) == (22.0, 24.0)


async def test_expensive_price_widens_the_range(hass: HomeAssistant) -> None:
    async with ac_env(hass, room="21.5", prices=hourly_prices(9.0, 0.0)) as env:
        await env.set_mode("heat_cool")
        attrs = env.attrs
        assert (attrs["effective_target_low_c"], attrs["effective_target_high_c"]) == (21.2, 24.8)
        assert env.calls() == []  # 21.5 is no longer below low - tolerance


async def test_cheap_price_preheats_with_the_adjusted_low(hass: HomeAssistant) -> None:
    async with ac_env(hass, room="22.0", prices=hourly_prices(0.0, 9.0)) as env:
        await env.set_mode("heat_cool")
        assert env.calls() == [("mode", "heat"), ("temp", 22.5)]


# ---------------------------------------------------------------- shadow
async def test_shadow_heat_cool_makes_no_writes(hass: HomeAssistant) -> None:
    async with ac_env(hass, shadow=True, room="20.0") as env:
        await env.set_mode("heat_cool")
        await env.advance(3600)
        assert env.state().attributes["hvac_action"] == "heating"
        await set_range(env, target_temp_low=19.0, target_temp_high=21.0)
        await env.set_room("23.0")
        await env.advance(3600)
        assert env.state().attributes["hvac_action"] == "cooling"
        assert env.calls() == []


# ---------------------------------------------------------------- cooling fails closed
COOL_LIMIT = {CONF_OUTDOOR_SENSOR: OUTDOOR, CONF_COOL_MIN_OUTDOOR: 15.0}


def _fault(hass: HomeAssistant, fault: str) -> None:
    if fault == "unavailable":
        hass.states.async_set(OUTDOOR, "unavailable")
    elif fault == "invalid":
        hass.states.async_set(OUTDOOR, "warm", {"unit_of_measurement": "°C"})
    else:
        hass.states.async_set(
            OUTDOOR, "30", {"unit_of_measurement": "°C"}, timestamp=time.time() - 7200
        )


@pytest.mark.parametrize("fault", ["unavailable", "stale", "invalid"])
@pytest.mark.parametrize("mode", ["heat_cool", "cool"])
async def test_outdoor_sensor_fault_never_allows_cooling(
    hass: HomeAssistant, fault: str, mode: str
) -> None:
    _fault(hass, fault)
    async with ac_env(hass, room="28.0", data=COOL_LIMIT) as env:
        await env.set_mode(mode)
        await env.advance(3600)
        assert env.calls() == []
        attrs = env.attrs
        assert attrs["outdoor_cool_locked"] is True
        assert attrs["outdoor_cool_lock_cause"] == "no_valid_reading"
        assert attrs["outdoor_sensor_status"] == fault
        assert attrs["pause_reason"] == "outdoor_lockout"
        await set_outdoor(hass, "15.5")  # valid, but inside the hysteresis band
        assert env.calls() == []
        await set_outdoor(hass, "16.0")
        assert env.calls()[0] == ("mode", "cool")
        assert env.attrs["outdoor_cool_lock_cause"] is None


async def test_fresh_start_without_reading_does_not_cool(hass: HomeAssistant) -> None:
    async with ac_env(hass, room="28.0", data=COOL_LIMIT) as env:
        await env.set_mode("heat_cool")
        assert env.calls() == []
        assert env.attrs["outdoor_sensor_status"] == "unavailable"


async def test_resume_with_running_cool_and_sensor_fault_turns_off(
    hass: HomeAssistant,
) -> None:
    hass.states.async_set(OUTDOOR, "unavailable")
    async with ac_env(
        hass, room="25.0", pump_mode=HVACMode.COOL, pump_target=24.0, data=COOL_LIMIT,
        restore=("heat_cool", {"outdoor_cool_locked": False}),
    ) as env:
        assert env.calls() == [("mode", "off")]
        assert env.attrs["active_direction"] == "idle"


async def test_heating_is_allowed_during_a_sensor_fault(hass: HomeAssistant) -> None:
    hass.states.async_set(OUTDOOR, "unavailable")
    data = {**COOL_LIMIT, CONF_HEAT_MAX_OUTDOOR: 15.0}
    async with ac_env(hass, room="20.0", data=data) as env:
        await env.set_mode("heat_cool")
        assert env.calls()[0] == ("mode", "heat")
        assert env.attrs["outdoor_heat_locked"] is False


# ---------------------------------------------------------------- mode switch
async def test_switching_from_running_heat_keeps_the_changeover(hass: HomeAssistant) -> None:
    async with ac_env(hass, room="21.0") as env:
        await env.set_mode("heat")
        assert env.calls()[0] == ("mode", "heat")
        await env.set_mode("heat_cool")
        assert env.attrs["active_direction"] == "heat"
        await env.set_room("25.0")
        assert env.calls()[-1] == ("mode", "off")
        assert env.attrs["pause_reason"] == "changeover_wait"
        await env.advance(30 * 60)
        assert env.calls()[-2:] == [("mode", "cool"), ("temp", 24.0)]


async def test_recent_single_mode_cooling_counts_for_the_changeover(
    hass: HomeAssistant,
) -> None:
    async with ac_env(hass, room="25.0") as env:
        await env.set_mode("cool")
        await env.set_room("23.0")  # cooling ends in single cool mode
        await env.set_mode("heat_cool")
        await env.set_room("21.0")
        assert env.attrs["pause_reason"] == "changeover_wait"


# ---------------------------------------------------------------- rejection
async def test_rejected_range_republishes_the_stored_values(hass: HomeAssistant) -> None:
    async with ac_env(hass, room="23.0") as env:
        await env.set_mode("heat_cool")
        entity = hass.data["climate"].get_entity(WRAPPER)
        with patch.object(entity, "async_write_ha_state") as write:
            with pytest.raises(ServiceValidationError):
                await entity.async_set_temperature(target_temp_low=23.5, target_temp_high=24.0)
            write.assert_called()
        with patch.object(entity, "async_write_ha_state") as write:
            with pytest.raises(ServiceValidationError):
                await entity.async_set_temperature(target_temp_low=10.0, target_temp_high=24.0)
            write.assert_called()
        assert (env.attrs["target_temp_low"], env.attrs["target_temp_high"]) == (22.0, 24.0)
