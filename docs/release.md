# Release policy, packaging and rollback

> **Nothing in this document authorises public distribution or live heater
> control.** Both require an explicit decision by the repository owner. This
> project is a pre-release, ungraded development preview, not approved for
> controlling production heaters. Nothing below is a support commitment.

## License

The repository uses the [MIT license](../LICENSE), selected by the owner and
merged in PR #36. [SECURITY.md](../SECURITY.md) explains private reporting;
[CONTRIBUTING.md](../CONTRIBUTING.md) describes contribution checks. A license
is not a supported-release or physical commissioning approval.

## HACS validation status: UNVERIFIED

The HACS action can only validate public repositories. While the repository is
private, the `hacs` job in `validate.yml` is skipped. A skipped job is **not** a
pass: HACS validation has not been run and the issue #9 criterion "HACS
validation succeeds" remains **unmet**. hassfest does run and passes.

## Tested versions

| Component | Tested version | Notes |
| --- | --- | --- |
| Home Assistant | 2026.11.0 | Pinned in `pyproject.toml` and CI. |
| Python, full Home Assistant stack | 3.14.2 or newer, below 3.15 | Matches `requires-python`. |
| Python, pure optimizer unit tests only | 3.12 and 3.13 | Run in CI without Home Assistant. |

Older Home Assistant or Python versions are **unsupported and untested**.
`hacs.json` sets `homeassistant` to `2026.11.0` as the minimum. Policy: the
minimum is raised only with a tested pin bump and a changelog entry; newer
versions are considered only once CI passes against them.

The authoritative version is `version` in
`custom_components/price_aware_thermostat/manifest.json`. `pyproject.toml`
says 0.1.0 and is not a release artefact.

## Status matrix

| Capability | Status |
| --- | --- |
| Shadow mode (read-only evaluation, default for new installs) | Implemented, CI-tested |
| Price optimizer, Nord Pool adapter, diagnostics | Implemented, CI-tested (shadow mode) |
| Element / heating switch live control (shadow mode disabled) | Experimental; not commissioned on physical equipment |
| Heat pump / AC climate type | Experimental heat/cool/off controller; CI-tested, not commissioned on real equipment |
| Frost protection, over-temperature protection, fail-safe on sensor loss | Requires independent hardware safety; never rely on this integration |
| Unattended heating of production heaters | Not approved |

Shadow mode remains the default for every new installation.

## Release procedure

Do not run these steps without an explicit owner decision to release.

1. Confirm the license decision and that no release-blocking safety issues are open.
2. Run the full checks (as in README and CI):
   - `pytest -q tests --cov=custom_components.price_aware_thermostat --cov-report=term-missing`
   - `python3 -m unittest discover -s tests`
   - `ruff check custom_components/price_aware_thermostat tests/integration`
   - `mypy custom_components/price_aware_thermostat`
3. Bump `version` in `custom_components/price_aware_thermostat/manifest.json`.
4. Move `CHANGELOG.md` Unreleased entries under the new version and date.
5. Merge to `main`. The HACS check cannot run while the repository is private;
   the release must not proceed until the HACS validation passes on a public
   repository, and hassfest passes too.
6. Tag `vX.Y.Z` matching the manifest version and create a GitHub release.
7. HACS custom-repository install (public repository only; HACS cannot add a
   private one): in HACS choose Custom repositories, add the repository URL
   with type **Integration**, download the release, restart Home Assistant,
   then add the integration under Settings, Devices & services.

## Upgrade notes

1. Take a Home Assistant backup first.
2. Read the changelog for the target version.
3. Update through HACS (or replace the folder), restart Home Assistant.
4. Confirm new or migrated entries remain in shadow mode before changing anything.
5. Config entries at version 1 migrate to version 2 with
   `control_type: switch`; entity IDs and settings are preserved.

## Remove and rollback

Shadow mode, unloading, removing the entry and stopping Home Assistant do
**not** command the heater or pump. They leave whatever state the device is in.

Remove:

1. Verify the actual state of the controlled switch/climate in Home Assistant
   and on the device, and set it manually as intended (for example off, or
   under your independent thermostat). Put the entry in shadow mode.
2. Take a backup.
3. Delete the integration entry under Devices & services, remove the
   folder (or uninstall via HACS), restart Home Assistant.
4. Re-enable the original thermostat or automation for the heater.

Rollback:

1. Verify the actual state of the controlled switch/climate and set it
   manually as intended; set all entries to shadow mode or stop Home
   Assistant. Stopping does not turn a heater off.
2. Restore the pre-upgrade backup, or reinstall the previous version folder.
3. Restart. Restoring the backup also restores config entries.

Config entries migrated to a newer version cannot be reliably opened by an
older release; restore the backup rather than downgrading in place. Rollback
and removal preserve the underlying `climate` and `switch` entities because
this integration only wraps them. The flip side is that no command is sent
for you, so an element left on stays on until you act.

See also [staging validation](staging-validation.md) and
[troubleshooting](troubleshooting.md).

## Release gates still open

- License decision.
- HACS validation on a public repository (currently unverified).
- HACS repository-level requirements: GitHub repository description and
  topics, issues enabled, repository not archived, brands check.
- Installation, upgrade, remove and rollback tested in a disposable Home
  Assistant environment.
- No tag or release has been created.
