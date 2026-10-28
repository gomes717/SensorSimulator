"""The CSV a graph exports (models/graph_export.py): one time column, every series on it."""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from models import graph_export


def test_series_sharing_a_timestamp_land_on_one_row():
    table = graph_export.rows(
        ([0.0, 5.0], [100.0, 110.0]), ([5.0], [108.0]), ([5.0], [2.0], [30.0])
    )
    assert table == [(0.0, 100.0, None, None, None), (5.0, 110.0, 108.0, 2.0, 30.0)]


def test_rows_are_ordered_by_time_across_series():
    table = graph_export.rows(([10.0], [1.0]), ([2.0], [2.0]), ([6.0], [0.0], [0.0]))
    assert [row[0] for row in table] == [2.0, 6.0, 10.0]


def test_a_nan_is_an_empty_cell():
    table = graph_export.rows(([0.0], [math.nan]), ([0.0], [90.0]), ([], [], []))
    assert table == [(0.0, None, 90.0, None, None)]


def test_no_data_gives_only_the_header(tmp_path):
    target = tmp_path / "empty.csv"
    graph_export.write_csv(target, graph_export.rows(([], []), ([], []), ([], [], [])))
    assert target.read_text(encoding="utf-8").splitlines() == [",".join(graph_export.HEADER)]


def test_write_csv_leaves_missing_cells_empty(tmp_path):
    target = tmp_path / "out.csv"
    graph_export.write_csv(target, [(0.0, 100.0, None, None, None)])
    assert target.read_text(encoding="utf-8").splitlines()[1] == "0.0,100.0,,,"
