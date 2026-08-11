"""Background thread for scanning nearby BLE devices via bleak."""
from __future__ import annotations

import asyncio

from PyQt6.QtCore import QThread, pyqtSignal

from bleak import BleakScanner


class BluetoothScanThread(QThread):
    """Worker thread that performs a single BLE scan and reports discovered devices.

    BLE scanning is async I/O under the hood (bleak), so it runs inside its
    own event loop on a background thread to avoid blocking the Qt UI thread.
    """

    device_found = pyqtSignal(str, str, int)  # name, address, rssi
    scan_failed = pyqtSignal(str)

    SCAN_DURATION = 5.0

    def run(self) -> None:
        """Scan for nearby BLE devices for SCAN_DURATION seconds and emit each one found."""
        try:
            devices = asyncio.run(
                BleakScanner.discover(timeout=self.SCAN_DURATION, return_adv=True)
            )
        except Exception as exc:  # pylint: disable=broad-except
            self.scan_failed.emit(str(exc))
            return
        for device, adv in devices.values():
            name = device.name or adv.local_name or "Unknown device"
            self.device_found.emit(name, device.address, adv.rssi)
