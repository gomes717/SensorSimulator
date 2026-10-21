"""Hardware check of the Dexcom G6 board mode (tickets 04-06 of .scratch/dexcom-g6-transmitter).

Run on the real board (flashed with the board-mode firmware, COM10 free, nothing else connected)::

    uv run python scripts/hw_dexcom_mode.py            # about 12 minutes: two readings, the window
    uv run python scripts/hw_dexcom_mode.py --quick    # skips the waits for the second reading

What it verifies, in order: the board starts in Multi mode (3 sensors, no pairing); a BLE write
switches it to Dexcom mode and the advertised identities change (``Dexcom00`` + "Nordic Glucose
Sensor 1", no Sensor 2/3); a G6 client authenticates with the transmitter ID and the board proves it
knows the ID too; a wrong ID is refused and unauthenticated control writes fail; the held reading
is the one the board logged; the CGMS stream to the app carries the same reading; the G6 identity
is connectable in a window after each reading and not outside it; a second reading arrives five
minutes later; and a switch back to Multi mode restores the three sensors. It leaves the board in
Multi mode.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import re
import sys
import time
from datetime import datetime

from bleak import BleakScanner
from hw_common import (
    ARTIFACTS,
    SerialCapture,
    config_uuid,
    connect,
    pick_config_device,
    refresh_gatt_cache,
    retry_ble,
    scan_sensors,
    write,
)

from api import dexcom_g6 as g6
from services import ble_session
from services.dexcom_g6_client import G6AuthRefused, G6Client, G6TransmitterNotVerified

COMM_PROFILE = config_uuid(0x14)
DEXCOM_ID = config_uuid(0x18)
TX_ID = "8SIM00"
G6_NAME = g6.advertised_name(TX_ID)
APP_NAME = "Nordic Glucose Sensor 1"
WINDOW_S = 30  # CONFIG_APP_G6_WINDOW_S

results: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)


async def scan(seconds: float = 8.0) -> dict[str, object]:
    """Every advertising device the radio sees: ``{advertised name: device}``."""
    found = await BleakScanner.discover(timeout=seconds, return_adv=True)
    return {(adv.local_name or dev.name or ""): dev for dev, adv in found.values()}


def board_readings(console: str) -> list[tuple[int, int, int]]:
    """The G6 readings the board logged: ``[(seq, ts_s, glucose)]``."""
    return [
        (int(s), int(t), int(g))
        for s, t, g in re.findall(r"dexcom reading seq=(\d+) ts=(\d+)s glucose=(\d+)", console)
    ]


async def read_comm_profile(client) -> int:
    return (await retry_ble(lambda: client.read_gatt_char(COMM_PROFILE)))[0]


async def switch_mode(device, dexcom: bool) -> None:
    """Connect to *device*, write the board mode, and let the board restart."""
    client = await connect(device)
    assert client is not None, "could not connect to write the board mode"
    try:
        await write(client, COMM_PROFILE, bytes([1 if dexcom else 0]))
    finally:
        await asyncio.sleep(1.0)
        with contextlib.suppress(Exception):  # the board may have dropped the link
            await client.disconnect()
    await asyncio.sleep(5.0)  # the board restarts its advertising


async def g6_session(device, tx_id: str = TX_ID):
    """A connected, subscribed G6 client (the caller disconnects), or None."""
    gatt = await connect(device)
    if gatt is None:
        return None, None
    client = G6Client(gatt, tx_id, timeout_s=10.0)
    await client.open()
    return gatt, client


async def main(quick: bool) -> int:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log = ARTIFACTS / f"dexcom-mode-{stamp}.log"
    print(f"console log: {log}", flush=True)
    with SerialCapture(log) as serial:
        await asyncio.sleep(3.0)

        # -- 1. Multi mode at the start --------------------------------------------------------
        named = await scan_sensors()
        names = [n for n, _ in named]
        check("starts in Multi mode: three Nordic sensors advertise", len(named) == 3, str(names))
        config_dev = pick_config_device(named)
        await refresh_gatt_cache(config_dev)  # the new characteristic is invisible to a cached GATT
        client = await connect(config_dev)
        assert client is not None, "no connection to the board"
        mode = await read_comm_profile(client)
        tx_id = bytes(await retry_ble(lambda: client.read_gatt_char(DEXCOM_ID))).decode()
        check("board mode reads 0 (Multi)", mode == 0, f"mode={mode}")
        check("the transmitter ID reads back", g6.valid_transmitter_id(tx_id), tx_id)
        await client.disconnect()
        await asyncio.sleep(2.0)

        # -- 2. switch to Dexcom ---------------------------------------------------------------
        await switch_mode(config_dev, dexcom=True)
        t_switch = time.monotonic()
        seen = await scan(10.0)
        check(f"{G6_NAME} (the G6 transmitter) advertises", G6_NAME in seen, str(sorted(seen)))
        check(f"{APP_NAME} (for the app) advertises", APP_NAME in seen)
        extra = [n for n in seen if n.startswith("Nordic Glucose Sensor ") and n != APP_NAME]
        check("Sensors 2 and 3 are silent", not extra, str(extra))
        console = serial.text()
        check("the board logged the switch", "board mode -> dexcom" in console)

        # -- 3. G6 conversation ----------------------------------------------------------------
        g6_dev = seen.get(G6_NAME)
        assert g6_dev is not None, "the G6 transmitter is not advertising; cannot continue"
        gatt, g6c = await g6_session(g6_dev)
        assert gatt is not None and g6c is not None, "could not connect to the G6 transmitter"
        try:
            await g6c.authenticate()
            check("G6 authentication succeeds with the transmitter ID", True)
            check("the board proves it knows the ID (xDrip checks this)", True)
            reading = await g6c.read_glucose()
            check(
                "a reading is held: 40-400 mg/dL, state OK",
                40 <= reading.glucose <= 400 and reading.state == g6.STATE_OK,
                str(reading),
            )
            logged = board_readings(serial.text())
            check(
                "the G6 reading is the one the board logged",
                any(
                    (s, t, gl) == (reading.seq, reading.ts_s, reading.glucose)
                    for s, t, gl in logged
                ),
                f"G6={reading.seq}/{reading.ts_s}/{reading.glucose} board={logged[-2:]}",
            )
            clock, start = await g6c.read_time()
            check("the transmitter clock runs", clock >= 0 and start == 0, f"{clock}s")
            ver = await g6c.request(g6.simple_request(g6.OP_VERSION))
            check("version, battery and session requests are answered", len(ver) == 18)
            bat = await g6c.request(g6.simple_request(g6.OP_BATTERY))
            check("battery reply is 10 bytes with a valid CRC", len(bat) == 10 and g6.crc_ok(bat))
        finally:
            await gatt.disconnect()
        await asyncio.sleep(2.0)

        # -- 4. an impostor ----------------------------------------------------------------------
        gatt, impostor = await g6_session(g6_dev, tx_id="8WRONG")
        if gatt is not None and impostor is not None:
            try:
                await impostor.authenticate()
                check("a wrong ID cannot authenticate", False, "it did")
            except G6TransmitterNotVerified:
                check("a wrong ID cannot authenticate (the board's proof does not verify)", True)
            except G6AuthRefused:
                check("a wrong ID cannot authenticate (refused)", True)
            finally:
                await gatt.disconnect()
        else:
            check("a wrong ID cannot authenticate", False, "no connection")
        await asyncio.sleep(2.0)

        gatt, bare = await g6_session(g6_dev)
        if gatt is not None and bare is not None:
            try:
                await bare.read_glucose()
                check("control before authentication is refused", False, "it answered")
            except Exception as exc:
                check("control before authentication is refused", True, type(exc).__name__)
            finally:
                await gatt.disconnect()
        await asyncio.sleep(2.0)

        # -- 5. the app's view (identity 1) ------------------------------------------------------
        app_dev = seen[APP_NAME]
        app = await connect(app_dev)
        assert app is not None, "could not connect the app link to Nordic Glucose Sensor 1"
        got: list[dict] = []
        measurement = sorted(
            (c for s in app.services for c in s.characteristics
             if c.uuid.lower() == ble_session.CGM_MEASUREMENT_UUID),
            key=lambda c: c.handle,
        )  # fmt: skip
        await app.start_notify(
            measurement[0], lambda _c, d: got.append(ble_session._decode_cgm_measurement(d))
        )
        check("the app link reads the board mode as Dexcom", await read_comm_profile(app) == 1)

        # -- 6. the 5-minute rule ----------------------------------------------------------------
        if not quick:
            # window: the board logged reading 0 at about t_switch; it closes WINDOW_S later
            await asyncio.sleep(max(0.0, 75.0 - (time.monotonic() - t_switch)))
            closed = await scan(8.0)
            check(
                f"{G6_NAME} is not connectable outside the window (75 s after the reading)",
                G6_NAME not in closed,
                str(sorted(closed)),
            )
            print("waiting for the second reading (5 minutes)...", flush=True)
            deadline = time.monotonic() + 330
            while time.monotonic() < deadline and len(board_readings(serial.text())) < 2:
                await asyncio.sleep(5.0)
            logged = board_readings(serial.text())
            check("a second reading is published five minutes later", len(logged) >= 2, str(logged))
            if len(logged) >= 2:
                check(
                    "seq +1 and ts +300 s between readings",
                    logged[1][0] == logged[0][0] + 1 and logged[1][1] - logged[0][1] == 300,
                    str(logged[:2]),
                )
                await asyncio.sleep(3.0)
                check(
                    "the app received the same reading over CGMS",
                    any(abs(m["glucose_value"] - logged[1][2]) < 0.6 for m in got),
                    f"app={[m['glucose_value'] for m in got]} board={logged[1][2]}",
                )
                opened = await scan(8.0)
                check(f"{G6_NAME} is connectable again after the reading", G6_NAME in opened)
                if G6_NAME in opened:
                    gatt, g6c = await g6_session(opened[G6_NAME])
                    if gatt is not None and g6c is not None:
                        try:
                            await g6c.authenticate()
                            again = await g6c.read_glucose()
                            check(
                                "the second G6 read is the new reading",
                                again.seq == logged[1][0] and again.glucose == logged[1][2],
                                str(again),
                            )
                        finally:
                            await gatt.disconnect()
        await app.disconnect()
        await asyncio.sleep(2.0)

        # -- 7. back to Multi ----------------------------------------------------------------------
        app_again = await connect(app_dev)
        if app_again is not None:
            await write(app_again, COMM_PROFILE, bytes([0]))
            await asyncio.sleep(1.0)
            with contextlib.suppress(Exception):  # the board may have dropped the link
                await app_again.disconnect()
        await asyncio.sleep(6.0)
        back = await scan_sensors()
        check(
            "back in Multi: three Nordic sensors advertise",
            len(back) == 3,
            str([n for n, _ in back]),
        )
        check("the G6 transmitter no longer advertises", G6_NAME not in await scan(5.0))

        console = serial.text()
        bad = [
            line for line in console.splitlines()
            if re.search(r"unknown op|ASSERT|FATAL|Fault|bad crc", line)
        ]  # fmt: skip
        check("no unknown opcodes, faults or asserts on the console", not bad, "; ".join(bad[:3]))

    failed = [name for name, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed", flush=True)
    for name in failed:
        print("  FAILED:", name)
    return 1 if failed else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--quick", action="store_true", help="skip the five-minute waits")
    sys.exit(asyncio.run(main(parser.parse_args().quick)))
