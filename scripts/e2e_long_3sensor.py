"""Long functional run of the 3-sensor build through the real app path.

Drives a real MainWindow + BleSessions (what the app does), pushes a layout with
two physiological models and a CSV replay, inserts food / exercise / PISA events
while it runs, and checks for the whole run that the data on the phone side is
the data the board produced and that the board is following its models:

  * every BLE measurement equals a value the board's console says it pushed
  * per-sensor completeness (notifications vs the 5 s cadence) and worst gap
  * each slot's whole trajectory against the app's own host-side model
    (models.engine.ModelStepper, the "expected" line), instant events replayed
  * the CSV slot against the CSV samples at the board's own sim time
  * each inserted event produces its effect on the board (carbs / exercise /
    PISA dip, glucose rise)

    uv run python scripts/e2e_long_3sensor.py --minutes 120

Run with the app closed and a 3-sensor firmware flashed. Output:
test-artifacts/long3-<stamp>/ (REPORT.md rewritten every 10 minutes, serial.log,
series.csv). The harness restores data/ when it ends.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import io
import itertools
import json
import re
import statistics
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "scripts"))

import userdata_guard
from e2e import SERIAL_PORT, SerialTap, Stream, Tee, pump
from e2e_4sensor import DEXCOM_CSV, FourCtx, _sensors
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

import gui.main_window as mw
from api import protocol
from models import dexcom_csv, royparker, uva_padova
from models.engine import ModelStepper, load_csv_window
from models.types import ExerciseEvent, FoodEvent, ModelId, PersonProfile

SLOTS = 3
NOTIFY_INTERVAL_S = 5.0

# --- pass criteria, fixed before the run -------------------------------------
MIN_COMPLETENESS = 0.97  # notifications / expected, per sensor
MAX_GAP_S = 30.0
MIN_BLE_MATCH = 0.995  # BLE values that equal a value the board pushed
PUSH_MATCH_TOL = 0.6  # mg/dL: SFLOAT rounds to 1 mg/dL above 204.7
PUSH_MATCH_WINDOW_S = 4.0  # serial lines reach the host a little after the push
PARITY_P99_ABS = 2.0  # mg/dL, board vs host model, instants replayed; p99 so a one-tick
# offset at a CSV sample boundary (a step of several mg/dL) is not read as drift
CSV_TOL = 3.0  # mg/dL against the CSV sample (neighbouring samples allowed)
MIN_MEAL_RISE = 5.0  # mg/dL rise after a scheduled meal

_TICK = re.compile(
    r"^\s*(?P<t>[\d.]+)\s+.*?model_tick\[(?P<slot>\d)\]:\s+t_sim=(?P<t_sim>[-\d.]+)min\s+"
    r"dt=(?P<dt>[-\d.]+)\s+.*?glucose=(?P<g>[-\d.]+)\s+reading=(?P<r>[-\d.]+)\s+"
    r"pisa=(?P<pisa>[-\d.]+)\s+carbs=(?P<carbs>[-\d.]+)\s+ex=(?P<ex>[-\d.]+)"
)
_PUSH = re.compile(r"^\s*(?P<t>[\d.]+)\s+.*?pushed slot (?P<slot>\d) glucose=(?P<g>[-\d.]+)")


@dataclass
class Tick:
    t: float  # host time of the console line
    t_sim: float
    dt: float
    glucose: float
    reading: float
    pisa: float
    carbs: float
    ex: float


@dataclass
class Instant:
    slot: int
    kind: str  # food | exercise | pisa
    args: tuple
    t_sim: float  # the slot's sim time when the write was issued


# ---- analysis (pure: reads the logs, no hardware) ---------------------------


def read_ticks(path: Path) -> dict[int, list[Tick]]:
    out: dict[int, list[Tick]] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = _TICK.match(line)
        if m:
            out.setdefault(int(m["slot"]), []).append(
                Tick(
                    float(m["t"]),
                    float(m["t_sim"]),
                    float(m["dt"]),
                    float(m["g"]),
                    float(m["r"]),
                    float(m["pisa"]),
                    float(m["carbs"]),
                    float(m["ex"]),
                )
            )
    return out


def read_pushes(path: Path) -> dict[int, list[tuple[float, float]]]:
    out: dict[int, list[tuple[float, float]]] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = _PUSH.match(line)
        if m:
            out.setdefault(int(m["slot"]), []).append((float(m["t"]), float(m["g"])))
    return out


def last_run(ticks: list[Tick]) -> list[Tick]:
    """The ticks since the last sim-clock reset (the sim restarts from 0 on every
    config apply, so anything earlier belongs to a previous setup)."""
    start = 0
    for i in range(1, len(ticks)):
        if ticks[i].t_sim < ticks[i - 1].t_sim - 1e-6:
            start = i
    return ticks[start:]


def ble_match(
    ble: list[tuple[float, float]], pushes: list[tuple[float, float]]
) -> tuple[int, int, list[tuple[float, float]]]:
    """(matched, total, unmatched examples): each BLE value must equal a value the
    board pushed within PUSH_MATCH_WINDOW_S of it arriving."""
    matched, bad = 0, []
    j = 0
    for t, v in ble:
        while j < len(pushes) and pushes[j][0] < t - PUSH_MATCH_WINDOW_S:
            j += 1
        k, hit = j, False
        while k < len(pushes) and pushes[k][0] <= t + PUSH_MATCH_WINDOW_S:
            if abs(pushes[k][1] - v) <= PUSH_MATCH_TOL:
                hit = True
                break
            k += 1
        matched += hit
        if not hit and len(bad) < 5:
            bad.append((round(t, 1), v))
    return matched, len(ble), bad


def completeness(times: list[float]) -> tuple[int, float, float, float]:
    """(count, expected, share, worst_gap_s) over the span of *times*."""
    if len(times) < 2:
        return len(times), 0.0, 0.0, 0.0
    span = times[-1] - times[0]
    expected = span / NOTIFY_INTERVAL_S + 1
    gaps = [b - a for a, b in itertools.pairwise(times)]
    return len(times), expected, len(times) / expected, max(gaps)


def observed_onset(ev: Instant, run: list[Tick]) -> Instant:
    """Align an inserted event to the tick where the board's console first shows it.

    The issue time was read off the last console line before the write, which is a
    couple of real seconds (about half a sim-minute at x10) before the board applied
    it; on a PISA edge falling 9 mg/dL per minute that alone is a 4 mg/dL error. The
    board's own first affected tick is the exact start, so replay from there."""
    window = [tk for tk in run if ev.t_sim - 1.0 <= tk.t_sim <= ev.t_sim + 3.0]
    if len(window) < 2:
        return ev
    base = window[0]
    for i, tk in enumerate(window[1:], start=1):
        if ev.kind == "pisa":
            hit = tk.pisa < 0.9999
        elif ev.kind == "food":
            hit = tk.carbs > base.carbs + 0.05
        else:
            hit = tk.ex > base.ex + 0.5
        if hit:
            # The stepper consumes an instant on the tick that first shows it. A PISA
            # bout starts at zero depth (sin 0), so its first visible tick is its second.
            first = max(0, i - (2 if ev.kind == "pisa" else 1))
            # The console prints t_sim to 2 decimals, so the true clock can sit up to
            # 0.005 below the printed value; start just under it so a ">=" in the
            # replay cannot defer the event by a whole tick.
            return Instant(ev.slot, ev.kind, ev.args, window[first].t_sim - 0.006)
    return ev


def parity(profile: PersonProfile, ticks: list[Tick], instants: list[Instant]) -> dict:
    """Step the host model with the board's own dt per tick and compare it with the
    board's true glucose x PISA factor.

    That product is what the host model's output means (the expected reading,
    PISA included) and it is independent of the sensor model: the board adds noise
    (Breton) or a deliberate per-slot offset (Ideal: (slot - (N-1)/2) * 2 mg/dL,
    model_thread.c) only after it, in ``reading``."""
    run = last_run(ticks)
    if len(run) < 10:
        return {"n": 0}
    pending = sorted((observed_onset(e, run) for e in instants), key=lambda e: e.t_sim)
    errs: list[float] = []
    worst = (0.0, 0.0)  # (abs err, t_sim)
    # ModelStepper prints a debug line per tick; keep it out of the report output.
    with contextlib.redirect_stdout(io.StringIO()):
        stepper = ModelStepper(profile)
        for tk in run:
            k = round((tk.t_sim - stepper.sim_clock_min) / tk.dt) if tk.dt > 0 else 0
            res = None
            for _ in range(max(0, k)):
                while pending and stepper.sim_clock_min >= pending[0].t_sim:
                    ev = pending.pop(0)
                    if ev.kind == "food":
                        stepper.add_instant_food(*ev.args)
                    elif ev.kind == "exercise":
                        stepper.add_instant_exercise(*ev.args)
                    else:
                        stepper.add_instant_pisa(*ev.args)
                res = stepper.tick(tk.dt, "2020-01-01T00:00:00+00:00")
            if res is not None:
                err = abs(res.glucose - tk.glucose * tk.pisa)
                errs.append(err)
                if err > worst[0]:
                    worst = (err, tk.t_sim)
    if not errs:
        return {"n": 0}
    errs.sort()
    return {
        "n": len(errs),
        "max": errs[-1],
        "mean": statistics.fmean(errs),
        "p99": errs[int(0.99 * (len(errs) - 1))],
        "worst_t_sim": worst[1],
    }


def csv_tracking(profile: PersonProfile, ticks: list[Tick]) -> dict:
    """Board glucose vs the CSV sample for its own sim time (neighbours allowed)."""
    samples, interval_s, _ = load_csv_window(profile)
    run = last_run(ticks)
    ok = total = 0
    worst = 0.0
    for tk in run:
        idx = int(tk.t_sim * 60 // interval_s)
        cands = [samples[i] for i in (idx - 1, idx, idx + 1) if 0 <= i < len(samples)]
        if not cands:
            continue
        total += 1
        err = min(abs(tk.glucose - c) for c in cands)
        worst = max(worst, err)
        ok += err <= CSV_TOL
    return {"n": total, "ok": ok, "worst": worst}


def meal_rises(ticks: list[Tick], meal_times_min: list[int], window_min: float = 240.0) -> list:
    """(meal sim-minute, rise in mg/dL) for each scheduled meal inside the run."""
    run = last_run(ticks)
    out = []
    for meal in meal_times_min:
        around = [tk for tk in run if meal <= tk.t_sim <= meal + window_min]
        before = [tk for tk in run if meal - 5 <= tk.t_sim <= meal]
        if not around or not before or run[-1].t_sim < meal + 60:
            continue
        out.append((meal, max(t.glucose for t in around) - before[0].glucose))
    return out


# ---- the live run -----------------------------------------------------------

EVENT_PLAN = [  # (minute of the planned 120, slot, kind, args)
    (20, 1, "food", (45, 60.0)),
    (25, 0, "pisa", (12, 0.45)),
    (50, 1, "food", (30, 40.0)),
    (55, 0, "exercise", (40, 60.0)),
    (80, 1, "food", (45, 60.0)),
    (85, 0, "pisa", (12, 0.45)),
    (100, 0, "exercise", (30, 50.0)),
]
MEALS_SLOT0 = [60, 360, 720, 1080]
MEALS_SLOT1 = [120, 840]


def build_profiles(csv_start_iso: str) -> dict[int, PersonProfile]:
    roy = PersonProfile("L-RoyParker", ModelId.ROYPARKER, royparker.default_params())
    roy.food_events = [FoodEvent(m, 50.0, 15) for m in MEALS_SLOT0]
    roy.exercise_events = [ExerciseEvent(480, 45, 60.0), ExerciseEvent(900, 45, 60.0)]
    uva = PersonProfile("L-UVA", ModelId.UVA_PADOVA, uva_padova.default_params())
    uva.food_events = [FoodEvent(m, 45.0, 30) for m in MEALS_SLOT1]
    csv_p = PersonProfile("L-CSV", ModelId.CAMBRIDGE, {})
    csv_p.data_source = "csv"
    csv_p.csv_path = DEXCOM_CSV
    csv_p.csv_window_start_iso = csv_start_iso
    return {0: roy, 1: uva, 2: csv_p}


class _Asserts:
    """push_layout only needs assert_; a failed one aborts the run's setup."""

    def assert_(self, ok: bool, name: str, detail: str = "") -> None:
        if not ok:
            raise RuntimeError(f"setup check failed: {name} {detail}")


def issue(ctx: FourCtx, ev: Instant) -> None:
    sess = ctx.cfg_session()
    sess.queue_write("sensor_select", protocol.encode_sensor_select(ev.slot))
    pump(300)
    if ev.kind == "food":
        sess.queue_write("food_instant", protocol.encode_food_instant(*ev.args))
    elif ev.kind == "exercise":
        sess.queue_write("exercise_instant", protocol.encode_exercise_instant(*ev.args))
    else:
        sess.queue_write("pisa_instant", protocol.encode_pisa_instant(*ev.args))


class EventCheck:
    """Waits for one inserted event's effect on its slot, reading the board's console."""

    def __init__(self, ev: Instant, before: dict, now: float) -> None:
        self.ev, self.before, self.deadline = ev, before, now + 400.0
        self.seen: dict[str, bool] = {}
        self.result: str | None = None

    def poll(self, line: dict | None, now: float) -> None:
        if self.result is not None or line is None:
            return
        k, g0 = self.ev.kind, self.before["glucose"]
        if k == "food":
            self.seen["carbs>0"] = self.seen.get("carbs>0") or line["carbs"] > 0.05
            self.seen["glucose rises"] = (
                self.seen.get("glucose rises") or line["glucose"] > g0 + 3.0
            )
        elif k == "exercise":
            self.seen["ex>0"] = self.seen.get("ex>0") or line["ex"] > 1.0
        else:
            dipped = line["pisa"] < 0.9
            self.seen["pisa dips"] = self.seen.get("pisa dips") or dipped
            if dipped:
                self.seen["reading below glucose"] = self.seen.get("reading below glucose") or (
                    line["reading"] < line["glucose"] - 3.0
                )
            if self.seen.get("pisa dips") and line["pisa"] > 0.99:
                self.seen["recovers"] = True
        need = {
            "food": ["carbs>0", "glucose rises"],
            "exercise": ["ex>0"],
            "pisa": ["pisa dips", "reading below glucose", "recovers"],
        }[k]
        if all(self.seen.get(n) for n in need):
            self.result = "PASS"
        elif now > self.deadline:
            self.result = (
                "FAIL (missing: " + ", ".join(n for n in need if not self.seen.get(n)) + ")"
            )


def write_report(
    run_dir: Path,
    ctx_info: dict,
    state: dict,
    final: bool,
    link_health: list[str] | None = None,
    name: str = "REPORT.md",
) -> None:
    serial_log = run_dir / "serial.log"
    lines = [
        "# 3-sensor long run",
        "",
        f"{'FINAL' if final else 'in progress'} — written {datetime.now():%Y-%m-%d %H:%M:%S}; "
        f"run so far {state['elapsed_min']:.1f} of {ctx_info['minutes']:.0f} min, "
        f"speed x{ctx_info['speed']:g}",
        "",
    ]
    verdicts: list[tuple[str, bool]] = []
    try:
        ticks = read_ticks(serial_log)
        pushes = read_pushes(serial_log)
    except OSError:
        ticks, pushes = {}, {}

    lines += ["## Completeness and delivery (BLE, per sensor)", ""]
    for slot in range(SLOTS):
        times = [t for t, _ in state["ble"].get(slot, [])]
        n, expected, share, gap = completeness(times)
        matched, total, bad = ble_match(state["ble"].get(slot, []), pushes.get(slot, []))
        mshare = matched / total if total else 0.0
        ok_c = share >= MIN_COMPLETENESS and gap <= MAX_GAP_S
        ok_m = mshare >= MIN_BLE_MATCH
        verdicts += [(f"S{slot + 1} completeness", ok_c), (f"S{slot + 1} BLE == board push", ok_m)]
        lines.append(
            f"- Sensor {slot + 1}: {n}/{expected:.0f} notifications ({share:.1%}), worst gap "
            f"{gap:.1f}s -> {'PASS' if ok_c else 'FAIL'}; BLE value equals a board push: "
            f"{matched}/{total} ({mshare:.2%}) -> {'PASS' if ok_m else 'FAIL'}"
            + (f"; unmatched {bad}" if bad else "")
        )

    lines += ["", "## Model following (board vs the app's own host model)", ""]
    profiles = ctx_info["profiles"]
    for slot in range(SLOTS):
        tk = ticks.get(slot, [])
        inst = [e for e in state["instants"] if e.slot == slot]
        p = parity(profiles[slot], tk, inst)
        if not p.get("n"):
            lines.append(f"- slot {slot}: no data yet")
            continue
        ok = p["p99"] <= PARITY_P99_ABS
        verdicts.append((f"slot {slot} parity", ok))
        lines.append(
            f"- slot {slot} ({profiles[slot].name}): {p['n']} ticks, board vs host model max "
            f"{p['max']:.2f} / p99 {p['p99']:.2f} / mean {p['mean']:.3f} mg/dL "
            f"(worst at sim {p['worst_t_sim']:.0f} min) -> {'PASS' if ok else 'FAIL'}"
        )

    c = csv_tracking(profiles[2], ticks.get(2, []))
    if c["n"]:
        ok = c["ok"] / c["n"] >= 0.99
        verdicts.append(("CSV tracking", ok))
        lines += [
            "",
            "## CSV slot",
            "",
            f"- {c['ok']}/{c['n']} board samples within {CSV_TOL:g} mg/dL of the CSV at the "
            f"board's sim time (worst {c['worst']:.1f}) -> {'PASS' if ok else 'FAIL'}",
        ]

    lines += ["", "## Scheduled meals (glucose rise after the meal)", ""]
    for slot, meals in ((0, MEALS_SLOT0), (1, MEALS_SLOT1)):
        for meal, rise in meal_rises(ticks.get(slot, []), meals):
            ok = rise >= MIN_MEAL_RISE
            verdicts.append((f"slot {slot} meal @{meal}", ok))
            lines.append(
                f"- slot {slot} meal at sim {meal} min: +{rise:.1f} mg/dL -> "
                f"{'PASS' if ok else 'FAIL'}"
            )

    lines += ["", "## Inserted events", ""]
    for chk in state["checks"]:
        ev = chk.ev
        res = chk.result or "pending"
        verdicts.append((f"{ev.kind} slot {ev.slot}", res == "PASS"))
        lines.append(
            f"- {ev.kind} {ev.args} on slot {ev.slot} at sim {ev.t_sim:.0f} min -> {res} "
            f"{dict(chk.seen)}"
        )

    lines += [
        "",
        "## Link health",
        "",
        *(
            link_health
            if link_health is not None
            else [
                f"- console lines kept arriving: {state['serial_fresh_pct']:.1f}% of samples",
                f"- link_silent events: {state['silent_events'] or 'none'}",
                f"- readback at start: {state['readback_start']}",
                f"- readback at end: {state['readback_end']}",
            ]
        ),
    ]
    failed = [n for n, ok in verdicts if not ok]
    lines += [
        "",
        f"## Verdict: {'PASS' if verdicts and not failed else 'FAIL' if failed else 'no data'}",
        "",
        (f"Failed checks: {', '.join(failed)}" if failed else "All checks within criteria."),
    ]
    (run_dir / name).write_text("\n".join(lines), encoding="utf-8")
    if name != "REPORT.md":
        return
    # Everything the analysis needs besides the console log, so a rule can be
    # re-scored offline without repeating the run.
    (run_dir / "state.json").write_text(
        json.dumps(
            {
                "ble": {str(k): v for k, v in state["ble"].items()},
                "instants": [
                    {"slot": e.slot, "kind": e.kind, "args": list(e.args), "t_sim": e.t_sim}
                    for e in state["instants"]
                ],
            }
        ),
        encoding="utf-8",
    )


def replay_check(ev: Instant, ticks: list[Tick]) -> EventCheck:
    """Re-evaluate an inserted event's effect from the saved console log."""
    run = last_run(ticks)
    before = [tk for tk in run if tk.t_sim <= ev.t_sim]
    after = [tk for tk in run if tk.t_sim >= ev.t_sim]
    if not before or not after:
        chk = EventCheck(ev, {"glucose": 0.0}, 0.0)
        chk.result = "no console data"
        return chk
    chk = EventCheck(ev, {"glucose": before[-1].glucose}, after[0].t)

    def as_line(tk: Tick) -> dict:
        return {
            "glucose": tk.glucose,
            "reading": tk.reading,
            "carbs": tk.carbs,
            "ex": tk.ex,
            "pisa": tk.pisa,
        }

    for tk in after:
        chk.poll(as_line(tk), tk.t)
        if chk.result is not None:
            break
    if chk.result is None:
        chk.poll(as_line(after[-1]), after[0].t + 1e6)
    return chk


def rescore(run_dir: Path) -> Path:
    """Rebuild the report of a finished run from its saved logs, with the current
    analysis rules. The original REPORT.md is left alone; the link-health section
    (not derivable from the logs) is carried over from it."""
    original = (run_dir / "REPORT.md").read_text(encoding="utf-8")
    head = re.search(r"run so far ([\d.]+) of ([\d.]+) min, speed x([\d.]+)", original)
    elapsed, minutes, speed = (float(x) for x in head.groups()) if head else (0.0, 120.0, 10.0)
    carried = None
    if "## Link health" in original:
        block = original.split("## Link health", 1)[1].split("## Verdict", 1)[0]
        carried = [ln for ln in block.splitlines() if ln.startswith("- ")]
    saved = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    rows_csv = dexcom_csv.read_egv(DEXCOM_CSV)
    instants = [
        Instant(e["slot"], e["kind"], tuple(e["args"]), e["t_sim"]) for e in saved["instants"]
    ]
    ticks = read_ticks(run_dir / "serial.log")
    state = {
        "ble": {int(k): [tuple(x) for x in v] for k, v in saved["ble"].items()},
        "instants": instants,
        "checks": [replay_check(ev, ticks.get(ev.slot, [])) for ev in instants],
        "elapsed_min": elapsed,
    }
    info = {
        "minutes": minutes,
        "speed": speed,
        "profiles": build_profiles(rows_csv[0][0].isoformat()),
    }
    write_report(
        run_dir,
        info,
        state,
        final=True,
        link_health=carried or ["- (not available)"],
        name="REPORT.rescored.md",
    )
    return run_dir / "REPORT.rescored.md"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--minutes", type=float, default=120.0)
    ap.add_argument("--speed", type=float, default=10.0)
    ap.add_argument("--board", default="D0:3F:4D:E2:7C:9B")
    ap.add_argument("--out", default=str(_ROOT / "test-artifacts"))
    ap.add_argument(
        "--rescore", metavar="RUN_DIR", help="rebuild REPORT.rescored.md for a finished run"
    )
    args = ap.parse_args()
    if args.rescore:
        print(rescore(Path(args.rescore)))
        return 0

    saved = userdata_guard.snapshot()
    run_id = datetime.now(UTC).strftime("%Y%m%d-%H%M%SZ")
    run_dir = Path(args.out) / f"long3-{run_id}"
    (run_dir / "cases").mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    streams = {
        "serial": Stream("serial", run_dir / "serial.log", 4000),
        "app": Stream("app", run_dir / "app.log", 2000),
        "ble": Stream("ble", run_dir / "ble.jsonl", 1000),
    }
    sys.stdout = Tee(sys.__stdout__, streams["app"], t0)
    sys.stderr = Tee(sys.__stderr__, streams["app"], t0)
    tap = SerialTap(SERIAL_PORT, streams["serial"], t0)
    if not tap.available:
        print("no serial console on", SERIAL_PORT, "- cannot check the board side")
        userdata_guard.restore(saved)
        return 1

    app = QApplication.instance() or QApplication(sys.argv)  # noqa: F841
    w = mw.MainWindow()
    w.show()
    pump(400)
    ctx = FourCtx(w, run_dir, t0, streams, args.board, True)

    ble: dict[int, list[tuple[float, float]]] = {i: [] for i in range(SLOTS)}
    addr_slot: dict[str, int] = {}

    def on_message(m: dict) -> None:
        g = m.get("glucose_value")
        slot = addr_slot.get(m.get("dev_id", ""))
        if g is not None and slot is not None:
            ble[slot].append((time.monotonic() - t0, float(g)))

    w._ble_log.new_message.connect(on_message)

    state: dict = {
        "ble": ble,
        "instants": [],
        "checks": [],
        "elapsed_min": 0.0,
        "serial_fresh_pct": 100.0,
        "silent_events": [],
        "readback_start": None,
        "readback_end": None,
    }
    rows = csv.writer((run_dir / "series.csv").open("w", newline="", encoding="utf-8"))
    rows.writerow(["t_real_s", "slot", "t_sim", "glucose", "reading", "carbs", "ex", "pisa"])

    try:
        found = ctx.scan()
        for name, addr in found.items():
            addr_slot[addr] = int(name.rsplit(" ", 1)[-1]) - 1
        for slot in range(SLOTS):
            for attempt in range(3):
                try:
                    sess = ctx.slot_session(slot)
                    sess.link_silent.connect(
                        lambda _a, s, n=slot: state["silent_events"].append(
                            (round(time.monotonic() - t0), n + 1, s)
                        )
                    )
                    break
                except Exception as exc:  # a skipped connect: retry from scratch
                    print(f"    slot {slot} connect attempt {attempt + 1} failed: {exc}")
                    ctx.drop_session(slot)
                    pump(3000)
            else:
                raise RuntimeError(f"could not connect sensor {slot + 1}")

        rows_csv = dexcom_csv.read_egv(DEXCOM_CSV)
        profiles = build_profiles(rows_csv[0][0].isoformat())
        sensors = _sensors()
        plan = [(0, "roy", "ideal"), (1, "uva", "breton"), (2, "csv", "ideal")]
        persons_by_key = {"roy": profiles[0], "uva": profiles[1], "csv": profiles[2]}
        ctx.push_layout(_Asserts(), plan, persons_by_key, sensors)
        ctx.set_speed_settle(ctx.cfg_session(), args.speed)
        ctx_info = {
            "minutes": args.minutes,
            "speed": args.speed,
            "profiles": profiles,
        }

        readback = _Asserts()

        def read_configs() -> dict:
            out = {}
            for s in range(SLOTS):
                with contextlib.suppress(Exception):
                    out[s] = ctx.slot_config(ctx.cfg_session(), s, readback)
            return out

        state["readback_start"] = read_configs()
        print("    layout pushed:", state["readback_start"])
        # the readback moved the cursor around; park it, and give the run a clean start
        ctx.cfg_session().queue_write("sensor_select", protocol.encode_sensor_select(0))
        pump(500)

        start = time.monotonic()
        end = start + args.minutes * 60
        scale = args.minutes / 120.0
        todo = sorted(EVENT_PLAN)
        next_sample = start
        next_report = start + 600
        fresh_n = fresh_ok = 0
        while time.monotonic() < end:
            now = time.monotonic()
            state["elapsed_min"] = (now - start) / 60
            if todo and (now - start) >= todo[0][0] * 60 * scale:
                _, slot, kind, ev_args = todo.pop(0)
                line = ctx.slot_line(slot)
                if line:
                    ev = Instant(slot, kind, ev_args, line["t_sim"])
                    state["instants"].append(ev)
                    state["checks"].append(EventCheck(ev, line, now))
                    issue(ctx, ev)
                    print(f"    t+{state['elapsed_min']:.1f} min: {kind} {ev_args} -> slot {slot}")
            for chk in state["checks"]:
                chk.poll(ctx.slot_line(chk.ev.slot, within=60), now)
            if now >= next_sample:
                next_sample = now + 30
                fresh_n += 1
                fresh_ok += ctx.serial.fresh(10.0)
                state["serial_fresh_pct"] = 100.0 * fresh_ok / fresh_n
                for s in range(SLOTS):
                    ln = ctx.slot_line(s, within=60)
                    if ln:
                        rows.writerow(
                            [
                                round(now - start, 1),
                                s,
                                ln["t_sim"],
                                ln["glucose"],
                                ln["reading"],
                                ln["carbs"],
                                ln["ex"],
                                ln["pisa"],
                            ]
                        )
            if now >= next_report:
                next_report = now + 600
                write_report(run_dir, ctx_info, state, final=False)
            QTest.qWait(500)

        state["readback_end"] = read_configs()
        state["elapsed_min"] = (time.monotonic() - start) / 60
        for chk in state["checks"]:
            chk.poll(ctx.slot_line(chk.ev.slot, within=60), time.monotonic() + 1e6)
        write_report(run_dir, ctx_info, state, final=True)
        print(f"report: {run_dir / 'REPORT.md'}")
    finally:
        with contextlib.suppress(Exception):
            ctx.stop_sessions()
        with contextlib.suppress(Exception):
            tap.stop()
        userdata_guard.restore(saved)
        (run_dir / "ble_summary.json").write_text(
            json.dumps({str(k): len(v) for k, v in ble.items()}), encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
