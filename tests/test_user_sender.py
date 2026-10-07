"""Users-screen slice 8 (Send to…): pushing one user to one sensor and reporting how it went
(gui/user_sender.py). A fake session plays the board; the test emits its answer."""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication

from gui.user_sender import UserSender, slot_of
from models import user_store
from models.types import CsvTrack


@pytest.fixture(scope="module", autouse=True)
def _app():
    return QApplication.instance() or QApplication([])


class _Signal:
    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)

    def disconnect(self, slot):
        self.slots.remove(slot)  # like Qt: TypeError-free only if connected

    def emit(self, *args):
        for slot in list(self.slots):
            slot(*args)


class _Session:
    def __init__(self, slot_index: int | None = 1, live=True, exposes=("user_name",)):
        self.slot_index = slot_index
        self.is_live = live
        self._exposes = set(exposes)
        self.board_layout_finished = _Signal()
        self.pushes: list[tuple[list, bool]] = []

    def exposes(self, key):
        return key in self._exposes

    def send_board_layout(self, entries, *, run=True):
        self.pushes.append((entries, run))

    def finish(self, ok=True, message="done"):
        self.board_layout_finished.emit("AA:BB", ok, message)


def _send(session, user=None, sender=None):
    sender = sender or UserSender(timeout_ms=60_000)
    done: list[tuple[bool, str]] = []
    started = sender.send(
        session, user or user_store.new_user("Ana"), lambda ok, message: done.append((ok, message))
    )
    return sender, started, done


# -- the happy path ---------------------------------------------------------------------------


def test_a_send_pushes_one_entry_for_the_sessions_slot_and_leaves_the_board_running():
    session = _Session(slot_index=2)
    _send(session)
    [(entries, run)] = session.pushes
    assert [e["slot"] for e in entries] == [2]
    assert run is True
    assert entries[0]["writes"][0][0] == "user_name"


def test_the_board_taking_the_writes_reports_success_naming_user_and_sensor():
    session = _Session(slot_index=1)
    sender, started, done = _send(session)
    assert started and sender.busy
    session.finish(True)
    [(ok, message)] = done
    assert ok is True
    assert "Ana" in message and "2" in message  # sensor 2
    assert not sender.busy


def test_a_single_sensor_board_is_slot_zero():
    session = _Session(slot_index=None)
    _send(session)
    assert session.pushes[0][0][0]["slot"] == 0
    assert slot_of(session) == 0


def test_a_csv_user_pushes_its_upload_too():
    user = user_store.new_user("Csv")
    user.mode = "csv"
    user.csv = CsvTrack(samples=[100] * 288, interval_s=300, foodlog=[], start_iso=None)
    session = _Session()
    _send(session, user)
    assert session.pushes[0][0][0]["csv"]["uploads"]


# -- failures ---------------------------------------------------------------------------------


def test_the_board_refusing_reports_why():
    session = _Session()
    sender, _, done = _send(session)
    session.finish(False, "device does not expose 'food'")
    assert done == [(False, "Could not send: device does not expose 'food'")]
    assert not sender.busy


def test_a_user_that_cannot_be_sent_is_refused_without_touching_the_board():
    user = user_store.new_user("Csv")
    user.mode = "csv"  # no window
    session = _Session()
    sender, started, done = _send(session, user)
    assert started and session.pushes == []
    assert done[0][0] is False and "CSV" in done[0][1]
    assert not sender.busy


def test_a_dead_link_fails_at_once():
    session = _Session(live=False)
    _, _, done = _send(session)
    assert session.pushes == []
    assert done[0][0] is False and "not connected" in done[0][1]


def test_a_board_whose_services_are_stale_explains_the_cache():
    session = _Session(exposes=())  # no user_name characteristic visible
    _, _, done = _send(session)
    assert session.pushes == []
    assert done[0][0] is False
    assert "reconnect" in done[0][1] and "user_name" in done[0][1]


def test_no_answer_in_time_is_a_failure():
    session = _Session()
    sender, _, done = _send(session)
    sender._timed_out(sender._generation)
    assert done[0][0] is False and "did not answer" in done[0][1]
    assert not sender.busy


def test_a_late_answer_after_the_timeout_is_ignored():
    session = _Session()
    sender, _, done = _send(session)
    sender._timed_out(sender._generation)
    session.finish(True)  # too late: the failure was already reported
    assert len(done) == 1


def test_a_stale_timeout_from_an_earlier_send_does_not_fail_a_later_one():
    session = _Session()
    sender, _, done = _send(session)
    first = sender._generation
    session.finish(True)
    _send(session, sender=sender)  # a second send
    sender._timed_out(first)  # the first send's timer fires now
    assert len(done) == 1 and sender.busy  # the second is still waiting


# -- one at a time ---------------------------------------------------------------------------------


def test_only_one_send_at_a_time():
    session = _Session()
    sender, started, _ = _send(session)
    assert started
    other = _Session(slot_index=0)
    done: list = []
    assert sender.send(other, user_store.new_user("Bo"), lambda *a: done.append(a)) is False
    assert other.pushes == [] and done == []


def test_it_can_send_again_once_the_first_finished():
    session = _Session()
    sender, _, _ = _send(session)
    session.finish(True)
    _, started, _ = _send(session, sender=sender)
    assert started


def test_the_finished_handler_is_removed_so_sends_do_not_pile_up():
    session = _Session()
    _send(session)
    session.finish(True)
    assert session.board_layout_finished.slots == []
