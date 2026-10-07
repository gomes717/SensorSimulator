"""Regression pin for issue 06: a BLE disconnect greys the user's tab
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
    w.state.cgms_only = True  # let glucose messages through the recording gate
    yield w
    w.close()


def _msg(user_id, dev_id, glucose, ts=_TS):
    return {"user_id": user_id, "dev_id": dev_id, "glucose_value": glucose, "timestamp": ts}


def test_disconnect_marks_only_that_devices_rows_offline(win):
    win._ble_log.add_message(_msg("Pt A", "AA:BB", 101.0))
    win._ble_log.add_message(_msg("Pt B", "CC:DD", 99.0))
    tab_a, tab_b = win.tabs.header("Pt A"), win.tabs.header("Pt B")

    win._ble_log.note_disconnected("AA:BB")

    assert tab_a.offline and not tab_a.isEnabled()
    assert win.tabs.is_offline("Pt A")
    assert not tab_b.offline  # other device untouched
    assert not win.tabs.is_offline("Pt B")


def test_reconnect_clears_offline(win):
    win._ble_log.add_message(_msg("Pt A", "AA:BB", 101.0))
    win._ble_log.note_disconnected("AA:BB")
    assert win.tabs.is_offline("Pt A")

    win._ble_log.add_message(_msg("Pt A", "AA:BB", 102.0, ts="2020-01-01T00:00:05+00:00"))

    assert not win.tabs.is_offline("Pt A")
    assert win.tabs.header("Pt A").isEnabled()
