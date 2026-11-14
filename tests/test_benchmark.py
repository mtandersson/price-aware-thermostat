"""Tests for the SYNTHETIC benchmark; pure Python, no Home Assistant required."""

import unittest
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from unittest import mock

from benchmarks import run_benchmark
from benchmarks.fixtures import (
    Scenario,
    build_scenarios,
    hourly_prices,
    to_quarter_hours,
    utc_range,
)
from custom_components.price_aware_thermostat.optimizer import PriceInterval, optimize


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


class BenchmarkTests(unittest.TestCase):
    def test_same_seed_gives_identical_output(self):
        def small(seed):
            sc = [s for s in build_scenarios(seed) if s.name == "dst_spring"]
            return run_benchmark.render(run_benchmark.run_all(seed, sc), seed)

        self.assertEqual(small(5), small(5))

    def test_different_seed_changes_scenarios(self):
        a = build_scenarios(1)
        b = build_scenarios(2)
        self.assertNotEqual(a[0].true_prices, b[0].true_prices)

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


def one_day(day, price=0.5):
    return Scenario(
        "t",
        "t",
        [PriceInterval(p.start, p.end, price) for p in hourly_prices(day, 1, seed=1)],
        [],
        0.0,
    )


class SimulationCoreTests(unittest.TestCase):
    PARAMS = run_benchmark.ModelParams(loss_kw_per_k=0.0, initial_temperature_c=5.0)

    def test_golden_flat_price_energy_and_cost(self):
        # No loss: +2 kW * 5/60 h / 1.5 kWh/K = 0.1111 K per step. From 5.0 the
        # heater is on until the temperature exceeds 6.0 + 0.2, i.e. 11 steps,
        # then stays off.
        res = run_benchmark.simulate(
            one_day(date(2026, 1, 12)), run_benchmark.BASE, False, self.PARAMS
        )
        self.assertEqual(res.heater_on_steps, 11)
        self.assertAlmostEqual(res.energy_kwh, 11 * 2.0 * 5 / 60, places=9)
        self.assertAlmostEqual(res.cost_sek, res.energy_kwh * 0.5, places=9)
        self.assertAlmostEqual(res.final_c, 5.0 + 11 * (2.0 * 5 / 60 / 1.5), places=9)
        self.assertAlmostEqual(res.peak_c, res.final_c, places=9)

    def test_hysteresis_direction(self):
        sc = one_day(date(2026, 1, 12))
        for start, expect_on in ((5.7, True), (5.9, False), (6.1, False), (6.5, False)):
            params = replace(self.PARAMS, initial_temperature_c=start)
            res = run_benchmark.simulate(sc, run_benchmark.BASE, False, params)
            self.assertEqual(res.heater_on_steps > 0, expect_on, start)
        # Heating stops only above target + tolerance, never earlier.
        res = run_benchmark.simulate(sc, run_benchmark.BASE, False, self.PARAMS)
        self.assertGreater(res.final_c, 6.2)
        self.assertLess(res.final_c, 6.2 + 0.12)

    def test_step_counts_follow_real_elapsed_time(self):
        for day, steps in (
            (date(2026, 1, 12), 24 * 12),
            (date(2026, 10, 25), 25 * 12),
            (date(2026, 3, 29), 23 * 12),
        ):
            res = run_benchmark.simulate(one_day(day), run_benchmark.BASE, False)
            self.assertEqual(res.steps, steps, day)
        by = {s.name: s for s in build_scenarios(42)}
        self.assertEqual(
            run_benchmark.simulate(by["dst_fall"], run_benchmark.BASE, False).steps, 120 * 12 + 12
        )
        self.assertEqual(
            run_benchmark.simulate(by["dst_spring"], run_benchmark.BASE, False).steps, 120 * 12 - 12
        )
        self.assertEqual(
            run_benchmark.simulate(by["hourly"], run_benchmark.BASE, False).steps, 7 * 24 * 12
        )

    def test_optimizer_only_sees_visible_prices_in_gap_scenario(self):
        by = {s.name: s for s in build_scenarios(42)}
        gap = by["gap"]
        cfg = replace(run_benchmark.BASE, lookahead_minutes=180)
        seen = []
        real = run_benchmark.optimize

        def spy(now, prices, config):
            seen.append(list(prices))
            return real(now, prices, config)

        with mock.patch.object(run_benchmark, "optimize", spy):
            res = run_benchmark.simulate(gap, cfg, True)
        visible = set(gap.visible_prices)
        self.assertEqual(len(seen), len(gap.visible_prices))
        for prices in seen:
            self.assertTrue(set(prices) <= visible)
        self.assertEqual(res.optimizer_calls, len(gap.visible_prices))
        nogap = run_benchmark.simulate(by["hourly"], cfg, True)
        self.assertNotEqual(res.cost_sek, nogap.cost_sek)
        self.assertGreater(
            res.unavailable_share, 0.25
        )  # 6 of 24 h hidden, plus truncated forecasts
        self.assertLess(res.unavailable_share, 0.6)
        self.assertLess(nogap.unavailable_share, 0.05)

    def test_headline_rows_seed_42(self):
        by = {s.name: s for s in build_scenarios(42)}
        base = run_benchmark.BASE

        def cost(name, filt, la, sens, optimized=True):
            cfg = replace(
                base, filter_type=filt, lookahead_minutes=la, sensitivity_c_per_sek_per_kwh=sens
            )
            return run_benchmark.simulate(by[name], cfg, optimized).cost_sek

        self.assertAlmostEqual(cost("hourly", "triangle", 180, 3.0, False), 44.44, delta=0.005)
        self.assertAlmostEqual(cost("hourly", "triangle", 180, 3.0), 33.68, delta=0.005)
        self.assertAlmostEqual(cost("hourly", "rectangle", 180, 3.0), 30.88, delta=0.005)
        self.assertAlmostEqual(cost("quarter", "triangle", 60, 1.0), 41.47, delta=0.005)
        self.assertAlmostEqual(cost("dst_fall", "rectangle", 180, 3.0), 8.84, delta=0.005)
        self.assertAlmostEqual(cost("flat", "triangle", 360, 3.0), 42.14, delta=0.005)


if __name__ == "__main__":
    unittest.main()
