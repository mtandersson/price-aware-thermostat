# Price-Aware Thermostat

A planned Home Assistant custom climate integration that optimizes on/off electric heating using weighted Nord Pool electricity price forecasts.

> **Status: algorithm prototype only. Not installable in Home Assistant.** There is currently no climate entity, config flow, manifest, or physical relay control. Keep existing frost protection operational.

## Goals

- Reproduce the predictable sensor-and-switch behavior of Home Assistant Generic Thermostat.
- Adjust the target temperature based on the current electricity price relative to a time-weighted lookahead window.
- Support rectangle and triangle filters with 15-minute, hourly, and mixed-duration price intervals.
- Use the official Home Assistant Nord Pool integration rather than scraping Nord Pool directly.
- Always enforce configured temperature limits and fall back to the base setpoint when forecast data is missing.
- Support a shadow mode before allowing control of any physical heater.

## Current prototype

The dependency-free Python module `custom_components/price_aware_thermostat/optimizer.py` calculates a recommended target temperature; it never calls Home Assistant services or changes the relay.

Run the current tests:

```bash
python -m unittest discover -s tests -v
```

Python 3.12 or later is required for this starter prototype.

## Development roadmap

See [Implementation plan](docs/implementation-plan.md) for the planned integration, hardware safety requirements, test strategy, and commissioning stages.

## References

- [Generic Thermostat](https://www.home-assistant.io/integrations/generic_thermostat/)
- [Official Nord Pool integration](https://www.home-assistant.io/integrations/nordpool/)
- [Nord Pool price-action documentation](https://www.home-assistant.io/actions/nordpool.get_prices_for_date/)
- [Nordpool Diff — algorithm inspiration](https://github.com/jpulakka/nordpool_diff)

## Safety

This is an experimental software project, not a validated heating controller. Home Assistant must not be the sole safety layer for an electric heater or frost-sensitive storage space. Never allow the prototype and an existing thermostat to control the same relay at the same time.
