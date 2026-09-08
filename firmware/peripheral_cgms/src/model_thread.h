#ifndef MODEL_THREAD_H
#define MODEL_THREAD_H

/*
 * Owns the physiological model + sensor noise state and steps it once per
 * wall-clock second (dt_min per tick depends on sim_config.mode — see
 * PROTOCOL_SPEC.md §6). Runs on its own k_thread, separate from comm_thread,
 * per the assignment's two-thread requirement (model vs. communication).
 */

#include <stdbool.h>

#include "sim_config.h"

struct model_measurement {
	float glucose_mg_dl;
	int valid; /* 1 if glucose_mg_dl is a fresh, unconsumed sensor sample */
	float carbs_g_per_min;
	float exercise_pct;
};

/* Live session control — deliberately NOT part of struct sim_config and
 * never persisted (see PROTOCOL_SPEC.md's "Run state" section). Defaults to
 * RUNNING at boot so the board is fully autonomous with no app connected. */
enum sim_run_state {
	SIM_RUN_STOPPED = 0,
	SIM_RUN_RUNNING = 1,
	SIM_RUN_PAUSED  = 2,
};

/* Instant (one-shot, non-recurring, never persisted) food/exercise events —
 * see PROTOCOL_SPEC.md's "Instant food/exercise events" section. Unlike the
 * recurring struct food_event/exercise_event above, adding one of these does
 * NOT go through apply_config_locked() — it does not reset sim_clock_min or
 * re-initialize model/sensor state, so it can be injected mid-run without
 * disrupting the simulation already in progress. */
struct food_instant_wire {
	uint16_t duration_min;
	float carbs_g;
} __packed;

struct exercise_instant_wire {
	uint16_t duration_min;
	float intensity_pct;
} __packed;

/* Instant PISA (Pressure-Induced Sensor Attenuation) event — a transient
 * downward attenuation of the sensor reading producing a false low without
 * real hypoglycaemia. Same non-disruptive semantics as the food/exercise
 * instant events (never persisted, never resets the run). depth_frac in
 * [0, 1] is the peak attenuation at the midpoint of duration_min; the
 * attenuation ramps in and out as depth_frac * sin(pi * elapsed/duration). */
struct pisa_instant_wire {
	uint16_t duration_min;
	float depth_frac;
} __packed;

/* Starts the model thread with the given initial config (already loaded from
 * flash / defaulted by the caller), run state RUNNING. Call once at boot. */
void model_thread_start(const struct sim_config *cfg);

/* Re-initializes model/sensor state and schedule from *cfg. Thread-safe;
 * takes effect on the model thread's next tick (up to ~1s later). Called by
 * comm_thread after persisting a newly received config to flash. */
void model_thread_apply_config(const struct sim_config *cfg);

/* Thread-safe. STOPPED always re-initializes model/sensor state from the
 * currently active config (sim_clock_min back to 0) and then holds; RUNNING
 * resumes/starts ticking; PAUSED holds without resetting. */
void model_thread_set_run_state(uint8_t state);

/* Thread-safe snapshot of the current run state, for GATT reads. */
uint8_t model_thread_get_run_state(void);

/* CGMS-only mode — live session control, not part of struct sim_config,
 * never persisted (same reasoning as sim_run_state above). While enabled,
 * the board streams only standard CGM Measurement notifications: no
 * Food/Exercise Status notifications (comm_thread.c checks this before
 * calling config_service_notify_food_exercise_status()) and no incoming
 * writes to person/sensor/mode/food/exercise/instant-event characteristics
 * (config_service.c's write handlers check this and reject with
 * BT_ATT_ERR_WRITE_NOT_PERMITTED) — the run-state and cgms-only
 * characteristics themselves stay writable so the app can still turn it
 * back off. Enabling does NOT reset anything — it just ensures the
 * simulation is RUNNING (starting it if it wasn't) and restricts what's
 * sent/accepted from there. Disabling always resets and stops (same as a
 * run-state STOPPED write), then waits for an explicit RUNNING write —
 * see PROTOCOL_SPEC.md's "CGMS Only mode" section. */
void model_thread_set_cgms_only(bool enabled);

/* Thread-safe snapshot of the current cgms-only flag, for GATT reads and
 * for config_service.c's write handlers to check before accepting a
 * config write. */
bool model_thread_get_cgms_only(void);

/* Copies out sensor *slot*'s latest measurement and, if it was unconsumed
 * (valid == 1), clears that slot's valid flag so it is only reported once.
 * carbs_g_per_min/exercise_pct are always current. Thread-safe. */
void model_thread_take_measurement(int slot, struct model_measurement *out);

/* Starts an instant carb bolus right now, active for duration_min simulated
 * minutes, without resetting sim_clock_min or re-initializing model/sensor
 * state — see the struct food_instant_wire comment above. Thread-safe,
 * non-blocking; drops the event if MAX_INSTANT_EVENTS slots are all already
 * active (logged, not fatal). Rate-fed models (Cambridge, UVA/Padova) spread
 * carbs_g evenly over duration_min; impulse-fed models (Roy&Parker,
 * Deichmann) deliver the full amount once, on the next tick. */
void model_thread_add_instant_food(int slot, uint16_t duration_min, float carbs_g);

/* Starts an instant exercise bout right now, active for duration_min
 * simulated minutes at intensity_pct, same non-disruptive semantics as
 * model_thread_add_instant_food(). While active, contributes
 * max(intensity_pct, whatever the recurring schedule currently gives). */
void model_thread_add_instant_exercise(int slot, uint16_t duration_min, float intensity_pct);

/* Starts an instant PISA attenuation right now, active for duration_min
 * simulated minutes, peak attenuation depth_frac at the midpoint. Multiplies
 * the sensor reading by (1 - depth_frac * sin(pi * elapsed/duration)) — a
 * smooth transient false low. Same non-disruptive semantics as the other
 * instant events; applies on the CSV data source too (PISA is a sensor
 * artefact, not a glucose change). */
void model_thread_add_instant_pisa(int slot, uint16_t duration_min, float depth_frac);

#endif /* MODEL_THREAD_H */
