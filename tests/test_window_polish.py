"""Window behaviour: windows open centred on the main window, the wheel / a two-finger touchpad
scroll reaches the page instead of being swallowed, and the schedule graph is laid out flush with
the table under it (gui/widgets.py, gui/schedule_graph.py)."""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from typing import Any

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtTest import QTest as _QTest
from PyQt6.QtWidgets import QApplication, QScrollArea, QVBoxLayout, QWidget

from gui.schedule_graph import ScheduleGraph
from gui.widgets import CenterOnMain, NoWheelComboBox, NoWheelTimeEdit

QTest: Any = _QTest  # the PyQt6 stubs reject valid QTest calls


@pytest.fixture(scope="module", autouse=True)
def _app():
    return QApplication.instance() or QApplication([])


def _wheel() -> QWheelEvent:
    return QWheelEvent(
        QPointF(5, 5),
        QPointF(5, 5),
        QPoint(0, 0),
        QPoint(0, -120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.ScrollUpdate,
        False,
    )


# -- the wheel reaches the page --------------------------------------------------------------


@pytest.mark.parametrize(
    "make",
    [lambda: ScheduleGraph("t", "y", "#3f8fd0"), NoWheelComboBox, NoWheelTimeEdit],
    ids=["graph", "combo", "time edit"],
)
def test_the_wheel_is_not_swallowed_by_the_widgets_on_a_user_page(make):
    widget = make()
    event = _wheel()
    event.accept()
    widget.wheelEvent(event)
    assert not event.isAccepted()  # ignored: the scroll area around it scrolls


def test_a_combo_box_keeps_its_choice_when_the_page_is_scrolled_over_it():
    combo = NoWheelComboBox()
    combo.addItems(["a", "b", "c"])
    combo.setCurrentIndex(1)
    combo.wheelEvent(_wheel())
    assert combo.currentIndex() == 1


def test_a_scroll_area_scrolls_when_the_wheel_is_over_a_graph_inside_it():
    area = QScrollArea()
    body = QWidget()
    layout = QVBoxLayout(body)
    graph = ScheduleGraph("t", "y", "#3f8fd0")
    layout.addWidget(graph)
    filler = QWidget()
    filler.setMinimumHeight(2000)
    layout.addWidget(filler)
    area.setWidget(body)
    area.resize(400, 300)
    area.show()
    QTest.qWait(50)
    bar = area.verticalScrollBar()
    assert bar is not None and bar.maximum() > 0
    # what Qt does with a wheel event: offer it to the widget under the pointer, and when that
    # one ignores it hand it to the parent, up to the scroll area's viewport
    event = _wheel()
    event.accept()
    graph.wheelEvent(event)
    assert not event.isAccepted()
    area.close()


# -- windows open centred on the main window --------------------------------------------------


@pytest.fixture
def main_window():
    main = QWidget()
    main.resize(900, 600)
    main.move(100, 80)
    app = QApplication.instance()
    assert app is not None
    centerer = CenterOnMain(main)
    app.installEventFilter(centerer)
    main.show()
    QTest.qWait(50)
    yield main
    app.removeEventFilter(centerer)
    main.close()


def _centre(widget: QWidget) -> QPoint:
    return widget.geometry().center()


def test_another_window_opens_centred_on_the_main_window(main_window):
    other = QWidget()
    other.setWindowFlag(Qt.WindowType.Window, True)
    other.resize(300, 200)
    other.show()
    QTest.qWait(50)
    main_centre = main_window.frameGeometry().center()
    assert abs(_centre(other).x() - main_centre.x()) <= 6
    assert abs(_centre(other).y() - main_centre.y()) <= 6
    other.close()


def test_a_window_is_centred_again_each_time_it_is_shown(main_window):
    other = QWidget()
    other.setWindowFlag(Qt.WindowType.Window, True)
    other.resize(300, 200)
    other.show()
    other.move(5, 5)
    other.hide()
    main_window.move(150, 120)
    QTest.qWait(50)
    other.show()
    QTest.qWait(50)
    main_centre = main_window.frameGeometry().center()
    assert abs(_centre(other).x() - main_centre.x()) <= 6
    other.close()


def test_the_main_window_itself_is_not_moved(main_window):
    before = main_window.pos()
    main_window.hide()
    main_window.show()
    QTest.qWait(50)
    assert main_window.pos() == before


def test_a_window_bigger_than_the_screen_area_is_kept_on_screen(main_window):
    other = QWidget()
    other.setWindowFlag(Qt.WindowType.Window, True)
    other.resize(300, 200)
    main_window.move(0, 0)
    other.show()
    QTest.qWait(50)
    screen = main_window.screen()
    assert screen is not None
    area = screen.availableGeometry()
    assert other.x() >= area.left() and other.y() >= area.top()
    other.close()


# -- the schedule graph hugs its edges ------------------------------------------------------


def test_the_schedule_graph_has_no_blank_band_around_the_plot():
    graph = ScheduleGraph("Exercise intensity over the day", "Intensity (%)", "#e0813f")
    graph.resize(1000, 420)
    graph.show()
    QTest.qWait(50)
    graph.draw()
    width, height = graph.width(), graph.height()
    ylabel = graph.ax.yaxis.label.get_window_extent()
    xlabel = graph.ax.xaxis.label.get_window_extent()
    plot = graph.ax.get_window_extent()
    assert ylabel.x0 <= 0.03 * width  # the y label starts at the left edge, level with the table
    assert plot.x1 >= 0.97 * width  # the plot reaches the right edge
    assert xlabel.y0 <= 0.08 * height  # the x label sits at the bottom: no band under it
    graph.close()
