#ifndef DEXCOM_SERVICE_H
#define DEXCOM_SERVICE_H

/*
 * A minimal imitation of a Dexcom transmitter's BLE profile, used when
 * sim_config.comm_profile == SIM_COMM_DEXCOM. This is NOT a faithful Dexcom
 * implementation: there is no J-PAKE / AES authentication handshake and only
 * the realtime glucose message is emitted (no backfill, no calibration). It
 * exists so the app (and other tooling) can exercise a second, non-SIG-CGMS
 * wire format. See PROTOCOL_SPEC.md's "Comm profile" section and
 * docs/BLE_PAYLOAD_VALIDATION.md §2.
 *
 * Service UUID: 0xFEBC. Characteristics under the base
 * F8083532-849E-531C-C594-30F1F86A4EA5:
 *   - ...3535  Control  (write + notify) — writes are ACKed, nothing else
 *   - ...3538  Glucose  (notify)         — the 14-byte realtime message
 *
 * The GATT service is registered unconditionally (BT_GATT_SERVICE_DEFINE);
 * comm_thread only *feeds* it when the Dexcom profile is active, and main.c
 * only advertises 0xFEBC / the "DXCM01" name in that mode.
 */

#include <stdint.h>

/* Notifies the Glucose characteristic with a realtime message:
 *   opcode(0x4E) | status(0) | u32 sequence | u32 timestamp_s |
 *   u16 glucose(mg/dL, low 12 bits) | state(0x06) | int8 trend
 * (little-endian, 14 bytes). Safe to call every tick; a negative return
 * (nobody subscribed) is expected and ignored by the caller. */
int dexcom_service_notify_glucose(uint16_t glucose_mg_dl, int8_t trend);

#endif /* DEXCOM_SERVICE_H */
