"""Users-screen slice 2: board-vs-saved comparison, "Ana#2" naming, and the 24 h preview
(.scratch/users-screen/spec.md). All pure — no Qt, no board."""

import struct
from dataclasses import replace

from models import user_match, user_sim, user_store
from models.types import (
    CsvTrack,
    ExerciseEvent,
    FoodEvent,
    ModelId,
    SensorId,
    User,
)


def _f32(x: float) -> float:
    return struct.unpack("<f", struct.pack("<f", x))[0]


def _user(**changes) -> User:
    base = replace(
        user_store.new_user("Ana"),
        food_events=[FoodEvent(480, 60.0, 20)],
        exercise_events=[ExerciseEvent(1080, 45, 70.0)],
    )
    return replace(base, **changes)


def _as_the_board_reports(user: User) -> User:
    """The user as a read-back returns it: every float went through float32."""
    return replace(
        user,
        model_params={k: _f32(v) for k, v in user.model_params.items()},
        sensor_params={k: _f32(v) for k, v in user.sensor_params.items()},
        food_events=[
            FoodEvent(e.time_of_day_min, _f32(e.carbs_g), e.duration_min) for e in user.food_events
        ],
        exercise_events=[
            ExerciseEvent(e.time_of_day_min, e.duration_min, _f32(e.intensity_pct))
            for e in user.exercise_events
        ],
    )


# -- compare -----------------------------------------------------------------


def test_a_user_matches_its_own_float32_readback():
    saved = _user(model_params={"BW": 82.3, "VG": 0.1})  # 82.3 and 0.1 are not float32-exact
    assert user_match.compare(saved, _as_the_board_reports(saved)) == []


def test_a_changed_parameter_is_reported():
    saved = _user(model_params={"BW": 82.3})
    board = _as_the_board_reports(replace(saved, model_params={"BW": 90.0}))
    assert user_match.compare(saved, board) == ["model parameters"]


def test_a_different_model_is_reported_as_the_model():
    saved = _user()
    board = replace(_as_the_board_reports(saved), model_id=ModelId.UVA_PADOVA)
    assert "model" in user_match.compare(saved, board)


def test_a_different_sensor_and_its_parameters_are_reported():
    saved = _user()
    board = replace(_as_the_board_reports(saved), sensor_id=SensorId.BRETON)
    assert "sensor" in user_match.compare(saved, board)


def test_schedules_are_compared_regardless_of_order():
    saved = _user(food_events=[FoodEvent(480, 60.0, 20), FoodEvent(720, 30.0, 15)])
    board = _as_the_board_reports(replace(saved, food_events=list(reversed(saved.food_events))))
    assert user_match.compare(saved, board) == []


def test_a_missing_meal_is_reported():
    saved = _user(food_events=[FoodEvent(480, 60.0, 20), FoodEvent(720, 30.0, 15)])
    board = _as_the_board_reports(replace(saved, food_events=saved.food_events[:1]))
    assert user_match.compare(saved, board) == ["food schedule"]


def test_a_different_exercise_is_reported():
    saved = _user()
    board = _as_the_board_reports(replace(saved, exercise_events=[]))
    assert user_match.compare(saved, board) == ["exercise schedule"]


def test_a_different_mode_is_the_only_difference_reported():
    saved = _user(mode="model")
    board = replace(_as_the_board_reports(saved), mode="csv", model_params={"BW": 1.0})
    assert user_match.compare(saved, board) == ["mode"]


def test_in_csv_mode_the_unused_model_inputs_are_not_compared():
    saved = _user(mode="csv")
    board = replace(_as_the_board_reports(saved), model_params={"BW": 1.0}, food_events=[])
    assert user_match.compare(saved, board) == []


def test_the_csv_track_is_not_compared_while_the_board_cannot_send_it():
    track = CsvTrack(samples=[100, 101], interval_s=300, foodlog=[])
    saved = _user(mode="csv", csv=track)
    board = replace(_as_the_board_reports(saved), csv=None)
    assert user_match.compare(saved, board) == []


def test_height_picture_and_id_are_not_compared():
    saved = _user(height_cm=171.0, picture="picture.png")
    board = replace(_as_the_board_reports(saved), id="other", height_cm=None, picture=None)
    assert user_match.compare(saved, board) == []


# -- overwrite ---------------------------------------------------------------


def test_overwrite_takes_the_boards_inputs_and_keeps_what_the_board_cannot_hold():
    saved = _user(height_cm=171.0, picture="picture.png", basal_u_per_h=0.9)
    board = replace(
        _as_the_board_reports(saved),
        id="board-id",
        model_id=ModelId.ROYPARKER,
        model_params={"BW": 77.0, "Gpeq": 5.0},
        food_events=[],
    )
    out = user_match.apply_board(saved, board)
    assert (out.id, out.name, out.height_cm, out.picture) == (saved.id, "Ana", 171.0, "picture.png")
    assert out.basal_u_per_h == 0.9
    assert out.model_id == ModelId.ROYPARKER
    assert out.model_params == {"BW": 77.0, "Gpeq": 5.0}
    assert out.food_events == []
    assert out.weight_kg == 77.0  # the weight follows the board's BW


def test_overwrite_keeps_the_saved_csv_when_the_board_sent_none():
    track = CsvTrack(samples=[100, 101], interval_s=300, foodlog=[])
    saved = _user(mode="csv", csv=track)
    out = user_match.apply_board(saved, replace(saved, csv=None))
    assert out.csv == track


def test_overwrite_does_not_change_the_saved_object():
    saved = _user()
    user_match.apply_board(saved, replace(saved, model_params={"BW": 1.0}))
    assert saved.model_params != {"BW": 1.0}


# -- names -------------------------------------------------------------------


def test_the_next_free_name_adds_a_number():
    assert user_match.next_free_name("Ana", {"Ana"}) == "Ana#2"


def test_the_next_free_name_skips_taken_numbers():
    assert user_match.next_free_name("Ana", {"Ana", "Ana#2", "Ana#3"}) == "Ana#4"


def test_a_numbered_name_is_renumbered_not_stacked():
    assert user_match.next_free_name("Ana#2", {"Ana", "Ana#2"}) == "Ana#3"


def test_a_free_gap_is_filled():
    assert user_match.next_free_name("Ana", {"Ana", "Ana#3"}) == "Ana#2"


def test_clip_name_limits_utf8_bytes_without_splitting_a_character():
    assert user_match.clip_name("a" * 40) == "a" * 30
    clipped = user_match.clip_name("é" * 20)  # 2 bytes each -> 15 characters
    assert clipped == "é" * 15
    assert len(clipped.encode("utf-8")) <= 30


def test_a_long_name_is_shortened_to_make_room_for_the_number():
    out = user_match.next_free_name("n" * 30, {"n" * 30})
    assert out.endswith("#2")
    assert len(out.encode("utf-8")) <= 30


# -- the profile the engine runs ----------------------------------------------


def test_the_engine_profile_carries_the_users_weight_as_bw():
    user = _user(weight_kg=55.0, model_params={"BW": 82.0, "VG": 0.15})
    profile = user_sim.person_profile_of(user)
    assert profile.params["BW"] == 55.0
    assert profile.params["VG"] == 0.15
    assert user.model_params["BW"] == 82.0  # the user itself is untouched
    assert profile.food_events == user.food_events
    assert profile.data_source == "model"


def test_a_user_without_a_weight_keeps_the_models_bw():
    user = _user(weight_kg=None, model_params={"BW": 82.0})
    assert user_sim.person_profile_of(user).params["BW"] == 82.0


# -- the 24 h preview ---------------------------------------------------------


def test_preview_covers_24_hours_at_one_minute(capsys):
    minutes, glucose = user_sim.preview_24h(_user(food_events=[], exercise_events=[]))
    assert len(minutes) == len(glucose) == 1440
    assert minutes[0] == 1 and minutes[-1] == 1440
    assert capsys.readouterr().out == ""  # the engine's per-tick log stays out of the app log


def test_preview_without_events_stays_flat():
    _, glucose = user_sim.preview_24h(_user(food_events=[], exercise_events=[]))
    assert max(glucose) - min(glucose) < 1.0


def test_preview_shows_the_meal():
    minutes, glucose = user_sim.preview_24h(_user(exercise_events=[]))
    before = [g for m, g in zip(minutes, glucose, strict=True) if m <= 480]
    after = [g for m, g in zip(minutes, glucose, strict=True) if m > 480]
    assert max(before) - min(before) < 1.0
    assert max(after) > max(before) + 20.0


def test_preview_is_the_same_every_time_it_is_asked():
    user = _user()
    assert user_sim.preview_24h(user) == user_sim.preview_24h(user)


def test_preview_of_a_csv_user_is_the_recorded_window():
    track = CsvTrack(samples=[100, 110, 120], interval_s=300, foodlog=[])
    assert user_sim.preview_24h(_user(mode="csv", csv=track)) == (
        [0.0, 5.0, 10.0],
        [100.0, 110.0, 120.0],
    )


def test_preview_of_a_csv_user_with_no_data_is_empty():
    assert user_sim.preview_24h(_user(mode="csv", csv=None)) == ([], [])


# -- the profiles the engine and the board push run on ------------------------------------------


def test_a_model_users_profile_has_no_csv_window():
    profile = user_sim.person_profile_of(_user())
    assert profile.data_source == "model" and profile.csv_track is None


def test_a_csv_users_profile_carries_its_window_not_a_file_path():
    track = CsvTrack(samples=[100, 110, 120], interval_s=300, foodlog=[(600, 30.0)])
    profile = user_sim.person_profile_of(_user(mode="csv", csv=track))
    assert profile.data_source == "csv"
    assert profile.csv_track is track
    assert profile.csv_path is None


def test_the_engine_replays_a_users_window_and_loops_it():
    from models.engine import ModelStepper

    track = CsvTrack(samples=[100, 110, 120], interval_s=300, foodlog=[])
    stepper = ModelStepper(user_sim.person_profile_of(_user(mode="csv", csv=track)))
    assert stepper.mode == "csv"
    got = [round(stepper.tick(5.0, "2020-01-01T00:00:00+00:00").glucose) for _ in range(7)]
    assert got == [100, 110, 120, 100, 110, 120, 100]  # one sample per 5 min, then it restarts


def test_a_csv_user_with_no_window_runs_no_model():
    from models.engine import ModelStepper

    stepper = ModelStepper(user_sim.person_profile_of(_user(mode="csv", csv=None)))
    assert stepper.mode == "idle"


def test_the_sensor_profile_carries_the_users_noise_model():
    user = _user(sensor_id=SensorId.BRETON, sensor_params={"sigma": 2.5})
    sensor = user_sim.sensor_profile_of(user)
    assert (sensor.sensor_id, sensor.params, sensor.name) == (
        SensorId.BRETON,
        {"sigma": 2.5},
        "Ana",
    )
    sensor.params["sigma"] = 9.0
    assert user.sensor_params["sigma"] == 2.5  # a copy


# -- comparing a recorded window now the board can send it back ----------------------------------


def _csv_user(samples=(100, 110, 120), foodlog=((600, 30.0),), **changes):
    track = CsvTrack(samples=list(samples), interval_s=300, foodlog=list(foodlog))
    return _user(mode="csv", csv=track, **changes)


def test_the_same_recorded_window_matches():
    saved = _csv_user(foodlog=((600, 30.1),))
    board = _csv_user(foodlog=((600, _f32(30.1)),))  # carbs came back through float32
    assert user_match.compare(saved, board) == []


def test_a_different_recorded_sample_is_reported():
    assert user_match.compare(_csv_user(), _csv_user(samples=(100, 111, 120))) == ["CSV window"]


def test_a_different_interval_or_length_is_reported():
    saved = _csv_user()
    longer = _csv_user(samples=(100, 110, 120, 130))
    assert user_match.compare(saved, longer) == ["CSV window"]


def test_a_different_food_log_is_reported():
    assert user_match.compare(_csv_user(), _csv_user(foodlog=((600, 99.0),))) == ["CSV window"]


def test_a_saved_user_with_no_window_differs_from_a_board_that_has_one():
    saved = _user(mode="csv", csv=None)
    assert user_match.compare(saved, _csv_user()) == ["CSV window"]


def test_a_board_that_could_not_send_its_window_is_not_compared():
    board = _user(mode="csv", csv=None)  # e.g. an older firmware
    assert user_match.compare(_csv_user(), board) == []


def test_overwrite_takes_the_boards_window():
    saved = _csv_user()
    board = _csv_user(samples=(1, 2, 3))
    assert user_match.apply_board(saved, board).csv == board.csv
