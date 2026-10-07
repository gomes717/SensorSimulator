"""A sensor reconnected right after it was closed can be handed Windows' already-closed GATT
objects ("[WinError -2147483629] the object was closed"): the session discovers again without the
cache instead of reporting a half-working connection (services/ble_session.py)."""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from typing import Any

import pytest

pytest.importorskip("PyQt6.QtCore")

from PyQt6.QtCore import QCoreApplication
from PyQt6.QtTest import QTest as _QTest

import services.ble_session as bs

_CLOSED = "[WinError -2147483629] O objeto foi fechado."


QTest: Any = _QTest  # the PyQt6 stubs reject valid QTest calls


@pytest.fixture(scope="module", autouse=True)
def _app():
    return QCoreApplication.instance() or QCoreApplication([])


class _Char:
    def __init__(self, uuid, handle, properties):
        self.uuid = uuid
        self.handle = handle
        self.properties = properties


class _Service:
    def __init__(self):
        self.uuid = bs.CGM_SERVICE_UUID
        self.characteristics = [_Char(bs.CGM_MEASUREMENT_UUID, 1, ["notify"])]


def _fake_client(attempts: list[dict], closed_attempts: int):
    """A BleakClient stand-in whose first *closed_attempts* connections fail to subscribe."""

    class Client:
        def __init__(self, _address, disconnected_callback=None, **options):
            attempts.append(options)
            self.options = options
            self.is_connected = True
            self.services = [_Service()]
            self._closed = len(attempts) <= closed_attempts

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def start_notify(self, _char, _callback):
            if self._closed:
                raise OSError(_CLOSED)

        async def stop_notify(self, _char):
            pass

        async def write_gatt_char(self, *_args, **_kwargs):
            pass

    return Client


def _connect(monkeypatch, closed_attempts: int):
    attempts: list[dict] = []
    monkeypatch.setattr(bs, "BleakClient", _fake_client(attempts, closed_attempts))
    monkeypatch.setattr(bs, "sys", type("S", (), {"platform": "linux"}))  # no Windows pairing
    monkeypatch.setattr(bs.time, "sleep", lambda _s: None)
    session = bs.BleSession("AA:BB", "Nordic Glucose Sensor 1", silence_limit_s=1e9)
    outcome: list[tuple] = []
    session.connected.connect(lambda *args: outcome.append(("connected", *args)))
    session.connect_failed.connect(lambda *args: outcome.append(("failed", *args)))
    session.start()
    for _ in range(100):
        if outcome:
            break
        QTest.qWait(50)
    session.stop()
    session.wait(5000)
    return attempts, outcome


def test_closed_services_are_discovered_again_without_the_cache(monkeypatch):
    attempts, outcome = _connect(monkeypatch, closed_attempts=1)
    assert len(attempts) == 2
    assert attempts[0] == {}  # the first connection may use the cache
    assert attempts[1] == {"winrt": {"use_cached_services": False}}
    assert outcome[0][0] == "connected"
    assert outcome[0][2] >= 1  # subscribed
    assert outcome[0][4] == ""  # and nothing to apologise for


def test_it_gives_up_after_one_fresh_attempt_instead_of_looping(monkeypatch):
    attempts, outcome = _connect(monkeypatch, closed_attempts=5)
    assert len(attempts) == 2
    assert outcome[0][0] == "connected"
    assert "reconnect" in outcome[0][4]  # the user is told what is wrong


def test_a_healthy_connection_is_not_retried(monkeypatch):
    attempts, outcome = _connect(monkeypatch, closed_attempts=0)
    assert len(attempts) == 1
    assert outcome[0][0] == "connected" and outcome[0][4] == ""
