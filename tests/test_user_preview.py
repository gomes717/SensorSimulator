"""Users-screen slice 8 (Preview): the 24 h preview window and the Preview button of the profile
screen (gui/user_preview_window.py, gui/user_profile_window.py)."""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication

from gui.user_preview_window import PreviewWindow
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


def test_the_preview_of_a_model_user_is_the_24_h_simulation():
    user = _user()
    window = PreviewWindow(user, _THRESHOLDS)
    minutes, glucose = user_sim.preview_24h(user)
    assert window.minutes == minutes and window.glucose == glucose
    assert len(window.glucose) == 1440
    assert max(window.glucose) > min(window.glucose) + 20  # the meals show


def test_the_preview_marks_each_meal_and_shades_each_exercise_bout():
    window = PreviewWindow(_user(), _THRESHOLDS)
    assert window.meal_marks == 2
    assert window.exercise_spans == 1


def test_the_preview_draws_the_range_thresholds():
    window = PreviewWindow(_user(), _THRESHOLDS)
    assert window.threshold_lines == 2  # low and high


def test_the_preview_of_a_csv_user_is_the_recorded_window_without_meal_marks():
    user = _user()
    user.mode = "csv"
    user.csv = CsvTrack(samples=[100, 120, 140], interval_s=300, foodlog=[(600, 30.0)])
    window = PreviewWindow(user, _THRESHOLDS)
    assert window.glucose == [100.0, 120.0, 140.0]
    assert window.minutes == [0.0, 5.0, 10.0]
    assert window.meal_marks == 0 and window.exercise_spans == 0  # the recording drives it


def test_a_csv_user_with_no_window_says_there_is_nothing_to_preview():
    user = _user()
    user.mode = "csv"
    user.csv = None
    window = PreviewWindow(user, _THRESHOLDS)
    assert window.glucose == []
    assert "No CSV window" in window.note.text()


def test_the_preview_says_it_has_no_sensor_noise():
    assert "noise" in PreviewWindow(_user(), _THRESHOLDS).note.text().lower()


# -- the button -------------------------------------------------------------------------------


def _screen(user):
    return UserProfileWindow(user, False, ProfileDeps([user], lambda: None, lambda _u, _old: None))


def test_the_profile_screen_previews_the_unsaved_edits():
    user = _user()
    screen = _screen(user)
    screen.profile_page.weight_spin.setValue(50.0)  # not saved
    window = screen.preview()
    assert window.glucose == user_sim.preview_24h(screen.user)[1]
    assert window.glucose != user_sim.preview_24h(user)[1]  # differs from the saved user
    window.close()


def test_the_preview_follows_the_mode_chosen_on_the_screen():
    user = _user()
    user.csv = CsvTrack(samples=[100] * 288, interval_s=300, foodlog=[])
    screen = _screen(user)
    user_edit.set_mode(screen.user, "csv")
    window = screen.preview()
    assert window.glucose == [100.0] * 288
    window.close()


def test_the_preview_button_opens_the_preview():
    screen = _screen(_user())
    screen.preview_button.click()
    from PyQt6.QtWidgets import QApplication as App

    assert any(isinstance(w, PreviewWindow) for w in App.topLevelWidgets())
    for w in App.topLevelWidgets():
        if isinstance(w, PreviewWindow):
            w.close()
