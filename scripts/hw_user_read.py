"""Hardware check: the Users screen's read-from-board path, through the real ``BleSession`` and
``UserReader`` (not fakes).

On the last slot it: remembers what the slot holds; writes a known user ("Ana": UVA/Padova,
Breton noise, a meal, an exercise bout, with float values that are not float32-exact); reads it
back and checks the three outcomes against saved users built in full precision —

    same content            -> Matches   (the float32-aware comparison works on real wire data)
    a changed parameter     -> Differs ["model parameters"]
    a different name        -> Unknown   (name-only matching)

— then renames the board's user ("Ana#2", what *Create* does) and reads the name back, and
finally restores everything the slot held.

Run (board connected, COM10 free, nothing else connected to it)::

    uv run python scripts/hw_user_read.py

The slot's clock-affecting writes reset the sim; nothing else on the board is touched.
"""

from __future__ import annotations

import asyncio
import sys
import time
from dataclasses import replace

from hw_common import ARTIFACTS, SerialCapture, checked_slot, refresh_gatt_cache, scan_sensors
from PyQt6.QtCore import QCoreApplication
from PyQt6.QtTest import QTest

from api import protocol
from gui.user_reader import UserReader
from models import board_layout, user_board, uva_padova
from models import sensors as sensor_defaults
from models.types import ExerciseEvent, FoodEvent, ModelId, SensorId
from services.ble_session import BleSession

SLOT = checked_slot(board_layout.MAX_SLOTS - 1)  # the last slot: least likely to hold real work
results: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)


def wait_until(condition, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        QTest.qWait(100)
    return condition()


def read_slot(reader: UserReader, session) -> user_board.BoardReading | None:
    got: list = []
    reader.read(session, lambda reading, error: got.append((reading, error)))
    if not wait_until(lambda: bool(got), 15):
        return None
    reading, error = got[0]
    if reading is None:
        print("   read failed:", error)
    return reading


def write_slot(session, reading: user_board.BoardReading) -> None:
    """Put *reading* on the slot: the same writes a Send makes, then the name."""
    session.queue_write("sensor_select", protocol.encode_sensor_select(SLOT))
    session.queue_write(
        "person", protocol.encode_person_config(reading.model_id, reading.model_params)
    )
    session.queue_write(
        "sensor", protocol.encode_sensor_config(reading.sensor_id, reading.sensor_params)
    )
    session.queue_write("data_source", protocol.encode_data_source(reading.is_csv))
    session.queue_write("food", protocol.encode_clear_food())
    for event in reading.food_events:
        session.queue_write("food", protocol.encode_food_event(event))
    session.queue_write("exercise", protocol.encode_clear_exercise())
    for event in reading.exercise_events:
        session.queue_write("exercise", protocol.encode_exercise_event(event))
    session.queue_write("user_name", protocol.encode_user_name(reading.name))
    QTest.qWait(8000)  # each write is applied and saved to flash by the board


def known_user() -> user_board.BoardReading:
    params = uva_padova.default_params()
    params["BW"] = 82.3  # not float32-exact: the comparison must cope
    return user_board.BoardReading(
        name="Ana",
        is_csv=False,
        model_id=ModelId.UVA_PADOVA,
        model_params=params,
        sensor_id=SensorId.BRETON,
        sensor_params=sensor_defaults.breton_default_params(),
        food_events=[FoodEvent(480, 60.1, 20)],
        exercise_events=[ExerciseEvent(1080, 45, 70.3)],
    )


def run(session) -> None:
    reader = UserReader()
    original = read_slot(reader, session)
    check("the slot can be read", original is not None)
    if original is None:
        return
    print(
        f"   slot {SLOT} held: name={original.name!r} csv={original.is_csv} "
        f"model={original.model_id.name} food={len(original.food_events)}"
    )
    try:
        sent = known_user()
        write_slot(session, sent)
        reading = read_slot(reader, session)
        check("the written user reads back", reading is not None)
        if reading is None:
            return
        check("the name reads back", reading.name == "Ana", repr(reading.name))
        check(
            "model, sensor and schedules read back",
            (reading.model_id, reading.sensor_id) == (sent.model_id, sent.sensor_id)
            and len(reading.food_events) == 1
            and len(reading.exercise_events) == 1,
        )

        saved = replace(user_board.user_from_reading(sent), id="saved")  # full precision
        outcome = user_board.classify([saved], reading)
        check(
            "same content is a Match (float32-aware, on real wire data)",
            isinstance(outcome, user_board.Matches),
            type(outcome).__name__,
        )

        changed = replace(saved, model_params={**saved.model_params, "BW": 90.0})
        outcome = user_board.classify([changed], reading)
        check(
            "a changed parameter Differs in 'model parameters'",
            isinstance(outcome, user_board.Differs) and outcome.differences == ["model parameters"],
            str(getattr(outcome, "differences", type(outcome).__name__)),
        )

        other = replace(saved, name="Someone else")
        check(
            "another name is Unknown (name-only matching)",
            isinstance(user_board.classify([other], reading), user_board.Unknown),
        )

        reader.write_name(session, SLOT, "Ana#2")  # what Create does
        QTest.qWait(3000)
        renamed = read_slot(reader, session)
        check(
            "the board's name follows a rename",
            renamed is not None and renamed.name == "Ana#2",
            repr(renamed.name if renamed else None),
        )
    finally:
        write_slot(session, original)
        restored = read_slot(reader, session)
        check(
            "the slot is restored",
            restored is not None
            and restored.name == original.name
            and restored.model_id == original.model_id
            and len(restored.food_events) == len(original.food_events),
        )


def connect_session(target, attempts: int = 3):
    """A connected BleSession for *target*, or None; Windows drops the odd first attempt."""
    for attempt in range(attempts):
        state = {"done": False, "ok": False, "err": ""}
        session = BleSession(target.address, f"Nordic Glucose Sensor {SLOT + 1}", None)
        session.connected.connect(lambda *_, s=state: s.update(done=True, ok=True))
        session.connect_failed.connect(lambda _a, e, s=state: s.update(done=True, err=e))
        session.start()
        if wait_until(lambda s=state: s["done"], 40) and state["ok"]:
            return session
        print(f"   connect attempt {attempt + 1} failed: {state['err'] or 'timeout'}", flush=True)
        session.stop()
        session.wait(5000)
        QTest.qWait(4000)
    return None


def main() -> int:
    named = asyncio.run(scan_sensors())  # before Qt: bleak's scanner will not start under it
    print("found:", [name for name, _ in named])
    target = next((dev for name, dev in named if name.endswith(str(SLOT + 1))), None)
    if target is None:
        print(f"slot {SLOT}'s identity is not advertising")
        return 2
    # BleSession discovers with Windows' cache allowed; refresh it for this identity first, so
    # a characteristic added by a firmware update is visible (see refresh_gatt_cache).
    asyncio.run(refresh_gatt_cache(target))
    app = QCoreApplication.instance() or QCoreApplication([])
    with SerialCapture(ARTIFACTS / "user_read_serial.log"):
        session = connect_session(target)
        if session is None:
            return 2
        QTest.qWait(3000)  # let the config characteristics settle
        try:
            run(session)
        finally:
            session.stop()
            session.wait(5000)
    del app
    failed = [name for name, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
