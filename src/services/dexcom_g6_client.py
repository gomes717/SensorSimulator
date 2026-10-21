"""A Dexcom G6 client: the sequence xDrip runs against a transmitter, over any GATT connection.

It subscribes to ExtraData and indicates on Authentication and Control, authenticates with the
transmitter ID (the board must prove it knows the ID too: xDrip checks that), and reads glucose.
Used by the hardware checks and the overnight test (``scripts/hw_dexcom_mode.py``,
``scripts/overnight_dexcom.py``); the protocol itself is ``api/dexcom_g6.py``.

*gatt* is anything shaped like a connected ``bleak.BleakClient``: ``start_notify(uuid, callback)``,
``write_gatt_char(uuid, data, response=...)``, ``read_gatt_char(uuid)``. The tests drive it with an
in-process transmitter (``api/dexcom_g6_transmitter.py``), so no radio is needed.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from api import ble_uuids
from api import dexcom_g6 as g6


class G6Error(Exception):
    """A protocol failure: a bad reply, a failed verification, a refused authentication."""


class G6AuthRefused(G6Error):
    """The transmitter answered the challenge with "not authenticated"."""


class G6TransmitterNotVerified(G6Error):
    """The transmitter did not prove it knows the ID (its hash of our token is wrong)."""


class G6Client:
    """One connection's worth of the G6 conversation."""

    def __init__(self, gatt: Any, tx_id: str, timeout_s: float = 10.0) -> None:
        self._gatt = gatt
        self._tx_id = tx_id
        self._timeout = timeout_s
        self._auth: asyncio.Queue[bytes] = asyncio.Queue()
        self._control: asyncio.Queue[bytes] = asyncio.Queue()
        self.bonded = False

    async def open(self) -> None:
        """Subscribe the way xDrip does: ExtraData first, then Authentication, then Control."""
        await self._gatt.start_notify(ble_uuids.DEXCOM_EXTRA_UUID, lambda _c, _d: None)
        await self._gatt.start_notify(
            ble_uuids.DEXCOM_AUTH_UUID, lambda _c, data: self._auth.put_nowait(bytes(data))
        )
        await self._gatt.start_notify(
            ble_uuids.DEXCOM_CONTROL_UUID, lambda _c, data: self._control.put_nowait(bytes(data))
        )

    async def _next(self, queue: asyncio.Queue[bytes], what: str) -> bytes:
        try:
            return await asyncio.wait_for(queue.get(), self._timeout)
        except TimeoutError:
            raise G6Error(f"no {what} from the transmitter within {self._timeout:g} s") from None

    async def authenticate(self, alt: bool = False) -> None:
        """The challenge: prove we know the ID, check the transmitter knows it too."""
        token = os.urandom(g6.TOKEN_LEN)
        await self._gatt.write_gatt_char(
            ble_uuids.DEXCOM_AUTH_UUID, g6.auth_request(token, alt), response=True
        )
        challenge = g6.decode_auth_challenge(await self._next(self._auth, "auth challenge"))
        if challenge is None:
            raise G6Error("the transmitter did not answer the auth request with a challenge")
        if challenge.token_hash != g6.challenge_hash(token, self._tx_id):
            raise G6TransmitterNotVerified("the transmitter's token hash is wrong")
        await self._gatt.write_gatt_char(
            ble_uuids.DEXCOM_AUTH_UUID,
            g6.auth_challenge_reply(g6.challenge_hash(challenge.challenge, self._tx_id)),
            response=True,
        )
        status = g6.decode_auth_status(await self._next(self._auth, "auth status"))
        if status is None:
            raise G6Error("the transmitter did not answer the challenge with a status")
        authenticated, self.bonded = status
        if not authenticated:
            raise G6AuthRefused("the transmitter refused the challenge reply (wrong ID?)")

    async def request(self, message: bytes) -> bytes:
        """Write *message* to Control and return the indicated reply."""
        while not self._control.empty():
            self._control.get_nowait()  # an old reply must not be taken for this one
        await self._gatt.write_gatt_char(ble_uuids.DEXCOM_CONTROL_UUID, message, response=True)
        return await self._next(self._control, "reply on Control")

    async def read_glucose(self) -> g6.Reading:
        """Ask for the reading the way a G6 client does (EGlucoseTx) and decode it."""
        reply = await self.request(g6.eglucose_request())
        reading = g6.decode_glucose(reply)
        if reading is None:
            raise G6Error(f"unreadable glucose reply: {reply.hex()}")
        return reading

    async def read_time(self) -> tuple[int, int]:
        """The transmitter clock: ``(current_s, session_start_s)``."""
        reply = await self.request(g6.time_request())
        parsed = g6.decode_time(reply)
        if parsed is None:
            raise G6Error(f"unreadable time reply: {reply.hex()}")
        return parsed
