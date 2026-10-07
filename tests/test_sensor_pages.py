"""Independent sensor pages: every sensor owns its graphs, history and stats."""

import os
import sys
from datetime import UTC, datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication

from gui.run_clock import RunClock
from gui.sensor_pages import SensorPages

_APP = QApplication.instance() or QApplication([])

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _stamp(seconds):
    return datetime.fromtimestamp(T0.timestamp() + seconds, UTC).isoformat(timespec="seconds")


@pytest.fixture
def win():
    import gui.main_window as mw
    from models import app_settings

    app_settings.save_pref("show_points", False)  # the private copy, whatever the real file says
    w = mw.MainWindow()
    w.state.cgms_only = True  # "recording" without a Start click
    w._clock.anchor(T0)
    w._clock.set_speed(1.0)
    yield w
    w.close()


def _glucose(user_id, value, at):
    return {"user_id": user_id, "dev_id": "aa", "glucose_value": value, "timestamp": _stamp(at)}


def test_a_message_lands_only_on_its_own_sensors_page(win):
    win.sensors.on_new_message(_glucose("Sensor A", 100.0, 5))
    win.sensors.on_new_message(_glucose("Sensor B", 200.0, 5))
    win.sensors.on_new_message(_glucose("Sensor A", 110.0, 10))
    a, b = win.tabs.pages.get("Sensor A"), win.tabs.pages.get("Sensor B")
    assert a.graph.buf.graph_y == [100.0, 110.0]
    assert b.graph.buf.graph_y == [200.0]
    assert win.tabs.pages.default_page.graph.buf.graph_y == []


def test_switching_sensors_keeps_every_pages_history(win):
    win.sensors.on_new_message(_glucose("Sensor A", 100.0, 5))
    win.sensors.on_new_message(_glucose("Sensor B", 200.0, 5))
    win.sensors.on_user_selected("Sensor A")
    assert win.sensors.current_page() is win.tabs.pages.get("Sensor A")
    win.sensors.on_user_selected("Sensor B")
    win.sensors.on_user_selected("Sensor A")
    assert win.tabs.pages.get("Sensor A").graph.buf.graph_y == [100.0]
    assert win.tabs.pages.get("Sensor B").graph.buf.graph_y == [200.0]


def test_a_stopped_run_records_nothing(win):
    win.state.cgms_only = False  # run state is "stopped"
    win.sensors.on_new_message(_glucose("Sensor A", 100.0, 5))
    page = win.tabs.pages.get("Sensor A")  # the list row selects it, but nothing is recorded
    assert page is None or page.graph.buf.graph_y == []


def test_every_page_shares_one_timeline(win):
    win.sensors.on_new_message(_glucose("Sensor A", 100.0, 30))
    win.sensors.on_new_message(_glucose("Sensor B", 100.0, 30))
    xs_a = win.tabs.pages.get("Sensor A").graph.buf.graph_x
    xs_b = win.tabs.pages.get("Sensor B").graph.buf.graph_x
    assert xs_a == xs_b == [30.0]


def test_restarting_clears_every_page_and_reanchors_the_clock(win):
    win.sensors.on_new_message(_glucose("Sensor A", 100.0, 5))
    win.sensors.on_new_message(_glucose("Sensor B", 100.0, 5))
    win.sim.reset_graph_view()
    assert win.tabs.pages.get("Sensor A").graph.buf.graph_y == []
    assert win.tabs.pages.get("Sensor B").graph.buf.graph_y == []
    assert win._clock.t0 > T0


def test_a_page_that_is_not_shown_defers_its_redraw_until_it_is():
    clock = RunClock(T0)
    thresholds = {"tbr2_below": 54, "tbr1_below": 70, "tar1_above": 180, "tar2_above": 250}
    pages = SensorPages(thresholds, 0.0, clock)
    pages.resize(400, 300)
    pages.show()
    a, b = pages.ensure("A"), pages.ensure("B")
    pages.show_page(a)
    b.add_received(1.0, 111.0)
    assert b._dirty  # hidden: recorded but not drawn
    pages.show_page(b)
    assert not b._dirty and b.graph.visible_xlim is not None  # caught up on show
    pages.close()


def test_show_points_is_one_app_wide_choice_that_every_page_follows(win):
    win.sensors.on_new_message(_glucose("Sensor A", 100.0, 5))
    win.sensors.on_new_message(_glucose("Sensor B", 120.0, 5))
    a, b = win.tabs.pages.get("Sensor A"), win.tabs.pages.get("Sensor B")
    assert not a.points_check.isChecked() and not b.points_check.isChecked()

    a.points_check.setChecked(True)  # the user ticks the box on page A

    assert b.points_check.isChecked()
    assert all(line.get_visible() for line in b.graph._g.point_lines.values())
    assert win.state.show_points is True
    later = win.sensors.page_of_user("Sensor C")  # a page created afterwards starts shown too
    assert later.points_check.isChecked()


def test_a_csv_page_labels_its_own_time_axis(win):
    page = win.sensors.page_of_user("Sensor A")
    page.set_csv(True)
    assert page.graph.ax.get_xlabel() == "Time (s)"
    page.set_csv(False)
    assert page.graph.ax.get_xlabel() == ""


def test_a_csv_page_has_no_expected_model_in_its_legend(win):
    page = win.sensors.page_of_user("Sensor A")
    page.set_csv(True)
    assert "Expected (model)" not in [t.get_text() for t in page.graph._g.legend.get_texts()]
    page.set_csv(False)
    assert "Expected (model)" in [t.get_text() for t in page.graph._g.legend.get_texts()]
