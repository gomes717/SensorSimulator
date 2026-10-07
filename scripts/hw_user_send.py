"""Hardware check: Send to… puts a user on a real sensor, through the real ``BleSession`` and
``UserSender`` (not fakes), and the board then runs it.

On the last slot it: remembers what the slot holds; sends a **model user** (UVA/Padova, Breton, a
meal, an exercise bout, values that are not float32-exact) and reads it back with the real
``UserReader`` — it must come back as a *Matches* of the user that was sent, and the board's console
must show that model running; then sends a **CSV user** (a real 24 h Dexcom window with its Food
Log) and checks the board reports CSV mode under that name and that the console's glucose is the
recorded window, sample for sample; finally it restores what the slot held.

Run (board connected, COM10 free, nothing else connected to it)::

    uv run python scripts/hw_user_send.py

Each send resets the slot's clock; nothing else on the board is touched.
"""

from __future__ import annotations

import asyncio
import re
import sys
from dataclasses import replace
from datetime import datetime

from hw_common import ARTIFACTS, SerialCapture, refresh_gatt_cache, scan_sensors
from hw_user_read import SLOT, connect_session, read_slot, wait_until, write_slot
from PyQt6.QtCore import QCoreApplication
from PyQt6.QtTest import QTest

from gui.user_reader import UserReader
from gui.user_sender import UserSender
from models import user_board, user_csv, user_edit, uva_padova
from models.types import ExerciseEvent, FoodEvent, ModelId, SensorId, User
from models.user_store import new_user

results: list[tuple[str, bool]] = []
_TICK = re.compile(
    rf"model_tick\[{SLOT}\]: t_sim=([0-9.]+)min dt=([0-9.]+) model=(\d+) sensor=(\d+) ds=(\d) "
    r"glucose=([0-9.\-]+)"
)


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)


def model_user() -> User:
    user = new_user("HW Model")
    user_edit.set_model(user, ModelId.UVA_PADOVA)
    user.model_params = {**uva_padova.default_params(), "BW": 70.3}  # not float32-exact
    user_edit.set_weight(user, 70.3)
    user_edit.set_sensor(user, SensorId.BRETON)
    user.food_events = [FoodEvent(480, 60.1, 20)]
    user.exercise_events = [ExerciseEvent(1080, 45, 70.3)]
    return user


def csv_user() -> User:
    from hw_common import ROOT

    user = new_user("HW Csv")
    user.mode = "csv"
    path = ROOT / "dataset" / "Dexcom_001.csv"
    from models import dexcom_csv

    start = dexcom_csv.read_egv(path)[0][0]
    user.csv = user_csv.load_track(path, datetime.fromisoformat(start.isoformat()))
    return user


def send(sender: UserSender, session, user: User) -> tuple[bool, str]:
    done: list[tuple[bool, str]] = []
    sender.send(session, user, lambda ok, message: done.append((ok, message)))
    if not wait_until(lambda: bool(done), 150):
        return False, "no answer from the sender"
    return done[0]


def ticks_after(console: str, offset: int) -> list[tuple[float, int, int, int, float]]:
    """(t_sim, model, sensor, ds, glucose) for the slot after *offset*, from the last reset."""
    rows = [
        (float(m[1]), int(m[3]), int(m[4]), int(m[5]), float(m[6]))
        for m in _TICK.finditer(console[offset:])
    ]
    start = 0
    for i in range(1, len(rows)):
        if rows[i][0] < rows[i - 1][0]:
            start = i
    return rows[start:]


def run(session, serial: SerialCapture) -> None:
    reader, sender = UserReader(), UserSender()
    original = read_slot(reader, session)
    check("the slot can be read first", original is not None)
    if original is None:
        return
    print(f"   slot {SLOT} held: name={original.name!r} csv={original.is_csv}")
    try:
        # -- a model user --------------------------------------------------------------
        sent = model_user()
        offset = len(serial.text())
        ok, message = send(sender, session, sent)
        check("a model user is accepted by the board", ok, message)
        if ok:
            QTest.qWait(4000)
            reading = read_slot(reader, session)
            check("the sent user reads back", reading is not None)
            if reading is not None:
                outcome = user_board.classify([replace(sent, id="saved")], reading)
                check(
                    "it reads back as a Match of what was sent (float32-aware, real wire data)",
                    isinstance(outcome, user_board.Matches),
                    type(outcome).__name__
                    + (
                        f" {outcome.differences}" if isinstance(outcome, user_board.Differs) else ""
                    ),
                )
            rows = ticks_after(serial.text(), offset)
            ran = [r for r in rows if r[1] == int(ModelId.UVA_PADOVA) and r[3] == 0]
            check(
                "the board's console shows the model running (UVA/Padova, model source)",
                len(ran) >= 2,
                f"{len(ran)} of {len(rows)} ticks",
            )

        # -- a CSV user ----------------------------------------------------------------
        csv = csv_user()
        assert csv.csv is not None
        offset = len(serial.text())
        ok, message = send(sender, session, csv)
        check("a CSV user is accepted by the board", ok, message)
        if ok:
            QTest.qWait(8000)
            reading = read_slot(reader, session)
            check(
                "the board reports CSV mode under the user's name",
                reading is not None and reading.is_csv and reading.name == "HW Csv",
                f"name={getattr(reading, 'name', None)!r} csv={getattr(reading, 'is_csv', None)}",
            )
            rows = [r for r in ticks_after(serial.text(), offset) if r[3] == 1]
            samples, interval = csv.csv.samples, csv.csv.interval_s
            wrong = []
            for t, _model, _sensor, _ds, glucose in rows:
                options = {
                    int(tt * 60.0 / interval) % len(samples) for tt in (t, max(t - 0.02, 0.0))
                }
                if not any(abs(glucose - samples[r]) <= 0.01 for r in options):
                    wrong.append((t, glucose))
            check(
                "the console's glucose is the recorded window, sample for sample",
                len(rows) >= 3 and not wrong,
                f"{len(rows)} CSV ticks, {len(wrong)} off: {wrong[:3]}",
            )
    finally:
        write_slot(session, original)
        restored = read_slot(reader, session)
        check(
            "the slot is restored",
            restored is not None
            and restored.name == original.name
            and restored.is_csv == original.is_csv
            and restored.model_id == original.model_id,
        )


def main() -> int:
    named = asyncio.run(scan_sensors())
    print("found:", [name for name, _ in named])
    target = next((dev for name, dev in named if name.endswith(str(SLOT + 1))), None)
    if target is None:
        print(f"slot {SLOT}'s identity is not advertising")
        return 2
    asyncio.run(refresh_gatt_cache(target))  # BleSession discovers with Windows' cache allowed
    app = QCoreApplication.instance() or QCoreApplication([])
    with SerialCapture(ARTIFACTS / "user_send_serial.log") as serial:
        session = connect_session(target)
        if session is None:
            print("could not connect")
            return 2
        QTest.qWait(3000)
        try:
            run(session, serial)
        finally:
            session.stop()
            session.wait(5000)
    del app
    failed = [name for name, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
