"""The app follows the speed the board reports (entering the Dexcom mode puts the board at x1):
gui/board_profile.py reads it on connect, gui/main_window.py adopts it without writing it back."""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication

from api import protocol
from gui.board_profile import BoardProfileController
from gui.config_controller import ConfigController

_APP = QApplication.instance() or QApplication([])


class _Session(QObject):
    config_read = pyqtSignal(str, str, bytes)

    def __init__(self):
        super().__init__()
        self.reads: list[str] = []

    def request_read(self, key):
        self.reads.append(key)


def _controller():
    c = ConfigController(lambda: 1.0)
    return BoardProfileController(c, lambda: None, lambda _text: None)


def test_a_connecting_sensor_is_asked_for_the_boards_speed():
    ctrl, session = _controller(), _Session()
    ctrl.on_session_connected(session)
    assert "speed" in session.reads


def test_the_boards_speed_answer_is_announced():
    ctrl, session = _controller(), _Session()
    seen = []
    ctrl.speed_seen.connect(seen.append)
    ctrl.on_session_connected(session)
    session.config_read.emit("aa", "speed", protocol.encode_speed(1.0))
    assert seen == [1.0]


def test_a_short_speed_answer_is_ignored():
    ctrl, session = _controller(), _Session()
    seen = []
    ctrl.speed_seen.connect(seen.append)
    ctrl.on_session_connected(session)
    session.config_read.emit("aa", "speed", b"\x00")
    assert seen == []


@pytest.fixture
def win():
    import gui.main_window as mw

    w = mw.MainWindow()
    yield w
    w.close()


def test_the_app_follows_the_board_to_x1_without_writing_it_back(win, monkeypatch):
    win.state.set_speed(60.0)
    win._clock.set_speed(60.0)
    sent = []
    monkeypatch.setattr(win._board, "broadcast", lambda *args: sent.append(args))
    win._adopt_board_speed(1.0)
    assert win.state.speed_mult == 1.0
    assert win._clock.speed_mult == 1.0
    assert win._controller.speed_mult == 1.0
    assert sent == []  # the board already runs at x1: a write would only reset its simulation


def test_the_configuration_window_shows_the_speed_the_board_runs_at(win):
    win.state.set_speed(60.0)
    win._adopt_board_speed(1.0)
    combo = win._configuration_window.speed_combo
    assert combo.currentData() == 1.0


def test_the_same_speed_changes_nothing(win):
    win.state.set_speed(1.0)
    restarts = []
    win.sim.restart = lambda: restarts.append(1)
    win._adopt_board_speed(1.0)
    assert restarts == []
