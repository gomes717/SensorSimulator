"""Issue 04: MainWindow runs one engine per occupied board slot and files each
slot's expected line onto that slot's own sensor page.
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication
from user_helpers import user_of

from models.types import ModelId, PersonProfile


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(app):
    import gui.main_window as mw

    w = mw.MainWindow()
    w.state.users[:] = [
        user_of(PersonProfile(name="P0", model_id=ModelId.CAMBRIDGE)),
        user_of(PersonProfile(name="P1", model_id=ModelId.UVA_PADOVA)),
        user_of(PersonProfile(name="P2", model_id=ModelId.DEICHMANN)),
    ]
    yield w
    w.sim.engines.stop_all()
    w.close()


def test_model_only_is_one_slot(win):
    win.state.model_only = True
    win.state.active_user = win.state.users[0]
    win.sim.restart()
    assert win.sim.engines.slots == [0]
    assert win.sim.per_slot_expected is False
    win.sim.engines.stop_all()


def test_layout_assignments_build_one_engine_per_slot(win):
    win.state.model_only = False
    win.state.board_layout.slots[0].person = "P0"
    win.state.board_layout.slots[2].person = "P2"
    win.sim.restart()
    assert win.sim.engines.slots == [0, 2]
    assert win.sim.per_slot_expected is True
    win.sim.engines.stop_all()


def test_expected_ticks_route_to_per_slot_history(win):
    win.state.model_only = False
    win.state.board_layout.slots[0].person = "P0"
    win.state.board_layout.slots[1].person = "P1"
    win.sim.restart()  # builds a paused pool
    win.sim.per_slot_expected = True

    uid0 = win.directory.slot_user_id(0)
    uid1 = win.directory.slot_user_id(1)
    assert uid0 != uid1

    # hand-drive a few ticks (bypassing the QThread) straight into the handler
    for _ in range(3):
        win.sim.on_expected_reading(0, "2020-01-01T00:00:00+00:00", 101.0, 0.0, 0.0)
        win.sim.on_expected_reading(1, "2020-01-01T00:00:01+00:00", 202.0, 0.0, 0.0)

    page0, page1 = win.tabs.pages.get(uid0), win.tabs.pages.get(uid1)
    assert page0.graph.buf.expected_y == [101.0, 101.0, 101.0]
    assert page1.graph.buf.expected_y == [202.0, 202.0, 202.0]
    assert not page0.graph.buf.graph_y  # received buffer untouched
    win.sim.engines.stop_all()


def test_expected_line_follows_the_live_session_id_after_a_reassignment(win):
    """A session's user_id is frozen at connect. Re-assigning its slot to another
    patient renames the device, but the board's readings keep arriving under the
    old id — so the expected line must use the session's id, not a freshly
    derived label, or the row plots a received trace with no model line."""

    class _FakeSession:
        slot_index = 2
        user_id = "test3 — Sensor 3"  # what it connected as

    class _FakeBt:
        def sessions(self):
            return {"aa:bb": _FakeSession()}

        def display_name(self, address):
            return "test4 — Sensor 3"  # renamed since

    win.windows.bluetooth = _FakeBt()
    win.state.board_layout.slots[2].person = "test4"
    try:
        assert win.directory.slot_user_id(2) == "test3 — Sensor 3"
    finally:
        # MainWindow.closeEvent drives the real window's teardown on the
        # fixture's close(); leaving a stand-in there aborts the interpreter.
        win.windows.bluetooth = None


def test_slot_user_id_falls_back_to_the_layout_when_nothing_is_connected(win):
    win.windows.bluetooth = None
    win.state.board_layout.slots[1].person = "P1"
    assert win.directory.slot_user_id(1) == "P1 — Sensor 2"
