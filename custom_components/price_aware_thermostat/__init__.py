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
    await hass.config_entries.async_forward_entry_setups(entry, [Platform.CLIMATE])
    return True


async def async_unload_entry(hass: HomeAssistant, entry: PriceAwareConfigEntry) -> bool:
    """Unload the climate platform and its event subscriptions."""
    from homeassistant.const import Platform

    return await hass.config_entries.async_unload_platforms(entry, [Platform.CLIMATE])


async def async_migrate_entry(hass: HomeAssistant, entry: PriceAwareConfigEntry) -> bool:
    """Migrate entries forward without changing user choices or IDs.

    1 -> 2: add an explicit switch control type.
    2.1 -> 2.2: price adjustment becomes relative to the user's target. Entries
    without a deviation get the default; entries that relied on the old
    minimum/maximum defaults get those values pinned so limits never widen.
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
    return True
