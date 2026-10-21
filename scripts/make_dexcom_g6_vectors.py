"""Regenerate the Dexcom G6 golden vectors shared by the Python and C implementations.

    uv run python scripts/make_dexcom_g6_vectors.py

Writes ``tests/data/dexcom_g6_vectors.json`` (read by ``tests/test_dexcom_g6.py``) and the C header
``firmware/peripheral_cgms/tests/host/g6_vectors.h`` (included by the host test), so the two
implementations are checked against the same bytes. The primitives are cross-checked here against
independent references before anything is written: the published CRC-16/XMODEM check value,
FIPS-197's AES example, and ``openssl`` for the challenge hash when it is installed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from api import dexcom_g6 as g6
from api import dexcom_g6_transmitter as tx

JSON_OUT = ROOT / "tests" / "data" / "dexcom_g6_vectors.json"
HEADER_OUT = ROOT / "firmware" / "peripheral_cgms" / "tests" / "host" / "g6_vectors.h"

TX_ID = "8SIM00"
TOKEN = bytes.fromhex("0102030405060708")
RANDOM = bytes.fromhex("a1b2c3d4e5f60718")
READING = g6.Reading(seq=7, ts_s=2100, glucose=123, state=g6.STATE_OK, trend=-12)
NOW_S = 2155


def _independent_checks() -> None:
    assert g6.crc16(b"123456789") == 0x31C3
    key = bytes(range(16))
    plain = bytes.fromhex("00112233445566778899aabbccddeeff")
    assert g6.aes128_encrypt_block(key, plain).hex() == "69c4e0d86a7b0430d8cdb78070b4c55a"
    if shutil.which("openssl"):
        for data in (TOKEN, RANDOM, bytes(8), bytes([0xFF] * 8)):
            out = subprocess.run(
                ["openssl", "enc", "-aes-128-ecb", "-nopad", "-K", g6.crypt_key(TX_ID).hex()],
                input=data + data, capture_output=True, check=True,
            ).stdout  # fmt: skip
            assert out[:8] == g6.challenge_hash(data, TX_ID), (
                "challenge hash disagrees with openssl"
            )
    else:
        print("openssl not found: the challenge hash was not cross-checked", file=sys.stderr)


def _control_case(name: str, authed: bool, latest: g6.Reading | None, request: bytes) -> dict:
    session = tx.Session(authenticated=authed)
    out = tx.handle_control(session, latest, NOW_S, request)
    return {
        "name": name,
        "authed": authed,
        "reading": None
        if latest is None
        else {
            "seq": latest.seq,
            "ts": latest.ts_s,
            "glucose": latest.glucose,
            "state": latest.state,
            "trend": latest.trend,
        },
        "now_s": NOW_S,
        "request": request.hex(),
        "result": out.result.value,
        "reply": out.reply.hex(),
    }


def build() -> dict:
    """All the vectors, as a JSON-able dict."""
    _independent_checks()
    session = tx.Session()
    request = g6.auth_request(TOKEN)
    challenge = tx.handle_auth(session, TX_ID, request, RANDOM)
    proof = g6.challenge_hash(RANDOM, TX_ID)
    status = tx.handle_auth(session, TX_ID, g6.auth_challenge_reply(proof), RANDOM)
    wrong = tx.Session()
    tx.handle_auth(wrong, TX_ID, request, RANDOM)
    bad = tx.handle_auth(wrong, TX_ID, g6.auth_challenge_reply(bytes(8)), RANDOM)

    controls = [
        _control_case("eglucose", True, READING, g6.eglucose_request()),
        _control_case("glucose", True, READING, g6.glucose_request()),
        _control_case("sensor", True, READING, g6.sensor_request()),
        _control_case("time", True, READING, g6.time_request()),
        _control_case("version", True, READING, g6.simple_request(g6.OP_VERSION)),
        _control_case("version1", True, READING, g6.simple_request(g6.OP_VERSION1)),
        _control_case("version2", True, READING, g6.simple_request(g6.OP_VERSION2)),
        _control_case("battery", True, READING, g6.simple_request(g6.OP_BATTERY)),
        _control_case(
            "calibrate", True, READING, g6.with_crc(bytes([g6.OP_CALIBRATE, 100, 0, 0, 0, 0, 0]))
        ),
        _control_case("no reading yet", True, None, g6.eglucose_request()),
        _control_case("not authenticated", False, READING, g6.eglucose_request()),
        _control_case("bad crc", True, READING, g6.eglucose_request()[:-1] + b"\x00"),
        _control_case("unknown opcode", True, READING, g6.simple_request(0x77)),
        _control_case("disconnect", True, READING, bytes([g6.OP_DISCONNECT])),
    ]
    return {
        "transmitter_id": TX_ID,
        "crc": [
            {"data": d.hex(), "crc": g6.crc16(d)}
            for d in (b"", b"123456789", bytes([0x4E]), bytes([0x30]), bytes(range(16)))
        ],
        "aes": [
            {
                "key": bytes(range(16)).hex(),
                "plain": "00112233445566778899aabbccddeeff",
                "cipher": "69c4e0d86a7b0430d8cdb78070b4c55a",
            }
        ],
        "hash": [
            {"id": TX_ID, "data": d.hex(), "hash": g6.challenge_hash(d, TX_ID).hex()}
            for d in (TOKEN, RANDOM, bytes(8), bytes([0xFF] * 8))
        ]
        + [{"id": "9ZZ99A", "data": TOKEN.hex(), "hash": g6.challenge_hash(TOKEN, "9ZZ99A").hex()}],
        "auth": {
            "id": TX_ID,
            "token": TOKEN.hex(),
            "random": RANDOM.hex(),
            "request": request.hex(),
            "challenge": challenge.reply.hex(),
            "reply": g6.auth_challenge_reply(proof).hex(),
            "status": status.reply.hex(),
            "wrong_reply": g6.auth_challenge_reply(bytes(8)).hex(),
            "wrong_status": bad.reply.hex(),
        },
        "control": controls,
        "grid": {
            "period_s": g6.GRID_PERIOD_S,
            "ticks": [
                {"sim_s": 0, "glucose": 100.4, "trend": 0.0},
                {"sim_s": 1, "glucose": 101.0, "trend": 0.1},
                {"sim_s": 299, "glucose": 130.0, "trend": 0.2},
                {"sim_s": 300, "glucose": 144.6, "trend": -1.25},
                {"sim_s": 450, "glucose": 150.0, "trend": 0.0},
                {"sim_s": 905, "glucose": 20.0, "trend": None},
                {"sim_s": 1200, "glucose": 999.0, "trend": 99.0},
            ],
        },
    }


def _grid_expected(data: dict) -> list[dict | None]:
    grid = tx.ReadingGrid(data["grid"]["period_s"])
    out: list[dict | None] = []
    for t in data["grid"]["ticks"]:
        r = grid.feed(t["sim_s"], t["glucose"], t["trend"])
        out.append(
            None
            if r is None
            else {"seq": r.seq, "ts": r.ts_s, "glucose": r.glucose, "trend": r.trend}
        )
    return out


def _header(data: dict) -> str:
    """The C header: the same vectors as string / integer literals."""

    def q(s: str) -> str:
        return '"' + s + '"'

    lines = ["/* GENERATED by scripts/make_dexcom_g6_vectors.py - do not edit. */", ""]
    lines.append(f"#define G6V_TX_ID {q(data['transmitter_id'])}")
    lines.append("")
    lines.append("struct g6v_crc { const char *data; unsigned crc; };")
    lines.append("static const struct g6v_crc G6V_CRC[] = {")
    lines += [f"\t{{{q(c['data'])}, {c['crc']}u}}," for c in data["crc"]]
    lines += ["};", ""]
    lines.append("struct g6v_aes { const char *key, *plain, *cipher; };")
    lines.append("static const struct g6v_aes G6V_AES[] = {")
    lines += [f"\t{{{q(a['key'])}, {q(a['plain'])}, {q(a['cipher'])}}}," for a in data["aes"]]
    lines += ["};", ""]
    lines.append("struct g6v_hash { const char *id, *data, *hash; };")
    lines.append("static const struct g6v_hash G6V_HASH[] = {")
    lines += [f"\t{{{q(h['id'])}, {q(h['data'])}, {q(h['hash'])}}}," for h in data["hash"]]
    lines += ["};", ""]
    a = data["auth"]
    lines.append(
        "static const struct { const char *id, *token, *random, *request, *challenge, *reply,"
        " *status, *wrong_reply, *wrong_status; } G6V_AUTH = {"
    )
    lines.append(
        "\t" + ", ".join(q(a[k]) for k in (
            "id", "token", "random", "request", "challenge", "reply", "status",
            "wrong_reply", "wrong_status",
        )) + "};"
    )  # fmt: skip
    lines.append("")
    lines.append(
        "struct g6v_control { const char *name; int authed; int has_reading; unsigned seq, ts,"
        " glucose, state; int trend; unsigned now_s; const char *request, *result, *reply; };"
    )
    lines.append("static const struct g6v_control G6V_CONTROL[] = {")
    for c in data["control"]:
        r = c["reading"] or {"seq": 0, "ts": 0, "glucose": 0, "state": 0, "trend": 0}
        lines.append(
            f"\t{{{q(c['name'])}, {int(c['authed'])}, {int(c['reading'] is not None)}, "
            f"{r['seq']}u, {r['ts']}u, {r['glucose']}u, {r['state']}u, {r['trend']}, "
            f"{c['now_s']}u, {q(c['request'])}, {q(c['result'])}, {q(c['reply'])}}},"
        )
    lines += ["};", ""]
    lines.append("struct g6v_tick { unsigned sim_s; double glucose; int has_trend; double trend;")
    lines.append("\tint expect; unsigned seq, ts, out_glucose; int out_trend; };")
    lines.append("static const struct g6v_tick G6V_TICKS[] = {")
    for t, e in zip(data["grid"]["ticks"], data["grid_expected"], strict=True):
        trend = t["trend"]
        lines.append(
            f"\t{{{t['sim_s']}u, {t['glucose']!r}, {int(trend is not None)}, "
            f"{(trend if trend is not None else 0.0)!r}, {int(e is not None)}, "
            f"{(e or {}).get('seq', 0)}u, {(e or {}).get('ts', 0)}u, "
            f"{(e or {}).get('glucose', 0)}u, {(e or {}).get('trend', 0)}}},"
        )
    lines += ["};", f"#define G6V_PERIOD_S {data['grid']['period_s']}u", ""]
    return "\n".join(lines)


def main() -> None:
    """Write both outputs."""
    data = build()
    data["grid_expected"] = _grid_expected(data)
    JSON_OUT.parent.mkdir(parents=True, exist_ok=True)
    JSON_OUT.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    HEADER_OUT.parent.mkdir(parents=True, exist_ok=True)
    HEADER_OUT.write_text(_header(data), encoding="utf-8", newline="\n")
    print(f"wrote {JSON_OUT.relative_to(ROOT)} and {HEADER_OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
