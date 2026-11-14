"""Unit tests for relay-command acknowledgment and retry safety."""

import unittest

from custom_components.price_aware_thermostat.relay_guard import RelayCommandGuard


class RelayCommandGuardTests(unittest.TestCase):
    def test_first_command_is_allowed(self):
        guard = RelayCommandGuard()
        self.assertTrue(guard.should_dispatch(True, False, now=10.0))

    def test_unacknowledged_command_is_not_repeated_immediately(self):
        guard = RelayCommandGuard(retry_seconds=120.0)
        guard.record_attempt(True, now=10.0)
        self.assertFalse(guard.should_dispatch(True, False, now=11.0))
        self.assertFalse(guard.should_dispatch(True, False, now=129.0))

    def test_retry_is_allowed_after_timeout(self):
        guard = RelayCommandGuard(retry_seconds=120.0)
        guard.record_attempt(True, now=10.0)
        self.assertTrue(guard.should_dispatch(True, False, now=130.0))

    def test_acknowledged_command_clears_pending_state(self):
        guard = RelayCommandGuard()
        guard.record_attempt(True, now=10.0)
        guard.acknowledge(False)
        self.assertIs(guard.pending_state, True)
        guard.acknowledge(True)
        self.assertIsNone(guard.pending_state)
        self.assertIsNone(guard.last_command_monotonic)
        self.assertFalse(guard.should_dispatch(True, True, now=11.0))

    def test_corrective_off_supersedes_pending_on(self):
        guard = RelayCommandGuard()
        guard.record_attempt(True, now=10.0)
        self.assertTrue(guard.should_dispatch(False, False, now=11.0))
        guard.record_attempt(False, now=11.0)
        self.assertFalse(guard.should_dispatch(False, False, now=12.0))

    def test_pending_off_is_not_overridden_when_heater_appears_on(self):
        guard = RelayCommandGuard()
        guard.record_attempt(False, now=10.0)
        self.assertFalse(guard.should_dispatch(True, True, now=11.0))

    def test_nonpositive_retry_is_rejected(self):
        with self.assertRaises(ValueError):
            RelayCommandGuard(retry_seconds=0)

    def test_failed_attempt_can_be_throttled(self):
        guard = RelayCommandGuard(retry_seconds=120)
        guard.record_attempt(False, now=10.0)
        self.assertFalse(guard.should_dispatch(False, True, now=11.0))
        self.assertTrue(guard.should_dispatch(False, True, now=130.0))


if __name__ == "__main__":
    unittest.main()
