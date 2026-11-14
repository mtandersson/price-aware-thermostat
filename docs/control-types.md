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
   electricity does the opposite. It stays within the minimum/maximum
   temperatures and, if set, the **maximum price adjustment** around the base
   target. `requested_adjustment_c` and `adjustment_clamped` show what pricing
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
| Command spacing | Commands other than off, and other than a target completing its own start, are at least 60 s apart. Off is never delayed. |
| Acknowledgment | Only the backend's observed mode/target acknowledges a command, with half a target step of tolerance for rounding. A successful service call alone is not an acknowledgment. |
| Retries | An unacknowledged or failed command is retried after 120 s, at most three times, then paused for 15 minutes (`retries_exhausted`). |
| Wrapper set to off | The backend is commanded off. **Enabling live control while the wrapper is off therefore turns the pump off**; choose a mode first if the pump should keep running. |
| Room temperature unavailable, invalid or stale | Fail-closed: off. Independent protection in the device remains essential. |
| Backend unavailable | No commands; `backend_available` is false and `command_status` is `backend_unavailable`. Normal operation resumes when it returns. |
| Backend lacks the wanted mode | No commands; `command_status` is `unsupported_backend_mode`. |
| Service error | Logged, throttled and bounded as above. |
| Restart | A restored heat/cool choice resumes after the restart pause. A pump already running in the restored mode keeps its demand rather than being pulsed off. |
| Options reload / unload | Pending work is cancelled; no command is sent after unload. |

### Windows and doors (optional)

Select binary sensors that report open as `on`. Operation pauses (off) after a
contact has been open continuously for the *open delay*, and is reconsidered only
after all contacts have been closed continuously for the *close delay*.
Reconsideration starts from a fresh hysteresis state. A missing, `unavailable` or
`unknown` contact is **unknown**: it never starts a pause and never ends one, and
`window_status` reports `unknown`. Any open contact wins over unknown ones.

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
| `intent_reason`, `pause_reason`, `window_status` | Why the proposal is off, and window/restart pauses. |
| `base_target_c`, `effective_target_c`, `compensation_c` | The three temperatures. |

Savings must not be inferred from a price-adjusted target alone. Real savings
depend on thermal losses and the heat pump's efficiency; any analysis belongs to
issue #7 and must state those assumptions.

## Options

Options can edit the sensor and actuator within the same control type, the AC
modes, thermostat settings and the AC-only settings (compensation, price
deviation, window contacts and delays). Changing control type requires a new
entry. Leaving *Maximum price adjustment* or *Window contacts* empty clears them.
Retargeting retains the wrapper entry/entity identity and updates the actuator
unique ID used for duplicate detection. Options reload cancels the old worker.

Before retargeting, save shadow mode first; for a switch the old switch must be
observed off and the new target must stay in shadow mode. This prevents leaving a
live old heater on without its controller. No automatic handoff command is sent.
