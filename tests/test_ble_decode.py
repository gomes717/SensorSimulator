"""Issue 20: BleSession's notification decode + per-slot demux is a pure seam
(`decode_notification`) — tested with hand-built bytes, no bleak, no Qt.
"""

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from api import ble_uuids, protocol
from services.ble_session import CGM_MEASUREMENT_UUID, decode_notification


def _decode(uuid, data, *, own=None, count=1, handle=None):
    return decode_notification(
        uuid,
        data,
        user_id="Pt",
        dev_id="AA:BB",
        own_instance_index=own,
        instance_count=count,
        instance_of_handle=handle,
    )


def _msg(r) -> dict:
    assert r.kind == "message"
    assert r.message is not None
    return r.message


def _sig_measurement(mant: int, exp: int = 0, t_off: int = 0) -> bytes:
    return (
        bytes([0x00, 0x00])
        + struct.pack("<H", (exp << 12) | (mant & 0x0FFF))
        + struct.pack("<H", t_off)
    )


def test_sig_measurement_decodes_to_a_message():
    m = _msg(_decode(CGM_MEASUREMENT_UUID, _sig_measurement(101, t_off=5)))
    assert m["glucose_value"] == 101
    assert m["time_offset_min"] == 5
    assert m["user_id"] == "Pt"
    assert "flags" in m  # SIG decoder marker


def test_dexcom_glucose_decodes_to_a_message():
    msg = struct.pack("<BBIIHBb", 0x4E, 0, 9, 600, 142, 0x06, 1)
    m = _msg(_decode(ble_uuids.DEXCOM_GLUCOSE_CHAR_UUID, msg))
    assert m["glucose_value"] == 142
    assert m["sequence"] == 9


def test_reset_sync_and_csv_control_are_control_events():
    assert _decode(ble_uuids.RESET_SYNC_UUID, b"\x02").kind == "reset_sync"
    r = _decode(ble_uuids.CSV_CONTROL_UUID, protocol.encode_csv_status(0))
    assert r.kind == "csv_control"
    assert r.message is None


def test_sibling_instance_measurement_is_ignored():
    assert _decode(CGM_MEASUREMENT_UUID, _sig_measurement(97), own=2, count=4, handle=0).kind == (
        "ignore"
    )
    assert (
        _msg(_decode(CGM_MEASUREMENT_UUID, _sig_measurement(97), own=2, count=4, handle=2))[
            "glucose_value"
        ]
        == 97
    )


def test_unnumbered_multi_instance_tags_the_user_id_with_the_slot():
    m = _msg(_decode(CGM_MEASUREMENT_UUID, _sig_measurement(97), own=None, count=4, handle=1))
    assert m["user_id"] == "Pt · Sensor 2"


def test_nameless_multi_instance_connection_stays_one_row():
    """A connection whose name never resolved (user_id == the bare address) is
    not fanned out to per-slot rows — see decode_notification's comment."""
    r = decode_notification(
        CGM_MEASUREMENT_UUID,
        _sig_measurement(97),
        user_id="AA:BB",
        dev_id="AA:BB",
        own_instance_index=None,
        instance_count=4,
        instance_of_handle=1,
    )
    assert _msg(r)["user_id"] == "AA:BB"


def test_food_exercise_status_for_another_slot_is_ignored():
    frame = struct.pack("<BBff", 3, 0, 1.5, 0.0)  # u8 slot, u8 pad, f32 carbs/min, f32 ex%
    assert _msg(_decode(ble_uuids.FOOD_EXERCISE_STATUS_UUID, frame, own=3))["slot"] == 3
    assert _decode(ble_uuids.FOOD_EXERCISE_STATUS_UUID, frame, own=1).kind == "ignore"
