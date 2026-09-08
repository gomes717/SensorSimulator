"""Persistent BLE connection that records every notification received from a device."""
from __future__ import annotations

import asyncio
import re
import sys
from datetime import datetime, timezone

from PyQt6.QtCore import QThread, pyqtSignal

from bleak import BleakClient

from api import ble_uuids
from api import protocol

# Standard Bluetooth SIG "Continuous Glucose Monitoring" service characteristics
# (used e.g. by Nordic's peripheral_cgms sample). Recognised specially so the
# glucose value can be decoded and reporting sped up instead of just logging hex.
CGM_SERVICE_UUID = "0000181f-0000-1000-8000-00805f9b34fb"
CGM_MEASUREMENT_UUID = "00002aa7-0000-1000-8000-00805f9b34fb"
CGM_SOCP_UUID = "00002aac-0000-1000-8000-00805f9b34fb"
SOCP_WRITE_CGM_COMMUNICATION_INTERVAL = 0x01

# Maps each simulator config characteristic's UUID to the short key used by
# BleSession.queue_write()/request_read() and by the config windows
# (person_config_window.py etc.) — must match src/config_service.c's
# characteristic UUIDs exactly.
CONFIG_CHAR_KEY_BY_UUID = {
    ble_uuids.PERSON_CONFIG_UUID: "person",  # read + write
    ble_uuids.SENSOR_CONFIG_UUID: "sensor",  # read + write
    ble_uuids.MODE_CONFIG_UUID: "mode",  # read + write
    ble_uuids.FOOD_EVENT_UUID: "food",  # write-only, appends one event
    ble_uuids.EXERCISE_EVENT_UUID: "exercise",  # write-only, appends one event
    ble_uuids.FOOD_INSTANT_UUID: "food_instant",  # write-only, one-shot, does not reset the board
    ble_uuids.EXERCISE_INSTANT_UUID: "exercise_instant",  # write-only, one-shot, does not reset the board
    ble_uuids.PISA_INSTANT_UUID: "pisa_instant",  # write-only, one-shot, does not reset the board
    ble_uuids.CGMS_ONLY_UUID: "cgms_only",  # read + write, not persisted on the board
    ble_uuids.DATA_SOURCE_UUID: "data_source",  # read + write, persisted (model vs CSV)
    ble_uuids.SPEED_UUID: "speed",  # read + write, persisted (x1..x1000 multiplier)
    ble_uuids.COMM_PROFILE_UUID: "comm_profile",  # read + write, persisted (SIG CGMS vs Dexcom)
    ble_uuids.CSV_CONTROL_UUID: "csv_control",  # write + notify, chunked CSV upload control
    ble_uuids.CSV_DATA_UUID: "csv_data",  # write-only, CSV upload data chunks
    ble_uuids.FOOD_EVENTS_READBACK_UUID: "food_list",  # read-only, full list
    ble_uuids.EXERCISE_EVENTS_READBACK_UUID: "exercise_list",  # read-only, full list
    ble_uuids.RUN_STATE_UUID: "run_state",  # read + write, not persisted on the board
    ble_uuids.RESET_SYNC_UUID: "reset_sync",  # notify-only
}
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
    write_failed = pyqtSignal(str, str, str)  # address, char_key, error message
    config_read = pyqtSignal(str, str, bytes)  # address, char_key, raw value
    reset_sync = pyqtSignal(str)  # address — see ble_uuids.RESET_SYNC_UUID
    csv_upload_progress = pyqtSignal(str, int, int)  # address, sent_bytes, total_bytes
    csv_upload_finished = pyqtSignal(str, bool, str)  # address, ok, message

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

        # Simulator config service (see ble_uuids.py / src/config_service.c):
        # characteristics discovered in _session(), keyed by CONFIG_CHAR_KEY_BY_UUID's
        # short names. Writes are queued from any thread via queue_write() and
        # drained by _session()'s own event loop, since bleak's client only
        # works on the loop it was created on.
        self._config_characteristics: dict[str, object] = {}
        self._write_queue: asyncio.Queue = asyncio.Queue()
        # CSV control notifications ({status, received_bytes}), consumed by
        # _do_csv_upload() as acks between the BEGIN / data / COMMIT phases.
        self._csv_ctrl_queue: asyncio.Queue = asyncio.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client: BleakClient | None = None  # set once connected, used by request_read()

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
        self._loop = asyncio.get_running_loop()
        pairing_error = ""
        if sys.platform == "win32":
            try:
                from services.windows_ble_pairing import pair_with_pin  # local import: Windows-only dep

                await pair_with_pin(self._address, CGM_TEST_PASSKEY)
            except Exception as exc:  # pylint: disable=broad-except
                # Not every device needs pairing, so this alone isn't fatal — fall
                # through to a normal connect — but remember why in case notify
                # subscriptions below fail for lack of an authenticated link.
                pairing_error = str(exc)

        async with BleakClient(self._address) as client:
            if not client.is_connected:
                raise ConnectionError("Device did not accept the connection")
            self._client = client

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
                    config_key = CONFIG_CHAR_KEY_BY_UUID.get(uuid)
                    if config_key is not None:
                        # Some of these are write-only, some read-only, some both
                        # (see CONFIG_CHAR_KEY_BY_UUID) — store regardless of which
                        # properties are present; queue_write()/request_read() are
                        # only ever called for the direction each key supports.
                        self._config_characteristics[config_key] = characteristic

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
                try:
                    char_key, payload = await asyncio.wait_for(self._write_queue.get(), timeout=0.2)
                except asyncio.TimeoutError:
                    continue
                characteristic = self._config_characteristics.get(char_key)
                if characteristic is None:
                    self.write_failed.emit(
                        self._address, char_key, "device does not expose this characteristic"
                    )
                    continue
                try:
                    await client.write_gatt_char(characteristic, payload, response=True)
                except Exception as exc:  # pylint: disable=broad-except
                    self.write_failed.emit(self._address, char_key, str(exc))

        self.disconnected.emit(self._address)

    def queue_write(self, char_key: str, payload: bytes) -> None:
        """Thread-safe: queue a simulator config characteristic write for this session's loop.

        *char_key* is one of "person"/"sensor"/"mode"/"food"/"exercise" (see
        CONFIG_CHAR_KEY_BY_UUID). Safe to call from any thread — writes are
        actually performed by _session()'s own asyncio loop, since bleak's
        client can only be driven from the loop it was created on. Silently
        dropped if the session hasn't finished connecting yet.
        """
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(self._write_queue.put_nowait, (char_key, payload))

    def request_read(self, char_key: str) -> None:
        """Thread-safe: ask the board for whatever it currently has for *char_key*.

        Result (or failure) arrives asynchronously via the config_read (or
        write_failed) signal — this call itself never blocks the calling
        thread. Safe to call from any thread; the actual GATT read runs on
        this session's own event loop, same reasoning as queue_write().
        """
        if self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(self._do_read(char_key), self._loop)

    async def _do_read(self, char_key: str) -> None:
        characteristic = self._config_characteristics.get(char_key)
        if characteristic is None or self._client is None:
            self.write_failed.emit(
                self._address, char_key, "device does not expose this characteristic"
            )
            return
        try:
            data = await self._client.read_gatt_char(characteristic)
        except Exception as exc:  # pylint: disable=broad-except
            self.write_failed.emit(self._address, char_key, str(exc))
            return
        self.config_read.emit(self._address, char_key, bytes(data))

    # ------------------------------------------------------------------
    # CSV playback upload (see api.protocol's encode_csv_* + PROTOCOL_SPEC.md)
    # ------------------------------------------------------------------

    def start_csv_upload(self, uploads: list[dict]) -> None:
        """Thread-safe: upload one or more CSV tracks to the board.

        Each entry in *uploads* is
        ``{"track": int, "blob": bytes, "row_count": int, "base_epoch_s": int,
        "interval_s": int}``. Progress and completion arrive on the
        csv_upload_progress / csv_upload_finished signals. Safe to call from any
        thread — the transfer runs on this session's own event loop.
        """
        if self._loop is None:
            self.csv_upload_finished.emit(self._address, False, "not connected")
            return
        asyncio.run_coroutine_threadsafe(self._do_csv_upload(uploads), self._loop)

    async def _await_csv_ctrl(self, timeout: float = 5.0) -> tuple[int, int]:
        """Wait for the next CSV control notification (status, received_bytes)."""
        return await asyncio.wait_for(self._csv_ctrl_queue.get(), timeout=timeout)

    async def _do_csv_upload(self, uploads: list[dict]) -> None:
        ctrl = self._config_characteristics.get("csv_control")
        data = self._config_characteristics.get("csv_data")
        if ctrl is None or data is None or self._client is None:
            self.csv_upload_finished.emit(
                self._address, False, "device does not expose the CSV characteristics"
            )
            return

        total = sum(len(u["blob"]) for u in uploads)
        sent = 0
        try:
            for u in uploads:
                blob: bytes = u["blob"]
                while not self._csv_ctrl_queue.empty():
                    self._csv_ctrl_queue.get_nowait()

                begin = protocol.encode_csv_begin(
                    u["track"], u["row_count"], u["base_epoch_s"],
                    u["interval_s"], len(blob), protocol.csv_crc32(blob),
                )
                await self._client.write_gatt_char(ctrl, begin, response=True)
                status, _ = await self._await_csv_ctrl()
                if status != protocol.CSV_CTRL_STATUS_OK:
                    raise RuntimeError(f"board rejected BEGIN for track {u['track']}")

                for chunk in protocol.iter_csv_data_chunks(blob):
                    await self._client.write_gatt_char(data, chunk, response=True)
                    sent += len(chunk) - 4  # minus the u32 offset prefix
                    self.csv_upload_progress.emit(self._address, min(sent, total), total)

                await self._client.write_gatt_char(
                    ctrl, protocol.encode_csv_commit(u["track"]), response=True
                )
                status, received = await self._await_csv_ctrl()
                if status != protocol.CSV_CTRL_STATUS_OK:
                    raise RuntimeError(
                        f"board rejected COMMIT for track {u['track']} "
                        f"(received {received}/{len(blob)} bytes)"
                    )
            self.csv_upload_finished.emit(self._address, True, f"CSV uploaded ({total} bytes)")
        except Exception as exc:  # pylint: disable=broad-except
            try:
                await self._client.write_gatt_char(
                    ctrl, protocol.encode_csv_abort(), response=True
                )
            except Exception:  # pylint: disable=broad-except
                pass
            self.csv_upload_finished.emit(self._address, False, str(exc))

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
            print(f"[ble] CGM measurement from {user_id}: {message.get('glucose_value')} mg/dL "
                  f"raw={data.hex()}")
        elif characteristic.uuid.lower() == ble_uuids.DEXCOM_GLUCOSE_CHAR_UUID:
            decoded = protocol.decode_dexcom_glucose(bytes(data))
            if decoded is not None:
                message.update(decoded)
            print(f"[ble] dexcom glucose from {user_id}: {message.get('glucose_value')} mg/dL "
                  f"seq={message.get('sequence')} raw={data.hex()}")
        elif characteristic.uuid.lower() == ble_uuids.FOOD_EXERCISE_STATUS_UUID:
            decoded = protocol.decode_food_exercise_status(bytes(data))
            if decoded is not None:
                message.update(decoded)
            print(f"[ble] food/exercise status from {user_id}: {decoded}")
        elif characteristic.uuid.lower() == ble_uuids.RESET_SYNC_UUID:
            print(f"[ble] reset_sync from {user_id}: generation={data[0] if data else '?'}")
            self.reset_sync.emit(self._address)
            return  # control event, not a data point — don't add it to new_message
        elif characteristic.uuid.lower() == ble_uuids.CSV_CONTROL_UUID:
            decoded = protocol.decode_csv_control_notify(bytes(data))
            print(f"[ble] csv_control from {user_id}: {decoded} raw={data.hex()}")
            if decoded is not None:
                self._csv_ctrl_queue.put_nowait(decoded)
            return  # control event, not a data point
        self.new_message.emit(message)
