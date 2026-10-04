"""The overnight orchestrator's verdicts and clock handling."""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import ble_overnight as on


def _sensor(notifications, worst_gap=0.0, **extra):
    base = {
        "notifications": notifications,
        "worst_gap_s": worst_gap,
        "gaps": 0,
        "silent_subscribes": 0,
        "resubscribes": 0,
        "connects": 1,
        "uptime_pct": 99.9,
        "churns": 0,
        "churn_recovered": 0,
        "churn_failed": 0,
        "churn_recovery_s": [],
    }
    return {**base, **extra}


def test_h1_passes_when_every_sensor_streamed_the_whole_time():
    result = {"elapsed_s": 3600, "sensors": {"S1": _sensor(715), "S4": _sensor(718)}}
    assert on.verdict_h1(result)[0].startswith("**PASS**")


def test_h1_fails_on_a_sensor_that_went_quiet():
    result = {"elapsed_s": 3600, "sensors": {"S1": _sensor(715), "S4": _sensor(300, 1800.0)}}
    out = on.verdict_h1(result)
    assert out[0].startswith("**FAIL**")
    assert "FAIL" in out[2]


def test_h3_needs_every_drop_recovered_and_no_bystander_gap():
    ok = {"sensors": {"S1": _sensor(100, churns=2, churn_recovered=2, churn_recovery_s=[6, 7])}}
    assert on.verdict_h3(ok)[0].startswith("**PASS**")
    stuck = {"sensors": {"S1": _sensor(100, churns=2, churn_recovered=1, churn_recovery_s=[6])}}
    assert on.verdict_h3(stuck)[0].startswith("**FAIL**")
    gap = {
        "sensors": {"S1": _sensor(100, churns=1, churn_recovered=1, churn_recovery_s=[6], gaps=1)}
    }
    assert on.verdict_h3(gap)[0].startswith("**FAIL**")


def test_h2_counts_sensors_left_silent(tmp_path):
    runs = [
        {
            "result": {
                "sensors": {
                    "Nordic Glucose Sensor 1": _sensor(24),
                    "Nordic Glucose Sensor 4": _sensor(24),
                }
            }
        },
        {
            "result": {
                "sensors": {
                    "Nordic Glucose Sensor 1": _sensor(24),
                    "Nordic Glucose Sensor 4": _sensor(0),
                }
            }
        },
        {"result": None},
    ]
    (tmp_path / "runs.jsonl").write_text("\n".join(json.dumps(r) for r in runs), encoding="utf-8")
    text = on.verdict_h2(tmp_path)[0]
    assert text.startswith("**FAIL**")
    assert "silent at the end of a run: 1/4" in text
    assert "2 of 3 runs" in text


def test_wilson_upper_bound_shrinks_with_more_clean_trials():
    assert on.wilson_upper(0, 0) == 1.0
    assert on.wilson_upper(0, 80) < on.wilson_upper(0, 20) < 0.2


def test_deadline_is_the_next_occurrence_of_the_clock_time():
    soon = (datetime.now() + timedelta(hours=1)).strftime("%H:%M")
    assert 0 < on.minutes_left(on.deadline_from(soon)) <= 61
    past = (datetime.now() - timedelta(hours=1)).strftime("%H:%M")
    assert on.minutes_left(on.deadline_from(past)) > 22 * 60
