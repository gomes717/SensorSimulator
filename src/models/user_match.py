"""Comparing a user read from the board with a saved one, and naming a new copy.

What the board returns has been through float32 (the wire format), so a saved
user's 82.3 kg never equals the board's 82.30000305…; every float is rounded to
float32 on both sides before it is compared. Only what the board can hold is
compared — not the picture, the height or the id. A CSV user's recorded window is
compared too (the board sends it back); a board that could not send one is not.
"""

from __future__ import annotations

import re
import struct
from dataclasses import replace

from api import protocol
from models.types import CsvTrack, ExerciseEvent, FoodEvent, User

MAX_NAME_BYTES = protocol.MAX_USER_NAME_BYTES  # what the board's name field holds
_NUMBERED = re.compile(r"#\d+$")


def _f32(value: float) -> float:
    return struct.unpack("<f", struct.pack("<f", value))[0]


def _params(names: list[str], params: dict[str, float]) -> list[float]:
    return [_f32(params.get(name, 0.0)) for name in names]


def _food(events: list[FoodEvent]) -> list[tuple[int, int, float]]:
    return sorted((e.time_of_day_min, e.duration_min, _f32(e.carbs_g)) for e in events)


def _exercise(events: list[ExerciseEvent]) -> list[tuple[int, int, float]]:
    return sorted((e.time_of_day_min, e.duration_min, _f32(e.intensity_pct)) for e in events)


def _foodlog(track: CsvTrack) -> list[tuple[int, float]]:
    return sorted((offset, _f32(carbs)) for offset, carbs in track.foodlog)


def _compare_windows(saved: User, board: User) -> list[str]:
    """The recorded windows of two CSV users. A board that could not send its recording (an older
    firmware) is not compared; one that did must hold exactly the saved window — the samples, the
    interval and the meals (carbs through float32). The file name and start time are not compared:
    the board holds neither."""
    if board.csv is None:
        return []
    if saved.csv is None:
        return ["CSV window"]
    same = (
        saved.csv.samples == board.csv.samples
        and saved.csv.interval_s == board.csv.interval_s
        and _foodlog(saved.csv) == _foodlog(board.csv)
    )
    return [] if same else ["CSV window"]


def compare(saved: User, board: User) -> list[str]:
    """What differs between *saved* and what the *board* runs; empty when they match.

    A different mode is the only difference reported (the other mode's inputs are
    kept but unused, so comparing them would only add noise). In CSV mode the recorded
    windows are compared; in model mode: the model and its parameters, the sensor and its
    parameters, and the two schedules (order does not matter).
    """
    if saved.mode != board.mode:
        return ["mode"]
    if saved.mode == "csv":
        return _compare_windows(saved, board)
    differences: list[str] = []
    if saved.model_id != board.model_id:
        differences.append("model")
    else:
        names = protocol.model_param_names(saved.model_id)
        if _params(names, saved.model_params) != _params(names, board.model_params):
            differences.append("model parameters")
    if saved.sensor_id != board.sensor_id:
        differences.append("sensor")
    else:
        names = protocol.sensor_param_names(saved.sensor_id)
        if _params(names, saved.sensor_params) != _params(names, board.sensor_params):
            differences.append("sensor parameters")
    if _food(saved.food_events) != _food(board.food_events):
        differences.append("food schedule")
    if _exercise(saved.exercise_events) != _exercise(board.exercise_events):
        differences.append("exercise schedule")
    return differences


def apply_board(saved: User, board: User) -> User:
    """*saved* overwritten with what the board runs (a new object; *saved* is untouched).

    The id, name, picture, height and basal rate stay — the board holds none of them —
    and so does the saved CSV track when the board sent none."""
    return replace(
        saved,
        mode=board.mode,
        weight_kg=board.model_params.get("BW", saved.weight_kg),
        model_id=board.model_id,
        model_params=dict(board.model_params),
        sensor_id=board.sensor_id,
        sensor_params=dict(board.sensor_params),
        food_events=list(board.food_events),
        exercise_events=list(board.exercise_events),
        csv=board.csv if board.csv is not None else saved.csv,
    )


def clip_name(name: str, limit: int = MAX_NAME_BYTES) -> str:
    """*name* cut to at most *limit* UTF-8 bytes, never in the middle of a character."""
    return name.encode("utf-8")[:limit].decode("utf-8", errors="ignore")


def next_free_name(base: str, existing: set[str]) -> str:
    """The first of ``<base>#2``, ``<base>#3``, … not in *existing*. A name that already
    ends in ``#N`` is renumbered rather than stacked (``Ana#2`` -> ``Ana#3``), and the
    base is shortened so the result still fits the board's name field."""
    root = _NUMBERED.sub("", base)
    number = 2
    while True:
        suffix = f"#{number}"
        candidate = clip_name(root, MAX_NAME_BYTES - len(suffix)) + suffix
        if candidate not in existing:
            return candidate
        number += 1
