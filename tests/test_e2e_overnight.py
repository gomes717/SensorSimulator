"""The overnight runner's scenario rotation and summary (no hardware)."""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import e2e_overnight_3sensor as on

from models import dexcom_csv


def test_every_cycle_has_one_csv_slot_and_two_distinct_model_slots():
    for n in range(1, 25):
        spec = on.make_spec(n, 35.0, 40.0)
        assert sorted(spec.profiles) == [0, 1, 2]
        models = [p.model_id for s, p in spec.profiles.items() if s != spec.csv_slot]
        assert len(set(models)) == 2
        assert getattr(spec.profiles[spec.csv_slot], "data_source", "model") == "csv"


def test_csv_slot_and_file_rotate_and_every_window_holds_a_full_day():
    slots = {on.make_spec(n, 35.0, 40.0).csv_slot for n in range(1, 7)}
    files = {on.make_spec(n, 35.0, 40.0).csv_file for n in range(1, 17)}
    assert slots == {0, 1, 2} and len(files) == 16
    for n in range(1, 33):
        spec = on.make_spec(n, 35.0, 40.0)
        rows = dexcom_csv.read_egv(spec.csv_file)
        start = datetime.fromisoformat(spec.window_start)
        assert rows[0][0] <= start and (rows[-1][0] - start).total_seconds() >= 86400


def test_event_plan_targets_only_model_slots():
    spec = on.make_spec(5, 35.0, 40.0)
    assert all(slot != spec.csv_slot for _, slot, _, _ in spec.events)
    assert {kind for _, _, kind, _ in spec.events} == {"food", "exercise", "pisa"}


def test_summary_counts_cycles_and_failures(tmp_path):
    results = [
        {"cycle": 1, "cycle_ok": True, "failed": []},
        {
            "cycle": 2,
            "cycle_ok": False,
            "failed": ["sensor 1 complete", "slot 0 follows the host model (X)"],
        },
        {"cycle": 3, "cycle_ok": False, "failed": [], "error": "RuntimeError: boom"},
    ]
    now = datetime(2026, 10, 4, 22, 0)
    on.write_summary(tmp_path, results, now, now)
    text = (tmp_path / "SUMMARY.md").read_text(encoding="utf-8")
    assert "cycles: 3 run, 1 fully passing, 1 aborted" in text
    assert "1x slot 0 follows the host model" in text and "RuntimeError: boom" in text


def test_parity_limit_scales_with_the_tick_and_never_drops_below_the_floor():
    spec = on.make_spec(2, 35.0, 40.0)  # Cambridge on slot 0: 15-minute meals
    prof = spec.profiles[0]
    limit, quantum = on.parity_limit(prof, [], 40.0, 500.0)
    assert abs(quantum - (40 / 60) / 15) < 1e-9 and 20 < limit < 25  # ~4.4% of 500 mg/dL
    slow, _ = on.parity_limit(prof, [], 1.0, 500.0)
    assert slow == on.PARITY_P99_MAX  # at real time one tick is negligible: the floor applies


def test_cycle_report_is_written_under_its_own_name(tmp_path):
    spec = on.make_spec(1, 35.0, 40.0)
    result = {
        "checks": [("a check", True, "fine"), ("last check", False, "bad")],
        "failed": ["last check"],
    }
    on.write_cycle_report(tmp_path, spec, result)
    assert (tmp_path / "REPORT.md").exists()
    assert not (tmp_path / "last check").exists()
