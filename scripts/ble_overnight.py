"""Unattended overnight BLE verification, three hypotheses, one board, one at a time.

    H2  cold starts   stagger-on firmware starts all four sensors non-silent,
                      measured over many fresh connects (ble_ab_batch.py)
    H3  churn         dropping one sensor's link every couple of minutes leaves
                      the other three streaming and the dropped one recovers
    H1  long soak     the shipping config holds all four sensors for hours; this
                      phase takes whatever time is left before --until

Everything is sequential because the board has four links and a second client
splits them. The board must already be flashed with the build under test, and
the app must be closed. Writes <out>/<stamp>/REPORT.md as it goes, so a run cut
short still leaves a verdict for the phases that finished.

    uv run python scripts/ble_overnight.py --until 09:00
"""

from __future__ import annotations

import argparse
import ctypes
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOAK = HERE / "ble_soak.py"
BATCH = HERE / "ble_ab_batch.py"
SERIAL_PS1 = HERE / "ble_soak_serial.ps1"

NOTIFY_INTERVAL_S = 5.0  # the board pushes one measurement per sensor this often
RESERVE_MIN = 10  # left free before --until for the last summary

# Pass criteria, stated up front so the verdicts are not fitted to the data.
H1_MIN_SHARE = 0.97  # every sensor delivers this share of the expected notifications
H1_MAX_GAP_S = 60.0  # and never goes quiet longer than this
H3_MIN_RECOVERED = 1.0  # every dropped sensor streams again within --churn-recovery-s


def keep_awake() -> None:
    """Stop Windows sleeping while this process lives (ES_CONTINUOUS | ES_SYSTEM_REQUIRED)."""
    ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)


def deadline_from(clock: str) -> datetime:
    """The next local occurrence of HH:MM."""
    hour, minute = (int(x) for x in clock.split(":"))
    now = datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return target if target > now else target + timedelta(days=1)


def minutes_left(deadline: datetime) -> float:
    return (deadline - datetime.now()).total_seconds() / 60


def log(out: Path, text: str) -> None:
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {text}"
    print(line, flush=True)
    with (out / "orchestrator.log").open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def with_serial(out_dir: Path, minutes: float, cmd: list[str], budget_s: float) -> None:
    """Run *cmd* with the board's console captured alongside, killed at *budget_s*."""
    serial = subprocess.Popen(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SERIAL_PS1),
            "-Minutes",
            str(minutes + 1),
            "-Out",
            str(out_dir),
        ],
        stdout=subprocess.DEVNULL,
    )
    time.sleep(2)
    try:
        subprocess.run(cmd, timeout=budget_s, check=False, capture_output=True)
    except subprocess.TimeoutExpired:
        pass
    finally:
        serial.terminate()


def soak_phase(out: Path, name: str, minutes: float, extra: list[str]) -> dict | None:
    """One timed soak (retried while the scan finds nothing). Returns its JSON."""
    phase_dir = out / name
    phase_dir.mkdir(parents=True, exist_ok=True)
    for attempt in range(3):
        before = set(phase_dir.glob("soak-*.json"))
        cmd = [
            sys.executable,
            str(SOAK),
            "--minutes",
            f"{minutes:.1f}",
            "--out",
            str(phase_dir),
            "--label",
            name,
            *extra,
        ]
        with_serial(phase_dir, minutes, cmd, budget_s=minutes * 60 + 600)
        new = sorted(set(phase_dir.glob("soak-*.json")) - before)
        if new:
            return json.loads(new[-1].read_text(encoding="utf-8"))
        time.sleep(60)
        log(out, f"{name}: no result, retry {attempt + 2}/3")
    return None


# ---- verdicts ---------------------------------------------------------------


def verdict_h1(result: dict | None) -> list[str]:
    if not result:
        return ["NO RESULT (the soak never produced a summary)"]
    expected = result["elapsed_s"] / NOTIFY_INTERVAL_S
    lines, ok = [], True
    for name, s in result["sensors"].items():
        share = s["notifications"] / expected if expected else 0.0
        bad = share < H1_MIN_SHARE or s["worst_gap_s"] > H1_MAX_GAP_S
        ok &= not bad
        lines.append(
            f"- {name}: {s['notifications']}/{expected:.0f} ({share:.1%}), worst gap"
            f" {s['worst_gap_s']}s, gaps {s['gaps']}, silent subscribes"
            f" {s['silent_subscribes']}, resubscribes {s['resubscribes']}, connects"
            f" {s['connects']}, uptime {s['uptime_pct']}%{'  <-- FAIL' if bad else ''}"
        )
    return [f"**{'PASS' if ok else 'FAIL'}** over {result['elapsed_s'] / 3600:.2f} h", *lines]


def verdict_h3(result: dict | None) -> list[str]:
    if not result:
        return ["NO RESULT (the soak never produced a summary)"]
    sensors = result["sensors"]
    drops = sum(s["churns"] for s in sensors.values())
    rec = sum(s["churn_recovered"] for s in sensors.values())
    times = sorted(t for s in sensors.values() for t in s["churn_recovery_s"])
    gaps = sum(s["gaps"] for s in sensors.values())
    ok = drops > 0 and rec / drops >= H3_MIN_RECOVERED and gaps == 0
    median = times[len(times) // 2] if times else float("nan")
    worst = times[-1] if times else float("nan")
    lines = [
        f"**{'PASS' if ok else 'FAIL'}**: {drops} drops, {rec} recovered, recovery"
        f" median {median}s / worst {worst}s, gaps over threshold across all sensors: {gaps}"
    ]
    for name, s in sensors.items():
        lines.append(
            f"- {name}: drops {s['churns']}, recovered {s['churn_recovered']}, failed"
            f" {s['churn_failed']}, gaps {s['gaps']} (worst {s['worst_gap_s']}s), notifications"
            f" {s['notifications']}, silent subscribes {s['silent_subscribes']}"
        )
    return lines


def verdict_h2(batch_dir: Path) -> list[str]:
    runs_file = batch_dir / "runs.jsonl"
    if not runs_file.exists():
        return ["NO RESULT (no batch runs recorded)"]
    runs = [json.loads(x) for x in runs_file.read_text(encoding="utf-8").splitlines() if x]
    got = [r for r in runs if r["result"]]
    sensor_runs = silent = 0
    per_sensor: dict[str, int] = {}
    silent_starts = 0  # initial subscribes that said OK and delivered nothing
    for r in got:
        counts = {n: s["notifications"] for n, s in r["result"]["sensors"].items()}
        best = max(counts.values(), default=0)
        for n, c in counts.items():
            sensor_runs += 1
            if best == 0 or c < 0.2 * best:
                silent += 1
                per_sensor[n[-1]] = per_sensor.get(n[-1], 0) + 1
        silent_starts += sum(s["silent_subscribes"] for s in r["result"]["sensors"].values())
    upper = wilson_upper(silent, sensor_runs)
    return [
        f"**{'PASS' if silent == 0 else 'FAIL'}**: {len(got)} of {len(runs)} runs produced a"
        f" result; sensors left silent at the end of a run: {silent}/{sensor_runs} (95% upper"
        f" bound {upper:.1%}); by sensor {per_sensor or 'none'}; start-ups that needed the"
        f" harness's forced reconnect (silent subscribe): {silent_starts}"
    ]


def wilson_upper(k: int, n: int, z: float = 1.96) -> float:
    """Upper end of the Wilson 95% interval for k events in n trials."""
    if n == 0:
        return 1.0
    p = k / n
    centre = p + z * z / (2 * n)
    spread = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return min(1.0, (centre + spread) / (1 + z * z / n))


def write_report(out: Path, sections: dict[str, list[str]], started: datetime) -> None:
    lines = [
        "# Overnight BLE verification",
        "",
        f"started {started:%Y-%m-%d %H:%M}, report written {datetime.now():%Y-%m-%d %H:%M}",
        "",
    ]
    for title, body in sections.items():
        lines += [f"## {title}", "", *body, ""]
    (out / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--until", default="09:00", help="stop at this local time (default: 09:00)")
    ap.add_argument("--out", default=".scratch/overnight", help="output directory")
    ap.add_argument("--cold-repeats", type=int, default=20, help="H2: runs per config (x2 configs)")
    ap.add_argument("--churn-minutes", type=float, default=90.0, help="H3 length")
    ap.add_argument("--churn-s", type=float, default=120.0, help="H3: seconds between drops")
    args = ap.parse_args()

    keep_awake()
    started = datetime.now()
    deadline = deadline_from(args.until)
    out = Path(args.out) / started.strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    sections: dict[str, list[str]] = {}
    log(out, f"until {deadline:%Y-%m-%d %H:%M} ({minutes_left(deadline):.0f} min available)")

    # H2 — many cold starts. Alternates the two connect orders so a slow drift in
    # this PC's Bluetooth stack cannot masquerade as an order effect.
    if minutes_left(deadline) > 60:
        log(out, "H2 cold starts")
        batch_cmd = [
            sys.executable,
            str(BATCH),
            "--fw-label",
            "cold-starts",
            "--configs",
            "baseline,reverse",
            "--repeats",
            str(args.cold_repeats),
            "--minutes",
            "2",
            "--pause-s",
            "15",
            "--serial",
            "--out",
            str(out / "H2"),
        ]
        cap_s = min(args.cold_repeats * 2 * 4.0 * 60, (minutes_left(deadline) - RESERVE_MIN) * 60)
        try:
            subprocess.run(batch_cmd, timeout=cap_s, check=False, capture_output=True)
        except subprocess.TimeoutExpired:
            log(out, "H2 hit its time cap; keeping the runs recorded so far")
            (out / "H2" / ".batch.lock").unlink(missing_ok=True)
        sections["H2 — cold starts"] = verdict_h2(out / "H2" / "cold-starts")
        write_report(out, sections, started)
        time.sleep(30)

    # H3 — churn. gap threshold 15 s: a healthy sensor never goes quiet longer than ~6 s.
    if minutes_left(deadline) > args.churn_minutes + RESERVE_MIN + 30:
        log(out, "H3 churn")
        result = soak_phase(
            out,
            "H3",
            args.churn_minutes,
            ["--churn-s", str(args.churn_s), "--gap-seconds", "15"],
        )
        sections["H3 — link churn"] = verdict_h3(result)
        write_report(out, sections, started)
        time.sleep(30)

    # H1 — long soak with everything that is left.
    remaining = minutes_left(deadline) - RESERVE_MIN
    if remaining > 20:
        log(out, f"H1 long soak for {remaining:.0f} min")
        result = soak_phase(out, "H1", remaining, ["--gap-seconds", "20"])
        sections["H1 — long soak"] = verdict_h1(result)
        write_report(out, sections, started)

    log(out, "done")
    (out / "DONE").write_text(datetime.now().isoformat(), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
