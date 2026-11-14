"""Price sensor showing which Nord Pool price the thermostat is using."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_utc_time_change
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .config_entry import PriceAwareConfigEntry
from .const import (
    CONF_AREA,
    CONF_DEADBAND,
    CONF_FILTER,
    CONF_LOOKAHEAD,
    CONF_NORDPOOL_ENTRY,
    CONF_SENSITIVITY,
    DEFAULTS,
    DOMAIN,
)
from .coordinator import NordPoolPriceCoordinator
from .optimizer import OptimizerConfig, optimize

PRICE_UNIT = "SEK/kWh"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PriceAwareConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add one price sensor per thermostat entry."""
    async_add_entities([PriceSensor(entry, entry.runtime_data)])


def device_info(entry: PriceAwareConfigEntry) -> DeviceInfo:
    """The thermostat device shared by the climate entity and the price sensor."""
    return DeviceInfo(identifiers={(DOMAIN, entry.entry_id)}, name=entry.title)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


class PriceSensor(CoordinatorEntity[NordPoolPriceCoordinator], SensorEntity):
    """Current price used by the optimizer, with its source and freshness.

    State writes are bounded: the value is re-evaluated on coordinator updates
    and every five minutes on the clock (price intervals are 15 or 60 minutes),
    and the state is only written when the value or attributes change.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "price"
    _attr_native_unit_of_measurement = PRICE_UNIT
    _attr_suggested_display_precision = 3
    _attr_icon = "mdi:cash-clock"

    def __init__(
        self, entry: PriceAwareConfigEntry, coordinator: NordPoolPriceCoordinator
    ) -> None:
        """Bind the sensor to the thermostat device."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_price"
        self._attr_device_info = device_info(entry)
        self._snapshot: tuple[Any, ...] | None = None

    async def async_added_to_hass(self) -> None:
        """Re-evaluate at price-interval boundaries."""
        await super().async_added_to_hass()
        self._refresh()
        self.async_on_remove(
            async_track_utc_time_change(self.hass, self._on_tick, minute="/5", second=0)
        )

    @callback
    def _on_tick(self, _now: datetime) -> None:
        self._write_if_changed()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._write_if_changed()

    @callback
    def _write_if_changed(self) -> None:
        if self._refresh():
            self.async_write_ha_state()

    def _refresh(self) -> bool:
        """Recompute value and attributes; return True when they changed."""
        settings = {**self._entry.data, **self._entry.options}
        current: float | None = None
        future: float | None = None
        try:
            filter_type = settings.get(CONF_FILTER, DEFAULTS[CONF_FILTER])
            config = OptimizerConfig(
                lookahead_minutes=int(settings.get(CONF_LOOKAHEAD, DEFAULTS[CONF_LOOKAHEAD])),
                filter_type="rectangle" if filter_type == "rectangle" else "triangle",
                deadband_sek_per_kwh=float(settings.get(CONF_DEADBAND, DEFAULTS[CONF_DEADBAND])),
                sensitivity_c_per_sek_per_kwh=float(
                    settings.get(CONF_SENSITIVITY, DEFAULTS[CONF_SENSITIVITY])
                ),
            )
            result = optimize(dt_util.utcnow(), self.coordinator.data or (), config)
            current = result.current_price_sek_per_kwh
            future = result.future_weighted_price_sek_per_kwh
        except (ValueError, TypeError):
            pass
        source_id = str(self._entry.data.get(CONF_NORDPOOL_ENTRY, ""))
        source = self.hass.config_entries.async_get_entry(source_id)
        coordinator = self.coordinator
        attributes: dict[str, Any] = {
            "price_source_entry_id": source_id,
            "price_source_title": source.title if source is not None else None,
            "market_area": self._entry.data.get(CONF_AREA),
            "future_weighted_price_sek_per_kwh": (
                round(future, 5) if future is not None else None
            ),
            "last_price_fetch": _iso(coordinator.last_success),
            "today_prices_cached": coordinator.today_cached,
            "tomorrow_prices_cached": coordinator.tomorrow_cached,
            "today_failure": coordinator.today_failure,
            "tomorrow_failure": coordinator.tomorrow_failure,
        }
        value = round(current, 5) if current is not None else None
        snapshot = (value, tuple(attributes.items()))
        if snapshot == self._snapshot:
            return False
        self._snapshot = snapshot
        self._attr_native_value = value
        self._attr_extra_state_attributes = attributes
        return True
