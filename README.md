# Price-Aware Thermostat

An experimental Home Assistant custom integration for on/off electric heaters that adjusts temperature targets using a weighted lookahead of Nord Pool electricity prices.

> **Development preview: not approved for unattended heating or frost protection.** The integration starts in **shadow mode** and never sends relay commands until a user explicitly disables shadow mode and confirms independent hardware safeguards. Keep your existing thermostat in service during development and evaluation.

## Features

- Home Assistant `ClimateEntity` for a temperature sensor and a heating switch.
- Time-weighted rectangle and triangle filters comparing current and future electricity prices.
- Support for 15-minute, hourly and mixed price intervals.
- Official Home Assistant Nord Pool action `nordpool.get_prices_for_date`, using SEK/MWh converted to SEK/kWh.
- Bounded temperatures, price deadband, heating hysteresis and a minimum relay cycle duration.
- Price-aware or fixed-temperature presets and explicit heat/off modes.
- UI-based setup and options flow, English UI strings, and diagnostics in climate attributes.
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

## Developer workflow

The pure optimizer and hysteresis tests have no Home Assistant dependency:

```bash
python -m unittest discover -s tests -v
```

Home Assistant compatibility checks run against Home Assistant 2026.11.0 in CI with Python 3.14. To run them manually in a dedicated development environment:

```bash
python -m pip install "homeassistant==2026.11.0" "pytest-homeassistant==2026.11.0" ruff
pytest -q tests/integration
ruff check custom_components/price_aware_thermostat tests/integration
```

See the [implementation plan](docs/implementation-plan.md) and [open issues](https://github.com/mtandersson/price-aware-thermostat/issues) for remaining safety, correctness and validation work.

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

No license is granted for redistribution or use beyond GitHub's default terms while the repository is private. A project license can be selected before any public release.
