"""Shared fakes for heat-pump/AC integration tests.

The backend is a *real* ``ClimateEntity`` registered on the climate platform, so
the wrapper's service calls go through Home Assistant's own entity services.
Nothing here reaches physical hardware.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from unittest.mock import patch

from homeassistant.components.climate import (
    ATTR_TEMPERATURE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant.common import (
    MockConfigEntry,
    MockEntityPlatform,
    async_fire_time_changed,
    mock_restore_cache,
)

from custom_components.price_aware_thermostat.const import (
    CONF_AREA,
    CONF_BASE_TEMP,
    CONF_CONTROL_TYPE,
    CONF_HEATER,
    CONF_HVAC_MODES,
    CONF_MAX_TEMP,
    CONF_MIN_CYCLE,
    CONF_MIN_TEMP,
    CONF_NORDPOOL_ENTRY,
    CONF_SENSOR,
    CONF_SHADOW,
    CONTROL_CLIMATE,
    DOMAIN,
)

WRAPPER = "climate.room_ac"
PUMP = "climate.pump"
ROOM = "sensor.room"


class FakePump(ClimateEntity):
    """A heat pump that records commands and can acknowledge, ignore or fail them."""

    _attr_name = "Pump"
    _attr_should_poll = False
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.COOL, HVACMode.OFF]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_min_temp = 10.0
    _attr_max_temp = 31.0
    _attr_target_temperature_step = 0.5

    def __init__(self, mode: HVACMode, target: float, name: str = "Pump") -> None:
        self._attr_name = name
        self._attr_hvac_mode = mode
        self._attr_target_temperature = target
        self.calls: list[tuple[str, Any]] = []
        self.acknowledge = True
        self.fail = False

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        self.calls.append(("mode", str(hvac_mode)))
        if self.fail:
            raise HomeAssistantError("simulated backend failure")
        if self.acknowledge:
            self._attr_hvac_mode = hvac_mode
            self.async_write_ha_state()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        self.calls.append(("temp", kwargs[ATTR_TEMPERATURE]))
        if self.fail:
            raise HomeAssistantError("simulated backend failure")
        if self.acknowledge:
            self._attr_target_temperature = kwargs[ATTR_TEMPERATURE]
            self.async_write_ha_state()

    def external_change(self, mode: HVACMode | None = None, target: float | None = None) -> None:
        """Simulate a native/app/remote change not made by the wrapper."""
        if mode is not None:
            self._attr_hvac_mode = mode
        if target is not None:
            self._attr_target_temperature = target
        self.async_write_ha_state()


@dataclass
class Clock:
    """Controllable replacement for the wrapper's monotonic clock."""

    now: float = 10_000.0

    def __call__(self) -> float:
        return self.now


@dataclass
class AcEnv:
    hass: HomeAssistant
    entry: MockConfigEntry
    pump: FakePump
    clock: Clock
    _ticks: int = field(default=0)

    def state(self) -> Any:
        state = self.hass.states.get(WRAPPER)
        assert state is not None
        return state

    @property
    def attrs(self) -> dict[str, Any]:
        return dict(self.state().attributes)

    async def set_mode(self, mode: str) -> None:
        await self.hass.services.async_call(
            "climate", "set_hvac_mode", {"entity_id": WRAPPER, "hvac_mode": mode}, blocking=True
        )
        await self.hass.async_block_till_done()

    async def set_preset(self, preset: str) -> None:
        await self.hass.services.async_call(
            "climate", "set_preset_mode", {"entity_id": WRAPPER, "preset_mode": preset},
            blocking=True,
        )
        await self.hass.async_block_till_done()

    async def set_target(self, value: float) -> None:
        await self.hass.services.async_call(
            "climate", "set_temperature", {"entity_id": WRAPPER, "temperature": value},
            blocking=True,
        )
        await self.hass.async_block_till_done()

    async def set_room(self, value: str, unit: str = "°C") -> None:
        self.hass.states.async_set(ROOM, value, {"unit_of_measurement": unit})
        await self.hass.async_block_till_done()

    async def advance(self, seconds: float) -> None:
        """Advance the wrapper clock and run the periodic evaluation."""
        self.clock.now += seconds
        await self.tick()

    async def tick(self) -> None:
        async_fire_time_changed(self.hass, dt_util.utcnow() + timedelta(seconds=61))
        await self.hass.async_block_till_done()

    def calls(self) -> list[tuple[str, Any]]:
        return list(self.pump.calls)


async def add_pump(
    hass: HomeAssistant, mode: HVACMode, target: float, name: str = "Pump"
) -> FakePump:
    assert await async_setup_component(hass, "climate", {})
    pump = FakePump(mode, target, name)
    platform = MockEntityPlatform(hass, domain="climate", platform_name="fakepump")
    await platform.async_add_entities([pump])
    await hass.async_block_till_done()
    return pump


def ac_data(source: str, **overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        CONF_CONTROL_TYPE: CONTROL_CLIMATE,
        CONF_HEATER: PUMP,
        CONF_SENSOR: ROOM,
        CONF_HVAC_MODES: ["heat", "cool"],
        CONF_NORDPOOL_ENTRY: source,
        CONF_AREA: "SE4",
        CONF_MIN_TEMP: 18.0,
        CONF_BASE_TEMP: 22.0,
        CONF_MAX_TEMP: 32.0,
    }
    data.update(overrides)
    return data


@asynccontextmanager
async def ac_env(
    hass: HomeAssistant,
    *,
    shadow: bool = False,
    room: str | None = "19.0",
    pump_mode: HVACMode = HVACMode.OFF,
    pump_target: float = 22.0,
    min_cycle: int = 0,
    data: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
    prices: tuple[Any, ...] = (),
    extra_states: dict[str, str] | None = None,
    restore: tuple[str, dict[str, Any]] | None = None,
) -> AsyncIterator[AcEnv]:
    """Set up one AC wrapper around a fake pump and unload it afterwards."""
    clock = Clock()
    pump = await add_pump(hass, pump_mode, pump_target)
    if room is not None:
        hass.states.async_set(ROOM, room, {"unit_of_measurement": "°C"})
    for entity_id, state in (extra_states or {}).items():
        hass.states.async_set(entity_id, state)
    source = MockConfigEntry(domain="nordpool", data={"areas": ["SE4"]})
    source.add_to_hass(hass)
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        title="Room AC",
        unique_id=PUMP,
        data=ac_data(source.entry_id, **(data or {})),
        options={CONF_SHADOW: shadow, CONF_MIN_CYCLE: min_cycle, **(options or {})},
    )
    entry.add_to_hass(hass)
    if restore is not None:
        mock_restore_cache(hass, [State(WRAPPER, restore[0], restore[1])])
    with (
        patch("custom_components.price_aware_thermostat.climate._monotonic", clock),
        patch(
            "custom_components.price_aware_thermostat.coordinator."
            "NordPoolPriceCoordinator._async_update_data",
            return_value=prices,
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        yield AcEnv(hass, entry, pump, clock)
        if entry.state is ConfigEntryState.LOADED:
            await hass.config_entries.async_unload(entry.entry_id)
            await hass.async_block_till_done()
