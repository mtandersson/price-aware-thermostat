"""Input validation for price optimization and safe thermostat parameters."""

import unittest
from datetime import UTC, datetime, timedelta

from custom_components.price_aware_thermostat.optimizer import (
    OptimizerConfig,
    PriceInterval,
)


class PriceValidationTests(unittest.TestCase):
    """Reject invalid settings before the controller can use them."""

    def test_rejects_timezone_naive_price_intervals(self):
        aware = datetime(2026, 11, 14, 10, tzinfo=UTC)
        naive = aware.replace(tzinfo=None)
        for start, end in (
            (naive, aware + timedelta(minutes=15)),
            (aware, naive + timedelta(minutes=15)),
        ):
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                PriceInterval(start, end, 0.5)

    def test_rejects_nonfinite_configuration(self):
        invalid = (
            ("base_temperature", float("nan")),
            ("maximum_temperature", float("inf")),
            ("deadband_sek_per_kwh", float("-inf")),
        )
        for field, value in invalid:
            with self.subTest(field=field), self.assertRaises(ValueError):
                OptimizerConfig(**{field: value})

    def test_rejects_too_short_lookahead(self):
        with self.assertRaises(ValueError):
            OptimizerConfig(lookahead_minutes=10)

    def test_rejects_unknown_filter(self):
        with self.assertRaises(ValueError):
            OptimizerConfig(filter_type="unknown")  # type: ignore[arg-type]

    def test_rejects_negative_deadband_and_sensitivity(self):
        for field in ("deadband_sek_per_kwh", "sensitivity_c_per_sek_per_kwh"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                OptimizerConfig(**{field: -0.01})

    def test_rejects_invalid_coverage_fraction(self):
        for fraction in (-0.1, 0, 1.1):
            with self.subTest(fraction=fraction), self.assertRaises(ValueError):
                OptimizerConfig(min_coverage_fraction=fraction)

    def test_rejects_nonfinite_price(self):
        start = datetime(2026, 11, 14, 10, tzinfo=UTC)
        for price in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(price=price), self.assertRaises(ValueError):
                PriceInterval(start, start + timedelta(minutes=15), price)


if __name__ == "__main__":
    unittest.main()
