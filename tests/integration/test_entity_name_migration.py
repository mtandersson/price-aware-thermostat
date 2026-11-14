"""Regression tests for issue #46: friendly names survive the device naming."""

from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant.common import MockConfigEntry

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

ENTITY_ID = "climate.storage_heater"
TITLE = "Förråd shadow"
CUSTOM = "Element Förrådet"
UPDATE = "custom_components.price_aware_thermostat.coordinator.NordPoolPriceCoordinator._async_update_data"


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, version=2, minor_version=2, title=TITLE,
        data={
            CONF_SENSOR: "sensor.st_temperature", CONF_HEATER: "switch.st_heater",
            CONF_NORDPOOL_ENTRY: "fake-source", CONF_AREA: "SE4",
            CONF_MIN_TEMP: 5.0, CONF_BASE_TEMP: 6.0, CONF_MAX_TEMP: 7.5,
        },
        options={CONF_SHADOW: True},
    )
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.st_temperature", "6.0", {"unit_of_measurement": "°C"})
    hass.states.async_set("switch.st_heater", "off")
    return entry


def _register(
    hass: HomeAssistant, entry: MockConfigEntry, name: str | None,
    device_name_by_user: str | None = None, with_device: bool = False,
) -> None:
    """Recreate the registry state an earlier version left behind."""
    device_id = None
    if with_device:
        dev_reg = dr.async_get(hass)
        device = dev_reg.async_get_or_create(
            config_entry_id=entry.entry_id, identifiers={(DOMAIN, entry.entry_id)}, name=TITLE,
        )
        if device_name_by_user is not None:
            dev_reg.async_update_device(device.id, name_by_user=device_name_by_user)
        device_id = device.id
    ent_reg = er.async_get(hass)
    ent_reg.async_get_or_create(
        "climate", DOMAIN, entry.entry_id, config_entry=entry,
        suggested_object_id="storage_heater", device_id=device_id,
    )
    ent_reg.async_update_entity(ENTITY_ID, name=name)


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    with patch(UPDATE, return_value=()):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


def _names(hass: HomeAssistant, entry: MockConfigEntry) -> tuple[str | None, str | None, str]:
    entity = er.async_get(hass).async_get(ENTITY_ID)
    assert entity is not None
    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, entry.entry_id), entry.entry_id)
    assert device is not None
    assert entity.device_id == device.id
    state = hass.states.get(ENTITY_ID)
    assert state is not None
    return entity.name, device.name_by_user, str(state.attributes["friendly_name"])


def _price_sensor_name(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    sensor_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_price")
    assert sensor_id is not None
    state = hass.states.get(sensor_id)
    assert state is not None
    return str(state.attributes["friendly_name"])


async def test_custom_registry_name_moves_to_device(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    _register(hass, entry, CUSTOM)
    await _setup(hass, entry)
    assert _names(hass, entry) == (None, CUSTOM, CUSTOM)
    assert _price_sensor_name(hass, entry).startswith(f"{CUSTOM} ")
    assert er.async_get(hass).async_get_entity_id("climate", DOMAIN, entry.entry_id) == ENTITY_ID
    assert entry.minor_version == 3


async def test_custom_name_with_device_from_previous_version(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    _register(hass, entry, CUSTOM, with_device=True)
    await _setup(hass, entry)
    assert _names(hass, entry) == (None, CUSTOM, CUSTOM)


async def test_hand_fixed_state_is_unchanged(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    _register(hass, entry, None, device_name_by_user=CUSTOM, with_device=True)
    await _setup(hass, entry)
    assert _names(hass, entry) == (None, CUSTOM, CUSTOM)


async def test_no_custom_name_means_no_change(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    _register(hass, entry, None)
    await _setup(hass, entry)
    assert _names(hass, entry) == (None, None, TITLE)
    assert entry.minor_version == 3


async def test_entry_without_registered_entity_migrates(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    await _setup(hass, entry)
    assert entry.minor_version == 3


async def test_user_device_name_keeps_entity_name(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    _register(hass, entry, "Radiator", device_name_by_user="Storage", with_device=True)
    await _setup(hass, entry)
    assert _names(hass, entry) == ("Radiator", "Storage", "Storage Radiator")


async def test_migration_is_idempotent_across_reload(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    _register(hass, entry, CUSTOM)
    await _setup(hass, entry)
    with patch(UPDATE, return_value=()):
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    assert _names(hass, entry) == (None, CUSTOM, CUSTOM)
    # A name set after the migration is the user's choice and is never moved.
    er.async_get(hass).async_update_entity(ENTITY_ID, name="Later")
    with patch(UPDATE, return_value=()):
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    assert _names(hass, entry) == ("Later", CUSTOM, f"{CUSTOM} Later")
    assert entry.minor_version == 3
