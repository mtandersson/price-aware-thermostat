# Staging validation and commissioning gates

This is a **test procedure**, not an implementation backlog. The outstanding development tasks live in [GitHub issues](https://github.com/mtandersson/price-aware-thermostat/issues).

> **Never connect an experimental live controller to a frost-sensitive or unattended heater.** The first validation must be performed in a disposable Home Assistant environment using a fake switch and simulated temperature sensor. Keep all real thermostats and frost protection unchanged.

## 1. Prepare an isolated environment

- Use a separate Home Assistant test installation (or a restored disposable backup), with no network access to real relay devices.
- Install the integration from a reviewed commit, not an unreviewed branch.
- Ensure no physical relay, thermostat or safety automation is exposed to the test environment. A disconnected or synthetic switch must be used.
- Create a synthetic temperature entity with values and units you can change intentionally, and a synthetic switch with clear ON/OFF acknowledgement.
- Configure the **official Home Assistant Nord Pool integration**, including a market area and SEK currency, in this test environment. Do not use the legacy Nordpool/HACS integration as a substitute.
- Record the exact Home Assistant version, integration commit, market area, and source configuration-entry ID in **private test notes**, not in a public issue.

## 2. Verify the official Nord Pool action

From Developer tools → Actions in the isolated test Home Assistant instance, find `nordpool.get_prices_for_date`. Supply the selected Nord Pool configuration entry, today's date, your configured area (for example `SE4`) and currency `SEK`. Check today's response and, when the exchange publishes it, tomorrow's response.

The relevant action parameters are:

```yaml
config_entry: YOUR_NORDPOOL_CONFIG_ENTRY_ID
date: "2026-11-14"
areas:
  - SE4
currency: SEK
```

The date is illustrative: use the date relevant to the test environment. Confirm the action returns an object keyed by the market area with timezone-aware `start`, `end` and `price` values.

- Verify each time interval has a positive duration with no overlaps and that hours around midnight and European DST transitions are correctly ordered in UTC.
- Verify the returned prices are in SEK/MWh and that the integration converts them to SEK/kWh by dividing by 1,000.
- Confirm 15-minute and hourly intervals both work; do not infer interval length from list positions.
- Verify a market area not configured for the source entry is rejected in the UI.
- Record a **sanitized** example fixture if needed, excluding personal URLs, configuration-entry IDs and tokens.
- An unavailable or incomplete response must result in the base setpoint, not in a forced HVAC OFF state.

Do not attempt to call the action against the real home just to satisfy this document; use the staging environment.

## 3. Validate shadow operation

Install Price-Aware Thermostat with the test temperature sensor, **synthetic** switch and selected Nord Pool source. Verify `shadow_mode: true` before changing any temperature.

Repeat the following checks, observing the climate attributes and the synthetic switch event log:

| Test condition | Expected behavior |
| --- | --- |
| Low synthetic temperature | `planned_heating` may be true; **zero switch service calls** |
| High synthetic temperature | Plan changes, still zero switch service calls |
| Fixed temperature preset | `effective_target_c` matches the manual base target |
| HVAC OFF | `optimization_reason: off`, no commands in shadow mode |
| Price service temporarily unavailable | Base target remains active and reason indicates missing prices |
| Temperature entity `unavailable` | No valid heat demand; still no commands |
| Temperature update older than configured age | Mark sensor sample as not fresh |
| Restart or config-entry reload | Restore user-facing settings; no unexpected switch writes |
| Next-day prices unpublished | Retain already-known valid prices and retry conservatively |

An unexpected call to `switch.turn_on` or `switch.turn_off` in shadow mode is a **release blocker**.

## 4. Synthetic live-mode fault injection

Only after shadow checks pass, a deliberately isolated and **fake** switch can be used to test actuation:

1. Verify a valid cold synthetic reading produces at most one ON command until the switch acknowledges.
2. Do not send an acknowledgement: additional temperature events must not flood the switch.
3. Simulate a failed service action; retries should be bounded rather than immediate.
4. Simulate an explicit OFF or invalid sensor: the controller must issue or prioritize OFF even if an earlier ON is pending.
5. Simulate unloading/reloading and 100 rapid sensor events; no outstanding controller jobs should remain after unload.
6. Remove/unavailable-switch behavior must be observable and must not trigger unexpected ON commands.

Do not run these tests against real equipment. Keep tests deterministic and attach evidence to the relevant GitHub issues.

## 5. Physical commissioning blockers

**These are mandatory checks before considering any real relay control.** They are intentionally *not* approved by passing software CI.

- Independently verify heater electrical load, relay rating, physical thermostat/thermal cut-off, power-loss behavior and safe temperature range.
- Confirm which independent device provides frost protection if Home Assistant, its sensor or its network fails.
- Identify and disable competing software writers to the same relay only as part of an explicitly approved cutover.
- Take and verify a full HA backup. Document a tested restore and an immediate switch-ownership rollback path.
- Confirm the meaning of OFF, manual override, and stale/failed sensor readings in winter conditions.
- Observe read-only shadow results using the real temperature sensor only **after** the integration is proven not to actuate the real relay. A fake switch is preferred for this stage.
- Require explicit approval for a real cutover. No repository commit or CI result automatically authorizes it.

If any safety condition cannot be verified, **do not enable live actuation**.

## 6. Cleanup and rollback

For a staging installation, keep shadow mode enabled, unload or remove the integration under Settings → Devices & services, remove its `custom_components/price_aware_thermostat` directory if no longer needed, and restart the staging instance. Check that no test entities or schedules remain.

For a future authorized physical cutover, restore exclusive relay control to the previously tested thermostat using the documented backup/rollback procedure. Do **not** leave two independent climate controllers writing the same switch.

## Evidence to capture

Keep test-version/commit, time-window, observed sample age, diagnostic snapshots with identifiers removed, number of synthetic service calls and the pass/fail record. Report failures as GitHub issues with reproducible steps; never publish access tokens, local addresses, full backups or private entity IDs.
