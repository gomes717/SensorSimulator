"""Users-screen slice 4, pure part: turning one slot's board reading into a user, and the
three outcomes of reading it against the saved users (.scratch/users-screen/spec.md)."""

import struct
from dataclasses import replace

from models import user_board, user_store
from models.types import ExerciseEvent, FoodEvent, ModelId, SensorId


def _f32(x: float) -> float:
    return struct.unpack("<f", struct.pack("<f", x))[0]


def _reading(**changes) -> user_board.BoardReading:
    base = user_board.BoardReading(
        name="Ana",
        is_csv=False,
        model_id=ModelId.UVA_PADOVA,
        model_params={"BW": _f32(82.3), "VG": _f32(0.1)},
        sensor_id=SensorId.BRETON,
        sensor_params={"a": _f32(1.5)},
        food_events=[FoodEvent(480, _f32(60.0), 20)],
        exercise_events=[ExerciseEvent(1080, 45, _f32(70.0))],
    )
    return replace(base, **changes)


def _saved_like(reading: user_board.BoardReading):
    """A saved user holding exactly what *reading* says (but in full precision)."""
    user = user_board.user_from_reading(reading)
    return replace(user, id="saved-id", height_cm=171.0, picture="picture.png")


# -- the board user -----------------------------------------------------------


def test_a_reading_becomes_a_user_with_the_boards_inputs():
    reading = _reading()
    user = user_board.user_from_reading(reading)
    assert user.name == "Ana"
    assert user.mode == "model"
    assert (user.model_id, user.sensor_id) == (ModelId.UVA_PADOVA, SensorId.BRETON)
    assert user.model_params == reading.model_params
    assert user.food_events == reading.food_events
    assert user.exercise_events == reading.exercise_events
    assert user.weight_kg == reading.model_params["BW"]  # the weight is the board's BW
    assert user.height_cm is None and user.picture is None and user.csv is None


def test_a_csv_reading_is_a_csv_user_with_no_data():
    user = user_board.user_from_reading(_reading(is_csv=True))
    assert user.mode == "csv"
    assert user.csv is None  # the board cannot send the track back yet


def test_a_board_with_no_name_gives_an_unknown_user():
    assert user_board.user_from_reading(_reading(name="")).name == "Unknown"


def test_each_board_user_gets_its_own_id():
    a = user_board.user_from_reading(_reading())
    b = user_board.user_from_reading(_reading())
    assert a.id != b.id


# -- classify -----------------------------------------------------------------


def test_a_name_nobody_has_is_unknown():
    outcome = user_board.classify([user_store.new_user("Bo")], _reading(name="Ana"))
    assert isinstance(outcome, user_board.Unknown)
    assert outcome.board.name == "Ana"


def test_a_board_with_no_name_is_unknown_even_if_a_user_is_called_unknown():
    saved = [replace(user_store.new_user("Unknown"))]
    outcome = user_board.classify(saved, _reading(name=""))
    assert isinstance(outcome, user_board.Unknown)


def test_an_unknown_name_is_not_matched_by_its_parameters():
    # name only (decided 2026-10-06): same parameters under another name is still unknown
    saved = replace(_saved_like(_reading()), name="Someone else")
    assert isinstance(user_board.classify([saved], _reading()), user_board.Unknown)


def test_the_same_name_with_the_same_content_matches():
    saved = _saved_like(_reading())
    outcome = user_board.classify([saved], _reading())
    assert isinstance(outcome, user_board.Matches)
    assert outcome.saved is saved


def test_the_same_name_with_something_different_differs():
    saved = _saved_like(_reading())
    outcome = user_board.classify([saved], _reading(food_events=[]))
    assert isinstance(outcome, user_board.Differs)
    assert outcome.saved is saved
    assert outcome.differences == ["food schedule"]
    assert outcome.board.food_events == []


def test_the_lookup_is_by_exact_name():
    saved = _saved_like(_reading())
    assert isinstance(user_board.classify([saved], _reading(name="ana")), user_board.Unknown)


# -- overwrite / create -------------------------------------------------------


def test_overwrite_keeps_the_saved_identity_and_takes_the_boards_content():
    saved = _saved_like(_reading())
    outcome = user_board.classify([saved], _reading(food_events=[]))
    assert isinstance(outcome, user_board.Differs)
    out = user_board.overwrite(outcome)
    assert (out.id, out.picture, out.height_cm) == ("saved-id", "picture.png", 171.0)
    assert out.food_events == []


def test_create_makes_a_new_user_named_with_the_next_free_number():
    saved = _saved_like(_reading())
    outcome = user_board.classify([saved], _reading(food_events=[]))
    assert isinstance(outcome, user_board.Differs)
    copy = user_board.create_copy([saved], outcome)
    assert copy.name == "Ana#2"
    assert copy.id != "saved-id"  # a new user, not the saved one
    assert copy.food_events == []
    assert saved.name == "Ana"  # the saved one is untouched


def test_create_skips_names_already_taken():
    saved = _saved_like(_reading())
    taken = replace(user_store.new_user("Ana#2"))
    outcome = user_board.classify([saved, taken], _reading(food_events=[]))
    assert isinstance(outcome, user_board.Differs)
    assert user_board.create_copy([saved, taken], outcome).name == "Ana#3"
