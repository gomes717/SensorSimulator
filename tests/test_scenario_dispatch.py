"""scripts/scenario_dispatch.py: the test-only scenario-step vocabulary, driving the app.

Pins that each step kind (used by scripts/e2e.py's S17 case) still reaches the right
app action. The scenario machinery is test tooling, not part of the application.
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication
from scenario_dispatch import ScenarioDispatch

from models.types import ModelId, PersonProfile


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(app):
    import gui.main_window as mw

    w = mw.MainWindow()
    w.dispatch = ScenarioDispatch(w, w._events).dispatch
    yield w
    w.close()


def test_speed_step_reaches_the_app(win):
    line = win.dispatch("speed", {"multiplier": 30})
    assert win.state.speed_mult == 30.0
    assert "x30" in line


def test_person_step_selects_the_profile(win):
    win.state.person_profiles.append(PersonProfile(name="Scn Pt", model_id=ModelId.CAMBRIDGE))
    win._on_profiles_changed()
    line = win.dispatch("person", {"person": "Scn Pt"})
    assert win.state.active_person is not None and win.state.active_person.name == "Scn Pt"
    assert "Scn Pt" in line


def test_run_state_start_then_stop(win):
    win.state.person_profiles.append(PersonProfile(name="Run Pt", model_id=ModelId.CAMBRIDGE))
    win._on_profiles_changed()
    win.dispatch("person", {"person": "Run Pt"})
    win.dispatch("run_state", {"state": "start"})
    assert win._run.state == "running"
    win.dispatch("run_state", {"state": "stop"})
    assert win._run.state == "stopped"


def test_insert_food_step_feeds_the_engine(win):
    win.state.person_profiles.append(PersonProfile(name="Food Pt", model_id=ModelId.CAMBRIDGE))
    win._on_profiles_changed()
    win.dispatch("person", {"person": "Food Pt"})
    win.dispatch("run_state", {"state": "start"})
    line = win.dispatch("insert_food", {"carbs_g": 40, "duration_min": 10})
    assert line == "insert_food 40 g / 10 min"
    win.dispatch("run_state", {"state": "stop"})


def test_unknown_kind_is_reported_not_raised(win):
    assert "unknown action" in win.dispatch("frobnicate", {})
