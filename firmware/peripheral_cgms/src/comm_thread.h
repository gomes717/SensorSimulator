#ifndef COMM_THREAD_H
#define COMM_THREAD_H

/*
 * Owns BLE communication: pushes CGMS measurements + Food/Exercise Status
 * notifications from the model thread's latest output, and applies config
 * writes (queued here by config_service.c's GATT write callbacks, which run
 * in BT host context and must stay short) — persisting each to flash and
 * handing it to model_thread. Runs on its own k_thread, separate from
 * model_thread, per the assignment's two-thread requirement.
 */

#include <stdint.h>
#include <bluetooth/services/cgms.h>

#include "sim_config.h"

enum cfg_msg_type {
	CFG_MSG_PERSON,
	CFG_MSG_SENSOR,
	CFG_MSG_MODE,
	CFG_MSG_FOOD_EVENT,
	CFG_MSG_EXERCISE_EVENT,
	CFG_MSG_RUN_STATE, /* not part of sim_config — never persisted to flash */
	/* Instant (one-shot) events — not part of sim_config, never persisted,
	 * and deliberately NOT routed through model_thread_apply_config(): see
	 * model_thread.h's struct food_instant_wire comment. Applied directly
	 * to model_thread's live instant-event slots. */
	CFG_MSG_FOOD_INSTANT,
	CFG_MSG_EXERCISE_INSTANT,
	/* Instant PISA attenuation — not part of sim_config, never persisted,
	 * applied directly to model_thread's live PISA slots (like the food/
	 * exercise instant events). */
	CFG_MSG_PISA_INSTANT,
	/* CGMS-only mode toggle — not part of sim_config, never persisted. See
	 * model_thread.h's model_thread_set_cgms_only() comment. */
	CFG_MSG_CGMS_ONLY,
	/* Data source (SIM_DATA_MODEL / SIM_DATA_CSV) — part of sim_config,
	 * persisted like person/sensor/mode. */
	CFG_MSG_DATA_SOURCE,
	/* Speed multiplier (float32, SIM_SPEED_MIN..SIM_SPEED_MAX) — part of
	 * sim_config, persisted. Replaces the legacy on/off CFG_MSG_MODE. */
	CFG_MSG_SPEED,
	/* BLE comm profile (SIM_COMM_SIG_CGMS / SIM_COMM_DEXCOM) — part of
	 * sim_config, persisted. Triggers a re-advertise (main_apply_comm_profile). */
	CFG_MSG_COMM_PROFILE,
	/* Uploaded-CSV transport (see csv_store.h / PROTOCOL_SPEC.md). CONTROL
	 * carries an opcode-tagged BEGIN/COMMIT/ABORT/CLEAR/STATUS payload; DATA
	 * carries u32 offset + track bytes. Handled straight through csv_store_*,
	 * not routed through model_thread_apply_config(). */
	CFG_MSG_CSV_CONTROL,
	CFG_MSG_CSV_DATA,
	/* Sensor-select cursor — NOT persisted. Sets which of the sensor_count
	 * slots subsequent person/sensor/food/exercise/data-source/CSV writes and
	 * reads target. See PROTOCOL_SPEC.md's "Sensor select" section. */
	CFG_MSG_SENSOR_SELECT,
};

/* Enqueue a config write for comm_thread to apply. Thread-safe, non-blocking
 * — safe to call from a GATT write callback. Returns 0 on success, a
 * negative errno if the queue is full or *data is too large. */
int comm_thread_enqueue_config(enum cfg_msg_type type, const void *data, uint16_t len);

/* Starts comm_thread. *cgms is an array of the CGMS service instances (one per
 * sensor slot) to push measurements to; *initial_cfg seeds the in-RAM working
 * copy comm_thread serves GATT reads from. Call once at boot. */
void comm_thread_start(struct bt_cgms **cgms, const struct sim_config *initial_cfg);

/* Thread-safe snapshot of the config comm_thread currently has applied —
 * used by config_service.c's read callbacks to serve GATT reads directly
 * from RAM (no flash access on the read path). */
void comm_thread_copy_config(struct sim_config *out);

/* Narrow read-path accessors for config_service.c — avoid copying the whole
 * ~2.85 KB struct sim_config onto the BT RX stack just to serve one field.
 * comm_thread_copy_selected_slot() returns the slot the sensor-select cursor
 * currently points at (what every per-sensor GATT read serves). */
void comm_thread_copy_selected_slot(struct sensor_slot *out);
uint8_t comm_thread_comm_profile(void);
float comm_thread_speed_mult(void);

/* The slot index (0..sensor_count-1) that per-sensor config reads/writes
 * currently target — set by the sensor-select characteristic. */
uint8_t comm_thread_selected_slot(void);

#endif /* COMM_THREAD_H */
