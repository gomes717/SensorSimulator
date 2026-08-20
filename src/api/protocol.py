"""Binary wire format for the simulator config BLE characteristics.

Must match the firmware's struct sim_config layout (src/sim_config.h) and
config_service.c write handlers exactly: little-endian, float32 params in the
same order as each model's C parameter struct (see each models/*.py module's
PARAM_NAMES, copied verbatim from the corresponding cgmsim/inc/*.h).
"""
from __future__ import annotations

import struct

from models import cambridge, deichmann, royparker, uva_padova
from models.types import ExerciseEvent, FoodEvent, ModelId, SensorId

# Must match MAX_MODEL_PARAMS / MAX_SENSOR_PARAMS in the firmware's sim_config.h.
MAX_MODEL_PARAMS = 34  # >= UVA/Padova's 33 params, the largest model
MAX_SENSOR_PARAMS = 14  # >= Facchinetti's 13 params, the largest sensor

_MODEL_PARAM_NAMES: dict[ModelId, list[str]] = {
    ModelId.CAMBRIDGE: cambridge.PARAM_NAMES,
    ModelId.UVA_PADOVA: uva_padova.PARAM_NAMES,
    ModelId.ROYPARKER: royparker.PARAM_NAMES,
    ModelId.DEICHMANN: deichmann.PARAM_NAMES,
}

# Sensor param field order — the configurable subset of each *State struct in
# cgmsim/inc/cgmsim_sensors.h (excludes internal-only fields like noise
# history / rng_state, which the firmware seeds itself). No sensor has a
# sampling_time_min field: every sensor emits a fresh reading on every
# simulation tick — how often the client actually sees a new value is a
# transport-cadence question (comm_thread's own poll/notify interval),
# not something the sensor model throttles.
_SENSOR_PARAM_NAMES: dict[SensorId, list[str]] = {
    SensorId.IDEAL: [],
    SensorId.BRETON: ["pacf", "sigma", "alpha", "beta"],
    SensorId.FACCHINETTI: [
        "a0", "a1", "a2", "b0", "b1", "b2",
        "aw1", "aw2", "sigma_v", "ac1", "ac2", "sigma_c",
    ],
}

_FOOD_CLEAR_SENTINEL = 0xFFFF
_EXERCISE_CLEAR_SENTINEL = 0xFFFF

# Run state values — see ble_uuids.RUN_STATE_UUID. Not part of the persisted
# sim_config; writing STOPPED always resets the board's model to its initial
# state (even if it was already stopped), whether or not it was previously
# running independently of the app.
RUN_STATE_STOPPED = 0
RUN_STATE_RUNNING = 1
RUN_STATE_PAUSED = 2


def model_param_names(model_id: ModelId) -> list[str]:
    return _MODEL_PARAM_NAMES[model_id]


def sensor_param_names(sensor_id: SensorId) -> list[str]:
    return _SENSOR_PARAM_NAMES[sensor_id]


def encode_person_config(model_id: ModelId, params: dict[str, float]) -> bytes:
    """1 byte model_id + MAX_MODEL_PARAMS little-endian float32, in PARAM_NAMES order."""
    names = _MODEL_PARAM_NAMES[model_id]
    if len(names) > MAX_MODEL_PARAMS:
        raise ValueError(f"model {model_id} has {len(names)} params > MAX_MODEL_PARAMS")
    values = [float(params.get(n, 0.0)) for n in names]
    values += [0.0] * (MAX_MODEL_PARAMS - len(values))
    return struct.pack("<B" + "f" * MAX_MODEL_PARAMS, int(model_id), *values)


def encode_sensor_config(sensor_id: SensorId, params: dict[str, float]) -> bytes:
    """1 byte sensor_id + MAX_SENSOR_PARAMS little-endian float32, in order."""
    names = _SENSOR_PARAM_NAMES[sensor_id]
    if len(names) > MAX_SENSOR_PARAMS:
        raise ValueError(f"sensor {sensor_id} has {len(names)} params > MAX_SENSOR_PARAMS")
    values = [float(params.get(n, 0.0)) for n in names]
    values += [0.0] * (MAX_SENSOR_PARAMS - len(values))
    return struct.pack("<B" + "f" * MAX_SENSOR_PARAMS, int(sensor_id), *values)


def encode_mode(fast_mode: bool) -> bytes:
    """1 byte: 0 = normal (1 sim-second per wall-second), 1 = fast (1 sim-minute per wall-second)."""
    return struct.pack("<B", 1 if fast_mode else 0)


def encode_food_event(event: FoodEvent) -> bytes:
    """u16 time_of_day_min + u16 duration_min + f32 carbs_g (8 bytes)."""
    return struct.pack("<HHf", event.time_of_day_min, event.duration_min, event.carbs_g)


def encode_clear_food() -> bytes:
    """Sentinel write (time_of_day_min == 0xFFFF) telling the board to drop all food events."""
    return struct.pack("<HHf", _FOOD_CLEAR_SENTINEL, 0, 0.0)


def encode_exercise_event(event: ExerciseEvent) -> bytes:
    """u16 time_of_day_min + u16 duration_min + f32 intensity_pct (8 bytes)."""
    return struct.pack("<HHf", event.time_of_day_min, event.duration_min, event.intensity_pct)


def encode_clear_exercise() -> bytes:
    """Sentinel write (time_of_day_min == 0xFFFF) telling the board to drop all exercise events."""
    return struct.pack("<HHf", _EXERCISE_CLEAR_SENTINEL, 0, 0.0)


def encode_food_instant(duration_min: int, carbs_g: float) -> bytes:
    """u16 duration_min + f32 carbs_g (6 bytes). One-shot, starts "now" on the board —
    no time_of_day_min, unlike encode_food_event; does not reset sim_clock/model state."""
    return struct.pack("<Hf", duration_min, carbs_g)


def encode_exercise_instant(duration_min: int, intensity_pct: float) -> bytes:
    """u16 duration_min + f32 intensity_pct (6 bytes). One-shot, same semantics as
    encode_food_instant."""
    return struct.pack("<Hf", duration_min, intensity_pct)


def encode_cgms_only(enabled: bool) -> bytes:
    """1 byte: 0 = normal (config exchange enabled), 1 = CGMS-only (standard CGM
    stream only, no config exchange)."""
    return struct.pack("<B", 1 if enabled else 0)


def decode_cgms_only(data: bytes) -> bool | None:
    """Inverse of encode_cgms_only."""
    if len(data) < 1:
        return None
    return data[0] != 0


def decode_food_exercise_status(data: bytes) -> dict[str, float] | None:
    """Decode the board's Food/Exercise Status notification: f32 carbs_g_per_min + f32 exercise_pct.

    This is what the board is *actually* feeding its on-device model right
    now (after evaluating its own copy of the food/exercise schedule), so the
    app can plot ground truth from the MCU rather than just its own guess.
    """
    if len(data) < 8:
        return None
    carbs_g_per_min, exercise_pct = struct.unpack_from("<ff", data)
    return {"carbs_g_per_min": carbs_g_per_min, "exercise_pct": exercise_pct}


# ------------------------------------------------------------------
# Readback (MCU -> app): inverse of the encode_* functions above, used to
# recover whatever config is currently applied/stored on the board.
# ------------------------------------------------------------------


def decode_person_config(data: bytes) -> tuple[ModelId, dict[str, float]] | None:
    """Inverse of encode_person_config — decode a Person Config characteristic read."""
    expected_len = 1 + MAX_MODEL_PARAMS * 4
    if len(data) < expected_len:
        return None
    model_id = ModelId(data[0])
    values = struct.unpack_from("<" + "f" * MAX_MODEL_PARAMS, data, 1)
    names = _MODEL_PARAM_NAMES[model_id]
    return model_id, {name: values[i] for i, name in enumerate(names)}


def decode_sensor_config(data: bytes) -> tuple[SensorId, dict[str, float]] | None:
    """Inverse of encode_sensor_config — decode a Sensor Config characteristic read."""
    expected_len = 1 + MAX_SENSOR_PARAMS * 4
    if len(data) < expected_len:
        return None
    sensor_id = SensorId(data[0])
    values = struct.unpack_from("<" + "f" * MAX_SENSOR_PARAMS, data, 1)
    names = _SENSOR_PARAM_NAMES[sensor_id]
    return sensor_id, {name: values[i] for i, name in enumerate(names)}


def decode_mode(data: bytes) -> bool | None:
    """Inverse of encode_mode — True if the board is currently in fast mode."""
    if len(data) < 1:
        return None
    return data[0] != 0


def encode_run_state(state: int) -> bytes:
    """1 byte: one of RUN_STATE_STOPPED/RUNNING/PAUSED."""
    return struct.pack("<B", state)


def decode_run_state(data: bytes) -> int | None:
    """Inverse of encode_run_state."""
    if len(data) < 1:
        return None
    return data[0]


def decode_food_events(data: bytes) -> list[FoodEvent]:
    """Decode the Food Events Readback characteristic: u8 count + up to 32 8-byte entries."""
    if len(data) < 1:
        return []
    count = min(data[0], 32)
    events = []
    offset = 1
    for _ in range(count):
        if offset + 8 > len(data):
            break
        time_min, duration_min, carbs_g = struct.unpack_from("<HHf", data, offset)
        events.append(FoodEvent(time_of_day_min=time_min, carbs_g=carbs_g, duration_min=duration_min))
        offset += 8
    return events


def decode_exercise_events(data: bytes) -> list[ExerciseEvent]:
    """Decode the Exercise Events Readback characteristic: u8 count + up to 32 8-byte entries."""
    if len(data) < 1:
        return []
    count = min(data[0], 32)
    events = []
    offset = 1
    for _ in range(count):
        if offset + 8 > len(data):
            break
        time_min, duration_min, intensity_pct = struct.unpack_from("<HHf", data, offset)
        events.append(
            ExerciseEvent(time_of_day_min=time_min, duration_min=duration_min, intensity_pct=intensity_pct)
        )
        offset += 8
    return events
