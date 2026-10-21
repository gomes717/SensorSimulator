"""The Dexcom G6 transmitter wire protocol, as xDrip speaks it.

Design and sources: ``.scratch/dexcom-g6-transmitter/``.

Pure functions and small dataclasses — no Qt, no bleak — so the app, the test client and the
tests share one definition. ``firmware/peripheral_cgms/src/dexcom_g6.c`` is the C twin; both are
checked against ``tests/data/dexcom_g6_vectors.json``.

Little-endian throughout. Every message except the authentication ones ends in a CRC-16/XMODEM
over all preceding bytes. Authentication (characteristic ``…3535``):

    client  01 | token[8] | 02                    -> transmitter  03 | hash(token)[8] | challenge[8]
    client  04 | hash(challenge)[8]               -> transmitter  05 | authenticated | bonded

with ``hash(x) = AES-128-ECB(key, x || x)[:8]`` and ``key = ASCII("00" + id + "00" + id)``: the
6-character transmitter ID is the shared secret.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass

# ---- identifiers ----------------------------------------------------------------------------

TRANSMITTER_ID_LEN = 6
_ID_CHARS = frozenset("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ")
DEFAULT_TRANSMITTER_ID = "8SIM00"

# Auth characteristic opcodes
OP_AUTH_REQUEST = 0x01
OP_AUTH_CHALLENGE = 0x03
OP_AUTH_CHALLENGE_REPLY = 0x04
OP_AUTH_STATUS = 0x05
OP_KEEP_ALIVE = 0x06
OP_BOND_REQUEST = 0x07
# Control characteristic opcodes (request, and its reply = the next value unless noted)
OP_DISCONNECT = 0x09
OP_UNBOND = 0x0A
OP_VERSION = 0x20
OP_VERSION_REPLY = 0x21
OP_BATTERY = 0x22
OP_BATTERY_REPLY = 0x23
OP_TIME = 0x24
OP_TIME_REPLY = 0x25
OP_SESSION_START = 0x26
OP_SESSION_START_REPLY = 0x27
OP_SESSION_STOP = 0x28
OP_SESSION_STOP_REPLY = 0x29
OP_SENSOR = 0x2E
OP_SENSOR_REPLY = 0x2F
OP_GLUCOSE = 0x30
OP_GLUCOSE_REPLY = 0x31
OP_CALIBRATE = 0x34
OP_CALIBRATE_REPLY = 0x35
OP_EGLUCOSE = 0x4E
OP_EGLUCOSE_REPLY = 0x4F
OP_VERSION1 = 0x4A
OP_VERSION1_REPLY = 0x4B
OP_VERSION2 = 0x52
OP_VERSION2_REPLY = 0x53

STATE_WARMING_UP = 0x02
STATE_OK = 0x06
TREND_INVALID = 127
GLUCOSE_MIN, GLUCOSE_MAX = 40, 400
GLUCOSE_MASK = 0x0FFF
DISPLAY_ONLY_MASK = 0xF000
GRID_PERIOD_S = 300  # one reading every 5 minutes

TOKEN_LEN = 8
HASH_LEN = 8


def valid_transmitter_id(tx_id: str) -> bool:
    """A G6 transmitter ID: six characters, digits and capital letters."""
    return len(tx_id) == TRANSMITTER_ID_LEN and all(c in _ID_CHARS for c in tx_id)


def advertised_name(tx_id: str) -> str:
    """The BLE name xDrip scans for: ``Dexcom`` + the last two characters of the ID."""
    return "Dexcom" + tx_id[4:6]


# ---- CRC-16/XMODEM (poly 0x1021, init 0) ----------------------------------------------------


def crc16(data: bytes) -> int:
    """CRC-16/XMODEM of *data* (the check value of ``b"123456789"`` is 0x31C3)."""
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def with_crc(body: bytes) -> bytes:
    """*body* followed by its CRC, little-endian."""
    return body + struct.pack("<H", crc16(body))


def crc_ok(message: bytes) -> bool:
    """Whether the last two bytes of *message* are the CRC of the rest."""
    return len(message) >= 3 and struct.unpack("<H", message[-2:])[0] == crc16(message[:-2])


# ---- AES-128 (encrypt only; the protocol never decrypts) ------------------------------------


def _build_sbox() -> tuple[int, ...]:
    """The AES S-box, derived from the field inverse so there is no table to mistype."""
    sbox = [0] * 256
    p = q = 1
    while True:
        p = p ^ ((p << 1) & 0xFF) ^ (0x1B if p & 0x80 else 0)  # p *= 3
        q ^= q << 1  # q /= 3
        q ^= q << 2
        q ^= q << 4
        q &= 0xFF
        if q & 0x80:
            q ^= 0x09
        x = q ^ ((q << 1) | (q >> 7)) ^ ((q << 2) | (q >> 6)) ^ ((q << 3) | (q >> 5))
        x ^= (q << 4) | (q >> 4)
        sbox[p] = (x ^ 0x63) & 0xFF
        if p == 1:
            break
    sbox[0] = 0x63
    return tuple(sbox)


_SBOX = _build_sbox()


def _xtime(a: int) -> int:
    return ((a << 1) ^ 0x1B) & 0xFF if a & 0x80 else a << 1


def _expand_key(key: bytes) -> list[bytes]:
    words = [list(key[i : i + 4]) for i in range(0, 16, 4)]
    rcon = 1
    for i in range(4, 44):
        temp = list(words[i - 1])
        if i % 4 == 0:
            temp = [_SBOX[b] for b in temp[1:] + temp[:1]]
            temp[0] ^= rcon
            rcon = _xtime(rcon)
        words.append([a ^ b for a, b in zip(words[i - 4], temp, strict=True)])
    return [bytes(b for w in words[r * 4 : r * 4 + 4] for b in w) for r in range(11)]


def aes128_encrypt_block(key: bytes, block: bytes) -> bytes:
    """AES-128 of one 16-byte *block* under a 16-byte *key* (FIPS-197)."""
    if len(key) != 16 or len(block) != 16:
        raise ValueError("AES-128 needs a 16-byte key and a 16-byte block")
    round_keys = _expand_key(key)
    state = [b ^ k for b, k in zip(block, round_keys[0], strict=True)]
    for rnd in range(1, 11):
        state = [_SBOX[b] for b in state]
        # ShiftRows (state is column-major: index = 4 * col + row)
        state = [state[(4 * (c + r) + r) % 16] for c in range(4) for r in range(4)]
        if rnd != 10:
            mixed = []
            for c in range(4):
                a = state[4 * c : 4 * c + 4]
                for r in range(4):
                    mixed.append(
                        _xtime(a[r]) ^ _xtime(a[(r + 1) % 4]) ^ a[(r + 1) % 4]
                        ^ a[(r + 2) % 4] ^ a[(r + 3) % 4]
                    )  # fmt: skip
            state = mixed
        state = [b ^ k for b, k in zip(state, round_keys[rnd], strict=True)]
    return bytes(state)


def crypt_key(tx_id: str) -> bytes:
    """The AES key derived from the transmitter ID: ASCII ``"00" + id + "00" + id``."""
    if not valid_transmitter_id(tx_id):
        raise ValueError(f"not a G6 transmitter ID: {tx_id!r}")
    return f"00{tx_id}00{tx_id}".encode("ascii")


def challenge_hash(data: bytes, tx_id: str) -> bytes:
    """``AES-128-ECB(key, data || data)[:8]`` — the proof of knowing the transmitter ID."""
    if len(data) != TOKEN_LEN:
        raise ValueError("a challenge / token is 8 bytes")
    return aes128_encrypt_block(crypt_key(tx_id), data + data)[:HASH_LEN]


# ---- client -> transmitter messages ---------------------------------------------------------


def auth_request(token: bytes, alt: bool = False) -> bytes:
    """``01 | token[8] | end`` (end = 02, or 01 for xDrip's "alt" variant)."""
    if len(token) != TOKEN_LEN:
        raise ValueError("the token is 8 bytes")
    return bytes([OP_AUTH_REQUEST]) + token + bytes([1 if alt else 2])


def auth_challenge_reply(hash8: bytes) -> bytes:
    """``04 | hash(challenge)[8]``."""
    return bytes([OP_AUTH_CHALLENGE_REPLY]) + hash8


def keep_alive(seconds: int = 25) -> bytes:
    """``06 | n`` (no CRC)."""
    return bytes([OP_KEEP_ALIVE, seconds & 0xFF])


def bond_request() -> bytes:
    """``07``."""
    return bytes([OP_BOND_REQUEST])


def simple_request(opcode: int) -> bytes:
    """A control request that is just the opcode and its CRC (EGlucoseTx, GlucoseTx, …)."""
    return with_crc(bytes([opcode]))


def eglucose_request() -> bytes:
    """``4E`` + CRC — the glucose request of a G6."""
    return simple_request(OP_EGLUCOSE)


def glucose_request() -> bytes:
    """``30`` + CRC — the G5-style glucose request."""
    return simple_request(OP_GLUCOSE)


def sensor_request() -> bytes:
    """``2E`` + CRC — the raw-data request (xDrip's own-calibration mode)."""
    return simple_request(OP_SENSOR)


def time_request() -> bytes:
    """``24`` + CRC."""
    return simple_request(OP_TIME)


# ---- transmitter -> client messages ---------------------------------------------------------


@dataclass(frozen=True)
class Reading:
    """One glucose reading as the transmitter reports it."""

    seq: int
    ts_s: int  # transmitter time, seconds
    glucose: int  # mg/dL
    state: int = STATE_OK
    trend: int = 0  # tenths of mg/dL/min; TREND_INVALID = unknown
    predicted: int | None = None
    status: int = 0
    display_only: bool = False

    @property
    def trend_mgdl_min(self) -> float | None:
        """The trend in mg/dL per minute, or None when the transmitter says it is unknown."""
        return None if self.trend == TREND_INVALID else self.trend / 10.0


def round_half_away(value: float) -> int:
    """Round to the nearest integer, halves away from zero (like the C twin)."""
    return int(math.copysign(math.floor(abs(value) + 0.5), value))


def clamp_glucose(mgdl: float) -> int:
    """A model value as the transmitter reports it: rounded and held to 40-400 mg/dL."""
    return max(GLUCOSE_MIN, min(GLUCOSE_MAX, round_half_away(mgdl)))


def clamp_trend(mgdl_per_min: float | None) -> int:
    """A rate of change as the int8 the protocol carries (tenths, ±126; 127 = unknown)."""
    if mgdl_per_min is None:
        return TREND_INVALID
    return max(-126, min(126, round_half_away(mgdl_per_min * 10)))


def _glucose_word(reading: Reading) -> int:
    return (reading.glucose & GLUCOSE_MASK) | (DISPLAY_ONLY_MASK if reading.display_only else 0)


def encode_glucose_reply(reading: Reading) -> bytes:
    """``31 status seq[4] ts[4] glu[2] state trend CRC`` (16 bytes)."""
    body = struct.pack(
        "<BBIIHBb", OP_GLUCOSE_REPLY, reading.status, reading.seq, reading.ts_s,
        _glucose_word(reading), reading.state, reading.trend,
    )  # fmt: skip
    return with_crc(body)


def encode_eglucose_reply(reading: Reading) -> bytes:
    """``4F status seq[4] ts[4] glu[2] state trend pred[2] CRC`` (18 bytes)."""
    predicted = reading.glucose if reading.predicted is None else reading.predicted
    body = struct.pack(
        "<BBIIHBbH", OP_EGLUCOSE_REPLY, reading.status, reading.seq, reading.ts_s,
        _glucose_word(reading), reading.state, reading.trend, predicted & 0x03FF,
    )  # fmt: skip
    return with_crc(body)


def encode_sensor_reply(reading: Reading) -> bytes:
    """``2F status ts[4] unfiltered[4] filtered[4] CRC`` (16 bytes); raw = glucose * 1000."""
    raw = reading.glucose * 1000
    return with_crc(struct.pack("<BBIII", OP_SENSOR_REPLY, reading.status, reading.ts_s, raw, raw))


def encode_time_reply(current_s: int, session_start_s: int, status: int = 0) -> bytes:
    """``25 status current[4] sessionStart[4] CRC`` (12 bytes)."""
    return with_crc(struct.pack("<BBII", OP_TIME_REPLY, status, current_s, session_start_s))


def decode_glucose(message: bytes) -> Reading | None:
    """Parse a ``31`` (G5) or ``4F`` (G6) reply; None if it is short, mistagged or has a bad CRC."""
    if len(message) < 14 or message[0] not in (OP_GLUCOSE_REPLY, OP_EGLUCOSE_REPLY):
        return None
    if not crc_ok(message):
        return None
    status, seq, ts, word, state, trend = struct.unpack_from("<BIIHBb", message, 1)
    predicted = None
    if message[0] == OP_EGLUCOSE_REPLY and len(message) >= 18:
        predicted = struct.unpack_from("<H", message, 14)[0] & 0x03FF
    return Reading(
        seq=seq, ts_s=ts, glucose=word & GLUCOSE_MASK, state=state, trend=trend,
        predicted=predicted, status=status, display_only=bool(word & DISPLAY_ONLY_MASK),
    )  # fmt: skip


@dataclass(frozen=True)
class AuthChallenge:
    """The transmitter's answer to an auth request."""

    token_hash: bytes
    challenge: bytes


def decode_auth_challenge(message: bytes) -> AuthChallenge | None:
    """Parse ``03 | tokenHash[8] | challenge[8]``."""
    if len(message) < 17 or message[0] != OP_AUTH_CHALLENGE:
        return None
    return AuthChallenge(token_hash=message[1:9], challenge=message[9:17])


def decode_auth_status(message: bytes) -> tuple[bool, bool] | None:
    """Parse ``05 | authenticated | bonded`` into ``(authenticated, bonded)``."""
    if len(message) < 3 or message[0] != OP_AUTH_STATUS:
        return None
    return message[1] == 1, message[2] == 1


def decode_time(message: bytes) -> tuple[int, int] | None:
    """Parse a ``25`` reply into ``(current_s, session_start_s)``."""
    if len(message) < 10 or message[0] != OP_TIME_REPLY or not crc_ok(message):
        return None
    _, current, start = struct.unpack_from("<BII", message, 1)
    return current, start


def decode_sensor(message: bytes) -> tuple[int, int, int] | None:
    """Parse a ``2F`` reply into ``(ts_s, unfiltered, filtered)``."""
    if len(message) < 14 or message[0] != OP_SENSOR_REPLY:
        return None
    _, ts, unfiltered, filtered = struct.unpack_from("<BIII", message, 1)
    return ts, unfiltered, filtered
