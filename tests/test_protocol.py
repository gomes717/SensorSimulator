"""Issue 10: round-trip coverage for the BLE wire codec (api.protocol).

The wire order and struct sizes are the contract with the firmware — see also
tests/test_param_order.py for the model/sensor field order.
"""

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from api import protocol
from models import cambridge
from models.types import ExerciseEvent, FoodEvent, ModelId, SensorId


@pytest.mark.parametrize(
    ("enc", "dec", "value"),
    [
        (protocol.encode_speed, protocol.decode_speed, 1.0),
        (protocol.encode_speed, protocol.decode_speed, 123.5),
        (protocol.encode_comm_profile, protocol.decode_comm_profile, True),
        (protocol.encode_comm_profile, protocol.decode_comm_profile, False),
        (protocol.encode_data_source, protocol.decode_data_source, True),
        (protocol.encode_data_source, protocol.decode_data_source, False),
        (protocol.encode_cgms_only, protocol.decode_cgms_only, True),
        (protocol.encode_cgms_only, protocol.decode_cgms_only, False),
        (protocol.encode_mode, protocol.decode_mode, True),
        (protocol.encode_mode, protocol.decode_mode, False),
        (protocol.encode_run_state, protocol.decode_run_state, protocol.RUN_STATE_RUNNING),
        (protocol.encode_run_state, protocol.decode_run_state, protocol.RUN_STATE_STOPPED),
        (protocol.encode_sensor_select, protocol.decode_sensor_select, 0),
        (protocol.encode_sensor_select, protocol.decode_sensor_select, 3),
    ],
)
def test_scalar_roundtrips(enc, dec, value):
    out = dec(enc(value))
    if isinstance(value, float):
        assert out == pytest.approx(value, rel=1e-4)
    else:
        assert out == value


def test_speed_is_clamped_on_encode():
    assert protocol.decode_speed(protocol.encode_speed(9999.0)) == pytest.approx(1000.0)
    assert protocol.decode_speed(protocol.encode_speed(0.0)) == pytest.approx(1.0)


def test_person_config_roundtrip():
    params = dict.fromkeys(cambridge.PARAM_NAMES, 0.0)
    params.update({"BW": 82.0, "Gpeq": 105.0, "SIT": 0.001})
    blob = protocol.encode_person_config(ModelId.CAMBRIDGE, params)
    decoded = protocol.decode_person_config(blob)
    assert decoded is not None
    model_id, back = decoded
    assert model_id == ModelId.CAMBRIDGE
    for name in cambridge.PARAM_NAMES:
        assert back[name] == pytest.approx(params[name], rel=1e-4)


def test_sensor_config_roundtrip():
    names = protocol.sensor_param_names(SensorId.BRETON)
    params = {n: float(i + 1) * 0.5 for i, n in enumerate(names)}
    decoded = protocol.decode_sensor_config(protocol.encode_sensor_config(SensorId.BRETON, params))
    assert decoded is not None
    sid, back = decoded
    assert sid == SensorId.BRETON
    for n in names:
        assert back[n] == pytest.approx(params[n], rel=1e-4)


def test_food_and_exercise_event_roundtrip():
    """encode_*_event is one 8-byte entry; decode_*_events reads the readback
    characteristic (u8 count + entries). Feed one entry through with a count."""
    food = FoodEvent(time_of_day_min=420, carbs_g=45.0, duration_min=30)
    ex = ExerciseEvent(time_of_day_min=1080, duration_min=40, intensity_pct=65.0)
    got_f = protocol.decode_food_events(bytes([1]) + protocol.encode_food_event(food))
    got_e = protocol.decode_exercise_events(bytes([1]) + protocol.encode_exercise_event(ex))
    assert got_f[0].time_of_day_min == 420
    assert got_f[0].carbs_g == pytest.approx(45.0)
    assert got_f[0].duration_min == 30
    assert got_e[0].time_of_day_min == 1080
    assert got_e[0].intensity_pct == pytest.approx(65.0)


def test_clear_sentinels_are_distinct_and_sized():
    assert len(protocol.encode_clear_food()) == 8
    assert len(protocol.encode_clear_exercise()) == 8
    assert protocol.encode_clear_food() != protocol.encode_food_event(
        FoodEvent(time_of_day_min=0, carbs_g=0.0, duration_min=0)
    )


def test_pisa_instant_wire_layout():
    blob = protocol.encode_pisa_instant(12, 0.4)
    assert len(blob) == 6
    dur, depth = struct.unpack("<Hf", blob)
    assert dur == 12
    assert depth == pytest.approx(0.4)
    # depth is clamped to [0, 1]
    _, depth_hi = struct.unpack("<Hf", protocol.encode_pisa_instant(1, 5.0))
    assert depth_hi == pytest.approx(1.0)


def test_glucose_track_clamps_to_int16():
    blob = protocol.build_glucose_track([100.4, -40000.0, 40000.0, 70.6])
    assert struct.unpack("<4h", blob) == (100, -32768, 32767, 71)


def test_csv_crc32_is_deterministic_and_order_sensitive():
    a = protocol.csv_crc32(b"abcdef")
    assert a == protocol.csv_crc32(b"abcdef")
    assert a != protocol.csv_crc32(b"fedcba")


def test_iter_csv_data_chunks_reassembles():
    blob = bytes(range(256)) * 4  # 1024 B
    chunks = list(protocol.iter_csv_data_chunks(blob, chunk_size=100))
    # each data chunk is [u32 offset][payload]
    rebuilt = bytearray(len(blob))
    for ch in chunks:
        off = struct.unpack("<I", ch[:4])[0]
        rebuilt[off : off + len(ch) - 4] = ch[4:]
    assert bytes(rebuilt) == blob


def test_decode_dexcom_glucose_from_a_realtime_message():
    msg = struct.pack("<BBIIHBb", 0x4E, 0, 7, 12345, 142 & 0x0FFF, 0x06, 1)
    got = protocol.decode_dexcom_glucose(msg)
    assert got is not None
    assert got["glucose_value"] == 142
    assert got["sequence"] == 7
    assert got["trend"] == 1


@pytest.mark.parametrize("bad", [b"", b"\x00", b"\x00\x01\x02"])
def test_decoders_tolerate_short_input(bad):
    # None (or a benign default), never an exception
    for dec in (
        protocol.decode_speed,
        protocol.decode_comm_profile,
        protocol.decode_data_source,
        protocol.decode_run_state,
        protocol.decode_sensor_select,
        protocol.decode_dexcom_glucose,
    ):
        dec(bad)


def test_build_csv_uploads_shapes_both_tracks():
    ups = protocol.build_csv_uploads(
        [100, 110, 120], 300, [(0, 30.0), (3600, 45.0)], "2020-01-01T00:00:00"
    )
    assert [u["track"] for u in ups] == [protocol.CSV_TRACK_GLUCOSE, protocol.CSV_TRACK_FOODLOG]
    assert ups[0]["row_count"] == 3
    assert ups[0]["interval_s"] == 300
    assert ups[1]["row_count"] == 2
    assert ups[0]["base_epoch_s"] == ups[1]["base_epoch_s"]  # one epoch for both


def test_build_csv_uploads_omits_foodlog_when_empty():
    ups = protocol.build_csv_uploads([100, 110], 300, [], "2020-01-01T00:00:00")
    assert len(ups) == 1 and ups[0]["track"] == protocol.CSV_TRACK_GLUCOSE
