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
release: license decision, HACS validation (unverified while the repository is
private) and an install/upgrade/remove/rollback test in a disposable Home
Assistant instance.

### Added

- Downloadable diagnostics v2: price cache freshness (last success, today/
  tomorrow cached, next retry) and coarse failure kinds; overlapping Nord Pool
  intervals are now rejected before caching. Synthetic 15-minute, hourly and
  Stockholm DST fixtures extend the service tests (#2).
- HACS repository metadata (`hacs.json`), hassfest-conformant `manifest.json`
  key order, a validation workflow, and release/status documentation.

- Control type selection: **Element / heating switch** and **Heat pump / AC
  climate**. The AC type is a shadow-only configuration preview (off mode,
  editable base target, no actuator commands). See
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

- Outage/recovery transitions of Nord Pool data are logged; HA platform
  semantics are declared.

### Fixed

- Stale temperature samples disable heat demand.
- Repeated unacknowledged heater commands are guarded against.
- Climate evaluation events are coalesced and cancelled on unload.
