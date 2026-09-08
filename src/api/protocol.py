"""Binary wire format for the simulator config BLE characteristics.

Must match the firmware's struct sim_config layout (src/sim_config.h) and
config_service.c write handlers exactly: little-endian, float32 params in the
same order as each model's C parameter struct. That order lives in one place —
each ``models/<m>.py`` module's ``PARAM_NAMES`` — which this module references
directly (``_MODEL_PARAM_NAMES`` below); it is pinned against the firmware
structs by ``tests/test_param_order.py``.
"""

from __future__ import annotations

import struct
import zlib
from collections.abc import Iterator
from datetime import datetime

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
        "a0",
        "a1",
        "a2",
        "b0",
        "b1",
        "b2",
        "aw1",
        "aw2",
        "sigma_v",
        "ac1",
        "ac2",
        "sigma_c",
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
    """Legacy on/off Mode characteristic (superseded by encode_speed). 1 byte."""
    return struct.pack("<B", 1 if fast_mode else 0)


SPEED_MIN = 1.0
SPEED_MAX = 1000.0
SPEED_DEFAULT = 1.0


def encode_speed(multiplier: float) -> bytes:
    """float32 LE simulation-speed multiplier, clamped to [SPEED_MIN, SPEED_MAX].

    dt_min per tick = (1/60) * multiplier on both the app engine and the MCU:
    x1 = real time, x60 = the old "fast mode", up to x1000."""
    m = max(SPEED_MIN, min(SPEED_MAX, float(multiplier)))
    return struct.pack("<f", m)


def decode_speed(data: bytes) -> float | None:
    """Inverse of encode_speed — the board's current speed multiplier."""
    if len(data) < 4:
        return None
    return struct.unpack_from("<f", data)[0]


def encode_pisa_instant(duration_min: int, depth_frac: float) -> bytes:
    """u16 duration_min + f32 depth_frac (6 bytes). One-shot PISA attenuation,
    starts "now" on the board; does not reset sim_clock/model state. depth_frac
    is the peak attenuation (0..1) at the midpoint of the bout."""
    return struct.pack("<Hf", int(duration_min), max(0.0, min(1.0, float(depth_frac))))


# ------------------------------------------------------------------
# Comm profile (see ble_uuids.COMM_PROFILE_UUID) + the basic Dexcom-style
# glucose message. Must match firmware src/sim_config.h + src/dexcom_service.c.
# ------------------------------------------------------------------

COMM_SIG_CGMS = 0
COMM_DEXCOM = 1

_DEXCOM_MSG_FMT = "<BBIIHBb"  # opcode, status, u32 seq, u32 ts_s, u16 glucose, state, i8 trend
DEXCOM_MSG_LEN = struct.calcsize(_DEXCOM_MSG_FMT)  # 14


def encode_comm_profile(dexcom: bool) -> bytes:
    """1 byte: 0 = SIG CGMS (0x181F), 1 = basic Dexcom-style stream (FEBC)."""
    return struct.pack("<B", COMM_DEXCOM if dexcom else COMM_SIG_CGMS)


def decode_comm_profile(data: bytes) -> bool | None:
    """Inverse of encode_comm_profile — True if the board is in Dexcom-style mode."""
    if len(data) < 1:
        return None
    return data[0] == COMM_DEXCOM


def decode_dexcom_glucose(data: bytes) -> dict | None:
    """Decode a basic Dexcom-style realtime glucose message (14 bytes, LE).

    Returns ``{glucose_value, sequence, time_offset_min, trend}`` or None. The
    glucose field carries mg/dL in its low 12 bits (top bits are display flags,
    ignored here — the firmware sets them to 0)."""
    if len(data) < DEXCOM_MSG_LEN:
        return None
    opcode, status, seq, ts_s, raw_glucose, _state, trend = struct.unpack_from(
        _DEXCOM_MSG_FMT, data
    )
    return {
        "glucose_value": float(raw_glucose & 0x0FFF),
        "sequence": seq,
        "time_offset_min": ts_s // 60,
        "trend": trend,
        "opcode": opcode,
        "status": status,
    }


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


# ------------------------------------------------------------------
# Data source + CSV playback upload (see ble_uuids.DATA_SOURCE_UUID /
# CSV_CONTROL_UUID / CSV_DATA_UUID and PROTOCOL_SPEC.md's "CSV playback data
# source" section). Must match the firmware's src/csv_store.c + comm_thread.c.
# ------------------------------------------------------------------

DATA_SOURCE_MODEL = 0
DATA_SOURCE_CSV = 1

CSV_TRACK_GLUCOSE = 0
CSV_TRACK_FOODLOG = 1

CSV_OP_BEGIN = 0x01
CSV_OP_COMMIT = 0x02
CSV_OP_ABORT = 0x03
CSV_OP_CLEAR = 0x04
CSV_OP_STATUS = 0x05

CSV_CTRL_STATUS_OK = 0
CSV_CTRL_STATUS_ERR = 1

# csv_begin_wire: u8 op; u8 track; u16 row_count; u32 base_epoch_s; u16 interval_s;
# u32 total_bytes; u32 crc32  (packed, little-endian) — 18 bytes.
_CSV_BEGIN_FMT = "<BBHIHII"


def encode_data_source(is_csv: bool) -> bytes:
    """1 byte: 0 = physiological model, 1 = replay uploaded CSV glucose track."""
    return struct.pack("<B", DATA_SOURCE_CSV if is_csv else DATA_SOURCE_MODEL)


def decode_data_source(data: bytes) -> bool | None:
    """Inverse of encode_data_source — True if the board is set to CSV playback."""
    if len(data) < 1:
        return None
    return data[0] == DATA_SOURCE_CSV


def csv_crc32(blob: bytes) -> int:
    """CRC-32/ISO-HDLC of *blob* — matches the firmware's crc32_ieee_update(0, ...)."""
    return zlib.crc32(blob) & 0xFFFFFFFF


def build_glucose_track(values_mg_dl: list[float]) -> bytes:
    """Pack a glucose track: one little-endian int16 mg/dL per sample.

    Values are rounded and clamped to int16 range; the firmware reads them back
    2 bytes at a time during playback (see csv_glucose_lookup)."""
    clamped = [max(-32768, min(32767, round(v))) for v in values_mg_dl]
    return struct.pack(f"<{len(clamped)}h", *clamped)


def build_foodlog_track(events: list[tuple[int, float]]) -> bytes:
    """Pack a food-log track: {u32 offset_s; f32 carbs_g} per meal, sorted by offset."""
    out = bytearray()
    for offset_s, carbs_g in sorted(events):
        out += struct.pack("<If", int(offset_s), float(carbs_g))
    return bytes(out)


def build_csv_uploads(
    samples: list[int],
    interval_s: int,
    foodlog: list[tuple[int, float]],
    start_iso: str,
) -> list[dict]:
    """The `start_csv_upload` / board-layout `csv.uploads` list for one CSV window.

    One glucose track, plus a food-log track when *foodlog* is non-empty. Both
    the Configuration "Send CSV to Board" path and BoardLayoutWindow build this
    the same way — keep it in one place (issue 19).
    """
    base_epoch = int(datetime.fromisoformat(start_iso).timestamp())
    uploads: list[dict] = [
        {
            "track": CSV_TRACK_GLUCOSE,
            "blob": build_glucose_track([float(s) for s in samples]),
            "row_count": len(samples),
            "base_epoch_s": base_epoch,
            "interval_s": interval_s,
        }
    ]
    if foodlog:
        uploads.append(
            {
                "track": CSV_TRACK_FOODLOG,
                "blob": build_foodlog_track(foodlog),
                "row_count": len(foodlog),
                "base_epoch_s": base_epoch,
                "interval_s": 0,
            }
        )
    return uploads


def encode_csv_begin(
    track: int,
    row_count: int,
    base_epoch_s: int,
    interval_s: int,
    total_bytes: int,
    crc32: int,
) -> bytes:
    """CSV control BEGIN: erase + header. See _CSV_BEGIN_FMT."""
    return struct.pack(
        _CSV_BEGIN_FMT,
        CSV_OP_BEGIN,
        track & 0xFF,
        row_count & 0xFFFF,
        base_epoch_s & 0xFFFFFFFF,
        interval_s & 0xFFFF,
        total_bytes & 0xFFFFFFFF,
        crc32 & 0xFFFFFFFF,
    )


def encode_csv_commit(track: int) -> bytes:
    """CSV control COMMIT: validate received bytes + CRC, activate the track."""
    return struct.pack("<BB", CSV_OP_COMMIT, track & 0xFF)


def encode_csv_abort() -> bytes:
    """CSV control ABORT: discard the in-progress upload."""
    return struct.pack("<B", CSV_OP_ABORT)


def encode_csv_clear(track: int) -> bytes:
    """CSV control CLEAR: wipe a committed track's manifest entry."""
    return struct.pack("<BB", CSV_OP_CLEAR, track & 0xFF)


def encode_csv_status(track: int) -> bytes:
    """CSV control STATUS: ask the board how many bytes it has received."""
    return struct.pack("<BB", CSV_OP_STATUS, track & 0xFF)


def encode_csv_data(offset: int, chunk: bytes) -> bytes:
    """CSV data write: u32 offset (into the track) + raw track bytes."""
    return struct.pack("<I", offset & 0xFFFFFFFF) + chunk


def iter_csv_data_chunks(blob: bytes, chunk_size: int = 224) -> Iterator[bytes]:
    """Yield successive encode_csv_data() writes covering *blob*.

    Default chunk_size leaves headroom under an ATT_MTU of 247 (247 - 3 ATT
    header - 4 offset = 240; 224 is a safe round number)."""
    for offset in range(0, len(blob), chunk_size):
        yield encode_csv_data(offset, blob[offset : offset + chunk_size])


def decode_csv_control_notify(data: bytes) -> tuple[int, int] | None:
    """Decode a CSV control notification: (status, received_bytes).

    status is CSV_CTRL_STATUS_OK / CSV_CTRL_STATUS_ERR; byte 1 is reserved."""
    if len(data) < 6:
        return None
    status = data[0]
    received = struct.unpack_from("<I", data, 2)[0]
    return status, received


def decode_food_exercise_status(data: bytes) -> dict[str, float] | None:
    """Decode the board's Food/Exercise Status notification.

    Per-slot wire format (10 bytes): ``u8 slot; u8 _pad; f32 carbs_g_per_min;
    f32 exercise_pct`` — one notification per active sensor slot per tick. The
    legacy single-sensor format (8 bytes: ``f32 carbs; f32 ex``) is still
    accepted and reported as ``slot`` 0.

    This is what the board is *actually* feeding its on-device model right
    now (after evaluating its own copy of the food/exercise schedule), so the
    app can plot ground truth from the MCU rather than just its own guess.
    """
    if len(data) >= 10:
        slot, _pad, carbs_g_per_min, exercise_pct = struct.unpack_from("<BBff", data)
        return {
            "slot": slot,
            "carbs_g_per_min": carbs_g_per_min,
            "exercise_pct": exercise_pct,
        }
    if len(data) >= 8:
        carbs_g_per_min, exercise_pct = struct.unpack_from("<ff", data)
        return {
            "slot": 0,
            "carbs_g_per_min": carbs_g_per_min,
            "exercise_pct": exercise_pct,
        }
    return None


def encode_sensor_select(slot: int) -> bytes:
    """1 byte: the sensor slot index [0, sensor_count) that subsequent per-sensor
    config writes/reads target on the board. Not persisted (a session cursor)."""
    return struct.pack("<B", slot & 0xFF)


def decode_sensor_select(data: bytes) -> int | None:
    """Inverse of encode_sensor_select — the slot the board currently has selected."""
    if len(data) < 1:
        return None
    return data[0]


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
        events.append(
            FoodEvent(time_of_day_min=time_min, carbs_g=carbs_g, duration_min=duration_min)
        )
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
            ExerciseEvent(
                time_of_day_min=time_min, duration_min=duration_min, intensity_pct=intensity_pct
            )
        )
        offset += 8
    return events
