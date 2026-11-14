"""Pure tests for heating and cooling price adjustment (issue #21)."""

import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from custom_components.price_aware_thermostat.optimizer import (
    OptimizerConfig,
    PriceInterval,
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


class HeatCoolTests(unittest.TestCase):
    def setUp(self):
        self.heat = OptimizerConfig(
            base_temperature=21,
            minimum_temperature=19,
            maximum_temperature=24,
            lookahead_minutes=60,
            deadband_sek_per_kwh=0.1,
            sensitivity_c_per_sek_per_kwh=0.5,
            hvac_mode="heat",
        )
        self.cool = replace(self.heat, hvac_mode="cool")
        self.now = T0 + timedelta(minutes=1)

    def test_cheap_now_raises_heating_and_lowers_cooling_target(self):
        prices = intervals([0.2, 1.2, 1.2, 1.2, 1.2])
        heat = optimize(self.now, prices, self.heat)
        cool = optimize(self.now, prices, self.cool)
        self.assertEqual("preheat", heat.reason)
        self.assertAlmostEqual(21.45, heat.effective_target_c)
        self.assertEqual("precool", cool.reason)
        self.assertAlmostEqual(20.55, cool.effective_target_c)

    def test_expensive_now_reverses_both_directions(self):
        prices = intervals([1.2, 0.2, 0.2, 0.2, 0.2])
        heat = optimize(self.now, prices, self.heat)
        cool = optimize(self.now, prices, self.cool)
        self.assertEqual("expensive", heat.reason)
        self.assertLess(heat.effective_target_c, 21)
        self.assertEqual("expensive", cool.reason)
        self.assertGreater(cool.effective_target_c, 21)

    def test_adjustments_are_mirror_images(self):
        prices = intervals([0.3, 0.9, 0.9, 0.9, 0.9])
        heat = optimize(self.now, prices, self.heat)
        cool = optimize(self.now, prices, self.cool)
        self.assertAlmostEqual(
            heat.effective_target_c - 21, 21 - cool.effective_target_c
        )

    def test_equal_prices_and_deadband_leave_base_target(self):
        for cfg in (self.heat, self.cool):
            for prices in ([0.5] * 5, [0.5, 0.55, 0.55, 0.55, 0.55]):
                result = optimize(self.now, intervals(prices), cfg)
                self.assertEqual("normal", result.reason)
                self.assertEqual(21, result.effective_target_c)
                self.assertFalse(result.clamped)

    def test_comfort_limits_clamp_and_are_reported(self):
        prices = intervals([0.0, 9.0, 9.0, 9.0, 9.0])
        heat = optimize(self.now, prices, self.heat)
        cool = optimize(self.now, prices, self.cool)
        self.assertEqual(24, heat.effective_target_c)
        self.assertTrue(heat.clamped)
        self.assertGreater(heat.requested_adjustment_c, 3)
        self.assertEqual(19, cool.effective_target_c)
        self.assertTrue(cool.clamped)
        self.assertLess(cool.requested_adjustment_c, -3)

    def test_max_deviation_limits_both_directions(self):
        limited_heat = replace(self.heat, max_deviation_c=0.5)
        limited_cool = replace(self.cool, max_deviation_c=0.5)
        cheap = intervals([0.0, 9.0, 9.0, 9.0, 9.0])
        pricey = intervals([9.0, 0.0, 0.0, 0.0, 0.0])
        self.assertEqual(21.5, optimize(self.now, cheap, limited_heat).effective_target_c)
        self.assertEqual(20.5, optimize(self.now, pricey, limited_heat).effective_target_c)
        self.assertEqual(20.5, optimize(self.now, cheap, limited_cool).effective_target_c)
        self.assertEqual(21.5, optimize(self.now, pricey, limited_cool).effective_target_c)

    def test_limits_removing_the_adjustment_remain_visible(self):
        # Base equals the maximum, so a heating increase is clamped away.
        cfg = replace(self.heat, base_temperature=24)
        result = optimize(self.now, intervals([0.0, 9.0, 9.0, 9.0, 9.0]), cfg)
        self.assertEqual("normal", result.reason)
        self.assertEqual(24, result.effective_target_c)
        self.assertTrue(result.clamped)
        self.assertGreater(result.requested_adjustment_c, 0)

    def test_setpoint_change_moves_adjusted_target_with_base(self):
        prices = intervals([0.2, 1.2, 1.2, 1.2, 1.2])
        first = optimize(self.now, prices, self.heat)
        moved = optimize(self.now, prices, replace(self.heat, base_temperature=22))
        self.assertAlmostEqual(first.effective_target_c + 1, moved.effective_target_c)

    def test_negative_prices_follow_the_same_signs(self):
        prices = intervals([-0.5, 0.6, 0.6, 0.6, 0.6])
        self.assertEqual("preheat", optimize(self.now, prices, self.heat).reason)
        self.assertEqual("precool", optimize(self.now, prices, self.cool).reason)

    def test_forecast_gap_and_missing_coverage_fall_back_to_base(self):
        gapped = intervals([0.2]) + intervals([1.2, 1.2], start=T0 + timedelta(hours=2))
        for cfg in (self.heat, self.cool):
            result = optimize(self.now, gapped, cfg)
            self.assertEqual("price_unavailable", result.reason)
            self.assertEqual(21, result.effective_target_c)
            self.assertEqual(0.0, result.requested_adjustment_c)
            self.assertFalse(result.clamped)

    def test_mixed_interval_lengths_match_uniform_data(self):
        quarter = intervals([0.2, 1.2, 1.2, 1.2, 1.2, 1.2])
        mixed = intervals([0.2], start=T0) + [
            PriceInterval(T0 + timedelta(minutes=15), T0 + timedelta(minutes=75), 1.2)
        ]
        for cfg in (self.heat, self.cool):
            self.assertAlmostEqual(
                optimize(self.now, quarter, cfg).effective_target_c,
                optimize(self.now, mixed, cfg).effective_target_c,
            )

    def test_hourly_and_quarter_hour_data_are_equivalent(self):
        hourly = intervals([0.2, 1.2, 1.2], minutes=60)
        quarter = intervals([0.2] * 4 + [1.2] * 8)
        # In the final quarter of the first hour both resolutions share the
        # same current price and the same forecast anchor at 11:00.
        now = T0 + timedelta(minutes=50)
        for cfg in (self.heat, self.cool):
            self.assertAlmostEqual(
                optimize(now, hourly, cfg).effective_target_c,
                optimize(now, quarter, cfg).effective_target_c,
            )

    def test_changing_mode_recomputes_instead_of_reusing_a_target(self):
        prices = intervals([0.2, 1.2, 1.2, 1.2, 1.2])
        heat = optimize(self.now, prices, self.heat).effective_target_c
        cool = optimize(self.now, prices, self.cool).effective_target_c
        back = optimize(self.now, prices, self.heat).effective_target_c
        self.assertGreater(heat, 21)
        self.assertLess(cool, 21)
        self.assertEqual(heat, back)

    def test_default_mode_is_unchanged_heating(self):
        legacy = OptimizerConfig(
            base_temperature=6, minimum_temperature=5.3, maximum_temperature=7.5,
            lookahead_minutes=60, deadband_sek_per_kwh=0.1, sensitivity_c_per_sek_per_kwh=2.0,
        )
        self.assertEqual("heat", legacy.hvac_mode)
        result = optimize(self.now, intervals([0.1, 1, 1, 1, 1]), legacy)
        self.assertEqual("preheat", result.reason)
        self.assertEqual(7.5, result.effective_target_c)

    def test_invalid_parameters_are_rejected(self):
        with self.assertRaises(ValueError):
            replace(self.heat, hvac_mode="auto")  # type: ignore[arg-type]
        for bad in (-0.1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                replace(self.heat, max_deviation_c=bad)


if __name__ == "__main__":
    unittest.main()
