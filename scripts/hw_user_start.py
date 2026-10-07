"""Hardware check: Start's push puts the app's user on a real sensor, name included (ADR 0005/0006).

Drives the real ``StartPush`` over the real ``BleSession`` on the board's last slot, with a minimal
stand-in for the app state (so no user data on this machine is touched): the slot is planned to run
a known model user, Start's push is run, and the slot is then read back with the real
``UserReader`` — it must come back as a *Matches* of that user, name and all. The slot is restored
afterwards, and the board's speed is left as it was.

Run (board connected, COM10 free, nothing else connected to it)::

    uv run python scripts/hw_user_start.py
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import replace

from hw_common import refresh_gatt_cache, scan_sensors
from hw_user_read import SLOT, connect_session, read_slot, wait_until, write_slot
from PyQt6.QtCore import QCoreApplication
from PyQt6.QtTest import QTest

from gui.start_push import StartPush
from gui.user_reader import UserReader
from models import user_board, user_edit, user_sim, uva_padova
from models.types import ExerciseEvent, FoodEvent, ModelId, SensorId
from models.user_store import new_user

RESTORE_SPEED = 60.0  # the board's speed before these checks; Start's push writes this value
results: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)


class _State:
    """The slice of AppState StartPush reads: what to push, to which slots, at what speed."""

    model_only = False
    speed_mult = RESTORE_SPEED

    def __init__(self, user) -> None:
        self._plan = {SLOT: (user_sim.person_profile_of(user), user_sim.sensor_profile_of(user))}

    def board_plan(self, _live_slots):
        return self._plan


class _Board:
    """The slice of BoardLink StartPush uses, over one real session."""

    def __init__(self, session) -> None:
        self._session = session

    def live_sessions(self):
        return [self._session]

    def live_slots(self):
        return {SLOT}

    def broadcast(self, char_key, payload):
        self._session.queue_write(char_key, payload)
        return 1


def run(session) -> None:
    reader = UserReader()
    original = read_slot(reader, session)
    check("the slot can be read first", original is not None)
    if original is None:
        return
    try:
        user = new_user("HW Start")
        user_edit.set_model(user, ModelId.UVA_PADOVA)
        user.model_params = {**uva_padova.default_params(), "BW": 66.6}  # not float32-exact
        user_edit.set_weight(user, 66.6)
        user_edit.set_sensor(user, SensorId.BRETON)
        user.food_events = [FoodEvent(540, 50.1, 25)]
        user.exercise_events = [ExerciseEvent(1020, 40, 60.3)]

        outcome: list[bool] = []
        status: list[str] = []
        push = StartPush(_State(user), _Board(session), status.append)  # type: ignore[arg-type]
        push.run(outcome.append)
        done = wait_until(lambda: bool(outcome), 150)
        check("Start's push is accepted by the board", done and outcome[0], "; ".join(status[-2:]))
        if not (done and outcome[0]):
            return
        QTest.qWait(4000)
        reading = read_slot(reader, session)
        check("the slot reads back", reading is not None)
        if reading is None:
            return
        check(
            "Start told the board the user's name", reading.name == "HW Start", repr(reading.name)
        )
        result = user_board.classify([replace(user, id="saved")], reading)
        check(
            "what Start pushed is a Match of the user (float32-aware, real wire data)",
            isinstance(result, user_board.Matches),
            type(result).__name__
            + (f" {result.differences}" if isinstance(result, user_board.Differs) else ""),
        )
    finally:
        write_slot(session, original)
        restored = read_slot(reader, session)
        check(
            "the slot is restored",
            restored is not None
            and restored.name == original.name
            and restored.model_id == original.model_id,
        )


def main() -> int:
    named = asyncio.run(scan_sensors())
    print("found:", [name for name, _ in named])
    target = next((dev for name, dev in named if name.endswith(str(SLOT + 1))), None)
    if target is None:
        print(f"slot {SLOT}'s identity is not advertising")
        return 2
    asyncio.run(refresh_gatt_cache(target))
    app = QCoreApplication.instance() or QCoreApplication([])
    session = connect_session(target)
    if session is None:
        print("could not connect")
        return 2
    QTest.qWait(3000)
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
