"""Running a User through the local model: the profile the engine takes, and the 24 h preview.

The preview uses the same :class:`~models.engine.ModelStepper` as the "expected" line,
so what it draws is by construction what a run would expect. The stepper applies no
sensor noise (the noise model is on-device only), which is exactly what the preview
wants.
"""

from __future__ import annotations

import contextlib
import io

from models.engine import ModelStepper
from models.types import PersonProfile, SensorProfile, User

_MINUTES_PER_DAY = 1440
_PREVIEW_STAMP = "1970-01-01T00:00:00+00:00"  # the stepper only stamps it onto a reading


def person_profile_of(user: User) -> PersonProfile:
    """The engine's profile for *user*: its model, parameters (with the user's weight as
    ``BW``), schedules and basal rate — and, in CSV mode, its recorded window (the engine
    replays that, or runs no model when the user has none). A copy; *user* is untouched."""
    params = dict(user.model_params)
    if user.weight_kg is not None:
        params["BW"] = user.weight_kg
    return PersonProfile(
        name=user.name,
        model_id=user.model_id,
        params=params,
        food_events=list(user.food_events),
        exercise_events=list(user.exercise_events),
        basal_u_per_h=user.basal_u_per_h,
        data_source=user.mode,
        csv_track=user.csv if user.mode == "csv" else None,
    )


def sensor_profile_of(user: User) -> SensorProfile:
    """The sensor-noise profile pushed to the board for *user* (a copy)."""
    return SensorProfile(name=user.name, sensor_id=user.sensor_id, params=dict(user.sensor_params))


def preview_24h(user: User) -> tuple[list[float], list[float]]:
    """``(minutes, mg/dL)`` for one simulated day of *user*.

    Model mode steps the model one minute at a time from midnight, so the glucose after
    minute *i* is plotted at *i* (1..1440). CSV mode is the recorded window on its own
    grid (0, interval, …); a CSV user with no data gives two empty lists.
    """
    if user.mode == "csv":
        if user.csv is None:
            return [], []
        step_min = user.csv.interval_s / 60.0
        times = [i * step_min for i in range(len(user.csv.samples))]
        return times, [float(v) for v in user.csv.samples]

    stepper = ModelStepper(person_profile_of(user))
    minutes: list[float] = []
    glucose: list[float] = []
    # The stepper logs every tick to stdout; 1440 lines per preview would swamp the app log.
    with contextlib.redirect_stdout(io.StringIO()):
        for minute in range(1, _MINUTES_PER_DAY + 1):
            glucose.append(stepper.tick(1.0, _PREVIEW_STAMP).glucose)
            minutes.append(float(minute))
    return minutes, glucose
