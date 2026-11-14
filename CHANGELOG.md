# Changelog

All notable changes are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/).

> **Pre-release, ungraded project.** This is not a graded Home Assistant Core
> integration and has no published release. It is not approved for controlling
> production heaters. See [docs/release.md](docs/release.md).

## [Unreleased]

`0.2.0` in `manifest.json` is only a placeholder version, not a release: no tag
or GitHub release exists. The entries below reflect `main`. Open before any
release: HACS validation (unverified while the repository is
private) and an install/upgrade/remove/rollback test in a disposable Home
Assistant instance.

### Added

- Bounded price-action (30 s) and switch/climate-action (10 s) waits, with
  cancellation and retry/corrective-OFF regressions (#12).
- Engineering quality evidence for all 54 HA rules, actionable entry-specific
  Nord Pool source repairs, full config-flow line/branch coverage and CI
  enforcement of >95% coverage per integration module (#12).

- Downloadable diagnostics v2: price cache freshness (last success, today/
  tomorrow cached, next retry) and coarse failure kinds; overlapping Nord Pool
  intervals are now rejected before caching. Synthetic 15-minute, hourly and
  Stockholm DST fixtures extend the service tests (#2).
- HACS repository metadata (`hacs.json`), hassfest-conformant `manifest.json`
  key order, a validation workflow, and release/status documentation.

- Control type selection: **Element / heating switch** and **Heat pump / AC
  climate**. The AC type supports heat/cool/off from an external room sensor,
  with compensation, restart protection and window pauses. It starts in shadow
  mode and has not been commissioned on physical equipment. See
  [docs/control-types.md](docs/control-types.md).
- Config entry migration from version 1 to 2 (`control_type: switch`),
  preserving entry/entity IDs, setpoints, presets and shadow options.
- Duplicate-actuator protection, including shadow entries, and rejection of
  integration-created climates as backends.
- Heat/cool price adjustment with bounded deviation.
- Shadow-first climate entity, UI config and options flow, English strings.
- Time-weighted rectangle/triangle price optimizer supporting 15-minute,
  hourly and mixed intervals.
- Nord Pool adapter using `nordpool.get_prices_for_date`, area validation,
  payload validation and cache rollover.
- Privacy-safe diagnostics and sensor unit validation.
- Strict typing, shadow-mode safety tests, coverage measurement and CI.
- Staging validation, troubleshooting and rollback documentation.

### Changed

- The options-flow safety confirmation is asked only when switching from shadow
  to live control (and the actuator can only change in shadow, so a new actuator
  is always confirmed). It is persisted as `safety_acknowledged` plus
  `safety_acknowledged_at` (UTC ISO time), the checkbox is hidden while live, and
  turning shadow mode on clears it. Entries already live under the previous
  rule, which forced the tick on every save, count as acknowledged without a
  migration.
- Outage/recovery transitions of Nord Pool data are logged; HA platform
  semantics are declared.

### Fixed

- Stale temperature samples disable heat demand.
- Repeated unacknowledged heater commands are guarded against.
- Climate evaluation events are coalesced and cancelled on unload.
