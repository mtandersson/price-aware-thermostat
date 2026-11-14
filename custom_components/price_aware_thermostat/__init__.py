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
