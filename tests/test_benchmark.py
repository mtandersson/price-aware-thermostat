"""Tests for the SYNTHETIC benchmark; pure Python, no Home Assistant required."""

import unittest
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

from benchmarks import run_benchmark
from benchmarks.fixtures import (
    build_scenarios,
    hourly_prices,
    to_quarter_hours,
    utc_range,
)
from custom_components.price_aware_thermostat.optimizer import optimize


def contiguous(prices):
    return all(a.end == b.start for a, b in zip(prices, prices[1:], strict=False))


class EquivalenceTests(unittest.TestCase):
    def test_quarter_matches_hourly_when_anchor_coincides(self):
        hourly = hourly_prices(date(2026, 1, 12), 3, seed=7)
        quarter = to_quarter_hours(hourly)
        for filt in ("rectangle", "triangle"):
            cfg = replace(
                run_benchmark.BASE,
                filter_type=filt,
                lookahead_minutes=180,
                deadband_sek_per_kwh=0.0,
            )
            # Last quarter of an hour: the current interval ends on the hour,
            # so both resolutions anchor the forecast at the same instant.
            for p in hourly[:-4]:
                now = p.start + timedelta(minutes=45)
                a = optimize(now, hourly, cfg)
                b = optimize(now, quarter, cfg)
                self.assertAlmostEqual(a.effective_target_c, b.effective_target_c, places=9)
                self.assertAlmostEqual(
                    a.future_weighted_price_sek_per_kwh,
                    b.future_weighted_price_sek_per_kwh,
                    places=9,
                )


class DstTests(unittest.TestCase):
    def test_day_lengths_are_real_elapsed_time(self):
        start, end = utc_range(date(2026, 10, 25), 1)
        self.assertEqual(end - start, timedelta(hours=25))
        start, end = utc_range(date(2026, 3, 29), 1)
        self.assertEqual(end - start, timedelta(hours=23))

    def test_series_cover_dst_days_without_gaps_or_overlap(self):
        for day, hours in ((date(2026, 10, 25), 25), (date(2026, 3, 29), 23)):
            hourly = hourly_prices(day, 1, seed=1)
            quarter = to_quarter_hours(hourly)
            self.assertEqual(len(hourly), hours)
            self.assertEqual(len(quarter), hours * 4)
            self.assertTrue(contiguous(hourly))
            self.assertTrue(contiguous(quarter))
            self.assertEqual(quarter[-1].end - quarter[0].start, timedelta(hours=hours))

    def test_lookahead_across_dst_counts_real_time(self):
        prices = hourly_prices(date(2026, 10, 24), 3, seed=3)
        cfg = replace(run_benchmark.BASE, lookahead_minutes=360)
        # 00:30 UTC on 25 Oct is 02:30 CEST; the next 6 real hours cross the
        # 03:00 -> 02:00 fallback and must still be fully covered.
        res = optimize(datetime(2026, 10, 25, 0, 30, tzinfo=UTC), prices, cfg)
        self.assertEqual(res.future_coverage_fraction, 1.0)
        self.assertNotEqual(res.reason, "price_unavailable")

    def test_simulation_runs_for_real_elapsed_time(self):
        sc = next(s for s in build_scenarios(42) if s.name == "dst_fall")
        expected_h = (sc.true_prices[-1].end - sc.true_prices[0].start).total_seconds() / 3600
        self.assertEqual(expected_h, 5 * 24 + 1)
        res = run_benchmark.simulate(sc, run_benchmark.BASE, False)
        self.assertLessEqual(res.runtime_h, expected_h)


class BenchmarkTests(unittest.TestCase):
    def test_same_seed_gives_identical_output(self):
        first = run_benchmark.render(run_benchmark.run_all(5), 5)
        second = run_benchmark.render(run_benchmark.run_all(5), 5)
        self.assertEqual(first, second)

    def test_different_seed_changes_output(self):
        self.assertNotEqual(
            run_benchmark.render(run_benchmark.run_all(1), 1).split("\n", 3)[3],
            run_benchmark.render(run_benchmark.run_all(2), 2).split("\n", 3)[3],
        )

    def test_fixed_baseline_never_adjusts_target(self):
        for sc in build_scenarios(42):
            res = run_benchmark.simulate(sc, run_benchmark.BASE, False)
            self.assertEqual(res.optimizer_calls, 0)
            self.assertEqual(res.adjusted_share, 0.0)
            self.assertEqual(res.unavailable_share, 0.0)

    def test_gap_scenario_loses_forecast_but_keeps_true_prices(self):
        by = {s.name: s for s in build_scenarios(42)}
        self.assertLess(len(by["gap"].visible_prices), len(by["gap"].true_prices))
        self.assertEqual(by["gap"].true_prices, by["hourly"].true_prices)

    def test_negative_scenario_has_negative_prices(self):
        by = {s.name: s for s in build_scenarios(42)}
        self.assertTrue(any(p.price_sek_per_kwh < 0 for p in by["negative"].true_prices))

    def test_flat_control_is_not_adjusted(self):
        sc = next(s for s in build_scenarios(42) if s.name == "flat")
        cfg = replace(run_benchmark.BASE, lookahead_minutes=180)
        self.assertEqual(run_benchmark.simulate(sc, cfg, True).adjusted_share, 0.0)


if __name__ == "__main__":
    unittest.main()
