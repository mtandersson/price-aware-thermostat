"""Pure tests for heat-pump/AC control logic (issue #23); no Home Assistant needed."""

import unittest

from custom_components.price_aware_thermostat.ac_control import (
    EXHAUSTED_COOLDOWN,
    OFF_EXHAUSTED_COOLDOWN,
    MIN_COMMAND_INTERVAL,
    RETRY_SECONDS,
    BackendLimits,
    Command,
    CommandMemory,
    Intent,
    Observation,
    WindowGuard,
    aggregate_contacts,
    cooling_demand,
    demand,
    heating_demand,
    native_target,
    plan_next_command,
)

LIMITS = BackendLimits(minimum=10, maximum=31, step=0.5)
MODES = ("heat", "cool", "off")
PAUSE = 300.0


def obs(mode, target=None, available=True, modes=MODES):
    return Observation(available, mode, target, modes)


class NativeTargetTests(unittest.TestCase):
    def test_compensation_is_signed_and_per_call(self):
        self.assertEqual(23.0, native_target(21.0, 2.0, LIMITS))
        self.assertEqual(19.0, native_target(21.0, -2.0, LIMITS))

    def test_rounds_to_device_step(self):
        self.assertEqual(21.5, native_target(21.3, 0, LIMITS))
        self.assertEqual(21.0, native_target(21.2, 0, LIMITS))
        self.assertEqual(21.5, native_target(21.25, 0, LIMITS))

    def test_grid_is_measured_from_minimum(self):
        offset = BackendLimits(minimum=16.0, maximum=30.0, step=1.0)
        self.assertEqual(22.0, native_target(21.6, 0, offset))

    def test_clamps_to_limits(self):
        self.assertEqual(31.0, native_target(30, 5, LIMITS))
        self.assertEqual(10.0, native_target(11, -5, LIMITS))

    def test_rejects_non_finite_input_and_bad_limits(self):
        with self.assertRaises(ValueError):
            native_target(float("nan"), 0, LIMITS)
        with self.assertRaises(ValueError):
            BackendLimits(30, 10, 0.5)
        with self.assertRaises(ValueError):
            BackendLimits(10, 30, 0)


class DemandTests(unittest.TestCase):
    def test_heating_hysteresis(self):
        # Start at or below target - cold (20.7); stop at or above target + hot (21.3).
        self.assertFalse(heating_demand(20.71, 21, False, 0.3, 0.3))
        self.assertTrue(heating_demand(20.69, 21, False, 0.3, 0.3))
        self.assertTrue(heating_demand(21.29, 21, True, 0.3, 0.3))
        self.assertFalse(heating_demand(21.31, 21, True, 0.3, 0.3))

    def test_cooling_hysteresis_mirrors_heating(self):
        # Start at or above target + hot (21.3); stop at or below target - cold (20.7).
        self.assertFalse(cooling_demand(21.29, 21, False, 0.3, 0.3))
        self.assertTrue(cooling_demand(21.31, 21, False, 0.3, 0.3))
        self.assertTrue(cooling_demand(20.71, 21, True, 0.3, 0.3))
        self.assertFalse(cooling_demand(20.69, 21, True, 0.3, 0.3))

    def test_invalid_reading_is_none_and_off_mode_has_no_demand(self):
        for fn in (heating_demand, cooling_demand):
            self.assertIsNone(fn(None, 21, False, 0.3, 0.3))
            self.assertIsNone(fn(float("nan"), 21, False, 0.3, 0.3))
        self.assertFalse(demand("off", 5, 21, True, 0.3, 0.3))
        with self.assertRaises(ValueError):
            heating_demand(20, 21, False, -1, 0.3)


class WindowTests(unittest.TestCase):
    def test_brief_opening_does_not_pause(self):
        guard = WindowGuard(open_delay=120, close_delay=60)
        self.assertFalse(guard.update(True, 0))
        self.assertFalse(guard.update(True, 119))
        self.assertFalse(guard.update(False, 120))
        self.assertFalse(guard.update(True, 200))
        self.assertFalse(guard.update(True, 319))

    def test_stable_open_pauses_and_stable_closure_resumes(self):
        guard = WindowGuard(open_delay=120, close_delay=60)
        guard.update(True, 0)
        self.assertTrue(guard.update(True, 120))
        self.assertTrue(guard.update(False, 130))
        self.assertTrue(guard.update(False, 189))
        self.assertFalse(guard.update(False, 190))

    def test_reopening_during_close_delay_restarts_it(self):
        guard = WindowGuard(open_delay=0, close_delay=60)
        self.assertTrue(guard.update(True, 0))
        guard.update(False, 10)
        guard.update(True, 40)
        guard.update(False, 50)
        self.assertTrue(guard.update(False, 100))
        self.assertFalse(guard.update(False, 110))

    def test_unknown_contact_neither_starts_nor_ends_a_pause(self):
        guard = WindowGuard(open_delay=10, close_delay=10)
        for t in (0, 100, 1000):
            self.assertFalse(guard.update(None, t))
        guard.update(True, 2000)
        self.assertTrue(guard.update(True, 2010))
        for t in (3000, 4000):
            self.assertTrue(guard.update(None, t))
        guard.update(False, 5000)
        self.assertFalse(guard.update(False, 5010))

    def test_unknown_interrupts_open_timer(self):
        guard = WindowGuard(open_delay=100, close_delay=10)
        guard.update(True, 0)
        guard.update(None, 50)
        self.assertFalse(guard.update(True, 120))
        self.assertTrue(guard.update(True, 220))

    def test_aggregate_contacts(self):
        self.assertFalse(aggregate_contacts([]))
        self.assertFalse(aggregate_contacts([False, False]))
        self.assertTrue(aggregate_contacts([False, True, None]))
        self.assertIsNone(aggregate_contacts([False, None]))

    def test_negative_delays_rejected(self):
        with self.assertRaises(ValueError):
            WindowGuard(open_delay=-1)


class PlannerTests(unittest.TestCase):
    def setUp(self):
        # The backend is first observed off at t=0, as after a restart; the
        # restart pause counts from that first observation.
        self.memory = CommandMemory(off_since=0.0)
        self.memory.note_observation(obs("off"), 0.0)

    def plan(self, intent, observation, now):
        return plan_next_command(intent, observation, self.memory, LIMITS, now, PAUSE)

    def test_restart_pause_blocks_start_then_allows_it(self):
        heat = Intent("heat", 22.0)
        self.assertEqual("restart_pause", self.plan(heat, obs("off"), 0).status)
        self.assertEqual("restart_pause", self.plan(heat, obs("off"), PAUSE - 1).status)
        result = self.plan(heat, obs("off"), PAUSE)
        self.assertEqual(Command("set_mode", "heat"), result.command)
        self.assertEqual("starting", result.status)

    def test_target_follows_mode_once_running(self):
        result = self.plan(Intent("heat", 22.0), obs("heat", 21.0), 1000)
        self.assertEqual(Command("set_temperature", target=22.0), result.command)

    def test_rounding_tolerance_counts_as_in_sync(self):
        for observed in (22.0, 22.25, 21.75):
            result = self.plan(Intent("heat", 22.0), obs("heat", observed), 1000)
            self.assertIsNone(result.command)
            self.assertEqual("in_sync", result.status)
        result = self.plan(Intent("heat", 22.0), obs("heat", 22.6), 1000)
        self.assertEqual("adjusting_target", result.status)

    def test_off_intent_turns_pump_off_immediately_without_waiting(self):
        for mode in ("heat", "cool", "auto"):
            memory = CommandMemory(off_since=0.0)
            result = plan_next_command(Intent("off"), obs(mode, 22), memory, LIMITS, 1.0, PAUSE)
            self.assertEqual(Command("set_mode", "off"), result.command)
        self.assertEqual("in_sync", self.plan(Intent("off"), obs("off"), 5).status)

    def test_changeover_goes_through_off_and_respects_the_pause(self):
        cool = Intent("cool", 24.0)
        first = self.plan(cool, obs("heat", 22), 1000)
        self.assertEqual(Command("set_mode", "off"), first.command)
        self.assertEqual("changeover_off", first.status)
        self.memory.record(first.command, 1000)
        # Backend reports off: the restart pause counts from when off was seen.
        wait = self.plan(cool, obs("off"), 1010)
        self.assertEqual("restart_pause", wait.status)
        self.assertIsNone(wait.command)
        later = self.plan(cool, obs("off"), 1010 + PAUSE)
        self.assertEqual(Command("set_mode", "cool"), later.command)

    def test_stale_start_is_not_replayed_after_intent_changes(self):
        self.plan(Intent("heat", 22.0), obs("off"), 0)  # waiting for the pause
        # While waiting the user turned the wrapper off: nothing is started.
        result = self.plan(Intent("off"), obs("off"), PAUSE + 10)
        self.assertIsNone(result.command)
        self.assertEqual("in_sync", result.status)

    def test_acknowledged_command_clears_pending_state(self):
        heat = Intent("heat", 22.0)
        start = self.plan(heat, obs("off"), PAUSE)
        self.memory.record(start.command, PAUSE)
        self.assertEqual(
            "pending_acknowledgment", self.plan(heat, obs("off"), PAUSE + 10).status
        )
        self.plan(heat, obs("heat", 22.0), PAUSE + 20)
        self.assertIsNone(self.memory.pending)
        self.assertEqual(0, self.memory.attempts)

    def test_unacknowledged_command_is_retried_then_bounded(self):
        heat = Intent("heat", 22.0)
        now = PAUSE
        sent = 0
        # Six attempts spaced by the retry interval stay inside the cooldown window.
        for _ in range(6):
            result = self.plan(heat, obs("off"), now)
            if result.command is not None:
                self.memory.record(result.command, now)
                sent += 1
            now += RETRY_SECONDS
        self.assertEqual(3, sent)
        self.assertEqual("retries_exhausted", self.plan(heat, obs("off"), now).status)

    def test_exhausted_retries_resume_after_cooldown(self):
        heat = Intent("heat", 22.0)
        now = PAUSE
        for _ in range(3):
            self.memory.record(self.plan(heat, obs("off"), now).command, now)
            now += RETRY_SECONDS
        self.assertEqual("retries_exhausted", self.plan(heat, obs("off"), now).status)
        resumed = self.plan(heat, obs("off"), now + EXHAUSTED_COOLDOWN)
        self.assertEqual(Command("set_mode", "heat"), resumed.command)

    def test_temperature_changes_are_rate_limited_but_off_is_not(self):
        self.memory.record(Command("set_temperature", target=21.0), 1000)
        limited = self.plan(Intent("heat", 23.0), obs("heat", 21.0), 1000 + 5)
        self.assertEqual("rate_limited", limited.status)
        allowed = self.plan(
            Intent("heat", 23.0), obs("heat", 21.0), 1000 + MIN_COMMAND_INTERVAL
        )
        self.assertEqual(Command("set_temperature", target=23.0), allowed.command)
        off = self.plan(Intent("off"), obs("heat", 21.0), 1000 + 5)
        self.assertEqual(Command("set_mode", "off"), off.command)

    def test_target_completing_a_start_is_not_rate_limited(self):
        self.memory.record(Command("set_mode", "heat"), 1000)
        result = self.plan(Intent("heat", 22.0), obs("heat", 18.0), 1001)
        self.assertEqual(Command("set_temperature", target=22.0), result.command)

    def test_start_shortly_after_off_keeps_minimum_spacing_even_without_pause(self):
        memory = CommandMemory(off_since=0.0)
        memory.note_observation(obs("off"), 0)
        memory.record(Command("set_mode", "off"), 1000)
        result = plan_next_command(Intent("heat", 22.0), obs("off"), memory, LIMITS, 1005, 0.0)
        self.assertEqual("rate_limited", result.status)
        later = plan_next_command(
            Intent("heat", 22.0), obs("off"), memory, LIMITS, 1000 + MIN_COMMAND_INTERVAL, 0.0
        )
        self.assertEqual(Command("set_mode", "heat"), later.command)

    def test_new_command_after_exhaustion_gets_its_own_retries(self):
        heat22 = Intent("heat", 22.0)
        now = PAUSE
        for _ in range(3):
            self.memory.record(self.plan(heat22, obs("heat", 18.0), now).command, now)
            now += RETRY_SECONDS
        self.assertIsNotNone(self.memory.exhausted_at)
        # A different target is a new episode: it is sent and retried normally.
        first = self.plan(Intent("heat", 23.0), obs("heat", 18.0), now + MIN_COMMAND_INTERVAL)
        self.assertEqual(Command("set_temperature", target=23.0), first.command)
        self.memory.record(first.command, now + MIN_COMMAND_INTERVAL)
        self.assertIsNone(self.memory.exhausted_at)
        self.assertEqual(1, self.memory.attempts)
        retry_at = now + MIN_COMMAND_INTERVAL + RETRY_SECONDS
        retry = self.plan(Intent("heat", 23.0), obs("heat", 18.0), retry_at)
        self.assertEqual(Command("set_temperature", target=23.0), retry.command)

    def test_in_sync_closes_an_unacknowledged_episode(self):
        heat = Intent("heat", 22.0)
        self.memory.record(self.plan(heat, obs("off"), PAUSE).command, PAUSE)
        self.memory.record(self.plan(heat, obs("off"), PAUSE + RETRY_SECONDS).command,
                           PAUSE + RETRY_SECONDS)
        self.assertEqual(2, self.memory.attempts)
        # Hours of "off, in sync" end that episode.
        self.assertEqual("in_sync", self.plan(Intent("off"), obs("off"), PAUSE + 7200).status)
        self.assertEqual(0, self.memory.attempts)
        later = PAUSE + 8000
        fresh = self.plan(heat, obs("off"), later)
        self.memory.record(fresh.command, later)
        self.assertEqual(1, self.memory.attempts)
        self.assertIsNone(self.memory.exhausted_at)

    def test_target_exemption_is_bounded_in_time(self):
        self.memory.record(Command("set_mode", "heat"), 1000)
        late = self.plan(Intent("heat", 23.0), obs("heat", 22.0), 1000 + RETRY_SECONDS)
        self.assertEqual(Command("set_temperature", target=23.0), late.command)  # spacing elapsed
        self.memory.record(Command("set_mode", "heat"), 5000)
        self.memory.last_command_at = 5000
        shortly = self.plan(Intent("heat", 24.0), obs("heat", 22.0), 5030)
        self.assertEqual(Command("set_temperature", target=24.0), shortly.command)
        # Not right after the start any more and not yet spaced: limited.
        self.memory.last_command = Command("set_temperature", target=24.0)
        self.memory.last_command_at = 5030
        self.assertEqual("rate_limited", self.plan(Intent("heat", 25.0), obs("heat", 22.0), 5040).status)

    def test_repeated_offs_are_spaced_but_the_first_is_immediate(self):
        run = Intent("off")
        first = self.plan(run, obs("heat", 22.0), 1000)
        self.assertEqual(Command("set_mode", "off"), first.command)
        self.memory.record(first.command, 1000)
        self.plan(run, obs("off"), 1001)  # acknowledged
        # Another writer flips the pump back on right away: no immediate second off.
        again = self.plan(run, obs("heat", 22.0), 1005)
        self.assertEqual("rate_limited", again.status)
        later = self.plan(run, obs("heat", 22.0), 1000 + MIN_COMMAND_INTERVAL)
        self.assertEqual(Command("set_mode", "off"), later.command)

    def test_failed_off_is_retried_sooner_than_a_failed_start(self):
        off = Intent("off")
        now = 1000.0
        for _ in range(3):
            result = self.plan(off, obs("heat", 22.0), now)
            self.memory.record(result.command, now)
            now += RETRY_SECONDS
        self.assertEqual("retries_exhausted", self.plan(off, obs("heat", 22.0), now).status)
        self.assertLess(OFF_EXHAUSTED_COOLDOWN, EXHAUSTED_COOLDOWN)
        resumed = self.plan(off, obs("heat", 22.0), 1000 + 2 * RETRY_SECONDS + OFF_EXHAUSTED_COOLDOWN)
        self.assertEqual(Command("set_mode", "off"), resumed.command)

    def test_pump_is_never_started_without_a_target(self):
        result = self.plan(Intent("heat", None), obs("off"), PAUSE)
        self.assertIsNone(result.command)
        self.assertEqual("invalid_backend_limits", result.status)

    def test_unavailable_backend_sends_nothing(self):
        for intent in (Intent("heat", 22.0), Intent("off")):
            result = self.plan(intent, obs(None, available=False), 5000)
            self.assertIsNone(result.command)
            self.assertEqual("backend_unavailable", result.status)

    def test_unsupported_mode_sends_nothing(self):
        result = self.plan(Intent("cool", 24.0), obs("off", modes=("heat", "off")), 5000)
        self.assertIsNone(result.command)
        self.assertEqual("unsupported_backend_mode", result.status)

    def test_missing_limits_block_target_commands(self):
        result = plan_next_command(
            Intent("heat", 22.0), obs("heat", 21.0), self.memory, None, 1000, PAUSE
        )
        self.assertIsNone(result.command)
        self.assertEqual("invalid_backend_limits", result.status)

    def test_fresh_off_observation_restarts_the_pause(self):
        heat = Intent("heat", 22.0)
        self.plan(heat, obs("heat", 22.0), 1000)
        self.plan(Intent("off"), obs("off"), 2000)
        self.assertEqual("restart_pause", self.plan(heat, obs("off"), 2000 + PAUSE - 1).status)
        self.assertEqual("starting", self.plan(heat, obs("off"), 2000 + PAUSE).status)


if __name__ == "__main__":
    unittest.main()
