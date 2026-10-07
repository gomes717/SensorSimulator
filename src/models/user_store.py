"""JSON persistence for saved Users (``data/users.json`` + ``data/users/<id>/``).

A user's scalar fields and schedules live in ``users.json``; its recorded CSV
window is its own file, ``data/users/<id>/csv.json``, so a 288-sample track does not
bloat the index. (The profile picture goes in the same folder.)

The first run of this store migrates the old Person / Sensor profiles (``profile_store``) and the
board layout (read raw, for the sensor each slot used) once; those files are left untouched as a
backup and never read again.
"""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

from models import board_layout, cambridge, profile_store
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

_VERSION = 1
_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_USERS_FILE = _DATA_DIR / "users.json"
_USERS_DIR = _DATA_DIR / "users"
_CSV_NAME = "csv.json"


def user_dir(user: User) -> Path:
    """The folder holding *user*'s picture and CSV (not created until something is saved)."""
    return _USERS_DIR / user.id


def new_user(name: str) -> User:
    """A fresh model-mode user: Cambridge + Ideal sensor, the model's default weight."""
    params = cambridge.default_params()
    return User(
        id=uuid.uuid4().hex,
        name=name,
        weight_kg=params.get("BW"),
        model_id=ModelId.CAMBRIDGE,
        model_params=params,
        sensor_id=SensorId.IDEAL,
        sensor_params=sensor_defaults.ideal_default_params(),
    )


# -- (de)serialisation -------------------------------------------------------


def _user_to_dict(user: User) -> dict:
    return {
        "id": user.id,
        "name": user.name,
        "height_cm": user.height_cm,
        "weight_kg": user.weight_kg,
        "picture": user.picture,
        "mode": user.mode,
        "model_id": int(user.model_id),
        "model_params": user.model_params,
        "sensor_id": int(user.sensor_id),
        "sensor_params": user.sensor_params,
        "food_events": [vars(e) for e in user.food_events],
        "exercise_events": [vars(e) for e in user.exercise_events],
        "basal_u_per_h": user.basal_u_per_h,
    }


def _user_from_dict(d: dict, csv: CsvTrack | None) -> User:
    return User(
        id=d["id"],
        name=d["name"],
        height_cm=d.get("height_cm"),
        weight_kg=d.get("weight_kg"),
        picture=d.get("picture"),
        mode=d.get("mode", "model"),
        model_id=ModelId(d["model_id"]),
        model_params=dict(d.get("model_params", {})),
        sensor_id=SensorId(d["sensor_id"]),
        sensor_params=dict(d.get("sensor_params", {})),
        food_events=[FoodEvent(**e) for e in d.get("food_events", [])],
        exercise_events=[ExerciseEvent(**e) for e in d.get("exercise_events", [])],
        basal_u_per_h=d.get("basal_u_per_h"),
        csv=csv,
    )


def _csv_to_dict(csv: CsvTrack) -> dict:
    return {
        "samples": csv.samples,
        "interval_s": csv.interval_s,
        "foodlog": [list(item) for item in csv.foodlog],
        "start_iso": csv.start_iso,
        "source_name": csv.source_name,
    }


def _csv_from_dict(d: dict) -> CsvTrack:
    return CsvTrack(
        samples=[int(v) for v in d["samples"]],
        interval_s=int(d["interval_s"]),
        foodlog=[(int(off), float(carbs)) for off, carbs in d.get("foodlog", [])],
        start_iso=d.get("start_iso"),
        source_name=d.get("source_name"),
    )


def _read_csv(user_id: str) -> CsvTrack | None:
    path = _USERS_DIR / user_id / _CSV_NAME
    if not path.is_file():
        return None
    try:
        return _csv_from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (ValueError, KeyError, OSError):
        return None


# -- public API --------------------------------------------------------------


def exists() -> bool:
    """Whether the users file has ever been written (even as an empty list)."""
    return _USERS_FILE.is_file()


def load() -> list[User]:
    """The saved users, or an empty list if none have been saved yet."""
    if not _USERS_FILE.is_file():
        return []
    data = json.loads(_USERS_FILE.read_text(encoding="utf-8"))
    return [_user_from_dict(d, _read_csv(d["id"])) for d in data.get("users", [])]


def save(users: list[User]) -> None:
    """Persist *users*; a user without a CSV window has no ``csv.json``."""
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    for user in users:
        csv_file = user_dir(user) / _CSV_NAME
        if user.csv is None:
            csv_file.unlink(missing_ok=True)
        else:
            csv_file.parent.mkdir(parents=True, exist_ok=True)
            csv_file.write_text(json.dumps(_csv_to_dict(user.csv)), encoding="utf-8")
    payload = {"version": _VERSION, "users": [_user_to_dict(u) for u in users]}
    _USERS_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def delete(user: User) -> None:
    """Remove *user*'s data folder (picture, CSV). The caller drops the user from the list it
    saves; nothing happens if the folder was never created."""
    shutil.rmtree(user_dir(user), ignore_errors=True)


# -- migration from Person + Sensor profiles ----------------------------------


def _slot_sensor_names(pairs: list[tuple[str | None, str | None]]) -> dict[str, str]:
    """person name -> the sensor profile its first slot used."""
    names: dict[str, str] = {}
    for person, sensor in pairs:
        if person and sensor and person not in names:
            names[person] = sensor
    return names


def _csv_track_of(person: PersonProfile) -> CsvTrack | None:
    samples, interval_s, foodlog = load_csv_window(person)
    if not samples:
        return None
    return CsvTrack(
        samples=samples,
        interval_s=interval_s,
        foodlog=foodlog,
        start_iso=person.csv_window_start_iso,
        source_name=Path(person.csv_path).name if person.csv_path else None,
    )


def migrate(
    persons: list[PersonProfile],
    sensors: list[SensorProfile],
    slot_pairs: list[tuple[str | None, str | None]],
) -> list[User]:
    """One User per Person. Its sensor is the one its slot used (*slot_pairs* are each slot's
    ``(person, sensor)`` names; the first slot, when a person was on several), else the default
    Ideal sensor; a CSV person's window is copied in, and a model person's leftover CSV choice
    is dropped."""
    sensor_for = _slot_sensor_names(slot_pairs)
    sensor_by_name = {s.name: s for s in sensors}
    users: list[User] = []
    for person in persons:
        sensor = sensor_by_name.get(sensor_for.get(person.name, ""))
        is_csv = person.data_source == "csv"
        users.append(
            User(
                id=uuid.uuid4().hex,
                name=person.name,
                weight_kg=person.params.get("BW"),
                mode="csv" if is_csv else "model",
                model_id=person.model_id,
                model_params=dict(person.params),
                sensor_id=sensor.sensor_id if sensor else SensorId.IDEAL,
                sensor_params=(
                    dict(sensor.params) if sensor else sensor_defaults.ideal_default_params()
                ),
                food_events=list(person.food_events),
                exercise_events=list(person.exercise_events),
                basal_u_per_h=person.basal_u_per_h,
                csv=_csv_track_of(person) if is_csv else None,
            )
        )
    return users


def slot_user_ids(users: list[User], layout: board_layout.BoardLayout) -> list[str | None]:
    """The user id on each slot of the old layout (matched by person name), None if unused
    or the person no longer exists."""
    by_name = {u.name: u.id for u in users}
    return [by_name.get(slot.person or "") for slot in layout.slots]


def load_or_migrate() -> list[User]:
    """The saved users; on the first run, the migrated old profiles (saved straight away).

    Once the users file exists — even empty — it is the only source: the old
    profile/layout files are never read again."""
    if exists():
        return load()
    persons, sensors = profile_store.load()
    users = migrate(persons, sensors, board_layout.read_legacy_pairs())
    save(users)
    return users
