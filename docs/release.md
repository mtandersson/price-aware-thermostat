# Release policy, packaging and rollback

> **Nothing in this document authorises public distribution or live heater
> control.** Both require an explicit decision by the repository owner. This
> project is a pre-release, ungraded custom integration, not approved for
> controlling production heaters.

## License: decision pending

No `LICENSE` file exists and none has been chosen. This is the repository
owner's decision and **blocks any public release or HACS default-store
submission**, because without a license others have no right to use, modify or
redistribute the code. Options:

- **MIT**: very permissive, minimal obligations.
- **Apache-2.0**: permissive with an explicit patent grant.
- **GPL-3.0**: copyleft; common for Home Assistant integrations (Home
  Assistant Core is Apache-2.0, many custom integrations use MIT or GPL).
- **Keep private / no license**: no redistribution rights granted.

## Supported versions

| Component | Supported | Notes |
| --- | --- | --- |
| Home Assistant | 2026.11.0 | Version pinned in `pyproject.toml` and tested in CI. |
| Python | 3.14 | Version used by the test environment. |

Older Home Assistant or Python versions are **unsupported and untested**.
`hacs.json` sets `homeassistant` to `2026.11.0` as the minimum. Policy: the
minimum is raised only with a tested pin bump and a changelog entry; newer
versions are supported only once CI passes against them.

## Support matrix

| Capability | Status |
| --- | --- |
| Shadow mode (read-only evaluation, default for new installs) | Supported |
| Price optimizer, Nord Pool adapter, diagnostics | Supported |
| Element / heating switch live control (shadow mode disabled) | Experimental; not commissioned on physical equipment |
| Heat pump / AC climate type | Experimental; shadow-only configuration preview |
| Frost protection, over-temperature protection, fail-safe on sensor loss | Requires independent hardware safety; never rely on this integration |
| Unattended heating of production heaters | Not approved |

Shadow mode remains the default for every new installation.

## Release procedure

Do not run these steps without an explicit owner decision to release.

1. Confirm the license decision and that no release-blocking safety issues are open.
2. Run the full checks: `uv run --frozen pytest -q`, `ruff`, `mypy`.
3. Bump `version` in `custom_components/price_aware_thermostat/manifest.json`.
4. Move `CHANGELOG.md` Unreleased entries under the new version and date.
5. Merge to `main`; confirm the validate workflow (HACS + hassfest) passes.
6. Tag `vX.Y.Z` matching the manifest version and create a GitHub release.
7. HACS custom-repository install: in HACS choose Custom repositories, add the
   repository URL with type **Integration**, download the release, restart
   Home Assistant, then add the integration under Settings, Devices & services.

## Upgrade notes

1. Take a Home Assistant backup first.
2. Read the changelog for the target version.
3. Update through HACS (or replace the folder), restart Home Assistant.
4. Confirm new or migrated entries remain in shadow mode before changing anything.
5. Config entries at version 1 migrate to version 2 with
   `control_type: switch`; entity IDs and settings are preserved.

## Remove and rollback

Remove:

1. Put the entry in shadow mode and confirm the heater is controlled by your
   independent thermostat.
2. Take a backup.
3. Delete the integration entry under Devices & services, remove the
   folder (or uninstall via HACS), restart Home Assistant.

Rollback:

1. Stop Home Assistant, or at least set all entries to shadow mode.
2. Restore the pre-upgrade backup, or reinstall the previous version folder.
3. Restart. Restoring the backup also restores config entries.

Config entries migrated to a newer version cannot be reliably opened by an
older release; restore the backup rather than downgrading in place. Rollback
leaves the underlying `climate` and `switch` entities untouched, since this
integration only wraps them and sends no command on removal. Re-enable the
original thermostat for the heater after removal.

See also [staging validation](staging-validation.md) and
[troubleshooting](troubleshooting.md).

## Release gates still open

- License decision.
- Installation, upgrade, remove and rollback tested in a disposable Home
  Assistant environment.
- No tag or release has been created.
