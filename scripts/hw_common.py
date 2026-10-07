"""Shared pieces of the small on-hardware checks (``hw_user_name.py``, ``hw_csv_loop.py``).

These are not the big e2e matrices (``e2e.py``, ``e2e_4sensor.py``): each check here pokes
one behaviour of the board directly over BLE and reads the firmware console to see what the
board did. They need the board connected, COM10 free, and nothing else holding its links.

Three things bite on this machine and are handled once here:

* Windows' BLE stack drops a fresh link or cancels a GATT operation now and then
  (``OSError`` / ``BleakError("Not connected")``) — :func:`retry_ble` and :func:`connect`
  retry them.
* Python cannot open the COM port reliably from Git Bash, so :class:`SerialCapture` runs a
  PowerShell ``SerialPort`` reader that appends the console to a log file.
* ``Sensor select`` clamps an out-of-range slot to ``0`` (the board has three sensors, so
  slot ``3`` becomes slot ``0``), :func:`checked_slot` refuses such a slot up front.
"""

from __future__ import annotations

import asyncio
import contextlib
import subprocess
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from bleak import BleakClient, BleakScanner

from models import board_layout

JLINK = Path(r"C:\Program Files\SEGGER\JLink_V924a\JLink.exe")
ARTIFACTS = ROOT / "test-artifacts" / "hw"
SERIAL_PORT = "COM10"


def config_uuid(n: int) -> str:
    """The 128-bit UUID of the simulator config characteristic ``5b2c00NN``."""
    return f"5b2c{n:04x}-0d6d-4a3a-8c1e-3f9b6e7a1a00"


SENSOR_SELECT = config_uuid(0x15)
USER_NAME = config_uuid(0x16)
DATA_SOURCE = config_uuid(0x11)
SPEED = config_uuid(0x12)
CSV_CONTROL = config_uuid(0x0F)
CSV_DATA = config_uuid(0x10)


def checked_slot(slot: int) -> int:
    """*slot* if the board has it. Without this a write to a missing slot lands on slot 0."""
    if not 0 <= slot < board_layout.MAX_SLOTS:
        raise ValueError(f"slot {slot} does not exist: the board has {board_layout.MAX_SLOTS}")
    return slot


class SerialCapture:
    """Context manager: append the firmware console to *log* until the block ends."""

    def __init__(self, log: Path) -> None:
        self.log = log
        self._proc: subprocess.Popen | None = None

    def __enter__(self) -> SerialCapture:
        self.log.parent.mkdir(parents=True, exist_ok=True)
        script = (
            f"$p=New-Object System.IO.Ports.SerialPort {SERIAL_PORT},115200,None,8,One;"
            "$p.ReadTimeout=1500;try{$p.Open()}catch{exit 2};"
            "while($true){try{$l=$p.ReadLine();if($l){[Console]::Out.WriteLine($l)}}catch{}}"
        )
        self._proc = subprocess.Popen(
            ["powershell", "-NoProfile", "-Command", script],
            stdout=self.log.open("w", encoding="utf-8", errors="replace"),
            stderr=subprocess.DEVNULL,
        )
        return self

    def __exit__(self, *_exc: object) -> None:
        if self._proc is not None:
            self._proc.kill()

    def text(self) -> str:
        """Everything captured so far."""
        return self.log.read_text(encoding="utf-8", errors="replace")


async def retry_ble[T](op: Callable[[], Awaitable[T]], tries: int = 4) -> T:
    """Run *op*, retrying the ``OSError`` Windows raises when it cancels a GATT operation."""
    for attempt in range(tries):
        try:
            return await op()
        except OSError as exc:
            print(f"   (BLE hiccup {type(exc).__name__}, retry {attempt + 1})", flush=True)
            await asyncio.sleep(1.5)
    return await op()


async def scan_sensors(timeout: float = 8.0) -> list[tuple[str, Any]]:
    """``[(advertised name, device)]`` for every "Nordic Glucose Sensor N", sorted by name."""
    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    named = [
        (adv.local_name or dev.name or "", dev)
        for dev, adv in found.values()
        if (adv.local_name or dev.name or "").startswith("Nordic Glucose Sensor")
    ]
    return sorted(named, key=lambda pair: pair[0])


def pick_config_device(named: list[tuple[str, Any]]) -> Any:
    """The device to configure through: identity 1 ("... Sensor 2"), not the flakier
    factory identity 0 — the same choice ``e2e_4sensor.py`` makes."""
    return next((dev for name, dev in named if name.endswith("2")), named[0][1])


async def connect(
    device: Any,
    notify_uuid: str | None = None,
    on_notify: Callable[[Any, bytearray], None] | None = None,
    tries: int = 6,
) -> BleakClient | None:
    """A connected client (optionally subscribed to *notify_uuid*), or None after *tries*."""
    for attempt in range(tries):
        client = BleakClient(device, timeout=20.0)
        try:
            await client.connect()
            await asyncio.sleep(2.0)  # let the link settle before the first GATT operation
            if notify_uuid is not None and on_notify is not None:
                await client.start_notify(notify_uuid, on_notify)
            return client
        except Exception as exc:  # bleak raises several unrelated types here
            print(f"   connect attempt {attempt + 1} failed: {exc!r}", flush=True)
            with contextlib.suppress(Exception):  # already gone — nothing to clean up
                await client.disconnect()
            await asyncio.sleep(4.0)
    return None


async def write(client: BleakClient, char: str, data: bytes, *, response: bool = True) -> None:
    """A GATT write that survives Windows cancelling it."""
    await retry_ble(lambda: client.write_gatt_char(char, data, response=response))


async def select_slot(client: BleakClient, slot: int) -> None:
    """Point the board's Sensor-select cursor at *slot* and give it time to apply."""
    await write(client, SENSOR_SELECT, bytes([checked_slot(slot)]))
    await asyncio.sleep(0.4)


def reset_board(command_file: Path) -> None:
    """Hardware-reset the board through J-Link (``r`` / ``g`` / ``q``)."""
    command_file.parent.mkdir(parents=True, exist_ok=True)
    command_file.write_text("r\ng\nq\n", encoding="utf-8")
    subprocess.run(
        [
            str(JLINK),
            "-device",
            "nRF54L15_M33",
            "-if",
            "SWD",
            "-speed",
            "4000",
            "-autoconnect",
            "1",
            "-CommandFile",
            str(command_file),
        ],
        capture_output=True,
        timeout=60,
        check=False,
    )
