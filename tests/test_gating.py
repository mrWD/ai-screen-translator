"""Unit tests for the single-in-flight-job state machine (screen_translator/gating.py).

These cover the busy-gating + hold-key retry logic that previously lived inline in
App and could only be exercised by hand on the real machine.

Run from the repo root:
    ./.venv/bin/python -m unittest discover -s tests -t .
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from screen_translator.gating import BusyGate  # noqa: E402


class TestBusyGate(unittest.TestCase):
    def test_single_in_flight(self):
        gate = BusyGate()
        self.assertTrue(gate.try_start())   # first acquires
        self.assertFalse(gate.try_start())  # second is rejected while busy

    def test_finish_frees_the_slot(self):
        gate = BusyGate()
        gate.try_start()
        self.assertFalse(gate.finish())     # no hold queued -> nothing to replay
        self.assertTrue(gate.try_start())   # slot is free again

    def test_hold_fires_immediately_when_idle(self):
        gate = BusyGate()
        self.assertTrue(gate.hold_start())  # idle -> translate now
        self.assertTrue(gate.hold_active)

    def test_hold_while_busy_is_queued_then_replayed(self):
        gate = BusyGate()
        gate.try_start()                    # a job is running
        self.assertFalse(gate.hold_start())  # busy -> queued, don't fire now
        self.assertTrue(gate.hold_pending)
        self.assertTrue(gate.finish())      # job done, key still held -> replay
        self.assertFalse(gate.hold_pending)  # consumed

    def test_hold_released_before_job_finishes_is_not_replayed(self):
        gate = BusyGate()
        gate.try_start()
        gate.hold_start()                   # queued while busy
        gate.hold_end()                     # user let go before the job finished
        self.assertFalse(gate.finish())     # nothing to replay
        self.assertFalse(gate.hold_active)
        self.assertFalse(gate.hold_pending)

    def test_finish_does_not_replay_a_hold_that_already_fired(self):
        gate = BusyGate()
        self.assertTrue(gate.hold_start())  # idle -> fires now, nothing queued
        self.assertFalse(gate.hold_pending)
        self.assertTrue(gate.try_start())   # the full-screen job it triggered starts
        self.assertFalse(gate.finish())     # active but never queued -> no replay

    def test_reset_hold_clears_hold_without_touching_busy(self):
        gate = BusyGate()
        gate.try_start()
        gate.hold_start()                   # queued
        gate.reset_hold()
        self.assertFalse(gate.hold_active)
        self.assertFalse(gate.hold_pending)
        self.assertTrue(gate.busy)          # the running job is untouched


if __name__ == "__main__":
    unittest.main()
