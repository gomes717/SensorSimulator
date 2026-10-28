"""Users-screen slice 6: the Food and Exercise pages — the table, and the add form
(gui/user_schedule_pages.py), and that the profile screen shows them."""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QTime
from PyQt6.QtWidgets import QApplication

from gui.user_profile_window import ProfileDeps, UserProfileWindow
from gui.user_schedule_pages import EXERCISE, FOOD, SchedulePage
from models import user_store
from models.types import ExerciseEvent, FoodEvent


@pytest.fixture(scope="module", autouse=True)
def _app():
    return QApplication.instance() or QApplication([])


def _user():
    user = user_store.new_user("Ana")
    user.food_events = []
    user.exercise_events = []
    return user


class _Env:
    def __init__(self, kind, user=None):
        self.user = user or _user()
        self.changes = 0
        self.page = SchedulePage(self.user, kind, self._changed)

    def _changed(self):
        self.changes += 1

    def rows(self):
        table = self.page.table
        out = []
        for r in range(table.rowCount()):
            cells = [table.item(r, c) for c in range(table.columnCount())]
            out.append(tuple(cell.text() for cell in cells if cell is not None))
        return out

    def add(self, hour, minute, first, second):
        self.page.time_edit.setTime(QTime(hour, minute))
        self.page.first_spin.setValue(first)
        self.page.second_spin.setValue(second)
        self.page.add_button.click()


# -- food ---------------------------------------------------------------------------------


def test_the_food_page_lists_the_users_meals():
    user = _user()
    user.food_events = [FoodEvent(480, 60.0, 20), FoodEvent(1140, 45.5, 30)]
    env = _Env(FOOD, user)
    assert env.rows() == [("08:00", "60", "20"), ("19:00", "45.5", "30")]


def test_adding_a_meal_updates_the_user_and_the_table():
    env = _Env(FOOD)
    env.add(8, 0, 60, 20)
    assert env.user.food_events == [FoodEvent(480, 60.0, 20)]
    assert env.rows() == [("08:00", "60", "20")]
    assert env.changes >= 1


def test_meals_added_out_of_order_are_listed_in_time_order():
    env = _Env(FOOD)
    env.add(19, 0, 45, 30)
    env.add(8, 0, 60, 20)
    assert [row[0] for row in env.rows()] == ["08:00", "19:00"]


def test_removing_the_selected_meal():
    user = _user()
    user.food_events = [FoodEvent(480, 60.0, 20), FoodEvent(1140, 45.0, 30)]
    env = _Env(FOOD, user)
    env.page.table.selectRow(0)
    env.page.remove_button.click()
    assert env.user.food_events == [FoodEvent(1140, 45.0, 30)]
    assert env.rows() == [("19:00", "45", "30")]


def test_remove_with_nothing_selected_does_nothing():
    user = _user()
    user.food_events = [FoodEvent(480, 60.0, 20)]
    env = _Env(FOOD, user)
    env.page.table.clearSelection()
    env.page.table.setCurrentCell(-1, -1)
    env.page.remove_button.click()
    assert len(env.user.food_events) == 1


def test_the_33rd_meal_is_refused_with_a_message():
    user = _user()
    user.food_events = [FoodEvent(i * 10, 10.0, 5) for i in range(32)]
    env = _Env(FOOD, user)
    env.add(23, 0, 10, 5)
    assert len(env.user.food_events) == 32
    assert "32" in env.page.message.text()


def test_a_successful_add_clears_an_earlier_message():
    user = _user()
    user.food_events = [FoodEvent(i * 10, 10.0, 5) for i in range(32)]
    env = _Env(FOOD, user)
    env.add(23, 0, 10, 5)
    env.page.table.selectRow(0)
    env.page.remove_button.click()
    env.add(23, 0, 10, 5)
    assert env.page.message.text() == ""


# -- exercise -----------------------------------------------------------------------------


def test_the_exercise_page_uses_duration_then_intensity():
    env = _Env(EXERCISE)
    first, second = (env.page.table.horizontalHeaderItem(i) for i in (1, 2))
    assert first is not None and first.text().startswith("Duration")
    assert second is not None and second.text().startswith("Intensity")
    env.add(18, 0, 45, 70)
    assert env.user.exercise_events == [ExerciseEvent(1080, 45, 70.0)]
    assert env.rows() == [("18:00", "45", "70")]


def test_food_and_exercise_pages_edit_their_own_lists():
    user = _user()
    food, exercise = _Env(FOOD, user), _Env(EXERCISE, user)
    food.add(8, 0, 60, 20)
    exercise.add(18, 0, 45, 70)
    assert len(user.food_events) == 1 and len(user.exercise_events) == 1


# -- following the user --------------------------------------------------------------------


def test_refresh_follows_changes_made_elsewhere():
    env = _Env(FOOD)
    env.user.food_events = [FoodEvent(600, 30.0, 10)]
    env.page.refresh()
    assert env.rows() == [("10:00", "30", "10")]


# -- inside the profile screen -------------------------------------------------------------


def test_the_profile_screen_has_every_page_in_menu_order():
    user = _user()
    win = UserProfileWindow(user, False, ProfileDeps([user], lambda: None, lambda _u, _old: None))
    items = [win.menu.item(i) for i in range(win.menu.count())]
    assert [item.text() for item in items if item is not None] == [
        "Profile",
        "CSV",
        "Food",
        "Exercise",
        "Model",
    ]


def test_a_meal_added_in_the_screen_makes_it_unsaved_and_saves_with_it():
    user = _user()
    users = [user]
    win = UserProfileWindow(user, False, ProfileDeps(users, lambda: None, lambda _u, _old: None))
    page = win.pages["food"]
    assert isinstance(page, SchedulePage)
    page.time_edit.setTime(QTime(8, 0))
    page.add_button.click()
    assert win.is_dirty
    assert win.save() is True
    assert len(users[0].food_events) == 1
    assert user.food_events == []  # the screen edited a copy


def test_a_meal_added_in_the_screen_shows_in_the_overview_at_once():
    user = _user()
    win = UserProfileWindow(user, False, ProfileDeps([user], lambda: None, lambda _u, _old: None))
    assert win.overview.meal_marks == 0
    page = win.pages["food"]
    page.time_edit.setTime(QTime(8, 0))
    page.first_spin.setValue(60)
    page.second_spin.setValue(20)
    page.add_button.click()
    assert win.overview.meal_marks == 1


def test_a_bout_added_and_a_meal_removed_in_the_screen_show_in_the_overview_at_once():
    user = _user()
    user.food_events = [FoodEvent(480, 60.0, 20)]
    win = UserProfileWindow(user, False, ProfileDeps([user], lambda: None, lambda _u, _old: None))
    exercise = win.pages["exercise"]
    exercise.time_edit.setTime(QTime(18, 0))
    exercise.first_spin.setValue(45)
    exercise.second_spin.setValue(70)
    exercise.add_button.click()
    assert win.overview.exercise_spans == 1
    food = win.pages["food"]
    food.table.selectRow(0)
    food.remove_button.click()
    assert win.overview.meal_marks == 0


# -- editing a row in place ----------------------------------------------------------------


def _two_meals():
    user = _user()
    user.food_events = [FoodEvent(480, 60.0, 20), FoodEvent(1140, 45.0, 30)]
    return _Env(FOOD, user)


def test_selecting_a_row_loads_it_into_the_fields():
    env = _two_meals()
    env.page.table.selectRow(1)
    assert env.page.time_edit.time() == QTime(19, 0)
    assert env.page.first_spin.value() == 45.0 and env.page.second_spin.value() == 30


def test_changing_a_field_changes_the_selected_row_without_add_and_remove():
    env = _two_meals()
    env.page.table.selectRow(0)
    env.page.first_spin.setValue(80.0)
    assert env.user.food_events == [FoodEvent(480, 80.0, 20), FoodEvent(1140, 45.0, 30)]
    assert env.rows()[0] == ("08:00", "80", "20")
    assert env.changes >= 1
    env.page.second_spin.setValue(40)
    assert env.user.food_events[0] == FoodEvent(480, 80.0, 40)  # still the same row, edited again


def test_changing_the_time_moves_the_row_in_order_and_keeps_it_selected():
    env = _two_meals()
    env.page.table.selectRow(0)
    env.page.time_edit.setTime(QTime(21, 0))
    assert [e.time_of_day_min for e in env.user.food_events] == [1140, 1260]
    assert env.page.table.currentRow() == 1
    env.page.first_spin.setValue(10.0)  # the moved meal is the one that keeps changing
    assert env.user.food_events[1] == FoodEvent(1260, 10.0, 20)
    assert env.user.food_events[0] == FoodEvent(1140, 45.0, 30)


def test_editing_leaves_the_event_count_alone():
    env = _two_meals()
    env.page.table.selectRow(0)
    env.page.first_spin.setValue(70.0)
    assert len(env.user.food_events) == 2 and env.page.table.rowCount() == 2


def test_after_add_nothing_is_selected_so_the_next_add_leaves_the_first_alone():
    env = _Env(FOOD)
    env.add(8, 0, 60, 20)
    assert env.page.table.selectionModel().selectedRows() == []
    env.add(13, 0, 30, 10)
    assert env.user.food_events == [FoodEvent(480, 60.0, 20), FoodEvent(780, 30.0, 10)]


def test_exercise_rows_edit_in_place_too():
    user = _user()
    user.exercise_events = [ExerciseEvent(1080, 45, 70.0)]
    env = _Env(EXERCISE, user)
    env.page.table.selectRow(0)
    env.page.second_spin.setValue(90.0)
    assert env.user.exercise_events == [ExerciseEvent(1080, 45, 90.0)]


def test_an_edit_in_the_screen_reaches_the_overview_at_once():
    user = _user()
    user.food_events = [FoodEvent(480, 60.0, 20)]
    win = UserProfileWindow(user, False, ProfileDeps([user], lambda: None, lambda _u, _old: None))
    page = win.pages["food"]
    page.table.selectRow(0)
    before = list(win.overview.glucose)
    page.first_spin.setValue(30.0)  # half the carbs
    assert win.overview.glucose != before  # the preview was recomputed
    assert page.table.currentRow() == 0  # the screen's own refresh did not lose the selection
    assert win.is_dirty
