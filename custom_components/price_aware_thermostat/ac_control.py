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
    """Temperature limits and step reported by the underlying climate (Celsius).

    The step grid is anchored at ``minimum``. ``readback`` is the largest error
    Home Assistant's display rounding can add when the backend's state is read
    back (half its display unit); it is part of the acknowledgment tolerance so a
    correctly applied target is never reported as unacknowledged. ``margin``
    keeps commanded targets inside the limits by that much when the displayed
    limits are coarse (whole degrees Fahrenheit can round a limit outward and a
    write beyond the real limit is rejected).
    """

    minimum: float
    maximum: float
    step: float
    readback: float = 0.0
    margin: float = 0.0

    def __post_init__(self) -> None:
        values = (self.minimum, self.maximum, self.step, self.readback, self.margin)
        if not all(isfinite(v) for v in values):
            raise ValueError("Backend limits must be finite")
        if (
            self.step <= 0
            or self.readback < 0
            or self.margin < 0
            or self.minimum + 2 * self.margin >= self.maximum
        ):
            raise ValueError("Backend limits must be ordered with a positive step")

    @property
    def tolerance(self) -> float:
        """Tolerance when comparing a commanded and an observed target."""
        return self.step / 2.0 + self.readback + 1e-6


def native_target(effective_room_target: float, compensation: float, limits: BackendLimits) -> float:
    """Map the effective room target to a supported pump setpoint.

    Compensation is signed and configured separately per mode. The result is
    clamped to the backend limits (inside the safety margin) and rounded to its
    step, measured from the reported minimum so devices with offset grids are
    respected.
    """
    if not isfinite(effective_room_target) or not isfinite(compensation):
        raise ValueError("Targets and compensation must be finite")
    lower = limits.minimum + limits.margin
    upper = limits.maximum - limits.margin
    raw = min(upper, max(lower, effective_room_target + compensation))
    steps = floor((raw - limits.minimum) / limits.step + 0.5)
    value = limits.minimum + steps * limits.step
    while value > upper + 1e-9:
        steps -= 1
        value = limits.minimum + steps * limits.step
    while value < lower - 1e-9:
        steps += 1
        value = limits.minimum + steps * limits.step
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


@dataclass
class OutdoorLockout:
    """Lock heating or cooling by outdoor temperature, with hysteresis.

    Cooling is allowed only at or above ``cool_min``; heating only at or below
    ``heat_max``. A ``None`` threshold disables that lockout. A lockout engages
    when the outdoor temperature crosses its threshold and releases only once
    it is ``hysteresis`` beyond the threshold on the allowed side. A missing
    reading (``None``) fails open: both lockouts are released, so a sensor
    fault never blocks heating.
    """

    cool_min: float | None = None
    heat_max: float | None = None
    hysteresis: float = 1.0
    cool_locked: bool = False
    heat_locked: bool = False

    def __post_init__(self) -> None:
        if not isfinite(self.hysteresis) or self.hysteresis < 0:
            raise ValueError("Hysteresis must be a nonnegative number")

    def update(self, outdoor: float | None) -> None:
        """Feed one outdoor reading in Celsius (None = missing/stale/invalid)."""
        if outdoor is None or not isfinite(outdoor):
            self.cool_locked = False
            self.heat_locked = False
            return
        if self.cool_min is None:
            self.cool_locked = False
        elif self.cool_locked:
            self.cool_locked = outdoor < self.cool_min + self.hysteresis
        else:
            self.cool_locked = outdoor < self.cool_min
        if self.heat_max is None:
            self.heat_locked = False
        elif self.heat_locked:
            self.heat_locked = outdoor > self.heat_max - self.hysteresis
        else:
            self.heat_locked = outdoor > self.heat_max

    def locks(self, mode: AcMode) -> bool:
        """Whether the given direction is currently locked out."""
        if mode == "heat":
            return self.heat_locked
        if mode == "cool":
            return self.cool_locked
        return False


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
        # A target sent right after its own start completes that start. Because
        # `recent` already limits this to the spacing window, no other command gets
        # the exemption; every other non-off command keeps the minimum spacing.
        completes_start = (
            command.kind == "set_temperature"
            and last is not None
            and last.kind == "set_mode"
            and last.mode != "off"
        )
        limited = recent and not completes_start
    if limited:
        return Plan(None, "rate_limited")
    return Plan(command, status)


@dataclass
class RangeSelector:
    """Pick heat or cool for a dual-setpoint (heat_cool) wrapper, with hysteresis.

    Heating starts at or below ``low - cold`` and stops at ``low + hot``;
    cooling starts at or above ``high + hot`` and stops at ``high - cold``. A
    direction never switches straight to the other: its demand must end first,
    and the other direction may start only ``changeover`` seconds after the
    previous direction's demand ended. A direction that is not ``allowed``
    (outdoor lockout) never starts and stops at once; the other direction is
    never used in its place, so a locked room above ``high`` stays idle.
    """

    changeover: float = 1800.0
    direction: AcMode = "off"
    last_active: AcMode = "off"
    last_active_at: float | None = None
    status: str = "idle"

    def __post_init__(self) -> None:
        if not isfinite(self.changeover) or self.changeover < 0:
            raise ValueError("Changeover interval must not be negative")

    def stop(self, now: float) -> None:
        """End any demand (window pause, lockout, sensor fault, mode change)."""
        if self.direction != "off":
            self.last_active = self.direction
            self.last_active_at = now
        self.direction = "off"

    def resume(self, mode: str | None, now: float) -> None:
        """Adopt a direction a backend is already running in (after a restart)."""
        direction: AcMode = "heat" if mode == "heat" else "cool" if mode == "cool" else "off"
        if direction != "off":
            self.direction = direction
            self.last_active = direction
            self.last_active_at = now

    def select(
        self,
        current: float,
        low: float,
        high: float,
        cold: float,
        hot: float,
        now: float,
        heat_allowed: bool = True,
        cool_allowed: bool = True,
    ) -> AcMode:
        """Return the demanded direction ("off" = idle) and update ``status``."""
        if not all(isfinite(v) for v in (current, low, high)) or low >= high:
            raise ValueError("A finite range with low < high is required")
        allowed = {"heat": heat_allowed, "cool": cool_allowed}
        if self.direction != "off":
            active = self.direction
            target = low if active == "heat" else high
            if allowed[active] and demand(active, current, target, True, cold, hot):
                self.last_active_at = now
                self.status = "demand"
                return active
            self.stop(now)
        candidate: AcMode = (
            "heat" if current <= low - cold else "cool" if current >= high + hot else "off"
        )
        if candidate == "off":
            self.status = "idle"
            return "off"
        if not allowed[candidate]:
            self.status = "outdoor_lockout"
            return "off"
        if (
            self.last_active not in ("off", candidate)
            and self.last_active_at is not None
            and now - self.last_active_at < self.changeover
        ):
            self.status = "changeover_wait"
            return "off"
        self.direction = candidate
        self.last_active = candidate
        self.last_active_at = now
        self.status = "demand"
        return candidate


def adjust_range(
    low: float, high: float, effective_low: float, effective_high: float, gap: float
) -> tuple[float, float, bool]:
    """Keep price-adjusted setpoints at least ``gap`` apart.

    Cheap prices move both setpoints inward (pre-heat raises low, pre-cool
    lowers high). They are then limited around the middle of the user's range
    so the adjusted range never becomes narrower than ``gap``. Returns the
    effective low/high and whether that limit applied.
    """
    if effective_high - effective_low >= gap - 1e-9:
        return effective_low, effective_high, False
    middle = (low + high) / 2.0
    return (
        min(effective_low, max(low, middle - gap / 2.0)),
        max(effective_high, min(high, middle + gap / 2.0)),
        True,
    )
