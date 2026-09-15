"""Issue 18: BoardLink is the one write surface over the connected board sessions."""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from api import protocol
from gui.board_link import BoardLink


class _FakeSession:
    def __init__(self, slot_index=None, is_live=True):
        self.slot_index = slot_index
        self.is_live = is_live  # a dropped link silently discards queued writes
        self.writes: list[tuple[str, bytes]] = []

    def queue_write(self, key, payload):
        self.writes.append((key, payload))


def _link(*sessions):
    d = {f"a{i}": s for i, s in enumerate(sessions)}
    return BoardLink(lambda: d), list(d.values())


def test_broadcast_writes_to_every_session():
    link, [s0, s1] = _link(_FakeSession(), _FakeSession())
    link.broadcast("speed", b"\x01\x02")
    assert s0.writes == s1.writes == [("speed", b"\x01\x02")]


def test_connected_and_multi_slot():
    empty = BoardLink(lambda: {})
    assert not empty.connected() and not empty.multi_slot()
    link, _ = _link(_FakeSession(slot_index=0), _FakeSession(slot_index=1))
    assert link.connected() and link.multi_slot()
    link2, _ = _link(_FakeSession(), _FakeSession())
    assert link2.connected() and not link2.multi_slot()


def test_send_to_slot_prefixes_the_cursor():
    numbered = _FakeSession(slot_index=2)
    link, _ = _link(_FakeSession(), numbered)
    link.send_to_slot(3, "pisa_instant", b"\xaa")
    assert numbered.writes == [
        ("sensor_select", protocol.encode_sensor_select(3)),
        ("pisa_instant", b"\xaa"),
    ]


def test_send_instant_none_slot_broadcasts_on_a_single_sensor_board():
    link, [s0, s1] = _link(_FakeSession(), _FakeSession())  # slot_index None -> single
    link.send_instant("food_instant", b"\x01", None)
    assert s0.writes == s1.writes == [("food_instant", b"\x01")]


def test_send_instant_all_sensors_walks_every_slot_on_a_multi_sensor_board():
    """Firmware applies an instant event to the cursor's slot only, so 'all
    sensors' must be cursor+event once per slot, not one broadcast."""
    from models import board_layout

    numbered = _FakeSession(slot_index=1)
    link, _ = _link(_FakeSession(), numbered)
    link.send_instant("food_instant", b"\x07", None)
    expected = []
    for i in range(board_layout.MAX_SLOTS):
        expected += [("sensor_select", protocol.encode_sensor_select(i)), ("food_instant", b"\x07")]
    assert numbered.writes == expected


def test_no_sessions_is_a_safe_noop():
    empty = BoardLink(lambda: None)  # provider may return None
    empty.broadcast("x", b"")
    empty.send_instant("x", b"", 1)
    empty.restart_all()


def test_a_dead_session_is_never_written_to():
    """A session whose link dropped still accepts queue_write(), but nothing
    drains it — so the board never sees the event and the user sees no error."""
    dead, live = _FakeSession(is_live=False), _FakeSession()
    link, _ = _link(dead, live)

    link.broadcast("speed", b"")

    assert dead.writes == []
    assert live.writes == [("speed", b"")]


def test_send_instant_reports_nothing_sent_when_no_link_is_live():
    link, _ = _link(_FakeSession(is_live=False))
    assert link.send_instant("food_instant", b"", None) == 0
