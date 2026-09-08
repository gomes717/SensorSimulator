#!/usr/bin/env python3
"""Validate the board's CGM stream against what it *should* be sending.

We can compute the expected series because we have both halves of the pipeline
in Python:

  * CSV data source  -> exact: the firmware emits row
    ``floor(t_min*60 / interval_s) % row_count`` verbatim (no noise).
  * model data source -> coarse: the pure-Python model port
    (src/models/<model>.py) stepped with the same dt rule; compared as a moving
    average because the on-device sensor noise is stochastic.

See docs/BLE_PAYLOAD_VALIDATION.md for the wire format and tolerances.

Modes
-----
  offline : rebuild the expected series from a profile / CSV window and (if a
            capture file is given) diff it. No board needed.
  live    : connect to the board, collect CGM Measurement notifications for
            N minutes, then diff.

Examples
--------
  # offline exact check of a CSV window vs a capture file
  python scripts/validate_ble_stream.py offline --csv-path dataset/Dexcom_001.csv \\
      --start 2020-02-13T17:23:32 --food-log dataset/Food_Log_001.csv \\
      --received capture.csv

  # live, board is a CSV-backed patient from data/profiles.json
  python scripts/validate_ble_stream.py live --address AA:BB:CC:DD:EE:FF --profile "CSV Patient" --minutes 10

Capture file format (one reading per line): ``time_offset_min,glucose_mg_dl``
"""

from __future__ import annotations

import argparse
import statistics
import sys
from datetime import datetime
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(_SRC))

from models import dexcom_csv, engine, profile_store  # noqa: E402
from models.types import PersonProfile  # noqa: E402

CSV_TOLERANCE_MG_DL = 1.0
MODEL_WINDOW = 12  # samples for the moving-average model comparison


# ----------------------------------------------------------------------
# Expected-series builders
# ----------------------------------------------------------------------


def csv_expected(samples: list[int], interval_s: int, t_offset_min: float) -> int:
    """The mg/dL the firmware's CSV branch emits at session time *t_offset_min*."""
    row = int(t_offset_min * 60.0 / interval_s) % len(samples)
    return samples[row]


def load_profile(name: str) -> PersonProfile:
    persons, _ = profile_store.load()
    for p in persons:
        if p.name == name:
            return p
    raise SystemExit(
        f"profile {name!r} not found in data/profiles.json "
        f"(have: {', '.join(p.name for p in persons) or 'none'})"
    )


def csv_window_from_args(args) -> tuple[list[int], int]:
    if args.profile:
        prof = load_profile(args.profile)
        samples, interval_s, _ = engine.load_csv_window(prof)
        if not samples:
            raise SystemExit(f"profile {args.profile!r} has no usable CSV window assigned")
        return samples, interval_s
    if not (args.csv_path and args.start):
        raise SystemExit("need --profile, or both --csv-path and --start")
    rows = dexcom_csv.read_egv(args.csv_path)
    start = datetime.fromisoformat(args.start)
    samples = dexcom_csv.resample(rows, start, dexcom_csv.DEFAULT_INTERVAL_S)
    return samples, dexcom_csv.DEFAULT_INTERVAL_S


# ----------------------------------------------------------------------
# Diffing
# ----------------------------------------------------------------------


def diff_csv(received: list[tuple[float, float]], samples: list[int], interval_s: int) -> bool:
    print(f"\n  {'t_off(min)':>11} {'received':>9} {'expected':>9} {'diff':>7}")
    worst = 0.0
    fails = 0
    for t_off, got in received:
        exp = csv_expected(samples, interval_s, t_off)
        d = got - exp
        worst = max(worst, abs(d))
        flag = "" if abs(d) <= CSV_TOLERANCE_MG_DL else "  <-- FAIL"
        if flag:
            fails += 1
        print(f"  {t_off:>11.1f} {got:>9.1f} {exp:>9d} {d:>7.1f}{flag}")
    ok = fails == 0
    print(
        f"\n  samples={len(received)}  worst |diff|={worst:.1f} mg/dL  "
        f"tolerance={CSV_TOLERANCE_MG_DL}  -> {'PASS' if ok else f'FAIL ({fails} out of range)'}"
    )
    return ok


def summarize_model(received: list[tuple[float, float]]) -> None:
    vals = [g for _, g in received]
    if not vals:
        print("  no samples")
        return
    print(
        f"  n={len(vals)}  mean={statistics.mean(vals):.1f}  "
        f"sd={statistics.pstdev(vals):.1f}  min={min(vals):.0f}  max={max(vals):.0f}"
    )
    print(
        "  (model mode: compare these to the app's Expected line / engine log; "
        "on-device noise makes a sample-exact check meaningless)"
    )


# ----------------------------------------------------------------------
# Capture sources
# ----------------------------------------------------------------------


def read_capture(path: str) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        a, b = line.split(",")[:2]
        out.append((float(a), float(b)))
    return out


async def collect_live(address: str, minutes: float) -> list[tuple[float, float]]:
    from bleak import BleakClient

    CGM_MEAS = "00002aa7-0000-1000-8000-00805f9b34fb"
    got: list[tuple[float, float]] = []

    def on_notify(_char, data: bytearray) -> None:
        if len(data) < 6:
            return
        raw = int.from_bytes(data[2:4], "little")
        mant = raw & 0x0FFF
        if mant >= 0x0800:
            mant -= 0x1000
        exp = raw >> 12
        if exp >= 0x8:
            exp -= 0x10
        glucose = mant * (10**exp)
        t_off = int.from_bytes(data[4:6], "little")
        got.append((float(t_off), float(round(glucose, 1))))
        print(f"    rx  t_off={t_off}min  glucose={glucose:.1f}  raw={data.hex()}")

    import asyncio

    async with BleakClient(address) as client:
        await client.start_notify(CGM_MEAS, on_notify)
        print(f"  collecting for {minutes} min…  (Ctrl-C to stop early)")
        try:
            await asyncio.sleep(minutes * 60)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        await client.stop_notify(CGM_MEAS)
    return got


# ----------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="mode", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--profile", help="PersonProfile name from data/profiles.json")
    common.add_argument("--csv-path", help="Dexcom CSV (instead of --profile)")
    common.add_argument("--start", help="ISO datetime of the 24 h window start (with --csv-path)")
    common.add_argument("--food-log", help="matching Food Log CSV (informational only here)")
    common.add_argument(
        "--model",
        action="store_true",
        help="treat the stream as model-backed: print a distribution summary "
        "instead of an exact diff",
    )

    off = sub.add_parser("offline", parents=[common], help="rebuild + diff a capture file")
    off.add_argument("--received", help="capture file: 'time_offset_min,glucose' per line")

    liv = sub.add_parser("live", parents=[common], help="connect to the board and diff")
    liv.add_argument("--address", required=True, help="board BLE address / UUID")
    liv.add_argument("--minutes", type=float, default=5.0)

    args = ap.parse_args()

    if args.mode == "offline":
        if not args.received:
            # No capture: just show the expected series head so it can be eyeballed.
            samples, interval_s = csv_window_from_args(args)
            print(
                f"expected CSV series: {len(samples)} rows @ {interval_s}s "
                f"({len(samples) * interval_s / 3600:.1f} h)"
            )
            for k in range(0, min(len(samples), 12)):
                print(f"  row {k:>3}  t_off={k * interval_s / 60:.0f}min  {samples[k]} mg/dL")
            return 0
        received = read_capture(args.received)
    else:
        import asyncio

        received = asyncio.run(collect_live(args.address, args.minutes))

    if not received:
        print("no readings collected")
        return 1

    if args.model:
        summarize_model(received)
        return 0

    samples, interval_s = csv_window_from_args(args)
    ok = diff_csv(received, samples, interval_s)
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
