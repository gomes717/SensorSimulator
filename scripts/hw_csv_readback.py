"""Hardware check: CSV readback — a board that replays a recording sends it back, so reading it
gives a user that holds the recording (the last piece of the users plan).

On the last slot, through the real ``BleSession``, ``UserSender`` and ``UserReader``: it sends a CSV
user (a real 24 h Dexcom window with its Food Log), reads the slot back and checks the recording
that came back is the one that was sent — every sample, the interval, the meals (carbs through
float32) and the start time — and that the user reads back as a *Matches* of the sent one; then
sends a model user and checks the read has no recording (and downloads none); finally it restores
what the slot held.

Run (board connected, COM10 free, nothing else connected to it)::

    uv run python scripts/hw_csv_readback.py
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import replace
from datetime import datetime

from hw_common import ROOT, refresh_gatt_cache, scan_sensors
from hw_user_read import SLOT, connect_session, read_slot, wait_until, write_slot
from PyQt6.QtCore import QCoreApplication
from PyQt6.QtTest import QTest

from gui.user_reader import UserReader
from gui.user_sender import UserSender
from models import dexcom_csv, user_board, user_csv, user_match
from models.types import User
from models.user_store import new_user

results: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)


def csv_user() -> User:
    user = new_user("HW Readback")
    user.mode = "csv"
    path = ROOT / "dataset" / "Dexcom_001.csv"
    start = dexcom_csv.read_egv(path)[0][0]
    user.csv = user_csv.load_track(path, datetime.fromisoformat(start.isoformat()))
    return user


def send(sender: UserSender, session, user: User) -> tuple[bool, str]:
    done: list[tuple[bool, str]] = []
    sender.send(session, user, lambda ok, message: done.append((ok, message)))
    if not wait_until(lambda: bool(done), 150):
        return False, "no answer from the sender"
    return done[0]


def run(session) -> None:
    reader, sender = UserReader(), UserSender()
    original = read_slot(reader, session)
    check("the slot can be read first", original is not None)
    if original is None:
        return
    try:
        sent = csv_user()
        assert sent.csv is not None
        ok, message = send(sender, session, sent)
        check("a CSV user is sent", ok, message)
        if not ok:
            return
        QTest.qWait(4000)
        reading = read_slot(reader, session)
        check("the slot reads back as CSV", reading is not None and reading.is_csv)
        got = reading.csv if reading is not None else None
        check("the board sent its recording back", got is not None)
        if got is not None:
            check(
                "every glucose sample came back",
                got.samples == sent.csv.samples,
                f"{len(got.samples)} of {len(sent.csv.samples)}",
            )
            check("the interval came back", got.interval_s == sent.csv.interval_s)
            check(
                "the meals came back (carbs through float32)",
                [(o, round(c, 3)) for o, c in got.foodlog]
                == [(o, round(user_match._f32(c), 3)) for o, c in sent.csv.foodlog],
                f"{len(got.foodlog)} meals",
            )
            check(
                "the start time came back",
                got.start_iso is not None
                and sent.csv.start_iso is not None
                and datetime.fromisoformat(got.start_iso).replace(microsecond=0)
                == datetime.fromisoformat(sent.csv.start_iso).replace(microsecond=0),
                f"{got.start_iso} vs {sent.csv.start_iso}",
            )
        if reading is not None:
            outcome = user_board.classify([replace(sent, id="saved")], reading)
            check(
                "the read user is a Match of the sent one, recording included",
                isinstance(outcome, user_board.Matches),
                type(outcome).__name__
                + (f" {outcome.differences}" if isinstance(outcome, user_board.Differs) else ""),
            )
            other = replace(
                sent, id="saved", csv=replace(sent.csv, samples=[*sent.csv.samples[:-1], 1])
            )
            changed = user_board.classify([other], reading)
            check(
                "a saved user whose window differs by one sample is a Difference",
                isinstance(changed, user_board.Differs) and changed.differences == ["CSV window"],
            )

        # -- a model user: no recording, and none is downloaded ---------------------------
        model = new_user("HW Model Rb")
        ok, message = send(sender, session, model)
        check("a model user is sent", ok, message)
        if ok:
            QTest.qWait(4000)
            reading = read_slot(reader, session)
            check(
                "a model board reads back with no recording",
                reading is not None and not reading.is_csv and reading.csv is None,
            )
    finally:
        write_slot(session, original)
        restored = read_slot(reader, session)
        check(
            "the slot is restored",
            restored is not None
            and restored.name == original.name
            and restored.is_csv == original.is_csv,
        )


def main() -> int:
    named = asyncio.run(scan_sensors())
    print("found:", [name for name, _ in named])
    target = next((dev for name, dev in named if name.endswith(str(SLOT + 1))), None)
    if target is None:
        print(f"slot {SLOT}'s identity is not advertising")
        return 2
    asyncio.run(refresh_gatt_cache(target))  # the new csv_read characteristic must be visible
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
