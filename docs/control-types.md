# Control types and backend contract

One integration supports two independently configured control types. Each entry
creates one virtual climate entity and selects one external temperature sensor.
The price optimizer is independent of the actuator backend.

| Control type | Actuator | Currently implemented modes | Live commands |
| --- | --- | --- | --- |
| Element / heating switch | Existing `switch` | Heat, off | Existing guarded switch control |
| Heat pump / AC climate | Existing `climate` | Off only, configuration preview | None |

## Existing storage-room installations

Version 1 entries migrate to version 2 with `control_type: switch`. Existing
entry/entity IDs, setpoints, presets, HVAC choices and shadow options remain
unchanged. The heating algorithm, relay guard and temperature freshness policy
remain in use. AC support does not replace the storage-room controller.

## Selecting a backend

Setup selects control type, external temperature sensor, controlled actuator,
Nord Pool source and existing temperature/price settings. The sensor must have
an available numeric temperature in Celsius, Fahrenheit or Kelvin. The actuator
must match the chosen control type. The shared `heater` configuration key is
retained for compatibility and identifies either kind of actuator.

For AC entries, choose intended heat and/or cool modes. The underlying climate
must support those modes and off, and report valid temperature limits/step.
These choices record configuration for subsequent controller work; they do not
advertise working heating/cooling in this preview. Configure room-appropriate
base/minimum/maximum temperatures explicitly: the existing setup defaults are
storage-room examples, not AC comfort defaults.

An actuator cannot be owned by two entries of this integration, including shadow
entries. Integration-created climates cannot be selected as backends, preventing
self-reference and chains/cycles. External writers cannot be detected generally.

Options can edit the sensor and actuator within the same control type, intended
AC modes and thermostat settings. Changing control type requires a new entry.
Retargeting retains the wrapper entry/entity identity and updates the actuator
unique ID used for duplicate detection. Options reload cancels the old worker. Before retargeting, save shadow mode
first; the old switch must be observed off and the new target must stay in shadow
mode. This prevents leaving a live old heater on without its controller. No
automatic handoff command is sent. AC preview never owns its physical backend,
so it does not require turning off an otherwise independently controlled pump.

## AC preview behavior and next steps

AC entries always remain in shadow mode, even if stored options are manually
changed. They expose the external room temperature and an editable, persistent
base target, fixed/price-aware preset selection, and off mode. Neither preset
runs AC optimization yet. `optimization_reason` is
`climate_backend_not_implemented`; `planned_heating` and `native_target_c` are
null and `live_control_supported` is false. No switch or climate command is sent,
even on off, missing sensors, reload or removal.

The three temperature contracts are:

1. **Base room target:** user-selected target, displayed by the climate UI.
2. **Effective room target:** price-adjusted demand target; in this preview it
   equals the base target.
3. **Native actuator target:** compensated, rounded and bounded pump command;
   not calculated in this preview.

Issue #21 implements heat/cool price-adjusted room targets. Issue #23 adds the
climate actuator state machine, compensation, windows and restart protection.
Issue #24 validates both types and documents migration/exporter compatibility.
Do not use this preview to replace an operating AC controller.
