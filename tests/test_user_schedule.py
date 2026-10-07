"""Users-screen slice 6, pure part: the 24 h food and exercise curves the pages draw, and adding
and removing events on a user (models/user_schedule.py, models/user_edit.py)."""

import pytest

from models import user_edit, user_schedule, user_store
from models.types import ExerciseEvent, FoodEvent


def _near(values, minute, expected):
    assert values[minute] == pytest.approx(expected), f"minute {minute}"


# -- food: grams per minute over the day ---------------------------------------------


def test_a_day_has_1440_minutes():
    assert len(user_schedule.food_rate_series([])) == 1440
    assert len(user_schedule.exercise_series([])) == 1440


def test_no_meals_is_a_flat_zero_day():
    assert set(user_schedule.food_rate_series([])) == {0.0}


def test_a_meal_is_spread_evenly_over_its_duration():
    rate = user_schedule.food_rate_series([FoodEvent(480, 60.0, 20)])
    _near(rate, 479, 0.0)
    _near(rate, 480, 3.0)  # 60 g over 20 min
    _near(rate, 499, 3.0)
    _near(rate, 500, 0.0)  # the window is [start, start + duration)
    assert sum(rate) == pytest.approx(60.0)  # nothing is lost or invented


def test_overlapping_meals_add_up():
    rate = user_schedule.food_rate_series([FoodEvent(480, 60.0, 20), FoodEvent(490, 30.0, 10)])
    _near(rate, 485, 3.0)
    _near(rate, 495, 3.0 + 3.0)


def test_a_meal_that_crosses_midnight_wraps_to_the_start_of_the_day():
    rate = user_schedule.food_rate_series([FoodEvent(1430, 40.0, 20)])  # 23:50 for 20 min
    _near(rate, 1430, 2.0)
    _near(rate, 1439, 2.0)
    _near(rate, 0, 2.0)
    _near(rate, 9, 2.0)
    _near(rate, 10, 0.0)
    assert sum(rate) == pytest.approx(40.0)


def test_a_meal_with_no_duration_does_not_divide_by_zero():
    rate = user_schedule.food_rate_series([FoodEvent(480, 60.0, 0)])
    assert sum(rate) == pytest.approx(60.0)  # treated as one minute


# -- exercise: intensity over the day ----------------------------------------------------


def test_an_exercise_bout_holds_its_intensity_for_its_duration():
    level = user_schedule.exercise_series([ExerciseEvent(1080, 45, 70.0)])
    _near(level, 1079, 0.0)
    _near(level, 1080, 70.0)
    _near(level, 1124, 70.0)
    _near(level, 1125, 0.0)


def test_overlapping_bouts_take_the_stronger_one():
    level = user_schedule.exercise_series(
        [ExerciseEvent(600, 60, 40.0), ExerciseEvent(630, 60, 80.0)]
    )
    _near(level, 610, 40.0)
    _near(level, 640, 80.0)  # the board feeds the model the matching bout, not the sum
    _near(level, 689, 80.0)
    _near(level, 690, 0.0)


def test_a_bout_that_crosses_midnight_wraps():
    level = user_schedule.exercise_series([ExerciseEvent(1420, 40, 50.0)])
    _near(level, 1439, 50.0)
    _near(level, 19, 50.0)
    _near(level, 20, 0.0)


# -- adding and removing events -----------------------------------------------------------


def _user():
    user = user_store.new_user("Ana")
    user.food_events = []
    user.exercise_events = []
    return user


def test_meals_are_kept_in_time_order():
    user = _user()
    user_edit.add_food_event(user, FoodEvent(720, 30.0, 15))
    user_edit.add_food_event(user, FoodEvent(480, 60.0, 20))
    assert [e.time_of_day_min for e in user.food_events] == [480, 720]


def test_a_meal_can_be_removed_by_position():
    user = _user()
    for minute in (480, 720, 1140):
        user_edit.add_food_event(user, FoodEvent(minute, 30.0, 15))
    user_edit.remove_food_event(user, 1)
    assert [e.time_of_day_min for e in user.food_events] == [480, 1140]


def test_removing_a_position_that_is_not_there_does_nothing():
    user = _user()
    user_edit.add_food_event(user, FoodEvent(480, 30.0, 15))
    user_edit.remove_food_event(user, 5)
    user_edit.remove_food_event(user, -1)
    assert len(user.food_events) == 1


def test_the_board_holds_32_meals_and_the_33rd_is_refused():
    user = _user()
    for i in range(32):
        user_edit.add_food_event(user, FoodEvent(i * 10, 10.0, 5))
    with pytest.raises(ValueError, match="32"):
        user_edit.add_food_event(user, FoodEvent(1000, 10.0, 5))
    assert len(user.food_events) == 32


def test_exercise_bouts_are_kept_in_time_order_and_removable():
    user = _user()
    user_edit.add_exercise_event(user, ExerciseEvent(1080, 45, 70.0))
    user_edit.add_exercise_event(user, ExerciseEvent(420, 30, 50.0))
    assert [e.time_of_day_min for e in user.exercise_events] == [420, 1080]
    user_edit.remove_exercise_event(user, 0)
    assert [e.time_of_day_min for e in user.exercise_events] == [1080]


def test_the_board_holds_32_exercise_bouts_and_the_33rd_is_refused():
    user = _user()
    for i in range(32):
        user_edit.add_exercise_event(user, ExerciseEvent(i * 10, 5, 50.0))
    with pytest.raises(ValueError, match="32"):
        user_edit.add_exercise_event(user, ExerciseEvent(1000, 5, 50.0))
