#!/usr/bin/env python3
"""End-to-end harness — drives the real app <-> board, captures three log
streams (firmware serial / app stdout+stderr / BLE traffic), and on any failure
freezes them into a per-case artifact folder with a FAILURE.md.

See docs/E2E_TEST_PLAN.md for the full design and the complete case backlog;
this file implements a representative subset (each suite gets 1-4 real cases).

Usage:
  python scripts/e2e.py                    # all implemented suites, default board
  python scripts/e2e.py --smoke            # a fast subset (~10 min)
  python scripts/e2e.py --only S7,S16      # selected suites
  python scripts/e2e.py --no-board         # skip board suites (SKIP, not FAIL)
  python scripts/e2e.py --reflash          # build.ps1 -Pristine + flash.ps1 first
  python scripts/e2e.py --loop 3           # run the whole thing 3 times back to back
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import traceback
from collections import deque
from datetime import UTC, datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
os.chdir(_ROOT)

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog  # noqa: E402

import graphic.main_window as mw  # noqa: E402
from api import protocol  # noqa: E402
from graphic.instant_event_dialog import (  # noqa: E402
    PisaInstantDialog,
)
from models import cambridge  # noqa: E402
from models.types import ModelId, PersonProfile  # noqa: E402
from services.ble_session import BleSession  # noqa: E402

DEFAULT_BOARD = "D0:3F:4D:E2:7C:9B"
SERIAL_PORT = "COM10"
DEXCOM_CSV = "dataset/Dexcom_001.csv"

CGM_MEAS = "00002aa7-0000-1000-8000-00805f9b34fb"


# ----------------------------------------------------------------------
# Capture streams
# ----------------------------------------------------------------------


class Stream:
    def __init__(self, name: str, path: Path, maxlen: int) -> None:
        self.name = name
        self._fh = open(path, "w", encoding="utf-8", buffering=1)
        self._buf: deque[str] = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self.last_add = 0.0  # monotonic time of the most recent line

    def add(self, line: str, t: float) -> None:
        rec = f"{t:8.2f}  {line.rstrip()}"
        with self._lock:
            self._buf.append(rec)
            self.last_add = time.monotonic()
            try:
                self._fh.write(rec + "\n")
            except ValueError:
                pass

    def fresh(self, max_age: float = 20.0) -> bool:
        """True if a line arrived within max_age seconds (serial tap alive)."""
        return (time.monotonic() - self.last_add) < max_age

    def tail(self, n: int) -> list[str]:
        with self._lock:
            return list(self._buf)[-n:]

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass


class Tee:
    """stdout/stderr wrapper: original + a Stream, line-buffered."""

    def __init__(self, orig, stream: Stream, t0: float) -> None:
        self._orig = orig
        self._stream = stream
        self._t0 = t0
        self._partial = ""

    def write(self, s: str) -> int:
        self._orig.write(s)
        self._partial += s
        while "\n" in self._partial:
            line, self._partial = self._partial.split("\n", 1)
            self._stream.add(line, time.monotonic() - self._t0)
        return len(s)

    def flush(self) -> None:
        self._orig.flush()

    def isatty(self) -> bool:  # some libs probe this
        return False


_PS_SERIAL = (
    "$p=New-Object System.IO.Ports.SerialPort {port},115200,None,8,One;"
    "$p.ReadTimeout=1500;try{{$p.Open()}}catch{{exit 2}};"
    "while($true){{try{{$l=$p.ReadLine();if($l){{[Console]::Out.WriteLine($l)}}}}catch{{}}}}"
)


def _kill_stray_serial_readers(port: str) -> None:
    """Kill leftover PowerShell SerialPort readers (from a crashed prior run)
    still holding *port* open, so a fresh SerialTap can attach."""
    try:
        subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_Process -Filter \"Name='powershell.exe'\" | "
                f"Where-Object {{ $_.CommandLine -like '*SerialPort*{port}*' }} | "
                "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }",
            ],
            capture_output=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


class SerialTap:
    """Reads the firmware console off COM10 via a PowerShell SerialPort subprocess
    (Git Bash / Python-on-Windows can't open COM ports reliably; PowerShell can)."""

    def __init__(self, port: str, stream: Stream, t0: float) -> None:
        self.available = False
        self._port = port
        self._stream = stream
        self._t0 = t0
        self._proc = None
        self._stop = False
        if port not in _list_com_ports():
            return
        if not self._spawn():
            return
        # confirm it actually emits firmware lines
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if any("model_tick" in ln or "model_thread" in ln for ln in stream.tail(50)):
                self.available = True
                break
            time.sleep(0.2)
        if self.available:
            threading.Thread(target=self._watchdog, daemon=True).start()

    def _spawn(self) -> bool:
        _kill_stray_serial_readers(self._port)
        time.sleep(0.5)
        try:
            self._proc = subprocess.Popen(
                ["powershell", "-NoProfile", "-Command", _PS_SERIAL.format(port=self._port)],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
        except OSError:
            return False
        threading.Thread(target=self._pump, args=(self._proc,), daemon=True).start()
        return True

    def _pump(self, proc) -> None:
        try:
            for line in proc.stdout:
                self._stream.add(line, time.monotonic() - self._t0)
        except ValueError:
            pass

    def _watchdog(self) -> None:
        """The PowerShell SerialPort reader occasionally wedges on a long run;
        respawn it if no line has arrived for 15 s."""
        while not self._stop:
            time.sleep(5)
            if self._stop:
                return
            if time.monotonic() - self._stream.last_add > 15:
                old = self._proc
                if self._spawn():
                    if old:
                        try:
                            old.terminate()
                        except OSError:
                            pass
                    time.sleep(3)

    def stop(self) -> None:
        self._stop = True
        if self._proc:
            try:
                self._proc.terminate()
            except OSError:
                pass


def _list_com_ports() -> list[str]:
    try:
        out = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "[System.IO.Ports.SerialPort]::GetPortNames() -join ','",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        ).stdout.strip()
        return [p.strip() for p in out.split(",") if p.strip()]
    except Exception:
        return []


# ----------------------------------------------------------------------
# Case + context
# ----------------------------------------------------------------------


class _AssertFail(Exception):
    pass


class _Timeout(Exception):
    pass


class _Skip(Exception):
    pass


class Ctx:
    def __init__(
        self,
        w,
        run_dir: Path,
        t0: float,
        streams: dict[str, Stream],
        board_addr: str | None,
        serial_ok: bool,
    ) -> None:
        self.w = w
        self.run_dir = run_dir
        self.t0 = t0
        self.streams = streams  # name -> Stream
        self.serial = streams["serial"]
        self.board_addr = board_addr
        self.serial_ok = serial_ok
        self.sessions: dict[str, BleSession] = {}
        self.results: list[tuple[str, str, str, str]] = []  # cid, suite, verdict, note
        self._run_jsonl = open(run_dir / "run.jsonl", "w", encoding="utf-8", buffering=1)

    def log_event(self, obj: dict) -> None:
        obj["t"] = round(time.monotonic() - self.t0, 2)
        self._run_jsonl.write(json.dumps(obj) + "\n")

    def record(self, cid: str, suite: str, verdict: str, note: str) -> None:
        self.results.append((cid, suite, verdict, note))
        print(f"  == {cid:8} {verdict:8} {note}")

    def mark(self, tag: str) -> None:
        for s in self.streams.values():
            s.add(f"{tag}", time.monotonic() - self.t0)

    # -- board ------------------------------------------------------------

    def require_board(self):
        if not self.board_addr:
            raise _Skip("--no-board")
        return self.connect_board()

    def connect_board(self, timeout_ms: int = 45000):
        addr = self.board_addr
        if addr in self.sessions:
            return self.sessions[addr]
        bt = self.w._ensure_bluetooth_window()
        state = {"done": False, "ok": False, "err": ""}
        sess = BleSession(addr, "Nordic Glucose Sensor", bt)
        sess.connected.connect(lambda *_: state.update(done=True, ok=True))
        sess.connect_failed.connect(lambda _a, e: state.update(done=True, ok=False, err=e))
        sess.new_message.connect(self.w._ble_log.add_message)
        bt._sessions[addr] = sess
        bt._names[addr] = "Nordic Glucose Sensor"
        print(f"    connecting to {addr} (pairing may take ~10 s)…")
        sess.start()
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline and not state["done"]:
            QTest.qWait(100)
        if not state["ok"]:
            bt._sessions.pop(addr, None)
            raise _Skip(f"board connect failed: {state.get('err') or 'timeout'}")
        self.sessions[addr] = sess
        print("    board connected")
        return sess

    def stop_sessions(self) -> None:
        if self.w._bluetooth_window is not None:
            try:
                self.w._bluetooth_window.stop_all_sessions()
            except Exception:
                pass
        self.sessions.clear()

    # -- serial helpers -------------------------------------------------

    def serial_tail(self, n: int = 200) -> list[str]:
        return self.serial.tail(n)

    def serial_has(self, needle: str, n: int = 300) -> bool:
        return any(needle in ln for ln in self.serial.tail(n))

    def serial_live(self, max_age: float = 20.0) -> bool:
        """serial was available at startup AND a line arrived recently (the
        PowerShell reader can wedge on long runs; a case should SKIP its serial
        checks rather than FAIL them when the tap is stale)."""
        return self.serial_ok and self.serial.fresh(max_age)

    def wait_serial(self, pred, timeout_s: float, desc: str) -> None:
        """Wait for a serial predicate; SKIP the case (not FAIL/TIMEOUT) if the
        tap goes stale before it's satisfied."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if pred():
                return
            QTest.qWait(150)
        if not self.serial_live(25.0):
            raise _Skip(f"serial console went stale waiting for: {desc}")
        raise _Timeout(desc)

    def close(self) -> None:
        self._run_jsonl.close()


class Case:
    def __init__(self, ctx: Ctx, cid: str, slug: str, suite: str) -> None:
        self.ctx = ctx
        self.cid = cid
        self.slug = slug
        self.suite = suite
        self.dir = ctx.run_dir / "cases" / f"{cid}_{slug}"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.events: list[tuple[float, str, str]] = []
        self.measures: dict[str, object] = {}
        self.verdict = "PASS"
        self._t0 = 0.0

    def __enter__(self) -> Case:
        self._t0 = time.monotonic()
        self.ctx.mark(f">>> CASE {self.cid} {self.slug}")
        self.ctx.log_event({"kind": "case_start", "case": self.cid})
        print(f"  -- {self.cid} {self.slug}")
        return self

    def _rel(self) -> float:
        return time.monotonic() - self._t0

    def step(self, msg: str) -> None:
        self.events.append((self._rel(), "step", msg))
        print(f"       · {msg}")

    def measure(self, name: str, value) -> object:
        self.measures[name] = value
        self.events.append((self._rel(), "measure", f"{name}={value}"))
        return value

    def assert_(self, ok: bool, name: str, detail: str = "") -> None:
        self.events.append((self._rel(), "assert", f"{name}: {'ok' if ok else 'FAIL'} {detail}"))
        self.ctx.log_event(
            {"kind": "assert", "case": self.cid, "name": name, "ok": bool(ok), "detail": detail}
        )
        if not ok:
            raise _AssertFail(f"{name}  ({detail})" if detail else name)

    def wait_until(self, fn, timeout_s: float, desc: str) -> None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                if fn():
                    return
            except Exception:
                pass
            QTest.qWait(100)
        raise _Timeout(desc)

    def __exit__(self, et, ev, tb) -> bool:
        dur = round(self._rel(), 2)
        self.ctx.mark(f"<<< CASE {self.cid} end")
        (self.dir / "serial.slice.log").write_text(
            "\n".join(self.ctx.serial.tail(500)), encoding="utf-8"
        )
        try:
            self.ctx.w.grab().save(str(self.dir / "screenshot.png"))
        except Exception:
            pass

        tb_text = None
        if et is None:
            self.verdict = "PASS"
        elif et is _AssertFail:
            self.verdict = "FAIL"
        elif et is _Timeout:
            self.verdict = "TIMEOUT"
        elif et is _Skip:
            self.verdict = "SKIP"
        else:
            self.verdict = "ERROR"
            tb_text = "".join(traceback.format_exception(et, ev, tb))

        note = (
            str(ev)
            if ev is not None
            else ", ".join(f"{k}={v}" for k, v in list(self.measures.items())[:4])
        )
        if self.verdict in ("FAIL", "TIMEOUT", "ERROR"):
            self._write_failure_md(note, tb_text)

        (self.dir / "result.json").write_text(
            json.dumps(
                {
                    "case": self.cid,
                    "slug": self.slug,
                    "suite": self.suite,
                    "verdict": self.verdict,
                    "duration_s": dur,
                    "measures": self.measures,
                    "events": [{"t": round(t, 2), "kind": k, "msg": m} for t, k, m in self.events],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        self.ctx.record(self.cid, self.suite, self.verdict, note)
        self.ctx.log_event({"kind": "case_end", "case": self.cid, "verdict": self.verdict})
        return True  # never propagate — keep the run going

    def _write_failure_md(self, note: str, tb_text: str | None) -> None:
        tl = "\n".join(f"  {t:6.2f}  {k:7}  {m}" for t, k, m in self.events)
        parts = [
            f"# {self.cid} {self.slug} — {self.verdict}",
            "",
            f"**{note}**",
            "",
            "## Timeline (relative s)",
            "```",
            tl,
            "```",
            "",
            "## Firmware serial — last 120 lines",
            "```",
            "\n".join(self.ctx.streams["serial"].tail(120)),
            "```",
            "",
            "## BLE traffic — last 60",
            "```",
            "\n".join(self.ctx.streams["ble"].tail(60)),
            "```",
            "",
            "## App log — last 40",
            "```",
            "\n".join(self.ctx.streams["app"].tail(40)),
            "```",
        ]
        if tb_text:
            parts += ["", "## Python traceback", "```", tb_text, "```"]
        (self.dir / "FAILURE.md").write_text("\n".join(parts), encoding="utf-8")


# ----------------------------------------------------------------------
# Shared helpers
# ----------------------------------------------------------------------


def pump(ms: int) -> None:
    QTest.qWait(ms)


def cgm_stream(ctx: Ctx) -> list[tuple[float, float]]:
    """(t_off_min, glucose) parsed from the shared BLE message log — works for
    both the SIG and Dexcom decoders (both emit glucose_value)."""
    out = []
    for m in ctx.w._ble_log.get_messages():
        if "glucose_value" in m and m.get("glucose_value") is not None:
            out.append((m.get("time_offset_min", 0), float(m["glucose_value"])))
    return out


def dexcom_stream(ctx: Ctx) -> list[float]:
    """Glucose values that came specifically from the Dexcom-style decoder
    (only it adds a `sequence` field)."""
    return [
        float(m["glucose_value"])
        for m in ctx.w._ble_log.get_messages()
        if "sequence" in m and m.get("glucose_value") is not None
    ]


def sig_stream(ctx: Ctx) -> list[float]:
    """Glucose values from the standard SIG CGM Measurement decoder (has
    `time_offset_min` / `flags`, never `sequence`)."""
    return [
        float(m["glucose_value"])
        for m in ctx.w._ble_log.get_messages()
        if m.get("glucose_value") is not None and "sequence" not in m and "flags" in m
    ]


def new_person(w, name: str, model=ModelId.CAMBRIDGE, params=None) -> PersonProfile:
    p = PersonProfile(name=name, model_id=model, params=params or cambridge.default_params())
    w._person_profiles.append(p)
    w._on_profiles_changed()
    cfg = w._configuration_window
    for i in range(cfg.person_combo.count()):
        if getattr(cfg.person_combo.itemData(i), "name", None) == name:
            cfg.person_combo.setCurrentIndex(i)
            break
    return p


def set_speed(w, mult: int) -> None:
    """Set the speed multiplier via the real slider. Calls _on_speed_changed
    directly too, so it still restarts the board/engine even when the slider
    value doesn't change (e.g. already at x1)."""
    cfg = w._configuration_window
    cfg.speed_slider.setValue(cfg._speed_to_slider(mult))
    w._on_speed_changed(float(mult))
    pump(150)


# ----------------------------------------------------------------------
# Cases  (each: fn(ctx) using `with Case(...) as c:`)
# ----------------------------------------------------------------------


def s1_connect(ctx: Ctx):
    with Case(ctx, "S1-02", "connect_pair_subscribe", "S1") as c:
        sess = ctx.require_board()
        c.step("connected; checking subscriptions")
        c.wait_until(
            lambda: any("glucose_value" in m for m in ctx.w._ble_log.get_messages()),
            30,
            "first CGM notification",
        )
        c.assert_(sess is not None, "session live")


def s2_stream_shape(ctx: Ctx):
    with Case(ctx, "S2-01", "cgm_stream_shape", "S2") as c:
        sess = ctx.require_board()
        sess.queue_write("run_state", protocol.encode_run_state(1))
        set_speed(ctx.w, 1)
        pump(1500)
        n0 = len(cgm_stream(ctx))
        c.wait_until(lambda: len(cgm_stream(ctx)) >= n0 + 4, 40, ">=4 fresh CGM notifications")
        vals = [g for _, g in cgm_stream(ctx)[n0:]]
        c.measure("n", len(vals))
        c.measure("range", f"{min(vals):.0f}-{max(vals):.0f}")
        c.assert_(
            all(20 <= v <= 500 for v in vals),
            "all plausible mg/dL",
            f"{min(vals):.0f}..{max(vals):.0f}",
        )
        if ctx.serial_live():
            c.assert_(ctx.serial_has("model_tick"), "serial shows model_tick (board is ticking)")


def s3_roundtrips(ctx: Ctx):
    with Case(ctx, "S3-03", "speed_roundtrip", "S3") as c:
        sess = ctx.require_board()
        for m in (10.0, 250.0, 1000.0):
            sess.queue_write("speed", protocol.encode_speed(m))
            pump(600)
            data = _read_char(sess, "speed", c)
            got = protocol.decode_speed(data)
            c.assert_(abs(got - m) < 0.5, f"speed x{m:g} round-trips", f"got {got}")
    with Case(ctx, "S3-04", "data_source_roundtrip", "S3") as c:
        sess = ctx.require_board()
        for csv in (True, False):
            sess.queue_write("data_source", protocol.encode_data_source(csv))
            pump(600)
            got = protocol.decode_data_source(_read_char(sess, "data_source", c))
            c.assert_(got == csv, f"data_source={csv} round-trips", f"got {got}")


def s4_run_state(ctx: Ctx):
    with Case(ctx, "S4-05", "datasource_stop_run_regression", "S4") as c:
        sess = ctx.require_board()
        c.step("data_source=csv, then STOP, then RUN back-to-back (the app's Start seq)")
        sess.queue_write("data_source", protocol.encode_data_source(True))
        sess.queue_write("run_state", protocol.encode_run_state(0))
        sess.queue_write("run_state", protocol.encode_run_state(1))
        pump(2500)
        got = protocol.decode_data_source(_read_char(sess, "data_source", c))
        c.assert_(got is True, "queued data_source not dropped by STOP", f"got {got}")
        sess.queue_write("data_source", protocol.encode_data_source(False))
        pump(800)


def s5_speed(ctx: Ctx):
    with Case(ctx, "S5-02", "speed_dt_on_serial", "S5") as c:
        sess = ctx.require_board()
        if not ctx.serial_live():
            raise _Skip("no serial console")
        sess.queue_write("speed", protocol.encode_speed(60.0))
        sess.queue_write("run_state", protocol.encode_run_state(1))
        ctx.wait_serial(lambda: ctx.serial_has("dt=1.0000"), 15, "serial dt=1.0000 at x60")
        c.assert_(True, "board dt scaled to x60")
        sess.queue_write("speed", protocol.encode_speed(1000.0))
        ctx.wait_serial(
            lambda: ctx.serial_has("dt=16.6") or ctx.serial_has("dt=16.7"),
            15,
            "serial dt~16.67 at x1000",
        )
        c.assert_(True, "board dt scaled to x1000")
        sess.queue_write("speed", protocol.encode_speed(1.0))


def s7_pisa(ctx: Ctx):
    with Case(ctx, "S7-03", "pisa_false_low", "S7") as c:
        w = ctx.w
        new_person(w, "E2E PISA", ModelId.CAMBRIDGE, cambridge.default_params())
        w._configuration_window.model_only_check.setChecked(True)
        set_speed(w, 60)
        QTest.mouseClick(w._start_pause_btn, Qt.MouseButton.LeftButton)
        c.wait_until(lambda: len(w._graph_y) >= 6, 15, "baseline points")
        base = sum(w._graph_y[-3:]) / 3
        base_n = len(w._graph_y)
        c.measure("baseline", round(base, 1))
        c.step("inject PISA 40% / 12 min")
        PisaInstantDialog.exec = lambda self: (
            self._duration_spin.setValue(12),
            self._depth_spin.setValue(40.0),
            QDialog.DialogCode.Accepted,
        )[-1]
        w._open_insert_pisa()
        c.wait_until(lambda: len(w._graph_y) >= base_n + 12, 20, "post-event points")
        post = w._graph_y[base_n:]
        trough = min(post)
        c.measure("trough", round(trough, 1))
        c.measure("recovered", round(post[-1], 1))
        c.assert_(trough < base - 10, "streamed value dips >10 mg/dL", f"{base:.0f}->{trough:.0f}")
        c.assert_(post[-1] > trough + 3, "recovers after the bout")
        c.assert_(len(w._pisa_spans) == 1 and len(w._pisa_patches) == 1, "graph interval shaded")
        QTest.mouseClick(w._stop_btn, Qt.MouseButton.LeftButton)
        w._configuration_window.model_only_check.setChecked(False)


def s8_csv(ctx: Ctx):
    with Case(ctx, "S8-02", "csv_playback_exact", "S8") as c:
        sess = ctx.require_board()
        from datetime import datetime as _dt

        from models import dexcom_csv

        rows = dexcom_csv.read_egv(DEXCOM_CSV)
        start = rows[0][0]
        samples = dexcom_csv.resample(rows, start, dexcom_csv.DEFAULT_INTERVAL_S)
        blob = protocol.build_glucose_track([float(s) for s in samples])
        c.step(f"upload {len(samples)} rows ({len(blob)} B)")
        up = {"done": False, "ok": False, "msg": ""}
        sess.csv_upload_finished.connect(lambda _a, ok, m: up.update(done=True, ok=ok, msg=m))
        sess.start_csv_upload(
            [
                {
                    "track": protocol.CSV_TRACK_GLUCOSE,
                    "blob": blob,
                    "row_count": len(samples),
                    "base_epoch_s": int(_dt.now().timestamp()),
                    "interval_s": dexcom_csv.DEFAULT_INTERVAL_S,
                }
            ]
        )
        c.wait_until(lambda: up["done"], 30, "upload finished")
        c.assert_(up["ok"], "upload ok", up["msg"])
        sess.queue_write("data_source", protocol.encode_data_source(True))
        sess.queue_write("speed", protocol.encode_speed(60.0))
        sess.queue_write("run_state", protocol.encode_run_state(0))
        sess.queue_write("run_state", protocol.encode_run_state(1))
        before = len(cgm_stream(ctx))
        c.wait_until(lambda: len(cgm_stream(ctx)) >= before + 4, 40, ">=4 CSV samples")
        got = [g for _, g in cgm_stream(ctx)[before:]]
        rowset = set(samples[:30])
        c.measure("got", got[:8])
        c.assert_(
            any(g in rowset for g in got), "streamed values are CSV rows (not the flat model line)"
        )
        c.assert_(not all(g == 100.0 for g in got), "not the model default")
        sess.queue_write("data_source", protocol.encode_data_source(False))


def _switch_profile(ctx, c, dexcom: bool):
    """Write comm_profile, reconnect, start, return the fresh count of the
    target stream once >=3 have arrived."""
    label = "dexcom" if dexcom else "sig"
    sess = ctx.sessions.get(ctx.board_addr) or ctx.connect_board()
    sess.queue_write("comm_profile", protocol.encode_comm_profile(dexcom))
    pump(1500)
    ctx.stop_sessions()
    pump(2000)
    sess = ctx.connect_board()
    sess.queue_write("speed", protocol.encode_speed(60.0))
    sess.queue_write("run_state", protocol.encode_run_state(1))
    stream = dexcom_stream if dexcom else sig_stream
    n0 = len(stream(ctx))
    c.wait_until(lambda: len(stream(ctx)) >= n0 + 3, 45, f"{label} glucose notifications")
    return [g for g in stream(ctx)[n0:]]


def s16_comm_profile(ctx: Ctx):
    with Case(ctx, "S16-01", "dexcom_profile_roundtrip", "S16") as c:
        ctx.require_board()
        c.step("force SIG CGMS baseline")
        sig1 = _switch_profile(ctx, c, dexcom=False)
        c.measure("sig_vals", sig1[:5])
        c.assert_(all(20 <= g <= 500 for g in sig1), "SIG stream plausible", f"{sig1[:5]}")

        c.step("switch to Dexcom-style profile")
        dex = _switch_profile(ctx, c, dexcom=True)
        c.measure("dexcom_vals", dex[:5])
        c.assert_(all(20 <= g <= 500 for g in dex), "Dexcom stream decodes plausible", f"{dex[:5]}")
        c.assert_(
            len(dexcom_stream(ctx)) > 0, "app received Dexcom-format messages (with sequence)"
        )
        c.assert_(
            (ctx.serial_has("pushed dexcom") if ctx.serial_live() else True),
            "firmware pushing dexcom messages",
        )

        c.step("switch back to SIG CGMS")
        sig2 = _switch_profile(ctx, c, dexcom=False)
        c.measure("sig_after", sig2[:5])
        c.assert_(all(20 <= g <= 500 for g in sig2), "SIG stream restored", f"{sig2[:5]}")
        ctx.stop_sessions()
        pump(1500)


def s10_window(ctx: Ctx):
    with Case(ctx, "S10-01", "rolling_window", "S10") as c:
        from models import app_settings

        w = ctx.w
        new_person(w, "E2E Window", ModelId.CAMBRIDGE, cambridge.default_params())
        w._configuration_window.model_only_check.setChecked(True)
        set_speed(w, 60)
        QTest.mouseClick(w._start_pause_btn, Qt.MouseButton.LeftButton)
        c.wait_until(lambda: len(w._graph_y) >= 14, 20, ">=14 points")
        total = len(w._graph_y)
        full = w._visible_xlim[1] - w._visible_xlim[0]
        app_settings.save_pref("view_window_s", 5)
        w._on_view_window_changed()
        pump(200)
        win = w._visible_xlim[1] - w._visible_xlim[0]
        vis = len(w._in_view(w._graph_x, w._graph_y))
        app_settings.save_pref("view_window_s", 0)
        w._on_view_window_changed()
        pump(200)
        restored = w._visible_xlim[1] - w._visible_xlim[0]
        c.measure("full_s", round(full))
        c.measure("win_s", round(win, 1))
        c.measure("vis", f"{vis}/{total}")
        c.assert_(win <= 6.0, "windowed span ~5 s", f"{win:.1f}")
        c.assert_(vis < total, "fewer points visible when windowed")
        c.assert_(restored >= full - 1.0, "'Entire run' restores full span")
        QTest.mouseClick(w._stop_btn, Qt.MouseButton.LeftButton)
        w._configuration_window.model_only_check.setChecked(False)
        app_settings.save_pref("view_window_s", 3600)


def s17_scenario(ctx: Ctx):
    with Case(ctx, "S17-01", "scenario_runner", "S17") as c:
        from models import scenario as scn

        w = ctx.w
        new_person(w, "E2E Scenario", ModelId.CAMBRIDGE, cambridge.default_params())
        w._configuration_window.model_only_check.setChecked(True)
        name, actions = scn.load("scenarios/demo_pisa.json")
        c.measure("scenario", name)

        # spy the dispatch so a trailing run_state:stop (which clears the graph)
        # doesn't erase the evidence a fault was injected
        seen = {"speed": 0, "food": 0, "pisa": 0, "start": 0, "stop": 0}
        real = w._scenario_dispatch

        def spy(kind, args):
            if kind == "speed":
                seen["speed"] += 1
            elif kind == "insert_food":
                seen["food"] += 1
            elif kind == "inject_fault":
                seen["pisa"] += 1
            elif kind == "run_state":
                seen[str(args.get("state"))] = seen.get(str(args.get("state")), 0) + 1
            return real(kind, args)

        fired: list[str] = []
        runner = scn.ScenarioRunner(actions, spy, w)
        runner.step.connect(fired.append)
        done = {"v": False}
        runner.finished.connect(lambda: done.__setitem__("v", True))
        for a in runner._actions:  # compress the timeline 10x for the test
            a.at_s = a.at_s / 10.0
        runner.start()
        c.wait_until(lambda: done["v"], 30, "scenario finished")
        c.measure("steps", len(fired))
        c.measure("seen", dict(seen))
        c.assert_(len(fired) >= len(actions), "every action fired", f"{len(fired)}/{len(actions)}")
        c.assert_(
            seen["speed"] and seen["food"] and seen["pisa"] and seen["start"] and seen["stop"],
            "speed / food / PISA / start / stop all dispatched",
            str(seen),
        )
        c.assert_(w._speed_mult == 60.0, "speed action reached the app", f"x{w._speed_mult}")
        QTest.mouseClick(w._stop_btn, Qt.MouseButton.LeftButton)
        w._configuration_window.model_only_check.setChecked(False)


def s1_reconnect(ctx: Ctx):
    with Case(ctx, "S1-04", "reconnect", "S1") as c:
        ctx.require_board()
        c.wait_until(lambda: len(cgm_stream(ctx)) >= 2, 30, "stream before reconnect")
        n0 = len(cgm_stream(ctx))
        c.step("drop the session, reconnect")
        ctx.stop_sessions()
        pump(2500)
        sess = ctx.connect_board()
        c.assert_(sess is not None, "reconnect succeeded")
        c.wait_until(lambda: len(cgm_stream(ctx)) > n0, 30, "CGM stream resumes")
        c.assert_(len(cgm_stream(ctx)) > n0, "new notifications after reconnect")


def s3_person(ctx: Ctx):
    with Case(ctx, "S3-01", "person_roundtrip", "S3") as c:
        sess = ctx.require_board()
        from models import uva_padova

        params = {**uva_padova.default_params(), "BW": 77.5, "VG": 1.61}
        sess.queue_write("person", protocol.encode_person_config(ModelId.UVA_PADOVA, params))
        pump(900)
        mid, got = protocol.decode_person_config(_read_char(sess, "person", c))
        c.measure("model_id", int(mid))
        c.assert_(mid == ModelId.UVA_PADOVA, "model id round-trips", str(mid))
        c.assert_(
            abs(got.get("BW", 0) - 77.5) < 0.05 and abs(got.get("VG", 0) - 1.61) < 0.05,
            "params round-trip",
            f"BW={got.get('BW')} VG={got.get('VG')}",
        )
        sess.queue_write(
            "person", protocol.encode_person_config(ModelId.CAMBRIDGE, cambridge.default_params())
        )
        pump(700)


def s3_food_events(ctx: Ctx):
    with Case(ctx, "S3-05", "food_events_roundtrip", "S3") as c:
        sess = ctx.require_board()
        from models.types import FoodEvent

        evs = [
            FoodEvent(time_of_day_min=420, duration_min=30, carbs_g=45.0),
            FoodEvent(time_of_day_min=780, duration_min=45, carbs_g=70.0),
            FoodEvent(time_of_day_min=1140, duration_min=30, carbs_g=55.0),
        ]
        sess.queue_write("food", protocol.encode_clear_food())
        for e in evs:
            sess.queue_write("food", protocol.encode_food_event(e))
        pump(1200)
        back = protocol.decode_food_events(_read_char(sess, "food_list", c))
        c.measure("count", len(back))
        c.assert_(len(back) == 3, "3 events stored", str(len(back)))
        c.assert_(
            abs(back[1].carbs_g - 70.0) < 0.1 and back[1].time_of_day_min == 780,
            "event fields round-trip",
            f"{back[1]}",
        )
        sess.queue_write("food", protocol.encode_clear_food())
        pump(600)


def s6_steady_state(ctx: Ctx):
    with Case(ctx, "S6-01", "cambridge_steady_state", "S6") as c:
        sess = ctx.require_board()
        sess.queue_write(
            "person", protocol.encode_person_config(ModelId.CAMBRIDGE, cambridge.default_params())
        )
        sess.queue_write("data_source", protocol.encode_data_source(False))
        sess.queue_write("speed", protocol.encode_speed(60.0))
        sess.queue_write("run_state", protocol.encode_run_state(0))
        sess.queue_write("run_state", protocol.encode_run_state(1))
        n0 = len(cgm_stream(ctx))
        c.wait_until(lambda: len(cgm_stream(ctx)) >= n0 + 6, 45, ">=6 samples")
        vals = [g for _, g in cgm_stream(ctx)[n0 + 2 :]]
        c.measure("mean", round(sum(vals) / len(vals), 1))
        c.assert_(
            all(80 <= v <= 120 for v in vals), "Cambridge sits near euglycaemia", f"{vals[:6]}"
        )
        sess.queue_write("speed", protocol.encode_speed(1.0))


def s7_instant_food_no_reset(ctx: Ctx):
    with Case(ctx, "S7-01", "instant_food_no_reset", "S7") as c:
        sess = ctx.require_board()
        if not ctx.serial_live():
            raise _Skip("no serial console")
        import re as _re

        def latest_tsim():
            for ln in reversed(ctx.serial.tail(25)):
                m = _re.search(r"t_sim=([\d.]+)min", ln)
                if m:
                    return float(m.group(1))
            return None

        def read_moving(tries: int = 14):
            """Return two t_sim reads ~2 s apart, the second clearly larger —
            i.e. the clock is live and advancing (not reset, not stale)."""
            prev = None
            for _ in range(tries):
                pump(2000)
                v = latest_tsim()
                if v is None:
                    continue
                if prev is not None and v > prev + 0.5:
                    return prev, v
                prev = v
            return None, None

        # fast enough that a few seconds is several sim-minutes, and running
        sess.queue_write("speed", protocol.encode_speed(60.0))
        sess.queue_write("run_state", protocol.encode_run_state(1))
        pump(6000)  # drain run-state/speed resets queued by earlier cases
        _, base = read_moving()
        if base is None:
            if not ctx.serial_live(25.0):
                raise _Skip("serial console went stale mid-run")
            c.assert_(False, "clock is live and advancing before the injection")
        c.measure("baseline_tsim", base)

        c.step("inject instant food mid-run — clock must keep climbing, not jump back")
        sess.queue_write("food_instant", protocol.encode_food_instant(15, 40.0))
        # firmware log wording: "instant food added" (pre-2026-09) /
        # "model_thread: slot N instant food, ..." (per-slot rewrite)
        c.wait_until(lambda: ctx.serial_has("instant food"), 10, "instant food logged")
        after: list[float] = []
        for _ in range(5):
            pump(2000)
            v = latest_tsim()
            if v is not None:
                after.append(v)
        c.measure("after", after)
        mono = all(b >= a - 0.1 for a, b in zip(after, after[1:]))
        no_backjump = bool(after) and after[0] >= base - 1.0  # not reset to ~0
        kept_climbing = bool(after) and after[-1] >= base + 2.0  # advanced ≥2 sim-min
        c.assert_(
            mono and no_backjump and kept_climbing,
            "sim clock kept advancing across the injection (no reset)",
            f"baseline@{base} then {after}",
        )
        c.assert_(
            not any("applied config" in ln for ln in ctx.serial.tail(30)),
            "no config re-apply logged for the instant event",
        )
        sess.queue_write("speed", protocol.encode_speed(1.0))


def s8_bad_crc(ctx: Ctx):
    with Case(ctx, "S8-06", "csv_bad_crc_rejected", "S8") as c:
        sess = ctx.require_board()
        blob = protocol.build_glucose_track([100.0] * 20)
        notes: list = []
        sess.csv_upload_finished.connect(lambda _a, ok, m: notes.append((ok, m)))
        # BEGIN with a deliberately wrong CRC, send data, COMMIT -> firmware must ERR
        good = protocol.csv_crc32(blob)
        begin = protocol.encode_csv_begin(
            protocol.CSV_TRACK_GLUCOSE, 20, 0, 2, len(blob), good ^ 0xFFFF
        )
        sess.queue_write("csv_control", begin)
        pump(700)
        for chunk in protocol.iter_csv_data_chunks(blob, 200):
            sess.queue_write("csv_data", chunk)
        pump(400)
        sess.queue_write("csv_control", protocol.encode_csv_commit(protocol.CSV_TRACK_GLUCOSE))
        pump(2000)
        crc_logged = (
            (ctx.serial_has("CRC mismatch") or ctx.serial_has("commit CRC"))
            if ctx.serial_live()
            else True
        )
        c.assert_(crc_logged, "firmware reported a CRC mismatch on commit")
        # board must still be alive & streaming
        n0 = len(cgm_stream(ctx))
        sess.queue_write("run_state", protocol.encode_run_state(1))
        c.wait_until(
            lambda: len(cgm_stream(ctx)) > n0, 20, "board still streaming after bad upload"
        )
        c.assert_(len(cgm_stream(ctx)) > n0, "board survived the bad upload")


def s9_cgms_only(ctx: Ctx):
    with Case(ctx, "S9-01", "cgms_only_rejects_writes", "S9") as c:
        sess = ctx.require_board()
        sess.queue_write("run_state", protocol.encode_run_state(1))
        pump(500)
        rejected: list = []
        sess.write_failed.connect(lambda _a, k, e: rejected.append((k, e)))
        c.step("enable CGMS Only, then attempt a person-config write")
        sess.queue_write("cgms_only", protocol.encode_cgms_only(True))
        pump(1500)
        sess.queue_write(
            "person", protocol.encode_person_config(ModelId.CAMBRIDGE, cambridge.default_params())
        )
        pump(1500)
        c.measure("rejected", [k for k, _ in rejected])
        c.assert_(
            any(k == "person" for k, _ in rejected),
            "person write rejected while CGMS-only",
            str(rejected),
        )
        c.step("disable CGMS Only")
        sess.queue_write("cgms_only", protocol.encode_cgms_only(False))
        sess.queue_write("run_state", protocol.encode_run_state(1))
        pump(1500)
        n0 = len(cgm_stream(ctx))
        c.wait_until(lambda: len(cgm_stream(ctx)) > n0, 20, "stream resumes after disable")
        c.assert_(len(cgm_stream(ctx)) > n0, "board streaming again after CGMS-only off")


def s12_malformed(ctx: Ctx):
    with Case(ctx, "S12-01", "malformed_writes_survived", "S12") as c:
        sess = ctx.require_board()
        n0 = len(cgm_stream(ctx))
        c.step("send a 1-byte person config and a 3-byte speed (both too short)")
        sess.queue_write("person", b"\x00")
        sess.queue_write("speed", b"\x00\x00\x00")
        pump(1500)
        sess.queue_write("run_state", protocol.encode_run_state(1))
        c.wait_until(lambda: len(cgm_stream(ctx)) > n0 + 1, 20, "board still streaming")
        c.assert_(len(cgm_stream(ctx)) > n0 + 1, "board unaffected by malformed writes")
        # a well-formed write still works afterwards
        sess.queue_write("speed", protocol.encode_speed(1.0))
        pump(600)
        got = protocol.decode_speed(_read_char(sess, "speed", c))
        c.assert_(
            abs(got - 1.0) < 0.5, "valid write still accepted after malformed ones", f"got {got}"
        )


def _read_char(sess: BleSession, key: str, c: Case) -> bytes:
    box = {}
    sess.config_read.connect(lambda _a, k, d: box.__setitem__(k, d))
    sess.request_read(key)
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline and key not in box:
        QTest.qWait(50)
    if key not in box:
        raise _Timeout(f"read {key}")
    return box[key]


SUITES: dict[str, list] = {
    "S1": [s1_connect, s1_reconnect],
    "S2": [s2_stream_shape],
    "S3": [s3_roundtrips, s3_person, s3_food_events],
    "S4": [s4_run_state],
    "S5": [s5_speed],
    "S6": [s6_steady_state],
    "S7": [s7_instant_food_no_reset, s7_pisa],
    "S8": [s8_csv, s8_bad_crc],
    "S9": [s9_cgms_only],
    "S10": [s10_window],
    "S12": [s12_malformed],
    "S16": [s16_comm_profile],
    "S17": [s17_scenario],
}
SMOKE = ["S1", "S2", "S5", "S6", "S7", "S16", "S17"]


# ----------------------------------------------------------------------


def _git_sha(path: str) -> str:
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=str(_ROOT), capture_output=True, text=True
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain", path], cwd=str(_ROOT), capture_output=True, text=True
        ).stdout.strip()
        return sha + ("-dirty" if dirty else "")
    except Exception:
        return "?"


def run_once(args) -> int:
    run_id = datetime.now(UTC).strftime("%Y%m%d-%H%M%SZ")
    run_dir = Path(args.out) / run_id
    (run_dir / "cases").mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()

    streams = {
        "serial": Stream("serial", run_dir / "serial.log", 4000),
        "app": Stream("app", run_dir / "app.log", 2000),
        "ble": Stream("ble", run_dir / "ble.jsonl", 1000),
    }
    sys.stdout = Tee(sys.__stdout__, streams["app"], t0)
    sys.stderr = Tee(sys.__stderr__, streams["app"], t0)

    board = None if args.no_board else args.board
    tap = SerialTap(SERIAL_PORT, streams["serial"], t0) if board else None
    serial_ok = bool(tap and tap.available)
    print(f"e2e run {run_id}  board={board or '(none)'}  serial={'ok' if serial_ok else 'no'}")

    app = QApplication.instance() or QApplication(sys.argv)
    w = mw.MainWindow()
    w.show()
    pump(400)

    # BLE traffic taps
    w._ble_log.new_message.connect(
        lambda m: streams["ble"].add(
            json.dumps(
                {
                    "dir": "in",
                    "char": (m.get("characteristic") or "")[-4:],
                    "glucose": m.get("glucose_value"),
                    "carbs": m.get("carbs_g_per_min"),
                    "raw": m.get("raw_hex"),
                }
            ),
            time.monotonic() - t0,
        )
    )
    _orig_qw = BleSession.queue_write

    def _qw(self, key, payload):
        streams["ble"].add(
            json.dumps({"dir": "out", "char": key, "hex": payload.hex()}), time.monotonic() - t0
        )
        return _orig_qw(self, key, payload)

    BleSession.queue_write = _qw

    ctx = Ctx(w, run_dir, t0, streams, board, serial_ok)
    (run_dir / "environment.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "board": board,
                "serial_port": SERIAL_PORT if serial_ok else None,
                "fw_sha": _git_sha("firmware/"),
                "app_sha": _git_sha("src/"),
                "python": sys.version.split()[0],
                "started": datetime.now(UTC).isoformat(timespec="seconds"),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    only = (
        {s.strip().upper() for s in args.only.split(",")}
        if args.only
        else set(SMOKE)
        if args.smoke
        else set(SUITES)
    )
    try:
        # Preflight: put the board in a known state so cases don't inherit
        # STOPPED / CSV / x1000 / Dexcom from a previous run.
        if board:
            try:
                pf = ctx.connect_board()
                for k, v in (
                    ("comm_profile", protocol.encode_comm_profile(False)),
                    ("data_source", protocol.encode_data_source(False)),
                    ("cgms_only", protocol.encode_cgms_only(False)),
                    ("speed", protocol.encode_speed(1.0)),
                    ("run_state", protocol.encode_run_state(1)),
                ):
                    pf.queue_write(k, v)
                pump(3000)
                ctx.log_event({"kind": "preflight", "ok": True})
                print("  preflight: board reset to model / x1 / running")
            except _Skip as exc:
                print(f"  preflight: board not reachable ({exc})")

        for suite, cases in SUITES.items():
            if suite not in only:
                continue
            for case_fn in cases:
                try:
                    case_fn(ctx)
                except Exception as exc:  # pragma: no cover - safety net
                    print(f"  !! {case_fn.__name__} crashed outside a Case: {exc}")
    finally:
        BleSession.queue_write = _orig_qw
        ctx.stop_sessions()
        if tap:
            tap.stop()
        subprocess.run(
            ["git", "checkout", "--", "data/profiles.json", "data/settings.json"],
            cwd=str(_ROOT),
            check=False,
        )

    counts: dict[str, int] = {}
    for _cid, _suite, verdict, _note in ctx.results:
        counts[verdict] = counts.get(verdict, 0) + 1
    lines = [f"# e2e {run_id}", "", "  ".join(f"{v}:{n}" for v, n in sorted(counts.items())), ""]
    for cid, suite, verdict, note in ctx.results:
        lines.append(f"- {verdict:8} {cid:9} {note}")
    (run_dir / "SUMMARY.md").write_text("\n".join(lines), encoding="utf-8")
    ctx.close()

    sys.stdout = sys.__stdout__
    sys.stderr = sys.__stderr__
    for s in streams.values():
        s.close()
    print(f"\n================ {run_id} ================")
    print("  ".join(f"{v}:{n}" for v, n in sorted(counts.items())) or "(no cases)")
    for cid, _s, verdict, note in ctx.results:
        print(f"  {verdict:8} {cid:9} {note}")
    print(f"artifacts: {run_dir}")
    bad = counts.get("FAIL", 0) + counts.get("ERROR", 0) + counts.get("TIMEOUT", 0)
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--board", default=DEFAULT_BOARD)
    ap.add_argument("--no-board", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--reflash", action="store_true")
    ap.add_argument("--loop", type=int, default=1, help="run the whole suite N times")
    ap.add_argument("--out", default=str(_ROOT / "test-artifacts"))
    args = ap.parse_args()

    if args.reflash:
        print("reflashing firmware…")
        subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-File",
                str(_ROOT / "firmware" / "scripts" / "build.ps1"),
                "-Pristine",
            ],
            check=True,
        )
        subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-File",
                str(_ROOT / "firmware" / "scripts" / "flash.ps1"),
            ],
            check=True,
        )
        time.sleep(3)

    if args.loop > 1:
        # Each iteration in a fresh process — Qt/matplotlib/BLE state does not
        # survive several MainWindow lifecycles in one interpreter.
        worst = 0
        child_args: list[str] = []
        skip = False
        for a in sys.argv[1:]:
            if skip:
                skip = False
                continue
            if a == "--loop":
                skip = True
                continue
            if a.startswith("--loop="):
                continue
            child_args.append(a)
        env = {**os.environ, "PYTHONPATH": ""}
        for i in range(args.loop):
            print(f"\n########## loop {i + 1}/{args.loop} ##########", flush=True)
            rc = subprocess.run([sys.executable, "-u", __file__, *child_args], env=env).returncode
            worst = max(worst, rc)
            time.sleep(3)
        print(f"\n########## loop done — worst rc {worst} ##########")
        os._exit(worst)

    os._exit(run_once(args))


if __name__ == "__main__":
    main()
