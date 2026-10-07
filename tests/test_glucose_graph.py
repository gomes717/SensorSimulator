"""GlucoseGraph: the trace changes colour where it crosses a limit, dots are optional."""

import math
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication

from gui.glucose_graph import GlucoseGraph
from gui.run_clock import RunClock

_APP = QApplication.instance() or QApplication([])

T = {"tbr2_below": 54.0, "tbr1_below": 70.0, "tar1_above": 180.0, "tar2_above": 250.0}


@pytest.fixture
def graph():
    return GlucoseGraph(dict(T), 0.0, RunClock(datetime.now(UTC)))


def _fill(graph, points):
    for x, y in points:
        graph.append_received(x, y)
    graph.redraw_glucose()


def _run(graph, cat):
    xs, ys = graph._g.seg_lines[cat].get_data()
    return list(xs), list(ys)


def test_a_rising_line_turns_yellow_exactly_at_the_limit(graph):
    _fill(graph, [(0.0, 100.0), (10.0, 200.0)])  # crosses 180 at x = 8
    assert _run(graph, "g") == ([0.0, 8.0], [100.0, 180.0])
    assert _run(graph, "y") == ([8.0, 10.0], [180.0, 200.0])
    assert _run(graph, "r") == ([], [])


def test_a_steep_rise_passes_through_every_band_at_its_crossings(graph):
    _fill(graph, [(0.0, 100.0), (10.0, 300.0)])  # 180 at x = 4, 250 at x = 7.5
    assert _run(graph, "g") == ([0.0, 4.0], [100.0, 180.0])
    assert _run(graph, "y") == ([4.0, 7.5], [180.0, 250.0])
    assert _run(graph, "r") == ([7.5, 10.0], [250.0, 300.0])


def test_a_falling_line_is_cut_at_the_limit_too(graph):
    _fill(graph, [(0.0, 100.0), (10.0, 20.0)])  # 70 at x = 3.75, 54 at x = 5.75
    assert _run(graph, "g") == ([0.0, 3.75], [100.0, 70.0])
    assert _run(graph, "y") == ([3.75, 5.75], [70.0, 54.0])
    assert _run(graph, "r") == ([5.75, 10.0], [54.0, 20.0])


def test_a_segment_inside_one_band_is_one_run(graph):
    _fill(graph, [(0.0, 100.0), (5.0, 110.0), (10.0, 120.0)])
    assert _run(graph, "g") == ([0.0, 5.0, 10.0], [100.0, 110.0, 120.0])


def test_runs_of_the_same_colour_that_are_apart_do_not_join(graph):
    _fill(graph, [(0.0, 100.0), (10.0, 200.0), (20.0, 100.0)])
    xs, _ys = _run(graph, "g")
    assert any(math.isnan(x) for x in xs)  # a gap between the two green stretches


def test_the_colour_is_right_before_the_next_sample_arrives(graph):
    """The reported bug: the colour only changed once a further dot was added."""
    _fill(graph, [(0.0, 170.0), (5.0, 190.0)])
    assert _run(graph, "y")[0][0] == pytest.approx(2.5)  # yellow starts at the limit
    assert _run(graph, "g")[0][-1] == pytest.approx(2.5)


def test_dots_are_hidden_by_default_and_can_be_shown(graph):
    _fill(graph, [(0.0, 100.0), (5.0, 200.0), (10.0, 300.0)])
    lines = graph._g.point_lines
    assert not any(line.get_visible() for line in lines.values())
    graph.set_show_points(True)
    assert all(line.get_visible() for line in lines.values())
    assert list(lines["g"].get_xdata()) == [0.0]
    assert list(lines["y"].get_xdata()) == [5.0]
    assert list(lines["r"].get_xdata()) == [10.0]
    graph.set_show_points(False)
    assert not any(line.get_visible() for line in lines.values())


def test_the_show_points_choice_survives_a_theme_rebuild(graph):
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QSplitter

    splitter = QSplitter(Qt.Orientation.Vertical)
    splitter.addWidget(graph.canvas)
    splitter.addWidget(graph.fe_canvas)
    graph.set_show_points(True)
    graph.rebuild_for_theme(splitter)
    assert all(line.get_visible() for line in graph._g.point_lines.values())


def test_the_time_label_can_move_onto_the_glucose_graph(graph):
    assert graph.ax.get_xlabel() == ""
    graph.set_time_label(True)
    assert graph.ax.get_xlabel() == "Time (s)"
    graph.set_time_label(False)
    assert graph.ax.get_xlabel() == ""


def test_the_expected_entry_leaves_the_legend_for_a_csv_sensor(graph):
    def labels():
        return [t.get_text() for t in graph._g.legend.get_texts()]

    assert "Expected (model)" in labels()
    graph.set_expected_visible(False)
    assert "Expected (model)" not in labels() and len(labels()) == 3
    assert not graph._g.expected_line.get_visible()
    graph.set_expected_visible(True)
    assert "Expected (model)" in labels()


def test_the_hidden_expected_entry_survives_a_theme_rebuild(graph):
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QSplitter

    splitter = QSplitter(Qt.Orientation.Vertical)
    splitter.addWidget(graph.canvas)
    splitter.addWidget(graph.fe_canvas)
    graph.set_expected_visible(False)
    graph.rebuild_for_theme(splitter)
    assert "Expected (model)" not in [t.get_text() for t in graph._g.legend.get_texts()]
