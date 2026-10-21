"""Scoring for the overnight mode-switch test (scripts/overnight_modeswitch_analysis.py)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import overnight_modeswitch_analysis as oma

G6 = "Dexcom00"
THREE = ["Nordic Glucose Sensor 1", "Nordic Glucose Sensor 2", "Nordic Glucose Sensor 3"]


def test_the_two_modes_are_told_apart_by_their_adverts():
    assert oma.classify_adverts([*THREE, "Some Phone"], G6) == (oma.MULTI, "")
    assert oma.classify_adverts([G6, "Nordic Glucose Sensor 1"], G6) == (oma.DEXCOM, "")


def test_a_half_switched_board_is_reported_with_what_was_seen():
    mode, detail = oma.classify_adverts([G6, *THREE], G6)  # both at once
    assert mode is None and "Dexcom00 advertises" in detail and "Sensor 2" in detail
    mode, detail = oma.classify_adverts(THREE[:2], G6)  # a sensor missing
    assert mode is None and "Sensor 3" not in detail
    assert oma.classify_adverts([], G6)[0] is None
    assert oma.classify_adverts([G6], G6)[0] is None  # the G6 alone has no app sensor


def test_the_logged_switches_must_be_the_ones_asked_for():
    log = (
        "x\nmain: board mode -> dexcom (G6, 1 sensor)\ny\n"
        "main: board mode -> multi (standard BLE)\n"
    )
    assert oma.parse_mode_lines(log) == ["dexcom", "multi"]
    assert oma.score_switches(["dexcom", "multi"], ["dexcom", "multi"]) == []
    assert "switch 2" in " ".join(oma.score_switches(["dexcom", "multi"], ["dexcom", "dexcom"]))
    assert oma.score_switches(["dexcom"], [])  # the board never switched


def test_a_slot_that_changed_across_the_trip_is_named():
    before = {0: {"name": "aa", "person": "01"}, 1: {"name": "bb", "person": "02"}}
    assert oma.fingerprint_diff(before, before) == []
    after = {0: {"name": "aa", "person": "01"}, 1: {"name": "bb", "person": "ff"}}
    assert oma.fingerprint_diff(before, after) == ["slot 2 person: 02 -> ff"]


def test_switch_times_are_summarised():
    assert oma.timing_summary([]) == "n/a"
    assert oma.timing_summary([6.0, 8.0, 40.0]) == "n=3 median 8.0 s max 40.0 s"


def test_the_verdict_groups_failures_by_check():
    results = oma.Results()
    results.add(1, "full", "a", True)
    for cycle in (2, 5):
        results.add(cycle, "quick", "adverts", False, "sensors seen: none")
    assert results.failed_cycles() == {2, 5}
    lines = oma.verdict(results)
    assert len(lines) == 1 and "adverts: failed in 2 cycle(s) (2, 5)" in lines[0]
    results.aborted = "5 bad cycles in a row"
    assert oma.verdict(results)[0].startswith("the run stopped early")
