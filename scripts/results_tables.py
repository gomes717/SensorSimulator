#!/usr/bin/env python3
"""Reproducible results tables for the thesis (issue 15).

Runs a fixed 24 h scenario (a standard 4-meal day) and tabulates the clinical
range metrics per model, and — with a board — per model x sensor-noise model,
with MARD of the noisy stream vs the noiseless model reference.

  python scripts/results_tables.py                 # offline: 4-model table (no board)
  python scripts/results_tables.py --board <addr>  # + per-noise table + MARD
  python scripts/results_tables.py --out results/  # write results/*.md + *.csv

Offline rows are deterministic. The board rows depend on the sensor noise RNG,
so re-run and average if you need tight figures (see docs/FEATURE_IDEAS.md #25).
"""

from __future__ import annotations

import argparse
import csv as csvmod
import statistics
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from models import cgm_metrics
from models.engine import ModelStepper
from models.types import FoodEvent, ModelId, PersonProfile, SensorId

# A standard day: breakfast / lunch / afternoon snack / dinner (g at min-of-day).
STANDARD_MEALS = [
    FoodEvent(time_of_day_min=420, carbs_g=45.0, duration_min=20),
    FoodEvent(time_of_day_min=780, carbs_g=70.0, duration_min=30),
    FoodEvent(time_of_day_min=1020, carbs_g=20.0, duration_min=15),
    FoodEvent(time_of_day_min=1200, carbs_g=60.0, duration_min=30),
]
HORIZON_MIN = 24 * 60
DT_MIN = 1.0  # 1-minute integration steps over the day

_METRIC_COLS = ("n", "mean", "sd", "cv", "tir", "tbr", "tbr1", "tbr2", "tar", "tar1", "tar2")


def _run_model_day(model_id: ModelId) -> list[float]:
    """The noiseless 24 h glucose trace for *model_id* on the standard day."""
    prof = PersonProfile(name="std", model_id=model_id, food_events=list(STANDARD_MEALS))
    s = ModelStepper(prof)
    return [s.tick(DT_MIN, "x").glucose for _ in range(HORIZON_MIN)]


def _metrics_row(name: str, series: list[float], span_min: float) -> dict[str, str]:
    m = cgm_metrics.compute(series, span_minutes=span_min)
    f = cgm_metrics.fmt_hm
    return {
        "row": name,
        "n": str(m.n),
        "mean": f"{m.mean:.1f}",
        "sd": f"{m.sd:.1f}",
        "cv": f"{m.cv:.1f}",
        "tir": f(m.tir_min),
        "tbr": f(m.tbr_min),
        "tbr1": f(m.tbr1_min),
        "tbr2": f(m.tbr2_min),
        "tar": f(m.tar_min),
        "tar1": f(m.tar1_min),
        "tar2": f(m.tar2_min),
    }


def mard(reference: list[float], measured: list[float]) -> float:
    """Mean Absolute Relative Difference (%) of *measured* vs *reference*, paired."""
    pairs = [(r, x) for r, x in zip(reference, measured, strict=False) if r > 0.0]
    if not pairs:
        return float("nan")
    return 100.0 * statistics.fmean(abs(x - r) / r for r, x in pairs)


def _md_table(rows: list[dict[str, str]]) -> str:
    head = ["", *(_METRIC_COLS)]
    if any("mard" in r for r in rows):
        head.append("MARD %")
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for r in rows:
        cells = [r["row"], *(r.get(c, "") for c in _METRIC_COLS)]
        if "MARD %" in head:
            cells.append(r.get("mard", ""))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def offline_table() -> list[dict[str, str]]:
    rows = []
    for mid in ModelId:
        rows.append(_metrics_row(mid.name.title(), _run_model_day(mid), HORIZON_MIN))
    return rows


def board_table(address: str, minutes_per_cell: float) -> list[dict[str, str]]:
    """model x noise: push config, stream, metrics + MARD vs the local model."""
    import contextlib

    from PyQt6.QtCore import QCoreApplication
    from PyQt6.QtTest import QTest

    from api import protocol
    from models import cambridge, deichmann, royparker, uva_padova
    from services.ble_session import BleSession

    _defaults = {
        ModelId.CAMBRIDGE: cambridge,
        ModelId.UVA_PADOVA: uva_padova,
        ModelId.ROYPARKER: royparker,
        ModelId.DEICHMANN: deichmann,
    }
    app = QCoreApplication.instance() or QCoreApplication([])
    rows: list[dict[str, str]] = []
    speed = 300.0  # 1 tick/s at x300 -> ~5 sim-min per BLE sample

    sess = BleSession(address, "Nordic Glucose Sensor", app)
    got: list[float] = []
    sess.new_message.connect(
        lambda m: got.append(float(m["glucose_value"])) if "glucose_value" in m else None
    )
    ready = {"c": False}
    sess.connected.connect(lambda *_a: ready.__setitem__("c", True))
    sess.start()
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline and not ready["c"]:
        QTest.qWait(200)  # pyright: ignore[reportCallIssue]
    if not ready["c"]:
        raise SystemExit(f"could not connect to {address}")

    for mid in ModelId:
        params = _defaults[mid].default_params()
        for sid in SensorId:
            sess.queue_write("person", protocol.encode_person_config(mid, params))
            sess.queue_write("sensor", protocol.encode_sensor_config(sid, {}))
            sess.queue_write("speed", protocol.encode_speed(speed))
            sess.queue_write("run_state", protocol.encode_run_state(protocol.RUN_STATE_STOPPED))
            sess.queue_write("run_state", protocol.encode_run_state(protocol.RUN_STATE_RUNNING))
            QTest.qWait(4000)  # pyright: ignore[reportCallIssue]
            got.clear()
            local = ModelStepper(PersonProfile(name="ref", model_id=mid))
            ref: list[float] = []
            end = time.monotonic() + minutes_per_cell * 60
            while time.monotonic() < end:
                QTest.qWait(1000)  # pyright: ignore[reportCallIssue]
                ref.append(local.tick(speed / 60.0, "x").glucose)
            row = _metrics_row(f"{mid.name.title()} / {sid.name}", got, minutes_per_cell * 60)
            row["mard"] = f"{mard(ref[: len(got)], got):.1f}" if got else "—"
            rows.append(row)
            print(f"  {row['row']}: n={row['n']} mean={row['mean']} MARD={row['mard']}")
    with contextlib.suppress(Exception):
        sess.stop()
        sess.wait(3000)
    return rows


def _write(out_dir: Path, name: str, rows: list[dict[str, str]], title: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{name}.md").write_text(f"# {title}\n\n{_md_table(rows)}\n", encoding="utf-8")
    cols = ["row", *_METRIC_COLS, *(["mard"] if any("mard" in r for r in rows) else [])]
    with (out_dir / f"{name}.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csvmod.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"  wrote {out_dir / name}.md / .csv")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--board", metavar="ADDR", help="also build the per-noise table via this board")
    ap.add_argument("--minutes", type=float, default=6.0, help="wall minutes per board cell")
    ap.add_argument("--out", type=Path, help="directory to write *.md / *.csv into")
    args = ap.parse_args()

    off = offline_table()
    title = "Range metrics per model — standard 24 h day (noiseless model, h:mm)"
    print(f"\n## {title}\n\n{_md_table(off)}\n")
    if args.out:
        _write(args.out, "results_models", off, title)

    if args.board:
        print("\n## Per model x sensor-noise (board) — metrics + MARD vs the model\n")
        brd = board_table(args.board, args.minutes)
        print("\n" + _md_table(brd) + "\n")
        if args.out:
            _write(args.out, "results_model_x_noise", brd, "Model x noise (board) — metrics + MARD")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
