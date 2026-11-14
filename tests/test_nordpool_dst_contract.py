"""Contract tests for 15-minute Nord Pool intervals and Swedish DST transitions.

These pure tests do not require Home Assistant, network access, or physical
actuators. All comparisons use real UTC instants, not naive wall-clock times.
"""

import unittest
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from custom_components.price_aware_thermostat.optimizer import (
    OptimizerConfig,
    normalize_nordpool_response,
    optimize,
)


def _interval(start: str, end: str, price: float = 250.0) -> dict[str, str | float]:
    """Build an official Nord Pool action-like price interval."""
    return {"start": start, "end": end, "price": price}


class NordPoolIntervalContractTests(unittest.TestCase):
    """Reject malformed interval series before storing a price forecast."""

    def test_unordered_quarter_hour_prices_are_sorted_by_instant(self):
        raw = {
            "SE4": [
                _interval("2026-11-14T10:15:00Z", "2026-11-14T10:30:00Z", 310),
                _interval("2026-11-14T10:00:00Z", "2026-11-14T10:15:00Z", 150),
            ]
        }
        result = normalize_nordpool_response(raw, "SE4")
        self.assertEqual([0.15, 0.31], [p.price_sek_per_kwh for p in result])
        self.assertEqual(datetime(2026, 11, 14, 10, 0, tzinfo=UTC), result[0].start)
        self.assertEqual(result[0].end, result[1].start)

    def test_duplicate_intervals_must_not_enter_price_cache(self):
        record = _interval("2026-11-14T10:00:00Z", "2026-11-14T10:15:00Z")
        with self.assertRaisesRegex(ValueError, "Overlapping"):
            normalize_nordpool_response({"SE4": [record, record]}, "SE4")

    def test_partially_overlapping_quarter_hour_intervals_rejected(self):
        raw = {
            "SE4": [
                _interval("2026-11-14T10:00:00Z", "2026-11-14T10:20:00Z"),
                _interval("2026-11-14T10:15:00Z", "2026-11-14T10:30:00Z"),
            ]
        }
        with self.assertRaisesRegex(ValueError, "Overlapping"):
            normalize_nordpool_response(raw, "SE4")

    def test_autumn_clock_rollback_is_not_a_false_overlap(self):
        # Europe/Stockholm returns from CEST (UTC+2) to CET (UTC+1).
        # The second interval begins at the earlier-looking local 02:00,
        # but the underlying instants are strictly contiguous.
        raw = {
            "SE4": [
                _interval(
                    "2026-10-25T02:00:00+01:00",
                    "2026-10-25T02:15:00+01:00",
                    190,
                ),
                _interval(
                    "2026-10-25T02:45:00+02:00",
                    "2026-10-25T02:00:00+01:00",
                    140,
                ),
            ]
        }
        values = normalize_nordpool_response(raw, "SE4")
        self.assertEqual([0.14, 0.19], [v.price_sek_per_kwh for v in values])
        self.assertEqual(values[0].end.astimezone(UTC), values[1].start.astimezone(UTC))
        stockholm = ZoneInfo("Europe/Stockholm")
        self.assertEqual(2, values[0].start.astimezone(stockholm).hour)
        self.assertEqual(2, values[1].start.astimezone(stockholm).hour)

    def test_autumn_repeated_hour_overlap_is_rejected_by_utc(self):
        raw = {
            "SE4": [
                _interval("2026-10-25T02:45:00+02:00", "2026-10-25T02:15:00+01:00"),
                _interval("2026-10-25T02:00:00+01:00", "2026-10-25T02:30:00+01:00"),
            ]
        }
        with self.assertRaisesRegex(ValueError, "Overlapping"):
            normalize_nordpool_response(raw, "SE4")

    def test_spring_forward_intervals_are_contiguous_in_utc(self):
        # The skipped local hour does not mean there is a missing price.
        raw = {
            "SE4": [
                _interval("2026-03-29T01:45:00+01:00", "2026-03-29T03:00:00+02:00"),
                _interval("2026-03-29T03:00:00+02:00", "2026-03-29T03:15:00+02:00"),
            ]
        }
        intervals = normalize_nordpool_response(raw, "SE4")
        self.assertEqual(900.0, (intervals[0].end - intervals[0].start).total_seconds())
        self.assertEqual(intervals[0].end.astimezone(UTC), intervals[1].start.astimezone(UTC))

    def test_normalized_utc_series_drives_optimizer_without_double_counting(self):
        starts = [
            "2026-10-25T02:30:00+02:00",
            "2026-10-25T02:45:00+02:00",
            "2026-10-25T02:00:00+01:00",
            "2026-10-25T02:15:00+01:00",
            "2026-10-25T02:30:00+01:00",
        ]
        ends = starts[1:] + ["2026-10-25T02:45:00+01:00"]
        data = [_interval(s, e, 100 if i == 0 else 500) for i, (s, e) in enumerate(zip(starts, ends, strict=True))]
        prices = normalize_nordpool_response({"SE4": list(reversed(data))}, "SE4")
        config = OptimizerConfig(
            base_temperature=6.0,
            minimum_temperature=5.0,
            maximum_temperature=8.0,
            lookahead_minutes=60,
            deadband_sek_per_kwh=0.0,
            sensitivity_c_per_sek_per_kwh=1.0,
        )
        current = datetime.fromisoformat(starts[0])
        result = optimize(current, prices, config)
        self.assertEqual("preheat", result.reason)
        self.assertAlmostEqual(1.0, result.future_coverage_fraction)
        self.assertAlmostEqual(0.4, result.price_difference_sek_per_kwh)


if __name__ == "__main__":
    unittest.main()
