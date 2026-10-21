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


# ---- a live link whose objects close under it --------------------------------------------------


class _LiveService(_Service):
    def __init__(self):
        super().__init__()
        self.characteristics.append(_Char(bs.ble_uuids.SPEED_UUID, 2, ["read", "write"]))


class _LiveClient:
    """Subscribing works; the config characteristic's reads and writes fail with the closed-object
    error on the first ``closed_connections`` connections (set by :func:`_live_client`)."""

    attempts: list[dict]
    done: list[tuple]
    closed_connections: int

    def __init__(self, _address, disconnected_callback=None, **options):
        self.attempts.append(options)
        self.is_connected = True
        self.services = [_LiveService()]
        self._closed = len(self.attempts) <= self.closed_connections

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def start_notify(self, _char, _callback):
        pass

    def _check(self, char) -> bool:
        """True for the config characteristic on a healthy connection."""
        if char.handle != 2:
            return False
        if self._closed:
            raise OSError(_CLOSED)
        return True

    async def write_gatt_char(self, char, payload, **_kwargs):
        if self._check(char):
            self.done.append(("write", bytes(payload)))

    async def read_gatt_char(self, char):
        self._check(char)
        self.done.append(("read", char.handle))
        return b""


def _live_client(attempts: list[dict], done: list[tuple], closed_connections: int):
    return type(
        "Client",
        (_LiveClient,),
        {"attempts": attempts, "done": done, "closed_connections": closed_connections},
    )


def _run_live(monkeypatch, closed_connections: int, act):
    attempts: list[dict] = []
    done: list[tuple] = []
    monkeypatch.setattr(bs, "BleakClient", _live_client(attempts, done, closed_connections))
    monkeypatch.setattr(bs, "sys", type("S", (), {"platform": "linux"}))
    monkeypatch.setattr(bs.time, "sleep", lambda _s: None)
    session = bs.BleSession("AA:BB", "Nordic Glucose Sensor 1", silence_limit_s=1e9)
    events: list[tuple] = []
    session.connected.connect(lambda *args: events.append(("connected", *args)))
    session.connect_failed.connect(lambda *args: events.append(("connect_failed", *args)))
    session.write_failed.connect(lambda *args: events.append(("write_failed", *args)))
    session.config_read.connect(lambda *args: events.append(("config_read", *args)))
    session.start()
    for _ in range(100):
        if events:
            break
        QTest.qWait(50)
    act(session)
    for _ in range(100):
        if len(events) > 1 or done:
            break
        QTest.qWait(50)
    QTest.qWait(300)  # long enough for a stray second signal to show up
    session.stop()
    session.wait(5000)
    return attempts, done, events


def test_a_write_that_finds_the_objects_closed_is_retried_on_a_fresh_connection(monkeypatch):
    attempts, done, events = _run_live(monkeypatch, 1, lambda s: s.queue_write("speed", b"\x05"))
    assert len(attempts) == 2
    assert attempts[1] == {"winrt": {"use_cached_services": False}}
    assert done == [("write", b"\x05")]  # exactly once, on the second connection
    assert [e[0] for e in events] == ["connected"]  # the recovery is not announced, nor a failure


def test_a_read_that_finds_the_objects_closed_is_asked_again(monkeypatch):
    attempts, done, events = _run_live(monkeypatch, 1, lambda s: s.request_read("speed"))
    assert len(attempts) == 2
    assert done == [("read", 2)]
    assert [e[0] for e in events] == ["connected", "config_read"]
    assert events[1][2:] == ("speed", b"\x07")


def test_it_retries_a_closed_write_only_once(monkeypatch):
    attempts, done, events = _run_live(monkeypatch, 5, lambda s: s.queue_write("speed", b"\x05"))
    assert len(attempts) == 2  # no reconnect loop
    assert done == []
    assert [e[0] for e in events] == ["connected", "write_failed"]
    assert "object" in events[1][3].lower() or "objeto" in events[1][3].lower()


def test_a_healthy_write_is_not_retried(monkeypatch):
    attempts, done, events = _run_live(monkeypatch, 0, lambda s: s.queue_write("speed", b"\x05"))
    assert len(attempts) == 1
    assert done == [("write", b"\x05")]
    assert [e[0] for e in events] == ["connected"]
