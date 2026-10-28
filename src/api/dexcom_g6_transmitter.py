"""A reference Dexcom G6 transmitter: the protocol state machine, with no radio.

This is the Python twin of ``firmware/peripheral_cgms/src/dexcom_g6.c``. The firmware's GATT layer
only moves bytes in and out of the C version; here the same decisions are made on plain ``bytes``,
so the app's G6 client, the overnight harness and the tests can run against a transmitter without
a board. The two are held to the same golden vectors (``tests/data/dexcom_g6_vectors.json``).

Also here: :class:`ReadingGrid`, the 5-minute rule — one reading per 300 simulated seconds,
held until the next.
"""

from __future__ import annotations

import enum
import struct
from dataclasses import dataclass

from api import dexcom_g6 as g6

BONDED = 1  # the transmitter always reports "already bonded": no SMP, no pairing

# Fixed answers for what the simulator does not model (see spec.md).
_FIRMWARE = bytes([1, 6, 5, 25])  # "1.6.5.25", a G6 transmitter firmware xDrip knows
_BT_FIRMWARE = bytes([1, 0, 0, 0])
_OTHER_FIRMWARE = bytes([0, 0, 0])
_BATTERY_A, _BATTERY_B, _TEMPERATURE_C = 3000, 2900, 25
_MAX_RUNTIME_DAYS = 112
_TYPICAL_SENSOR_DAYS = 10
_WARMUP_S = 7200


class Result(enum.Enum):
    """What the GATT layer should do with a control request."""

    REPLY = "reply"  # indicate ``reply`` on Control
    NOT_AUTHENTICATED = "not authenticated"  # fail the write with ATT error 8
    BAD_CRC = "bad crc"  # drop it
    UNKNOWN = "unknown"  # log it, drop it
    DISCONNECT = "disconnect"  # the client asked us to hang up
    ACK = "ack"  # accepted, nothing to send


@dataclass
class Outcome:
    """The result of handling one write: what happened, and the reply bytes if there are any."""

    result: Result
    reply: bytes = b""


@dataclass
class Session:
    """Per-connection authentication state."""

    authenticated: bool = False
    challenge: bytes | None = None


@dataclass
class AuthOutcome:
    """The result of one write to the Authentication characteristic."""

    known: bool
    reply: bytes = b""


def handle_auth(session: Session, tx_id: str, data: bytes, random8: bytes) -> AuthOutcome:
    """Handle a write to ``…3535``; *random8* is the challenge to issue if one is asked for."""
    if not data:
        return AuthOutcome(False)
    op = data[0]
    if op == g6.OP_AUTH_REQUEST and len(data) == 10:
        session.authenticated = False
        session.challenge = random8
        proof = g6.challenge_hash(data[1:9], tx_id)
        return AuthOutcome(True, bytes([g6.OP_AUTH_CHALLENGE]) + proof + random8)
    if op == g6.OP_AUTH_CHALLENGE_REPLY and len(data) == 9:
        if session.challenge is not None:
            ok = data[1:9] == g6.challenge_hash(session.challenge, tx_id)
        else:
            # xDrip sometimes writes the reply twice: with the challenge already used, a repeat
            # on a link that did authenticate leaves the session as it is.
            ok = session.authenticated
        session.authenticated = ok
        session.challenge = None
        return AuthOutcome(True, bytes([g6.OP_AUTH_STATUS, 1 if ok else 0, BONDED]))
    if op in (g6.OP_KEEP_ALIVE, g6.OP_BOND_REQUEST):
        return AuthOutcome(True)  # we claim to be bonded already; nothing to send
    return AuthOutcome(False)


def _no_reading() -> g6.Reading:
    """What a transmitter with nothing to report says: no glucose, state "stopped"."""
    return g6.Reading(seq=0, ts_s=0, glucose=0, state=0x01, trend=g6.TREND_INVALID)


def _version() -> bytes:
    body = bytes([g6.OP_VERSION_REPLY, 0]) + _FIRMWARE + _BT_FIRMWARE + bytes([1]) + _OTHER_FIRMWARE
    return g6.with_crc(body + struct.pack("<H", 0))


def _version1() -> bytes:
    body = (
        bytes([g6.OP_VERSION1_REPLY, 0])
        + _FIRMWARE
        + struct.pack("<IHBHH", 0, 0, 0, _MAX_RUNTIME_DAYS, 0)
    )
    return g6.with_crc(body)


def _version2() -> bytes:
    return g6.with_crc(
        struct.pack("<BBBHH", g6.OP_VERSION2_REPLY, 0, _TYPICAL_SENSOR_DAYS, 0, _WARMUP_S)
    )


def _battery() -> bytes:
    return g6.with_crc(
        struct.pack("<BBHHBb", g6.OP_BATTERY_REPLY, 0, _BATTERY_A, _BATTERY_B, 0, _TEMPERATURE_C)
    )


def _session_start(request: bytes, now_s: int) -> bytes:
    requested = struct.unpack_from("<I", request, 5)[0] if len(request) >= 9 else 0
    return g6.with_crc(
        struct.pack("<BBBIII", g6.OP_SESSION_START_REPLY, 0, 0x01, requested, 0, now_s)
    )


def _session_stop(now_s: int) -> bytes:
    return g6.with_crc(struct.pack("<BBBIII", g6.OP_SESSION_STOP_REPLY, 0, 0x01, now_s, 0, now_s))


def _calibrate() -> bytes:
    return g6.with_crc(bytes([g6.OP_CALIBRATE_REPLY, 0, 0x00]))


def handle_control(session: Session, latest: g6.Reading | None, now_s: int, data: bytes) -> Outcome:
    """Handle a write to Control (``…3534``): *latest* is the held reading, *now_s* the tx clock."""
    if not data:
        return Outcome(Result.UNKNOWN)
    if not session.authenticated:
        return Outcome(Result.NOT_AUTHENTICATED)
    op = data[0]
    if op == g6.OP_DISCONNECT:
        return Outcome(Result.DISCONNECT)
    if op == g6.OP_UNBOND:
        return Outcome(Result.ACK)
    if len(data) >= 3 and not g6.crc_ok(data):
        return Outcome(Result.BAD_CRC)
    reading = latest if latest is not None else _no_reading()
    replies = {
        g6.OP_EGLUCOSE: lambda: g6.encode_eglucose_reply(reading),
        g6.OP_GLUCOSE: lambda: g6.encode_glucose_reply(reading),
        g6.OP_SENSOR: lambda: g6.encode_sensor_reply(reading),
        g6.OP_TIME: lambda: g6.encode_time_reply(now_s, 0),
        g6.OP_VERSION: _version,
        g6.OP_VERSION1: _version1,
        g6.OP_VERSION2: _version2,
        g6.OP_BATTERY: _battery,
        g6.OP_SESSION_START: lambda: _session_start(data, now_s),
        g6.OP_SESSION_STOP: lambda: _session_stop(now_s),
        g6.OP_CALIBRATE: _calibrate,
    }
    make = replies.get(op)
    if make is None:
        return Outcome(Result.UNKNOWN)
    return Outcome(Result.REPLY, make())


class ReadingGrid:
    """The 5-minute rule: one reading per :data:`GRID_PERIOD_S` of simulated time, held between.

    Feed it every model tick; it returns a new :class:`~api.dexcom_g6.Reading` when a grid boundary
    has been crossed (the very first tick publishes at once so a client can read immediately) and
    ``None`` otherwise. ``ts`` is the boundary time; ``seq`` counts publications. A restart of the
    simulated clock (it goes backwards) starts the grid over.
    """

    def __init__(self, period_s: int = g6.GRID_PERIOD_S) -> None:
        self._period = period_s
        self._k: int | None = None  # grid index of the latest publication
        self._seq = 0
        self.latest: g6.Reading | None = None

    def reset(self) -> None:
        """Forget everything: the next tick publishes seq 0 again."""
        self._k = None
        self._seq = 0
        self.latest = None

    def feed(
        self, sim_s: float, glucose_mgdl: float, trend_mgdl_min: float | None = None,
        state: int = g6.STATE_OK,
    ) -> g6.Reading | None:  # fmt: skip
        """Offer the model's value at simulated time *sim_s*; returns a reading if one is due."""
        k = int(sim_s // self._period)
        if self._k is not None and k < self._k:
            self.reset()
        if self._k is not None and k == self._k:
            return None
        self._k = k
        reading = g6.Reading(
            seq=self._seq,
            ts_s=k * self._period,
            glucose=g6.clamp_glucose(glucose_mgdl),
            state=state,
            trend=g6.clamp_trend(trend_mgdl_min),
        )
        self._seq += 1
        self.latest = reading
        return reading
