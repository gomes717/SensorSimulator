"""Overnight functional cycles on the 3-sensor build, through the real app path.

Every cycle sends every command the app has and checks what comes back:

  send                                  check
  person / sensor / data source         read back per slot (sensor-select cursor)
  scheduled food + exercise events      read the board's event lists back
  CSV upload (a different Dexcom file    the board plays the CSV it was sent; the
    and 24 h window each cycle)           food log shows up as carbs; BLE == board
  speed, run state, cgms_only            read back; stop / pause / resume behave
  instant food / exercise / PISA         the effect shows on the right slot
  (a sensor link dropped and re-opened)  it streams again, its config is intact

and for the whole cycle: every BLE value is one the board pushed, each sensor is
complete, and every slot follows the app's own host model (models.engine).
Models, sensors, the CSV file and the CSV slot rotate between cycles.

    uv run python scripts/e2e_overnight_3sensor.py --until 12:00

Output: test-artifacts/overnight3-<stamp>/ (SUMMARY.md after every cycle, one
folder per cycle with its REPORT.md, serial.log for the whole night). Run with
the app closed and the 3-sensor firmware flashed. data/ is restored at the end.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "scripts"))

import e2e_long_3sensor as lg
import userdata_guard
from ble_overnight import deadline_from, keep_awake, minutes_left
from e2e import SERIAL_PORT, SerialTap, Stream, Tee, pump
from e2e_4sensor import FourCtx, _sensors
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

import gui.main_window as mw
from api import protocol
from models import cambridge, deichmann, dexcom_csv, royparker, uva_padova
from models.engine import load_csv_window
from models.types import ExerciseEvent, FoodEvent, ModelId, PersonProfile

SLOTS = 3

# Pass criteria, fixed before the night. Parity is looser than the 2 h run's 2.0 p99:
# that run showed the firmware applies a scheduled exercise window ~2 ticks earlier than
# the host model, worth up to ~3 mg/dL at the window edges and nowhere else.
PARITY_P99_MAX = 3.5  # floor of the parity limit, mg/dL (see parity_limit)
PARITY_SLACK_TICKS = 2  # the characterised firmware/host schedule phase offset
FOODLOG_MIN_HIT = 0.95
RECONNECT_BUDGET_S = 60.0

MODEL_POOL = [
    ("Cambridge", ModelId.CAMBRIDGE, cambridge.default_params),
    ("UVA", ModelId.UVA_PADOVA, uva_padova.default_params),
    ("RoyParker", ModelId.ROYPARKER, royparker.default_params),
    ("Deichmann", ModelId.DEICHMANN, deichmann.default_params),
]


@dataclass
class Spec:
    cycle: int
    speed: float
    minutes: float
    profiles: dict[int, PersonProfile]
    sensor_key: dict[int, str]
    csv_slot: int
    csv_file: str
    window_start: str
    events: list[tuple[float, int, str, tuple]]  # (minute of the planned 35, slot, kind, args)
    meals: dict[int, list[int]]  # model slot -> scheduled meal minutes


def parity_limit(
    prof: PersonProfile, instants: list[lg.Instant], speed: float, host_range: float
) -> tuple[float, float]:
    """(limit in mg/dL, quantum as a fraction): how far the board may sit from the host.

    Meals and exercise are delivered per tick, and a tick is speed/60 simulated
    minutes. The firmware and the host model decide differently whether the last tick
    of a window is inside it, so they deliver up to one tick's worth more or less than
    each other (measured: a 15-minute 50 g meal at x40 is 22 ticks / 48.9 g on the board
    and 23 ticks / 51.1 g on the host, 4.4%). That is a discretisation difference, not a
    wrong model, and it scales with the tick: one tick of the shortest window as a
    fraction of the glucose excursion. The limit never drops below PARITY_P99_MAX."""
    durations = [e.duration_min for e in prof.food_events if e.duration_min > 0]
    durations += [e.duration_min for e in prof.exercise_events if e.duration_min > 0]
    durations += [e.args[0] for e in instants if e.kind in ("food", "exercise")]
    quantum = (speed / 60.0) / min(durations) if durations else 0.0
    return max(PARITY_P99_MAX, quantum * host_range), quantum


def make_spec(n: int, minutes: float, speed: float) -> Spec:
    csv_slot = n % SLOTS
    a, b = (s for s in range(SLOTS) if s != csv_slot)
    (name_a, mid_a, params_a) = MODEL_POOL[(2 * n) % 4]
    (name_b, mid_b, params_b) = MODEL_POOL[(2 * n + 1) % 4]
    ideal_first = n % 2 == 0
    sensor_key = {a: "ideal" if ideal_first else "breton", csv_slot: "ideal"}
    sensor_key[b] = "breton" if ideal_first else "ideal"

    pa = PersonProfile(f"O{n}-{name_a}", mid_a, params_a())
    pa.food_events = [FoodEvent(m, 50.0, 15) for m in (60, 360, 720, 1080)]
    pa.exercise_events = [ExerciseEvent(480, 30, 30.0), ExerciseEvent(900, 30, 30.0)]
    pb = PersonProfile(f"O{n}-{name_b}", mid_b, params_b())
    pb.food_events = [FoodEvent(m, 40.0, 30) for m in (120, 600, 1000)]
    pb.exercise_events = [ExerciseEvent(540, 40, 30.0)]

    idx = n % 16 + 1
    path = f"dataset/Dexcom_{idx:03d}.csv"
    rows = dexcom_csv.read_egv(path)
    days = max(1, (rows[-1][0] - rows[0][0]).days - 1)
    start = rows[0][0] + timedelta(days=(3 * n) % days)
    pc = PersonProfile(f"O{n}-CSV{idx:03d}", ModelId.CAMBRIDGE, cambridge.default_params())
    pc.data_source = "csv"
    pc.csv_path = path
    pc.csv_window_start_iso = start.isoformat()

    events = [
        (4, a, "food", (45, 60.0)),
        (9, b, "exercise", (30, 30.0)),
        (14, a, "pisa", (12, 0.45)),
        (19, b, "food", (30, 40.0)),
        (24, a, "exercise", (30, 30.0)),
        (29, b, "pisa", (12, 0.45)),
    ]
    return Spec(
        n,
        speed,
        minutes,
        {a: pa, b: pb, csv_slot: pc},
        sensor_key,
        csv_slot,
        path,
        start.isoformat(),
        events,
        {a: [60, 360, 720, 1080], b: [120, 600, 1000]},
    )


# ---- small helpers over the real session API ---------------------------------


def read_char(sess, key: str, timeout: float = 4.0) -> bytes | None:
    """One characteristic read, without leaving a handler behind on the session."""
    box: dict[str, bytes] = {}

    def on_read(_addr: str, k: str, data: bytes) -> None:
        box[k] = data

    sess.config_read.connect(on_read)
    try:
        sess.request_read(key)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and key not in box:
            QTest.qWait(50)
    finally:
        with contextlib.suppress(TypeError):
            sess.config_read.disconnect(on_read)
    return box.get(key)


def select_slot(sess, slot: int) -> bool:
    for _ in range(30):
        sess.queue_write("sensor_select", protocol.encode_sensor_select(slot))
        pump(250)
        data = read_char(sess, "sensor_select")
        if data is not None and protocol.decode_sensor_select(data) == slot:
            return True
    return False


def close(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= tol * max(1.0, abs(b))


class Shared:
    """State the live message handler and the cycles both touch."""

    def __init__(self) -> None:
        self.ble: dict[int, list[tuple[float, float]]] = {i: [] for i in range(SLOTS)}
        self.addr_slot: dict[str, int] = {}
        self.silent: list[tuple[int, int, bool]] = []  # (t, sensor number, silent)
        self.hooked: set[int] = set()


def connect_all(ctx: FourCtx, shared: Shared, t0: float) -> None:
    for name, addr in ctx.scan().items():
        shared.addr_slot[addr] = int(name.rsplit(" ", 1)[-1]) - 1
    for slot in range(SLOTS):
        for attempt in range(4):
            try:
                sess = ctx.slot_session(slot)
            except Exception as exc:  # a skipped connect: back off and retry from scratch
                print(f"    sensor {slot + 1} connect attempt {attempt + 1} failed: {exc}")
                ctx.drop_session(slot)
                pump(5000)
                continue
            if id(sess) not in shared.hooked:
                shared.hooked.add(id(sess))
                sess.link_silent.connect(
                    lambda _a, s, n=slot: shared.silent.append(
                        (round(time.monotonic() - t0), n + 1, s)
                    )
                )
            break
        else:
            raise RuntimeError(f"could not connect sensor {slot + 1}")


# ---- one cycle -----------------------------------------------------------------


def sweep(ctx: FourCtx, spec: Spec, sensors: dict) -> list[tuple[str, bool, str]]:
    """Read back, per slot, everything the layout push sent."""
    out: list[tuple[str, bool, str]] = []
    sess = ctx.cfg_session()
    for slot in range(SLOTS):
        prof = spec.profiles[slot]
        sensor = sensors[spec.sensor_key[slot]]
        if not select_slot(sess, slot):
            out.append((f"slot {slot} cursor", False, "sensor_select never reached it"))
            continue
        pm = protocol.decode_person_config(read_char(sess, "person") or b"")
        ok = pm is not None and pm[0] == prof.model_id
        if ok:
            ok = all(close(v, prof.params.get(k, v), 1e-3) for k, v in pm[1].items())
        out.append((f"slot {slot} person/params", ok, f"{pm and pm[0].name}"))
        sm = protocol.decode_sensor_config(read_char(sess, "sensor") or b"")
        out.append((f"slot {slot} sensor", sm is not None and sm[0] == sensor.sensor_id, f"{sm}"))
        ds = protocol.decode_data_source(read_char(sess, "data_source") or b"")
        is_csv = getattr(prof, "data_source", "model") == "csv"
        out.append((f"slot {slot} data source", ds == is_csv, f"board={ds} sent={is_csv}"))
        fl = protocol.decode_food_events(read_char(sess, "food_list") or b"")
        want = sorted(
            (e.time_of_day_min, e.duration_min, round(e.carbs_g, 1)) for e in prof.food_events
        )
        got = sorted((e.time_of_day_min, e.duration_min, round(e.carbs_g, 1)) for e in fl)
        out.append((f"slot {slot} food events", got == want, f"{len(got)} of {len(want)}"))
        xl = protocol.decode_exercise_events(read_char(sess, "exercise_list") or b"")
        want_x = sorted(
            (e.time_of_day_min, e.duration_min, round(e.intensity_pct, 1))
            for e in prof.exercise_events
        )
        got_x = sorted((e.time_of_day_min, e.duration_min, round(e.intensity_pct, 1)) for e in xl)
        out.append(
            (f"slot {slot} exercise events", got_x == want_x, f"{len(got_x)} of {len(want_x)}")
        )
    return out


def ble_since(shared: Shared, slot: int, t: float) -> int:
    return sum(1 for ts, _ in shared.ble[slot] if ts >= t)


def wait_for_ble(
    shared: Shared, slots: list[int], since: float, t0: float, budget: float
) -> float | None:
    """Seconds until every slot in *slots* has delivered a value after *since*."""
    deadline = time.monotonic() + budget
    while time.monotonic() < deadline:
        if all(ble_since(shared, s, since) > 0 for s in slots):
            return time.monotonic() - t0 - since
        QTest.qWait(250)
    return None


def profile_roundtrip(ctx: FourCtx, shared: Shared, t0: float) -> list[tuple[str, bool, str]]:
    """Switch the board to the Dexcom profile and back, checking each stream.

    The profile is persisted on the board and the board drops its links to
    re-advertise, so every session is reopened afterwards. Whatever happens, it
    ends by trying to put the SIG CGMS profile back."""
    out: list[tuple[str, bool, str]] = []
    try:
        for dexcom in (True, False):
            label = "Dexcom" if dexcom else "SIG CGMS"
            ctx.cfg_session().queue_write("comm_profile", protocol.encode_comm_profile(dexcom))
            pump(4000)
            for slot in range(SLOTS):
                ctx.drop_session(slot)
            pump(10000)
            connect_all(ctx, shared, t0)
            mark = time.monotonic() - t0
            took = wait_for_ble(shared, list(range(SLOTS)), mark, t0, 60.0)
            out.append((f"{label} profile streams on every sensor", took is not None, f"{took}"))
            back = protocol.decode_comm_profile(read_char(ctx.cfg_session(), "comm_profile") or b"")
            out.append((f"{label} profile read back", back == dexcom, f"board={back}"))
    except Exception as exc:  # reported, and the finally below still restores SIG
        out.append(("comm_profile round trip", False, f"{type(exc).__name__}: {exc}"))
    finally:
        with contextlib.suppress(Exception):
            ensure_sig_profile(ctx, shared, t0)
    return out


def ensure_sig_profile(ctx: FourCtx, shared: Shared, t0: float) -> None:
    """The profile survives resets, so never start a cycle on a leftover Dexcom one."""
    back = protocol.decode_comm_profile(read_char(ctx.cfg_session(), "comm_profile") or b"")
    if back is not True:
        return
    ctx.cfg_session().queue_write("comm_profile", protocol.encode_comm_profile(False))
    pump(4000)
    for slot in range(SLOTS):
        ctx.drop_session(slot)
    pump(10000)
    connect_all(ctx, shared, t0)


def run_cycle(
    ctx: FourCtx, spec: Spec, shared: Shared, cdir: Path, t0: float, profile_every: int = 0
) -> dict:
    def rel() -> float:
        return time.monotonic() - t0

    checks: list[tuple[str, bool, str]] = []
    sensors = _sensors()
    keys = {s: f"s{s}" for s in range(SLOTS)}
    persons = {keys[s]: spec.profiles[s] for s in range(SLOTS)}
    plan = [(s, keys[s], spec.sensor_key[s]) for s in range(SLOTS)]

    silent_start = len(shared.silent)
    connect_all(ctx, shared, t0)
    ensure_sig_profile(ctx, shared, t0)
    ctx.push_layout(lg._Asserts(), plan, persons, sensors)  # person/sensor/source/events/CSV
    checks += sweep(ctx, spec, sensors)

    sess = ctx.cfg_session()
    for flag in (True, False):  # cgms_only round trip, ends in the normal state
        sess.queue_write("cgms_only", protocol.encode_cgms_only(flag))
        pump(800)
        back = protocol.decode_cgms_only(read_char(sess, "cgms_only") or b"")
        checks.append((f"cgms_only={flag} read back", back == flag, f"board={back}"))

    ctx.set_speed_settle(sess, spec.speed)
    sp = protocol.decode_speed(read_char(sess, "speed") or b"")
    checks.append(("speed read back", sp is not None and abs(sp - spec.speed) < 0.5, f"{sp}"))
    rs = protocol.decode_run_state(read_char(sess, "run_state") or b"")
    checks.append(("run state read back RUNNING", rs == protocol.RUN_STATE_RUNNING, f"{rs}"))

    # ---- the measured run -------------------------------------------------
    serial_log = ctx.run_dir / "serial.log"
    offset = serial_log.stat().st_size
    t_start = rel()
    start = time.monotonic()
    end = start + spec.minutes * 60
    scale = spec.minutes / 35.0
    todo = sorted(spec.events)
    instants: list[lg.Instant] = []
    stale_since = None
    stale_s = 0.0
    print(f"    cycle {spec.cycle}: running {spec.minutes:g} min at x{spec.speed:g}")
    while time.monotonic() < end:
        now = time.monotonic()
        if todo and (now - start) >= todo[0][0] * 60 * scale:
            _, slot, kind, args = todo.pop(0)
            line = ctx.slot_line(slot)
            if line:
                ev = lg.Instant(slot, kind, args, line["t_sim"])
                instants.append(ev)
                lg.issue(ctx, ev)
        if ctx.serial.fresh(15.0):
            stale_since = None
        else:
            stale_since = stale_since or now
            stale_s = max(stale_s, now - stale_since)
        QTest.qWait(500)
    t_end = rel()

    # ---- run-state behaviour (after the measured window) -------------------
    a = next(s for s in range(SLOTS) if s != spec.csv_slot)
    silent_before = len(shared.silent)
    sess.queue_write("run_state", protocol.encode_run_state(protocol.RUN_STATE_PAUSED))
    pump(3500)
    held1 = ctx.slot_line(a)
    pump(6000)
    held2 = ctx.slot_line(a)
    checks.append(
        (
            "PAUSED holds the sim clock",
            bool(held1 and held2 and abs(held2["t_sim"] - held1["t_sim"]) < 0.5),
            f"{held1 and held1['t_sim']} -> {held2 and held2['t_sim']}",
        )
    )
    sess.queue_write("run_state", protocol.encode_run_state(protocol.RUN_STATE_RUNNING))
    pump(6000)
    resumed = ctx.slot_line(a)
    checks.append(
        (
            "RUNNING resumes from the held clock (no reset)",
            bool(held2 and resumed and resumed["t_sim"] >= held2["t_sim"] - 0.05),
            f"{held2 and held2['t_sim']} -> {resumed and resumed['t_sim']}",
        )
    )
    sess.queue_write("run_state", protocol.encode_run_state(protocol.RUN_STATE_STOPPED))
    pump(25000)  # longer than the watchdog's limit: a stopped board must not read as silent
    checks.append(
        (
            "a stopped board is not reported as a silent link",
            len(shared.silent) == silent_before,
            f"{shared.silent[silent_before:]}",
        )
    )
    restart = rel()
    sess.queue_write("run_state", protocol.encode_run_state(protocol.RUN_STATE_RUNNING))
    took = wait_for_ble(shared, list(range(SLOTS)), restart, t0, 40.0)
    back = ctx.slot_line(a)
    checks.append(("START after STOP streams on every sensor", took is not None, f"{took}"))
    checks.append(
        (
            "STOP reset the model (clock restarted)",
            bool(back and back["t_sim"] < 60.0),
            f"t_sim={back and back['t_sim']}",
        )
    )

    # ---- a sensor link dropped and re-opened ------------------------------
    r = spec.cycle % SLOTS
    ctx.drop_session(r)
    pump(6000)
    dropped = rel()
    ok_conn = True
    try:
        connect_all(ctx, shared, t0)
    except RuntimeError as exc:
        ok_conn = False
        checks.append((f"sensor {r + 1} reconnect", False, str(exc)))
    if ok_conn:
        took = wait_for_ble(shared, [r], dropped, t0, RECONNECT_BUDGET_S)
        checks.append(
            (f"sensor {r + 1} streams again after a dropped link", took is not None, f"{took}")
        )

    if profile_every and spec.cycle % profile_every == 0:
        checks += profile_roundtrip(ctx, shared, t0)
    raised = [e for e in shared.silent[silent_start:] if e[2]]
    checks.append(("no link_silent raised during the cycle", not raised, f"{raised or 'none'}"))
    ble_slice = {
        s: [(t, v) for t, v in shared.ble[s] if t_start <= t <= t_end] for s in range(SLOTS)
    }
    save_cycle_state(cdir, spec, offset, t_start, t_end, instants, ble_slice, checks, stale_s)
    result = analyse(
        spec,
        ble_slice,
        ctx.run_dir / "serial.log",
        offset,
        t_start,
        t_end,
        instants,
        checks,
        stale_s,
    )
    write_cycle_report(cdir, spec, result)
    return result


def save_cycle_state(
    cdir: Path,
    spec: Spec,
    offset: int,
    t_start: float,
    t_end: float,
    instants: list[lg.Instant],
    ble: dict[int, list[tuple[float, float]]],
    live_checks: list[tuple[str, bool, str]],
    stale_s: float,
) -> None:
    """Everything analyse() needs besides the serial log, so a cycle can be re-scored."""
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "state.json").write_text(
        json.dumps(
            {
                "cycle": spec.cycle,
                "minutes": spec.minutes,
                "speed": spec.speed,
                "offset": offset,
                "t_start": t_start,
                "t_end": t_end,
                "stale_s": stale_s,
                "instants": [
                    {"slot": e.slot, "kind": e.kind, "args": list(e.args), "t_sim": e.t_sim}
                    for e in instants
                ],
                "ble": {str(k): v for k, v in ble.items()},
                "live_checks": [list(c) for c in live_checks],
            }
        ),
        encoding="utf-8",
    )


def rescore(run_dir: Path) -> Path:
    """Re-analyse every saved cycle of a finished night with the current rules."""
    results = []
    for cdir in sorted(run_dir.glob("cycle-*")):
        state_file = cdir / "state.json"
        if not state_file.exists():
            continue
        st = json.loads(state_file.read_text(encoding="utf-8"))
        spec = make_spec(st["cycle"], st["minutes"], st["speed"])
        instants = [
            lg.Instant(e["slot"], e["kind"], tuple(e["args"]), e["t_sim"]) for e in st["instants"]
        ]
        result = analyse(
            spec,
            {int(k): [tuple(x) for x in v] for k, v in st["ble"].items()},
            run_dir / "serial.log",
            st["offset"],
            st["t_start"],
            st["t_end"],
            instants,
            [tuple(c) for c in st["live_checks"]],
            st["stale_s"],
        )
        result["cycle_ok"] = not result["failed"]
        write_cycle_report(cdir, spec, result, name="REPORT.rescored.md")
        results.append(result)
    now = datetime.now()
    write_summary(run_dir, results, now, now, name="SUMMARY.rescored.md")
    return run_dir / "SUMMARY.rescored.md"


def analyse(
    spec: Spec,
    ble: dict[int, list[tuple[float, float]]],
    serial_log: Path,
    offset: int,
    t_start: float,
    t_end: float,
    instants: list[lg.Instant],
    checks: list[tuple[str, bool, str]],
    stale_s: float,
) -> dict:
    ticks = {
        s: [t for t in v if t.t <= t_end] for s, v in lg.read_ticks(serial_log, offset).items()
    }
    pushes = lg.read_pushes(serial_log, offset)
    for slot in range(SLOTS):
        prof = spec.profiles[slot]
        times = [(t, v) for t, v in ble[slot] if t_start <= t <= t_end]
        n, expected, share, gap = lg.completeness([t for t, _ in times])
        matched, total, bad = lg.ble_match(times, pushes.get(slot, []))
        checks.append(
            (
                f"sensor {slot + 1} complete",
                share >= lg.MIN_COMPLETENESS and gap <= lg.MAX_GAP_S,
                f"{n}/{expected:.0f} ({share:.1%}), worst gap {gap:.1f}s",
            )
        )
        checks.append(
            (
                f"sensor {slot + 1} BLE == board push",
                total > 0 and matched / total >= lg.MIN_BLE_MATCH,
                f"{matched}/{total}" + (f" unmatched {bad}" if bad else ""),
            )
        )
        tk = ticks.get(slot, [])
        mine = [e for e in instants if e.slot == slot]
        # A replayed CSV steps by several mg/dL every 5 minutes, so a one-tick phase
        # offset reads as a big error; the CSV slot is judged by csv_tracking below.
        p = {} if slot == spec.csv_slot else lg.parity(prof, tk, mine, PARITY_SLACK_TICKS)
        if slot == spec.csv_slot:
            pass
        elif p.get("n"):
            limit, quantum = parity_limit(prof, mine, spec.speed, p["host_range"])
            checks.append(
                (
                    f"slot {slot} follows the host model ({prof.name})",
                    p["p99"] <= limit,
                    f"within +-{PARITY_SLACK_TICKS} ticks: mean {p['mean']:.3f} p99 {p['p99']:.2f}"
                    f" max {p['max']:.2f}; strict same-tick: mean {p['strict_mean']:.3f}"
                    f" p99 {p['strict_p99']:.2f} max {p['strict_max']:.2f} over {p['n']} ticks;"
                    f" limit {limit:.1f} = one tick of the shortest window ({quantum:.1%}) of the"
                    f" {p['host_range']:.0f} mg/dL excursion",
                )
            )
        else:
            checks.append((f"slot {slot} follows the host model", False, "no console ticks"))
        for ev in mine:
            chk = lg.replay_check(ev, tk)
            checks.append(
                (
                    f"{ev.kind} {ev.args} on slot {slot}",
                    chk.result == "PASS",
                    f"{chk.result} {chk.seen}",
                )
            )
        for meal, rise in lg.meal_rises(tk, spec.meals.get(slot, [])):
            checks.append(
                (
                    f"slot {slot} meal @{meal} raises glucose",
                    rise >= lg.MIN_MEAL_RISE,
                    f"+{rise:.1f}",
                )
            )

    c = spec.csv_slot
    cp = spec.profiles[c]
    ct = lg.csv_tracking(cp, ticks.get(c, []))
    checks.append(
        (
            f"CSV slot {c} plays the CSV it was sent "
            f"({Path(spec.csv_file).name}, {spec.window_start[:10]})",
            ct["n"] > 0 and ct["ok"] / ct["n"] >= 0.99,
            f"{ct['ok']}/{ct['n']} within {lg.CSV_TOL:g} mg/dL, worst {ct['worst']:.1f}",
        )
    )
    _samples, _interval, foodlog = load_csv_window(cp)
    run = lg.last_run(ticks.get(c, []))
    reached = run[-1].t_sim if run else 0.0
    inside = [(off / 60.0, g) for off, g in foodlog if off / 60.0 + 5 < reached]
    hits = sum(any(m <= tk.t_sim <= m + 60 and tk.carbs > 0.001 for tk in run) for m, _ in inside)
    checks.append(
        (
            "CSV food log shows up as carbs",
            (not inside) or hits / len(inside) >= FOODLOG_MIN_HIT,
            f"{hits}/{len(inside)} meals" if inside else "no meals in this window",
        )
    )
    checks.append(("console stayed alive", stale_s < 60.0, f"longest stall {stale_s:.0f}s"))
    failed = [name for name, ok, _ in checks if not ok]
    return {"cycle": spec.cycle, "checks": checks, "failed": failed}


def write_cycle_report(cdir: Path, spec: Spec, result: dict, name: str = "REPORT.md") -> None:
    cdir.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# Cycle {spec.cycle}",
        "",
        f"CSV slot {spec.csv_slot}: {spec.csv_file}, window from {spec.window_start}",
        "models: "
        + ", ".join(
            f"slot {s} {p.name} ({spec.sensor_key[s]})" for s, p in sorted(spec.profiles.items())
        ),
        f"speed x{spec.speed:g}, {spec.minutes:g} min",
        "",
    ]
    for check_name, ok, detail in result["checks"]:
        lines.append(f"- {'PASS' if ok else 'FAIL'}  {check_name}: {detail}")
    lines += [
        "",
        f"## {'PASS' if not result['failed'] else 'FAIL'}: "
        + (", ".join(result["failed"]) or "all checks"),
    ]
    (cdir / name).write_text("\n".join(lines), encoding="utf-8")


def write_summary(
    run_dir: Path,
    results: list[dict],
    started: datetime,
    until: datetime,
    name: str = "SUMMARY.md",
) -> None:
    ok = [r for r in results if r["cycle_ok"]]
    lines = [
        "# Overnight 3-sensor run",
        "",
        f"started {started:%Y-%m-%d %H:%M}, until {until:%Y-%m-%d %H:%M}, "
        f"summary written {datetime.now():%Y-%m-%d %H:%M}",
        "",
        f"cycles: {len(results)} run, {len(ok)} fully passing, "
        f"{sum(1 for r in results if r.get('error'))} aborted by an error",
        "",
        "| cycle | result | failed checks |",
        "|---|---|---|",
    ]
    for r in results:
        what = r.get("error") or ", ".join(r.get("failed", [])) or "-"
        lines.append(f"| {r['cycle']} | {'PASS' if r['cycle_ok'] else 'FAIL'} | {what} |")
    tally: dict[str, int] = {}
    for r in results:
        for failed_check in r.get("failed", []):
            key = failed_check.split(" (")[0]
            tally[key] = tally.get(key, 0) + 1
    if tally:
        lines += ["", "## Failing checks, by how many cycles", ""]
        lines += [f"- {n}x {k}" for k, n in sorted(tally.items(), key=lambda kv: -kv[1])]
    (run_dir / name).write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--until", default="12:00", help="stop at this local time (default 12:00)")
    ap.add_argument("--cycle-minutes", type=float, default=35.0, help="measured run per cycle")
    ap.add_argument(
        "--speed", type=float, default=40.0, help="x40 plays a 24 h CSV window in 36 min"
    )
    ap.add_argument("--cycles", type=int, default=0, help="stop after this many (0: until --until)")
    ap.add_argument(
        "--comm-profile-every",
        type=int,
        default=0,
        help="also switch the comm profile to Dexcom and back every N-th cycle (0: never)",
    )
    ap.add_argument(
        "--rescore", metavar="RUN_DIR", help="re-analyse a finished night with the current rules"
    )
    ap.add_argument(
        "--profile-trial",
        action="store_true",
        help="only run the comm_profile round trip once and print the result",
    )
    ap.add_argument("--board", default="D0:3F:4D:E2:7C:9B")
    ap.add_argument("--out", default=str(_ROOT / "test-artifacts"))
    args = ap.parse_args()
    if args.rescore:
        print(rescore(Path(args.rescore)))
        return 0

    keep_awake()
    saved = userdata_guard.snapshot()
    started = datetime.now()
    deadline = deadline_from(args.until)
    run_id = datetime.now(UTC).strftime("%Y%m%d-%H%M%SZ")
    run_dir = Path(args.out) / f"overnight3-{run_id}"
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
        print("no serial console on", SERIAL_PORT)
        userdata_guard.restore(saved)
        return 1

    app = QApplication.instance() or QApplication(sys.argv)  # noqa: F841
    w = mw.MainWindow()
    w.show()
    pump(400)
    ctx = FourCtx(w, run_dir, t0, streams, args.board, True)
    shared = Shared()

    def on_message(m: dict) -> None:
        g = m.get("glucose_value")
        slot = shared.addr_slot.get(m.get("dev_id", ""))
        if g is not None and slot is not None:
            shared.ble[slot].append((time.monotonic() - t0, float(g)))

    w._ble_log.new_message.connect(on_message)

    results: list[dict] = []
    failures_in_a_row = 0
    n = 0
    try:
        if args.profile_trial:
            connect_all(ctx, shared, t0)
            for name, ok, detail in profile_roundtrip(ctx, shared, t0):
                print(f"  {'PASS' if ok else 'FAIL'}  {name}: {detail}")
            return 0
        while (
            minutes_left(deadline) > args.cycle_minutes + 12
            and (not args.cycles or n < args.cycles)
            and not (run_dir / "STOP").exists()
        ):
            n += 1
            spec = make_spec(n, args.cycle_minutes, args.speed)
            print(f"== cycle {n} ({minutes_left(deadline):.0f} min to the deadline)")
            try:
                res = run_cycle(
                    ctx, spec, shared, run_dir / f"cycle-{n:02d}", t0, args.comm_profile_every
                )
                res["cycle_ok"] = not res["failed"]
                failures_in_a_row = 0
            except Exception as exc:  # the cycle is lost, the night is not
                print(f"    cycle {n} aborted: {type(exc).__name__}: {exc}")
                res = {
                    "cycle": n,
                    "cycle_ok": False,
                    "failed": [],
                    "error": f"{type(exc).__name__}: {exc}",
                }
                failures_in_a_row += 1
                for slot in range(SLOTS):
                    ctx.drop_session(slot)
                pump(8000)
                if failures_in_a_row >= 3:
                    with contextlib.suppress(Exception):
                        ctx.jlink_reset()
                    pump(25000)
                if failures_in_a_row >= 8:
                    print("    eight cycles in a row failed: stopping")
                    results.append(res)
                    break
            results.append(res)
            write_summary(run_dir, results, started, deadline)
        write_summary(run_dir, results, started, deadline)
        print(f"summary: {run_dir / 'SUMMARY.md'}")
    finally:
        with contextlib.suppress(Exception):
            ctx.stop_sessions()
        with contextlib.suppress(Exception):
            tap.stop()
        userdata_guard.restore(saved)
        (run_dir / "done").write_text(datetime.now().isoformat(), encoding="utf-8")
        (run_dir / "ble_counts.json").write_text(
            json.dumps({str(k): len(v) for k, v in shared.ble.items()}), encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
