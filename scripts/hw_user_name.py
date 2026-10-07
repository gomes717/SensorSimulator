"""Hardware check for the per-slot user name (characteristic ``5b2c0016``).

Verifies on the real board: a name reads back on its slot, other slots are untouched, 30
bytes fit and 31 are refused, UTF-8 round-trips, names survive a power cycle, an empty write
clears, and writing a name does not reset the simulation clock.

Run (board connected, COM10 free, nothing else connected to the board)::

    uv run python scripts/hw_user_name.py

It leaves slots 1 and 2 with no name. Last run: 12/12 on 2026-10-06 (slice 3 of
``.scratch/users-screen/spec.md``).
"""

from __future__ import annotations

import asyncio
import re
import sys

from hw_common import (
    ARTIFACTS,
    USER_NAME,
    SerialCapture,
    connect,
    pick_config_device,
    reset_board,
    retry_ble,
    scan_sensors,
    select_slot,
    write,
)

SLOTS = (1, 2)  # the slots written; slot 0 is left alone
results: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)


def last_t_sim(console: str) -> float | None:
    found = re.findall(r"t_sim=([0-9.]+)min", console)
    return float(found[-1]) if found else None


async def put(client, slot: int, name: bytes) -> None:
    await select_slot(client, slot)
    await write(client, USER_NAME, name)
    await asyncio.sleep(1.0)  # comm_thread applies the name, then erases + writes flash


async def get(client, slot: int) -> bytes:
    await select_slot(client, slot)
    return bytes(await retry_ble(lambda: client.read_gatt_char(USER_NAME)))


async def before_power_cycle(client, serial: SerialCapture) -> None:
    for slot in SLOTS:
        print(f"   slot {slot} initially: {await get(client, slot)!r}")

    before = last_t_sim(serial.text())
    await put(client, 1, b"Ana")
    check("write 'Ana' reads back on the same slot", await get(client, 1) == b"Ana")
    after = last_t_sim(serial.text())
    check(
        "a name write does not reset the simulation clock",
        before is not None and after is not None and after >= before,
        f"t_sim {before} -> {after}",
    )

    await put(client, 2, b"Bo")
    check("slot 2 holds its own name", await get(client, 2) == b"Bo")
    check("slot 1 is unchanged by the slot 2 write", await get(client, 1) == b"Ana")

    await put(client, 1, b"n" * 30)
    check("a 30-byte name is accepted whole", await get(client, 1) == b"n" * 30)
    refused = False
    try:
        await client.write_gatt_char(USER_NAME, b"n" * 31, response=True)
    except Exception as exc:  # the ATT error surfaces as a bleak protocol error
        refused = True
        print("   31-byte write refused:", type(exc).__name__, exc)
    await asyncio.sleep(0.5)
    check("a 31-byte write is refused by the board", refused)
    check("the refused write left the old name", await get(client, 1) == b"n" * 30)

    utf8 = "é".encode() * 15
    await put(client, 1, utf8)
    check("a UTF-8 name round-trips", await get(client, 1) == utf8)

    await put(client, 1, b"Ana#2")  # known values for after the power cycle
    await put(client, 2, b"Bo")


async def after_power_cycle(client) -> None:
    check("after a power cycle slot 1 name persisted", await get(client, 1) == b"Ana#2")
    check("after a power cycle slot 2 name persisted", await get(client, 2) == b"Bo")
    for slot in SLOTS:
        await put(client, slot, b"")
        check(f"an empty write clears slot {slot}", await get(client, slot) == b"")


async def session(serial: SerialCapture, label: str) -> bool:
    named = await scan_sensors()
    print(f"{label}: found", [name for name, _ in named])
    if not named:
        return False
    client = await connect(pick_config_device(named))
    if client is None:
        return False
    try:
        if label == "before":
            await before_power_cycle(client, serial)
        else:
            await after_power_cycle(client)
    finally:
        await client.disconnect()
    return True


async def main() -> int:
    with SerialCapture(ARTIFACTS / "user_name_serial.log") as serial:
        await asyncio.sleep(3)
        if not await session(serial, "before"):
            print("no sensor found / could not connect")
            return 2
        print("-- resetting the board --", flush=True)
        reset_board(ARTIFACTS / "reset.jlink")
        await asyncio.sleep(8)
        if not await session(serial, "after"):
            print("no sensor found / could not connect after the reset")
            return 2
    failed = [name for name, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
