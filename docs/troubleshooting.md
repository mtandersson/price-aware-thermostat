# Troubleshooting and supported configuration

Price-Aware Thermostat is an experimental Home Assistant **custom integration**. It has not been validated as a standalone frost-protection or unattended heating safety system.

## Prerequisites

- A Home Assistant instance with the official **Nord Pool** integration configured for the selected market area.
- A supported `sensor` entity with `device_class: temperature` and a recognized unit (`°C`, `°F` or `K`).
- A `switch` entity representing a heating actuator. Physical relay control is **not supported without a separate equipment safety assessment**.
- One integration entry per heater switch. Two different climate integrations should not command the same switch.
- Default `shadow_mode: true`; this is a read-only simulation regarding the switch.

The integration does not create or own a physical device, and does not provide automatic device discovery, firmware updates or direct Nord Pool API requests. It consumes existing Home Assistant entities and an official Home Assistant action.

## Configuration reference

| Setting | Purpose |
| --- | --- |
| Temperature sensor | Current reading and unit; unknown, stale or invalid readings invalidate automatic heat demand |
| Heating switch | Selected actuator; **never controlled in shadow mode** |
| Nord Pool configuration | Official integration entry providing `nordpool.get_prices_for_date` |
| Market area | Must belong to the selected Nord Pool configuration, e.g. `SE4` |
| Minimum/base/maximum temperatures | Bounds and ordinary setpoint in Celsius; never substitute for physical safety limits |
| Lookahead (minutes) | Future price window beginning after the current price interval |
| Filter | Rectangle weights all covered minutes equally; triangle emphasizes nearer price intervals |
| Deadband (SEK/kWh) | Ignore small price differences |
| Sensitivity | Target shift in °C per SEK/kWh beyond the deadband |
| Cold/hot tolerance | Hysteresis around the effective target |
| Minimum cycle duration | Limits ordinary relay changes; explicit safety OFF may bypass this guard |
| Maximum sensor sample age | Freshness limit for temperature reports, default 60 minutes |
| Shadow mode | When enabled, **never** invokes `switch.turn_on` or `switch.turn_off` |

The normal thermostat UI temperature is the base target; `effective_target_c` is the optimized target.

## Diagnostics

The climate entity exposes `shadow_mode`, `temperature_sample_fresh`, `planned_heating`, `effective_target_c`, `optimization_reason`, current electricity price, weighted future price, price difference, forecast coverage, `requested_adjustment_c` (what pricing asked for before comfort limits) and `adjustment_clamped` (limits reduced or removed it). The integration's downloadable diagnostics omit private entity IDs, room names and temperature measurements.

## Common problems

### No temperature is shown

Verify the selected sensor is reporting a numeric value with a recognized temperature unit and is not `unknown`/`unavailable`. A stored historic reading is not fresh until the sensor reports within the configured maximum age. The controller treats invalid data as a reason **not** to demand heat.

### Price optimization shows `price_unavailable`

Confirm the official Nord Pool integration is present, the selected configuration entry and market area match, and the test environment's `nordpool.get_prices_for_date` action returns prices. Tomorrow's prices may be unavailable before publication or during an outage. The optimizer then falls back to its configured base setpoint.

### Shadow mode plans heating but the switch does not change

That is the intended behavior. Shadow mode computes a plan without sending any heater commands.

### The switch has not acknowledged a service command

A successful Home Assistant service response is not a physical relay acknowledgement. The controller uses observed switch state and rate-limits retry attempts. Check the hardware integration's device status before assuming the heater changed state.

### Home Assistant restarts, unloads or reloads the integration

Persistent base target and user modes are covered by automated tests. However, the physical switch and its own restore behavior depend on the actuator integration and hardware. Do not rely on software restart recovery as independent frost protection.

### Removal

In a staging Home Assistant instance, remove the entry from Settings → Devices & services, remove the integration folder if no longer needed, and restart. Confirm that no other automation or thermostat was modified.

## Data updates

The price coordinator checks for missing calendar-day prices every 15 minutes, caches days already fetched successfully, and retries unavailable prices conservatively. Climate decisions re-evaluate when the temperature/switch state changes, when prices refresh, or on the periodic evaluation schedule.

## Known limitations

- Prices are spot-energy prices, excluding taxes, distribution charges and tariff effects.
- Price shifting may increase consumption through additional heat loss; savings are **not proven**.
- Incorrect physical safe-temperature bounds cannot be corrected in software.
- The integration does not guarantee communication with or actual operation of a physical switch.
- No historical trend chart is shipped. For comparison, use the climate attributes in a staging dashboard with limited retention.
- The custom integration has no official Home Assistant quality tier and is not released through HACS.

See [staging validation](staging-validation.md) and the [GitHub issue tracker](https://github.com/mtandersson/price-aware-thermostat/issues) for evidence, open risks and future work.
