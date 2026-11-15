# Control types and backend contract

One integration supports two independently configured control types. Each entry
creates one virtual climate entity and selects one external temperature sensor.
The price optimizer is independent of the actuator backend.

| Control type | Actuator | Modes | Commands sent while live |
| --- | --- | --- | --- |
| Element / heating switch | Existing `switch` | Heat, off | `switch.turn_on` / `switch.turn_off`, guarded by a relay command guard |
| Heat pump / AC climate | Existing `climate` | The configured heat and/or cool modes, plus off | `climate.set_hvac_mode` and `climate.set_temperature` on the backend |

Both types start in **shadow mode**: decisions are calculated and exposed, but
no service is called. Live control requires turning shadow mode off in the
options and confirming safe ownership and independent hardware safeguards. The
two types never share state; the switch controller's behaviour is unchanged.

## Existing storage-room installations

Version 1 entries migrate to version 2 with `control_type: switch`. Existing
entry/entity IDs, setpoints, presets, HVAC choices and shadow options remain
unchanged. The heating algorithm, relay guard and temperature freshness policy
remain in use. The AC type does not replace the storage-room controller.

## Selecting a backend

Setup selects control type, external temperature sensor, controlled actuator,
Nord Pool source and temperature/price settings. The sensor must have an
available numeric temperature in Celsius, Fahrenheit or Kelvin. The actuator
must match the chosen control type. The shared `heater` configuration key
identifies either kind of actuator.

For AC entries, choose the heat and/or cool modes the wrapper may use. The
underlying climate must support those modes and off, support a target
temperature, and report valid minimum/maximum temperatures and a target step.
Configure room-appropriate base/minimum/maximum temperatures explicitly: the
setup defaults are storage-room examples, not AC comfort defaults.

An actuator cannot be owned by two entries of this integration, including shadow
entries. Integration-created climates cannot be selected as backends, which
prevents self-reference and chains. External writers cannot be detected in
general (see [Ownership](#ownership-and-reconciliation)).

## Heat pump / AC controller

### Three temperatures

1. **Base room target:** what you choose in the climate UI and what `temperature`
   shows. It persists across restarts.
2. **Effective room target:** the price-adjusted demand target (`effective_target_c`).
   Cheap electricity raises it for heating and lowers it for cooling; expensive
   electricity does the opposite. It is the base target ± at most the
   **maximum price adjustment** (default 0.8 °C, 0 disables price adjustment),
   then clamped to the minimum/maximum temperatures, which are absolute safety
   limits and the UI setpoint range. `requested_adjustment_c` and `adjustment_clamped` show what pricing
   asked for before limits. The fixed preset bypasses price adjustment; missing,
   invalid or insufficient price data falls back to the base target and does
   **not** switch the pump off.
3. **Native pump target:** the effective target plus a separate, signed
   **heating or cooling compensation**, clamped to the backend's limits and
   rounded to its step (measured from its minimum). Compensation never feeds back
   into the optimizer.

### Demand and commands

The external room sensor is the demand source. Heating starts at or below
`target - heat-on tolerance` and stops at or above `target + heat-off tolerance`;
cooling starts at or above `target + heat-off tolerance` and stops at or below
`target - heat-on tolerance`. While demand exists the pump keeps running in the
selected mode with the native target; temperature commands change the target
rather than toggling the pump on and off. When demand ends the wrapper sends
`off`.

Every evaluation recomputes the next single command from the *current* intent,
so a start that became obsolete while waiting (manual off, window opened,
changed price) is never replayed.

| Situation | Behaviour |
| --- | --- |
| Start | `set_hvac_mode` to the wanted mode, then `set_temperature` if the target differs. |
| Minimum restart pause | After the backend is observed off (and after a Home Assistant restart, counted from the first observation) a start waits for *Minimum cycle / pump restart pause*. |
| Heat ↔ cool changeover | Always goes through off: off, restart pause, then the other mode. A foreign mode (for example `auto`) is treated the same way. |
| Command spacing | Commands other than off, and other than a target sent right after its own start, are at least 60 s apart. A first off is never delayed; repeated offs are at least 60 s apart so another writer flipping the mode back cannot cause a command storm. The restart pause must be at least 60 s when configured; an entry stored earlier with a smaller value keeps it until its options are next saved. |
| Acknowledgment | Only the backend's observed mode/target acknowledges a command, with half a target step of tolerance for rounding. A successful service call alone is not an acknowledgment. |
| Retries | An unacknowledged or failed command is retried after 120 s, at most three times, then paused (`retries_exhausted`): 15 minutes for a start or target change, 5 minutes for a safety off. A different command, or reaching "in sync", starts a fresh count, so a pump that ignores commands combined with a rapidly flipping demand is limited by the 60 s spacing rather than the three-attempt bound. |
| Start without a target | A pump is never started unless a native target is known; if the backend reports no valid limits, `command_status` is `invalid_backend_limits`. |
| Wrapper set to off | The backend is commanded off. **Enabling live control while the wrapper is off therefore turns the pump off**; choose a mode first if the pump should keep running. |
| Room temperature unavailable, invalid or stale | Fail-closed: off. Independent protection in the device remains essential. For the first two minutes after the wrapper starts, a missing reading does not turn a running pump off (`command_status: startup_grace`), because the sensor often loads after the wrapper; this avoids a compressor off/on cycle on every restart. |
| Backend unavailable | No commands; `backend_available` is false and `command_status` is `backend_unavailable`. Normal operation resumes when it returns. |
| Backend lacks the wanted mode | No commands; `command_status` is `unsupported_backend_mode`. |
| Service error | Any exception from the backend is logged, throttled and bounded as above and does not stop the controller. |
| Restart | A restored heat/cool choice resumes after the restart pause. A pump already running in the restored mode keeps its demand, decided by the first evaluation that can see both the backend and the room sensor, rather than being pulsed off. |
| Options reload / unload | Pending work is cancelled; no command is sent after unload. |

### Windows and doors (optional)

Select binary sensors that report open as `on`. Operation pauses (off) after a
contact has been open continuously for the *open delay*, and is reconsidered only
after all contacts have been closed continuously for the *close delay*.
Reconsideration starts from a fresh hysteresis state. A missing, `unavailable` or
`unknown` contact is **unknown**: it never starts a pause and never ends one,
it restarts the open-time measurement (a contact flapping between open and
unavailable never reaches the open delay), and `window_status` reports
`unknown`. A window that is open while its contact is unavailable is therefore
not detected. Any open contact wins over unknown ones.

### Outdoor lockouts (optional)

With an outdoor temperature sensor, cooling can be allowed only at or above a
limit and heating only at or below another (see
[settings](settings.md#outdoor-lockouts)). Each lockout has its own state with
hysteresis (default 1.0 °C): it engages when the outdoor temperature crosses the
limit and releases once it is the hysteresis past the limit on the allowed side.
The state is kept between evaluations and restored after a restart.

While the wrapper's direction is locked, its HVAC mode stays as set, the
proposed backend mode is off, and `intent_reason` and `pause_reason` are
`outdoor_lockout`. Only the locked direction is affected: with heat and cool
both configured, a cooling lockout never blocks heating. A restart after a
lockout releases still waits for the restart pause.

A missing, unavailable, non-numeric or stale (older than the maximum sensor age)
outdoor reading **fails open**: nothing is locked, so a sensor fault never blocks
heating. Attributes: `outdoor_temperature_c`, `outdoor_lockout` (`none`, `heat`
or `cool`, for the current direction), `outdoor_heat_locked`,
`outdoor_cool_locked` and `outdoor_sensor_status`. The switch type ignores
these settings.

### Units

Home Assistant reports a climate's limits and target, and expects service
temperatures, in its system unit. The controller converts to and from Celsius
internally; the `*_c` attributes are Celsius. The backend's target step is read
as being in the system unit, as the Home Assistant frontend does. Home Assistant
also displays the backend's limits and target rounded to whole degrees
Fahrenheit (tenths of a degree Celsius), so the acknowledgment tolerance includes
half of that display unit, and on a Fahrenheit system targets stay half a degree
inside the reported limits (a displayed maximum can round past the real one and
be rejected on write). A backend whose step attribute is not in the system unit
may be commanded to a slightly different grid value than intended; the backend
rounds it itself and the tolerance still acknowledges it.

### Ownership and reconciliation

While live, this controller is authoritative for its backend. A native, app or
remote change is detected from the backend's state and reconciled on the next
evaluation, subject to the command rules above; `backend_in_sync` shows the
difference. Do not run another automation or thermostat that writes the same
backend: that creates two writers and a command fight that this integration
bounds but cannot resolve. It does not claim to detect every external writer.

The wrapper owns only the commands above. Fan speed, swing, presets, humidity,
auto/dry modes and other device controls stay with the backend and are not part
of this controller.

### Removal and shutdown send no command

Unloading the entry, removing it, reloading options or stopping Home Assistant
sends no command to the backend. It stays in its last commanded mode and target,
so check its actual state afterwards. See
[evaluation, compatibility and migration](ac-evaluation-and-migration.md) for the
evaluation procedure, dashboard example, exporter status and rollback steps.

### Reported mode is not compressor runtime

`backend_reported_hvac_action` is what the backend *says*. The minimum restart
pause protects software scheduling; neither it nor a reported mode proves that a
compressor is running or has rested. The wrapper's own `hvac_action` shows `off`
when the wrapper is off, and otherwise only what the backend reports.

### Shadow evaluation

In shadow mode the same decisions are computed and exposed without any climate or
switch call, including for off, sensor-failure, window and error paths:

| Attribute | Meaning |
| --- | --- |
| `proposed_backend_mode`, `proposed_native_target_c` | What the wrapper would command. |
| `observed_backend_mode`, `observed_native_target_c` | What the backend currently reports (for example under its existing controller). |
| `backend_in_sync`, `command_status` | Whether they agree and why a command is waiting. |
| `intent_reason`, `pause_reason`, `window_status` | Why the proposal is off, and window/restart/outdoor-lockout pauses. |
| `outdoor_temperature_c`, `outdoor_lockout`, `outdoor_sensor_status` | Outdoor reading, the active lockout (`none`/`heat`/`cool`) and sensor health. |
| `base_target_c`, `effective_target_c`, `compensation_c` | The three temperatures. |

Savings must not be inferred from a price-adjusted target alone. Real savings
depend on thermal losses and the heat pump's efficiency; any analysis belongs to
issue #7 and must state those assumptions.

## Reconfiguring the price source

The Nord Pool config entry and market area can be changed in the options
(**Configure**) together with the other settings, or on their own with the
**Reconfigure** action. Both validate the same way: the source must be a
`nordpool` entry that offers the chosen area. Both are allowed in live mode as
well, because a price source never commands the actuator; it only changes the
price signal, and a failing source falls back to the base target and raises a
repair as before. The earlier shadow-only restriction on Reconfigure (issue #3)
was therefore dropped. The source and area are stored in the entry data (not
options) so there is a single source of truth, entry/entity IDs are preserved
and the entry reloads. The options flow only validates the source when it is
changed, so an unchanged broken source (already shown as a repair) never blocks
other option changes such as enabling shadow mode.

Each entry has a device with the climate entity and an **Electricity price**
sensor (SEK/kWh): the current price used by the optimizer, with attributes
`price_source_entry_id`, `price_source_title`, `market_area`,
`future_weighted_price_sek_per_kwh`, `last_price_fetch`,
`today_prices_cached`, `tomorrow_prices_cached`, `today_failure` and
`tomorrow_failure`. It is re-evaluated on price updates and every five minutes
on the clock, and its state is only written when something changed.

## Options

Options can edit the price source and area, the sensor and actuator within the
same control type, the AC modes, thermostat settings including the maximum price
adjustment (both control types), and the AC-only settings (compensation, window
contacts and delays, outdoor lockouts). Changing control type requires a new entry. The maximum
price adjustment is required (0–20 °C; set 0 to disable price adjustment) and
cannot be left empty; leaving *Window contacts* empty clears them.
Retargeting retains the wrapper entry/entity identity and updates the actuator
unique ID used for duplicate detection. Options reload cancels the old worker.

Retargeting a heat-pump/AC entry requires shadow mode before and after, but does
not check the old pump's state: live-to-shadow sends no command, so check and set
the old backend yourself. Before retargeting, save shadow mode first; for a switch the old switch must be
observed off and the new target must stay in shadow mode. This prevents leaving a
live old heater on without its controller. No automatic handoff command is sent.
