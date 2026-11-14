"""Pure heat-pump/AC decision logic; it never calls a Home Assistant service.

The room sensor is the demand source. The wrapper commands an *existing*
climate entity with HVAC mode and temperature commands; it does not model the
pump as an electrical relay. A reported HVAC mode shows what the backend says,
not that a compressor is running.

Everything here is deterministic and takes explicit ``now`` values (monotonic
seconds), so restart pauses, changeover and retries are unit tested without
Home Assistant.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import floor, isfinite
from typing import Literal

AcMode = Literal["off", "heat", "cool"]

# Commands to one backend are spaced at least this far apart (seconds) unless
# they turn the pump off, which is never delayed.
MIN_COMMAND_INTERVAL = 60.0
RETRY_SECONDS = 120.0
MAX_ATTEMPTS = 3
EXHAUSTED_COOLDOWN = 900.0
# A failed safety "off" is retried sooner than a failed start.
OFF_EXHAUSTED_COOLDOWN = 300.0


@dataclass(frozen=True)
class BackendLimits:
    """Temperature limits and step reported by the underlying climate."""

    minimum: float
    maximum: float
    step: float

    def __post_init__(self) -> None:
        if not all(isfinite(v) for v in (self.minimum, self.maximum, self.step)):
            raise ValueError("Backend limits must be finite")
        if self.minimum >= self.maximum or self.step <= 0:
            raise ValueError("Backend limits must be ordered with a positive step")

    @property
    def tolerance(self) -> float:
        """Rounding tolerance when comparing a commanded and an observed target."""
        return self.step / 2.0 + 1e-6


def native_target(effective_room_target: float, compensation: float, limits: BackendLimits) -> float:
    """Map the effective room target to a supported pump setpoint.

    Compensation is signed and configured separately per mode. The result is
    clamped to the backend limits and rounded to its step, measured from the
    minimum so devices with offset grids are respected.
    """
    if not isfinite(effective_room_target) or not isfinite(compensation):
        raise ValueError("Targets and compensation must be finite")
    raw = min(limits.maximum, max(limits.minimum, effective_room_target + compensation))
    steps = floor((raw - limits.minimum) / limits.step + 0.5)
    value = min(limits.maximum, limits.minimum + steps * limits.step)
    return round(value, 4)


def heating_demand(
    current: float | None, target: float, active: bool, cold_tolerance: float, hot_tolerance: float
) -> bool | None:
    """Heating demand with hysteresis; None means the reading is invalid."""
    if current is None or not isfinite(current) or not isfinite(target):
        return None
    if cold_tolerance < 0 or hot_tolerance < 0:
        raise ValueError("Tolerances must be nonnegative")
    if active:
        return current < target + hot_tolerance
    return current <= target - cold_tolerance


def cooling_demand(
    current: float | None, target: float, active: bool, cold_tolerance: float, hot_tolerance: float
) -> bool | None:
    """Cooling demand with hysteresis, mirroring heating.

    Cooling starts above ``target + hot_tolerance`` and stops at or below
    ``target - cold_tolerance``.
    """
    if current is None or not isfinite(current) or not isfinite(target):
        return None
    if cold_tolerance < 0 or hot_tolerance < 0:
        raise ValueError("Tolerances must be nonnegative")
    if active:
        return current > target - cold_tolerance
    return current >= target + hot_tolerance


def demand(
    mode: AcMode, current: float | None, target: float, active: bool, cold: float, hot: float
) -> bool | None:
    """Dispatch to the demand rule of the selected wrapper mode."""
    if mode == "heat":
        return heating_demand(current, target, active, cold, hot)
    if mode == "cool":
        return cooling_demand(current, target, active, cold, hot)
    return False


def aggregate_contacts(states: list[bool | None]) -> bool | None:
    """Combine window/door contacts: any open wins, then any unknown, else closed.

    True = open, False = closed, None = unknown/unavailable. With no contacts
    configured the result is False (nothing to pause for).
    """
    if any(state is True for state in states):
        return True
    if any(state is None for state in states):
        return None
    return False


@dataclass
class WindowGuard:
    """Pause only after a stable open interval; resume only after stable closure.

    A missing or unavailable contact is *unknown*: it never starts a pause and
    never ends one. An unknown reading therefore cannot silently resume a
    paused pump, nor stop a running one, and the status is exposed for display.
    """

    open_delay: float = 120.0
    close_delay: float = 60.0
    paused: bool = False
    _open_since: float | None = field(default=None, repr=False)
    _closed_since: float | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.open_delay < 0 or self.close_delay < 0:
            raise ValueError("Window delays must not be negative")

    def update(self, is_open: bool | None, now: float) -> bool:
        """Feed one aggregated reading and return whether operation is paused."""
        if is_open is None:
            # Neither timer advances on missing data.
            self._open_since = None
            self._closed_since = None
            return self.paused
        if is_open:
            self._closed_since = None
            if self._open_since is None:
                self._open_since = now
            if not self.paused and now - self._open_since >= self.open_delay:
                self.paused = True
        else:
            self._open_since = None
            if self._closed_since is None:
                self._closed_since = now
            if self.paused and now - self._closed_since >= self.close_delay:
                self.paused = False
        return self.paused


@dataclass(frozen=True)
class Observation:
    """What the underlying climate currently reports."""

    available: bool
    mode: str | None = None
    target: float | None = None
    supported_modes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Intent:
    """What the wrapper wants the backend to do right now."""

    mode: AcMode
    native_target: float | None = None
    reason: str = ""


@dataclass(frozen=True)
class Command:
    """One backend service call: set the HVAC mode or the target temperature."""

    kind: Literal["set_mode", "set_temperature"]
    mode: str | None = None
    target: float | None = None

    @property
    def signature(self) -> tuple[str, str | None, float | None]:
        return (self.kind, self.mode, self.target)


@dataclass(frozen=True)
class Plan:
    """The next command (if any) and a stable status for diagnostics."""

    command: Command | None
    status: str


@dataclass
class CommandMemory:
    """Command history needed for restart pause, rate limits and bounded retries."""

    # Monotonic time the backend was last seen (or assumed, at startup) to be off.
    # Initialised to the start time so a restart never starts the pump at once.
    off_since: float
    last_command_at: float | None = None
    last_command: Command | None = None
    pending: Command | None = None
    pending_since: float | None = None
    attempts: int = 0
    exhausted_at: float | None = None
    last_seen_mode: str | None = None

    def note_observation(self, obs: Observation, now: float) -> None:
        """Track when the backend went off; a fresh off restarts the pause."""
        if obs.available and obs.mode == "off":
            if self.last_seen_mode != "off":
                self.off_since = now
        if obs.available:
            self.last_seen_mode = obs.mode

    def acknowledged(self, command: Command, obs: Observation, tolerance: float) -> bool:
        """True when the observed backend matches the command's effect."""
        if command.kind == "set_mode":
            return obs.mode == command.mode
        return (
            obs.target is not None
            and command.target is not None
            and abs(obs.target - command.target) <= tolerance
        )

    def record(self, command: Command, now: float) -> None:
        """Record a dispatched command, including attempts that fail."""
        if self.pending is not None and self.pending.signature == command.signature:
            self.attempts += 1
        else:
            # A different command is a new episode: earlier failures must not
            # shorten or block its retries.
            self.attempts = 1
            self.exhausted_at = None
        self.pending = command
        self.pending_since = now
        self.last_command_at = now
        self.last_command = command
        if self.attempts >= MAX_ATTEMPTS:
            self.exhausted_at = now


def plan_next_command(
    intent: Intent,
    obs: Observation,
    memory: CommandMemory,
    limits: BackendLimits | None,
    now: float,
    min_restart_pause: float,
) -> Plan:
    """Decide the single next backend command for the current intent.

    The plan is recomputed from fresh intent on every evaluation, so a start
    that became obsolete while waiting (pause, manual off, price change) is
    never replayed. At most one command is returned per call.
    """
    if not obs.available:
        return Plan(None, "backend_unavailable")
    memory.note_observation(obs, now)
    tolerance = limits.tolerance if limits is not None else 0.5

    # Clear an acknowledged command; keep a stale unacknowledged one for retry logic.
    if memory.pending is not None and memory.acknowledged(memory.pending, obs, tolerance):
        memory.pending = None
        memory.pending_since = None
        memory.attempts = 0
        memory.exhausted_at = None

    if intent.mode != "off" and intent.mode not in obs.supported_modes:
        return Plan(None, "unsupported_backend_mode")

    command, status = _desired_command(intent, obs, memory, limits, tolerance, now, min_restart_pause)
    if command is None:
        if status == "in_sync":
            # Nothing is wanted: an earlier unacknowledged episode is over and
            # must not count towards the next, unrelated command.
            memory.pending = None
            memory.pending_since = None
            memory.attempts = 0
            memory.exhausted_at = None
        return Plan(None, status)

    return _throttle(command, memory, now, status)


def _desired_command(
    intent: Intent,
    obs: Observation,
    memory: CommandMemory,
    limits: BackendLimits | None,
    tolerance: float,
    now: float,
    min_restart_pause: float,
) -> tuple[Command | None, str]:
    if intent.mode == "off":
        if obs.mode == "off":
            return None, "in_sync"
        return Command("set_mode", "off"), "turning_off"

    # Wanted: heat or cool.
    if obs.mode == intent.mode:
        if intent.native_target is None or limits is None:
            return None, "invalid_backend_limits"
        if obs.target is not None and abs(obs.target - intent.native_target) <= tolerance:
            return None, "in_sync"
        return Command("set_temperature", target=intent.native_target), "adjusting_target"

    if obs.mode == "off":
        waited = now - memory.off_since
        if waited < min_restart_pause:
            return None, "restart_pause"
        if intent.native_target is None or limits is None:
            # Never start the pump without a target to give it.
            return None, "invalid_backend_limits"
        return Command("set_mode", intent.mode), "starting"

    # Another active or foreign mode: safe changeover goes through off first.
    return Command("set_mode", "off"), "changeover_off"


def _throttle(command: Command, memory: CommandMemory, now: float, status: str) -> Plan:
    pending = memory.pending
    is_off = command.kind == "set_mode" and command.mode == "off"
    cooldown = OFF_EXHAUSTED_COOLDOWN if is_off else EXHAUSTED_COOLDOWN
    if pending is not None and pending.signature == command.signature:
        if memory.exhausted_at is not None:
            if now - memory.exhausted_at < cooldown:
                return Plan(None, "retries_exhausted")
            memory.attempts = 0
            memory.exhausted_at = None
        elif memory.pending_since is not None and now - memory.pending_since < RETRY_SECONDS:
            return Plan(None, "pending_acknowledgment")
    last = memory.last_command
    recent = (
        memory.last_command_at is not None and now - memory.last_command_at < MIN_COMMAND_INTERVAL
    )
    if is_off:
        # A safety off is never delayed, except that repeated offs are spaced so
        # another writer flipping the mode back cannot make this a command storm.
        limited = recent and last is not None and last.kind == "set_mode" and last.mode == "off"
    else:
        # A target sent right after its own start completes that start. The
        # exemption is bounded in time; every other non-off command keeps spacing.
        completes_start = (
            command.kind == "set_temperature"
            and last is not None
            and last.kind == "set_mode"
            and last.mode != "off"
            and memory.last_command_at is not None
            and now - memory.last_command_at < RETRY_SECONDS
        )
        limited = recent and not completes_start
    if limited:
        return Plan(None, "rate_limited")
    return Plan(command, status)
