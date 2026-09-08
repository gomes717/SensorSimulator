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
    import graphic.main_window as mw

    w = mw.MainWindow()
    yield w
    w.close()


def test_title_is_plain_without_a_csv_person(win):
    assert win._fe_graph_title() == "Food / Exercise"


def test_title_says_report_only_for_a_csv_person(win):
    win._active_person = PersonProfile(name="CSV Pt", model_id=ModelId.CAMBRIDGE, data_source="csv")
    win._redraw_food_ex_graph()
    assert "report-only" in win._fe_ax.get_title().lower()

    win._active_person = PersonProfile(name="Model Pt", model_id=ModelId.CAMBRIDGE)
    win._redraw_food_ex_graph()
    assert win._fe_ax.get_title() == "Food / Exercise"
