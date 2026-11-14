"""Unit tests for the pure optimizer; no Home Assistant installation required."""

import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from custom_components.price_aware_thermostat.optimizer import (
    OptimizerConfig,
    PriceInterval,
    normalize_nordpool_response,
    optimize,
)

UTC = timezone.utc
T0 = datetime(2026, 11, 14, 10, 0, tzinfo=UTC)


def intervals(prices, minutes=15, start=T0):
    return [
        PriceInterval(
            start + timedelta(minutes=minutes * i),
            start + timedelta(minutes=minutes * (i + 1)),
            value,
        )
        for i, value in enumerate(prices)
    ]


class OptimizerTests(unittest.TestCase):
    def setUp(self):
        self.cfg = OptimizerConfig(
            base_temperature=6,
            minimum_temperature=5.3,
            maximum_temperature=7.5,
            lookahead_minutes=60,
            deadband_sek_per_kwh=0.1,
            sensitivity_c_per_sek_per_kwh=2.0,
        )

    def test_cheap_now_preheats(self):
        result = optimize(T0 + timedelta(minutes=1), intervals([0.1, 1, 1, 1, 1]), self.cfg)
        self.assertEqual("preheat", result.reason)
        self.assertEqual(7.5, result.effective_target_c)

    def test_expensive_now_reduces_but_not_below_minimum(self):
        result = optimize(T0 + timedelta(minutes=1), intervals([2, 0.1, 0.1, 0.1, 0.1]), self.cfg)
        self.assertEqual("expensive", result.reason)
        self.assertEqual(5.3, result.effective_target_c)

    def test_small_price_difference_ignored(self):
        result = optimize(T0, intervals([0.5, 0.51, 0.51, 0.51, 0.51]), self.cfg)
        self.assertEqual("normal", result.reason)
        self.assertEqual(6.0, result.effective_target_c)

    def test_no_future_prices_falls_back(self):
        result = optimize(T0, intervals([0.5]), self.cfg)
        self.assertEqual("price_unavailable", result.reason)
        self.assertEqual(6.0, result.effective_target_c)

    def test_negative_prices_supported(self):
        result = optimize(T0, intervals([-1, 0, 0, 0, 0]), self.cfg)
        self.assertEqual("preheat", result.reason)

    def test_invalid_now_is_rejected(self):
        with self.assertRaises(ValueError):
            optimize(datetime(2026, 11, 14, 10, 0), intervals([1, 1, 1, 1, 1]), self.cfg)

    def test_forecast_gap_causes_fallback(self):
        data = intervals([0.5, 1]) + intervals([1, 1, 1], start=T0 + timedelta(minutes=45))
        result = optimize(T0, data, self.cfg)
        self.assertEqual("price_unavailable", result.reason)

    def test_partial_contiguous_forecast_above_threshold_allowed(self):
        data = intervals([0.5, 1, 1, 1])
        result = optimize(T0, data, self.cfg)
        self.assertEqual("preheat", result.reason)
        self.assertAlmostEqual(0.75, result.future_coverage_fraction)

    def test_mixed_hourly_quarterly_matches_uniform_resolution(self):
        uniform = intervals([0.5, 1, 1, 1, 1])
        mixed = [uniform[0], PriceInterval(T0 + timedelta(minutes=15), T0 + timedelta(minutes=75), 1)]
        self.assertAlmostEqual(
            optimize(T0, uniform, self.cfg).future_weighted_price_sek_per_kwh,
            optimize(T0, mixed, self.cfg).future_weighted_price_sek_per_kwh,
        )

    def test_rectangle_vs_triangle_emphasizes_nearer_interval(self):
        data = intervals([0.5, 2, 0, 0, 0])
        rectangle = optimize(T0, data, OptimizerConfig(filter_type="rectangle", **self._config_other()))
        triangle = optimize(T0, data, OptimizerConfig(filter_type="triangle", **self._config_other()))
        self.assertGreater(
            triangle.future_weighted_price_sek_per_kwh,
            rectangle.future_weighted_price_sek_per_kwh,
        )

    def _config_other(self):
        return dict(
            base_temperature=6,
            minimum_temperature=5.3,
            maximum_temperature=7.5,
            lookahead_minutes=60,
            deadband_sek_per_kwh=0.1,
            sensitivity_c_per_sek_per_kwh=2,
        )

    def test_swedish_dst_fall_back_interval_not_double_counted(self):
        sweden = ZoneInfo("Europe/Stockholm")
        start = datetime(2026, 10, 25, 0, 30, tzinfo=UTC)
        data = intervals([0.5, 1, 1, 1, 1], start=start)
        result = optimize(start.astimezone(sweden), data, self.cfg)
        self.assertEqual("preheat", result.reason)
        self.assertAlmostEqual(1.0, result.future_coverage_fraction)

    def test_overnight_utc_continuity(self):
        start = datetime(2026, 11, 14, 23, 45, tzinfo=UTC)
        data = intervals([0.5, 1, 1, 1, 1], start=start)
        result = optimize(start, data, self.cfg)
        self.assertEqual("preheat", result.reason)

    def test_nord_pool_price_mwh_to_kwh(self):
        raw = {
            "SE4": [
                {
                    "start": "2026-11-14T10:00:00+00:00",
                    "end": "2026-11-14T10:15:00+00:00",
                    "price": 320.5,
                }
            ]
        }
        parsed = normalize_nordpool_response(raw, "SE4")
        self.assertAlmostEqual(0.3205, parsed[0].price_sek_per_kwh)

    def test_overlap_rejected(self):
        data = [
            PriceInterval(T0, T0 + timedelta(minutes=30), 1),
            PriceInterval(T0 + timedelta(minutes=15), T0 + timedelta(minutes=45), 1),
        ]
        with self.assertRaises(ValueError):
            optimize(T0, data, self.cfg)

    def test_invalid_market_area_is_rejected(self):
        with self.assertRaises(ValueError):
            normalize_nordpool_response({"SE4": []}, "SE3")

    def test_missing_current_price_falls_back(self):
        result = optimize(T0 - timedelta(minutes=1), intervals([0.5, 1, 1, 1, 1]), self.cfg)
        self.assertEqual("price_unavailable", result.reason)

    def test_invalid_temperature_bounds_rejected(self):
        with self.assertRaises(ValueError):
            OptimizerConfig(base_temperature=4, minimum_temperature=5, maximum_temperature=8)


if __name__ == "__main__":
    unittest.main()
