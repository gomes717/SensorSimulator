"""Issue 18: ScenarioDispatch is the scenario-step vocabulary, driving the app.

Pins that MainWindow._scenario_dispatch (used by scripts/e2e.py's S17 case)
still routes each kind to the right action after the extraction.
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


def test_speed_step_reaches_the_app(win):
    line = win._scenario_dispatch("speed", {"multiplier": 30})
    assert win._speed_mult == 30.0
    assert "x30" in line


def test_person_step_selects_the_profile(win):
    win._person_profiles.append(PersonProfile(name="Scn Pt", model_id=ModelId.CAMBRIDGE))
    win._on_profiles_changed()
    line = win._scenario_dispatch("person", {"person": "Scn Pt"})
    assert win._active_person is not None and win._active_person.name == "Scn Pt"
    assert "Scn Pt" in line


def test_run_state_start_then_stop(win):
    win._person_profiles.append(PersonProfile(name="Run Pt", model_id=ModelId.CAMBRIDGE))
    win._on_profiles_changed()
    win._scenario_dispatch("person", {"person": "Run Pt"})
    win._scenario_dispatch("run_state", {"state": "start"})
    assert win._run_state == "running"
    win._scenario_dispatch("run_state", {"state": "stop"})
    assert win._run_state == "stopped"


def test_insert_food_step_feeds_the_engine(win):
    win._person_profiles.append(PersonProfile(name="Food Pt", model_id=ModelId.CAMBRIDGE))
    win._on_profiles_changed()
    win._scenario_dispatch("person", {"person": "Food Pt"})
    win._scenario_dispatch("run_state", {"state": "start"})
    line = win._scenario_dispatch("insert_food", {"carbs_g": 40, "duration_min": 10})
    assert line == "insert_food 40 g / 10 min"
    win._scenario_dispatch("run_state", {"state": "stop"})


def test_unknown_kind_is_reported_not_raised(win):
    assert "unknown action" in win._scenario_dispatch("frobnicate", {})
