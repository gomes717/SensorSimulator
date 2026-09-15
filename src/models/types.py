"""Shared data types for person/sensor profiles and food/exercise events.

``ModelId``/``SensorId`` values are the wire-format identifiers sent to the
board (see src/api/protocol.py) — they must match the dispatch tables in the
firmware's src/config_service.c exactly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum


class ModelId(IntEnum):
    """Which physiological model a PersonProfile runs."""

    CAMBRIDGE = 0
    UVA_PADOVA = 1
    ROYPARKER = 2
    DEICHMANN = 3


# One spelling of each model's name, shared by every window that shows one.
MODEL_LABELS = {
    ModelId.CAMBRIDGE: "Cambridge (Hovorka)",
    ModelId.UVA_PADOVA: "UVA/Padova T1DMS",
    ModelId.ROYPARKER: "Roy & Parker (exercise)",
    ModelId.DEICHMANN: "Deichmann (HR-driven exercise)",
}


class SensorId(IntEnum):
    """Which CGM sensor noise model a SensorProfile applies (on-device only)."""

    IDEAL = 0
    BRETON = 1
    FACCHINETTI = 2


@dataclass
class FoodEvent:
    """A meal that recurs every simulated day at the same time of day.

    For rate-fed models (Cambridge, UVA/Padova) *carbs_g* is spread evenly
    over *duration_min*. For impulse-fed models (Roy/Parker, Deichmann) the
    full amount is delivered once, at *time_of_day_min*, and the model's own
    absorption compartments do the spreading — *duration_min* is unused there.
    """

    time_of_day_min: int  # 0-1439, minutes since simulated midnight
    carbs_g: float
    duration_min: int = 15


@dataclass
class ExerciseEvent:
    """An exercise bout that recurs every simulated day at the same time of day."""

    time_of_day_min: int  # 0-1439
    duration_min: int
    intensity_pct: float = (
        50.0  # 0-100; drives Roy/Parker directly, maps to heart rate for Deichmann
    )


@dataclass
class PersonProfile:
    """A saved simulated patient: which model to run, its parameters, and its schedule."""

    name: str
    model_id: ModelId
    params: dict[str, float] = field(default_factory=dict)
    food_events: list[FoodEvent] = field(default_factory=list)
    exercise_events: list[ExerciseEvent] = field(default_factory=list)
    # None -> use the model's own steady-state basal (see engine.py's basal lookup)
    basal_u_per_h: float | None = None
    # Where this patient's glucose comes from: "model" (run the physiological
    # model, current behavior) or "csv" (replay a recorded 24 h region from a
    # Dexcom CSV). CSV playback is not wired yet — see docs/TODO.md — but the
    # choice and its source region are persisted here.
    data_source: str = "model"
    csv_path: str | None = None
    csv_window_start_iso: str | None = None
    # Optional matching Food Log CSV (D1NAMO-style) for the same 24 h window —
    # replayed report-only alongside the glucose trace when data_source == "csv".
    food_log_path: str | None = None


@dataclass
class SensorProfile:
    """A saved CGM sensor noise profile — only meaningful on-device (see engine.py note)."""

    name: str
    sensor_id: SensorId
    params: dict[str, float] = field(default_factory=dict)
