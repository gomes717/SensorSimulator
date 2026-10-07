"""Test helper: build a User from the engine-level profiles many tests were written with."""

from models.engine import load_csv_window
from models.types import CsvTrack, PersonProfile, SensorId, SensorProfile, User


def user_of(person: PersonProfile, sensor: SensorProfile | None = None) -> User:
    """A User running what *person* (and *sensor*) describe; a CSV person's window is copied in."""
    track = None
    if person.data_source == "csv":
        samples, interval_s, foodlog = load_csv_window(person)
        if samples:
            track = CsvTrack(samples=samples, interval_s=interval_s, foodlog=foodlog)
    return User(
        id=f"id-{person.name}",
        name=person.name,
        weight_kg=person.params.get("BW"),
        mode=person.data_source,
        model_id=person.model_id,
        model_params=dict(person.params),
        sensor_id=sensor.sensor_id if sensor else SensorId.IDEAL,
        sensor_params=dict(sensor.params) if sensor else {},
        food_events=list(person.food_events),
        exercise_events=list(person.exercise_events),
        basal_u_per_h=person.basal_u_per_h,
        csv=track,
    )
