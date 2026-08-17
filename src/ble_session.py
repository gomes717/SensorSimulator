"""Persistent BLE connection that records every notification received from a device."""
from __future__ import annotations

import asyncio
import re
import sys
from datetime import datetime, timezone

from PyQt6.QtCore import QThread, pyqtSignal

from bleak import BleakClient

# Standard Bluetooth SIG "Continuous Glucose Monitoring" service characteristics
# (used e.g. by Nordic's peripheral_cgms sample). Recognised specially so the
# glucose value can be decoded and reporting sped up instead of just logging hex.
CGM_SERVICE_UUID = "0000181f-0000-1000-8000-00805f9b34fb"
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

    A single physical device can expose several independent CGMS service
    instances over this one connection (e.g. one board simulating several
    sensors, as with this project's peripheral_cgms multi-sensor sample) —
    all sharing the same characteristic UUIDs, distinguishable only by which
    service instance/handle a notification came from. Each of that board's
    BLE identities/addresses is meant to represent one specific simulated
    sensor (its advertised name ends in that sensor's index, e.g. "...
    Sensor 2"), so this session shows only the CGMS instance at that same
    index and ignores notifications from the other instances that are also
    technically visible on this connection — connecting to a given MAC
    should show that one sensor's data, not all of them. If the name has no
    trailing index (some other, unrelated multi-instance device), every
    instance is shown instead, tagged "Sensor N" in user_id so each still
    gets its own row rather than colliding.
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
        # Populated in _session(): maps a CGM Measurement characteristic's
        # handle to which CGMS service instance (0-based) it belongs to. A
        # single physical device can expose several independent CGMS service
        # instances over one connection (e.g. one board simulating several
        # sensors), all sharing the same characteristic UUID, so the handle
        # is the only thing that tells their notifications apart.
        self._instance_by_handle: dict[int, int] = {}
        self._instance_count = 0
        # If the advertised name ends in a number (e.g. "Nordic Glucose
        # Sensor 2"), this identity is meant to represent that one specific
        # simulated sensor, even though every instance is technically
        # visible on any connection to this device (see class docstring).
        match = re.search(r"(\d+)\s*$", name or "")
        self._own_instance_index = int(match.group(1)) if match else None

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
        pairing_error = ""
        if sys.platform == "win32":
            try:
                from windows_ble_pairing import pair_with_pin  # local import: Windows-only dep

                await pair_with_pin(self._address, CGM_TEST_PASSKEY)
            except Exception as exc:  # pylint: disable=broad-except
                # Not every device needs pairing, so this alone isn't fatal — fall
                # through to a normal connect — but remember why in case notify
                # subscriptions below fail for lack of an authenticated link.
                pairing_error = str(exc)

        async with BleakClient(self._address) as client:
            if not client.is_connected:
                raise ConnectionError("Device did not accept the connection")

            notify_count = 0
            subscribed_count = 0
            last_error = ""
            socp_characteristics = []
            instance_index = -1
            for service in client.services:
                if service.uuid.lower() == CGM_SERVICE_UUID:
                    # A single device can expose several independent CGMS
                    # service instances over this one connection (e.g. one
                    # board simulating several sensors); each is a separate
                    # primary service sharing the same characteristic UUIDs,
                    # so count them in the order bleak enumerates them.
                    instance_index += 1
                for characteristic in service.characteristics:
                    uuid = characteristic.uuid.lower()
                    if uuid == CGM_MEASUREMENT_UUID:
                        self._instance_by_handle[characteristic.handle] = instance_index
                    if "notify" in characteristic.properties or "indicate" in characteristic.properties:
                        notify_count += 1
                        try:
                            await client.start_notify(characteristic, self._handle_notification)
                            subscribed_count += 1
                        except Exception as exc:  # pylint: disable=broad-except
                            last_error = str(exc)
                            continue
                    if uuid == CGM_SOCP_UUID and "write" in characteristic.properties:
                        socp_characteristics.append((instance_index, characteristic))

            if subscribed_count == 0 and notify_count > 0 and pairing_error:
                last_error = f"auto-pairing failed: {pairing_error}"

            self._instance_count = instance_index + 1
            for socp_instance, socp_characteristic in socp_characteristics:
                # Only (re)configure the instance this identity actually
                # represents — leave sibling instances, which belong to
                # other simulated sensors, untouched.
                if self._own_instance_index is not None and socp_instance != self._own_instance_index:
                    continue
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

    def _user_id(self) -> str:
        """Return a display identifier that stays unique across multiple connected devices.

        Several identical sensor boards (e.g. a batch of Nordic peripheral_cgms
        kits) commonly advertise the exact same BLE name, so the name alone
        can't be trusted to distinguish them once more than one is connected
        at a time. Suffixing with the last two bytes of the address keeps
        each device's readings on their own row in the UI.
        """
        if not self._name:
            return self._address
        suffix = self._address.replace("-", ":").split(":")[-2:]
        return f"{self._name} ({':'.join(suffix)})" if suffix else self._name

    def _handle_notification(self, characteristic, data: bytearray) -> None:
        """Turn a raw GATT notification into a message dict and emit it.

        Drops CGM Measurement notifications from sibling instances that
        don't belong to this identity's own sensor (see class docstring).
        """
        user_id = self._user_id()
        if characteristic.uuid.lower() == CGM_MEASUREMENT_UUID and self._instance_count > 1:
            instance = self._instance_by_handle.get(characteristic.handle)
            if self._own_instance_index is not None:
                if instance != self._own_instance_index:
                    return
            elif instance is not None:
                user_id = f"{user_id} · Sensor {instance + 1}"

        message = {
            "user_id": user_id,
            "dev_id": self._address,
            "characteristic": characteristic.uuid,
            "raw_hex": data.hex(),
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        if characteristic.uuid.lower() == CGM_MEASUREMENT_UUID:
            message.update(_decode_cgm_measurement(bytes(data)))
        self.new_message.emit(message)
