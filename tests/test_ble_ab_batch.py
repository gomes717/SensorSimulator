"""The A/B batch runner's verdicts: which sensors count as silent, and the table."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import ble_ab_batch as ab
import pytest


def _result(**notifications: int) -> dict:
    return {
        "sensors": {
            f"Nordic Glucose Sensor {n}": {"notifications": count}
            for n, count in notifications.items()
        }
    }


def test_sensor_with_almost_no_data_is_silent():
    result = _result(**{"1": 24, "2": 24, "3": 23, "4": 1})
    assert ab.silent_sensors(result) == ["Nordic Glucose Sensor 4"]


def test_all_streaming_means_none_silent():
    assert ab.silent_sensors(_result(**{"1": 24, "2": 23, "3": 24, "4": 22})) == []


def test_every_sensor_silent_when_nothing_arrived():
    result = _result(**{"1": 0, "2": 0})
    assert len(ab.silent_sensors(result)) == 2


def test_table_counts_silent_runs_per_sensor():
    runs = [
        {"config": "baseline", "result": _result(**{"1": 24, "4": 0})},
        {"config": "baseline", "result": _result(**{"1": 24, "4": 24})},
        {"config": "baseline", "result": None},
    ]
    row = next(line for line in ab.table(runs) if line.startswith("baseline"))
    assert " 1/2 " in row  # sensor 4: silent in one of the two runs that saw it
    assert row.split()[1:3] == ["3", "1"]  # three runs, one with no result


def test_second_batch_is_refused_while_the_board_is_held(tmp_path):
    with (
        ab.board_lock(tmp_path),
        pytest.raises(SystemExit, match="another batch holds the board"),
        ab.board_lock(tmp_path),
    ):
        pass
    with ab.board_lock(tmp_path):  # released again once the first batch ends
        pass
