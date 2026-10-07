"""Closing a sensor's tab closes it and moves to another — a reading that was already on its way
must not bring the tab back (greyed) afterwards (gui/sensor_controller.py)."""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication

_TS = "2026-01-01T00:00:00+00:00"


@pytest.fixture(scope="module", autouse=True)
def _app():
    return QApplication.instance() or QApplication([])


class _Signal:
    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)


class _Session:
    def __init__(self, slot_index, user_id):
        self.slot_index = slot_index
        self.user_id = user_id
        self.is_live = True
        self.config_read = _Signal()
        self.write_failed = _Signal()

    def queue_write(self, *_a):
        pass

    def request_read(self, *_a):
        pass


class _Bluetooth:
    def __init__(self, sessions):
        self.sessions_by_address = sessions
        self.disconnected: list[str] = []

    def sessions(self):
        return self.sessions_by_address

    def disconnect_device(self, address):
        self.disconnected.append(address)

    def relabel(self):
        pass

    def display_name(self, address):
        return address

    def stop_all_sessions(self):
        pass

    def close(self):
        pass


@pytest.fixture
def win():
    import gui.main_window as mw

    w = mw.MainWindow()
    w.state.model_only = False
    sessions = {
        "a": _Session(0, "S1"),
        "b": _Session(1, "S2"),
    }
    w.windows.bluetooth = _Bluetooth(sessions)
    for address, session in sessions.items():
        w.sensors.on_session_connected(address, session)
    yield w
    w.sim.engines.stop_all()
    w.windows.bluetooth = None
    w.close()


def _reading(key, address):
    return {"user_id": key, "dev_id": address, "glucose_value": 100.0, "timestamp": _TS}


def test_closing_a_tab_removes_it_disconnects_and_selects_another(win):
    assert win.tabs.tab_keys() == ["S1", "S2"]
    win.sensors.on_user_selected("S1")
    win.sensors.on_tab_close_requested("S1")
    assert win.tabs.tab_keys() == ["S2"]
    assert win.windows.bluetooth.disconnected == ["a"]
    assert win.sensors.selected_user == "S2"


def test_a_reading_already_on_its_way_does_not_bring_the_closed_tab_back(win):
    """Disconnecting waits for the session to stop; a reading it had queued is delivered after,
    and used to re-create the tab (the disconnect notice then greyed it)."""
    win.sensors.on_tab_close_requested("S1")
    win.sensors.on_new_message(_reading("S1", "a"))  # the late one
    assert win.tabs.tab_keys() == ["S2"]


def test_the_late_disconnect_notice_of_a_closed_sensor_changes_nothing(win):
    win.sensors.on_tab_close_requested("S1")
    win.tabs.mark_device_offline("a")
    assert win.tabs.tab_keys() == ["S2"]


def test_other_sensors_keep_streaming_after_one_is_closed(win):
    win.sensors.on_tab_close_requested("S1")
    win.sensors.on_new_message(_reading("S2", "b"))
    assert win.tabs.tab_keys() == ["S2"]


def test_a_closed_sensor_that_connects_again_gets_its_tab_back(win):
    win.sensors.on_tab_close_requested("S1")
    session = _Session(0, "S1")
    win.windows.bluetooth.sessions_by_address["a"] = session
    win.sensors.on_session_connected("a", session)
    assert "S1" in win.tabs.tab_keys()
    win.sensors.on_new_message(_reading("S1", "a"))  # and its readings count again
    assert win.tabs.tab_keys().count("S1") == 1


def test_closing_the_last_tab_goes_back_to_the_empty_page(win):
    win.sensors.on_tab_close_requested("S1")
    win.sensors.on_tab_close_requested("S2")
    assert win.tabs.tab_keys() == []
    assert win.sensors.selected_user is None
