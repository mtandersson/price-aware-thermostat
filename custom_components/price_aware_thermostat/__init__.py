"""Price-Aware Thermostat Home Assistant integration.

The import path is deliberately lightweight so the pure optimizer tests run
without importing Home Assistant.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .config_entry import PriceAwareConfigEntry


async def async_setup_entry(hass: HomeAssistant, entry: PriceAwareConfigEntry) -> bool:
    """Start a coordinator and add the configured climate entity."""
    from homeassistant.const import Platform

    from .coordinator import NordPoolPriceCoordinator

    coordinator = NordPoolPriceCoordinator(hass, entry)
    # A service outage must never prevent local temperature regulation.
    await coordinator.async_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, [Platform.CLIMATE, Platform.SENSOR])
    return True


async def async_unload_entry(hass: HomeAssistant, entry: PriceAwareConfigEntry) -> bool:
    """Unload the climate platform and its event subscriptions."""
    from homeassistant.const import Platform

    return await hass.config_entries.async_unload_platforms(entry, [Platform.CLIMATE, Platform.SENSOR])


async def async_migrate_entry(hass: HomeAssistant, entry: PriceAwareConfigEntry) -> bool:
    """Migrate entries forward without changing user choices or IDs.

    1 -> 2: add an explicit switch control type.
    2.1 -> 2.2: price adjustment becomes relative to the user's target. Entries
    without a deviation get the default; entries that relied on the old
    minimum/maximum defaults get those values pinned so limits never widen.
    2.2 -> 2.3: keep a customised climate friendly name now that the entity
    belongs to a named device (see ``_move_entity_name_to_device``).
    """
    from .const import (
        CONF_CONTROL_TYPE,
        CONF_MAX_DEVIATION,
        CONF_MAX_TEMP,
        CONF_MIN_TEMP,
        CONTROL_SWITCH,
        DEFAULTS,
        LEGACY_MAX_TEMP,
        LEGACY_MIN_TEMP,
    )

    if entry.version > 2:
        return False
    if entry.version == 1:
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_CONTROL_TYPE: CONTROL_SWITCH},
            version=2, minor_version=1,
        )
    if entry.minor_version < 2:
        merged = {**entry.data, **entry.options}
        options = dict(entry.options)
        if merged.get(CONF_MAX_DEVIATION) is None:
            options[CONF_MAX_DEVIATION] = DEFAULTS[CONF_MAX_DEVIATION]
        for key, legacy in ((CONF_MIN_TEMP, LEGACY_MIN_TEMP), (CONF_MAX_TEMP, LEGACY_MAX_TEMP)):
            if merged.get(key) is None:
                options[key] = legacy
        hass.config_entries.async_update_entry(entry, options=options, minor_version=2)
    if entry.minor_version < 3:
        _move_entity_name_to_device(hass, entry)
        hass.config_entries.async_update_entry(entry, minor_version=3)
    return True


def _move_entity_name_to_device(hass: HomeAssistant, entry: PriceAwareConfigEntry) -> None:
    """Move a custom climate registry name onto the device's user name.

    The climate entity has ``has_entity_name`` and no own name, so Home
    Assistant shows ``<device name> <registry name>`` once a registry name is
    set. Before the device existed the registry name was the whole friendly
    name. Moving it to the device's ``name_by_user`` keeps that friendly name
    (and gives the price sensor a matching prefix). The entity_id is never
    touched. A device name the user already chose is left alone, and so is the
    entity name in that case.
    """
    from homeassistant.const import Platform
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    from .const import DOMAIN

    ent_reg = er.async_get(hass)
    entity_id = ent_reg.async_get_entity_id(Platform.CLIMATE, DOMAIN, entry.entry_id)
    if entity_id is None:
        return
    entity = ent_reg.async_get(entity_id)
    if entity is None or not entity.name:
        return
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, entry.entry_id)}, name=entry.title,
    )
    if device.name_by_user:
        return
    dev_reg.async_update_device(device.id, name_by_user=entity.name)
    ent_reg.async_update_entity(entity_id, name=None, device_id=device.id)
