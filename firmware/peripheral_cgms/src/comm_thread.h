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
};

/* Enqueue a config write for comm_thread to apply. Thread-safe, non-blocking
 * — safe to call from a GATT write callback. Returns 0 on success, a
 * negative errno if the queue is full or *data is too large. */
int comm_thread_enqueue_config(enum cfg_msg_type type, const void *data, uint16_t len);

/* Starts comm_thread. *cgms is the single CGMS service instance to push
 * measurements to; *initial_cfg seeds the in-RAM working copy comm_thread
 * serves GATT reads from. Call once at boot. */
void comm_thread_start(struct bt_cgms *cgms, const struct sim_config *initial_cfg);

/* Thread-safe snapshot of the config comm_thread currently has applied —
 * used by config_service.c's read callbacks to serve GATT reads directly
 * from RAM (no flash access on the read path). */
void comm_thread_copy_config(struct sim_config *out);

#endif /* COMM_THREAD_H */
