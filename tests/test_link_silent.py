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
