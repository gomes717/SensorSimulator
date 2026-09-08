"""Regression pin for issue 06: a BLE disconnect marks the user's tree row
offline, and a reconnect clears it.

Runs the real MainWindow on Qt's offscreen platform.
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication

_TS = "2020-01-01T00:00:00+00:00"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(app):
    import gui.main_window as mw

    w = mw.MainWindow()
    w._cgms_only = True  # let glucose messages through the recording gate
    yield w
    w.close()


def _msg(user_id, dev_id, glucose, ts=_TS):
    return {"user_id": user_id, "dev_id": dev_id, "glucose_value": glucose, "timestamp": ts}


def test_disconnect_marks_only_that_devices_rows_offline(win):
    win._ble_log.add_message(_msg("Pt A", "AA:BB", 101.0))
    win._ble_log.add_message(_msg("Pt B", "CC:DD", 99.0))
    row_a, row_b = win._user_items["Pt A"], win._user_items["Pt B"]

    win._ble_log.note_disconnected("AA:BB")

    assert "offline" in row_a.text(0)
    assert row_a.text(1) == "—"
    assert "Pt A" in win._offline_users
    assert "offline" not in row_b.text(0)  # other device untouched
    assert "Pt B" not in win._offline_users


def test_reconnect_clears_offline(win):
    win._ble_log.add_message(_msg("Pt A", "AA:BB", 101.0))
    win._ble_log.note_disconnected("AA:BB")
    assert "Pt A" in win._offline_users

    win._ble_log.add_message(_msg("Pt A", "AA:BB", 102.0, ts="2020-01-01T00:00:05+00:00"))

    assert "Pt A" not in win._offline_users
    assert "offline" not in win._user_items["Pt A"].text(0)
    assert win._user_items["Pt A"].text(1) == "102.00"
