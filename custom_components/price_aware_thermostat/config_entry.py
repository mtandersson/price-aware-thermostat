"""Typed configuration entry for the price-aware thermostat integration."""

from homeassistant.config_entries import ConfigEntry

from .coordinator import NordPoolPriceCoordinator

type PriceAwareConfigEntry = ConfigEntry[NordPoolPriceCoordinator]
