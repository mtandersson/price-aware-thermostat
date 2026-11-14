# Price-Aware Thermostat

An experimental Home Assistant custom integration for on/off electric heaters that adjusts temperature targets using a weighted lookahead of Nord Pool electricity prices.

> **Development preview: not approved for unattended heating or frost protection.** The integration starts in **shadow mode** and never sends relay commands until a user explicitly disables shadow mode and confirms independent hardware safeguards. Keep your existing thermostat in service during development and evaluation.

## Control types

The existing **Element / heating switch** type retains storage-room regulation.
The **Heat pump / AC climate** type regulates a room from an external
temperature sensor by commanding an existing climate entity with HVAC-mode and
temperature commands, with separate heating and cooling price adjustment and
compensation, a minimum restart pause, safe heat/cool changeover and optional
window/door pauses. Both types start in shadow mode, which sends no commands.
Live AC control is new and has not been validated on real equipment; do not use
it to replace a working controller without the staging steps in the guides.
See [control types and backend contract](docs/control-types.md) and, for the
AC type, [evaluation, compatibility and migration](docs/ac-evaluation-and-migration.md).
A synthetic, simulation-only [optimizer benchmark](docs/benchmark.md) is also available; it does not measure real savings.

## HACS (not yet available)

The repository carries HACS metadata, but no release exists, HACS validation is
unverified (the HACS check cannot run on a private repository) and HACS cannot
add a private repository. Only after the owner makes the repository public and
publishes a release could it be added as a custom repository of type
**Integration**. See [release policy and procedures](docs/release.md).

## Release status

Pre-release, ungraded development preview, not approved for controlling
production heaters. Tested versions, the status matrix, upgrade, removal
and rollback procedures, and the license information are in
[docs/release.md](docs/release.md). See also the [changelog](CHANGELOG.md).

## Features

- Home Assistant `ClimateEntity` for a temperature sensor and a heating switch.
- Time-weighted rectangle and triangle filters comparing current and future electricity prices.
- Support for 15-minute, hourly and mixed price intervals.
- Official Home Assistant Nord Pool action `nordpool.get_prices_for_date`, using SEK/MWh converted to SEK/kWh.
- Bounded temperatures, price deadband, heating hysteresis and a minimum relay cycle duration.
- Configurable maximum age for temperature sensor reports (default: 60 minutes). Stale or unavailable readings disable automatic heat demand; independent hardware frost protection remains essential.
- Price-aware or fixed-temperature presets and explicit heat/off modes.
- UI-based setup and options flow, English UI strings, diagnostics in climate attributes, and downloadable diagnostics with price-cache freshness and failure kinds.
- The Nord Pool price source and market area are shown and editable in the options, also in live mode, and an **Electricity price** sensor on the thermostat device shows the current price used, its source entry, market area, weighted future price and cache freshness.
- A default **read-only shadow mode** for side-by-side evaluation before commissioning.

## Development installation

Do not install this on a heater that lacks independent hardware safety controls.

1. Copy `custom_components/price_aware_thermostat` into the `custom_components` folder of a non-production Home Assistant instance.
2. Restart Home Assistant and add **Price-Aware Thermostat** under Settings → Devices & services.
3. Select a temperature sensor, switch, official Nord Pool integration entry and market area (e.g. `SE4`).
4. Configure minimum, base and maximum temperatures. These are safety-relevant, not pre-approved defaults for your equipment.
5. Leave **Shadow mode** enabled. Inspect `planned_heating`, `effective_target_c`, `optimization_reason` and current sensor readings.
6. Only consider disabling shadow mode after a physical safety inspection, backup and exclusive switch-ownership review, using the separate commissioning checklist.

This is a custom integration under development, not a graded Home Assistant Core integration or a released HACS integration.

## How the optimizer works

It compares the current price to a weighted mean of forecast intervals. The `triangle` filter favors nearer intervals, while `rectangle` weights all covered minutes equally. Calculations are duration-based and tolerate mixed interval lengths. In case of missing or invalid price forecasts, the base temperature is used. This fallback does **not** substitute for a hardwired safety thermostat.

The normal target temperature displayed in the climate UI is the base setpoint; `effective_target_c` is the price-adjusted setpoint.

The optimizer supports heating and cooling. Heating raises the target while electricity is relatively cheap and lowers it while expensive; cooling mirrors this (lower target when cheap, higher when expensive). Price adjustment is **relative to the target you set**: the effective target is your target ± at most the *maximum price adjustment* (default 0.8 °C; 0 disables price adjustment), then clamped to the minimum/maximum temperatures. Minimum and maximum (new-entry defaults 5.0/25.0 °C) are absolute safety limits and the allowed setpoint range in the UI; they no longer define the size of the price adjustment. `requested_adjustment_c` shows what the price signal asked for before limits and `adjustment_clamped` shows when limits reduced or removed it, so a target equal to the base is still explainable. Both are neutral (`0.0` / `false`) in the fixed preset and when off. The switch controller is heating-only; cooling applies to the heat-pump/AC control type.

## Staging validation, troubleshooting and removal

Before any use with a physical heater, follow the [staging validation and commissioning gates](docs/staging-validation.md), including a disconnected mock relay, official Nord Pool service-action verification, and rollback evidence. Do **not** enable live control on production equipment to collect this evidence.

See [troubleshooting and configuration reference](docs/troubleshooting.md) for complete setup options, expected diagnostics, supported entities, error handling and step-by-step removal from a staging installation.

The first supported release is blocked on a verified official Nord Pool contract and a hardware safety review; see [issue #2](https://github.com/mtandersson/price-aware-thermostat/issues/2), [issue #6](https://github.com/mtandersson/price-aware-thermostat/issues/6) and [quality audit #12](https://github.com/mtandersson/price-aware-thermostat/issues/12).

## Quality audit

See the [rule-by-rule quality evidence](docs/quality-audit.md) for the engineering
review, custom-helper adaptations, automated checks and external release gates.
The integration has no official Home Assistant quality tier.

## Developer workflow

The pure optimizer and hysteresis tests have no Home Assistant dependency:

```bash
python -m unittest discover -s tests -v
```

Home Assistant compatibility checks target Home Assistant 2026.11.0 and
Python 3.14.2 or newer within the 3.14 series. For development with Nix and direnv:

```bash
direnv allow
# Entering the directory loads the locked Nix shell and synchronizes .venv.
pytest -q tests --cov=custom_components.price_aware_thermostat --cov-report=term-missing
ruff check custom_components/price_aware_thermostat tests/integration
mypy custom_components/price_aware_thermostat
```

Without direnv, use `nix develop`; it loads the same environment. `flake.lock`
pins Python and uv, while `uv.lock` pins the development dependencies. The shell
runs `uv sync --frozen --only-group dev`; dependencies stay in the ignored
project-local `.venv`. No Home Assistant installation is configured or modified.
To intentionally update Python dependencies, run `uv lock --upgrade`, review the
lock diff and rerun the checks. Pure tests above still run without HA on Python
3.12/3.13 as tested in CI.

Development work is tracked in [GitHub issues](https://github.com/mtandersson/price-aware-thermostat/issues): [Nord Pool adapter (#2)](https://github.com/mtandersson/price-aware-thermostat/issues/2), [configuration (#3)](https://github.com/mtandersson/price-aware-thermostat/issues/3), [safe climate control (#4)](https://github.com/mtandersson/price-aware-thermostat/issues/4), [shadow diagnostics (#5)](https://github.com/mtandersson/price-aware-thermostat/issues/5), [commissioning (#6)](https://github.com/mtandersson/price-aware-thermostat/issues/6), and [benchmarking (#7)](https://github.com/mtandersson/price-aware-thermostat/issues/7).

## Important limitations

- This code has not been commissioned against physical heating equipment.
- Changing an actuator that another thermostat controls can cause competing commands. **Never enable live control while another automation/thermostat writes to the same switch.**
- A missing or invalid temperature sensor causes live control to attempt to turn the relay off. Independent physical frost/over-temperature protection remains necessary.
- Electricity prices exclude local tariffs, network charges, VAT and additional fees.
- Optimization does not guarantee lower energy consumption or cost; higher temperatures can increase thermal losses.
- The default minimum temperature is an example, **not a safe temperature recommendation** for any particular storage room.

## References

- [Home Assistant Generic Thermostat](https://www.home-assistant.io/integrations/generic_thermostat/)
- [Official Nord Pool integration](https://www.home-assistant.io/integrations/nordpool/)
- [Get prices for date](https://www.home-assistant.io/actions/nordpool.get_prices_for_date/)
- [Nordpool Diff (algorithm inspiration)](https://github.com/jpulakka/nordpool_diff)

## License

[MIT](LICENSE). The software is provided without warranty and must not be relied on as a safety device. See [SECURITY.md](SECURITY.md) to report vulnerabilities and [CONTRIBUTING.md](CONTRIBUTING.md) to contribute.
