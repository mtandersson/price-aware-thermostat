"""Guard against repeated or unacknowledged Home Assistant relay commands.

The guard does not control any device. It tracks a pending command until
the switch state confirms it, suppresses duplicate requests, and allows
bounded retries if a relay never acknowledges the requested state.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RelayCommandGuard:
    """Track one relay command at a time without assuming service success is an ACK."""

    retry_seconds: float = 120.0
    pending_state: bool | None = None
    last_command_monotonic: float | None = None

    def __post_init__(self) -> None:
        """Reject a nonpositive retry interval."""
        if self.retry_seconds <= 0:
            raise ValueError("Relay command retry interval must be positive")

    def acknowledge(self, observed: bool) -> None:
        """Clear the pending command only when the switch reports that state."""
        if self.pending_state is not None and self.pending_state == observed:
            self.pending_state = None
            self.last_command_monotonic = None

    def should_dispatch(self, desired: bool, observed: bool, now: float) -> bool:
        """Decide whether a command is needed, including cancelling a pending opposite.

        A pending command is not considered acknowledged just because the
        Home Assistant service returned successfully. If the desired state
        changes before the relay acknowledges, a corrective command may be
        required even if the observed relay is already at the desired state.
        """
        if desired == observed:
            # Correct an unacknowledged ON command with OFF; never bypass
            # minimum-cycle protection to reassert heating over a pending OFF.
            return self.pending_state is True and not desired
        if self.pending_state == desired and self.last_command_monotonic is not None:
            return now - self.last_command_monotonic >= self.retry_seconds
        return True

    def record_attempt(self, desired: bool, now: float) -> None:
        """Throttle repeated service attempts until an observed-state ACK."""
        self.pending_state = desired
        self.last_command_monotonic = now
