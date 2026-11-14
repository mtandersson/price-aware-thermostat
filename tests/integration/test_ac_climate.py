"""Heat-pump/AC controller behaviour against a fake underlying climate (issue #23)."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM
from pytest_homeassistant.common import async_mock_service

from custom_components.price_aware_thermostat.const import (
    CONF_COOL_COMPENSATION,
    CONF_HEAT_COMPENSATION,
    CONF_MAX_DEVIATION,
    CONF_MAX_SENSOR_AGE,
    CONF_WINDOW_CLOSE_DELAY,
    CONF_WINDOW_OPEN_DELAY,
    CONF_WINDOW_SENSORS,
)
from custom_components.price_aware_thermostat.optimizer import PriceInterval
from tests.integration.ac_support import PUMP, ROOM, WRAPPER, ac_env, add_pump

WINDOW = "binary_sensor.window"


def window_data(open_delay: int = 120, close_delay: int = 60) -> dict[str, object]:
    return {
        CONF_WINDOW_SENSORS: [WINDOW],
        CONF_WINDOW_OPEN_DELAY: open_delay,
        CONF_WINDOW_CLOSE_DELAY: close_delay,
    }


def hourly_prices(first: float, rest: float) -> tuple[PriceInterval, ...]:
    start = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    return tuple(
        PriceInterval(
            start + timedelta(hours=i), start + timedelta(hours=i + 1), first if i == 0 else rest
        )
        for i in range(6)
    )


# --------------------------------------------------------------------------- basics
async def test_heating_starts_pump_with_mode_then_compensated_target(hass: HomeAssistant) -> None:
    async with ac_env(hass, data={CONF_HEAT_COMPENSATION: 1.0}) as env:
        assert env.calls() == []  # a fresh wrapper is off and the pump already is
        await env.set_mode("heat")
        assert env.calls() == [("mode", "heat"), ("temp", 23.0)]
        attrs = env.attrs
        assert attrs["observed_backend_mode"] == "heat"
        assert attrs["observed_native_target_c"] == 23.0
        assert attrs["proposed_native_target_c"] == 23.0
        assert attrs["compensation_c"] == 1.0
        assert attrs["backend_in_sync"] is True
        assert attrs["command_status"] == "in_sync"
        assert attrs["effective_target_c"] == 22.0  # room target unaffected by compensation
        assert env.state().attributes["temperature"] == 22.0  # UI shows the base target


async def test_cooling_starts_pump_with_separate_compensation(hass: HomeAssistant) -> None:
    async with ac_env(
        hass, room="25.0", data={CONF_HEAT_COMPENSATION: 3.0, CONF_COOL_COMPENSATION: -1.0}
    ) as env:
        await env.set_mode("cool")
        assert env.calls() == [("mode", "cool"), ("temp", 21.0)]
        assert env.attrs["compensation_c"] == -1.0


async def test_no_demand_inside_the_hysteresis_band_sends_nothing(hass: HomeAssistant) -> None:
    async with ac_env(hass, room="21.9") as env:
        await env.set_mode("heat")
        assert env.calls() == []
        assert env.attrs["intent_reason"] == "no_demand"
        await env.set_room("22.1")
        assert env.calls() == []


async def test_demand_ends_above_target_plus_tolerance(hass: HomeAssistant) -> None:
    async with ac_env(hass) as env:
        await env.set_mode("heat")
        assert env.calls() == [("mode", "heat")]
        await env.set_room("22.2")  # still inside the band: keep running
        assert env.calls() == [("mode", "heat")]
        await env.set_room("22.4")  # above target + 0.3
        assert env.calls() == [("mode", "heat"), ("mode", "off")]


async def test_native_target_is_rounded_and_clamped_with_bounded_command_rate(
    hass: HomeAssistant,
) -> None:
    async with ac_env(hass, data={CONF_HEAT_COMPENSATION: 5.0}) as env:
        await env.set_mode("heat")
        assert env.calls() == [("mode", "heat"), ("temp", 27.0)]
        await env.set_target(21.3)  # 21.3 + 5 = 26.3 -> nearest 0.5 step
        assert env.calls()[-1] == ("temp", 27.0)  # rate limited: no new command yet
        assert env.attrs["command_status"] == "rate_limited"
        await env.advance(60)
        assert env.calls()[-1] == ("temp", 26.5)
        await env.set_target(31.5)  # 36.5 -> clamped to the pump maximum 31
        await env.advance(60)
        assert env.calls()[-1] == ("temp", 31.0)
        assert env.attrs["backend_in_sync"] is True


async def test_event_burst_is_coalesced_into_no_extra_commands(hass: HomeAssistant) -> None:
    async with ac_env(hass) as env:
        await env.set_mode("heat")
        before = env.calls()
        for i in range(50):
            hass.states.async_set(ROOM, f"{19.0 + i / 1000:.3f}", {"unit_of_measurement": "°C"})
        await hass.async_block_till_done()
        assert env.calls() == before


# ------------------------------------------------------------ restart/changeover
async def test_restart_pause_blocks_start_and_manual_off_prevents_stale_start(
    hass: HomeAssistant,
) -> None:
    async with ac_env(hass, min_cycle=300) as env:
        await env.set_mode("heat")
        assert env.calls() == []
        assert env.attrs["command_status"] == "restart_pause"
        assert env.attrs["pause_reason"] == "restart_pause"
        await env.advance(299)
        assert env.calls() == []
        await env.set_mode("off")  # manual off while the start is still pending
        await env.advance(600)
        assert env.calls() == []
        assert env.attrs["pause_reason"] is None


async def test_restart_pause_ends_and_pump_starts(hass: HomeAssistant) -> None:
    async with ac_env(hass, min_cycle=300) as env:
        await env.set_mode("heat")
        await env.advance(300)
        assert env.calls() == [("mode", "heat")]


async def test_heat_to_cool_changeover_goes_through_off_and_pause(hass: HomeAssistant) -> None:
    async with ac_env(hass, min_cycle=300) as env:
        await env.set_mode("heat")
        await env.advance(300)
        assert env.calls() == [("mode", "heat")]
        await env.set_room("25.0")
        await env.set_mode("cool")  # heat demand is gone and cooling is wanted
        assert env.calls()[-1] == ("mode", "off")
        assert env.pump.hvac_mode == HVACMode.OFF
        await env.advance(299)
        assert env.calls()[-1] == ("mode", "off")  # restart pause after changeover
        assert env.attrs["pause_reason"] == "restart_pause"
        await env.advance(1)
        # The pump's setpoint already equals the request, so only the mode is sent.
        assert env.calls()[-1] == ("mode", "cool")
        assert env.calls() == [("mode", "heat"), ("mode", "off"), ("mode", "cool")]
        assert env.pump.hvac_mode == HVACMode.COOL


async def test_changing_wrapper_mode_does_not_reuse_stale_demand(hass: HomeAssistant) -> None:
    async with ac_env(hass, room="22.2") as env:
        await env.set_mode("heat")
        assert env.calls() == []
        await env.set_room("21.0")
        assert env.calls() == [("mode", "heat")]
        await env.set_room("22.2")  # inside the band: heating would continue
        await env.set_mode("cool")  # cooling must start from fresh hysteresis state
        assert env.attrs["intent_reason"] == "no_demand"
        assert env.pump.hvac_mode == HVACMode.OFF


# ------------------------------------------------------------------------ windows
async def test_window_pauses_after_stable_open_and_resumes_after_stable_closure(
    hass: HomeAssistant,
) -> None:
    async with ac_env(hass, data=window_data(), extra_states={WINDOW: "off"}) as env:
        await env.set_mode("heat")
        assert env.calls() == [("mode", "heat")]
        hass.states.async_set(WINDOW, "on")
        await hass.async_block_till_done()
        await env.advance(119)
        assert env.calls() == [("mode", "heat")]
        assert env.attrs["window_status"] == "open_pending"
        await env.advance(1)
        assert env.calls()[-1] == ("mode", "off")
        assert env.attrs["window_status"] == "open_paused"
        assert env.attrs["pause_reason"] == "window_open"
        hass.states.async_set(WINDOW, "off")
        await hass.async_block_till_done()
        await env.advance(59)
        assert env.calls()[-1] == ("mode", "off")
        await env.advance(1)
        assert env.calls()[-1] == ("mode", "heat")
        assert env.attrs["window_status"] == "closed"


async def test_brief_window_opening_does_not_interrupt_heating(hass: HomeAssistant) -> None:
    async with ac_env(hass, data=window_data(), extra_states={WINDOW: "off"}) as env:
        await env.set_mode("heat")
        hass.states.async_set(WINDOW, "on")
        await hass.async_block_till_done()
        await env.advance(60)
        hass.states.async_set(WINDOW, "off")
        await hass.async_block_till_done()
        await env.advance(600)
        assert env.calls() == [("mode", "heat")]


async def test_unavailable_window_contact_is_reported_and_never_pauses(
    hass: HomeAssistant,
) -> None:
    async with ac_env(hass, data=window_data(), extra_states={WINDOW: "unavailable"}) as env:
        await env.set_mode("heat")
        await env.advance(3600)
        assert env.calls() == [("mode", "heat")]
        assert env.attrs["window_status"] == "unknown"


# ----------------------------------------------------------- failures and sensors
async def test_failed_service_calls_are_retried_a_bounded_number_of_times(
    hass: HomeAssistant,
) -> None:
    async with ac_env(hass) as env:
        env.pump.fail = True
        await env.set_mode("heat")
        # Seven steps (840 s) stay inside the 900 s cooldown that follows attempt three.
        for _ in range(7):
            await env.advance(120)
        assert env.calls() == [("mode", "heat")] * 3
        assert env.attrs["command_status"] == "retries_exhausted"
        env.pump.fail = False
        await env.advance(900)  # cooldown ends; a fresh attempt is allowed
        assert env.pump.hvac_mode == HVACMode.HEAT


async def test_unacknowledged_commands_are_not_hammered(hass: HomeAssistant) -> None:
    async with ac_env(hass) as env:
        env.pump.acknowledge = False
        await env.set_mode("heat")
        assert env.attrs["command_status"] == "starting"  # the plan that was just sent
        await env.advance(30)
        assert env.attrs["command_status"] == "pending_acknowledgment"
        for _ in range(2):
            await env.advance(30)  # 90 s: still inside the 120 s retry interval
        assert env.calls() == [("mode", "heat")]
        await env.advance(30)  # 120 s: one bounded retry is allowed
        assert env.calls() == [("mode", "heat")] * 2
        assert env.attrs["backend_in_sync"] is False


async def test_unavailable_backend_receives_no_commands_until_it_returns(
    hass: HomeAssistant,
) -> None:
    async with ac_env(hass) as env:
        await env.set_mode("heat")
        env.pump._attr_available = False
        env.pump.async_write_ha_state()
        await hass.async_block_till_done()
        await env.set_mode("off")
        await env.advance(600)
        assert env.calls() == [("mode", "heat")]
        assert env.attrs["backend_available"] is False
        assert env.attrs["command_status"] == "backend_unavailable"
        env.pump._attr_available = True
        env.pump.async_write_ha_state()
        await hass.async_block_till_done()
        assert env.calls()[-1] == ("mode", "off")


@pytest.mark.parametrize("reading", ["unavailable", "unknown", "not-a-number"])
async def test_invalid_room_temperature_turns_a_running_pump_off(
    hass: HomeAssistant, reading: str
) -> None:
    async with ac_env(hass) as env:
        await env.set_mode("heat")
        await env.advance(121)  # past the startup grace period
        await env.set_room(reading)
        assert env.calls()[-1] == ("mode", "off")
        assert env.attrs["intent_reason"] == "room_temperature_unavailable"


async def test_stale_room_temperature_turns_a_running_pump_off(hass: HomeAssistant) -> None:
    async with ac_env(hass, options={CONF_MAX_SENSOR_AGE: 30}) as env:
        await env.set_mode("heat")
        await env.advance(121)  # past the startup grace period
        with patch(
            "custom_components.price_aware_thermostat.climate.is_temperature_sample_fresh",
            return_value=False,
        ):
            await env.set_room("19.1")
        assert env.calls()[-1] == ("mode", "off")
        assert env.attrs["temperature_sample_fresh"] is False


async def test_unsupported_backend_mode_sends_nothing(hass: HomeAssistant) -> None:
    async with ac_env(hass) as env:
        env.pump._attr_hvac_modes = [HVACMode.COOL, HVACMode.OFF]
        env.pump.async_write_ha_state()
        await hass.async_block_till_done()
        await env.set_mode("heat")
        assert env.calls() == []
        assert env.attrs["command_status"] == "unsupported_backend_mode"


# ------------------------------------------------------- lifecycle and ownership
async def test_unload_cancels_pending_work_and_stops_commands(hass: HomeAssistant) -> None:
    async with ac_env(hass, min_cycle=300) as env:
        await env.set_mode("heat")
        assert await hass.config_entries.async_unload(env.entry.entry_id)
        await hass.async_block_till_done()
        env.clock.now += 3600
        await env.tick()
        hass.states.async_set(ROOM, "10.0", {"unit_of_measurement": "°C"})
        await hass.async_block_till_done()
        assert env.calls() == []


async def test_restored_running_mode_keeps_a_running_pump_without_off_pulse(
    hass: HomeAssistant,
) -> None:
    async with ac_env(
        hass,
        room="22.1",  # inside the band: an already running pump keeps its demand
        pump_mode=HVACMode.HEAT,
        pump_target=22.0,
        restore=("heat", {"preset_mode": "price_aware"}),
    ) as env:
        assert env.state().state == "heat"
        assert env.calls() == []
        assert env.attrs["backend_in_sync"] is True


async def test_restored_mode_still_respects_the_restart_pause(hass: HomeAssistant) -> None:
    async with ac_env(
        hass, min_cycle=300, restore=("heat", {"preset_mode": "price_aware"})
    ) as env:
        assert env.state().state == "heat"
        assert env.calls() == []
        assert env.attrs["pause_reason"] == "restart_pause"
        await env.advance(300)
        assert env.calls() == [("mode", "heat")]


async def test_external_native_changes_are_reconciled_and_exposed(hass: HomeAssistant) -> None:
    async with ac_env(hass) as env:
        await env.set_mode("heat")
        env.pump.external_change(target=26.0)
        await hass.async_block_till_done()
        assert env.calls()[-1] == ("temp", 22.0)
        env.pump.external_change(mode=HVACMode.COOL)
        await hass.async_block_till_done()
        assert env.calls()[-1] == ("mode", "off")  # foreign mode: safe changeover via off
        # After the safe off, restarting heat keeps the minimum command spacing.
        assert env.attrs["command_status"] in {
            "turning_off", "changeover_off", "restart_pause", "rate_limited"
        }
        assert env.pump.hvac_mode == HVACMode.OFF


# ----------------------------------------------------------------- price behaviour
async def test_missing_prices_fall_back_to_ordinary_room_regulation(hass: HomeAssistant) -> None:
    async with ac_env(hass) as env:
        await env.set_mode("heat")
        assert env.attrs["optimization_reason"] == "price_unavailable"
        assert env.attrs["effective_target_c"] == 22.0
        assert env.pump.hvac_mode == HVACMode.HEAT  # not forced off by price loss


async def test_cheap_now_lowers_the_cooling_target_and_raises_the_heating_target(
    hass: HomeAssistant,
) -> None:
    prices = hourly_prices(0.0, 9.0)
    async with ac_env(hass, room="25.0", prices=prices, options={CONF_MAX_DEVIATION: 0.5}) as env:
        await env.set_mode("cool")
        assert env.attrs["optimization_reason"] == "precool"
        assert env.attrs["effective_target_c"] == 21.5
        assert env.attrs["base_target_c"] == 22.0
        assert env.calls()[-1] == ("temp", 21.5)
        await env.set_room("19.0")
        await env.set_mode("heat")
        assert env.attrs["optimization_reason"] == "preheat"
        assert env.attrs["effective_target_c"] == 22.5


async def test_price_adjustment_changes_demand_not_only_the_pump_setpoint(
    hass: HomeAssistant,
) -> None:
    # Room 22.1 is inside the hysteresis band of the base target (22.0), so with no
    # prices there is no heating demand. A cheap-now price raises the effective
    # heating target to 22.5, which creates demand at the same room temperature.
    async with ac_env(hass, room="22.1", prices=hourly_prices(0.0, 9.0),
                      options={CONF_MAX_DEVIATION: 0.5}) as env:
        await env.set_mode("heat")
        assert env.pump.hvac_mode == HVACMode.HEAT
    async with ac_env(hass, room="22.1", options={CONF_MAX_DEVIATION: 0.5}) as env:
        await env.set_mode("heat")
        assert env.pump.hvac_mode == HVACMode.OFF


# ------------------------------------------------------------------- shadow mode
@pytest.mark.parametrize(
    "scenario",
    ["heat_demand", "cool_demand", "off_with_running_pump", "bad_sensor", "window", "failing"],
)
async def test_shadow_mode_never_sends_actuator_commands(
    hass: HomeAssistant, scenario: str
) -> None:
    # Set the switch component up first, otherwise it replaces the mocked services
    # when the integration (which depends on it) is set up.
    assert await async_setup_component(hass, "switch", {})
    switch_calls = [async_mock_service(hass, "switch", s) for s in ("turn_on", "turn_off")]
    kwargs: dict[str, object] = {"shadow": True}
    if scenario == "cool_demand":
        kwargs["room"] = "26.0"
    if scenario == "off_with_running_pump":
        kwargs["pump_mode"] = HVACMode.HEAT
    if scenario == "window":
        kwargs.update(data=window_data(0, 0), extra_states={WINDOW: "off"})
    async with ac_env(hass, **kwargs) as env:  # type: ignore[arg-type]
        env.pump.fail = scenario == "failing"
        mode = "cool" if scenario == "cool_demand" else "heat"
        await env.set_mode(mode)
        if scenario == "bad_sensor":
            await env.set_room("unavailable")
        if scenario == "window":
            hass.states.async_set(WINDOW, "on")
            await hass.async_block_till_done()
            await env.advance(10)
        await env.set_mode("off")
        await env.advance(3600)
        assert env.calls() == []
        assert all(not group for group in switch_calls)
        assert env.attrs["shadow_mode"] is True


async def test_shadow_mode_exposes_the_proposal_beside_the_observed_backend(
    hass: HomeAssistant,
) -> None:
    async with ac_env(hass, shadow=True, pump_mode=HVACMode.COOL, pump_target=24.0) as env:
        await env.set_mode("heat")
        attrs = env.attrs
        assert attrs["proposed_backend_mode"] == "heat"
        assert attrs["proposed_native_target_c"] == 22.0
        assert attrs["observed_backend_mode"] == "cool"
        assert attrs["observed_native_target_c"] == 24.0
        assert attrs["backend_in_sync"] is False
        assert attrs["backend_reported_hvac_action"] is None
        assert env.calls() == []


async def test_reported_action_is_exposed_but_not_treated_as_compressor_runtime(
    hass: HomeAssistant,
) -> None:
    async with ac_env(hass, shadow=True) as env:
        await env.set_mode("heat")
        assert env.state().attributes.get("hvac_action") != "heating"
        env.pump._attr_hvac_action = "heating"
        env.pump.async_write_ha_state()
        await hass.async_block_till_done()
        assert env.attrs["backend_reported_hvac_action"] == "heating"


# ------------------------------------------------------------ multiple instances
async def test_two_wrappers_regulate_independent_pumps(hass: HomeAssistant) -> None:
    from pytest_homeassistant.common import MockConfigEntry

    from custom_components.price_aware_thermostat.const import DOMAIN
    from tests.integration.ac_support import ac_data

    async with ac_env(hass) as env:
        second = await add_pump(hass, HVACMode.OFF, 22.0, "Pump Two")
        hass.states.async_set("sensor.room_two", "26.0", {"unit_of_measurement": "°C"})
        entry = MockConfigEntry(
            domain=DOMAIN, version=2, title="Room Two", unique_id="climate.pump_two",
            data=ac_data(
                env.entry.data["nordpool_config_entry"],
                heater="climate.pump_two", temperature_sensor="sensor.room_two",
            ),
            options={"shadow_mode": False, "min_cycle_seconds": 0},
        )
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await env.set_mode("heat")
        await hass.services.async_call(
            "climate", "set_hvac_mode",
            {"entity_id": "climate.room_two", "hvac_mode": "cool"}, blocking=True,
        )
        await hass.async_block_till_done()
        assert env.calls() == [("mode", "heat")]
        assert second.calls == [("mode", "cool")]  # its target already equals the request
        assert hass.states.get(WRAPPER).attributes["backend_entity_id"] == PUMP
        assert hass.states.get("climate.room_two").attributes["backend_entity_id"] == (
            "climate.pump_two"
        )


# ---------------------------------------------------------- review follow-ups
async def test_startup_grace_keeps_a_running_pump_until_the_sensor_loads(
    hass: HomeAssistant,
) -> None:
    async with ac_env(
        hass, room=None, pump_mode=HVACMode.HEAT, restore=("heat", {"preset_mode": "price_aware"})
    ) as env:
        assert env.calls() == []
        assert env.attrs["command_status"] == "startup_grace"
        await env.advance(60)
        assert env.calls() == []  # still inside the grace period
        await env.set_room("22.1")  # the sensor loads, inside the band: demand is kept
        assert env.calls() == []
        assert env.attrs["backend_in_sync"] is True


async def test_missing_sensor_after_the_grace_period_fails_closed(hass: HomeAssistant) -> None:
    async with ac_env(
        hass, room=None, pump_mode=HVACMode.HEAT, restore=("heat", {"preset_mode": "price_aware"})
    ) as env:
        await env.advance(121)
        assert env.calls() == [("mode", "off")]


async def test_backend_loading_after_the_wrapper_keeps_a_running_pump(
    hass: HomeAssistant,
) -> None:
    async with ac_env(
        hass, room="22.1", pump_mode=HVACMode.HEAT, pump_available=False,
        restore=("heat", {"preset_mode": "price_aware"}),
    ) as env:
        assert env.calls() == []
        assert env.attrs["command_status"] == "backend_unavailable"
        env.pump._attr_available = True
        env.pump.async_write_ha_state()
        await hass.async_block_till_done()
        # The first evaluation that could see the backend still treats a running
        # pump as having demand, so it is not pulsed off.
        assert env.calls() == []
        assert env.attrs["backend_in_sync"] is True


async def test_unexpected_exception_from_the_backend_is_contained_and_bounded(
    hass: HomeAssistant,
) -> None:
    async with ac_env(hass) as env:
        env.pump.raise_value_error = True
        await env.set_mode("heat")  # must not raise
        assert env.attrs["command_status"] == "starting"
        for _ in range(7):
            await env.advance(120)
        assert env.calls() == [("mode", "heat")] * 3
        assert env.attrs["command_status"] == "retries_exhausted"
        env.pump.raise_value_error = False
        await env.advance(900)
        assert env.pump.hvac_mode == HVACMode.HEAT


async def test_wrapper_action_reflects_a_heating_backend_while_the_wrapper_is_off(
    hass: HomeAssistant,
) -> None:
    async with ac_env(hass, shadow=True, pump_mode=HVACMode.HEAT) as env:
        assert env.state().state == "off"
        assert env.state().attributes.get("hvac_action") != "off"
        env.pump._attr_hvac_action = "heating"
        env.pump.async_write_ha_state()
        await hass.async_block_till_done()
        assert env.state().attributes["hvac_action"] == "heating"
        assert env.attrs["backend_reported_hvac_action"] == "heating"
        assert env.calls() == []


async def test_wrapper_action_is_off_once_the_backend_is_really_off(hass: HomeAssistant) -> None:
    async with ac_env(hass, shadow=True) as env:
        assert env.state().attributes["hvac_action"] == "off"


async def test_imperial_home_assistant_units_are_converted(hass: HomeAssistant) -> None:
    hass.config.units = US_CUSTOMARY_SYSTEM
    # Home Assistant reports limits in the system unit and the frontend reads the
    # step in the system unit too, so a Fahrenheit system uses a Fahrenheit step.
    async with ac_env(hass, room="25.0", pump_step=1.0) as env:
        pump = hass.states.get(PUMP)
        assert pump is not None
        assert pump.attributes["min_temp"] == 50  # the backend reports Fahrenheit
        await env.set_mode("cool")
        assert env.calls()[0] == ("mode", "cool")
        # Base target 22 degC is about 71.6 F. The pump must be commanded about
        # 22 degC (nearest 1 F step: 72 F = 22.2 degC), not clamped to its minimum
        # by comparing 22 with the Fahrenheit limit of 50.
        assert env.pump.target_temperature == pytest.approx(22.0, abs=0.3)
        attrs = env.attrs
        assert attrs["proposed_native_target_c"] == pytest.approx(22.0, abs=0.3)
        assert attrs["observed_native_target_c"] == pytest.approx(22.0, abs=0.3)
        assert attrs["backend_in_sync"] is True


async def test_restored_cool_mode_starts_cooling(hass: HomeAssistant) -> None:
    async with ac_env(
        hass, room="25.0", restore=("cool", {"preset_mode": "price_aware"})
    ) as env:
        assert env.state().state == "cool"
        assert env.calls() == [("mode", "cool")]


async def test_unacknowledged_start_is_not_replayed_after_manual_off(
    hass: HomeAssistant,
) -> None:
    async with ac_env(hass) as env:
        env.pump.acknowledge = False
        await env.set_mode("heat")
        assert env.calls() == [("mode", "heat")]
        await env.set_mode("off")  # the start was never acknowledged and is now obsolete
        env.pump.acknowledge = True
        await env.advance(1200)
        assert env.calls() == [("mode", "heat")]
        assert env.pump.hvac_mode == HVACMode.OFF


async def test_options_reload_keeps_a_running_pump_running(hass: HomeAssistant) -> None:
    async with ac_env(hass) as env:
        await env.set_mode("heat")
        await env.set_room("22.1")  # inside the band: heating continues
        assert env.calls() == [("mode", "heat")]
        assert await hass.config_entries.async_reload(env.entry.entry_id)
        await hass.async_block_till_done()
        await env.advance(60)
        assert env.state().state == "heat"
        assert env.calls() == [("mode", "heat")]  # no off/on pulse across the reload
