"""JSON persistence for saved Person and Sensor profiles (data/profiles.json)."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from models.types import ExerciseEvent, FoodEvent, ModelId, PersonProfile, SensorId, SensorProfile

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_PROFILES_FILE = _DATA_DIR / "profiles.json"


def _person_to_dict(person: PersonProfile) -> dict:
    d = asdict(person)
    d["model_id"] = int(person.model_id)
    return d


def _person_from_dict(d: dict) -> PersonProfile:
    return PersonProfile(
        name=d["name"],
        model_id=ModelId(d["model_id"]),
        params=dict(d.get("params", {})),
        food_events=[FoodEvent(**ev) for ev in d.get("food_events", [])],
        exercise_events=[ExerciseEvent(**ev) for ev in d.get("exercise_events", [])],
        basal_u_per_h=d.get("basal_u_per_h"),
    )


def _sensor_to_dict(sensor: SensorProfile) -> dict:
    d = asdict(sensor)
    d["sensor_id"] = int(sensor.sensor_id)
    return d


def _sensor_from_dict(d: dict) -> SensorProfile:
    return SensorProfile(
        name=d["name"], sensor_id=SensorId(d["sensor_id"]), params=dict(d.get("params", {}))
    )


def load() -> tuple[list[PersonProfile], list[SensorProfile]]:
    """Load saved profiles, or return two empty lists if nothing has been saved yet."""
    if not _PROFILES_FILE.exists():
        return [], []
    data = json.loads(_PROFILES_FILE.read_text(encoding="utf-8"))
    persons = [_person_from_dict(d) for d in data.get("persons", [])]
    sensors = [_sensor_from_dict(d) for d in data.get("sensors", [])]
    return persons, sensors


def save(persons: list[PersonProfile], sensors: list[SensorProfile]) -> None:
    """Persist *persons* and *sensors* to disk, creating the data directory if needed."""
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "persons": [_person_to_dict(p) for p in persons],
        "sensors": [_sensor_to_dict(s) for s in sensors],
    }
    _PROFILES_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
