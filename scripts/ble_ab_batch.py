"""A/B batch runner for the multi-sensor BLE silent-subscribe investigation.

Sensor 4 sometimes accepts start_notify and then never notifies. A single soak
run cannot tell a fix from run-to-run luck, so this runs a matrix of host-side
configurations several times each and reports, per configuration, how often each
sensor ended up silent.

It drives scripts/ble_soak.py (the app must be closed — the soak owns the
board's links) and reads the <out>/soak-<stamp>.json result each run writes.

Firmware-side variants (e.g. CONFIG_APP_CGMS_STAGGER_NOTIFY on/off) need a
rebuild + reflash between batches: run one batch per firmware build and give
each its own --fw-label, then compare with --compare.

    uv run python scripts/ble_ab_batch.py --fw-label stagger-on --repeats 5
    uv run python scripts/ble_ab_batch.py --fw-label stagger-off --repeats 5
    uv run python scripts/ble_ab_batch.py --compare stagger-on stagger-off

    # only some configurations, with the board's serial log captured per run
    uv run python scripts/ble_ab_batch.py --fw-label x --configs baseline,only4 --serial
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

SOAK = Path(__file__).with_name("ble_soak.py")
SERIAL_PS1 = Path(__file__).with_name("ble_soak_serial.ps1")

# name -> extra ble_soak.py arguments. Each isolates one hypothesis:
#   baseline      today's behaviour: all four at once, scan-enumeration order
#   reverse       sensor 4 gets first pick of the connect order
#   serial-rev    sensor 4 fully streaming before any other identity connects
#   stagger       connections opened 4 s apart instead of together
#   nocache       Windows GATT cache bypassed (stale same-UUID service cache?)
#   only4         sensor 4 alone: identity-specific, or concurrency-specific?
#   only34/only234  how far does the failure follow the number of live links?
CONFIGS: dict[str, list[str]] = {
    "baseline": [],
    "reverse": ["--connect-order", "reverse"],
    "serial-rev": ["--connect-order", "reverse", "--serialize-first"],
    "stagger": ["--stagger-s", "4"],
    "nocache": ["--no-cache"],
    "only4": ["--only", "4"],
    "only34": ["--only", "3,4"],
    "only234": ["--only", "2,3,4"],
}

# A sensor delivering fewer notifications than this share of the busiest sensor
# in the same run counts as silent. The board pushes every ~5 s, so a healthy
# sensor lands within a couple of notifications of its siblings.
SILENT_SHARE = 0.2


def run_once(name: str, extra: list[str], args, out_dir: Path, fw_label: str) -> dict | None:
    """One soak. Returns its JSON result, or None when no result was written."""
    before = set(out_dir.glob("soak-*.json"))
    cmd = [
        sys.executable,
        str(SOAK),
        "--minutes",
        str(args.minutes),
        "--out",
        str(out_dir),
        "--label",
        f"{fw_label}/{name}",
        *extra,
    ]
    serial = None
    if args.serial:
        serial = subprocess.Popen(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(SERIAL_PS1),
                "-Minutes",
                str(args.minutes + 1),
                "-Out",
                str(out_dir),
            ],
            stdout=subprocess.DEVNULL,
        )
        time.sleep(2)
    # Discovery can retry for a while before the timed part even starts.
    budget = args.minutes * 60 + 240
    try:
        subprocess.run(cmd, timeout=budget, check=False, capture_output=True)
    except subprocess.TimeoutExpired:
        print(f"    soak exceeded {budget:.0f}s and was killed", flush=True)
    finally:
        if serial is not None:
            serial.terminate()
    new = sorted(set(out_dir.glob("soak-*.json")) - before)
    return json.loads(new[-1].read_text(encoding="utf-8")) if new else None


def silent_sensors(result: dict) -> list[str]:
    counts = {n: s["notifications"] for n, s in result["sensors"].items()}
    best = max(counts.values(), default=0)
    if best == 0:
        return sorted(counts)
    return sorted(n for n, c in counts.items() if c < SILENT_SHARE * best)


def short(name: str) -> str:
    return name.rsplit(" ", 1)[-1]


def load_runs(root: Path, fw_label: str) -> list[dict]:
    path = root / fw_label / "runs.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def table(runs: list[dict]) -> list[str]:
    by_config: dict[str, list[dict]] = {}
    for r in runs:
        by_config.setdefault(r["config"], []).append(r)
    lines = [
        f"{'config':12} {'runs':>4} {'no-result':>9}  "
        + "  ".join(f"S{i} silent" for i in range(1, 5))
        + "   median notifications S1/S2/S3/S4"
    ]
    for config, rs in by_config.items():
        got = [r for r in rs if r["result"]]
        silent_by = {str(i): 0 for i in range(1, 5)}
        present = {str(i): 0 for i in range(1, 5)}
        notes: dict[str, list[int]] = {str(i): [] for i in range(1, 5)}
        for r in got:
            for n, s in r["result"]["sensors"].items():
                present[short(n)] = present.get(short(n), 0) + 1
                notes.setdefault(short(n), []).append(s["notifications"])
            for n in silent_sensors(r["result"]):
                silent_by[short(n)] = silent_by.get(short(n), 0) + 1
        cells = [
            f"{silent_by[i]:>2}/{present[i]:<2}   " if present[i] else "  -     " for i in "1234"
        ]
        med = "/".join(str(int(statistics.median(notes[i]))) if notes[i] else "-" for i in "1234")
        silent_cells = "  ".join(cells)
        lines.append(f"{config:12} {len(rs):>4} {len(rs) - len(got):>9}  {silent_cells}   {med}")
    return lines


@contextlib.contextmanager
def board_lock(root: Path):
    """Refuse to run while another batch holds the board.

    Two batches at once split the board's four links between two soaks and
    silently corrupt both result sets (they did, once). The lock file holds the
    owner's pid; if a crash left it behind, delete it by hand.
    """
    root.mkdir(parents=True, exist_ok=True)
    path = root / ".batch.lock"
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        owner = path.read_text(encoding="utf-8").strip() or "unknown"
        raise SystemExit(
            f"another batch holds the board (pid {owner}); if it is dead, delete {path}"
        ) from None
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(str(os.getpid()))
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--fw-label", default="", help="name of the firmware build under test")
    ap.add_argument("--repeats", type=int, default=3, help="runs per configuration (default: 3)")
    ap.add_argument("--minutes", type=float, default=3.0, help="length of each run (default: 3)")
    ap.add_argument(
        "--configs",
        default=",".join(CONFIGS),
        help=f"comma-separated subset of: {', '.join(CONFIGS)} (default: all)",
    )
    ap.add_argument(
        "--pause-s",
        type=float,
        default=20.0,
        help="rest between runs so the board re-advertises every identity (default: 20)",
    )
    ap.add_argument("--serial", action="store_true", help="capture the board's console per run")
    ap.add_argument("--out", default=".scratch/soak/ab", help="output directory")
    ap.add_argument(
        "--compare", nargs="+", metavar="FW_LABEL", help="only print the table of these builds"
    )
    args = ap.parse_args()
    root = Path(args.out)

    if args.compare:
        for fw in args.compare:
            print(f"== {fw}")
            print("\n".join(table(load_runs(root, fw))), "\n")
        return 0
    if not args.fw_label:
        ap.error("--fw-label is required (or use --compare)")

    unknown = [c for c in args.configs.split(",") if c not in CONFIGS]
    if unknown:
        ap.error(f"unknown config(s): {', '.join(unknown)}")
    out_dir = root / args.fw_label
    out_dir.mkdir(parents=True, exist_ok=True)
    names = args.configs.split(",")
    # Interleave (round-robin) rather than finishing one config first, so slow
    # drift on this PC's Bluetooth stack does not masquerade as a config effect.
    plan = [(n, rep) for rep in range(args.repeats) for n in names]
    total_min = len(plan) * (args.minutes + args.pause_s / 60 + 0.5)
    print(f"{len(plan)} runs of {args.minutes} min, about {total_min:.0f} min total", flush=True)

    for i, (name, rep) in enumerate(plan, 1):
        print(f"[{i}/{len(plan)}] {name} (repeat {rep + 1})", flush=True)
        result = run_once(name, CONFIGS[name], args, out_dir, args.fw_label)
        silent = silent_sensors(result) if result else None
        print(f"    silent: {silent if result else 'NO RESULT'}", flush=True)
        record = {"config": name, "repeat": rep + 1, "result": result}
        with (out_dir / "runs.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
        time.sleep(args.pause_s)

    report = "\n".join(table(load_runs(root, args.fw_label)))
    (out_dir / "report.txt").write_text(report + "\n", encoding="utf-8")
    print("\n" + report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
