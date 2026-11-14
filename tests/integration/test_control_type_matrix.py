"""Both control types through the same lifecycle and safety matrix (issue #24).

The storage-room switch type remains a regression requirement: it is run through
every scenario next to the heat-pump/AC climate type.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.setup import async_setup_component
from pytest_homeassistant.common import MockConfigEntry, async_mock_service

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
from tests.integration.ac_support import ROOM, WRAPPER, ac_env

SWITCH_WRAPPER = "climate.storage"


@dataclass
class Rig:
    """One thermostat of either type plus a way to see actuator calls."""

    hass: HomeAssistant
    kind: str
    entry: MockConfigEntry
    wrapper: str

    def actuator_calls(self) -> list[Any]:
        raise NotImplementedError

    async def mode(self, mode: str) -> None:
        await self.hass.services.async_call(
            "climate", "set_hvac_mode", {"entity_id": self.wrapper, "hvac_mode": mode},
            blocking=True,
        )
        await self.hass.async_block_till_done()

    async def room(self, value: str) -> None:
        self.hass.states.async_set(ROOM, value, {"unit_of_measurement": "°C"})
        await self.hass.async_block_till_done()

    def attrs(self) -> dict[str, Any]:
        state = self.hass.states.get(self.wrapper)
        assert state is not None
        return dict(state.attributes)


class SwitchRig(Rig):
    on: list[Any]
    off: list[Any]

    def actuator_calls(self) -> list[Any]:
        return [*self.on, *self.off]


class ClimateRig(Rig):
    env: Any

    def actuator_calls(self) -> list[Any]:
        return list(self.env.pump.calls)


@asynccontextmanager
async def switch_rig(
    hass: HomeAssistant, *, shadow: bool, running: bool = False, failing: bool = False
) -> AsyncIterator[SwitchRig]:
    hass.states.async_set(ROOM, "5.0", {"unit_of_measurement": "°C"})
    hass.states.async_set("switch.heater", "on" if running else "off")
    entry = MockConfigEntry(
        domain=DOMAIN, version=2, title="Storage",
        data={
            "control_type": "switch", CONF_SENSOR: ROOM, CONF_HEATER: "switch.heater",
            CONF_NORDPOOL_ENTRY: "fake", CONF_AREA: "SE4",
            CONF_MIN_TEMP: 4.0, CONF_BASE_TEMP: 6.0, CONF_MAX_TEMP: 8.0,
        },
        options={CONF_SHADOW: shadow, CONF_MIN_CYCLE: 0},
    )
    entry.add_to_hass(hass)
    rig = SwitchRig(hass, "switch", entry, SWITCH_WRAPPER)
    # Set the switch component up first: otherwise it replaces the mocked services
    # when the integration (which depends on it) is set up, and every "zero calls"
    # assertion would pass vacuously.
    assert await async_setup_component(hass, "switch", {})
    error = HomeAssistantError("simulated backend failure") if failing else None
    rig.on = async_mock_service(hass, "switch", "turn_on", raise_exception=error)
    rig.off = async_mock_service(hass, "switch", "turn_off", raise_exception=error)
    with patch(
        "custom_components.price_aware_thermostat.coordinator."
        "NordPoolPriceCoordinator._async_update_data",
        return_value=(),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        yield rig
        if entry.state is ConfigEntryState.LOADED:
            await hass.config_entries.async_unload(entry.entry_id)


@asynccontextmanager
async def climate_rig(
    hass: HomeAssistant, *, shadow: bool, running: bool = False, failing: bool = False
) -> AsyncIterator[ClimateRig]:
    pump_mode = HVACMode.HEAT if running else HVACMode.OFF
    async with ac_env(hass, shadow=shadow, room="19.0", pump_mode=pump_mode) as env:
        env.pump.fail = failing
        rig = ClimateRig(hass, "climate", env.entry, WRAPPER)
        rig.env = env
        yield rig


def rig_for(kind: str, hass: HomeAssistant, *, shadow: bool, **flags: bool) -> Any:
    factory = switch_rig if kind == "switch" else climate_rig
    return factory(hass, shadow=shadow, **flags)


COLD = {"switch": "5.0", "climate": "19.0"}  # demand for heat in either type
TYPES = ["switch", "climate"]


@pytest.mark.parametrize("kind", TYPES)
@pytest.mark.parametrize(
    "scenario",
    [
        "cold_heat", "off_mode", "sensor_unavailable", "sensor_invalid", "stale_sensor",
        "failing_backend", "off_while_device_running",
    ],
)
async def test_shadow_mode_makes_zero_actuator_calls_for_both_types(
    hass: HomeAssistant, kind: str, scenario: str
) -> None:
    flags = {
        "failing_backend": {"failing": True},
        "off_while_device_running": {"running": True},
    }.get(scenario, {})
    async with rig_for(kind, hass, shadow=True, **flags) as rig:
        await rig.room(COLD[kind])
        if scenario == "cold_heat":
            await rig.mode("heat")
        elif scenario in ("off_mode", "off_while_device_running"):
            await rig.mode("heat")
            await rig.mode("off")
        elif scenario == "sensor_unavailable":
            await rig.mode("heat")
            await rig.room("unavailable")
        elif scenario == "sensor_invalid":
            await rig.mode("heat")
            await rig.room("not-a-number")
        else:
            await rig.mode("heat")
            with patch(
                "custom_components.price_aware_thermostat.climate.is_temperature_sample_fresh",
                return_value=False,
            ):
                # A changed value triggers an evaluation while reports count as stale.
                await rig.room("5.1" if kind == "switch" else "19.1")
                assert rig.attrs()["temperature_sample_fresh"] is False
        assert rig.actuator_calls() == []
        assert rig.attrs()["shadow_mode"] is True


@pytest.mark.parametrize("kind", TYPES)
async def test_live_mode_commands_the_actuator_for_both_types(
    hass: HomeAssistant, kind: str
) -> None:
    async with rig_for(kind, hass, shadow=False) as rig:
        await rig.room(COLD[kind])
        await rig.mode("heat")
        assert rig.actuator_calls() != []


@pytest.mark.parametrize("kind", TYPES)
async def test_missing_prices_fall_back_to_the_base_target_for_both_types(
    hass: HomeAssistant, kind: str
) -> None:
    async with rig_for(kind, hass, shadow=True) as rig:
        await rig.mode("heat")
        attrs = rig.attrs()
        assert attrs["optimization_reason"] == "price_unavailable"
        assert attrs["effective_target_c"] == rig.hass.states.get(rig.wrapper).attributes[
            "temperature"
        ]


@pytest.mark.parametrize("kind", TYPES)
async def test_base_target_and_mode_survive_options_reload_for_both_types(
    hass: HomeAssistant, kind: str
) -> None:
    async with rig_for(kind, hass, shadow=True) as rig:
        await rig.mode("heat")
        target = 7.0 if kind == "switch" else 23.0
        await hass.services.async_call(
            "climate", "set_temperature", {"entity_id": rig.wrapper, "temperature": target},
            blocking=True,
        )
        await hass.async_block_till_done()
        assert rig.entry.options[CONF_BASE_TEMP] == target
        assert await hass.config_entries.async_reload(rig.entry.entry_id)
        await hass.async_block_till_done()
        assert rig.entry.state is ConfigEntryState.LOADED
        state = hass.states.get(rig.wrapper)
        assert state is not None
        assert state.attributes["temperature"] == target
        assert state.state == "heat"
        assert rig.actuator_calls() == []
        assert await hass.config_entries.async_unload(rig.entry.entry_id)
        await hass.async_block_till_done()
        assert hass.states.get(rig.wrapper).state == "unavailable"
        assert rig.actuator_calls() == []  # unloading sends no command


@pytest.mark.parametrize("kind", TYPES)
async def test_hvac_modes_match_the_control_type(hass: HomeAssistant, kind: str) -> None:
    async with rig_for(kind, hass, shadow=True) as rig:
        modes = rig.attrs()["hvac_modes"]
        assert modes == (["heat", "off"] if kind == "switch" else ["heat", "cool", "off"])


async def test_switch_and_climate_instances_run_side_by_side_without_interference(
    hass: HomeAssistant,
) -> None:
    async with climate_rig(hass, shadow=False) as climate:
        hass.states.async_set("sensor.storage_room", "5.0", {"unit_of_measurement": "°C"})
        hass.states.async_set("switch.heater", "off")
        on = async_mock_service(hass, "switch", "turn_on")
        entry = MockConfigEntry(
            domain=DOMAIN, version=2, title="Storage",
            data={
                "control_type": "switch", CONF_SENSOR: "sensor.storage_room",
                CONF_HEATER: "switch.heater", CONF_NORDPOOL_ENTRY: "fake", CONF_AREA: "SE4",
                CONF_MIN_TEMP: 4.0, CONF_BASE_TEMP: 6.0, CONF_MAX_TEMP: 8.0,
            },
            options={CONF_SHADOW: False, CONF_MIN_CYCLE: 0},
        )
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await hass.services.async_call(
            "climate", "set_hvac_mode",
            {"entity_id": SWITCH_WRAPPER, "hvac_mode": "heat"}, blocking=True,
        )
        await hass.async_block_till_done()
        assert on and on[-1].data["entity_id"] == "switch.heater"
        assert climate.actuator_calls() == []  # the AC pump was never touched
        await climate.mode("heat")
        assert ("mode", "heat") in climate.actuator_calls()
        assert all(call.data["entity_id"] == "switch.heater" for call in on)
        assert hass.states.get(SWITCH_WRAPPER).attributes.get("backend_entity_id") is None
        assert hass.states.get(WRAPPER).attributes["backend_entity_id"] == "climate.pump"
        assert HVACMode.HEAT == hass.states.get(WRAPPER).state
