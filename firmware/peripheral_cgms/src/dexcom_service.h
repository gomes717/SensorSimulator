#ifndef DEXCOM_SERVICE_H
#define DEXCOM_SERVICE_H

/*
 * The GATT face of the Dexcom G6 board mode: a transmitter xDrip can authenticate against and read.
 * The protocol itself (CRC, the AES challenge, request -> reply, the 5-minute grid) lives in
 * dexcom_g6.c and is tested on the PC; this file only moves bytes between GATT and that module and
 * keeps the per-connection authentication state.
 *
 * Characteristics (base F8083532-849E-531C-C594-30F1F86A4EA5, as xDrip's BluetoothServices):
 *   ...3533 Communication  read + notify
 *   ...3534 Control        write + indicate   commands in, replies out
 *   ...3535 Authentication write + read + indicate
 *   ...3536 Backfill       notify
 *   ...3538 ExtraData      notify
 * No characteristic requires encryption: the board reports itself "already bonded" and the G6
 * challenge (keyed by the transmitter ID, sim_config.dexcom_tx_id) is the only gate. The service is
 * registered unconditionally but answers only in the Dexcom board mode.
 *
 * A G6 never streams: the client connects, authenticates, asks for the reading, and leaves. The
 * board keeps the latest reading and answers requests from it.
 */

#include <stdint.h>

#include "dexcom_g6.h"

/* Forget the held reading and the transmitter clock (mode switch, boot). */
void dexcom_service_reset(void);

/* Make *r the reading clients are served until the next one. */
void dexcom_service_publish(const struct g6_reading *r);

/* The transmitter clock in seconds (simulated time); answered to a time request. */
void dexcom_service_set_clock(uint32_t now_s);

#endif /* DEXCOM_SERVICE_H */
