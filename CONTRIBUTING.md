# Contributing

Thanks for your interest. This is an experimental project; please open an issue
before starting larger changes.

## Safety first

Changes that affect relay switching, stale-sensor handling, shadow mode or
temperature limits are safety-relevant. Describe the failure modes you
considered and add tests for them.

## Development

See the *Developer workflow* section of the [README](README.md). Before opening
a pull request, run:

```bash
pytest -q tests --cov=custom_components.price_aware_thermostat --cov-report=term-missing
ruff check custom_components/price_aware_thermostat tests/integration
mypy custom_components/price_aware_thermostat
```

Use [Conventional Commits](https://www.conventionalcommits.org/) style titles
(`feat:`, `fix:`, `docs:`, `test:`, `chore:`). By contributing you agree that
your work is licensed under the MIT license.
