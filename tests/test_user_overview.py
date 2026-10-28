"""The profile screen's overview (gui/user_overview.py): the 24 h glucose preview with the range
regions, the meals and the exercise, and that the screen shows it and keeps it up to date."""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication

from gui.user_overview import UserOverview
from gui.user_profile_window import ProfileDeps, UserProfileWindow
from models import user_edit, user_sim, user_store
from models.types import CsvTrack, ExerciseEvent, FoodEvent

_THRESHOLDS = {"tbr2_below": 54.0, "tbr1_below": 70.0, "tar1_above": 180.0, "tar2_above": 250.0}


@pytest.fixture(scope="module", autouse=True)
def _app():
    return QApplication.instance() or QApplication([])


def _user():
    user = user_store.new_user("Ana")
    user.food_events = [FoodEvent(480, 60.0, 20), FoodEvent(1140, 45.0, 30)]
    user.exercise_events = [ExerciseEvent(1080, 45, 70.0)]
    return user


def _screen(user):
    return UserProfileWindow(user, False, ProfileDeps([user], lambda: None, lambda _u, _old: None))


def test_the_overview_of_a_model_user_is_the_24_h_simulation():
    user = _user()
    view = UserOverview(user, _THRESHOLDS)
    minutes, glucose = user_sim.preview_24h(user)
    assert view.minutes == minutes and view.glucose == glucose
    assert len(view.glucose) == 1440
    assert max(view.glucose) > min(view.glucose) + 20  # the meals show


def test_the_overview_marks_each_meal_and_shades_each_exercise_bout():
    view = UserOverview(_user(), _THRESHOLDS)
    assert view.meal_marks == 2
    assert view.exercise_spans == 1


def test_the_overview_draws_the_five_range_regions():
    assert UserOverview(_user(), _THRESHOLDS).band_count == 5


def test_the_overview_draws_the_two_schedules_under_the_glucose():
    view = UserOverview(_user(), _THRESHOLDS)
    assert len(view.food_values) == len(view.exercise_values) == 1440
    assert view.food_values[480] == pytest.approx(3.0)  # 60 g over 20 min
    assert view.exercise_values[1080] == pytest.approx(70.0)


def test_the_overview_follows_the_user_when_refreshed():
    user = _user()
    view = UserOverview(user, _THRESHOLDS)
    user.food_events = []
    view.refresh()
    assert view.meal_marks == 0 and max(view.food_values) == 0.0


def test_the_overview_of_a_csv_user_is_the_recorded_window_without_schedules():
    user = _user()
    user.mode = "csv"
    user.csv = CsvTrack(samples=[100, 120, 140], interval_s=300, foodlog=[(600, 30.0)])
    view = UserOverview(user, _THRESHOLDS)
    assert view.glucose == [100.0, 120.0, 140.0]
    assert view.minutes == [0.0, 5.0, 10.0]
    assert view.meal_marks == 0 and view.exercise_spans == 0  # the recording drives it
    assert view.food_values == [] and view.exercise_values == []
    assert view.band_count == 5


def test_a_csv_user_with_no_window_says_there_is_nothing_to_preview():
    user = _user()
    user.mode = "csv"
    user.csv = None
    view = UserOverview(user, _THRESHOLDS)
    assert view.glucose == []
    assert "No CSV window" in view.note.text()


def test_the_overview_says_it_has_no_sensor_noise():
    assert "noise" in UserOverview(_user(), _THRESHOLDS).note.text().lower()


def test_new_thresholds_redraw_the_regions():
    view = UserOverview(_user(), _THRESHOLDS)
    view.set_thresholds({**_THRESHOLDS, "tar1_above": 160.0})
    assert view.band_count == 5


# -- inside the profile screen -----------------------------------------------------------------


def test_the_profile_screen_previews_the_unsaved_edits():
    user = _user()
    screen = _screen(user)
    screen.profile_page.weight_spin.setValue(50.0)  # not saved
    assert screen.overview.glucose == user_sim.preview_24h(screen.user)[1]
    assert screen.overview.glucose != user_sim.preview_24h(user)[1]  # differs from the saved user


def test_the_overview_follows_the_mode_chosen_on_the_screen():
    user = _user()
    user.csv = CsvTrack(samples=[100] * 288, interval_s=300, foodlog=[])
    screen = _screen(user)
    user_edit.set_mode(screen.user, "csv")
    screen._changed()
    assert screen.overview.glucose == [100.0] * 288


def test_the_overview_shows_on_every_page_but_profile():
    user = _user()
    screen = _screen(user)
    screen.show()
    shown = {}
    for key in ("profile", "csv", "food", "exercise", "model"):
        screen._select(key)
        shown[key] = screen.overview.isVisible()
    assert shown == {"profile": False, "csv": True, "food": True, "exercise": True, "model": True}
    screen.close()
