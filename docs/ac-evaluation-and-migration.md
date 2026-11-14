# Heat pump / AC: evaluation, compatibility and migration

This guide covers the **Heat pump / AC climate** control type: how to evaluate it
without commanding any equipment, what has and has not been verified for
HomeKit/Matter exporters, and how to move from an existing automation to this
integration and back. It is a **test and operations procedure, not a release
approval**. Nothing here authorises replacing a working controller. The backlog
lives in [GitHub issues](https://github.com/mtandersson/price-aware-thermostat/issues);
behaviour is defined in [control types](control-types.md).

> **No production cutover is authorised by this guide.** Moving a real AC from
> its current controller to this integration is a separate decision for the
> owner of the equipment, taken after the evaluation below. Savings are not
> established by any step here (see [Savings](#savings-are-not-implied)).

## 1. Non-production evaluation procedure

Goal: see the wrapper's decisions beside an existing controller for several
days, with provable zero commands.

### 1.1 Reproducible disposable check (no real equipment)

1. Use a disposable Home Assistant instance (a fresh container or a restored
   throw-away backup) with no access to real devices.
2. Provide a stand-in backend climate: for example a climate from Home
   Assistant's `demo` integration, or any template/virtual climate that supports
   off, a heat and/or cool mode, a target temperature, minimum/maximum
   temperatures and a target step. Open **Developer tools → States** and confirm
   the entity reports `hvac_modes`, `min_temp`, `max_temp` and
   `target_temp_step`; setup rejects a backend that does not.
3. Provide a stand-in room sensor: a `sensor` with `device_class: temperature`
   and a unit of `°C`, `°F` or `K` that you can change by hand (for example a
   template sensor driven by an `input_number`).
4. Configure the official Nord Pool integration for a test area (see
   [staging validation](staging-validation.md) for the price-source checks).
5. Add **Price-Aware Thermostat**, control type *Heat pump / AC climate*, with
   room-appropriate base/minimum/maximum temperatures. Leave **Shadow mode on**.
6. Set the wrapper to `heat` or `cool` and move the stand-in room temperature
   through the hysteresis band. Check each row:

| Condition | Expected in shadow mode |
| --- | --- |
| Room below `target - tolerance` (heat) / above `target + tolerance` (cool) | `proposed_backend_mode` is the wrapper mode, `proposed_native_target_c` is set; **zero** climate service calls |
| Room inside the band | `intent_reason: no_demand`, proposed mode `off` |
| Wrapper set to `off` | `intent_reason: wrapper_off`, proposed mode `off` |
| Room sensor `unavailable`, `unknown` or stale | `intent_reason: room_temperature_unavailable`; no commands |
| Window contact open for longer than the open delay | `window_status: open_paused`, `pause_reason: window_open` |
| Price data missing | `optimization_reason: price_unavailable`; effective target equals the base target; the proposal is **not** forced off |
| Stand-in backend changed by hand | `observed_*` follow it; `backend_in_sync` becomes false; still no commands |
| Backend `unavailable` | `command_status: backend_unavailable` |
| Reload and restart | Settings, base target and the chosen mode are restored; no commands |

   Any `climate` or `switch` service call originating from the wrapper while
   shadow mode is on is a **release blocker**. Check **Developer tools → Events**
   (listen to `call_service`) or the backend's logbook.
7. Only on a disposable instance: to see commands, turn shadow mode off with the
   safety confirmation and watch the stand-in backend receive `set_hvac_mode`
   and `set_temperature`. Verify the restart pause, the changeover through off,
   window pause/resume and the retry bounds described in
   [control types](control-types.md).

The automated tests in `tests/integration/test_ac_climate.py` exercise the same
matrix against a real `ClimateEntity` fake. They prove the software behaviour
against the Home Assistant entity-service contract; they do not prove anything
about a real heat pump.

### 1.2 Shadow evaluation beside a real controller (read-only)

If you choose to observe a real AC while its existing controller stays in
charge, the wrapper in shadow mode only *reads* the backend and the room sensor.
It sends nothing, even when the wrapper is off or a sensor fails. Keep these
rules:

- Keep the wrapper in shadow mode. Do not enable live control during evaluation.
- Do not edit or disable the existing controller.
- Do not treat the wrapper's `proposed_*` values as having been applied: the
  backend only shows what the existing controller does.
- Record the Home Assistant version, integration commit, and your settings in
  private notes, not in a public issue.

### 1.3 Comparison dashboard

The wrapper exposes the proposal and the observed backend as attributes. To chart
them without creating high-frequency churn, publish the few values you want to
compare as template sensors, which the recorder stores with long-term statistics:

```yaml
# configuration.yaml: adapt the entity IDs to your installation
template:
  - sensor:
      - name: "AC room target (effective)"
        unique_id: ac_room_target_effective
        unit_of_measurement: "°C"
        device_class: temperature
        state_class: measurement
        state: "{{ state_attr('climate.room_ac', 'effective_target_c') }}"
        availability: "{{ state_attr('climate.room_ac', 'effective_target_c') is not none }}"
      - name: "AC proposed pump target"
        unique_id: ac_proposed_pump_target
        unit_of_measurement: "°C"
        device_class: temperature
        state_class: measurement
        state: "{{ state_attr('climate.room_ac', 'proposed_native_target_c') }}"
        availability: "{{ state_attr('climate.room_ac', 'proposed_native_target_c') is not none }}"
      - name: "AC observed pump target"
        unique_id: ac_observed_pump_target
        unit_of_measurement: "°C"
        device_class: temperature
        state_class: measurement
        state: "{{ state_attr('climate.room_ac', 'observed_native_target_c') }}"
        availability: "{{ state_attr('climate.room_ac', 'observed_native_target_c') is not none }}"
  - sensor:
      - name: "AC proposed mode"
        unique_id: ac_proposed_mode
        state: "{{ state_attr('climate.room_ac', 'proposed_backend_mode') }}"
      - name: "AC observed mode"
        unique_id: ac_observed_mode
        state: "{{ state_attr('climate.room_ac', 'observed_backend_mode') }}"
      - name: "AC command status"
        unique_id: ac_command_status
        state: "{{ state_attr('climate.room_ac', 'command_status') }}"
```

```yaml
# Lovelace: existing control (observed) against the proposal over time
type: vertical-stack
cards:
  - type: history-graph
    title: Room temperature and targets
    hours_to_show: 48
    entities:
      - entity: sensor.room_temperature        # your room sensor
      - entity: sensor.ac_room_target_effective
      - entity: sensor.ac_observed_pump_target
      - entity: sensor.ac_proposed_pump_target
  - type: history-graph
    title: Mode: existing control vs proposal
    hours_to_show: 48
    entities:
      - entity: sensor.ac_observed_mode
      - entity: sensor.ac_proposed_mode
      - entity: sensor.ac_command_status
  - type: entities
    title: Why
    entities:
      - type: attribute
        entity: climate.room_ac
        attribute: intent_reason
      - type: attribute
        entity: climate.room_ac
        attribute: pause_reason
      - type: attribute
        entity: climate.room_ac
        attribute: window_status
      - type: attribute
        entity: climate.room_ac
        attribute: optimization_reason
```

The pump target compared here includes the heating/cooling compensation, so it
will differ from the room target by design. Reported HVAC mode shows what the
backend says; it is not measured compressor runtime and not an energy meter.

## 2. HomeKit and Matter compatibility

What is **verified**: the wrapper is a standard Home Assistant `climate` entity.
Its supported features, HVAC modes, minimum/maximum temperature, target step,
current temperature and base target are exercised through Home Assistant's own
state machine and entity services in `tests/integration`. That is a test of the
Home Assistant entity contract using mocked/fake backends.

What is **not verified**: any actual HomeKit Bridge, Matter exporter, controller
app or physical device. Being in the `climate` domain does not by itself
establish compatibility, and this project makes no compatibility claim. Known
points to check yourself on a disposable instance before relying on it:

- The wrapper offers presets `price_aware` and `fixed`. Exporters typically do not
  expose presets, so those choices will not be available outside Home Assistant.
- The wrapper's modes are the configured subset of heat/cool plus off, and it does
  not provide automatic heat/cool changeover. An exporter that offers `auto` may
  have no equivalent to map it to.
- Target temperature is the **base room target**; the effective target and the
  native pump target are attributes only.
- Changes made in the exporter's app arrive as ordinary service calls to the
  wrapper and are subject to the same limits and persistence.

Manual checklist (owner, on a disposable instance): confirm the entity appears
with the expected modes and range in the Home Assistant thermostat card, that
changing the target in the thermostat UI changes the **base** target while the
current temperature shows the room sensor, and, if you export it, record what the
exporter actually shows and sends.

## 3. Migrating from an existing external-sensor automation

This procedure keeps the old setup recoverable and prevents two writers on one
backend. It does not install, enable or cut over anything by itself.

### Before you start

1. Make a verified Home Assistant backup and store it off the instance.
2. Inventory and write down: the room sensor entity ID, the backend `climate`
   entity ID and the entity IDs of any window contacts; the backend's current
   HVAC mode and target; the automations, scripts, blueprints and apps that write
   to the backend (check each automation's traces and the backend's logbook).
3. Export the existing automation(s) (YAML) and keep them unchanged.
4. Decide the comfort settings: base/minimum/maximum temperature, tolerances,
   restart pause, compensation per mode, window delays, optional price deviation.

### Entity IDs: no duplicates

The wrapper creates its own climate entity (named after the entry title). Do not
rename the old entity to the same ID or create a second entity with an ID that
already exists. If you want dashboards and voice assistants to keep working
through an existing ID, rename the **old** entity first through an explicit,
collision-free rename, then give the wrapper the freed ID; otherwise update the
references to the new entity. Retargeting the wrapper later keeps its own entity
and unique ID stable.

### Cutover steps (owner decision, after evaluation)

1. Evaluate in shadow mode first (section 1) and review the comparison.
2. Stop the previous writer: disable the automation(s) and any other source of
   backend commands. **Do not run both.** The integration prevents a second
   wrapper on the same backend but cannot detect other writers.
3. Check the backend's current state, then set the wrapper to the mode you want
   (`heat` or `cool`) **before** enabling live control. With the wrapper off, live
   control turns the pump off.
4. In the options, turn shadow mode off and confirm exclusive ownership and
   independent hardware safeguards.
5. Confirm the outcome: the backend mode and target match `proposed_*`,
   `backend_in_sync` is true, and no other automation is changing the backend.
   Keep watching for several days.

### Rollback

1. In the wrapper options turn **shadow mode on** (live control off). Neither this
   nor removing the entry commands the backend; it stays in its last commanded mode
   and target.
2. Check the backend's actual state and set it manually as intended.
3. Re-enable the original automation(s) from the exported YAML only after the
   wrapper is in shadow mode or removed, so there is never more than one writer.
4. Confirm the backend follows the original controller again.
5. Optionally remove the integration entry. The old configuration was kept
   unchanged throughout, so nothing needs to be restored beyond re-enabling it.

## 4. What the wrapper controls, and what it does not

- Commands it sends while live: `climate.set_hvac_mode` and
  `climate.set_temperature` on the selected backend.
- Not controlled: fan speed, swing, presets, humidity, auto/dry/fan-only modes,
  scheduling inside the device, and any other device feature. Automatic heat/cool
  changeover, adaptive learning and production cutover are outside this release.
- Behaviours it provides: separate heat/cool compensation, comfort limits and an
  optional maximum price deviation, window/door pause, a minimum restart pause,
  safe changeover through off, bounded retries, and fail-closed handling of an
  unavailable, invalid or stale room sensor. Details and the full failure table
  are in [control types](control-types.md).
- Removing or unloading the integration, or stopping Home Assistant, sends no
  command. Always check the backend's actual state afterwards.

## Savings are not implied

A price-adjusted target shows what the algorithm would ask for; it does not show
energy or money saved. Savings depend on the building's thermal losses, the heat
pump's efficiency (COP) and the price spread, none of which this guide measures.
Any analysis must state those assumptions and belongs to
[issue #7](https://github.com/mtandersson/price-aware-thermostat/issues/7).
