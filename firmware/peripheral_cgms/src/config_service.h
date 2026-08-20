#ifndef CONFIG_SERVICE_H
#define CONFIG_SERVICE_H

/*
 * Custom "simulator config" GATT service — see SensorSimulator/PROTOCOL_SPEC.md
 * §2 for the authoritative UUID/byte-layout contract. Registered automatically
 * at boot via BT_GATT_SERVICE_DEFINE; config_service_init() just logs.
 */

#include <stdint.h>

void config_service_init(void);

/* Notifies the Food/Exercise Status characteristic if anyone has subscribed.
 * *data must be the 8-byte {float carbs_g_per_min; float exercise_pct;}
 * wire payload. Returns 0 on success; a negative errno (commonly meaning
 * "nobody is subscribed yet") is expected and safe to ignore. */
int config_service_notify_food_exercise_status(const void *data, uint16_t len);

/* Notifies the Reset Sync characteristic the instant a config/state reset
 * actually takes effect (called from model_thread.c's apply_config_locked()).
 * True clock synchronization: the app anchors its own t=0 to the arrival of
 * this notification instead of to whenever it sent the write that caused the
 * reset, eliminating the BLE round-trip + processing latency (up to a
 * couple seconds, worse in fast mode) that otherwise shows up as a visible
 * phase shift between the app's local model and the board's. Payload is a
 * single incrementing byte (generation counter) — its arrival time is the
 * signal, the value itself is only for diagnostics. Return value: see
 * config_service_notify_food_exercise_status(). */
int config_service_notify_reset_sync(void);

#endif /* CONFIG_SERVICE_H */
