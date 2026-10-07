"""What one board slot says, as a user — and what to do with it.

Reading a slot gives a :class:`BoardReading` (the raw facts: name, source, models,
schedules). :func:`classify` looks the name up among the saved users and says which of
three things happened:

* :class:`Unknown` — no saved user has that name (or the board has none): a new, unsaved
  user built from the board.
* :class:`Matches` — the saved user of that name holds exactly what the board runs.
* :class:`Differs` — same name, different content: the caller asks whether to overwrite the
  saved user with the board's content (:func:`overwrite`) or keep both
  (:func:`create_copy`, which numbers the copy ``Name#2``).

Pure — no Qt, no board — so the three outcomes are tested without either.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace

from models import user_match
from models.types import CsvTrack, ExerciseEvent, FoodEvent, ModelId, SensorId, User

UNKNOWN_NAME = "Unknown"  # what a board user with no name is called until the user renames it


@dataclass(frozen=True)
class BoardReading:
    """The facts read from one slot, already decoded."""

    name: str  # empty when the board holds no name
    is_csv: bool
    model_id: ModelId
    model_params: dict[str, float]
    sensor_id: SensorId
    sensor_params: dict[str, float]
    food_events: list[FoodEvent]
    exercise_events: list[ExerciseEvent]
    # The recording the board replays, read back from it; None when the board is not replaying one
    # or could not send it (an older firmware).
    csv: CsvTrack | None = None


@dataclass(frozen=True)
class Unknown:
    """No saved user has this name: the board's user, unsaved."""

    board: User


@dataclass(frozen=True)
class Matches:
    """The saved user of this name holds what the board runs."""

    saved: User


@dataclass(frozen=True)
class Differs:
    """The saved user of this name differs from the board in *differences*."""

    saved: User
    board: User
    differences: list[str]


Outcome = Unknown | Matches | Differs


def user_from_reading(reading: BoardReading) -> User:
    """A new user (fresh id) holding what *reading* says. Height and picture are unknown to
    the board; a CSV board's recording comes with the reading (None if it held none)."""
    return User(
        id=uuid.uuid4().hex,
        name=reading.name or UNKNOWN_NAME,
        weight_kg=reading.model_params.get("BW"),
        mode="csv" if reading.is_csv else "model",
        model_id=reading.model_id,
        model_params=dict(reading.model_params),
        sensor_id=reading.sensor_id,
        sensor_params=dict(reading.sensor_params),
        food_events=list(reading.food_events),
        exercise_events=list(reading.exercise_events),
        csv=reading.csv,
    )


def classify(saved_users: list[User], reading: BoardReading) -> Outcome:
    """Look *reading*'s name up (exactly, by name only) among *saved_users*."""
    board = user_from_reading(reading)
    saved = next((u for u in saved_users if reading.name and u.name == reading.name), None)
    if saved is None:
        return Unknown(board)
    differences = user_match.compare(saved, board)
    return Differs(saved, board, differences) if differences else Matches(saved)


def overwrite(outcome: Differs) -> User:
    """The saved user replaced by what the board runs (its id, picture and height stay)."""
    return user_match.apply_board(outcome.saved, outcome.board)


def create_copy(saved_users: list[User], outcome: Differs) -> User:
    """The board's user as a new user named with the next free ``Name#N``."""
    name = user_match.next_free_name(outcome.saved.name, {u.name for u in saved_users})
    return replace(outcome.board, name=name)
