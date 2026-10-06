"""Regression pins for three board-vs-app-truth bugs (2026-09-16):

1. A read requested for one slot must never be attributed to whatever row
   happens to be selected by the time the response arrives (gui/board_mode.py).
2. _fe_graph_title must agree with the main title / fe_canvas visibility — all
   three now share BoardMode, not the app's local, possibly-never-sent guess.
3. A PISA fault inserted for one slot must shade only that slot's graph, not
   every open sensor's (gui/instant_events.py + MainWindow._record_pisa_span).
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication

from api import protocol
from models import board_layout as bl
from models.types import ModelId, PersonProfile


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(app):
    import gui.main_window as mw

    w = mw.MainWindow()
    w._model_only = False
    w._person_profiles[:] = [
        PersonProfile(name="modelPt", model_id=ModelId.UVA_PADOVA),
        PersonProfile(name="csvPt", model_id=ModelId.CAMBRIDGE, data_source="csv"),
    ]
    w._board_layout = bl.BoardLayout(
        [bl.SlotAssignment(person="modelPt"), bl.SlotAssignment(person="csvPt")]
    )
    yield w
    w._engines.stop_all()
    w.close()


class _FakeSession:
    """Just enough of BleSession for _selected_slot()/BoardMode.refresh() to work."""

    def __init__(self, slot_index, user_id):
        self.slot_index = slot_index
        self.user_id = user_id
        self.is_live = True
        self.config_read = _FakeSignal()
        self.writes: list[tuple[str, bytes]] = []

    def queue_write(self, key, payload):
        self.writes.append((key, payload))

    def request_read(self, _char_key):
        pass  # the test drives config_read directly


class _FakeSignal:
    def __init__(self):
        self._slots = []

    def connect(self, slot):
        self._slots.append(slot)

    def emit(self, *args):
        for slot in self._slots:
            slot(*args)


class _FakeBt:
    """Stand-in for BluetoothWindow: sessions() plus the no-ops closeEvent needs."""

    def __init__(self, sessions: dict):
        self._sessions = sessions

    def sessions(self):
        return self._sessions

    def stop_all_sessions(self):
        pass

    def close(self):
        pass


@pytest.fixture(autouse=True)
def _detach_bluetooth_window(win):
    """closeEvent() calls stop_all_sessions()/close() on _bluetooth_window if set —
    a real BluetoothWindow can do that safely, a test's fake sessions dict cannot,
    so make sure teardown never sees the fake."""
    yield
    win._bluetooth_window = None


def test_a_read_for_slot_x_lands_on_slot_x_even_after_the_user_switches_rows(win):
    """The historical bug: _on_board_mode_read used to re-ask "what's selected
    now" instead of using the slot the read was actually requested for."""
    slot0_id, slot1_id = win._slot_user_id(0), win._slot_user_id(1)
    session_a = _FakeSession(slot_index=0, user_id=slot0_id)
    session_b = _FakeSession(slot_index=1, user_id=slot1_id)
    win._bluetooth_window = _FakeBt({"a": session_a, "b": session_b})

    win._on_user_selected(slot1_id)  # select csvPt (slot 1) -> triggers a read
    # Before the (simulated) response arrives, the user switches back to slot 0.
    win._on_user_selected(slot0_id)

    # The response for slot 1's read arrives late, tagged to session_b (slot 1).
    session_b.config_read.emit("addr-b", "data_source", protocol.encode_data_source(True))

    assert win._board_mode.label(1) == "CSV replay"
    assert win._board_mode.label(0) is None  # untouched by slot 1's answer


def test_fe_title_agrees_with_the_main_title_for_an_unsent_csv_change(win):
    """Reproduces the exact report: the board already confirmed this slot is
    running a model (an earlier read cached it); the person's LOCAL profile is
    then switched to CSV in Person Configuration but never sent. The main
    title correctly keeps trusting the board's cached answer over the stale
    local edit — _fe_graph_title must agree, not read the local profile on its
    own and flip to CSV immediately."""
    slot = 1
    win._board_mode._model[slot] = "Cambridge (Hovorka)"  # confirmed by an earlier read
    user_id = win._slot_user_id(slot)
    win._bluetooth_window = _FakeBt({"b": _FakeSession(slot_index=slot, user_id=user_id)})

    win._on_user_selected(user_id)

    assert win._data_source_label() == "Cambridge (Hovorka)"
    # The old bug: _fe_graph_title read self._selected_person().data_source
    # unconditionally ("csv" — never sent to the board) and switched to the
    # report-only CSV label immediately, disagreeing with the title above.
    assert win._fe_graph_title() == "Food / Exercise"

    win._board_mode._is_csv[slot] = True  # the board now genuinely confirms CSV
    assert win._data_source_label() == "CSV replay"
    assert "report-only" in win._fe_graph_title().lower()


def test_pisa_span_only_shades_the_targeted_slot(win):
    win._restart_engine()
    win._on_user_selected(win._slot_user_id(0))  # viewing slot 0

    win._record_pisa_span(1, 100.0, 160.0)  # fault targets slot 1, not the one on screen

    assert win._hist(win._slot_user_id(1))["pisa"] == [(100.0, 160.0)]
    assert win._hist(win._slot_user_id(0))["pisa"] == []
    assert win._graph.buf.pisa_spans == []  # slot 0 is bound and must stay unshaded

    win._on_user_selected(win._slot_user_id(1))  # switch to the targeted slot
    assert win._graph.buf.pisa_spans == [(100.0, 160.0)]


def test_pisa_span_targeting_all_sensors_reaches_every_slot(win):
    win._restart_engine()
    win._record_pisa_span(None, 5.0, 20.0)
    for slot in range(bl.MAX_SLOTS):
        assert (5.0, 20.0) in win._hist(win._slot_user_id(slot))["pisa"]
