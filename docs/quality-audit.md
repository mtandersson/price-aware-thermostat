# Engineering quality evidence

Reviewed 2026-11-14 against Home Assistant 2026.11.0 and its
[54 quality rules](https://contributors.home-assistant.io/core/quality-scale/rules/).
This custom integration is **ungraded**. This is an engineering audit, not a
claim to Bronze, Silver, Gold or Platinum, and not a commissioning approval.

**Implemented** means there is source/test evidence. **Adapted** records a
conscious difference for this user-created helper, not an officially accepted
Core exemption. **Not applicable** means the feature does not exist here.
Release and equipment checks remain with their existing issues; this document
is evidence, not another implementation backlog.

## Bronze

| Rule | Assessment | Evidence / rationale |
| --- | --- | --- |
| action-setup | Not applicable | No custom actions are registered; the climate platform owns its standard actions. |
| appropriate-polling | Implemented | `coordinator.py`: 15-minute refresh, per-day cache, one-hour current-day and 30-minute tomorrow retry cooldowns; `test_nordpool_cache_edges.py`. |
| brands | Adapted | No Core brands registration is claimed for this private custom helper; public distribution validation stays in #9. |
| common-modules | Implemented | Shared `coordinator.py`, `optimizer.py`, `control.py`, `relay_guard.py`, `ac_control.py`, `backends.py`; the single entity platform shares its base class in `climate.py`. |
| config-flow | Implemented | UI setup, options and source/area reconfiguration, localized field/error descriptions; `test_flow.py`, `test_backends.py`, `test_quality_flow.py`. |
| config-flow-test-coverage | Implemented | All executable lines and branches of `config_flow.py` covered; CI enforces both, including correction after source/backend errors and live price-source changes. |
| dependency-transparency | Implemented | Manifest dependencies are built-in HA platforms; `after_dependencies` orders official Nord Pool. No direct external HTTP client or Python runtime requirements. Dev dependencies are explicit in `pyproject.toml`/`uv.lock`. |
| docs-actions | Not applicable | No integration-specific actions; standard climate commands are described in `control-types.md`. |
| docs-conditions | Not applicable | No integration-specific conditions. |
| docs-high-level-description | Implemented | README describes purpose, two control types and experimental status. |
| docs-installation-instructions | Implemented | README development installation and staging prerequisites. |
| docs-removal-instructions | Implemented | `staging-validation.md`, `troubleshooting.md`, `release.md`. |
| docs-triggers | Not applicable | No integration-specific triggers. |
| entity-event-setup | Implemented | `async_added_to_hass` registers listeners/timer; `async_on_remove` unsubscribes and cancels the worker; `test_event_coalescing.py`. |
| entity-unique-id | Implemented | Entry-ID-based climate identity survives retarget/reload; `test_backends.py`, `test_reconfigure.py`. |
| has-entity-name | Implemented | `_attr_has_entity_name = True`; user-selected standalone helper name. |
| runtime-data | Implemented | `PriceAwareConfigEntry` stores the typed coordinator in `runtime_data`. Coordinator receives its config entry explicitly. |
| test-before-configure | Adapted | Validate source domain/areas, sensor reading/unit, backend capabilities and internal duplicate ownership before accepting. Do not make network availability a prerequisite for a local thermostat; transient price loss is a supported base-target state. |
| test-before-setup | Adapted | Setup refreshes prices before exposing the entity, but keeps local regulation available on price failure. Persisted sensors/backends may load later; runtime freshness and backend guards handle that. Setup retry would remove local control during a cloud outage. |
| unique-config-entry | Implemented | Reject duplicate internal actuator ownership and recursive climate backends, including shadow entries. External automation ownership requires explicit commissioning inventory (#6). |

## Silver

| Rule | Assessment | Evidence / rationale |
| --- | --- | --- |
| action-exceptions | Implemented | Standard climate setters raise translated `ServiceValidationError`; backend errors are bounded and exposed; `test_exception_translations.py`, `test_ac_climate.py`, `test_relay_service_guard.py`. |
| config-entry-unloading | Implemented | Unload/reload and worker cancellation tests, including in-flight work and independent instances. Unload intentionally sends no actuator command. |
| docs-configuration-parameters | Implemented | `troubleshooting.md` and `control-types.md` cover shared and AC options, units, delays and clearable fields. |
| docs-installation-parameters | Implemented | README, configuration reference and backend/source selection in `control-types.md`. |
| entity-unavailable | Adapted | A logical controller remains available to expose the reason and accept an explicit OFF even when a sensor/backend is missing. Current temperature becomes unknown; automatic demand and backend commands use separate validity/freshness guards. Price unavailability alone never disables local control. Tests: `test_restart_and_failures.py`, `test_ac_climate.py`, `test_control_type_matrix.py`. No physical device reachability is implied by helper availability. |
| integration-owner | Implemented | `@mtandersson` in `manifest.json`. |
| log-when-unavailable | Implemented | One warning on current-day price loss and one recovery message; repeated failures throttled; `test_price_recovery_logging.py`. |
| parallel-updates | Implemented | `PARALLEL_UPDATES = 1`; per-entity lock and coalescing worker. |
| reauthentication-flow | Not applicable | No credentials owned by this integration. Authentication belongs to official Nord Pool and the existing device integrations. |
| test-coverage | Implemented | CI measures lines and branches, requires >95% per integration module and 100% config-flow lines/branches. Coverage is not equipment-safety evidence. |

## Gold

| Rule | Assessment | Evidence / rationale |
| --- | --- | --- |
| devices | Not applicable | User-created virtual thermostat controls existing HA entities; it must not register a duplicate physical device. |
| diagnostics | Implemented | Downloadable allowlisted diagnostics omit entity/entry IDs, names, precise room temperatures and arbitrary options; `test_diagnostics.py`. |
| discovery | Not applicable | Explicit helper creation; no network device discovery. |
| discovery-update-info | Not applicable | No discovered addresses. Existing device integrations own connectivity. |
| docs-data-update | Implemented | `troubleshooting.md` and `control-types.md`: refresh, events, command intervals/retries and freshness. |
| docs-examples | Implemented | Read-only comparison sensors/dashboard in `ac-evaluation-and-migration.md`; staging synthetic-device scenarios. |
| docs-known-limitations | Implemented | README, control contracts and release status explicitly distinguish software evidence, device behavior and physical protection. |
| docs-supported-devices | Implemented | `control-types.md` describes switch/climate capabilities, units/limits/step and unsupported controls; no real model or exporter certification is claimed (#24). |
| docs-supported-functions | Implemented | Heat/off switch, configured heat/cool/off climate, presets, price adjustment, compensation/windows and diagnostics documented. |
| docs-troubleshooting | Implemented | Missing/stale sources, Repairs, command acknowledgment, restart, removal and AC troubleshooting. |
| docs-use-cases | Implemented | Storage-room switch regulation and external-sensor AC evaluation/migration guides. |
| dynamic-devices | Not applicable | Each entry has one explicitly selected existing actuator; device discovery belongs to its provider. |
| entity-category | Not applicable | The sole climate is the main user control, not an auxiliary diagnostic/config entity. |
| entity-device-class | Not applicable | HA climate entities have no device-class property. Selected temperature sensors remain owned by their provider. |
| entity-disabled-by-default | Not applicable | One explicitly created primary climate entity; no noisy optional auxiliary entity inventory. |
| entity-translations | Adapted | The helper's name is explicitly user supplied; no hard-coded generated device/entity name is translated. Config/options/selectors/errors use `strings.json` and `translations/en.json`. English is the supported language. |
| exception-translations | Implemented | All user-facing climate setter errors carry translation keys/placeholders; parity and exception tests. |
| icon-translations | Not applicable | No integration-specific icons; climate frontend presentation uses standard HVAC semantics. |
| reconfiguration-flow | Implemented | Source/area changes via Reconfigure or options, also live (no actuator command); settings/options retain entity identity. `test_reconfigure.py`, `test_price_source_ui.py`. |
| repair-issues | Implemented | Entry-bound actionable warning for deleted/wrong source or removed market area. Valid configuration clears it even during API cooldown; transient outages do not create a repair. `test_price_source_repairs.py`. |
| stale-devices | Not applicable | No physical device registry records owned by this integration. |

## Platinum

| Rule | Assessment | Evidence / rationale |
| --- | --- | --- |
| async-dependency | Implemented | Only asynchronous HA service calls, coordinator and event subscriptions; no direct blocking network or file IO in control/refresh paths. |
| inject-websession | Not applicable | No HTTP session/client is created. Official Nord Pool owns its client and session. |
| strict-typing | Implemented | `py.typed`, typed config entry and strict mypy for all integration source in CI. |

## Verification and software safety review

Baseline before this audit: 317 tests, 22 subtests, 97% line coverage. The completed audit has 335 passing tests and 22 passing subtests, with
97.47% combined line/branch coverage and 100% config-flow line/branch coverage.
It adds source repair lifecycle/error recovery and stalled-service tests. CI also runs Ruff, strict mypy, Python 3.12/3.13 pure tests and
hassfest. The HACS check is skipped for a private repository; that is not a pass.

The HA test harness enables event-loop debug and checks lingering tasks/timers.
`test_event_coalescing.py` covers a 100-event burst and cancellation of in-flight
work. `test_ac_climate.py` covers obsolete intent, command retries, off/changeover,
restart and cancellation; `test_control_type_matrix.py` checks both types.
Source review found no blocking file/network calls in runtime control/refresh.
Price actions have a 30-second timeout per date; switch/climate actions have a
10-second timeout. Timeout cancels the waiting coroutine, preserves retry
throttling and allows a fresh corrective OFF decision. Cancellation is
cooperative: a device integration may already have transmitted a command, so
observed state and hardware protection still matter. `test_service_timeouts.py`
checks cancellation, no immediate retry storm and corrective OFF.

These checks bound software work; they do not prove latency under every external
integration or physical failure condition.

Security/safety review: no credentials stored here, no direct device/network
client, diagnostic allowlist, translated user errors, no writes in shadow,
internal duplicate/recursive ownership rejection, stale/invalid sensor guards,
observed acknowledgments, bounded retries and fresh-intent reevaluation.
Unload/reload sends no off command: physical equipment can remain in its last
mode. External writers, stuck relays, independent thermal/frost protection and
backup/rollback must be verified under #6 before a separately approved cutover.

Known warnings in the pinned development dependencies concern aiohttp application
inheritance and backoff's deprecated `asyncio.iscoroutinefunction`; they originate
outside this integration. They are reported, not suppressed by the audit.

## External evidence and release gates

- **#2:** isolated live official Nord Pool SE4/SEK config flow/action verified on
  2026-11-14 with HA 2026.11.0, Python 3.14.10 and pynordpool 0.4.0. Both today and
  tomorrow returned 96 contiguous quarter-hour intervals. Coordinator output
  matched the normalized action response; SEK/MWh divided by 1000 gives SEK/kWh.
  The intended deployment's entry/area and real historical hourly/DST responses
  remain separate checks; committed hourly/DST fixtures are synthetic.
- **#5:** actual shadow observations/comparison over time remain to be collected.
- **#6 / #4:** independent hardware protection, external writer inventory,
  physical fault behavior, verified backup/restore and approved cutover remain.
- **#24:** real thermostat UI and HomeKit/Matter exporter/device evaluation remain.
- **#9:** HACS validation, install/upgrade/removal/rollback evidence and an explicit
  release/publication decision remain. MIT licensing is already merged.

Completing this engineering audit does not complete those tickets or authorize
production installation, public distribution or unattended heating.
