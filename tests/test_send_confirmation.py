"""Config-send feedback: a link that never subscribed the reset-sync channel
must say so rather than time out and blame the connection."""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QApplication, QLabel

from api import ble_uuids
from gui.device_target import await_send_confirmation

_app = QApplication.instance() or QApplication([])


class _Session(QLabel):  # QLabel only to get a QObject with signals
    reset_sync = pyqtSignal(str)

    def __init__(self, subscribed):
        super().__init__()
        self._subscribed = {u.lower() for u in subscribed}

    def notifies(self, uuid):
        return uuid.lower() in self._subscribed


def test_a_link_without_reset_sync_reports_that_instead_of_waiting():
    status = QLabel()
    await_send_confirmation(_Session(subscribed=[]), status)
    assert "no confirmation channel" in status.text()
    assert "check connection" not in status.text()


def test_a_link_with_reset_sync_waits_then_confirms():
    status = QLabel()
    session = _Session(subscribed=[ble_uuids.RESET_SYNC_UUID])

    await_send_confirmation(session, status)
    assert status.text() == "Sending to board…"

    session.reset_sync.emit("aa:bb")
    assert status.text() == "✓ Applied on board"


def test_nothing_is_committed_until_the_board_acknowledges():
    """The slot -> patient rename must describe the board, not the attempt: it
    runs on the acknowledgement and never on a send that is merely queued."""
    status = QLabel()
    session = _Session(subscribed=[ble_uuids.RESET_SYNC_UUID])
    renamed = []

    await_send_confirmation(session, status, on_confirmed=lambda: renamed.append("done"))
    assert renamed == []  # queued, not yet acknowledged

    session.reset_sync.emit("aa:bb")
    assert renamed == ["done"]


def test_a_link_with_no_confirmation_channel_commits_nothing():
    renamed = []
    await_send_confirmation(_Session(subscribed=[]), QLabel(), on_confirmed=renamed.append)
    assert renamed == []
