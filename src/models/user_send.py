"""The ordered writes that put a user on a board slot — the pure half of **Send to…**.

An entry is what :meth:`services.ble_session.BleSession.send_board_layout` takes for one slot:
``{"slot": n, "writes": [(characteristic key, payload), …], "csv": {"uploads": […]} | None}``.
The board's Sensor-select cursor is set to the slot first by the session; the writes follow.

* **A model user** is written its name, then its model and sensor noise, then switched to the model
  source and given its meals and exercise (each list **cleared first**: the board keeps what an
  earlier session left, and a stale meal on the board is exactly how it and the app's expected line
  drift apart). The model writes are shared with Start's push (:func:`model_writes`).
* **A CSV user** is written its name, then its recorded window is uploaded, and only then is the
  slot switched to the CSV source (so it never plays its old model with the CSV source already
  on). The board's own model config is left alone — the recording drives it.

The name goes first and is a plain write: it is metadata and does not reset the board's simulation.
"""

from __future__ import annotations

from datetime import datetime

from api import protocol
from models import user_sim
from models.types import PersonProfile, SensorProfile, User

_NO_START = "2020-01-01T00:00:00"  # a window with no recorded start still needs an epoch to upload


def model_writes(person: PersonProfile, sensor: SensorProfile | None) -> list[tuple[str, bytes]]:
    """Ordered writes that make a slot run *person* (and *sensor*'s noise): the model, the noise,
    the model source, then each schedule cleared and rewritten."""
    writes: list[tuple[str, bytes]] = [
        ("person", protocol.encode_person_config(person.model_id, person.params))
    ]
    if sensor is not None:
        writes.append(("sensor", protocol.encode_sensor_config(sensor.sensor_id, sensor.params)))
    writes += [
        ("data_source", protocol.encode_data_source(False)),
        ("food", protocol.encode_clear_food()),
        *(("food", protocol.encode_food_event(ev)) for ev in person.food_events),
        ("exercise", protocol.encode_clear_exercise()),
        *(("exercise", protocol.encode_exercise_event(ev)) for ev in person.exercise_events),
    ]
    return writes


def name_write(name: str) -> tuple[str, bytes] | None:
    """The write that tells the board *name*, or None when there is nothing it can hold (empty, or
    longer than its 30 bytes — a name that arrived from an older profile)."""
    name = name.strip()
    if not name or len(name.encode("utf-8")) > protocol.MAX_USER_NAME_BYTES:
        return None
    return ("user_name", protocol.encode_user_name(name))


def refusal(user: User) -> str | None:
    """Why *user* cannot be sent, or None."""
    name = user.name.strip()
    if not name:
        return "Give the user a name before sending it."
    if len(name.encode("utf-8")) > protocol.MAX_USER_NAME_BYTES:
        return f"The name is longer than the board's {protocol.MAX_USER_NAME_BYTES} bytes."
    if user.mode == "csv" and (user.csv is None or not user.csv.samples):
        return "This user replays a CSV recording but has no CSV window — choose one first."
    return None


def slot_entry(slot: int, user: User) -> dict:
    """The ``send_board_layout`` entry that makes *slot* run *user*. Raises ValueError, with the
    reason, for a user :func:`refusal` rejects."""
    problem = refusal(user)
    if problem is not None:
        raise ValueError(problem)
    name = ("user_name", protocol.encode_user_name(user.name.strip()))
    if user.mode == "csv":
        assert user.csv is not None  # refusal() checked
        start = user.csv.start_iso or _NO_START
        uploads = protocol.build_csv_uploads(
            user.csv.samples,
            user.csv.interval_s,
            user.csv.foodlog,
            datetime.fromisoformat(start).isoformat(),
        )
        return {
            "slot": slot,
            "writes": [name],
            "csv": {"uploads": uploads},
            # The CSV source is switched on only once the recording is complete on the board.
            "after_csv": [("data_source", protocol.encode_data_source(True))],
        }
    writes = model_writes(user_sim.person_profile_of(user), user_sim.sensor_profile_of(user))
    return {"slot": slot, "writes": [name, *writes], "csv": None}
