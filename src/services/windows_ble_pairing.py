"""Windows-only helper: auto-supply a fixed PIN during BLE pairing.

bleak's built-in ``BleakClient.pair()`` only implements the Just-Works
pairing ceremony (it blindly accepts any request), which does not work for
devices that use Passkey Entry — Windows needs the PIN supplied via
``DevicePairingRequestedEventArgs.accept_with_pin()``. This talks to the
WinRT Device Enumeration API directly, independent of any BleakClient
instance, so it can run before the GATT connection is even opened.
"""

from __future__ import annotations

import asyncio

from winrt.windows.devices.bluetooth import BluetoothLEDevice
from winrt.windows.devices.enumeration import (
    DeviceInformation,
    DevicePairingKinds,
    DevicePairingResultStatus,
)

# Empirically, a device's very first custom-pairing negotiation after a
# connection (or a previous unpair) commonly fails outright with a generic
# WinRT "FAILED" status and no pairing_requested event ever firing, then
# succeeds cleanly a few seconds later on retry — observed consistently
# against this project's peripheral_cgms multi-sensor firmware. A short
# backoff-and-retry clears it far more reliably than any single attempt.
PAIR_ATTEMPTS = 3
PAIR_RETRY_DELAY_SECONDS = 3.0


def _address_to_int(address: str) -> int:
    """Convert a colon/dash-separated MAC address string to its integer form."""
    return int(address.replace(":", "").replace("-", ""), 16)


async def pair_with_pin(address: str, pin: str) -> None:
    """Pair with the BLE device at *address*, auto-supplying *pin* if one is requested.

    Retries up to :data:`PAIR_ATTEMPTS` times with a short delay (see module
    docstring above) before giving up. Raises ConnectionError if every
    attempt fails.
    """
    last_error: Exception | None = None
    for attempt in range(PAIR_ATTEMPTS):
        try:
            await _pair_once(address, pin)
            return
        except Exception as exc:  # pylint: disable=broad-except
            last_error = exc
            if attempt < PAIR_ATTEMPTS - 1:
                await asyncio.sleep(PAIR_RETRY_DELAY_SECONDS)
    raise last_error


async def _pair_once(address: str, pin: str) -> None:
    """Make a single pairing attempt against the device at *address*.

    Always (re)pairs fresh, even if Windows already has a pairing record for
    this address: that record may predate the firmware's fixed passkey (or
    come from pairing by hand via Windows Settings), and a stale/mismatched
    link key can't be verified up front — it just fails silently later and
    surfaces as Windows' own pairing prompt when the GATT link tries to
    re-authenticate. Unpairing first guarantees *pin* is what actually gets
    used, and keeps the whole flow in this app instead of Windows Settings.
    Raises ConnectionError if pairing is attempted and does not succeed.
    """
    device = await BluetoothLEDevice.from_bluetooth_address_async(_address_to_int(address))
    if device is None:
        raise ConnectionError(f"Device {address} not found for pairing")

    try:
        device_information = await DeviceInformation.create_from_id_async(
            device.device_information.id
        )
        if device_information.pairing.is_paired:
            await device_information.pairing.unpair_async()
            device_information = await DeviceInformation.create_from_id_async(
                device.device_information.id
            )

        custom_pairing = device_information.pairing.custom

        def handler(sender, args):
            if args.pairing_kind == DevicePairingKinds.PROVIDE_PIN:
                args.accept_with_pin(pin)
            else:
                args.accept()

        token = custom_pairing.add_pairing_requested(handler)
        try:
            ceremony = DevicePairingKinds.CONFIRM_ONLY | DevicePairingKinds.PROVIDE_PIN
            result = await custom_pairing.pair_async(ceremony)
        finally:
            custom_pairing.remove_pairing_requested(token)

        if result.status not in (
            DevicePairingResultStatus.PAIRED,
            DevicePairingResultStatus.ALREADY_PAIRED,
        ):
            raise ConnectionError(f"Pairing failed: {result.status.name}")
    finally:
        device.close()
