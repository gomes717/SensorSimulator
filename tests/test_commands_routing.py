"""A command from a sensor page's Commands panel reaches THAT sensor — never the
selected row, never every slot of a multi-sensor board."""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class _Signal:
    def connect(self, _slot):
        pass


class _Done:
    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)

    def disconnect(self, slot):
        self.slots.remove(slot)

    def emit(self, *args):
        for slot in list(self.slots):
            slot(*args)


class _Session:
    """Hashable stand-in for a live BleSession (BoardMode wires its config_read)."""

    config_read = _Signal()

    def __init__(self, slot_index, user_id, live=True):
        self.slot_index = slot_index
        self.user_id = user_id
        self.is_live = live
        self.board_layout_finished = _Done()

    def exposes(self, _key):
        return True

    def send_board_layout(self, _entries, *, run=True):
        """Start's config push: the board takes it at once."""
        self.board_layout_finished.emit("addr", True, "applied")

    def request_read(self, _key):
        pass

    def queue_write(self, *_args):
        pass


class _FakeBt:
    def __init__(self, sessions):
        self._sessions = sessions

    def sessions(self):
        return self._sessions

    def stop_all_sessions(self):
        pass

    def close(self):
        pass


@pytest.fixture
def sent():
    return []


@pytest.fixture
def win(app, sent, monkeypatch):
    import gui.main_window as mw

    w = mw.MainWindow()
    events = w._events
    monkeypatch.setattr(
        events, "inject_food", lambda slot, dur, carbs: sent.append(("food", slot, dur, carbs))
    )
    monkeypatch.setattr(
        events, "inject_exercise", lambda slot, dur, pct: sent.append(("ex", slot, dur, pct))
    )
    monkeypatch.setattr(
        events, "inject_fault", lambda kind, vals, slot=None: sent.append((kind, slot, vals))
    )
    yield w
    w.windows.bluetooth = None
    w.close()


def _multi_sensor_board(win, live=(True, True)):
    ids = [win.directory.slot_user_id(i) for i in range(len(live))]
    sessions = {f"a{i}": _Session(i, ids[i], live[i]) for i in range(len(live))}
    win.windows.bluetooth = _FakeBt(sessions)
    pages = [win.sensors.page_of_user(uid) for uid in ids]
    for page in pages:
        page.commands.set_blocked("")  # the block rules have their own tests
    return pages


def test_a_page_sends_to_its_own_slot_whatever_is_selected(win, sent):
    page0, page1 = _multi_sensor_board(win)
    win.sensors.on_user_selected(page0.key)  # page 0 is on screen...
    assert page1.slot == 1
    page1.commands.send_food(55.0, 40)  # ...but the command comes from page 1
    page1.commands.send_exercise(20, 60.0)
    page1.commands.send_pisa(12, 35.0)
    assert sent == [
        ("food", 1, 40, 55.0),
        ("ex", 1, 20, 60.0),
        ("pisa", 1, (12, pytest.approx(0.35))),
    ]


def test_each_pages_commands_go_to_their_own_slot(win, sent):
    page0, page1 = _multi_sensor_board(win)
    page0.commands.send_food(50.0, 15)
    page1.commands.send_food(50.0, 15)
    assert [entry[1] for entry in sent] == [0, 1]


def test_a_single_sensor_page_broadcasts_with_no_slot(win, sent):
    win.windows.bluetooth = _FakeBt({"a": _Session(None, "Nordic")})
    page = win.sensors.page_of_user("Nordic")
    page.commands.set_blocked("")
    page.commands.send_food(50.0, 15)
    assert sent and sent[0][1] is None


def test_an_offline_sensors_commands_are_blocked_but_a_live_ones_are_not(win):
    live_page, dead_page = _multi_sensor_board(win, live=(True, False))
    win._run.start()  # a run in progress, so the only block left is the link
    win.sensors.refresh_commands()
    assert live_page.commands.blocked_reason == ""
    assert "offline" in dead_page.commands.blocked_reason
    assert not any(b.isEnabled() for b in dead_page.commands.send_buttons())


def test_the_result_is_shown_on_the_page_that_sent_it(win, monkeypatch):
    page0, page1 = _multi_sensor_board(win)
    monkeypatch.setattr(win._events, "inject_food", lambda slot, dur, carbs: "✓ sent")
    page1.commands.send_food(50.0, 15)
    assert page1.commands.result_text() == "✓ sent"
    assert page0.commands.result_text() == ""
