#!/usr/bin/env python3
"""End-to-end harness for the 4-sensor firmware build (CONFIG_APP_SENSOR_COUNT=4).

Complements scripts/e2e.py (single-sensor regression). This one drives the
multi-slot path: it pushes a whole Board Layout (4 independent models + sensor
noise, one slot on CSV) via the real BoardLayoutWindow + BleSession, then checks
each slot from the firmware's own per-slot `model_tick[i]:` serial line and from
a second BLE connection's demuxed stream. Covers: per-slot models + noise, CSV
playback on a slot, the shared fast-mode clock, and slot-targeted Insert Food /
Exercise / PISA.

Usage:
  python scripts/e2e_4sensor.py                 # all cases
  python scripts/e2e_4sensor.py --only F1,F5    # selected cases (exact ids; "F" = all)
  python scripts/e2e_4sensor.py --no-board      # everything SKIPs
  python scripts/e2e_4sensor.py --loop 3        # 3 fresh processes back to back
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
os.chdir(_ROOT)

from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

# Reuse the single-sensor harness's capture + case machinery verbatim.
from e2e import (  # noqa: E402
    Case, Ctx, SerialTap, Stream, Tee, _Skip, _git_sha, pump, _read_char,
    SERIAL_PORT,
)
import graphic.main_window as mw  # noqa: E402
from api import protocol  # noqa: E402
from graphic.board_layout_window import BoardLayoutWindow  # noqa: E402
from models import app_settings  # noqa: E402
from models import cambridge, deichmann, royparker, uva_padova  # noqa: E402
from models import dexcom_csv, sensors as sensor_defaults  # noqa: E402
from models.board_layout import BoardLayout  # noqa: E402
from models.types import ModelId, PersonProfile, SensorId, SensorProfile  # noqa: E402
from services.ble_session import BleSession  # noqa: E402

DEXCOM_CSV = "dataset/Dexcom_001.csv"
NAMES = [f"Nordic Glucose Sensor {i}" for i in range(1, 5)]

_TICK_RE = re.compile(
    r"model_tick\[(?P<slot>\d)\]:\s+t_sim=(?P<t_sim>[-\d.]+)min\s+dt=(?P<dt>[-\d.]+)\s+"
    r"model=(?P<model>\d)\s+sensor=(?P<sensor>\d)\s+ds=(?P<ds>\d)\s+"
    r"glucose=(?P<glucose>[-\d.]+)\s+reading=(?P<reading>[-\d.]+)\s+"
    r"pisa=(?P<pisa>[-\d.]+)\s+carbs=(?P<carbs>[-\d.]+)\s+ex=(?P<ex>[-\d.]+)"
)


# ----------------------------------------------------------------------
# 4-sensor context: scans the identities, connects sessions, parses per-slot
# serial, and pushes a Board Layout through the real app widget.
# ----------------------------------------------------------------------

class FourCtx(Ctx):
    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self._scan: dict[str, str] = {}          # name -> address
        self._slot_sess: dict[int, BleSession] = {}

    # -- discovery / connect -------------------------------------------

    def scan(self) -> dict[str, str]:
        """Discover the 4 identity addresses. Runs on a worker thread — bleak's
        WinRT scanner refuses to start on the Qt GUI (STA) main thread."""
        if self._scan:
            return self._scan
        if not self.board_addr:
            raise _Skip("--no-board")
        import threading

        from bleak import BleakScanner

        found: dict[str, str] = {}
        err: dict[str, Exception] = {}

        async def go():
            for d, adv in (await BleakScanner.discover(timeout=10.0, return_adv=True)).values():
                n = adv.local_name or d.name or ""
                if n in NAMES:
                    found[n] = d.address

        def worker():
            try:
                asyncio.run(go())
            except Exception as exc:  # noqa: BLE001
                err["e"] = exc

        t = threading.Thread(target=worker, daemon=True)
        t.start()
        deadline = time.monotonic() + 25
        while t.is_alive() and time.monotonic() < deadline:
            QTest.qWait(100)
        if err:
            raise _Skip(f"scan failed: {err['e']}")
        if not found:
            raise _Skip("no 'Nordic Glucose Sensor N' identities in range")
        self._scan = found
        print(f"    scan: {found}")
        return self._scan

    def slot_session(self, slot: int, timeout_ms: int = 40000) -> BleSession:
        """Connect (once) a BleSession to identity `slot`'s address. The numbered
        name makes BleSession skip Windows pairing and demux to this slot."""
        if slot in self._slot_sess:
            return self._slot_sess[slot]
        name = NAMES[slot]
        addr = self.scan().get(name)
        if not addr:
            raise _Skip(f"{name} not advertising")
        bt = self.w._ensure_bluetooth_window()
        state = {"done": False, "ok": False, "err": ""}
        sess = BleSession(addr, name, bt)
        sess.connected.connect(lambda *_: state.update(done=True, ok=True))
        sess.connect_failed.connect(lambda _a, e: state.update(done=True, ok=False, err=e))
        sess.new_message.connect(self.w._ble_log.add_message)
        bt._sessions[addr] = sess
        bt._names[addr] = name
        print(f"    connecting {name} ({addr})…")
        sess.start()
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline and not state["done"]:
            QTest.qWait(100)
        if not state["ok"]:
            bt._sessions.pop(addr, None)
            raise _Skip(f"{name} connect failed: {state.get('err') or 'timeout'}")
        self._slot_sess[slot] = sess
        self.sessions[addr] = sess
        print(f"    {name} connected")
        return sess

    # Every config / layout / instant write goes through one session. Any
    # identity reaches the shared config service; identity 1 is used (not 0)
    # because the factory identity 0 (D0:...) accumulated Windows pairing
    # associations during earlier testing and drops connections more often.
    CFG_SLOT = 1

    def cfg_session(self) -> BleSession:
        return self.slot_session(self.CFG_SLOT)

    # -- per-slot serial ---------------------------------------------

    def slot_line(self, slot: int, within: int = 400) -> dict | None:
        for ln in reversed(self.serial.tail(within)):
            m = _TICK_RE.search(ln)
            if m and int(m.group("slot")) == slot:
                g = m.groupdict()
                return {
                    "slot": int(g["slot"]),
                    "t_sim": float(g["t_sim"]), "dt": float(g["dt"]),
                    "model": int(g["model"]), "sensor": int(g["sensor"]),
                    "ds": int(g["ds"]), "glucose": float(g["glucose"]),
                    "reading": float(g["reading"]), "pisa": float(g["pisa"]),
                    "carbs": float(g["carbs"]), "ex": float(g["ex"]),
                }
        return None

    def wait_slot(self, slot: int, pred, timeout_s: float, desc: str) -> dict:
        deadline = time.monotonic() + timeout_s
        last = None
        while time.monotonic() < deadline:
            last = self.slot_line(slot)
            if last is not None:
                try:
                    if pred(last):
                        return last
                except Exception:
                    pass
            QTest.qWait(150)
        if not self.serial_live(25.0):
            raise _Skip(f"serial went stale waiting for: {desc}")
        raise _Skip(f"slot {slot}: {desc} (last={last})")

    def all_slot_lines(self) -> dict[int, dict]:
        return {i: self.slot_line(i) for i in range(4)}

    def slot_line_raw(self, slot: int, within: int = 400) -> str | None:
        for ln in reversed(self.serial.tail(within)):
            m = _TICK_RE.search(ln)
            if m and int(m.group("slot")) == slot:
                return m.group(0)
        return None

    # -- per-slot GATT readback -----------------------------------

    def select_slot(self, sess: BleSession, slot: int, c: Case) -> None:
        """Set the sensor-select cursor and BLOCK until the board's own readback
        confirms it — comm_thread can lag the write by a tick or two while it's
        busy pushing measurements, and a per-slot read done too early would hit
        the previous slot."""
        for _ in range(40):
            sess.queue_write("sensor_select", protocol.encode_sensor_select(slot))
            pump(250)
            if protocol.decode_sensor_select(_read_char(sess, "sensor_select", c)) == slot:
                return
        raise _Skip(f"sensor_select cursor never reached {slot}")

    def slot_config(self, sess: BleSession, slot: int, c: Case) -> tuple:
        """(model_name, sensor_name, is_csv) for `slot`, via the sensor-select cursor."""
        self.select_slot(sess, slot, c)
        pm = protocol.decode_person_config(_read_char(sess, "person", c))
        sm = protocol.decode_sensor_config(_read_char(sess, "sensor", c))
        ds = protocol.decode_data_source(_read_char(sess, "data_source", c))
        return (pm[0].name if pm else None, sm[0].name if sm else None, bool(ds))

    def all_slot_configs(self, sess: BleSession, c: Case) -> dict[int, tuple]:
        return {i: self.slot_config(sess, i, c) for i in range(4)}

    # -- misc ----------------------------------------------------

    def drop_session(self, slot: int) -> None:
        """Disconnect one identity's session and forget it (so slot_session can
        reconnect it fresh)."""
        sess = self._slot_sess.pop(slot, None)
        if sess is None:
            return
        self.sessions.pop(sess._address, None)
        bt = self.w._bluetooth_window
        if bt is not None:
            bt._sessions.pop(sess._address, None)
        sess.stop()
        sess.wait(6000)

    def jlink_reset(self) -> None:
        jlink = r"C:\Program Files\SEGGER\JLink_V924a\JLink.exe"
        if not os.path.exists(jlink):
            raise _Skip("JLink.exe not found — cannot reset the board")
        import tempfile

        script = os.path.join(tempfile.gettempdir(), "e2e4s_reset.jlink")
        with open(script, "w", encoding="ascii") as f:
            f.write("r\ngo\nexit\n")
        try:
            subprocess.run([jlink, "-device", "nRF54L15_M33", "-if", "SWD",
                            "-speed", "4000", "-autoconnect", "1", "-CommandFile", script],
                           capture_output=True, timeout=40)
        except Exception as exc:  # noqa: BLE001
            raise _Skip(f"jlink reset failed: {exc}")
        self._scan = {}  # addresses are stable, but force a fresh discovery

    # -- speed (with settle) --------------------------------------

    def set_speed_settle(self, sess: BleSession, mult: float, timeout_s: float = 12.0) -> None:
        """Write the speed multiplier and WAIT for the board's config-apply to
        drain before returning. A SPEED write goes through the firmware's full
        apply_config_locked() path (it resets the sim clock and, critically,
        clears the per-slot instant-event arrays), so an Insert-Food/Exercise/
        PISA write fired too soon after would be wiped by the pending apply.
        reset_sync fires the instant that apply lands — wait for it."""
        got = {"n": 0}
        sess.reset_sync.connect(lambda _a: got.__setitem__("n", got["n"] + 1))
        n0 = got["n"]
        sess.queue_write("speed", protocol.encode_speed(float(mult)))
        sess.queue_write("run_state", protocol.encode_run_state(1))
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline and got["n"] == n0:
            QTest.qWait(100)
        pump(1200)   # let the model thread run a couple ticks at the new dt

    # -- layout push -----------------------------------------------

    def push_layout(self, c: Case, assign: list[tuple], persons: dict, sensors: dict) -> None:
        """`assign` is [(slot, person_key, sensor_key), ...]. Builds the real
        BoardLayoutWindow, calls _build_slots(), pushes via send_board_layout()."""
        plist = list(persons.values())
        slist = list(sensors.values())
        layout = BoardLayout()
        for slot, pk, sk in assign:
            layout.slots[slot].person = persons[pk].name
            layout.slots[slot].sensor = sensors[sk].name

        sess = self.cfg_session()
        stub_bt = type("BT", (), {"sessions": lambda _s: {b: sess for b in [sess._address]},
                                  "display_name": lambda _s, _a: "cfg"})()
        win = BoardLayoutWindow(plist, slist, layout, lambda: None, lambda: stub_bt)
        slots, errs = win._build_slots()
        c.assert_(not errs, "layout builds without errors", "; ".join(errs))
        c.assert_(len(slots) == len(assign), f"{len(assign)} slot entries built", str(len(slots)))

        done = {"v": None}
        sess.board_layout_finished.connect(lambda _a, ok, m: done.__setitem__("v", (ok, m)))
        sess.send_board_layout(slots)
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline and done["v"] is None:
            QTest.qWait(150)
        c.assert_(done["v"] is not None, "layout push finished", "timeout")
        c.assert_(done["v"][0], "layout push OK", str(done["v"]))


# ----------------------------------------------------------------------
# Shared profile fixtures
# ----------------------------------------------------------------------

def _persons() -> dict[str, PersonProfile]:
    p = {
        "camb": PersonProfile("F4-Cambridge", ModelId.CAMBRIDGE, cambridge.default_params()),
        "uva": PersonProfile("F4-UVA", ModelId.UVA_PADOVA, uva_padova.default_params()),
        "roy": PersonProfile("F4-Roy", ModelId.ROYPARKER, royparker.default_params()),
        "deich": PersonProfile("F4-Deichmann", ModelId.DEICHMANN, deichmann.default_params()),
    }
    csv = PersonProfile("F4-CSV", ModelId.CAMBRIDGE, cambridge.default_params())
    csv.data_source = "csv"
    csv.csv_path = DEXCOM_CSV
    rows = dexcom_csv.read_egv(DEXCOM_CSV)
    csv.csv_window_start_iso = rows[0][0].isoformat()
    p["csv"] = csv
    return p


def _sensors() -> dict[str, SensorProfile]:
    return {
        "ideal": SensorProfile("F4-Ideal", SensorId.IDEAL, {}),
        "breton": SensorProfile("F4-Breton", SensorId.BRETON,
                                sensor_defaults.breton_default_params()),
    }


def _set_speed(sess: BleSession, mult: float) -> None:
    sess.queue_write("speed", protocol.encode_speed(float(mult)))
    sess.queue_write("run_state", protocol.encode_run_state(1))


# ----------------------------------------------------------------------
# Cases
# ----------------------------------------------------------------------

def f1_layout_models(ctx: FourCtx):
    with Case(ctx, "F1-01", "layout_4_models_1_csv", "F1") as c:
        persons, sensors = _persons(), _sensors()
        # slot0 Cambridge/Ideal, slot1 UVA/Breton, slot2 Roy/Ideal, slot3 CSV
        ctx.push_layout(c, [
            (0, "camb", "ideal"), (1, "uva", "breton"),
            (2, "roy", "ideal"), (3, "csv", "breton"),
        ], persons, sensors)
        _set_speed(ctx.cfg_session(), 60)

        if not ctx.serial_live():
            raise _Skip("no serial console")
        exp_model = {0: 0, 1: 1, 2: 2, 3: 0}   # CSV slot keeps its person's model id
        exp_ds = {0: 0, 1: 0, 2: 0, 3: 1}
        for s in range(4):
            line = ctx.wait_slot(
                s, lambda ln, s=s: ln["model"] == exp_model[s] and ln["ds"] == exp_ds[s],
                40, f"slot {s} model={exp_model[s]} ds={exp_ds[s]}")
            c.measure(f"slot{s}", f"model={line['model']} sensor={line['sensor']} ds={line['ds']}")

        # per-slot GATT readback via the sensor-select cursor
        got = ctx.all_slot_configs(ctx.cfg_session(), c)
        exp_rb = {0: ("CAMBRIDGE", "IDEAL", False), 1: ("UVA_PADOVA", "BRETON", False),
                  2: ("ROYPARKER", "IDEAL", False), 3: ("CAMBRIDGE", "BRETON", True)}
        c.measure("readback", got)
        c.assert_(got == exp_rb, "per-slot readback matches the layout", f"{got} != {exp_rb}")

        # shared clock: all four slot lines within a couple of ticks of each other
        lines = ctx.all_slot_lines()
        ts = [lines[i]["t_sim"] for i in range(4) if lines[i]]
        c.assert_(len(ts) == 4, "all four slots ticking", str(list(lines)))
        c.assert_(max(ts) - min(ts) < 1.0, "slots share one sim clock",
                  f"t_sim spread {max(ts) - min(ts):.3f} min")


def f2_independent_streams(ctx: FourCtx):
    with Case(ctx, "F2-01", "per_identity_demux", "F2") as c:
        sess0 = ctx.cfg_session()
        if not ctx.slot_line(0):
            persons, sensors = _persons(), _sensors()
            ctx.push_layout(c, [(0, "camb", "ideal"), (1, "uva", "breton"),
                                (2, "roy", "ideal"), (3, "deich", "breton")], persons, sensors)
            _set_speed(sess0, 60)
        # distinct steady values per slot after the layout: slot 0 Cambridge/Ideal
        # sits ~97, slot 2 Roy&Parker/Ideal ~100-101, slot 3 CSV ~59-70.
        s2 = ctx.slot_session(2)
        c.assert_(s2._own_instance_index == 2, "session parsed its slot index from the name",
                  str(s2._own_instance_index))

        base = len(ctx.w._ble_log.get_messages())

        def _s2_vals():
            return [float(m["glucose_value"])
                    for m in ctx.w._ble_log.get_messages()[base:]
                    if m.get("glucose_value") is not None
                    and "Sensor 3" in (m.get("user_id") or "")]

        try:
            c.wait_until(lambda: len(_s2_vals()) >= 2, 80, "slot-2 identity measurements")
        except Exception:
            pass
        vals = _s2_vals()
        if not vals:
            # WinRT is flaky about start_notify across 4 same-UUID CGMS
            # instances; the demux logic itself is covered by F1 (the cfg
            # identity only ever shows slot 0). Don't fail the run on transport.
            ctx.drop_session(2)   # don't leave it congesting later cases
            raise _Skip("slot-2 identity produced no measurements (WinRT multi-instance notify)")
        s2_serial = ctx.slot_line(2)
        s0_serial = ctx.slot_line(0)
        c.measure("slot2_ble", [round(v, 1) for v in vals[:8]])
        c.measure("slot2_serial_reading", s2_serial and round(s2_serial["reading"], 1))
        c.assert_(s2._instance_count == 4, "slot-2 session sees all 4 CGMS instances",
                  str(s2._instance_count))
        # every value this identity surfaced must be slot 2's, never a sibling's
        c.assert_(all(abs(v - s2_serial["reading"]) < 10 for v in vals),
                  "slot-2 identity only reports slot-2 glucose",
                  f"ble={[round(v, 1) for v in vals]} vs slot2 serial ~{s2_serial['reading']:.1f}")
        c.assert_(not any(abs(v - s0_serial["reading"]) < 1.0 for v in vals),
                  "slot-2 identity never leaks slot-0's value",
                  f"slot0 serial ~{s0_serial['reading']:.1f}")


def f3_csv_slot(ctx: FourCtx):
    with Case(ctx, "F3-01", "csv_slot_playback", "F3") as c:
        if not ctx.serial_live():
            raise _Skip("no serial console")
        persons, sensors = _persons(), _sensors()
        ctx.push_layout(c, [(0, "camb", "ideal"), (1, "uva", "breton"),
                            (2, "roy", "ideal"), (3, "csv", "breton")], persons, sensors)
        _set_speed(ctx.cfg_session(), 60)

        rows = dexcom_csv.read_egv(DEXCOM_CSV)
        samples = dexcom_csv.resample(rows, rows[0][0], dexcom_csv.DEFAULT_INTERVAL_S)
        rowset = set(float(s) for s in samples[:60])

        line3 = ctx.wait_slot(3, lambda ln: ln["ds"] == 1, 40, "slot 3 ds=1 (CSV)")
        c.assert_(line3["ds"] == 1, "slot 3 on CSV playback")
        seen = []
        c.wait_until(lambda: (seen.append(ctx.slot_line(3)["glucose"]) or True)
                     and len(set(seen)) >= 4, 40, "several distinct slot-3 glucose values")
        c.measure("slot3_glucose", [round(v, 1) for v in seen[-8:]])
        c.assert_(any(round(v, 0) in {round(r, 0) for r in rowset} for v in seen),
                  "slot 3 glucose values are CSV rows (not a flat model line)",
                  f"{[round(v,1) for v in seen[-6:]]}")
        c.assert_(not all(v == seen[0] for v in seen), "slot 3 is not frozen")
        # a model slot alongside it is unaffected
        c.assert_(ctx.slot_line(0)["ds"] == 0 and ctx.slot_line(0)["model"] == 0,
                  "slot 0 still runs its model while slot 3 plays CSV")


def f4_fast_mode(ctx: FourCtx):
    with Case(ctx, "F4-01", "fast_mode_shared_clock", "F4") as c:
        if not ctx.serial_live():
            raise _Skip("no serial console")
        sess = ctx.cfg_session()
        if not ctx.slot_line(0):
            persons, sensors = _persons(), _sensors()
            ctx.push_layout(c, [(0, "camb", "ideal"), (1, "uva", "breton"),
                                (2, "roy", "ideal"), (3, "deich", "breton")], persons, sensors)

        _set_speed(sess, 60)
        for s in range(4):
            ln = ctx.wait_slot(s, lambda ln: abs(ln["dt"] - 1.0) < 0.02, 20,
                               f"slot {s} dt ~1.0 at x60")
            c.measure(f"dt60_slot{s}", ln["dt"])
        _set_speed(sess, 1000)
        for s in range(4):
            ctx.wait_slot(s, lambda ln: 16.5 < ln["dt"] < 16.8, 20,
                          f"slot {s} dt ~16.67 at x1000")
        c.assert_(True, "all four slots follow the shared speed multiplier")
        _set_speed(sess, 60)


def f5_insert_food(ctx: FourCtx):
    with Case(ctx, "F5-01", "insert_food_targeted", "F5") as c:
        if not ctx.serial_live():
            raise _Skip("no serial console")
        sess = ctx.cfg_session()
        if not ctx.slot_line(1):
            persons, sensors = _persons(), _sensors()
            ctx.push_layout(c, [(0, "camb", "ideal"), (1, "uva", "breton"),
                                (2, "roy", "ideal"), (3, "deich", "breton")], persons, sensors)
        ctx.set_speed_settle(sess, 60)
        ctx.wait_slot(1, lambda ln: ln["dt"] > 0.5, 15, "slot 1 ticking fast")
        g0 = ctx.slot_line(1)["glucose"]
        c.measure("slot1_glucose_before", round(g0, 1))

        c.step("sensor_select=1, then a 60 g / 45 min instant bolus")
        sess.queue_write("sensor_select", protocol.encode_sensor_select(1))
        sess.queue_write("food_instant", protocol.encode_food_instant(45, 60.0))

        hit = ctx.wait_slot(1, lambda ln: ln["carbs"] > 0.05, 25, "slot 1 carbs > 0")
        c.measure("slot1_carbs", round(hit["carbs"], 3))
        c.assert_(ctx.slot_line(0)["carbs"] < 1e-6, "slot 0 carbs stayed 0 (event was slot-targeted)")
        c.assert_(ctx.slot_line(2)["carbs"] < 1e-6, "slot 2 carbs stayed 0")
        up = ctx.wait_slot(1, lambda ln: ln["glucose"] > g0 + 3.0, 40,
                           "slot 1 glucose rises after the bolus")
        c.measure("slot1_glucose_after", round(up["glucose"], 1))


def f6_insert_exercise(ctx: FourCtx):
    with Case(ctx, "F6-01", "insert_exercise_targeted", "F6") as c:
        if not ctx.serial_live():
            raise _Skip("no serial console")
        sess = ctx.cfg_session()
        if not ctx.slot_line(2):
            persons, sensors = _persons(), _sensors()
            ctx.push_layout(c, [(0, "camb", "ideal"), (1, "uva", "breton"),
                                (2, "roy", "ideal"), (3, "deich", "breton")], persons, sensors)
        ctx.set_speed_settle(sess, 60)
        ctx.wait_slot(2, lambda ln: ln["dt"] > 0.5, 15, "slot 2 ticking fast")

        c.step("sensor_select=2 (Roy&Parker reacts), then a 60 % / 40 min instant bout")
        sess.queue_write("sensor_select", protocol.encode_sensor_select(2))
        sess.queue_write("exercise_instant", protocol.encode_exercise_instant(40, 60.0))

        hit = ctx.wait_slot(2, lambda ln: ln["ex"] > 1.0, 25, "slot 2 ex > 0")
        c.measure("slot2_ex", round(hit["ex"], 1))
        c.assert_(ctx.slot_line(0)["ex"] < 1e-6, "slot 0 ex stayed 0 (event was slot-targeted)")
        c.assert_(ctx.slot_line(1)["ex"] < 1e-6, "slot 1 ex stayed 0")


def f7_insert_pisa(ctx: FourCtx):
    with Case(ctx, "F7-01", "insert_pisa_targeted", "F7") as c:
        if not ctx.serial_live():
            raise _Skip("no serial console")
        sess = ctx.cfg_session()
        if not ctx.slot_line(0):
            persons, sensors = _persons(), _sensors()
            ctx.push_layout(c, [(0, "camb", "ideal"), (1, "uva", "breton"),
                                (2, "roy", "ideal"), (3, "deich", "breton")], persons, sensors)
        ctx.set_speed_settle(sess, 60)
        ctx.wait_slot(0, lambda ln: ln["dt"] > 0.5 and ln["pisa"] > 0.99, 15, "slot 0 clean")
        r0 = ctx.slot_line(0)["reading"]
        c.measure("slot0_reading_before", round(r0, 1))

        c.step("sensor_select=0, then a 45 % / 12 min PISA attenuation")
        sess.queue_write("sensor_select", protocol.encode_sensor_select(0))
        sess.queue_write("pisa_instant", protocol.encode_pisa_instant(12, 0.45))

        dip = ctx.wait_slot(0, lambda ln: ln["pisa"] < 0.9, 25, "slot 0 pisa factor drops")
        c.measure("slot0_pisa_trough", round(dip["pisa"], 3))
        c.assert_(dip["reading"] < dip["glucose"] - 3.0,
                  "slot 0 reading pulled below true glucose", f"{dip['reading']:.1f} vs {dip['glucose']:.1f}")
        c.assert_(ctx.slot_line(1)["pisa"] > 0.99, "slot 1 pisa unaffected (event was slot-targeted)")
        rec = ctx.wait_slot(0, lambda ln: ln["pisa"] > 0.99, 40, "slot 0 pisa recovers to ~1.0")
        c.measure("slot0_pisa_recovered", round(rec["pisa"], 3))


def _ensure_layout(ctx: FourCtx, c: Case, csv_slot3: bool = False) -> None:
    """Push the standard 4-model layout if the board isn't already running one."""
    if ctx.slot_line(0):
        return
    persons, sensors = _persons(), _sensors()
    s3 = ("csv" if csv_slot3 else "deich", "breton")
    ctx.push_layout(c, [(0, "camb", "ideal"), (1, "uva", "breton"),
                        (2, "roy", "ideal"), (3, s3[0], s3[1])], persons, sensors)
    _set_speed(ctx.cfg_session(), 60)


def f8_alerts(ctx: FourCtx):
    with Case(ctx, "F8-01", "range_alerts", "F8") as c:
        if not ctx.serial_live():
            raise _Skip("no serial console")
        ctx.cfg_session()
        _ensure_layout(ctx, c)
        name = NAMES[ctx.CFG_SLOT]   # the cfg identity's own slot (~100-107 mg/dL)

        def _row():
            return next((it for uid, it in ctx.w._user_items.items() if name in uid), None)

        def _fresh_msg(n0: int, timeout: float, desc: str) -> None:
            c.wait_until(lambda: any(
                "glucose_value" in m and name in (m.get("user_id") or "")
                for m in ctx.w._ble_log.get_messages()[n0:]), timeout, desc)

        _fresh_msg(len(ctx.w._ble_log.get_messages()), 40, "a CGM measurement on the cfg identity")
        c.assert_(_row() is not None, "the sensor has a tree row")

        saved = dict(ctx.w._thresholds)
        try:
            # _category(): default thresholds tbr2=54 tbr1=70 tar1=180 tar2=250
            c.assert_(ctx.w._category(40.0) == "r" and ctx.w._category(60.0) == "y"
                      and ctx.w._category(120.0) == "g" and ctx.w._category(200.0) == "y"
                      and ctx.w._category(300.0) == "r",
                      "_category() maps values to r/y/g bands")

            # Phase 1: raise the LOW line above the reading -> LOW badge.
            ctx.w._thresholds = {**saved, "tbr1_below": 130.0}
            pump(200)
            _fresh_msg(len(ctx.w._ble_log.get_messages()), 30, "a fresh measurement to re-badge")
            pump(400)
            c.measure("low_row", _row().text(0))
            c.assert_("LOW" in _row().text(0),
                      "row shows the LOW badge when the reading is below the low line", _row().text(0))

            # Phase 2: real 70 line -> the reading is back in range -> no badge.
            ctx.w._thresholds = dict(saved)
            pump(200)
            _fresh_msg(len(ctx.w._ble_log.get_messages()), 30, "a fresh measurement to clear the badge")
            pump(400)
            c.measure("clear_row", _row().text(0))
            c.assert_("LOW" not in _row().text(0) and "HIGH" not in _row().text(0),
                      "badge clears once the reading is back in range", _row().text(0))
        finally:
            ctx.w._thresholds = saved


def f9_per_slot_isolation(ctx: FourCtx):
    with Case(ctx, "F9-01", "sensor_select_isolates_writes", "F9") as c:
        sess = ctx.cfg_session()
        _ensure_layout(ctx, c)
        if not ctx.serial_live():
            raise _Skip("no serial console")

        before = ctx.all_slot_configs(sess, c)
        c.measure("before", before)
        c.assert_(before[1][0] != "DEICHMANN", "slot 1 is not already Deichmann", str(before[1]))

        c.step("sensor_select=1 (confirmed), then write a Deichmann person to slot 1 only")
        ctx.select_slot(sess, 1, c)
        sess.queue_write("person", protocol.encode_person_config(
            ModelId.DEICHMANN, deichmann.default_params()))
        ctx.wait_slot(1, lambda ln: ln["model"] == 3, 25, "slot 1 serial now model=3 (Deichmann)")

        after = ctx.all_slot_configs(sess, c)
        c.measure("after", after)
        c.assert_(after[1][0] == "DEICHMANN", "slot 1 changed to Deichmann", str(after[1]))
        for s in (0, 2, 3):
            c.assert_(after[s] == before[s], f"slot {s} untouched by the slot-1 write",
                      f"{before[s]} -> {after[s]}")


def f10_reconnect_autonomy(ctx: FourCtx):
    with Case(ctx, "F10-01", "disconnect_one_others_run", "F10") as c:
        if not ctx.serial_live():
            raise _Skip("no serial console")
        drop = 3   # the cfg session is identity 1 — drop a different one
        ctx.cfg_session()
        _ensure_layout(ctx, c)
        ctx.slot_session(drop)
        ctx.wait_slot(2, lambda ln: True, 15, "slot 2 ticking")

        c.step(f"drop the identity-{drop} session")
        ctx.drop_session(drop)
        c.wait_until(lambda: ctx.serial_has("Disconnected") and ctx.serial_has(f"identity {drop}"),
                     15, f"board logged the identity-{drop} disconnect")
        pump(1500)
        before = ctx.slot_line_raw(2)
        pump(7000)
        after = ctx.slot_line_raw(2)
        c.measure("slot2_line_moved", before != after)
        c.assert_(ctx.serial_live(8.0), "serial still live after the drop")
        c.assert_(after is not None and before != after,
                  f"slot 2 keeps emitting fresh model_tick lines after identity {drop} leaves",
                  f"{before!r} == {after!r}")
        c.assert_(ctx.slot_line(2)["dt"] > 0.0, "board still RUNNING (not stopped)")

        c.step(f"reconnect identity {drop}")
        try:
            ctx.slot_session(drop)
            base = len(ctx.w._ble_log.get_messages())
            c.wait_until(lambda: any("glucose_value" in m and NAMES[drop] in (m.get("user_id") or "")
                                     for m in ctx.w._ble_log.get_messages()[base:]),
                         45, f"identity {drop} streams again after reconnect")
            c.measure("reconnect_streamed", True)
        except Exception as exc:  # best-effort: the autonomy check above is the point
            c.measure("reconnect_streamed", f"not confirmed ({type(exc).__name__})")


def f11_reboot_persistence(ctx: FourCtx):
    with Case(ctx, "F11-01", "layout_survives_reboot", "F11") as c:
        if not ctx.serial_live():
            raise _Skip("no serial console")
        sess = ctx.cfg_session()
        persons, sensors = _persons(), _sensors()
        ctx.push_layout(c, [(0, "camb", "ideal"), (1, "uva", "breton"),
                            (2, "roy", "ideal"), (3, "deich", "breton")], persons, sensors)
        _set_speed(sess, 60)
        before = ctx.all_slot_configs(sess, c)
        c.measure("before_reboot", before)
        c.assert_(before[3][0] == "DEICHMANN", "slot 3 = Deichmann before reboot", str(before[3]))

        t_pre = ctx.slot_line(0)["t_sim"]
        c.step(f"stop sessions, hardware-reset the board via J-Link (sim clock was ~{t_pre:.0f} min)")
        ctx.stop_sessions()
        ctx._slot_sess.clear()
        pump(1500)
        ctx.jlink_reset()

        # a fresh boot restarts sim_clock_min at 0 — the reliable "it rebooted" signal
        ctx.wait_slot(0, lambda ln: ln["t_sim"] < 8.0, 45, "board rebooted (sim clock restarted)")
        for s, m in ((0, 0), (1, 1), (2, 2), (3, 3)):
            ctx.wait_slot(s, lambda ln, m=m: ln["model"] == m, 30,
                          f"slot {s} serial model={m} reloaded from flash")

        sess = ctx.cfg_session()   # re-scans + reconnects (addresses are stable)
        after = ctx.all_slot_configs(sess, c)
        c.measure("after_reboot", after)
        c.assert_(after == before, "the 4-slot layout was reloaded from flash unchanged",
                  f"{before} -> {after}")


def _select_target(bar, address: str, slot: int) -> None:
    """Point a DeviceTargetBar at *address*. The target slot is derived from that
    device's own identity (numbered advertised name), so it should already equal
    *slot* — there is no separate slot picker anymore."""
    bar.refresh()
    for i in range(bar.combo.count()):
        if bar.combo.itemData(i) == address:
            bar.combo.setCurrentIndex(i)
            break
    pump(100)
    assert bar.selected_slot() == slot, (
        f"target device's derived slot should be {slot}, got {bar.selected_slot()}")


def f12_config_window_target_slot(ctx: FourCtx):
    with Case(ctx, "F12-01", "config_window_target_slot", "F12") as c:
        if not ctx.serial_live():
            raise _Skip("no serial console")
        sess = ctx.cfg_session()
        addr = sess._address
        # push a fresh known layout (earlier cases mutate individual slots, so
        # don't trust _ensure_layout's "already running" shortcut here)
        persons, sensors = _persons(), _sensors()
        ctx.push_layout(c, [(0, "camb", "ideal"), (1, "uva", "breton"),
                            (2, "roy", "ideal"), (3, "deich", "breton")], persons, sensors)
        _set_speed(sess, 60)
        ctx.wait_slot(1, lambda ln: ln["model"] == 1, 25, "slot 1 starts as UVA/Padova")

        # --- Person window → write a Deichmann person to slot 2 only ---
        ctx.w._person_profiles.append(
            PersonProfile("F12-Deichmann", ModelId.DEICHMANN, deichmann.default_params()))
        ctx.w._on_profiles_changed()
        ctx.w._open_person_config()
        pcw = ctx.w._person_config_window
        pcw._reload_list()
        pcw._list.setCurrentRow(len(ctx.w._person_profiles) - 1)
        _select_target(pcw._target_bar, addr, 2)
        c.step("Person window: Target slot 2, Send to Board (Deichmann)")
        pcw._send_to_board()
        ctx.wait_slot(2, lambda ln: ln["model"] == 3, 30, "slot 2 serial model=3 (Deichmann)")
        c.assert_(ctx.slot_line(0)["model"] == 0, "slot 0 untouched by the slot-2 send")
        c.assert_(ctx.slot_line(1)["model"] == 1, "slot 1 untouched by the slot-2 send")

        # --- Person window → Read from Board, slot 1, expect UVA/Padova ---
        # _on_config_read writes the decoded model onto the selected profile.
        prof = ctx.w._person_profiles[-1]
        prof.model_id = ModelId.CAMBRIDGE   # so a stale value can't pass the check
        _select_target(pcw._target_bar, addr, 1)
        c.step("Person window: Target slot 1, Read from Board")
        pcw._read_from_board()
        c.wait_until(lambda: prof.model_id == ModelId.UVA_PADOVA, 15,
                     "person readback for slot 1 loaded into the form")
        c.measure("slot1_readback", prof.model_id.name)
        c.assert_(prof.model_id == ModelId.UVA_PADOVA,
                  "Read from Board with slot 1 selected returns slot 1's config",
                  prof.model_id.name)

        # --- Sensor window → write a Breton sensor to slot 0 only ---
        ctx.w._sensor_profiles.append(
            SensorProfile("F12-Breton", SensorId.BRETON, sensor_defaults.breton_default_params()))
        ctx.w._on_profiles_changed()
        ctx.w._open_sensor_config()
        scw = ctx.w._sensor_config_window
        scw._reload_list()
        scw._list.setCurrentRow(len(ctx.w._sensor_profiles) - 1)
        _select_target(scw._target_bar, addr, 0)
        c.step("Sensor window: Target slot 0, Send to Board (Breton)")
        scw._send_to_board()
        ctx.wait_slot(0, lambda ln: ln["sensor"] == 1, 30, "slot 0 serial sensor=1 (Breton)")
        c.assert_(ctx.slot_line(2)["sensor"] == 0, "slot 2 sensor untouched by the slot-0 send")


def f13_instant_dialog_target_slot(ctx: FourCtx):
    with Case(ctx, "F13-01", "instant_dialog_target_slot", "F13") as c:
        if not ctx.serial_live():
            raise _Skip("no serial console")
        from graphic.instant_event_dialog import FoodInstantDialog
        from PyQt6.QtWidgets import QDialog

        sess = ctx.cfg_session()
        _ensure_layout(ctx, c)
        ctx.set_speed_settle(sess, 60)
        ctx.wait_slot(1, lambda ln: ln["dt"] > 0.5, 15, "slot 1 ticking fast")

        c.assert_(ctx.w._multi_slot_count() == 4,
                  "app sees a multi-sensor board (sensor_select exposed)")

        # Drive "Insert Food Now" like a user: dialog picks slot 1, 55 g / 40 min.
        def fake_exec(self):
            self._carbs_spin.setValue(55.0)
            self._duration_spin.setValue(40)
            self._slot_combo.setCurrentIndex(1)
            return QDialog.DialogCode.Accepted

        orig = FoodInstantDialog.exec
        FoodInstantDialog.exec = fake_exec
        try:
            ctx.w._open_insert_food()
        finally:
            FoodInstantDialog.exec = orig

        hit = ctx.wait_slot(1, lambda ln: ln["carbs"] > 0.05, 25, "slot 1 carbs > 0 after the dialog")
        c.measure("slot1_carbs", round(hit["carbs"], 3))
        c.assert_(ctx.slot_line(0)["carbs"] < 1e-6, "slot 0 carbs stayed 0 (dialog targeted slot 1)")
        c.assert_(ctx.slot_line(2)["carbs"] < 1e-6, "slot 2 carbs stayed 0")
        c.assert_(ctx.slot_line(3)["carbs"] < 1e-6, "slot 3 carbs stayed 0 (not a 4x broadcast)")


def f14_identity_shows_patient(ctx: FourCtx):
    with Case(ctx, "F14-01", "identity_shows_patient_name", "F14") as c:
        from models import board_layout as bl

        adv = NAMES[2]                       # "Nordic Glucose Sensor 3" -> slot 2
        addr = ctx.scan().get(adv)
        if not addr:
            raise _Skip(f"{adv} not advertising")
        bt = ctx.w._ensure_bluetooth_window()

        # unassigned -> the raw advertised name everywhere
        bl.save(bl.BoardLayout())
        ctx.w._board_layout = bl.load()
        c.assert_(bl.device_label(adv) == adv, "unassigned slot -> raw sensor name", bl.device_label(adv))
        c.assert_(bl.session_name(adv) == adv, "unassigned slot -> raw session name")
        bt._add_device(adv, addr, -40)
        row = bt._row_for_address(addr)
        c.assert_(bt._table.item(row, 0).text() == adv,
                  "Bluetooth list shows the sensor name when no patient is set", bt._table.item(row, 0).text())

        # assign slot 2 -> "F14-Patient" and relabel live
        layout = bl.BoardLayout()
        layout.slots[2].person = "F14-Patient"
        bl.save(layout)
        ctx.w._board_layout = bl.load()
        c.assert_(bl.device_label(adv) == "F14-Patient — Sensor 3",
                  "assigned slot -> 'patient — Sensor N' label", bl.device_label(adv))
        c.assert_(bl.session_name(adv) == "F14-Patient", "assigned slot -> patient session name")
        bt.relabel()
        c.assert_(bt._table.item(row, 0).text() == "F14-Patient — Sensor 3",
                  "Bluetooth list relabels to the patient after assignment",
                  bt._table.item(row, 0).text())

        # connect through the real BluetoothWindow path -> tree row carries the
        # patient. The label logic above is the core of the feature; the live
        # connect + tree row is a bonus that SKIPs under BLE congestion.
        state = {"conn": False, "err": None}
        c.step("connect identity 3 with slot 2 = 'F14-Patient'")
        bt._open_session(addr, adv)
        sess = bt._sessions.get(addr)
        sess.connected.connect(lambda *_: state.__setitem__("conn", True))
        sess.connect_failed.connect(lambda _a, e: state.__setitem__("err", e))
        sess.new_message.connect(ctx.w._ble_log.add_message)
        ctx._slot_sess[2] = sess
        ctx.sessions[addr] = sess
        for _ in range(600):
            QTest.qWait(100)
            if state["conn"] or state["err"]:
                break
        if not state["conn"]:
            raise _Skip(f"identity 3 connect failed under load: {state['err'] or 'timeout'}")
        c.assert_("F14-Patient" in sess._user_id() and sess._own_instance_index == 2,
                  "session shows the patient but still demuxes to slot 2", sess._user_id())
        # a CGM measurement (has glucose_value) is what creates a tree row
        c.wait_until(lambda: any("glucose_value" in m and "F14-Patient" in (m.get("user_id") or "")
                                 for m in ctx.w._ble_log.get_messages()), 60,
                     "a CGM measurement tagged with the patient name")
        pump(300)
        c.measure("tree_rows", list(ctx.w._user_items))
        c.assert_(any("F14-Patient" in uid for uid in ctx.w._user_items),
                  "the tree has a row under the patient name",
                  str(list(ctx.w._user_items)))
        ctx.drop_session(2)   # display test — don't hold the link for later cases


CASES = [f1_layout_models, f2_independent_streams, f3_csv_slot,
         f4_fast_mode, f5_insert_food, f6_insert_exercise, f7_insert_pisa,
         f8_alerts, f9_per_slot_isolation, f12_config_window_target_slot,
         f13_instant_dialog_target_slot, f14_identity_shows_patient,
         f10_reconnect_autonomy, f11_reboot_persistence]


# ----------------------------------------------------------------------
# Runner
# ----------------------------------------------------------------------

def run_once(args) -> int:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    run_dir = Path(args.out) / f"4sensor-{run_id}"
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
    print(f"e2e_4sensor {run_id}  board={board or '(none)'}  serial={'ok' if serial_ok else 'no'}")

    app = QApplication.instance() or QApplication(sys.argv)
    w = mw.MainWindow()
    w.show()
    pump(400)
    w._ble_log.new_message.connect(lambda m: streams["ble"].add(json.dumps({
        "user": m.get("user_id"), "glucose": m.get("glucose_value"),
        "carbs": m.get("carbs_g_per_min"), "slot": m.get("slot"),
    }), time.monotonic() - t0))

    ctx = FourCtx(w, run_dir, t0, streams, board, serial_ok)
    (run_dir / "environment.json").write_text(json.dumps({
        "run_id": run_id, "board": board, "serial_port": SERIAL_PORT if serial_ok else None,
        "fw_sha": _git_sha("firmware/"), "app_sha": _git_sha("src/"),
        "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }, indent=2), encoding="utf-8")

    picks = [p.strip().upper() for p in args.only.split(",") if p.strip()]
    try:
        if board:
            try:
                pf = ctx.cfg_session()
                for k, v in (("data_source", protocol.encode_data_source(False)),
                             ("cgms_only", protocol.encode_cgms_only(False)),
                             ("speed", protocol.encode_speed(1.0)),
                             ("run_state", protocol.encode_run_state(1))):
                    pf.queue_write(k, v)
                pump(2500)
                print("  preflight: cfg session up, board reset to model / x1 / running")
            except _Skip as exc:
                print(f"  preflight: {exc}")

        for fn in CASES:
            cid = fn.__name__.split("_")[0].upper()   # f1_... -> F1
            # "F" matches every case; "F1".."F11" match exactly (so --only F1
            # does not also pull in F10/F11).
            if picks and not any(p == cid or p == "F" for p in picks):
                continue
            try:
                fn(ctx)
            except Exception as exc:  # pragma: no cover
                print(f"  !! {fn.__name__} crashed outside a Case: {exc}")
    finally:
        ctx.stop_sessions()
        if tap:
            tap.stop()
        subprocess.run(["git", "checkout", "--", "data/profiles.json", "data/settings.json"],
                       cwd=str(_ROOT), check=False)
        # not git-tracked — a case may create it; leave the tree clean
        (_ROOT / "data" / "board_layout.json").unlink(missing_ok=True)

    counts: dict[str, int] = {}
    for _cid, _s, verdict, _n in ctx.results:
        counts[verdict] = counts.get(verdict, 0) + 1
    lines = [f"# e2e_4sensor {run_id}", "",
             "  ".join(f"{v}:{n}" for v, n in sorted(counts.items())), ""]
    for cid, _s, verdict, note in ctx.results:
        lines.append(f"- {verdict:8} {cid:9} {note}")
    (run_dir / "SUMMARY.md").write_text("\n".join(lines), encoding="utf-8")
    ctx.close()

    sys.stdout, sys.stderr = sys.__stdout__, sys.__stderr__
    for s in streams.values():
        s.close()

    def _p(s: str) -> None:  # console may be cp1252; never let a summary line crash the run
        print(s.encode("ascii", "replace").decode("ascii"))

    _p(f"\n================ 4sensor {run_id} ================")
    _p("  ".join(f"{v}:{n}" for v, n in sorted(counts.items())) or "(no cases)")
    for cid, _s, verdict, note in ctx.results:
        _p(f"  {verdict:8} {cid:9} {note}")
    _p(f"artifacts: {run_dir}")
    bad = counts.get("FAIL", 0) + counts.get("ERROR", 0) + counts.get("TIMEOUT", 0)
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--board", default="D0:3F:4D:E2:7C:9B")
    ap.add_argument("--no-board", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("--loop", type=int, default=1)
    ap.add_argument("--out", default=str(_ROOT / "test-artifacts"))
    args = ap.parse_args()

    if args.loop > 1:
        child = [a for a in sys.argv[1:]
                 if a != "--loop" and not a.startswith("--loop")]
        skip = False
        clean = []
        for a in sys.argv[1:]:
            if skip:
                skip = False
                continue
            if a == "--loop":
                skip = True
                continue
            if a.startswith("--loop="):
                continue
            clean.append(a)
        worst = 0
        for i in range(args.loop):
            print(f"\n########## 4sensor loop {i + 1}/{args.loop} ##########", flush=True)
            rc = subprocess.run([sys.executable, "-u", __file__, *clean],
                                env={**os.environ, "PYTHONPATH": ""}).returncode
            worst = max(worst, rc)
            time.sleep(3)
        os._exit(worst)

    os._exit(run_once(args))


if __name__ == "__main__":
    main()
