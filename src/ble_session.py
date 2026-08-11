"""Persistent BLE connection that records every notification received from a device."""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone

from PyQt6.QtCore import QThread, pyqtSignal

from bleak import BleakClient

# Standard Bluetooth SIG "Continuous Glucose Monitoring" service characteristics
# (used e.g. by Nordic's peripheral_cgms sample). Recognised specially so the
# glucose value can be decoded and reporting sped up instead of just logging hex.
CGM_MEASUREMENT_UUID = "00002aa7-0000-1000-8000-00805f9b34fb"
CGM_SOCP_UUID = "00002aac-0000-1000-8000-00805f9b34fb"
SOCP_WRITE_CGM_COMMUNICATION_INTERVAL = 0x01
# This project's peripheral_cgms firmware has been patched to interpret the
# communication interval in seconds instead of the spec's whole minutes (see
# cgms.c), so this value is seconds, not minutes, and already matches the
# firmware's own 5-second default — the write below just guarantees it.
FAST_COMM_INTERVAL_SECONDS = 5

# Matches the fixed test passkey configured in the peripheral_cgms sample's
# firmware (prj.conf CONFIG_BT_APP_PASSKEY + main.c auth_app_passkey()), so
# pairing can be completed automatically instead of via Windows' pairing UI.
CGM_TEST_PASSKEY = "123456"


def _sfloat_to_float(raw: int) -> float:
    """Decode an IEEE-11073 16-bit SFLOAT, the format CGMS uses for glucose concentration."""
    mantissa = raw & 0x0FFF
    if mantissa >= 0x0800:
        mantissa -= 0x1000
    exponent = raw >> 12
    if exponent >= 0x8:
        exponent -= 0x10
    return mantissa * (10 ** exponent)


def _decode_cgm_measurement(data: bytes) -> dict:
    """Decode a CGM Measurement record's glucose concentration (mg/dL) and time offset."""
    if len(data) < 6:
        return {}
    flags = data[1]
    glucose_raw = int.from_bytes(data[2:4], "little")
    time_offset = int.from_bytes(data[4:6], "little")
    return {
        "glucose_value": round(_sfloat_to_float(glucose_raw), 1),
        "time_offset_min": time_offset,
        "flags": flags,
    }


class BleSession(QThread):
    """Keeps a GATT connection open and turns incoming notifications into messages.

    Runs its own asyncio event loop on a background thread so the connection
    can stay alive and keep receiving notifications for as long as needed,
    without blocking the Qt UI thread. Subscribes to every notify/indicate
    characteristic the device exposes, since the concrete GATT profile of
    the connected sensor is not known ahead of time. If the device exposes
    the standard CGM Service, its measurement is decoded to a glucose value
    and its reporting interval is (re)confirmed at 5 seconds, matching this
    project's patched firmware (see FAST_COMM_INTERVAL_SECONDS above).
    """

    connected = pyqtSignal(str, int, int, str)  # address, subscribed, notify-capable, last error
    connect_failed = pyqtSignal(str, str)  # address, error message
    disconnected = pyqtSignal(str)
    new_message = pyqtSignal(dict)

    def __init__(self, address: str, name: str, parent=None) -> None:
        """Store the target device's address and display name for this session."""
        super().__init__(parent)
        self._address = address
        self._name = name

    def stop(self) -> None:
        """Request the session to close the connection and end its run loop."""
        self.requestInterruption()

    def run(self) -> None:
        """Connect, subscribe to notifications, and idle until interruption is requested."""
        try:
            asyncio.run(self._session())
        except Exception as exc:  # pylint: disable=broad-except
            self.connect_failed.emit(self._address, str(exc))

    async def _session(self) -> None:
        """Pair (Windows only, best-effort), open the connection, subscribe, then wait."""
        if sys.platform == "win32":
            try:
                from windows_ble_pairing import pair_with_pin  # local import: Windows-only dep

                await pair_with_pin(self._address, CGM_TEST_PASSKEY)
            except Exception:  # pylint: disable=broad-except
                pass  # not every device needs pairing; fall through to a normal connect

        async with BleakClient(self._address) as client:
            if not client.is_connected:
                raise ConnectionError("Device did not accept the connection")

            notify_count = 0
            subscribed_count = 0
            last_error = ""
            socp_characteristic = None
            for service in client.services:
                for characteristic in service.characteristics:
                    uuid = characteristic.uuid.lower()
                    if "notify" in characteristic.properties or "indicate" in characteristic.properties:
                        notify_count += 1
                        try:
                            await client.start_notify(characteristic, self._handle_notification)
                            subscribed_count += 1
                        except Exception as exc:  # pylint: disable=broad-except
                            last_error = str(exc)
                            continue
                    if uuid == CGM_SOCP_UUID and "write" in characteristic.properties:
                        socp_characteristic = characteristic

            if socp_characteristic is not None:
                try:
                    await client.write_gatt_char(
                        socp_characteristic,
                        bytes([SOCP_WRITE_CGM_COMMUNICATION_INTERVAL, FAST_COMM_INTERVAL_SECONDS]),
                    )
                except Exception:  # pylint: disable=broad-except
                    pass

            self.connected.emit(self._address, subscribed_count, notify_count, last_error)

            while not self.isInterruptionRequested():
                await asyncio.sleep(0.2)

        self.disconnected.emit(self._address)

    def _handle_notification(self, characteristic, data: bytearray) -> None:
        """Turn a raw GATT notification into a message dict and emit it."""
        message = {
            "user_id": self._name or self._address,
            "dev_id": self._address,
            "characteristic": characteristic.uuid,
            "raw_hex": data.hex(),
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        if characteristic.uuid.lower() == CGM_MEASUREMENT_UUID:
            message.update(_decode_cgm_measurement(bytes(data)))
        self.new_message.emit(message)
