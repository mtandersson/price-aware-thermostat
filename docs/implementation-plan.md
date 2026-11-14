# Implementation roadmap

The project specification is maintained in [private Notion](https://app.notion.com/p/41826b4f5990815a84d7f7ae648a85c8).

This is a staged implementation plan, not authorization to change the existing Home Assistant installation.

## Phase 1: Pure price optimizer

- [x] UTC-aware electricity-price interval model and SEK/MWh to SEK/kWh conversion
- [x] Rectangle and triangle time-weighted future-price comparisons
- [x] Configurable deadband, sensitivity, target-temperature clamps, and fallback
- [x] Unit tests for negative prices, forecast gaps, mixed interval lengths, and daylight saving time
- [ ] Verify the response schema of the official Nord Pool action against the target Home Assistant installation
- [ ] Evaluate historical electricity prices against a non-optimized baseline with a thermal model

## Phase 2: Home Assistant integration foundation

- [ ] Add `manifest.json`, config flow, options flow, and English translation strings
- [ ] Configure a Nord Pool integration entry, market area, and currency
- [ ] Fetch today's and tomorrow's price intervals through the documented HA action
- [ ] Cache data and recompute at appropriate price boundaries
- [ ] Expose useful diagnostics: current price, weighted future price, effective setpoint, and reason

## Phase 3: Climate thermostat controller

- [ ] Implement a `ClimateEntity` with one temperature sensor and one heating switch
- [ ] Support hysteresis, minimum on/off times, cooldown, and state restoration
- [ ] Support price-aware auto mode, fixed manual target, and explicit off
- [ ] Fail safely when temperature sensors or price data become unavailable
- [ ] Test concurrent events and ensure a single controller owns the physical switch

## Phase 4: Shadow mode and safe commissioning

- [ ] Provide shadow mode with **zero** physical relay writes
- [ ] Compare simulated targets and control decisions with the existing thermostat
- [ ] Verify heater safety features, relay load rating, temperature limits, and an independent physical safety layer
- [ ] Require a separate review before cutover; prepare a backup and a rollback plan

## Out of scope for the initial version

- Machine-learning-based room modelling
- Weather forecasts and network demand tariffs
- Claiming measured cost savings before a validated baseline is available
- Direct Nord Pool web scraping or duplicate physical relay control

## Unresolved decisions

- Confirm exact Home Assistant entity IDs, original template thermostat configuration, and Nord Pool configuration entry.
- Confirm the safe physical frost-protection temperature; the previously configured 5.3 °C is not automatically an approved hard minimum.
- Decide whether expensive prices may lower the normal setpoint or may only suppress preheating.
- Define the fail-safe heater policy for missing or stale temperature readings.
- Decide the manual override semantics and what survives Home Assistant restart.

## Safety gate

The prototype cannot control a physical heater. Do not disable or modify existing Home Assistant automation or climate entities based on this code.
