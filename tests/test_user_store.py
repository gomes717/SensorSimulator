"""Users-screen slice 1: the User type, its on-disk store, and the one-time migration
from the old Person / Sensor profiles + board layout (.scratch/users-screen/spec.md)."""

import json
from pathlib import Path

from models import board_layout, user_store
from models import sensors as sensor_defaults
from models.engine import load_csv_window
from models.types import (
    CsvTrack,
    ExerciseEvent,
    FoodEvent,
    ModelId,
    PersonProfile,
    SensorId,
    SensorProfile,
    User,
)

_DEXCOM_HEADER = (
    "Index,Timestamp (YYYY-MM-DDThh:mm:ss),Event Type,Event Subtype,Patient Info,"
    "Device Info,Source Device ID,Glucose Value (mg/dL),Insulin Value (u),"
    "Carb Value (grams),Duration (hh:mm:ss),Glucose Rate of Change (mg/dL/min),"
    "Transmitter Time (Long Integer)"
)


def _write_dexcom(tmp_path: Path) -> Path:
    lines = [_DEXCOM_HEADER]
    for i in range(24 * 12):  # one day at the 5-minute Dexcom cadence
        minutes = i * 5
        ts = f"2020-01-01 {minutes // 60:02d}:{minutes % 60:02d}:00"
        lines.append(f"{i + 1},{ts},EGV,,,,iPhone G6,{100 + i % 40},,,,,")
    path = tmp_path / "Dexcom_007.csv"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _model_person(name="Ana", model_id=ModelId.UVA_PADOVA) -> PersonProfile:
    return PersonProfile(
        name=name,
        model_id=model_id,
        params={"BW": 82.5, "VG": 1.9},
        food_events=[FoodEvent(time_of_day_min=480, carbs_g=60.0, duration_min=20)],
        exercise_events=[ExerciseEvent(time_of_day_min=1080, duration_min=45, intensity_pct=70.0)],
        basal_u_per_h=1.1,
    )


def _sensor(name="Breton") -> SensorProfile:
    return SensorProfile(name=name, sensor_id=SensorId.BRETON, params={"a": 1.5})


def _pairs(*assignments: tuple[str | None, str | None]) -> list[tuple[str | None, str | None]]:
    """What the old layout file recorded: each slot's (person, sensor) profile names."""
    return list(assignments)


def _layout(*people: str | None) -> board_layout.BoardLayout:
    """The layout now: each slot records only the user it runs."""
    return board_layout.BoardLayout(slots=[board_layout.SlotAssignment(person=p) for p in people])


def _write_legacy_layout(*assignments: tuple[str | None, str | None]) -> None:
    """Put an old-format layout file (person + sensor per slot) where the app reads it."""
    board_layout._LAYOUT_FILE.write_text(
        json.dumps({"slots": [{"person": p, "sensor": s} for p, s in assignments]}),
        encoding="utf-8",
    )


# -- the store ---------------------------------------------------------------


def test_load_with_nothing_saved_is_empty():
    assert user_store.load() == []


def test_a_new_user_gets_its_own_id_and_model_defaults():
    a, b = user_store.new_user("A"), user_store.new_user("B")
    assert a.id != b.id
    assert a.mode == "model"
    assert a.csv is None
    assert a.weight_kg == a.model_params["BW"]  # the model's own default weight


def test_save_then_load_round_trips_every_field(tmp_path):
    user = User(
        id="u1",
        name="Ana",
        height_cm=171.0,
        weight_kg=82.5,
        picture="picture.png",
        mode="csv",
        model_id=ModelId.ROYPARKER,
        model_params={"BW": 82.5, "Gpeq": 5.5},
        sensor_id=SensorId.FACCHINETTI,
        sensor_params={"p": 2.0},
        food_events=[FoodEvent(60, 30.0, 15)],
        exercise_events=[ExerciseEvent(120, 30, 40.0)],
        basal_u_per_h=0.9,
        csv=CsvTrack(
            samples=[100, 101, 102],
            interval_s=300,
            foodlog=[(600, 45.0)],
            start_iso="2020-01-01T00:00:00",
            source_name="Dexcom_007.csv",
        ),
    )
    user_store.save([user])
    assert user_store.load() == [user]


def test_the_csv_window_is_kept_in_the_users_own_folder(tmp_path):
    user = user_store.new_user("Ana")
    user.csv = CsvTrack(samples=[1, 2], interval_s=300, foodlog=[], start_iso=None)
    user_store.save([user])
    assert (user_store.user_dir(user) / "csv.json").is_file()
    assert "samples" not in (user_store._USERS_FILE).read_text(encoding="utf-8")


def test_a_user_without_csv_has_no_csv_file_and_loads_with_none():
    user = user_store.new_user("Ana")
    user_store.save([user])
    assert not (user_store.user_dir(user) / "csv.json").exists()
    assert user_store.load()[0].csv is None


def test_save_removes_a_csv_file_the_user_no_longer_has():
    user = user_store.new_user("Ana")
    user.csv = CsvTrack(samples=[1], interval_s=300, foodlog=[], start_iso=None)
    user_store.save([user])
    user.csv = None
    user_store.save([user])
    assert not (user_store.user_dir(user) / "csv.json").exists()


def test_delete_removes_the_users_folder_and_nothing_else():
    keep, gone = user_store.new_user("Keep"), user_store.new_user("Gone")
    for user in (keep, gone):
        user.csv = CsvTrack(samples=[1], interval_s=300, foodlog=[], start_iso=None)
    user_store.save([keep, gone])
    user_store.delete(gone)
    assert not user_store.user_dir(gone).exists()
    assert (user_store.user_dir(keep) / "csv.json").is_file()


def test_delete_of_a_user_that_was_never_saved_is_harmless():
    user_store.delete(user_store.new_user("Never saved"))


# -- migration ---------------------------------------------------------------


def test_a_person_becomes_a_user_with_its_models_and_schedule():
    person = _model_person()
    [user] = user_store.migrate([person], [], _pairs())
    assert (user.name, user.model_id, user.mode) == ("Ana", ModelId.UVA_PADOVA, "model")
    assert user.model_params == person.params
    assert user.food_events == person.food_events
    assert user.exercise_events == person.exercise_events
    assert user.basal_u_per_h == 1.1
    assert user.weight_kg == 82.5  # the BW the person already had
    assert user.height_cm is None


def test_the_user_takes_the_sensor_its_slot_used():
    [user] = user_store.migrate(
        [_model_person()], [_sensor()], _pairs((None, None), ("Ana", "Breton"))
    )
    assert user.sensor_id == SensorId.BRETON
    assert user.sensor_params == {"a": 1.5}


def test_an_unassigned_person_gets_the_default_ideal_sensor():
    [user] = user_store.migrate([_model_person()], [_sensor()], _pairs())
    assert user.sensor_id == SensorId.IDEAL
    assert user.sensor_params == sensor_defaults.ideal_default_params()


def test_a_person_on_several_slots_takes_the_first_slots_sensor():
    other = SensorProfile(name="Other", sensor_id=SensorId.FACCHINETTI, params={})
    pairs = _pairs(("Ana", "Breton"), ("Ana", "Other"))
    [user] = user_store.migrate([_model_person()], [_sensor(), other], pairs)
    assert user.sensor_id == SensorId.BRETON


def test_a_csv_person_gets_the_window_copied_into_the_user(tmp_path):
    path = _write_dexcom(tmp_path)
    person = PersonProfile(
        name="Csv",
        model_id=ModelId.CAMBRIDGE,
        params={"BW": 70.0},
        data_source="csv",
        csv_path=str(path),
        csv_window_start_iso="2020-01-01T00:00:00",
    )
    [user] = user_store.migrate([person], [], _pairs())
    samples, interval_s, foodlog = load_csv_window(person)
    assert user.mode == "csv"
    assert user.csv == CsvTrack(
        samples=samples,
        interval_s=interval_s,
        foodlog=foodlog,
        start_iso="2020-01-01T00:00:00",
        source_name="Dexcom_007.csv",
    )
    assert len(samples) > 0


def test_a_csv_person_whose_file_is_gone_keeps_csv_mode_with_no_data(tmp_path):
    person = PersonProfile(
        name="Csv",
        model_id=ModelId.CAMBRIDGE,
        params={"BW": 70.0},
        data_source="csv",
        csv_path=str(tmp_path / "missing.csv"),
        csv_window_start_iso="2020-01-01T00:00:00",
    )
    [user] = user_store.migrate([person], [], _pairs())
    assert user.mode == "csv"
    assert user.csv is None


def test_a_model_person_that_once_picked_a_csv_does_not_carry_it_over(tmp_path):
    person = _model_person()
    person.csv_path = str(_write_dexcom(tmp_path))
    person.csv_window_start_iso = "2020-01-01T00:00:00"
    [user] = user_store.migrate([person], [], _pairs())
    assert user.mode == "model"
    assert user.csv is None


def test_slot_user_ids_follow_the_old_layout_by_name():
    users = user_store.migrate([_model_person("Ana"), _model_person("Bo")], [], _pairs())
    ana, bo = users
    layout = _layout("Bo", "Gone", "Ana")
    assert user_store.slot_user_ids(users, layout) == [bo.id, None, ana.id]
    assert user_store.slot_user_ids(users, _layout()) == [None, None, None]


# -- first run of the new store ----------------------------------------------


def test_first_load_migrates_the_old_profiles_and_saves_them():
    from models import profile_store

    profile_store.save([_model_person()], [_sensor()])
    _write_legacy_layout(("Ana", "Breton"))
    users = user_store.load_or_migrate()
    assert [u.name for u in users] == ["Ana"]
    assert user_store.load() == users  # now on disk


def test_the_old_files_are_left_alone_as_a_backup():
    from models import profile_store

    profile_store.save([_model_person()], [_sensor()])
    before = profile_store._PROFILES_FILE.read_bytes()
    user_store.load_or_migrate()
    assert profile_store._PROFILES_FILE.read_bytes() == before


def test_the_second_load_does_not_migrate_again():
    from models import profile_store

    profile_store.save([_model_person("Ana")], [])
    first = user_store.load_or_migrate()
    first[0].name = "Renamed"
    user_store.save(first)
    profile_store.save([_model_person("Ana"), _model_person("New")], [])
    assert [u.name for u in user_store.load_or_migrate()] == ["Renamed"]


def test_a_store_saved_empty_stays_empty_instead_of_migrating():
    from models import profile_store

    user_store.save([])
    profile_store.save([_model_person()], [])
    assert user_store.load_or_migrate() == []


def test_the_file_format_is_versioned():
    user_store.save([user_store.new_user("Ana")])
    data = json.loads(user_store._USERS_FILE.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert data["users"][0]["name"] == "Ana"
