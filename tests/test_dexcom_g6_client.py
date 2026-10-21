"""The G6 client (services/dexcom_g6_client.py) against the in-process reference transmitter:
the whole conversation without a radio."""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from api import ble_uuids
from api import dexcom_g6 as g6
from api import dexcom_g6_transmitter as tx
from services.dexcom_g6_client import (
    G6AuthRefused,
    G6Client,
    G6Error,
    G6TransmitterNotVerified,
)

BOARD_ID = "8SIM00"


class FakeGatt:
    """The shape of a connected BleakClient, backed by the reference transmitter."""

    def __init__(self, tx_id: str = BOARD_ID, reading: g6.Reading | None = None) -> None:
        self.tx_id = tx_id
        self.reading = reading
        self.session = tx.Session()
        self.callbacks: dict[str, object] = {}
        self.writes: list[tuple[str, bytes]] = []
        self.silent = False  # swallow control requests: the transmitter never answers
        self.random = bytes(range(8))

    async def start_notify(self, uuid, callback):
        self.callbacks[uuid] = callback

    async def read_gatt_char(self, uuid):
        return b""

    async def write_gatt_char(self, uuid, data, response=True):
        self.writes.append((uuid, bytes(data)))
        if uuid == ble_uuids.DEXCOM_AUTH_UUID:
            out = tx.handle_auth(self.session, self.tx_id, bytes(data), self.random)
            if out.reply:
                self.callbacks[ble_uuids.DEXCOM_AUTH_UUID](None, bytearray(out.reply))
        elif uuid == ble_uuids.DEXCOM_CONTROL_UUID:
            out = tx.handle_control(self.session, self.reading, 4242, bytes(data))
            if out.result is tx.Result.NOT_AUTHENTICATED:
                raise OSError("ATT error 8: insufficient authorization")
            if out.reply and not self.silent:
                self.callbacks[ble_uuids.DEXCOM_CONTROL_UUID](None, bytearray(out.reply))


def run(coro):
    return asyncio.run(coro)


async def _open(gatt: FakeGatt, client_id: str = BOARD_ID, timeout: float = 1.0) -> G6Client:
    client = G6Client(gatt, client_id, timeout_s=timeout)
    await client.open()
    return client


def test_a_client_with_the_right_id_authenticates_and_reads_the_glucose():
    async def go():
        gatt = FakeGatt(reading=g6.Reading(seq=3, ts_s=900, glucose=137, trend=-4))
        client = await _open(gatt)
        await client.authenticate()
        reading = await client.read_glucose()
        assert (reading.seq, reading.ts_s, reading.glucose, reading.trend) == (3, 900, 137, -4)
        assert client.bonded  # the board always claims to be bonded already
        assert await client.read_time() == (4242, 0)

    run(go())


def test_the_subscriptions_are_made_in_the_order_xdrip_makes_them():
    async def go():
        gatt = FakeGatt()
        await _open(gatt)
        assert list(gatt.callbacks) == [
            ble_uuids.DEXCOM_EXTRA_UUID,
            ble_uuids.DEXCOM_AUTH_UUID,
            ble_uuids.DEXCOM_CONTROL_UUID,
        ]

    run(go())


def test_a_client_with_the_wrong_id_cannot_authenticate():
    """It cannot verify the transmitter's proof either: the two sides hold different keys."""

    async def go():
        client = await _open(FakeGatt(tx_id=BOARD_ID), client_id="8WRONG")
        with pytest.raises(G6TransmitterNotVerified):
            await client.authenticate()

    run(go())


def test_a_transmitter_that_refuses_the_reply_is_reported():
    async def go():
        gatt = FakeGatt()
        client = await _open(gatt)
        real = gatt.write_gatt_char

        async def tamper(uuid, data, response=True):
            if uuid == ble_uuids.DEXCOM_AUTH_UUID and data[0] == g6.OP_AUTH_CHALLENGE_REPLY:
                data = g6.auth_challenge_reply(bytes(8))  # a wrong answer
            await real(uuid, data, response)

        gatt.write_gatt_char = tamper  # type: ignore[method-assign]
        with pytest.raises(G6AuthRefused):
            await client.authenticate()

    run(go())


def test_control_before_authentication_is_refused_by_the_transmitter():
    async def go():
        client = await _open(FakeGatt(reading=g6.Reading(0, 0, 100)))
        with pytest.raises(OSError, match="ATT error 8"):
            await client.read_glucose()

    run(go())


def test_a_silent_transmitter_times_out_with_a_clear_error():
    async def go():
        gatt = FakeGatt(reading=g6.Reading(0, 0, 100))
        client = await _open(gatt, timeout=0.05)
        await client.authenticate()
        gatt.silent = True
        with pytest.raises(G6Error, match="no reply on Control"):
            await client.read_glucose()

    run(go())


def test_a_stale_reply_is_not_mistaken_for_the_next_one():
    async def go():
        gatt = FakeGatt(reading=g6.Reading(seq=1, ts_s=300, glucose=111))
        client = await _open(gatt)
        await client.authenticate()
        gatt.callbacks[ble_uuids.DEXCOM_CONTROL_UUID](  # a late indication from before
            None, bytearray(g6.encode_eglucose_reply(g6.Reading(0, 0, 90)))
        )
        assert (await client.read_glucose()).glucose == 111

    run(go())
