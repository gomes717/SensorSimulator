"""Regression pin for issue 08: the food/exercise graph is labelled report-only
when the active person's data source is CSV, and reverts for a model person.
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication

from models.types import ModelId, PersonProfile


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(app):
    import gui.main_window as mw

    w = mw.MainWindow()
    yield w
    w.close()


def test_title_is_plain_without_a_csv_person(win):
    assert win._fe_graph_title() == "Food / Exercise"


@pytest.mark.parametrize("model_only", [True, False])
def test_title_says_report_only_for_any_csv_person(win, model_only):
    """A CSV-backed person runs no model, Model Only or board-connected."""
    win._model_only = model_only
    win._active_person = PersonProfile(name="CSV Pt", model_id=ModelId.CAMBRIDGE, data_source="csv")
    win._graph.redraw_food_ex()
    assert "report-only" in win._graph.fe_ax.get_title().lower()

    win._active_person = PersonProfile(name="Model Pt", model_id=ModelId.CAMBRIDGE)
    win._graph.redraw_food_ex()
    assert win._graph.fe_ax.get_title() == "Food / Exercise"


def test_food_ex_graph_follows_the_selected_sensor_not_the_active_person(win):
    """A multi-sensor board can mix a CSV slot and a model slot: the graph must
    track whichever row is selected, not the single _active_person."""
    from models import board_layout as bl

    csv_person = PersonProfile(name="CSV Pt", model_id=ModelId.CAMBRIDGE, data_source="csv")
    model_person = PersonProfile(name="Model Pt", model_id=ModelId.CAMBRIDGE)
    win._model_only = False
    win._person_profiles[:] = [csv_person, model_person]
    win._board_layout = bl.BoardLayout(
        [bl.SlotAssignment(person="CSV Pt"), bl.SlotAssignment(person="Model Pt")]
    )

    win._on_user_selected(win._slot_user_id(0))
    assert win._graph.fe_canvas.isVisibleTo(win) is False
    assert "report-only" in win._fe_graph_title().lower()

    win._on_user_selected(win._slot_user_id(1))
    assert win._graph.fe_canvas.isVisibleTo(win) is True
    assert win._fe_graph_title() == "Food / Exercise"


# --- issue 14: CSV Analysis whole-recording metrics panel -----------------


def test_csv_analysis_has_whole_and_selected_panels(app):
    import datetime

    from gui.csv_analysis_window import CsvAnalysisWindow

    c = CsvAnalysisWindow()
    n = 48 * 12  # 48 h at 5 min
    c._times = [datetime.datetime(2020, 1, 1) + datetime.timedelta(minutes=5 * i) for i in range(n)]
    t0 = c._times[0]
    c._values = [100.0] * (n - 24) + [45.0] * 24  # last 2 h are TBR2
    c._hours = [(t - t0).total_seconds() / 3600.0 for t in c._times]
    c._start_hour = 0.0

    span = (c._times[-1] - t0).total_seconds() / 60.0
    c._fill_stats(c._whole_labels, c._metrics(c._values, span))
    c._refresh_selection()

    assert c._whole_labels["n"].text() == str(n)
    assert c._whole_labels["tbr"].text().startswith("2:00")  # 24 * 5 min low
    # the first 24 h window is all in range
    assert c._stat_labels["tbr"].text().startswith("0:00")
    assert int(c._stat_labels["n"].text()) < n
    c.close()
