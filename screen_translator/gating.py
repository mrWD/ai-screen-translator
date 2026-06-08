"""The single-in-flight-job policy as a tiny, framework-free state machine.

One job runs at a time (`busy`). A new trigger while busy is dropped, *except* the
hold key: if the hold key fires while busy it's remembered (`hold_pending`) and
replayed the moment the running job finishes — but only if the key is still held
(`hold_active`). This is pure boolean bookkeeping, so it lives apart from the Qt
wiring in `App` and is unit-tested directly.
"""

from __future__ import annotations


class BusyGate:
    def __init__(self) -> None:
        self.busy = False          # a job is in flight
        self.hold_active = False   # the hold key is physically down
        self.hold_pending = False  # hold fired while busy -> replay when free

    def try_start(self) -> bool:
        """Acquire the single slot. Returns False if a job is already running."""
        if self.busy:
            return False
        self.busy = True
        return True

    def finish(self) -> bool:
        """Mark the running job done. Returns True if a queued hold-peek should
        now fire (it waited for this job and the key is still held)."""
        self.busy = False
        if self.hold_pending and self.hold_active:
            self.hold_pending = False
            return True
        return False

    def hold_start(self) -> bool:
        """Hold key pressed. Returns True if a full-screen translate should start
        now, or False if it was queued because a job is already running."""
        self.hold_active = True
        if self.busy:
            self.hold_pending = True
            return False
        return True

    def hold_end(self) -> None:
        """Hold key released — drop any queued retry."""
        self.hold_active = False
        self.hold_pending = False

    def reset_hold(self) -> None:
        """Clear hold state without touching `busy` (used when the hotkey listener
        is rebuilt — a key held across the swap never delivers its release)."""
        self.hold_active = False
        self.hold_pending = False
