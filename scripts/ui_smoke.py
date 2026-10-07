"""End-to-end UI smoke tests: drive the real PyQt app with synthetic clicks.

Scenarios
---------
  A  CSV on board   : connect board -> CSV Analysis (open + slide + assign) ->
                      Send CSV to Board -> x60 speed -> Start -> check graph/stats
  B  model on board : model patient -> x60 speed -> Start -> check received +
                      expected lines + stats
  C  model + food   : Model Only, Cambridge -> x60 -> Start -> Insert Food Now
                      -> carbs line rises, glucose responds, stats populate
  D  model + exercise: Model Only, Deichmann -> x60 -> Start -> Insert Exercise
                      Now -> exercise line rises, stats populate
  E  model + PISA   : Model Only, Cambridge -> x60 -> Start -> Insert PISA Now
                      -> glucose dips (false low) + recovers, interval shaded
  F  rolling window : run, shrink the graph time-window, check only the recent
                      slice is shown, then "Entire run" restores the full span

A and B need a board; they are SKIPPED (not failed) if it will not connect.
The speed multiplier is set via the Configuration-window slider.

Screenshots + a PASS/SKIP/FAIL line per scenario are written to --out.

Usage:  python scripts/ui_smoke.py [--board D0:3F:4D:E2:7C:9B] [--no-board]
        [--only A,C] [--out <dir>]
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
os.chdir(_ROOT)

import userdata_guard
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

import gui.csv_analysis_window as cav
import gui.main_window as mw
from models import app_settings, cambridge, deichmann
from models.types import ModelId, PersonProfile
from services.ble_session import BleSession

DEFAULT_BOARD = "D0:3F:4D:E2:7C:9B"
DEXCOM_CSV = "dataset/Dexcom_001.csv"

_results: list[tuple[str, str, str]] = []  # (scenario, verdict, note)
_out = _ROOT / "scratchpad_ui"


def pump(ms: int) -> None:
    QTest.qWait(ms)


def wait_until(cond, timeout_ms: int, desc: str) -> bool:
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        if cond():
            return True
        QTest.qWait(100)
    print(f"    ! timed out waiting for: {desc}")
    return False


def shot(widget, name: str) -> None:
    _out.mkdir(exist_ok=True)
    widget.grab().save(str(_out / f"{name}.png"))
    print(f"    shot {name}.png")


def record(scenario: str, ok: bool, note: str = "", skipped: bool = False) -> None:
    verdict = "SKIP" if skipped else ("PASS" if ok else "FAIL")
    _results.append((scenario, verdict, note))
    print(f"  == {scenario}: {verdict} {note}")


# ----------------------------------------------------------------------
# Board connection (bypasses the scan UI, uses the real BleSession stack)
# ----------------------------------------------------------------------


def connect_board(w, addr: str, timeout_ms: int = 45000):
    bt = w._ensure_bluetooth_window()
    if addr in bt.sessions():
        return bt.sessions()[addr]
    state = {"done": False, "ok": False, "err": ""}
    sess = BleSession(addr, "Nordic Glucose Sensor", bt)
    sess.connected.connect(lambda *_: state.update(done=True, ok=True))
    sess.connect_failed.connect(lambda _a, e: state.update(done=True, ok=False, err=e))
    sess.new_message.connect(w._ble_log.add_message)
    bt._sessions[addr] = sess
    bt._names[addr] = "Nordic Glucose Sensor"
    print(f"    connecting to {addr} (pairing may take ~10 s)…")
    sess.start()
    if not wait_until(lambda: state["done"], timeout_ms, "board connect"):
        return None
    if not state["ok"]:
        print(f"    connect failed: {state['err']}")
        bt._sessions.pop(addr, None)
        return None
    print("    board connected")
    return sess


def select_first_tree_user(w) -> bool:
    if not wait_until(lambda: bool(w.tabs.tab_keys()), 20000, "first CGM message"):
        return False
    w.tabs.select(w.tabs.tab_keys()[0])
    pump(200)
    return True


def graph_ok(w, min_points: int = 3) -> tuple[bool, str]:
    ys = [
        v
        for v in (
            w.sensors.current_page().graph.buf.graph_y
            + w.sensors.current_page().graph.buf.expected_y
        )
        if v == v
    ]
    n = len(ys)
    spread = (max(ys) - min(ys)) if ys else 0.0
    plausible = all(20.0 <= v <= 500.0 for v in ys) if ys else False
    mean_lbl = w.sensors.current_page().stats.labels["mean"].text()
    stats_ok = mean_lbl not in ("—", "")
    ok = n >= min_points and plausible and stats_ok
    return ok, f"points={n} spread={spread:.1f} mean={mean_lbl} plausible={plausible}"


# ----------------------------------------------------------------------
# Scenarios
# ----------------------------------------------------------------------


def scenario_A_csv_on_board(w, board_addr):
    print("\n[A] CSV on board")
    sess = connect_board(w, board_addr)
    if sess is None:
        record("A CSV-on-board", False, "board unavailable", skipped=True)
        return

    # CSV Analysis: open window, load CSV, slide, assign to a CSV patient
    csv_person = PersonProfile(name="CSV Test", model_id=ModelId.CAMBRIDGE)
    w.state.person_profiles.append(csv_person)
    w._on_profiles_changed()

    QTest.mouseClick(w.csv_analysis_btn, Qt.MouseButton.LeftButton)
    pump(200)
    cw = w._csv_analysis_window
    cav.QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (DEXCOM_CSV, ""))
    cw._open_csv()
    pump(300)
    if cw._slider.isEnabled():
        cw._slider.setValue(int(cw._slider.maximum() * 0.1))
        pump(200)
    cav.QInputDialog.getItem = staticmethod(lambda *a, **k: ("CSV Test", True))
    QTest.mouseClick(cw._assign_btn, Qt.MouseButton.LeftButton)
    pump(300)
    print(f"    assigned: {cw._assign_status.text()}")

    # Data source + Send CSV now live in the Person Configuration window (issue 16).
    w.windows.open("person")
    pcw = w.windows.get("person")
    pump(200)
    row = next(i for i in range(pcw._list.count()) if pcw._list.item(i).text() == "CSV Test")
    pcw._list.setCurrentRow(row)
    pump(200)
    if not pcw._src_csv_radio.isChecked():
        record("A CSV-on-board", False, "person config did not switch to CSV")
        return

    # Send CSV to the board
    up = {"done": False, "ok": False, "msg": ""}
    sess.csv_upload_finished.connect(lambda _a, ok, m: up.update(done=True, ok=ok, msg=m))
    pcw._send_csv_to_board()
    if not wait_until(lambda: up["done"], 30000, "CSV upload finished"):
        record("A CSV-on-board", False, "CSV upload never finished")
        return

    cfg = w._configuration_window
    print(f"    upload: ok={up['ok']} {up['msg']}")
    if not up["ok"]:
        record("A CSV-on-board", False, f"upload failed: {up['msg']}")
        return

    # Fast mode (speed multiplier) + Start, then watch the stream
    cfg.speed_combo.setCurrentIndex(cfg.speed_combo.findData(60.0))  # x60 speed
    pump(200)
    QTest.mouseClick(w._run.start_pause_btn, Qt.MouseButton.LeftButton)
    pump(500)
    select_first_tree_user(w)
    wait_until(
        lambda: len(w.sensors.current_page().graph.buf.graph_y) >= 4,
        90000,  # Start first writes the profiles to the board
        ">=4 CGM points from board",
    )
    ok, note = graph_ok(w, min_points=4)
    # CSV values must not be the model's flat 100 line
    flat = (
        len({round(v) for v in w.sensors.current_page().graph.buf.graph_y}) <= 1
        if w.sensors.current_page().graph.buf.graph_y
        else True
    )
    ok = ok and not flat
    shot(w, "A_csv_on_board")
    QTest.mouseClick(w._run.stop_btn, Qt.MouseButton.LeftButton)
    pump(300)
    record("A CSV-on-board", ok, note + f" flat={flat}")


def scenario_B_model_on_board(w, board_addr):
    print("\n[B] model on board")
    sess = connect_board(w, board_addr)
    if sess is None:
        record("B model-on-board", False, "board unavailable", skipped=True)
        return

    person = PersonProfile(
        name="Model Test", model_id=ModelId.CAMBRIDGE, params=cambridge.default_params()
    )
    w.state.person_profiles.append(person)
    w._on_profiles_changed()
    cfg = w._configuration_window
    idx = next(
        i
        for i in range(cfg.person_combo.count())
        if getattr(cfg.person_combo.itemData(i), "name", None) == "Model Test"
    )
    cfg.person_combo.setCurrentIndex(idx)
    pump(200)

    # push person config to the board so it runs the same model
    from api import protocol
    from gui.device_target import restart_board

    sess.queue_write("person", protocol.encode_person_config(person.model_id, person.params))
    sess.queue_write("data_source", protocol.encode_data_source(False))
    restart_board(sess)
    pump(1000)

    cfg.speed_combo.setCurrentIndex(cfg.speed_combo.findData(60.0))  # x60 speed
    pump(200)
    QTest.mouseClick(w._run.start_pause_btn, Qt.MouseButton.LeftButton)
    pump(500)
    select_first_tree_user(w)
    wait_until(
        lambda: (
            len(w.sensors.current_page().graph.buf.graph_y) >= 3
            and len(w.sensors.current_page().graph.buf.expected_y) >= 5
        ),
        40000,
        "received + expected points",
    )
    recv_pts, exp_pts = (
        len(w.sensors.current_page().graph.buf.graph_y),
        len(w.sensors.current_page().graph.buf.expected_y),
    )  # capture before Stop clears them
    ok, note = graph_ok(w, min_points=3)
    ok = ok and exp_pts >= 5  # the local "expected" model line is running in parallel
    shot(w, "B_model_on_board")
    QTest.mouseClick(w._run.stop_btn, Qt.MouseButton.LeftButton)
    pump(300)
    # a default Cambridge patient with no meals sits at its steady state, so a
    # near-flat line here is the *correct* model output, not a stuck one.
    record(
        "B model-on-board",
        ok,
        note + f" recv_pts={recv_pts} expected_pts={exp_pts} (flat=steady-state)",
    )


def _model_only_run(w, person, name, do_after, checks, tag):
    """Shared Model-Only driver for the food/exercise scenarios."""
    w.state.person_profiles.append(person)
    w._on_profiles_changed()
    cfg = w._configuration_window
    idx = next(
        i
        for i in range(cfg.person_combo.count())
        if getattr(cfg.person_combo.itemData(i), "name", None) == person.name
    )
    cfg.person_combo.setCurrentIndex(idx)
    pump(200)
    cfg.model_only_check.setChecked(True)
    cfg.speed_combo.setCurrentIndex(cfg.speed_combo.findData(60.0))  # x60 speed
    pump(200)
    QTest.mouseClick(w._run.start_pause_btn, Qt.MouseButton.LeftButton)
    wait_until(
        lambda: len(w.sensors.current_page().graph.buf.graph_y) >= 5, 15000, "baseline model points"
    )
    base_glucose = list(w.sensors.current_page().graph.buf.graph_y)
    do_after(w)
    wait_until(
        lambda: len(w.sensors.current_page().graph.buf.graph_y) >= len(base_glucose) + 12,
        20000,
        "post-event points",
    )
    ok, note = checks(w, base_glucose)
    shot(w, tag)
    QTest.mouseClick(w._run.stop_btn, Qt.MouseButton.LeftButton)
    pump(300)
    cfg.model_only_check.setChecked(False)
    return ok, note


def scenario_C_model_food(w):
    print("\n[C] model + insert food (Model Only, Cambridge)")

    def insert_food(w):
        w.sensors.current_page().commands.send_food(80.0, 10)
        print("    inserted 80 g / 10 min")

    def checks(w, base):
        carbs_peak = (
            max(w.sensors.current_page().graph.buf.food_ex_carbs_y)
            if w.sensors.current_page().graph.buf.food_ex_carbs_y
            else 0.0
        )
        rise = (
            (max(w.sensors.current_page().graph.buf.graph_y) - max(base))
            if (w.sensors.current_page().graph.buf.graph_y and base)
            else 0.0
        )
        mean_ok = w.sensors.current_page().stats.labels["mean"].text() not in ("—", "")
        ok = carbs_peak > 0.0 and rise > 2.0 and mean_ok
        mean_txt = w.sensors.current_page().stats.labels["mean"].text()
        return (
            ok,
            f"carbs_peak={carbs_peak:.2f} glucose_rise={rise:.1f} mean={mean_txt}",
        )

    person = PersonProfile(
        name="Food Test", model_id=ModelId.CAMBRIDGE, params=cambridge.default_params()
    )
    ok, note = _model_only_run(w, person, "Food Test", insert_food, checks, "C_model_food")
    record("C model+food", ok, note)


def scenario_D_model_exercise(w):
    print("\n[D] model + insert exercise (Model Only, Deichmann)")

    def insert_ex(w):
        w.sensors.current_page().commands.send_exercise(30, 70.0)
        print("    inserted 30 min / 70 %")

    def checks(w, base):
        ex_peak = (
            max(w.sensors.current_page().graph.buf.food_ex_exercise_y)
            if w.sensors.current_page().graph.buf.food_ex_exercise_y
            else 0.0
        )
        moved = (
            abs(max(w.sensors.current_page().graph.buf.graph_y) - max(base))
            if (w.sensors.current_page().graph.buf.graph_y and base)
            else 0.0
        )
        mean_ok = w.sensors.current_page().stats.labels["mean"].text() not in ("—", "")
        ok = ex_peak > 0.0 and mean_ok
        mean_txt = w.sensors.current_page().stats.labels["mean"].text()
        return (
            ok,
            f"exercise_peak={ex_peak:.1f} glucose_delta={moved:.1f} mean={mean_txt}",
        )

    person = PersonProfile(
        name="Exercise Test", model_id=ModelId.DEICHMANN, params=deichmann.default_params()
    )
    ok, note = _model_only_run(w, person, "Exercise Test", insert_ex, checks, "D_model_exercise")
    record("D model+exercise", ok, note)


def scenario_E_model_pisa(w):
    print("\n[E] model + insert PISA (Model Only, Cambridge)")

    def insert_pisa(w):
        w.sensors.current_page().commands.send_pisa(15, 30.0)
        print("    inserted PISA 30 % / 15 min")

    def checks(w, base):
        base_lvl = sum(base) / len(base) if base else 0.0
        post = w.sensors.current_page().graph.buf.graph_y[len(base) :]
        trough = min(post) if post else base_lvl
        recovered = post[-1] if post else base_lvl
        spans = len(w.sensors.current_page().graph.buf.pisa_spans)
        patches = len(w.sensors.current_page().graph.pisa_patches)
        ok = (
            (trough < base_lvl - 8.0) and (recovered > trough + 3.0) and spans == 1 and patches == 1
        )
        return ok, (
            f"base={base_lvl:.1f} trough={trough:.1f} recovered={recovered:.1f} "
            f"spans={spans} patches={patches}"
        )

    person = PersonProfile(
        name="PISA Test", model_id=ModelId.CAMBRIDGE, params=cambridge.default_params()
    )
    ok, note = _model_only_run(w, person, "PISA Test", insert_pisa, checks, "E_model_pisa")
    record("E model+PISA", ok, note)


def scenario_F_view_window(w):
    print("\n[F] rolling graph window")
    person = PersonProfile(
        name="Window Test", model_id=ModelId.CAMBRIDGE, params=cambridge.default_params()
    )
    w.state.person_profiles.append(person)
    w._on_profiles_changed()
    cfg = w._configuration_window
    idx = next(
        i
        for i in range(cfg.person_combo.count())
        if getattr(cfg.person_combo.itemData(i), "name", None) == person.name
    )
    cfg.person_combo.setCurrentIndex(idx)
    cfg.model_only_check.setChecked(True)
    cfg.speed_combo.setCurrentIndex(cfg.speed_combo.findData(60.0))
    pump(200)
    QTest.mouseClick(w._run.start_pause_btn, Qt.MouseButton.LeftButton)
    wait_until(lambda: len(w.sensors.current_page().graph.buf.graph_y) >= 14, 20000, ">=14 points")
    total_pts = len(w.sensors.current_page().graph.buf.graph_y)
    full_span = (
        w.sensors.current_page().graph.visible_xlim[1]
        - w.sensors.current_page().graph.visible_xlim[0]
    )

    # shrink the window to 5 s of wall time
    app_settings.save_pref("view_window_s", 5)
    w._on_view_window_changed()
    pump(200)
    win_span = (
        w.sensors.current_page().graph.visible_xlim[1]
        - w.sensors.current_page().graph.visible_xlim[0]
    )
    visible_pts = len(
        w.sensors.current_page().graph.in_view(
            w.sensors.current_page().graph.buf.graph_x, w.sensors.current_page().graph.buf.graph_y
        )
    )

    # back to entire run
    app_settings.save_pref("view_window_s", 0)
    w._on_view_window_changed()
    pump(200)
    restored_span = (
        w.sensors.current_page().graph.visible_xlim[1]
        - w.sensors.current_page().graph.visible_xlim[0]
    )

    shot(w, "F_view_window")
    QTest.mouseClick(w._run.stop_btn, Qt.MouseButton.LeftButton)
    pump(200)
    cfg.model_only_check.setChecked(False)
    app_settings.save_pref("view_window_s", 3600)  # restore default
    w.state.view_window_s = 3600.0

    ok = win_span <= 6.0 and visible_pts < total_pts and restored_span >= full_span - 1.0
    record(
        "F view-window",
        ok,
        f"full_span={full_span:.0f}s windowed={win_span:.1f}s "
        f"visible={visible_pts}/{total_pts} restored={restored_span:.0f}s",
    )


# ----------------------------------------------------------------------


def main() -> int:
    global _out
    saved_userdata = userdata_guard.snapshot()
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--board", default=DEFAULT_BOARD)
    ap.add_argument("--no-board", action="store_true")
    ap.add_argument("--only", default="A,B,C,D,E,F")
    ap.add_argument("--out", default=str(_ROOT / "scratchpad_ui"))
    args = ap.parse_args()
    _out = Path(args.out)
    only = {s.strip().upper() for s in args.only.split(",")}
    board = None if args.no_board else args.board

    app = QApplication(sys.argv)
    w = mw.MainWindow()
    w.show()
    pump(400)

    try:
        if "A" in only:
            (
                scenario_A_csv_on_board(w, board)
                if board
                else record("A CSV-on-board", False, "--no-board", skipped=True)
            )
        if "B" in only:
            (
                scenario_B_model_on_board(w, board)
                if board
                else record("B model-on-board", False, "--no-board", skipped=True)
            )
        if "C" in only:
            scenario_C_model_food(w)
        if "D" in only:
            scenario_D_model_exercise(w)
        if "E" in only:
            scenario_E_model_pisa(w)
        if "F" in only:
            scenario_F_view_window(w)
    finally:
        # the assign/config/speed/window steps persist to data/ — restore it
        userdata_guard.restore(saved_userdata)
        try:
            if w.windows.bluetooth is not None:
                w.windows.bluetooth.stop_all_sessions()
        except Exception:
            pass

    print("\n================ SUMMARY ================")
    for name, verdict, note in _results:
        print(f"  {verdict:4}  {name:22}  {note}")
    failed = [r for r in _results if r[1] == "FAIL"]
    app.processEvents()
    os._exit(1 if failed else 0)


if __name__ == "__main__":
    main()
