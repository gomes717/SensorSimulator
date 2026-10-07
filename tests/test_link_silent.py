"""The silent-subscribe watchdog as wired into BleSession and the Bluetooth window.

No radio: a real BleSession object is driven with a fake client and characteristic.
"""

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from api import protocol
from gui.bluetooth_window import BluetoothWindow
from services import ble_session
from services.ble_session import CGM_MEASUREMENT_UUID, FAST_COMM_INTERVAL_SECONDS, BleSession

LIMIT = 4 * FAST_COMM_INTERVAL_SECONDS
MEASUREMENT = bytes.fromhex("0600f9f34900")  # a real CGM Measurement: glucose 101.7


class FakeClient:
    def __init__(self, fail_start=False):
        self.calls = []
        self._fail_start = fail_start

    async def stop_notify(self, char):
        self.calls.append("stop")

    async def start_notify(self, char, _handler):
        self.calls.append("start")
        if self._fail_start:
            raise OSError("[WinError -2147483629] object closed")


@pytest.fixture
def session(monkeypatch):
    now = {"t": 1000.0}
    monkeypatch.setattr(ble_session.time, "monotonic", lambda: now["t"])
    s = BleSession("AA:BB", "Nordic Glucose Sensor 4")
    s._instance_by_handle = {7: 3}
    s._instance_count = 4
    s._watchdog.arm(now["t"])
    s.advance = lambda secs: now.__setitem__("t", now["t"] + secs)
    s.signals = []
    s.link_silent.connect(lambda addr, silent: s.signals.append((addr, silent)))
    return s


def _char():
    return SimpleNamespace(uuid=CGM_MEASUREMENT_UUID, handle=7)


def _tick(session, client):
    asyncio.run(session._check_silence(client, _char()))


def test_a_silent_subscription_is_rearmed_then_reported_once(session):
    client = FakeClient()
    for _ in range(3):
        session.advance(LIMIT)
        _tick(session, client)
    assert client.calls == ["stop", "start"] * 3
    assert session.signals == [("AA:BB", True)]
    session.advance(LIMIT)
    _tick(session, client)
    assert session.signals == [("AA:BB", True)]  # keeps re-arming, does not repeat


def test_measurements_after_a_report_clear_it(session):
    client = FakeClient()
    for _ in range(3):
        session.advance(LIMIT)
        _tick(session, client)
    session._handle_notification(_char(), bytearray(MEASUREMENT))
    assert session.signals == [("AA:BB", True), ("AA:BB", False)]


def test_a_failing_rearm_does_not_kill_the_session_loop(session):
    session.advance(LIMIT)
    _tick(session, FakeClient(fail_start=True))  # must not raise


def test_other_characteristics_do_not_count_as_data(session):
    other = SimpleNamespace(uuid="00001a00-0000-1000-8000-00805f9b34fb", handle=9)
    for _ in range(3):
        session.advance(LIMIT)
        session._handle_notification(other, bytearray(b"\x01" + b"\x00" * 9))
        _tick(session, FakeClient())
    assert session.signals == [("AA:BB", True)]


def test_a_stopped_board_is_not_reported_and_resuming_gets_grace(session):
    client = FakeClient()
    session.queue_write("run_state", protocol.encode_run_state(protocol.RUN_STATE_STOPPED))
    for _ in range(5):
        session.advance(LIMIT)
        _tick(session, client)
    assert client.calls == [] and session.signals == []
    session.queue_write("run_state", protocol.encode_run_state(protocol.RUN_STATE_RUNNING))
    session.advance(LIMIT - 1)
    _tick(session, client)
    assert client.calls == []  # still inside the grace period


def test_bluetooth_window_shows_and_clears_the_silent_state():
    shown = {}
    fake = SimpleNamespace(
        _names={"AA:BB": "Sensor 4"},
        _set_status_cell=lambda addr, text: shown.__setitem__(addr, text),
        _status=SimpleNamespace(setText=lambda text: shown.__setitem__("message", text)),
    )
    BluetoothWindow._on_link_silent(fake, "AA:BB", True)
    assert shown["AA:BB"] == "No data" and "Sensor 4" in shown["message"]
    BluetoothWindow._on_link_silent(fake, "AA:BB", False)
    assert shown["AA:BB"] == "Connected"


# ---------------------------------------------------------------------------
# Link drop + reconnect race (board power-cycled while the app is running)
# ---------------------------------------------------------------------------


def _window_double(sessions):
    log = SimpleNamespace(offline=[], note_disconnected=lambda a: log.offline.append(a))
    shown = {}
    return SimpleNamespace(
        _sessions=sessions,
        _names={"AA:BB": "Sensor 1"},
        _advertised={"AA:BB": "Nordic Glucose Sensor 1"},
        _statuses={"AA:BB": "Connected"},
        _ble_log=log,
        shown=shown,
        _set_status_cell=lambda addr, text: shown.__setitem__(addr, text),
        _status=SimpleNamespace(setText=lambda text: shown.__setitem__("message", text)),
        _update_button_states=lambda: None,
    )


def test_a_late_finish_from_the_old_session_leaves_the_new_one_alone():
    old, new = object(), object()
    win = _window_double({"AA:BB": new})
    BluetoothWindow._on_session_finished(win, "AA:BB", old)
    assert win._sessions == {"AA:BB": new}
    assert win._advertised == {"AA:BB": "Nordic Glucose Sensor 1"}
    assert win._statuses == {"AA:BB": "Connected"}


def test_finishing_the_current_session_cleans_up_but_keeps_the_advertised_name():
    """A failed connect must not cost the row its name: the retry would otherwise
    open under the bare MAC and stream every sensor into one row."""
    cur = object()
    win = _window_double({"AA:BB": cur})
    BluetoothWindow._on_session_finished(win, "AA:BB", cur)
    assert win._sessions == {} and win._statuses == {}
    assert win._advertised == {"AA:BB": "Nordic Glucose Sensor 1"}


def test_a_late_disconnect_from_the_old_session_does_not_badge_the_new_one():
    old, new = object(), object()
    win = _window_double({"AA:BB": new})
    BluetoothWindow._on_session_disconnected(win, "AA:BB", old)
    assert win._ble_log.offline == [] and "AA:BB" not in win.shown


def test_the_current_sessions_disconnect_badges_it_offline():
    cur = object()
    win = _window_double({"AA:BB": cur})
    win._on_disconnected = lambda addr: BluetoothWindow._on_disconnected(win, addr)
    BluetoothWindow._on_session_disconnected(win, "AA:BB", cur)
    assert win._ble_log.offline == ["AA:BB"]


def test_a_dropped_link_ends_the_session_loop(monkeypatch):
    """The peripheral vanishing (board powered off) must end the session: it used
    to idle forever on a dead client, still flagged live and "Connected"."""

    class DroppingClient:
        is_connected = True
        services = ()

        def __init__(self, _address, disconnected_callback=None):
            self._cb = disconnected_callback

        async def __aenter__(self):
            asyncio.get_running_loop().call_later(0.3, self._cb, self)
            return self

        async def __aexit__(self, *_exc):
            return False

    monkeypatch.setattr(ble_session, "BleakClient", DroppingClient)
    s = BleSession("AA:BB", "Nordic Glucose Sensor 1")
    seen = []
    s.disconnected.connect(seen.append)
    asyncio.run(asyncio.wait_for(s._session(), timeout=5))
    assert seen == ["AA:BB"] and s.is_live is False


def test_a_rescan_without_a_name_keeps_the_name_already_learned():
    rows = []
    win = SimpleNamespace(
        _addresses=[],
        _advertised={"AA:BB": "Nordic Glucose Sensor 2"},
        _statuses={},
        _table=SimpleNamespace(
            rowCount=lambda: 0,
            insertRow=lambda row: None,
            setItem=lambda row, col, item: rows.append((col, item.text())),
        ),
    )
    BluetoothWindow._add_device(win, "Unknown device", "AA:BB", -50)
    assert win._advertised["AA:BB"] == "Nordic Glucose Sensor 2"
    assert (0, "Nordic Glucose Sensor 2") in rows
