"""The Dexcom G6 protocol (api/dexcom_g6.py) and its reference transmitter
(api/dexcom_g6_transmitter.py), checked against independent known answers and the golden vectors
that the firmware's C twin is also held to (tests/data/dexcom_g6_vectors.json)."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from api import dexcom_g6 as g6
from api import dexcom_g6_transmitter as tx

VECTORS = json.loads(
    (Path(__file__).resolve().parent / "data" / "dexcom_g6_vectors.json").read_text("utf-8")
)
TX_ID = VECTORS["transmitter_id"]


# -- primitives against published answers ------------------------------------------------------


def test_crc16_check_value_is_the_published_xmodem_one():
    assert g6.crc16(b"123456789") == 0x31C3


@pytest.mark.parametrize("case", VECTORS["crc"], ids=lambda c: c["data"][:12] or "empty")
def test_crc16_matches_the_vectors(case):
    assert g6.crc16(bytes.fromhex(case["data"])) == case["crc"]


@pytest.mark.parametrize("case", VECTORS["aes"])
def test_aes128_matches_fips_197(case):
    out = g6.aes128_encrypt_block(bytes.fromhex(case["key"]), bytes.fromhex(case["plain"]))
    assert out.hex() == case["cipher"]


@pytest.mark.parametrize("case", VECTORS["hash"], ids=lambda c: c["id"] + c["data"][:4])
def test_challenge_hash_matches_the_vectors(case):
    assert g6.challenge_hash(bytes.fromhex(case["data"]), case["id"]).hex() == case["hash"]


def test_the_key_is_the_id_padded_the_way_xdrip_does():
    assert g6.crypt_key("8SIM00") == b"008SIM00008SIM00"


@pytest.mark.parametrize("bad", ["", "8SIM0", "8SIM000", "8sim00", "8SIM 0", "8SIM0é"])
def test_a_transmitter_id_is_six_digits_or_capitals(bad):
    assert not g6.valid_transmitter_id(bad)
    with pytest.raises(ValueError):
        g6.crypt_key(bad)


def test_the_advertised_name_is_dexcom_plus_the_last_two_characters():
    assert g6.advertised_name("8SIM00") == "Dexcom00"
    assert g6.advertised_name("8AB1CD") == "DexcomCD"


def test_crc_ok_rejects_short_and_corrupt_messages():
    msg = g6.eglucose_request()
    assert g6.crc_ok(msg)
    assert not g6.crc_ok(msg[:2])
    assert not g6.crc_ok(msg[:-1] + bytes([msg[-1] ^ 1]))


# -- reading encodings ----------------------------------------------------------------------


def test_the_glucose_replies_round_trip_through_the_decoder():
    reading = g6.Reading(seq=9, ts_s=2700, glucose=187, state=g6.STATE_OK, trend=-5, predicted=190)
    for encode, length in ((g6.encode_eglucose_reply, 18), (g6.encode_glucose_reply, 16)):
        wire = encode(reading)
        assert len(wire) == length
        back = g6.decode_glucose(wire)
        assert back is not None
        assert (back.seq, back.ts_s, back.glucose, back.state, back.trend) == (9, 2700, 187, 6, -5)
    assert g6.decode_glucose(g6.encode_eglucose_reply(reading)).predicted == 190  # type: ignore[union-attr]


def test_the_decoder_refuses_bad_crc_short_and_mistagged_messages():
    wire = g6.encode_eglucose_reply(g6.Reading(seq=1, ts_s=0, glucose=100))
    assert g6.decode_glucose(wire[:-1] + bytes([wire[-1] ^ 0xFF])) is None
    assert g6.decode_glucose(wire[:10]) is None
    assert g6.decode_glucose(bytes([0x99]) + wire[1:]) is None


def test_the_display_only_flag_does_not_leak_into_the_glucose_value():
    reading = g6.Reading(seq=0, ts_s=0, glucose=250, display_only=True)
    back = g6.decode_glucose(g6.encode_glucose_reply(reading))
    assert back is not None and back.glucose == 250 and back.display_only


def test_glucose_is_clamped_to_the_range_a_dexcom_reports():
    assert [g6.clamp_glucose(x) for x in (12.0, 39.6, 40.4, 399.5, 700.0)] == [40, 40, 40, 400, 400]


def test_the_trend_is_tenths_clamped_and_unknown_is_127():
    assert g6.clamp_trend(1.25) == 13  # half away from zero, as in C
    assert g6.clamp_trend(-1.25) == -13
    assert g6.clamp_trend(99.0) == 126 and g6.clamp_trend(-99.0) == -126
    assert g6.clamp_trend(None) == g6.TREND_INVALID
    assert g6.Reading(0, 0, 100, trend=g6.TREND_INVALID).trend_mgdl_min is None
    assert g6.Reading(0, 0, 100, trend=-12).trend_mgdl_min == pytest.approx(-1.2)


# -- authentication ------------------------------------------------------------------------


def test_the_auth_flow_matches_the_vectors_and_ends_authenticated():
    a = VECTORS["auth"]
    session = tx.Session()
    out = tx.handle_auth(session, a["id"], bytes.fromhex(a["request"]), bytes.fromhex(a["random"]))
    assert out.known and out.reply.hex() == a["challenge"]
    out = tx.handle_auth(session, a["id"], bytes.fromhex(a["reply"]), bytes.fromhex(a["random"]))
    assert out.reply.hex() == a["status"] and session.authenticated


def test_a_wrong_challenge_reply_is_refused():
    a = VECTORS["auth"]
    session = tx.Session()
    tx.handle_auth(session, a["id"], bytes.fromhex(a["request"]), bytes.fromhex(a["random"]))
    out = tx.handle_auth(session, a["id"], bytes.fromhex(a["wrong_reply"]), bytes(8))
    assert out.reply.hex() == a["wrong_status"] and not session.authenticated


def test_a_client_that_does_not_know_the_id_cannot_authenticate():
    """It can request a challenge (anyone can) but cannot answer it: the hash needs the ID."""
    session = tx.Session()
    rnd = bytes(range(8))
    out = tx.handle_auth(session, TX_ID, g6.auth_request(bytes(8)), rnd)
    challenge = g6.decode_auth_challenge(out.reply)
    assert challenge is not None
    forged = g6.challenge_hash(challenge.challenge, "8WRONG")  # a different ID
    reply = tx.handle_auth(session, TX_ID, g6.auth_challenge_reply(forged), rnd)
    assert g6.decode_auth_status(reply.reply) == (False, True)
    assert not session.authenticated


def test_the_transmitter_proves_it_knows_the_id_to_the_client():
    """xDrip verifies tokenHash: the challenge reply carries hash(token) under the shared key."""
    token = bytes(range(8, 16))
    out = tx.handle_auth(tx.Session(), TX_ID, g6.auth_request(token), bytes(8))
    parsed = g6.decode_auth_challenge(out.reply)
    assert parsed is not None and parsed.token_hash == g6.challenge_hash(token, TX_ID)


def test_a_challenge_reply_without_a_request_is_not_authenticated():
    session = tx.Session()
    out = tx.handle_auth(session, TX_ID, g6.auth_challenge_reply(bytes(8)), bytes(8))
    assert g6.decode_auth_status(out.reply) == (False, True) and not session.authenticated


@pytest.mark.parametrize(
    "data", [b"", bytes([g6.OP_AUTH_REQUEST]), bytes([g6.OP_AUTH_REQUEST]) + bytes(8), b"\x99\x00"]
)
def test_malformed_or_unknown_auth_writes_are_not_answered(data):
    assert not tx.handle_auth(tx.Session(), TX_ID, data, bytes(8)).known


def test_keep_alive_and_bond_request_are_accepted_silently():
    for msg in (g6.keep_alive(), g6.bond_request()):
        out = tx.handle_auth(tx.Session(), TX_ID, msg, bytes(8))
        assert out.known and out.reply == b""


# -- control requests ----------------------------------------------------------------------


@pytest.mark.parametrize("case", VECTORS["control"], ids=lambda c: c["name"])
def test_control_requests_match_the_vectors(case):
    r = case["reading"]
    latest = (
        None if r is None else g6.Reading(r["seq"], r["ts"], r["glucose"], r["state"], r["trend"])
    )
    out = tx.handle_control(
        tx.Session(authenticated=case["authed"]),
        latest,
        case["now_s"],
        bytes.fromhex(case["request"]),
    )
    assert out.result.value == case["result"]
    assert out.reply.hex() == case["reply"]


def test_every_reply_xdrip_checks_carries_a_valid_crc_and_the_length_it_expects():
    reading = g6.Reading(seq=1, ts_s=300, glucose=110)
    expected_len = {
        g6.OP_EGLUCOSE_REPLY: 18, g6.OP_GLUCOSE_REPLY: 16, g6.OP_SENSOR_REPLY: 16,
        g6.OP_TIME_REPLY: 12, g6.OP_VERSION_REPLY: 18, g6.OP_BATTERY_REPLY: 10,
        g6.OP_VERSION2_REPLY: 9, g6.OP_SESSION_START_REPLY: 17, g6.OP_SESSION_STOP_REPLY: 17,
        g6.OP_CALIBRATE_REPLY: 5,
    }  # fmt: skip
    session = tx.Session(authenticated=True)
    for request in (
        g6.eglucose_request(), g6.glucose_request(), g6.sensor_request(), g6.time_request(),
        g6.simple_request(g6.OP_VERSION), g6.simple_request(g6.OP_BATTERY),
        g6.simple_request(g6.OP_VERSION2), g6.simple_request(g6.OP_SESSION_START),
        g6.simple_request(g6.OP_SESSION_STOP), g6.simple_request(g6.OP_CALIBRATE),
    ):  # fmt: skip
        out = tx.handle_control(session, reading, 300, request)
        assert out.result is tx.Result.REPLY
        assert g6.crc_ok(out.reply), request.hex()
        assert len(out.reply) == expected_len[out.reply[0]], request.hex()


def test_control_is_refused_until_authenticated():
    out = tx.handle_control(tx.Session(), g6.Reading(0, 0, 100), 0, g6.eglucose_request())
    assert out.result is tx.Result.NOT_AUTHENTICATED and out.reply == b""


def test_the_time_reply_reports_the_transmitter_clock():
    out = tx.handle_control(tx.Session(authenticated=True), None, 4242, g6.time_request())
    assert g6.decode_time(out.reply) == (4242, 0)


def test_the_sensor_reply_carries_raw_values_derived_from_the_glucose():
    out = tx.handle_control(
        tx.Session(authenticated=True), g6.Reading(0, 300, 120), 300, g6.sensor_request()
    )
    assert g6.decode_sensor(out.reply) == (300, 120_000, 120_000)


# -- the 5-minute rule ---------------------------------------------------------------------


def test_the_first_tick_publishes_at_once_then_one_reading_per_period():
    grid = tx.ReadingGrid()
    first = grid.feed(0.0, 100.0)
    assert first is not None and (first.seq, first.ts_s) == (0, 0)
    assert grid.feed(1.0, 101.0) is None and grid.feed(299.9, 130.0) is None
    second = grid.feed(300.0, 144.6)
    assert second is not None and (second.seq, second.ts_s, second.glucose) == (1, 300, 145)
    assert grid.latest is second  # held until the next boundary


def test_the_reading_is_the_value_at_the_boundary_not_the_last_one_before_it():
    grid = tx.ReadingGrid()
    grid.feed(0.0, 100.0)
    grid.feed(299.0, 120.0)
    reading = grid.feed(301.0, 130.0)
    assert reading is not None and reading.glucose == 130 and reading.ts_s == 300


def test_a_jump_over_several_boundaries_publishes_once_with_the_latest_boundary_time():
    grid = tx.ReadingGrid()
    grid.feed(0.0, 100.0)
    reading = grid.feed(1000.0, 150.0)
    assert reading is not None and reading.ts_s == 900 and reading.seq == 1


def test_the_grid_starts_over_when_the_simulated_clock_restarts():
    grid = tx.ReadingGrid()
    grid.feed(0.0, 100.0)
    grid.feed(900.0, 110.0)
    again = grid.feed(5.0, 90.0)
    assert again is not None and (again.seq, again.ts_s) == (0, 0)


def test_the_grid_matches_the_vectors():
    grid = tx.ReadingGrid(VECTORS["grid"]["period_s"])
    for tick, expected in zip(VECTORS["grid"]["ticks"], VECTORS["grid_expected"], strict=True):
        got = grid.feed(tick["sim_s"], tick["glucose"], tick["trend"])
        if expected is None:
            assert got is None
        else:
            assert got is not None
            assert {
                "seq": got.seq,
                "ts": got.ts_s,
                "glucose": got.glucose,
                "trend": got.trend,
            } == expected
