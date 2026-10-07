"""Users-screen slice 8 (Send to…), pure part: the ordered writes that put a user on a board slot
(models/user_send.py). Start's push and Send share the model writes, so they cannot drift."""

import pytest

from api import protocol
from models import user_edit, user_send, user_sim, user_store
from models.types import CsvTrack, ExerciseEvent, FoodEvent, ModelId, SensorId


def _model_user():
    user = user_store.new_user("Ana")
    user_edit.set_model(user, ModelId.UVA_PADOVA)
    user_edit.set_sensor(user, SensorId.BRETON)
    user_edit.set_weight(user, 82.5)
    user.food_events = [FoodEvent(480, 60.0, 20), FoodEvent(720, 30.0, 15)]
    user.exercise_events = [ExerciseEvent(1080, 45, 70.0)]
    return user


def _csv_user(foodlog=((3600, 30.0),)):
    user = user_store.new_user("Csv")
    user.mode = "csv"
    user.csv = CsvTrack(
        samples=[100 + i % 50 for i in range(288)],
        interval_s=300,
        foodlog=list(foodlog),
        start_iso="2020-01-01T00:00:00",
        source_name="Dexcom_001.csv",
    )
    return user


def _keys(entry):
    return [key for key, _payload in entry["writes"]]


# -- a model user -------------------------------------------------------------------------


def test_a_model_user_is_written_name_first_then_the_models_then_the_schedules():
    entry = user_send.slot_entry(2, _model_user())
    assert entry["slot"] == 2
    assert _keys(entry) == [
        "user_name",
        "person",
        "sensor",
        "data_source",
        "food",  # clear
        "food",
        "food",
        "exercise",  # clear
        "exercise",
    ]
    assert entry["csv"] is None


def test_the_writes_carry_the_users_content():
    user = _model_user()
    writes = dict(user_send.slot_entry(0, user)["writes"])
    assert protocol.decode_user_name(writes["user_name"]) == "Ana"
    model_id, params = protocol.decode_person_config(writes["person"]) or (None, {})
    assert model_id == ModelId.UVA_PADOVA and params["BW"] == pytest.approx(82.5)
    sensor_id, _ = protocol.decode_sensor_config(writes["sensor"]) or (None, {})
    assert sensor_id == SensorId.BRETON
    assert protocol.decode_data_source(writes["data_source"]) is False  # off any earlier CSV


def test_the_schedules_are_cleared_before_they_are_written():
    user = _model_user()
    food = [p for key, p in user_send.slot_entry(0, user)["writes"] if key == "food"]
    assert food[0] == protocol.encode_clear_food()  # a stale meal on the board would drift
    assert food[1:] == [protocol.encode_food_event(e) for e in user.food_events]
    exercise = [p for key, p in user_send.slot_entry(0, user)["writes"] if key == "exercise"]
    assert exercise[0] == protocol.encode_clear_exercise()


def test_a_model_user_with_no_events_still_clears_both_lists():
    user = _model_user()
    user.food_events, user.exercise_events = [], []
    assert _keys(user_send.slot_entry(0, user))[-2:] == ["food", "exercise"]


def test_the_weight_is_what_is_written_as_bw():
    user = _model_user()
    user.model_params["BW"] = 40.0  # a stale copy; the weight is the truth
    user.weight_kg = 91.0
    writes = dict(user_send.slot_entry(0, user)["writes"])
    _, params = protocol.decode_person_config(writes["person"]) or (None, {})
    assert params["BW"] == pytest.approx(91.0)


# -- a CSV user -----------------------------------------------------------------------------


def test_a_csv_user_is_written_its_name_and_csv_mode_and_then_uploaded():
    entry = user_send.slot_entry(1, _csv_user())
    assert _keys(entry) == ["user_name", "data_source"]
    assert protocol.decode_data_source(dict(entry["writes"])["data_source"]) is True
    uploads = entry["csv"]["uploads"]
    assert [u["track"] for u in uploads] == [protocol.CSV_TRACK_GLUCOSE, protocol.CSV_TRACK_FOODLOG]
    assert uploads[0]["row_count"] == 288 and uploads[0]["interval_s"] == 300


def test_a_csv_user_does_not_overwrite_the_boards_model_config():
    keys = _keys(user_send.slot_entry(1, _csv_user()))
    assert "person" not in keys and "sensor" not in keys  # the recording drives it


def test_a_csv_user_without_a_food_log_uploads_only_the_glucose_track():
    uploads = user_send.slot_entry(1, _csv_user(foodlog=()))["csv"]["uploads"]
    assert [u["track"] for u in uploads] == [protocol.CSV_TRACK_GLUCOSE]


def test_a_csv_window_with_no_start_time_still_uploads():
    user = _csv_user()
    assert user.csv is not None
    user.csv.start_iso = None
    assert user_send.slot_entry(1, user)["csv"]["uploads"]


# -- refusals ---------------------------------------------------------------------------------


def test_a_csv_user_with_no_window_is_refused_with_a_reason():
    user = _csv_user()
    user.csv = None
    assert "CSV" in (user_send.refusal(user) or "")
    with pytest.raises(ValueError):
        user_send.slot_entry(0, user)


def test_a_csv_user_with_an_empty_window_is_refused():
    user = _csv_user()
    assert user.csv is not None
    user.csv.samples = []
    assert user_send.refusal(user) is not None


def test_a_good_user_is_not_refused():
    assert user_send.refusal(_model_user()) is None
    assert user_send.refusal(_csv_user()) is None


def test_a_name_the_board_cannot_hold_is_refused_before_anything_is_built():
    user = _model_user()
    user.name = "n" * 31
    assert "30" in (user_send.refusal(user) or "")
    with pytest.raises(ValueError):
        user_send.slot_entry(0, user)


def test_a_blank_name_is_refused():
    user = _model_user()
    user.name = "   "
    assert user_send.refusal(user) is not None


# -- Start's push and Send agree on the model writes ----------------------------------------


def test_start_and_send_write_the_same_model_inputs():
    from gui.start_push import slot_entry as start_entry

    user = _model_user()
    start = start_entry(0, user_sim.person_profile_of(user), user_sim.sensor_profile_of(user))
    send = user_send.slot_entry(0, user)
    assert send["writes"][1:] == start["writes"]  # Send is Start's writes with the name first
