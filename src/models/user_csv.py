"""Turning a chosen Dexcom file and a start time into the 24 h window a user keeps.

The window is **copied** into the user (see :class:`~models.types.CsvTrack`): glucose resampled onto
the Dexcom 5-minute grid, plus the meals of the matching Food Log (the file that shares the
glucose file's number, found without asking). After this the original file is not needed again.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from api import protocol
from models import dexcom_csv, food_log_csv
from models.types import CsvTrack


def load_track(path: str | Path, start: datetime) -> CsvTrack:
    """The 24 h window of *path* beginning at *start*. Raises ValueError, with a message fit to
    show, if the file cannot be read, is not a Dexcom export, or has nothing in that window.

    A missing or unreadable Food Log is not an error: the meals are report-only, so the glucose
    window is kept without them."""
    path = Path(path)
    try:
        rows = dexcom_csv.read_egv(path)
    except OSError as exc:
        raise ValueError(f"The file could not be read: {exc.strerror or exc}") from exc
    if not dexcom_csv.slice_window(rows, start):
        raise ValueError("The recording has no readings in that 24 h window.")
    samples = dexcom_csv.resample(rows, start, dexcom_csv.DEFAULT_INTERVAL_S)
    return CsvTrack(
        samples=samples,
        interval_s=dexcom_csv.DEFAULT_INTERVAL_S,
        foodlog=_meals(path, start),
        start_iso=start.isoformat(),
        source_name=path.name,
    )


def _meals(glucose_path: Path, start: datetime) -> list[tuple[int, float]]:
    food_path = food_log_csv.matching_food_log_path(glucose_path)
    if food_path is None:
        return []
    try:
        return food_log_csv.slice_window(food_log_csv.read_food_log(food_path), start)
    except (ValueError, OSError):
        return []


def track_from_download(tracks: dict[int, dict]) -> CsvTrack | None:
    """The window a board sent back, as a user's :class:`CsvTrack`; None if the board holds no
    glucose recording for the slot, or sent fewer rows than it claims (never a guess).

    *tracks* maps a track number to ``{"present", "interval_s", "row_count", "base_epoch_s",
    "blob"}`` (what ``BleSession.start_csv_download`` collects). The start time is the board's
    base epoch; the board holds no file name, so the window says where it came from."""
    glucose = tracks.get(protocol.CSV_TRACK_GLUCOSE)
    if glucose is None or not glucose["present"]:
        return None
    samples = protocol.parse_glucose_blob(glucose["blob"])
    if len(samples) != glucose["row_count"] or not samples:
        return None
    meals = tracks.get(protocol.CSV_TRACK_FOODLOG)
    foodlog = protocol.parse_foodlog_blob(meals["blob"]) if meals and meals["present"] else []
    return CsvTrack(
        samples=samples,
        interval_s=glucose["interval_s"],
        foodlog=foodlog,
        start_iso=datetime.fromtimestamp(glucose["base_epoch_s"]).isoformat(),
        source_name="read from the board",
    )
