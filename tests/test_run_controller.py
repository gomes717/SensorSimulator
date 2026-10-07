"""RunController: the run state machine and the blocked-until-a-sensor-is-live rule.

No radio, no real engines: the hooks are recording fakes.
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication, QPushButton

from api import protocol
from gui.run_controller import PAUSED, RUNNING, STARTING, STOPPED, RunController, RunHooks

_APP = QApplication.instance() or QApplication([])


class FakeBoard:
    def __init__(self, live=0, total=None):
        self.live = live
        self.total = live if total is None else total
        self.sent = []

    def live_sessions(self):
        return [object()] * self.live

    def sessions(self):
        return {i: object() for i in range(self.total)}

    def broadcast(self, char_key, payload):
        self.sent.append((char_key, payload))
        return self.live


class FakeEngines:
    def __init__(self, calls):
        self._calls = calls

    def pause_all(self):
        self._calls.append("pause")

    def resume_all(self):
        self._calls.append("resume")


class Rig:
    def __init__(self, live=0, total=None, model_only=False):
        self.calls = []
        self.status = []
        self.board = FakeBoard(live, total)
        self.model_only = model_only
        self.push_result = True  # None holds the push until the test finishes it
        self.pending = None
        engines = FakeEngines(self.calls)
        hooks = RunHooks(
            board=self.board,
            engines=engines,
            model_only=lambda: self.model_only,
            restart_engine=lambda: self.calls.append("restart"),
            stop_engine=lambda: self.calls.append("stop_engine"),
            reset_graph_view=lambda: self.calls.append("reset_graph"),
            anchor_clock=lambda: self.calls.append("anchor"),
            push_config=self._push_config,
            show_status=self.status.append,
        )
        self.start_btn, self.stop_btn = QPushButton(), QPushButton()
        self.run = RunController(hooks, self.start_btn, self.stop_btn)

    def _push_config(self, on_done):
        self.calls.append("push")
        self.pending = on_done
        if self.push_result is not None:
            on_done(self.push_result)

    def run_states_sent(self):
        return [protocol.decode_run_state(p) for k, p in self.board.sent if k == "run_state"]


def test_buttons_are_blocked_with_no_live_sensor():
    rig = Rig(live=0)
    assert not rig.start_btn.isEnabled() and not rig.stop_btn.isEnabled()


def test_a_live_sensor_unblocks_them():
    rig = Rig(live=0)
    rig.board.live = 1
    rig.run.refresh_enabled()
    assert rig.start_btn.isEnabled() and rig.stop_btn.isEnabled()


def test_model_only_needs_no_sensor():
    rig = Rig(live=0, model_only=True)
    rig.run.refresh_enabled()
    assert rig.start_btn.isEnabled()


def test_cgms_only_lock_blocks_even_a_live_sensor():
    rig = Rig(live=2)
    rig.run.set_locked(True)
    assert not rig.start_btn.isEnabled()
    rig.run.set_locked(False)
    assert rig.start_btn.isEnabled()


def test_a_run_in_progress_stays_controllable_after_every_sensor_drops():
    rig = Rig(live=1)
    rig.run.toggle()
    rig.board.live = 0
    rig.run.refresh_enabled()
    assert rig.start_btn.isEnabled() and rig.stop_btn.isEnabled()


def test_start_pause_resume_stop_cycle_and_labels():
    rig = Rig(live=1)
    assert rig.run.state == STOPPED and rig.start_btn.text() == "Start"
    rig.run.toggle()
    assert rig.run.state == RUNNING and rig.start_btn.text() == "Pause"
    assert rig.calls[:4] == ["push", "restart", "anchor", "resume"]
    rig.run.toggle()
    assert rig.run.state == PAUSED and rig.start_btn.text() == "Resume"
    rig.run.toggle()
    assert rig.run.state == RUNNING
    rig.run.stop()
    assert rig.run.state == STOPPED and rig.start_btn.text() == "Start"
    assert rig.calls[-2:] == ["stop_engine", "reset_graph"]


def test_start_resets_then_starts_every_board():
    rig = Rig(live=2)
    rig.run.start()
    assert rig.run_states_sent() == [protocol.RUN_STATE_STOPPED, protocol.RUN_STATE_RUNNING]


def test_start_waits_for_the_configuration_before_touching_the_run():
    rig = Rig(live=2)
    rig.push_result = None
    rig.run.start()
    assert rig.run.state == STARTING and rig.start_btn.text() == "Starting…"
    assert not rig.start_btn.isEnabled() and not rig.stop_btn.isEnabled()
    assert rig.calls == ["push"] and rig.board.sent == []  # nothing started yet
    assert "starting" in rig.run.commands_block_reason()
    rig.run.toggle()  # a second click while starting is ignored
    assert rig.calls == ["push"]
    rig.pending(True)
    assert rig.run.state == RUNNING
    assert rig.run_states_sent() == [protocol.RUN_STATE_STOPPED, protocol.RUN_STATE_RUNNING]


def test_a_failed_push_leaves_the_run_stopped():
    rig = Rig(live=2)
    rig.push_result = False
    rig.run.start()
    assert rig.run.state == STOPPED and rig.start_btn.text() == "Start"
    assert rig.board.sent == [] and "restart" not in rig.calls
    assert rig.start_btn.isEnabled()


def test_stopping_while_the_configuration_is_in_flight_cancels_that_start():
    rig = Rig(live=1)
    rig.push_result = None
    rig.run.start()
    rig.run.stop()
    rig.pending(True)  # the late answer must not start a run
    assert rig.run.state == STOPPED
    assert "restart" not in rig.calls


def test_a_run_state_that_reaches_nobody_is_reported():
    rig = Rig(live=0, total=3)
    rig.run.start()
    assert any("reached 0 of 3" in m for m in rig.status)


def test_no_status_when_no_board_is_connected():
    rig = Rig(live=0, total=0, model_only=True)
    rig.run.start()
    assert rig.status == []


def test_clicking_the_buttons_drives_the_machine():
    rig = Rig(live=1)
    rig.start_btn.click()
    assert rig.run.state == RUNNING
    rig.stop_btn.click()
    assert rig.run.state == STOPPED


def test_commands_are_blocked_with_a_reason_until_running_with_a_live_sensor():
    rig = Rig(live=0)
    assert "No live sensor" in rig.run.commands_block_reason()
    rig.board.live = 1
    assert "Start a run" in rig.run.commands_block_reason()
    rig.run.toggle()
    assert rig.run.commands_block_reason() == ""
    rig.run.toggle()
    assert "paused" in rig.run.commands_block_reason()
    rig.run.set_locked(True)
    assert "CGMS-only" in rig.run.commands_block_reason()


def test_model_only_commands_need_no_sensor_but_still_a_run():
    rig = Rig(live=0, model_only=True)
    assert "Start a run" in rig.run.commands_block_reason()
    rig.run.toggle()
    assert rig.run.commands_block_reason() == ""


def test_every_refresh_is_announced_so_per_sensor_state_can_follow():
    rig = Rig(live=1)
    ticks = []
    rig.run.refreshed.connect(lambda: ticks.append(1))
    rig.run.refresh_enabled()
    rig.run.refresh_enabled()
    assert len(ticks) == 2
    rig.run.toggle()  # a state change refreshes too
    assert len(ticks) > 2
