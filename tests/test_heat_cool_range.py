"""Pure heat_cool direction selection and range price adjustment (unittest only)."""

import unittest

from custom_components.price_aware_thermostat.ac_control import (
    OutdoorLockout,
    RangeSelector,
    adjust_range,
)


class RangeSelectorTests(unittest.TestCase):
    def test_heats_below_low_cools_above_high_and_idles_between(self) -> None:
        sel = RangeSelector(changeover=0)
        self.assertEqual(sel.select(21.7, 22, 24, 0.3, 0.3, 0), "heat")
        self.assertEqual(sel.select(22.2, 22, 24, 0.3, 0.3, 1), "heat")  # hysteresis
        self.assertEqual(sel.select(22.3, 22, 24, 0.3, 0.3, 2), "off")
        self.assertEqual(sel.status, "idle")
        self.assertEqual(sel.select(24.2, 22, 24, 0.3, 0.3, 3), "off")
        self.assertEqual(sel.select(24.3, 22, 24, 0.3, 0.3, 4), "cool")
        self.assertEqual(sel.select(23.8, 22, 24, 0.3, 0.3, 5), "cool")
        self.assertEqual(sel.select(23.7, 22, 24, 0.3, 0.3, 6), "off")

    def test_never_switches_directly_and_waits_for_the_changeover(self) -> None:
        sel = RangeSelector(changeover=1800)
        self.assertEqual(sel.select(21.0, 22, 24, 0.3, 0.3, 0), "heat")
        # A sudden hot reading only ends heating; it never starts cooling.
        self.assertEqual(sel.select(25.0, 22, 24, 0.3, 0.3, 10), "off")
        self.assertEqual(sel.status, "changeover_wait")
        self.assertEqual(sel.select(25.0, 22, 24, 0.3, 0.3, 1809), "off")
        self.assertEqual(sel.select(25.0, 22, 24, 0.3, 0.3, 1810), "cool")
        self.assertEqual(sel.select(21.0, 22, 24, 0.3, 0.3, 1900), "off")
        self.assertEqual(sel.status, "changeover_wait")
        self.assertEqual(sel.select(21.0, 22, 24, 0.3, 0.3, 1900 + 1800), "heat")
        # The same direction may restart without waiting.
        sel.stop(4000)
        self.assertEqual(sel.select(21.0, 22, 24, 0.3, 0.3, 4001), "heat")

    def test_locked_direction_never_starts_and_the_other_is_not_used(self) -> None:
        sel = RangeSelector(changeover=0)
        self.assertEqual(sel.select(26.0, 22, 24, 0.3, 0.3, 0, cool_allowed=False), "off")
        self.assertEqual(sel.status, "outdoor_lockout")
        self.assertEqual(sel.select(20.0, 22, 24, 0.3, 0.3, 1, heat_allowed=False), "off")
        self.assertEqual(sel.status, "outdoor_lockout")
        self.assertEqual(sel.select(20.0, 22, 24, 0.3, 0.3, 2), "heat")
        self.assertEqual(sel.select(20.0, 22, 24, 0.3, 0.3, 3, heat_allowed=False), "off")
        self.assertEqual(sel.direction, "off")

    def test_resume_stop_and_validation(self) -> None:
        sel = RangeSelector()
        sel.resume("cool", 5)
        self.assertEqual((sel.direction, sel.last_active_at), ("cool", 5))
        sel.resume("off", 6)
        self.assertEqual(sel.direction, "cool")
        sel.stop(7)
        sel.stop(8)
        self.assertEqual((sel.direction, sel.last_active_at), ("off", 7))
        with self.assertRaises(ValueError):
            sel.select(22.0, 24, 22, 0.3, 0.3, 0)
        with self.assertRaises(ValueError):
            sel.select(float("nan"), 22, 24, 0.3, 0.3, 0)
        with self.assertRaises(ValueError):
            RangeSelector(changeover=-1)


class AdjustRangeTests(unittest.TestCase):
    def test_adjust_range_keeps_the_gap(self) -> None:
        self.assertEqual(adjust_range(22, 24, 21.2, 24.8, 1.0), (21.2, 24.8, False))
        self.assertEqual(adjust_range(22, 24, 22.8, 23.2, 1.0), (22.5, 23.5, True))
        self.assertEqual(adjust_range(22, 24, 22.2, 23.8, 1.0), (22.2, 23.8, False))
        # A range exactly the gap wide admits no inward adjustment.
        self.assertEqual(adjust_range(22, 23, 22.4, 22.6, 1.0), (22.0, 23.0, True))


class AsymmetricFailSafeTests(unittest.TestCase):
    def test_missing_reading_locks_cooling_but_never_heating(self) -> None:
        lock = OutdoorLockout(cool_min=15.0, heat_max=15.0, hysteresis=1.0)
        self.assertTrue(lock.cool_locked)  # no reading yet: cooling is not allowed
        lock.update(15.5)
        self.assertTrue(lock.cool_locked)  # starts locked: needs 16 °C to release
        lock.update(30.0)
        self.assertTrue(lock.heat_locked)
        self.assertFalse(lock.cool_locked)
        lock.update(None)
        self.assertTrue(lock.cool_locked)
        self.assertFalse(lock.heat_locked)
        lock.update(15.5)  # inside the hysteresis band after a fault
        self.assertTrue(lock.cool_locked)
        disabled = OutdoorLockout(heat_max=15.0)
        disabled.update(None)
        self.assertFalse(disabled.cool_locked or disabled.heat_locked)


if __name__ == "__main__":
    unittest.main()
