"""Tests for the pure hysteresis controller."""

import unittest
from datetime import datetime, timedelta, timezone

from custom_components.price_aware_thermostat.control import (
    desired_heater_state,
    is_temperature_sample_fresh,
)


class HeatingControlTests(unittest.TestCase):
    def test_switch_on_below_cold_threshold(self):
        self.assertTrue(desired_heater_state(5.5, 6.0, False, 0.3, 0.3))

    def test_switch_off_above_hot_threshold(self):
        self.assertFalse(desired_heater_state(6.5, 6.0, True, 0.3, 0.3))

    def test_switch_keeps_on_inside_hysteresis(self):
        self.assertTrue(desired_heater_state(6.1, 6.0, True, 0.3, 0.3))

    def test_switch_keeps_off_inside_hysteresis(self):
        self.assertFalse(desired_heater_state(5.8, 6.0, False, 0.3, 0.3))

    def test_missing_sensor_returns_none(self):
        self.assertIsNone(desired_heater_state(None, 6.0, True, 0.3, 0.3))

    def test_invalid_sensor_returns_none(self):
        self.assertIsNone(desired_heater_state(float("nan"), 6.0, False, 0.3, 0.3))

    def test_invalid_target_returns_none(self):
        self.assertIsNone(desired_heater_state(5.0, float("inf"), False, 0.3, 0.3))

    def test_exact_cold_threshold_turns_on(self):
        self.assertTrue(desired_heater_state(5.7, 6.0, False, 0.3, 0.3))

    def test_invalid_tolerance_is_rejected(self):
        with self.assertRaises(ValueError):
            desired_heater_state(5.0, 6.0, False, -0.1, 0.3)


    def test_recent_temperature_report_is_fresh(self):
        now = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
        self.assertTrue(
            is_temperature_sample_fresh(
                now - timedelta(minutes=3), now, timedelta(minutes=60)
            )
        )

    def test_stale_temperature_report_is_rejected(self):
        now = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
        self.assertFalse(
            is_temperature_sample_fresh(
                now - timedelta(minutes=61), now, timedelta(minutes=60)
            )
        )

    def test_missing_temperature_report_timestamp_is_rejected(self):
        now = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
        self.assertFalse(
            is_temperature_sample_fresh(None, now, timedelta(minutes=60))
        )

    def test_naive_temperature_report_timestamp_is_rejected(self):
        now = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
        self.assertFalse(
            is_temperature_sample_fresh(
                datetime(2026, 11, 14, 12), now, timedelta(minutes=60)
            )
        )

    def test_future_temperature_report_timestamp_is_rejected(self):
        now = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
        self.assertFalse(
            is_temperature_sample_fresh(
                now + timedelta(minutes=1), now, timedelta(minutes=60)
            )
        )


if __name__ == "__main__":
    unittest.main()
