"""Users-screen slice 4: reading one board slot into a BoardReading (gui/user_reader.py).

Driven with fake sessions, like test_board_mode_fixes.py: the test plays the board by
emitting the config_read answers itself.
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

pytest.importorskip("PyQt6.QtWidgets")

import struct

from PyQt6.QtWidgets import QApplication

from api import protocol
from gui.user_reader import READS, UserReader
from models.types import ExerciseEvent, FoodEvent, ModelId, SensorId


@pytest.fixture(scope="module", autouse=True)
def _app():
    return QApplication.instance() or QApplication([])


class _Signal:
    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)

    def emit(self, *args):
        for slot in list(self.slots):
            slot(*args)


class _Session:
    def __init__(self, slot_index: int | None = 1, live=True):
        self.slot_index = slot_index
        self.is_live = live
        self.config_read = _Signal()
        self.write_failed = _Signal()
        self.sent: list[tuple] = []  # ("write", key, payload) / ("read", key), in order

    def queue_write(self, key, payload):
        self.sent.append(("write", key, payload))

    def request_read(self, key):
        self.sent.append(("read", key))

    def answer(self, key, data):
        self.config_read.emit("AA:BB", key, data)


def _answers() -> dict[str, bytes]:
    """What a board running UVA/Padova + Breton for 'Ana' answers to the six reads."""
    params = dict.fromkeys(protocol.model_param_names(ModelId.UVA_PADOVA), 0.0)
    params["BW"] = 82.5
    sensor = dict.fromkeys(protocol.sensor_param_names(SensorId.BRETON), 0.0)
    food = struct.pack("<B", 1) + struct.pack("<HHf", 480, 20, 60.0)
    exercise = struct.pack("<B", 1) + struct.pack("<HHf", 1080, 45, 70.0)
    return {
        "user_name": b"Ana",
        "data_source": protocol.encode_data_source(False),
        "person": protocol.encode_person_config(ModelId.UVA_PADOVA, params),
        "sensor": protocol.encode_sensor_config(SensorId.BRETON, sensor),
        "food_list": food,
        "exercise_list": exercise,
    }


def _read(session, reader=None):
    reader = reader or UserReader(timeout_ms=60_000)
    done: list[tuple] = []
    started = reader.read(session, lambda reading, error: done.append((reading, error)))
    return reader, started, done


def test_a_read_selects_the_slot_then_asks_for_all_six_facts_in_order():
    session = _Session(slot_index=2)
    _read(session)
    assert session.sent[0] == ("write", "sensor_select", protocol.encode_sensor_select(2))
    assert list(session.sent[1:]) == [("read", key) for key in READS]


def test_the_answers_become_a_reading_once_all_six_arrive():
    session = _Session()
    _, started, done = _read(session)
    assert started
    for key, data in _answers().items():
        assert done == []  # nothing is reported before the last answer
        session.answer(key, data)
    [(reading, error)] = done
    assert error == ""
    assert reading is not None
    assert reading.name == "Ana"
    assert reading.is_csv is False
    assert (reading.model_id, reading.sensor_id) == (ModelId.UVA_PADOVA, SensorId.BRETON)
    assert reading.model_params["BW"] == 82.5
    assert reading.food_events == [FoodEvent(480, 60.0, 20)]
    assert reading.exercise_events == [ExerciseEvent(1080, 45, 70.0)]


def test_a_board_in_csv_mode_reads_as_csv():
    session = _Session()
    _, _, done = _read(session)
    answers = _answers() | {"data_source": protocol.encode_data_source(True)}
    for key, data in answers.items():
        session.answer(key, data)
    assert done[0][0] is not None and done[0][0].is_csv is True


def test_a_board_with_no_name_reads_as_an_empty_name():
    session = _Session()
    _, _, done = _read(session)
    for key, data in (_answers() | {"user_name": b""}).items():
        session.answer(key, data)
    assert done[0][0] is not None and done[0][0].name == ""


def test_a_single_sensor_board_is_read_as_slot_zero():
    session = _Session(slot_index=None)
    _read(session)
    assert session.sent[0] == ("write", "sensor_select", protocol.encode_sensor_select(0))


def test_a_dead_link_fails_at_once_without_touching_the_board():
    session = _Session(live=False)
    reader, started, done = _read(session)
    assert started
    assert session.sent == []
    assert done[0][0] is None
    assert "not connected" in done[0][1]
    assert not reader.busy


def test_a_timeout_reports_what_never_answered():
    session = _Session()
    reader, _, done = _read(session)
    for key in ("user_name", "data_source", "person"):
        session.answer(key, _answers()[key])
    reader._timed_out(reader._generation)
    [(reading, error)] = done
    assert reading is None
    assert "sensor" in error and "food_list" in error and "exercise_list" in error
    assert "user_name" not in error
    assert not reader.busy


def test_a_refused_read_fails_the_whole_read():
    session = _Session()
    reader, _, done = _read(session)
    session.write_failed.emit("AA:BB", "sensor", "GATT error")
    assert done[0][0] is None
    assert "sensor" in done[0][1] and "GATT error" in done[0][1]
    assert not reader.busy


def test_a_characteristic_the_board_does_not_expose_explains_the_stale_cache():
    session = _Session()
    reader, _, done = _read(session)
    session.write_failed.emit("AA:BB", "user_name", "device does not expose this characteristic")
    assert done[0][0] is None
    assert "user_name" in done[0][1] and "reconnect" in done[0][1]
    assert not reader.busy


def test_an_unreadable_person_config_fails_instead_of_guessing():
    session = _Session()
    _, _, done = _read(session)
    for key, data in (_answers() | {"person": b"\x00"}).items():
        session.answer(key, data)
    assert done[0][0] is None
    assert "person" in done[0][1]


def test_only_one_read_at_a_time():
    session = _Session()
    reader, started, _ = _read(session)
    assert started and reader.busy
    other = _Session(slot_index=0)
    done: list = []
    assert reader.read(other, lambda *a: done.append(a)) is False
    assert other.sent == [] and done == []  # the second read never started


def test_it_can_read_again_once_the_first_finished():
    session = _Session()
    reader, _, _ = _read(session)
    for key, data in _answers().items():
        session.answer(key, data)
    assert not reader.busy
    done: list = []
    assert reader.read(session, lambda *a: done.append(a)) is True


def test_answers_that_arrive_when_no_read_is_pending_are_ignored():
    session = _Session()
    reader, _, done = _read(session)
    for key, data in _answers().items():
        session.answer(key, data)
    assert len(done) == 1
    session.answer("person", b"junk")  # a late or foreign answer (e.g. BoardMode's own read)
    assert len(done) == 1 and not reader.busy


def test_answers_from_another_session_are_ignored():
    mine, other = _Session(slot_index=1), _Session(slot_index=2)
    reader, _, done = _read(mine)
    reader.read(other, lambda *_a: None)  # refused (busy) — but it must not get wired either
    for key, data in _answers().items():
        other.answer(key, data)
    assert done == []


def test_the_name_is_written_after_selecting_its_slot():
    session = _Session()
    UserReader().write_name(session, 2, "Ana#2")
    assert session.sent == [
        ("write", "sensor_select", protocol.encode_sensor_select(2)),
        ("write", "user_name", b"Ana#2"),
    ]


def test_a_name_the_board_cannot_hold_is_refused_before_anything_is_sent():
    session = _Session()
    with pytest.raises(ValueError):
        UserReader().write_name(session, 2, "n" * 31)
    assert session.sent == []
