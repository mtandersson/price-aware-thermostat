# Settings reference

This page explains every setting of the Price-Aware Thermostat as the code
implements it. Temperatures are in °C and prices in SEK/kWh. The ranges are the
limits that the setup and options forms accept.

Settings appear in three places:

- **Add integration** (config flow): all settings except shadow mode. New
  entries always start in shadow mode.
- **Configure** (options): all settings, including the Nord Pool source and
  market area, shadow mode and the safety acknowledgement. Name and control
  type are not here.
- **Reconfigure**: only the Nord Pool source and market area. This works in
  live mode too, because the price source never commands the device.

Settings marked *heat pump/AC only* are shown only for the heat pump/AC
control type and are ignored by the element/switch type.

## How price changes the target

On each update the optimizer:

1. Takes the price of the current interval (`P_now`).
2. Computes a weighted mean of the prices from the end of the current
   interval to **lookahead** minutes later (`P_future`), using the
   **filter type**. If less than 75 % of that window has prices, or the
   current price is missing, it uses the base temperature.
3. Computes `diff = P_future − P_now`. A positive value means that now is
   cheaper than the near future.
4. Removes the **deadband**: `excess = max(0, |diff| − deadband)`, with the
   sign of `diff`.
5. Computes the adjustment: `sensitivity × excess`. Heating adds it to the
   base temperature (warm up while it is cheap). Cooling subtracts it (cool
   down while it is cheap).
6. Limits the adjustment to ± the **maximum price deviation** (default
   0.8 °C) around the base temperature, which is the target you set. Then it
   clamps the result to the absolute **minimum/maximum temperature**.

The thermostat then regulates the room against this *effective target* with
the cold/hot tolerances. The climate card shows the base temperature as the
target. The price-adjusted target is in the `effective_target_c` attribute.

### Worked example (heating)

Base 6.0 °C, minimum 5.0 °C, maximum 25.0 °C, maximum price deviation
0.8 °C, deadband 0.20 SEK/kWh, sensitivity 2.0 °C per SEK/kWh.

| Situation | P_now | P_future | diff | excess | adjustment | target | reason |
|---|---|---|---|---|---|---|---|
| Cheap now | 0.50 | 0.80 | +0.30 | +0.10 | +0.2 °C | **6.2 °C** | preheat |
| Very cheap now | 0.50 | 1.20 | +0.70 | +0.50 | +1.0 °C | 7.0 → **6.8 °C** (capped at base + 0.8) | preheat |
| Small difference | 1.00 | 1.15 | +0.15 | 0 | 0 | **6.0 °C** | normal |
| Expensive now | 2.00 | 1.00 | −1.00 | −0.80 | −1.6 °C | 4.4 → **5.2 °C** (capped at base − 0.8) | expensive |

In the last two rows `requested_adjustment_c` shows the uncapped value
(+1.0 or −1.6) and `adjustment_clamped` is `true`. If you set the maximum
deviation to 2.0, the second row would give 7.0 °C and the last row
5.0 °C, because the absolute minimum then limits it.

With a heat pump in cooling mode the sign is reversed: base 24 °C and the
"very cheap now" prices give 24 − 1.0 = 23.0 → **23.2 °C** (precool, capped
at base − 0.8).

## Basic settings

### Name
Display name of the climate entity. Set up only. Example: `Garage heater`.

### Control type
Set up only. The value cannot be changed later.

- **Element / heating switch**: turns a `switch` on and off. Heating only.
- **Heat pump / AC climate**: commands an existing `climate` entity: mode
  (heat, cool, off) and setpoint. The room sensor configured here decides
  regulation, not the pump's own sensor.

See [control types](control-types.md).

### Temperature sensor
The room sensor that the thermostat regulates against. It must be a
temperature sensor with a numeric reading in °C, °F or K. Other units are
rejected. Readings are converted to °C. Example: `sensor.garage_temperature`.

### Controlled switch or climate (actuator)
The `switch` or `climate` entity that is controlled. It must match the control
type. Only one Price-Aware Thermostat may control each device, and an instance
cannot control another instance. You can change it in the options only under
the hand-off rules: shadow mode stays on, and for a switch the old switch must
be off.

### Nord Pool configuration and market area
The official Nord Pool integration that supplies prices, and the price area to
read, for example `SE3` or `SE4`. The area must be one of the areas that this
Nord Pool entry provides. Default area: `SE4`. Change both with
**Reconfigure** or in the options. Both work in live mode too. If prices
cannot be fetched, the thermostat uses the base target and shows a repair
issue.

### Base temperature
- Default: 6.0 °C. Range: −20 to 40 °C. It must be between the minimum and
  the maximum.
- Your normal target, the value that you set on the climate card. Price
  adjustment moves the effective target around it, by at most the maximum
  price deviation. It is used as is when prices are flat (within the
  deadband), unavailable or not covering enough of the lookahead, and in the
  fixed (non-price-aware) preset.
- Examples: 6.0 for a frost-protected garage, 21 for a living room.

### Minimum and maximum temperature
- Defaults for new entries: 5.0 °C and 25.0 °C. Range: −20 to 40 °C, with
  minimum ≤ base ≤ maximum. Entries that existed before this change keep
  their old limits (5.3 and 7.5 °C if they used the old defaults), so limits
  never widen on upgrade.
- Absolute safety limits. The effective target never goes outside them, and
  they are also the allowed setpoint range on the climate card. They do not
  set the size of the price adjustment; the maximum price deviation does.
- Example: a garage with minimum 5 °C can never be set or price-adjusted
  below 5 °C.

## Price optimisation

### Lookahead (minutes)
- Default: 180. Range: 15 to 1440, in steps of 15.
- How far into the future the current price is compared. A short lookahead
  reacts to hour-to-hour swings. A long one follows the daily shape.
- Example: 180 compares the current 15-minute or hourly price with the next
  3 hours. If fewer than 75 % of those minutes have published prices, for
  example late in the evening before the next day's prices exist, the base
  target is used.

### Filter type
- Default: `triangle`. Options: `triangle`, `rectangle`.
- How the future prices are averaged:
  - **Triangle**: the weight falls linearly from 1 just after the current
    interval to 0 at the end of the lookahead. Prices soon matter more than
    prices far ahead.
  - **Rectangle**: a plain time-weighted average. Every minute counts the
    same.
- Weighting is by duration, so mixed 15-minute and hourly data give the same
  result.
- Example: lookahead 180, next hour 1.00 SEK/kWh, the two hours after that
  2.00 SEK/kWh. Rectangle gives (1 + 2 + 2) / 3 = 1.67. Triangle gives about
  1.44 because the cheap first hour weighs more.

### Price deadband (SEK/kWh)
- Default: 0.20. Range: 0 to 20.
- Price differences up to this size are ignored. Only the part above it moves
  the target, so small fluctuations do not cause constant changes.
- Example: with 0.20, a difference of 0.15 gives no change, and 0.50 is
  treated as 0.30.

### Sensitivity (°C per SEK/kWh)
- Default: 2.0. Range: 0 to 20.
- How strongly the target reacts: °C of adjustment per SEK/kWh of price
  difference beyond the deadband. 0 turns price optimisation off.
- Example: 2.0 × 0.30 SEK/kWh = 0.6 °C above the base when heating.

### Maximum price deviation
- Default: 0.8 °C. Range: 0 to 20 °C. Required, for all control types.
- The largest price adjustment: the effective target stays within
  base ± this value, and the absolute minimum/maximum still apply.
  0 turns price adjustment off, so the thermostat always uses the base.
- Example: base 21, deviation 0.8 keeps the target between 20.2 and
  21.8 °C.
- **Change for existing switch entries:** pre-heating used to be limited only
  by the maximum. It is now capped at base + 0.8 °C. With base 6.0 and
  maximum 7.5, pre-heating now reaches at most 6.8 °C instead of 7.5 °C. To
  restore the wider range, set this field to 1.5 (7.5 − 6.0) in the options.
  Pre-heating still never goes above the maximum.

## Advanced

### Cold-side and warm-side tolerance (°C)
- Defaults: 0.3 and 0.3. Range: 0 to 5.
- Hysteresis around the effective target, so the device does not toggle at
  the target:
  - Heating starts when the room is **at or below** target − cold tolerance.
    It stops when the room reaches target + hot tolerance.
  - Cooling starts when the room is **at or above** target + hot tolerance.
    It stops when the room reaches target − cold tolerance.
- Example: target 6.0, both 0.3. The heater turns on at 5.7 °C and off at
  6.3 °C.

### Minimum cycle / restart pause (seconds)
- Default: 300. Range: 0 to 3600 for a switch, 60 to 3600 for a heat pump/AC.
- **Switch**: the relay must have stayed in its current state at least this
  long before it is switched in normal operation. Turning off because the
  sensor is missing, the thermostat is off, or a command was not
  acknowledged is never delayed.
- **Heat pump/AC**: the minimum pause after the pump is turned off before it
  may start again. This protects the compressor.
- Example: 300 means at most one normal relay change per 5 minutes.

### Maximum temperature sample age (minutes)
- Default: 60. Range: 5 to 360.
- A reading whose last report is older than this counts as unavailable. Then
  the switch is turned off, or the heat pump is set to off. Sensors that
  report the same value again count as fresh.
- Example: with 60, a sensor that stopped reporting 61 minutes ago stops
  heating.

### Heat/cool modes to control (heat pump/AC only)
- Default: `heat`. Options: `heat`, `cool`, or both.
- The HVAC modes that the thermostat offers and may command on the pump. The
  pump must support each selected mode and `off`. A mode that is not
  selected is never sent.
- Example: select only `heat` for a pump that must never cool.
- With both `heat` and `cool` selected, the thermostat also offers
  **heat/cool** (`heat_cool`, Auto in HomeKit): two setpoints on one card. See
  [heat/cool mode](control-types.md#heatcool-mode-two-setpoints).

### Minimum gap between heat and cool targets (°C) (heat pump/AC only)
- Default: 1.0. Range: 0.1 to 10.
- Used only in heat/cool mode. The cooling (high) target must be at least this
  much above the heating (low) target; a setting that would narrow it is
  rejected. Price adjustment never narrows the range below it either. Maximum
  minus minimum temperature must be at least the gap.
- Example: 1.0 accepts 22/23 °C, rejects 22/22.5 °C.

### Minimum time between heat/cool changeover (minutes) (heat pump/AC only)
- Default: 30. Range: 0 to 240.
- Used only in heat/cool mode. After heating last ran, cooling may start only
  this long afterwards, and the reverse. The room must also have left the
  active direction's band first, so the pump never flips straight from heating
  to cooling. The restart pause also applies as usual.
- Example: 30 means a sunny afternoon after a cold morning waits at least half
  an hour after heating stopped before cooling starts.

### Heating and cooling compensation (heat pump/AC only)
- Defaults: 0.0 °C. Range: −10 to 10, in steps of 0.5.
- An offset added to the room target before it is sent as the pump's
  setpoint. The pump measures temperature at its own indoor unit, which is
  often warmer or colder than the room sensor. The result is clamped to the
  pump's own limits.
- Examples: heating compensation +2 sends 23 °C for a 21 °C room target.
  Cooling compensation −1 sends 23 °C for a 24 °C target.

## Window

All settings in this group apply to the heat pump/AC type only.

### Window/door contacts
- Default: none.
- Binary sensors. When any contact has stayed open for the open delay, the
  pump is set to off. A missing or unavailable contact counts as unknown. It
  never starts a pause and never ends one.

### Open delay (seconds)
- Default: 120. Range: 0 to 3600, in steps of 15.
- How long a contact must stay open before the pump is paused. Short airing
  does not stop the pump.
- Example: 120 ignores a door opened for a minute.

### Close delay (seconds)
- Default: 60. Range: 0 to 3600, in steps of 15.
- How long all contacts must stay closed before operation resumes. Then the
  thermostat starts from a fresh hysteresis state.

## Outdoor lockouts

All settings in this group apply to the heat pump/AC type only and are optional;
an empty field disables that lockout. They change only what the thermostat
proposes: the wrapper keeps the HVAC mode you chose, and the locked direction is
proposed as off (through the usual restart pause and command rules). In shadow
mode nothing is written.

### Outdoor temperature sensor
- Default: none.
- A temperature sensor outdoors (°C, °F or K). A sensor that is missing,
  unavailable, not a number or older than the maximum temperature sample age is
  handled asymmetrically. Heating fails open: a sensor fault never blocks
  heating. Cooling fails closed: with a cooling limit set, cooling is locked
  until a valid reading has released it (at or above the limit plus the
  hysteresis), in every mode. This includes a fresh start before the first
  reading. `outdoor_cool_lock_cause` is `no_valid_reading` or `temperature`.
  `outdoor_sensor_status` shows `ok`, `unavailable`, `stale`, `invalid` or
  `not_configured`, and a warning is logged.
- The outdoor sensor uses the same maximum temperature sample age as the room
  sensor. An outdoor source that updates rarely will read as stale, which
  releases heating and blocks cooling; check `outdoor_sensor_status` and raise
  the sample age if needed.
- A heating lock restored after a restart is kept until the first valid outdoor
  reading or for at most 120 s, so a sensor that loads late does not release it.
- The limits are ignored when no outdoor sensor is selected.

### Cool only when outdoor is at or above (°C)
- Default: empty (disabled). Range: −40 to 40.
- Cooling is locked while the outdoor temperature is below this value.
- Example: 15 stops the pump cooling on a cool day.

### Heat only when outdoor is at or below (°C)
- Default: empty (disabled). Range: −40 to 40.
- Heating is locked while the outdoor temperature is above this value.
- Example: 15 stops the pump heating on a warm day.

### Hysteresis (°C)
- Default: 1.0. Range: 0 to 10.
- A lockout engages when the outdoor temperature crosses its limit and releases
  only once it is this far past the limit on the allowed side.
- Example: heating limit 15 and hysteresis 1.0: heating locks above 15 °C and
  unlocks at 14 °C or below. Cooling limit 15: locks below 15 °C, unlocks at
  16 °C or above.

## Safety

### Shadow mode
- Default: on. Every new entry starts in shadow mode.
- In shadow mode the thermostat calculates and shows decisions
  (`planned_heating`, effective target, diagnostics) but sends **no**
  commands to the device. Use it to check behaviour before you hand over
  control.
- In shadow mode the card's action (Heating, Cooling, Idle, Off) shows what
  the thermostat *would* do, not what the device is doing. The device's own
  report is the `backend_reported_hvac_action` attribute (heat pump/AC).

### Safety acknowledgement
- Asked once, when you save the options with shadow mode turned off for the
  first time. It is remembered, with the time of confirmation, and the
  checkbox is hidden while live. Turning shadow mode on again clears it, so
  the next switch to live control asks again.
- By checking it you confirm two things. First, no other automation,
  schedule or integration controls the device. Second, independent hardware
  protection, such as a thermal cutout or a hardwired safety thermostat,
  exists. The software fallbacks are not a substitute for it.
- See [staging validation](staging-validation.md) before live use.

## Electricity price sensor

Each entry has its own device with an **Electricity price** sensor
(`sensor.<name>_electricity_price`). It shows the price that the optimizer
uses, so you can check the source without opening diagnostics. It is
read-only and never commands the device.

- **State:** the price of the current interval in SEK/kWh, shown with three
  decimals. It is `unknown` when no price covers the current time.
- It is refreshed when new prices are fetched and every 5 minutes, so it
  follows 15-minute intervals. The state is written only when something
  changes.

| Attribute | Meaning |
|---|---|
| `price_source_entry_id` | ID of the Nord Pool config entry in use. |
| `price_source_title` | Title of that Nord Pool entry, or `null` if it no longer exists. |
| `market_area` | The configured price area, for example `SE4`. |
| `future_weighted_price_sek_per_kwh` | `P_future`: the weighted mean over the lookahead, using the configured filter. `null` when there are too few forecast prices. |
| `last_price_fetch` | UTC time of the last successful price fetch. |
| `today_prices_cached`, `tomorrow_prices_cached` | Whether today's and tomorrow's prices are in the cache. |
| `today_failure`, `tomorrow_failure` | Kind of the latest fetch failure for that day, or `null`. |

The difference `future_weighted_price_sek_per_kwh − state` is the `diff` in
[How price changes the target](#how-price-changes-the-target).
