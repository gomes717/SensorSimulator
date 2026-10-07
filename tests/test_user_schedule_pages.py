"""Users-screen slice 6: the Food and Exercise pages — the table, the add form, and the 24 h graph
(gui/user_schedule_pages.py, gui/schedule_graph.py), and that the profile screen shows them."""

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


def test_adding_a_meal_updates_the_user_the_table_and_the_graph():
    env = _Env(FOOD)
    env.add(8, 0, 60, 20)
    assert env.user.food_events == [FoodEvent(480, 60.0, 20)]
    assert env.rows() == [("08:00", "60", "20")]
    assert env.changes >= 1
    values = env.page.graph.values
    assert len(values) == 1440
    assert values[480] == pytest.approx(3.0)  # 60 g over 20 min
    assert values[479] == 0.0


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
    assert env.page.graph.values[480] == 0.0


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
    values = env.page.graph.values
    assert values[1080] == pytest.approx(70.0)
    assert values[1125] == 0.0


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
    assert env.page.graph.values[600] == pytest.approx(3.0)


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
