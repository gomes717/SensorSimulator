"""Hardware check: CSV playback loops when its window ends.

Uploads three tracks to the LAST slot, plays each past its end at high speed, and checks
every ``model_tick`` console line against the value a looping playback must give —
``row = floor(t_sim * 60 / interval_s) mod rows`` — plus, for a window with a Food Log, that
the meals fire again in the second window:

    A  a synthetic 1 h window (12 samples) at x60
    B  a synthetic 24 h window (288 samples) at x1000
    C  a real 24 h window from ``dataset/Dexcom_001.csv`` with its Food Log, at x1000

Run (board connected, COM10 free, nothing else connected to the board; ~13 minutes)::

    uv run python scripts/hw_csv_loop.py

Each Speed write resets the board's clock to 0 (see the speed-write note in PROTOCOL_SPEC.md),
so the case's ticks are the ones after the last reset. Afterwards the slot is put back to the
model source with no CSV tracks, and the speed back to ``RESTORE_SPEED``.

Last run: 2026-10-06 (see ``docs/FIRMWARE.md`` §CSV playback).
"""

from __future__ import annotations

import asyncio
import re
import sys

from hw_common import (
    ARTIFACTS,
    CSV_CONTROL,
    CSV_DATA,
    DATA_SOURCE,
    ROOT,
    SPEED,
    SerialCapture,
    checked_slot,
    connect,
    pick_config_device,
    retry_ble,
    scan_sensors,
    select_slot,
    write,
)

from api import protocol
from models import board_layout, dexcom_csv, food_log_csv

SLOT = checked_slot(board_layout.MAX_SLOTS - 1)  # the last slot: least likely to hold real work
RESTORE_SPEED = 60.0  # what the board ran at before these checks; written back at the end
INTERVAL_S = 300

_TICK = re.compile(
    rf"model_tick\[{SLOT}\]: t_sim=([0-9.]+)min dt=[0-9.]+.*? ds=1 "
    r"glucose=([0-9.\-]+).*? carbs=([0-9.\-]+)"
)


async def upload_one(client, notifications: asyncio.Queue, upload: dict) -> None:
    blob = upload["blob"]
    begin = protocol.encode_csv_begin(
        upload["track"],
        upload["row_count"],
        upload["base_epoch_s"],
        upload["interval_s"],
        len(blob),
        protocol.csv_crc32(blob),
    )
    while not notifications.empty():
        notifications.get_nowait()
    await write(client, CSV_CONTROL, begin)
    status = protocol.decode_csv_control_notify(await asyncio.wait_for(notifications.get(), 15))
    print("   begin ->", status)
    for chunk in protocol.iter_csv_data_chunks(blob):
        await write(client, CSV_DATA, chunk)
        await asyncio.sleep(0.05)
    await write(client, CSV_CONTROL, protocol.encode_csv_commit(upload["track"]))
    status = protocol.decode_csv_control_notify(await asyncio.wait_for(notifications.get(), 15))
    print("   commit ->", status)


def ticks_after(console: str, offset: int) -> list[tuple[float, float, float]]:
    """(t_sim_min, glucose, carbs) of the slot's CSV-source lines after *offset*, from the
    last clock reset (the sim clock only ever goes backwards when the board resets it)."""
    rows = [(float(m[1]), float(m[2]), float(m[3])) for m in _TICK.finditer(console[offset:])]
    start = 0
    for i in range(1, len(rows)):
        if rows[i][0] < rows[i - 1][0]:
            start = i
    return rows[start:]


def verify(name: str, samples: list[int], got: list, *, with_meals: bool) -> bool:
    if not got:
        print(f"FAIL {name}: no CSV-source ticks seen for slot {SLOT}")
        return False
    span_min = len(samples) * INTERVAL_S / 60.0
    windows = got[-1][0] / span_min
    wrong = []
    for t, glucose, _carbs in got:
        # t_sim is printed with 2 decimals: accept either side of a row boundary
        rows = {int(tt * 60.0 / INTERVAL_S) % len(samples) for tt in (t, max(t - 0.02, 0.0))}
        if not any(abs(glucose - samples[r]) <= 0.01 for r in rows):
            wrong.append((t, glucose, sorted(samples[r] for r in rows)))
    print(
        f"   {name}: {len(got)} ticks, t_sim to {got[-1][0]:.1f} min, "
        f"span {span_min:.0f} min => {windows:.2f} windows played"
    )
    if wrong:
        print(f"   mismatches (t_sim, got, expected): {wrong[:5]} ... {len(wrong)} total")
    meals_again = True
    if with_meals:
        per_window = [
            sum(1 for t, _g, c in got if k * span_min <= t < (k + 1) * span_min and c > 0)
            for k in range(int(windows) + 1)
        ]
        print(f"   ticks with carbs>0 per window: {per_window}")
        meals_again = len(per_window) >= 2 and per_window[0] > 0 and per_window[1] > 0
    ok = windows > 1.0 and not wrong and meals_again
    print(("PASS " if ok else "FAIL ") + name)
    return ok


async def run_case(client, notifications, serial, name, samples, speed, seconds, foodlog=()):
    print(f"-- {name}: {len(samples)} samples @ {INTERVAL_S}s, speed x{speed:g}, {seconds}s")
    await select_slot(client, SLOT)
    uploads = protocol.build_csv_uploads(samples, INTERVAL_S, list(foodlog), "2020-01-01T00:00:00")
    for upload in uploads:
        await upload_one(client, notifications, upload)
    await write(client, DATA_SOURCE, protocol.encode_data_source(True))
    await asyncio.sleep(1.0)
    await write(client, SPEED, protocol.encode_speed(speed))  # resets the clock to 0
    offset = len(serial.text())
    await asyncio.sleep(seconds)
    return verify(name, samples, ticks_after(serial.text(), offset), with_meals=bool(foodlog))


def real_window() -> tuple[list[int], list[tuple[int, float]]]:
    """The first 24 h of ``dataset/Dexcom_001.csv`` and its matching Food Log."""
    path = ROOT / "dataset" / "Dexcom_001.csv"
    rows = dexcom_csv.read_egv(path)
    start = rows[0][0]
    samples = dexcom_csv.resample(rows, start, INTERVAL_S)
    foodlog = food_log_csv.slice_window(
        food_log_csv.read_food_log(path.with_name("Food_Log_001.csv")), start
    )
    return samples, foodlog


async def restore(client) -> None:
    await select_slot(client, SLOT)
    await write(client, DATA_SOURCE, protocol.encode_data_source(False))
    await write(client, CSV_CONTROL, protocol.encode_csv_clear(protocol.CSV_TRACK_GLUCOSE))
    await write(client, CSV_CONTROL, protocol.encode_csv_clear(protocol.CSV_TRACK_FOODLOG))
    await asyncio.sleep(1.0)
    await write(client, SPEED, protocol.encode_speed(RESTORE_SPEED))
    print(f"restored slot {SLOT} (model source, no CSV) and speed x{RESTORE_SPEED:g}")


async def main() -> int:
    notifications: asyncio.Queue = asyncio.Queue()
    with SerialCapture(ARTIFACTS / "csv_loop_serial.log") as serial:
        await asyncio.sleep(3)
        named = await scan_sensors()
        print("found:", [name for name, _ in named])
        if not named:
            return 2
        client = await connect(
            pick_config_device(named),
            CSV_CONTROL,
            lambda _char, data: notifications.put_nowait(bytes(data)),
        )
        if client is None:
            print("could not hold a connection")
            return 2
        try:
            speed = protocol.decode_speed(
                bytes(await retry_ble(lambda: client.read_gatt_char(SPEED)))
            )
            print(f"board speed now x{speed}")
            hour = [100 + 10 * i for i in range(12)]
            day = [100 + (i % 100) for i in range(288)]
            real, foodlog = real_window()
            print(f"real window: {len(real)} samples, {len(foodlog)} meals")
            ok = [
                await run_case(client, notifications, serial, "A 1 h window", hour, 60.0, 150),
                await run_case(client, notifications, serial, "B 24 h window", day, 1000.0, 210),
                await run_case(
                    client,
                    notifications,
                    serial,
                    "C real Dexcom_001 + food log",
                    real,
                    1000.0,
                    230,
                    foodlog,
                ),
            ]
        finally:
            try:
                await restore(client)
            finally:
                await client.disconnect()
    return 0 if all(ok) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
