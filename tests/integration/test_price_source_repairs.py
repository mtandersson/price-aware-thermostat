"""Actionable source repairs, per-entry isolation and safe local fallback."""

from datetime import datetime
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant.common import MockConfigEntry, async_mock_service

from custom_components.price_aware_thermostat.const import (
    CONF_AREA,
    CONF_NORDPOOL_ENTRY,
    DOMAIN,
)
from custom_components.price_aware_thermostat.coordinator import NordPoolPriceCoordinator


@pytest.mark.parametrize("case", ["deleted", "wrong_domain", "missing_area"])
async def test_invalid_source_repair_and_recovery(hass: HomeAssistant, case: str) -> None:
    source = MockConfigEntry(domain="other" if case == "wrong_domain" else "nordpool",
                             data={"areas": [] if case == "missing_area" else ["SE4"]})
    if case != "deleted":
        source.add_to_hass(hass)
    entry = MockConfigEntry(domain=DOMAIN,
                            data={CONF_NORDPOOL_ENTRY: source.entry_id, CONF_AREA: "SE4"})
    entry.add_to_hass(hass)
    coordinator = NordPoolPriceCoordinator(hass, entry)
    registry = ir.async_get(hass)
    issue_id = f"price_source_{entry.entry_id}"
    # Price service failure remains a base-target fallback, not a setup failure.
    with patch(f"custom_components.{DOMAIN}.coordinator.dt_util.now",
               return_value=datetime.fromisoformat("2026-11-14T10:00:00+01:00")):
        assert await coordinator._async_update_data() == ()
        issue = registry.async_get_issue(DOMAIN, issue_id)
        assert issue is not None
        assert issue.config_entry_id == entry.entry_id
        assert issue.translation_key == "invalid_price_source"
        assert issue.severity is ir.IssueSeverity.WARNING
        # Repeated checks do not reset the user's ignored choice.
        ir.async_ignore_issue(hass, DOMAIN, issue_id, True)
        await coordinator._async_update_data()
        assert registry.async_get_issue(DOMAIN, issue_id).dismissed_version is not None
        if case == "deleted":
            source.add_to_hass(hass)
        else:
            # Correct by choosing a real replacement source, as Reconfigure does.
            source = MockConfigEntry(domain="nordpool", data={"areas": ["SE4"]})
            source.add_to_hass(hass)
            coordinator._source_entry_id = source.entry_id
        # Source correction clears the repair even during the API retry cooldown.
        await coordinator._async_update_data()
        assert registry.async_get_issue(DOMAIN, issue_id) is None


async def test_transient_price_outage_has_no_repair(hass: HomeAssistant) -> None:
    source = MockConfigEntry(domain="nordpool", data={"areas": ["SE4"]})
    source.add_to_hass(hass)
    entry = MockConfigEntry(domain=DOMAIN,
                            data={CONF_NORDPOOL_ENTRY: source.entry_id, CONF_AREA: "SE4"})
    entry.add_to_hass(hass)
    coordinator = NordPoolPriceCoordinator(hass, entry)
    async_mock_service(hass, "nordpool", "get_prices_for_date", response={"SE4": []})
    assert await coordinator._async_update_data() == ()
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"price_source_{entry.entry_id}") is None


async def test_repairs_are_isolated_per_thermostat(hass: HomeAssistant) -> None:
    entries = [MockConfigEntry(domain=DOMAIN,
               data={CONF_NORDPOOL_ENTRY: "missing", CONF_AREA: "SE4"}) for _ in range(2)]
    coordinators = []
    for entry in entries:
        entry.add_to_hass(hass)
        coordinator = NordPoolPriceCoordinator(hass, entry)
        coordinator._check_price_source()
        coordinators.append(coordinator)
    registry = ir.async_get(hass)
    for entry in entries:
        assert registry.async_get_issue(DOMAIN, f"price_source_{entry.entry_id}") is not None
    source = MockConfigEntry(domain="nordpool", data={"areas": ["se4"]})
    source.add_to_hass(hass)
    coordinators[0]._source_entry_id = source.entry_id
    coordinators[0]._check_price_source()
    assert registry.async_get_issue(DOMAIN, f"price_source_{entries[0].entry_id}") is None
    assert registry.async_get_issue(DOMAIN, f"price_source_{entries[1].entry_id}") is not None


async def test_source_repair_survives_reload_and_clears_after_reconfigure(
    hass: HomeAssistant,
) -> None:
    """Exercise the repair with a real thermostat lifecycle and source handoff."""
    from custom_components.price_aware_thermostat.const import (
        CONF_HEATER,
        CONF_NAME,
        CONF_SENSOR,
        CONF_SHADOW,
        DEFAULTS,
    )

    entry = MockConfigEntry(domain=DOMAIN, version=2, title="Repair lifecycle", data={
        **DEFAULTS, CONF_NAME: "Repair lifecycle", CONF_NORDPOOL_ENTRY: "deleted-source",
        CONF_AREA: "SE4", CONF_SENSOR: "sensor.repair_room", CONF_HEATER: "switch.repair_heat",
    }, options={CONF_SHADOW: True})
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.repair_room", "5", {"unit_of_measurement": "°C"})
    hass.states.async_set("switch.repair_heat", "off")
    calls = [async_mock_service(hass, "switch", name) for name in ("turn_on", "turn_off")]
    issue_id = f"price_source_{entry.entry_id}"
    registry = ir.async_get(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert registry.async_get_issue(DOMAIN, issue_id) is not None
    ir.async_ignore_issue(hass, DOMAIN, issue_id, True)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert registry.async_get_issue(DOMAIN, issue_id).dismissed_version is not None
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert registry.async_get_issue(DOMAIN, issue_id).dismissed_version is not None
    source = MockConfigEntry(domain="nordpool", data={"areas": ["SE4"]})
    source.add_to_hass(hass)
    flow = await entry.start_reconfigure_flow(hass)
    await hass.config_entries.flow.async_configure(flow["flow_id"], {
        CONF_NORDPOOL_ENTRY: source.entry_id, CONF_AREA: "SE4",
    })
    await hass.async_block_till_done()
    assert registry.async_get_issue(DOMAIN, issue_id) is None
    assert not any(calls)
    assert await hass.config_entries.async_unload(entry.entry_id)
