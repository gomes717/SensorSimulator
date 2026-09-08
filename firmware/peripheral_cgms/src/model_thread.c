#include <math.h>
#include <string.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/printk.h>

#include "model_thread.h"
#include "config_service.h"
#include "csv_store.h"
#include "models/cgmsim_cambridge.h"
#include "models/cgmsim_uva_padova.h"
#include "models/cgmsim_royparker.h"
#include "models/cgmsim_deichmann.h"
#include "models/cgmsim_sensors.h"

/* Every model's Params struct is a plain struct-of-doubles whose field order
 * matches its PARAM_NAMES list in SensorSimulator/src/models/*.py exactly. */
BUILD_ASSERT(sizeof(CambridgeParams) == 17 * sizeof(double), "CambridgeParams field count");
BUILD_ASSERT(sizeof(UvaPadovaParams) == 33 * sizeof(double), "UvaPadovaParams field count");
BUILD_ASSERT(sizeof(RoyParkerParams) == 22 * sizeof(double), "RoyParkerParams field count");
BUILD_ASSERT(sizeof(DeichmannParams) == 22 * sizeof(double), "DeichmannParams field count");

#define MODEL_THREAD_STACK_SIZE 4096
#define MODEL_THREAD_PRIORITY   5
#define MINUTES_PER_DAY         1440.0
/* Max simulated-minutes per fixed-step ODE integration step. A tick's dt
 * (= speed_mult / 60) above this is split into ceil(dt / this) sub-steps so
 * the models stay numerically stable at high speed multipliers. 1.0 keeps
 * x1..x60 (dt <= 1) exactly as before. */
#define MODEL_SUBSTEP_MAX_MIN   1.0

#define CSV_FOODLOG_SPREAD_MIN 30
#define CSV_FOODLOG_MAX_HITS   8
#define MAX_INSTANT_EVENTS     8

static K_THREAD_STACK_DEFINE(model_thread_stack, MODEL_THREAD_STACK_SIZE);
static struct k_thread model_thread_data;

/* Config handoff from comm_thread -> model_thread. */
static K_MUTEX_DEFINE(cfg_lock);
static struct sim_config pending_cfg;
static bool cfg_pending;

/* Published measurements, one per sensor slot, read by comm_thread. */
static K_MUTEX_DEFINE(meas_lock);
static struct model_measurement latest[MAX_SIM_SENSORS];

/* Model-thread-private per-slot runtime. */
struct slot_runtime {
	union {
		CambridgeState cambridge;
		UvaPadovaState uva_padova;
		RoyParkerState royparker;
		DeichmannState deichmann;
	} model_state;
	union {
		CambridgeParams cambridge;
		UvaPadovaParams uva_padova;
		RoyParkerParams royparker;
		DeichmannParams deichmann;
	} model_params;
	union {
		BretonState breton;
		FacchinetttiState facchinetti;
	} sensor_state;
	double basal_u_per_h;
	int last_fired_day[MAX_EVENTS];
};

static struct sim_config active_cfg;
static struct slot_runtime rt[MAX_SIM_SENSORS];
static double sim_clock_min; /* shared across all slots */

/* Instant (one-shot, non-persisted) events — per slot, added by comm_thread. */
struct instant_food_slot {
	bool active;
	bool delivered;
	double remaining_min;
	double duration_min;
	double carbs_g;
};
struct instant_exercise_slot {
	bool active;
	double remaining_min;
	double intensity_pct;
};
struct instant_pisa_slot {
	bool active;
	double remaining_min;
	double duration_min;
	double depth;
};

static K_MUTEX_DEFINE(instant_lock);
static struct instant_food_slot instant_food[MAX_SIM_SENSORS][MAX_INSTANT_EVENTS];
static struct instant_exercise_slot instant_exercise[MAX_SIM_SENSORS][MAX_INSTANT_EVENTS];
static struct instant_pisa_slot instant_pisa[MAX_SIM_SENSORS][MAX_INSTANT_EVENTS];

static uint8_t sensor_count(void)
{
	uint8_t n = active_cfg.sensor_count;

	return (n >= 1 && n <= MAX_SIM_SENSORS) ? n : 1;
}

static bool model_is_rate_fed(uint8_t model_id)
{
	return model_id == SIM_MODEL_CAMBRIDGE || model_id == SIM_MODEL_UVA_PADOVA;
}

static bool in_daily_window(double time_of_day_min, double start_min, double duration_min)
{
	double end = start_min + duration_min;
	double t = fmod(time_of_day_min, MINUTES_PER_DAY);

	if (t < 0.0) {
		t += MINUTES_PER_DAY;
	}
	if (end <= MINUTES_PER_DAY) {
		return t >= start_min && t < end;
	}
	return t >= start_min || t < (end - MINUTES_PER_DAY);
}

static double slot_hr_baseline(int s)
{
	return (active_cfg.slots[s].model_id == SIM_MODEL_DEICHMANN)
		       ? rt[s].model_params.deichmann.HRb
		       : 80.0;
}

static void init_slot(int s)
{
	const struct sensor_slot *cfg = &active_cfg.slots[s];
	struct slot_runtime *r = &rt[s];

	switch (cfg->model_id) {
	case SIM_MODEL_UVA_PADOVA: {
		double *dst = (double *)&r->model_params.uva_padova;
		size_t n = sizeof(UvaPadovaParams) / sizeof(double);

		for (size_t i = 0; i < n; i++) {
			dst[i] = (double)cfg->model_params[i];
		}
		uva_padova_init(&r->model_state.uva_padova, &r->model_params.uva_padova);
		r->basal_u_per_h = uva_padova_basal_iir_u_per_h(&r->model_params.uva_padova);
		break;
	}
	case SIM_MODEL_ROYPARKER: {
		double *dst = (double *)&r->model_params.royparker;
		size_t n = sizeof(RoyParkerParams) / sizeof(double);

		for (size_t i = 0; i < n; i++) {
			dst[i] = (double)cfg->model_params[i];
		}
		royparker_init(&r->model_state.royparker, &r->model_params.royparker);
		r->basal_u_per_h = r->model_params.royparker.u1b;
		break;
	}
	case SIM_MODEL_DEICHMANN: {
		double *dst = (double *)&r->model_params.deichmann;
		size_t n = sizeof(DeichmannParams) / sizeof(double);

		for (size_t i = 0; i < n; i++) {
			dst[i] = (double)cfg->model_params[i];
		}
		deichmann_init(&r->model_state.deichmann, &r->model_params.deichmann);
		r->basal_u_per_h = r->model_params.deichmann.IIRb;
		break;
	}
	case SIM_MODEL_CAMBRIDGE:
	default: {
		double *dst = (double *)&r->model_params.cambridge;
		size_t n = sizeof(CambridgeParams) / sizeof(double);

		for (size_t i = 0; i < n; i++) {
			dst[i] = (double)cfg->model_params[i];
		}
		cambridge_init(&r->model_state.cambridge, &r->model_params.cambridge);
		r->basal_u_per_h = cambridge_basal_iir_u_per_h(&r->model_params.cambridge);
		break;
	}
	}

	switch (cfg->sensor_id) {
	case SIM_SENSOR_BRETON:
		breton_init(&r->sensor_state.breton, k_uptime_get_32() + (uint32_t)s * 7919u);
		r->sensor_state.breton.pacf = cfg->sensor_params[0];
		r->sensor_state.breton.sigma = cfg->sensor_params[1];
		r->sensor_state.breton.alpha = cfg->sensor_params[2];
		r->sensor_state.breton.beta = cfg->sensor_params[3];
		break;
	case SIM_SENSOR_FACCHINETTI:
		facchinetti_init(&r->sensor_state.facchinetti, k_uptime_get_32() + (uint32_t)s * 7919u);
		r->sensor_state.facchinetti.a0 = cfg->sensor_params[0];
		r->sensor_state.facchinetti.a1 = cfg->sensor_params[1];
		r->sensor_state.facchinetti.a2 = cfg->sensor_params[2];
		r->sensor_state.facchinetti.b0 = cfg->sensor_params[3];
		r->sensor_state.facchinetti.b1 = cfg->sensor_params[4];
		r->sensor_state.facchinetti.b2 = cfg->sensor_params[5];
		r->sensor_state.facchinetti.aw1 = cfg->sensor_params[6];
		r->sensor_state.facchinetti.aw2 = cfg->sensor_params[7];
		r->sensor_state.facchinetti.sigma_v = cfg->sensor_params[8];
		r->sensor_state.facchinetti.ac1 = cfg->sensor_params[9];
		r->sensor_state.facchinetti.ac2 = cfg->sensor_params[10];
		r->sensor_state.facchinetti.sigma_c = cfg->sensor_params[11];
		break;
	case SIM_SENSOR_IDEAL:
	default:
		break;
	}

	for (int i = 0; i < MAX_EVENTS; i++) {
		r->last_fired_day[i] = -1;
	}
}

static void apply_config_locked(const struct sim_config *cfg)
{
	active_cfg = *cfg;

	for (int s = 0; s < sensor_count(); s++) {
		init_slot(s);
	}

	sim_clock_min = 0.0;

	k_mutex_lock(&instant_lock, K_FOREVER);
	memset(instant_food, 0, sizeof(instant_food));
	memset(instant_exercise, 0, sizeof(instant_exercise));
	memset(instant_pisa, 0, sizeof(instant_pisa));
	k_mutex_unlock(&instant_lock);

	printk("model_thread: applied config (sensor_count=%u, slot0 model=%u sensor=%u ds=%u)\n",
	       active_cfg.sensor_count, active_cfg.slots[0].model_id,
	       active_cfg.slots[0].sensor_id, active_cfg.slots[0].data_source);

	config_service_notify_reset_sync();
}

/* Step one sensor slot for this tick; writes latest[s]. */
static void tick_slot(int s, double dt_min, double time_of_day, int day_index)
{
	const struct sensor_slot *cfg = &active_cfg.slots[s];
	struct slot_runtime *r = &rt[s];
	bool rate_fed = model_is_rate_fed(cfg->model_id);
	bool csv_mode = (cfg->data_source == SIM_DATA_CSV) && csv_glucose_available(s);

	if (csv_mode) {
		struct csv_food_hit hits[CSV_FOODLOG_MAX_HITS];
		int n = csv_foodlog_window(s, sim_clock_min * 60.0,
					   (sim_clock_min + dt_min) * 60.0, hits, CSV_FOODLOG_MAX_HITS);

		for (int i = 0; i < n; i++) {
			model_thread_add_instant_food(s, CSV_FOODLOG_SPREAD_MIN, hits[i].carbs_g);
		}
	}

	double carbs = 0.0;

	for (int i = 0; i < cfg->food_count; i++) {
		const struct food_event *ev = &cfg->food[i];

		if (rate_fed) {
			if (in_daily_window(time_of_day, ev->time_min, ev->duration_min)) {
				carbs += ev->carbs_g / ev->duration_min;
			}
		} else if (r->last_fired_day[i] != day_index &&
			   in_daily_window(time_of_day, ev->time_min, dt_min)) {
			carbs += ev->carbs_g;
			r->last_fired_day[i] = day_index;
		}
	}

	double exercise_pct = 0.0;
	double hr_bpm = slot_hr_baseline(s);

	for (int i = 0; i < cfg->exercise_count; i++) {
		const struct exercise_event *ev = &cfg->exercise[i];

		if (in_daily_window(time_of_day, ev->time_min, ev->duration_min)) {
			exercise_pct = ev->intensity_pct;
			hr_bpm = slot_hr_baseline(s) + ev->intensity_pct / 100.0 * 80.0;
			break;
		}
	}

	double pisa_factor = 1.0;

	k_mutex_lock(&instant_lock, K_FOREVER);
	for (int i = 0; i < MAX_INSTANT_EVENTS; i++) {
		struct instant_food_slot *is = &instant_food[s][i];

		if (!is->active) {
			continue;
		}
		if (rate_fed) {
			carbs += is->carbs_g / is->duration_min;
		} else if (!is->delivered) {
			carbs += is->carbs_g;
			is->delivered = true;
		}
		is->remaining_min -= dt_min;
		if (is->remaining_min <= 0.0) {
			is->active = false;
		}
	}
	for (int i = 0; i < MAX_INSTANT_EVENTS; i++) {
		struct instant_exercise_slot *is = &instant_exercise[s][i];

		if (!is->active) {
			continue;
		}
		if (is->intensity_pct > exercise_pct) {
			exercise_pct = is->intensity_pct;
			hr_bpm = slot_hr_baseline(s) + is->intensity_pct / 100.0 * 80.0;
		}
		is->remaining_min -= dt_min;
		if (is->remaining_min <= 0.0) {
			is->active = false;
		}
	}
	for (int i = 0; i < MAX_INSTANT_EVENTS; i++) {
		struct instant_pisa_slot *is = &instant_pisa[s][i];

		if (!is->active) {
			continue;
		}
		double elapsed = is->duration_min - is->remaining_min;
		double frac = (is->duration_min > 0.0) ? elapsed / is->duration_min : 1.0;

		frac = (frac < 0.0) ? 0.0 : (frac > 1.0 ? 1.0 : frac);
		pisa_factor *= (1.0 - is->depth * sin(frac * 3.14159265358979323846));
		is->remaining_min -= dt_min;
		if (is->remaining_min <= 0.0) {
			is->active = false;
		}
	}
	k_mutex_unlock(&instant_lock);
	if (pisa_factor < 0.0) {
		pisa_factor = 0.0;
	}

	double carbs_rate_display = rate_fed ? carbs : (carbs > 0.0 ? carbs / dt_min : 0.0);
	double glucose = 0.0;
	float csv_glucose = 0.0f;
	double reading_val;
	int reading_valid = 1;

	if (csv_mode && csv_glucose_lookup(s, sim_clock_min, &csv_glucose)) {
		glucose = (double)csv_glucose;
		reading_val = glucose * pisa_factor;
	} else {
		/* The glucose-insulin ODEs are integrated with a fixed explicit
		 * step. A large dt (high speed multiplier) makes them diverge:
		 * at x300 the step is 5 sim-minutes and Cambridge blows up to
		 * ~600 mg/dL while the others collapse to 0. Sub-step so every
		 * integration step is <= MODEL_SUBSTEP_MAX_MIN. x1..x60 (dt <= 1)
		 * are unaffected (nsub == 1). For impulse-fed models `carbs` is
		 * a one-shot mass, so it is delivered on the first sub-step only;
		 * rate-fed `carbs` (g/min) and the exercise level apply to every
		 * sub-step. */
		int nsub = (int)ceil(dt_min / MODEL_SUBSTEP_MAX_MIN);

		if (nsub < 1) {
			nsub = 1;
		}
		double sub_dt = dt_min / (double)nsub;
		double carbs_rest = rate_fed ? carbs : 0.0;

		switch (cfg->model_id) {
		case SIM_MODEL_UVA_PADOVA:
			for (int k = 0; k < nsub; k++) {
				uva_padova_step(&r->model_state.uva_padova,
						&r->model_params.uva_padova,
						(k == 0) ? carbs : carbs_rest,
						r->basal_u_per_h / 60.0, sub_dt);
			}
			glucose = uva_padova_glucose_mg_dl(&r->model_state.uva_padova,
							  &r->model_params.uva_padova);
			break;
		case SIM_MODEL_ROYPARKER:
			for (int k = 0; k < nsub; k++) {
				royparker_step(&r->model_state.royparker,
					       &r->model_params.royparker,
					       (k == 0) ? carbs : carbs_rest,
					       r->basal_u_per_h, exercise_pct,
					       sim_clock_min, sub_dt);
			}
			glucose = royparker_glucose_mg_dl(&r->model_state.royparker);
			break;
		case SIM_MODEL_DEICHMANN:
			for (int k = 0; k < nsub; k++) {
				deichmann_step(&r->model_state.deichmann,
					       &r->model_params.deichmann,
					       (k == 0) ? carbs : carbs_rest,
					       r->basal_u_per_h, hr_bpm, sub_dt);
			}
			glucose = deichmann_glucose_mg_dl(&r->model_state.deichmann);
			break;
		case SIM_MODEL_CAMBRIDGE:
		default:
			for (int k = 0; k < nsub; k++) {
				cambridge_step(&r->model_state.cambridge,
					       &r->model_params.cambridge,
					       (k == 0) ? carbs : carbs_rest,
					       r->basal_u_per_h, sub_dt);
			}
			glucose = cambridge_glucose_mg_dl(&r->model_state.cambridge,
							 &r->model_params.cambridge);
			break;
		}

		CGMReading reading;

		switch (cfg->sensor_id) {
		case SIM_SENSOR_BRETON:
			reading = breton_update(&r->sensor_state.breton, glucose, dt_min);
			break;
		case SIM_SENSOR_FACCHINETTI:
			reading = facchinetti_update(&r->sensor_state.facchinetti, glucose, dt_min);
			break;
		case SIM_SENSOR_IDEAL:
		default:
			reading = ideal_cgm_update(glucose);
			/* Small deterministic per-slot offset so the N Ideal-sensor
			 * streams are visibly distinct before per-slot config exists. */
			reading.value_mg_dl += (s - (sensor_count() - 1) / 2.0) * 2.0;
			break;
		}
		reading_val = reading.value_mg_dl * pisa_factor;
		reading_valid = reading.valid;
	}

	k_mutex_lock(&meas_lock, K_FOREVER);
	if (reading_valid) {
		latest[s].glucose_mg_dl = (float)reading_val;
		latest[s].valid = 1;
	}
	latest[s].carbs_g_per_min = (float)carbs_rate_display;
	latest[s].exercise_pct = (float)exercise_pct;
	k_mutex_unlock(&meas_lock);

	printk("model_tick[%d]: t_sim=%.2fmin dt=%.4f model=%u sensor=%u ds=%u glucose=%.2f "
	       "reading=%.2f pisa=%.3f carbs=%.3f ex=%.1f\n",
	       s, sim_clock_min, dt_min, cfg->model_id, cfg->sensor_id, cfg->data_source,
	       glucose, reading_val, pisa_factor, carbs_rate_display, exercise_pct);
}

static void model_tick(void)
{
	float mult = active_cfg.speed_mult;

	if (!(mult >= SIM_SPEED_MIN && mult <= SIM_SPEED_MAX)) {
		mult = SIM_SPEED_DEFAULT;
	}
	double dt_min = (1.0 / 60.0) * (double)mult;
	double time_of_day = fmod(sim_clock_min, MINUTES_PER_DAY);
	int day_index = (int)(sim_clock_min / MINUTES_PER_DAY);

	for (int s = 0; s < sensor_count(); s++) {
		tick_slot(s, dt_min, time_of_day, day_index);
	}

	sim_clock_min += dt_min;
}

static K_MUTEX_DEFINE(run_state_lock);
static uint8_t run_state = SIM_RUN_RUNNING;

void model_thread_set_run_state(uint8_t state)
{
	printk("model_thread: run_state -> %u (0=stopped 1=running 2=paused)\n", state);

	if (state == SIM_RUN_STOPPED) {
		k_mutex_lock(&cfg_lock, K_FOREVER);
		if (!cfg_pending) {
			pending_cfg = active_cfg;
			cfg_pending = true;
		}
		k_mutex_unlock(&cfg_lock);
	}

	k_mutex_lock(&run_state_lock, K_FOREVER);
	run_state = state;
	k_mutex_unlock(&run_state_lock);
}

uint8_t model_thread_get_run_state(void)
{
	k_mutex_lock(&run_state_lock, K_FOREVER);
	uint8_t state = run_state;
	k_mutex_unlock(&run_state_lock);

	return state;
}

static K_MUTEX_DEFINE(cgms_only_lock);
static bool cgms_only;

void model_thread_set_cgms_only(bool enabled)
{
	printk("model_thread: cgms_only -> %d\n", enabled);

	k_mutex_lock(&cgms_only_lock, K_FOREVER);
	cgms_only = enabled;
	k_mutex_unlock(&cgms_only_lock);

	if (enabled) {
		model_thread_set_run_state(SIM_RUN_RUNNING);
	} else {
		model_thread_set_run_state(SIM_RUN_STOPPED);
	}
}

bool model_thread_get_cgms_only(void)
{
	k_mutex_lock(&cgms_only_lock, K_FOREVER);
	bool enabled = cgms_only;
	k_mutex_unlock(&cgms_only_lock);

	return enabled;
}

static void model_thread_entry(void *p1, void *p2, void *p3)
{
	ARG_UNUSED(p1);
	ARG_UNUSED(p2);
	ARG_UNUSED(p3);

	while (1) {
		k_mutex_lock(&cfg_lock, K_FOREVER);
		if (cfg_pending) {
			apply_config_locked(&pending_cfg);
			cfg_pending = false;
		}
		k_mutex_unlock(&cfg_lock);

		uint8_t state = model_thread_get_run_state();

		if (state == SIM_RUN_RUNNING) {
			model_tick();
		} else {
			printk("model_thread: idle (run_state=%u)\n", state);
		}

		k_sleep(K_SECONDS(1));
	}
}

void model_thread_start(const struct sim_config *cfg)
{
	k_mutex_lock(&cfg_lock, K_FOREVER);
	pending_cfg = *cfg;
	cfg_pending = true;
	k_mutex_unlock(&cfg_lock);

	k_thread_create(&model_thread_data, model_thread_stack, MODEL_THREAD_STACK_SIZE,
			 model_thread_entry, NULL, NULL, NULL, MODEL_THREAD_PRIORITY, 0,
			 K_NO_WAIT);
	k_thread_name_set(&model_thread_data, "model_thread");
}

void model_thread_apply_config(const struct sim_config *cfg)
{
	k_mutex_lock(&cfg_lock, K_FOREVER);
	pending_cfg = *cfg;
	cfg_pending = true;
	k_mutex_unlock(&cfg_lock);
}

void model_thread_take_measurement(int slot, struct model_measurement *out)
{
	if (slot < 0 || slot >= MAX_SIM_SENSORS) {
		memset(out, 0, sizeof(*out));
		return;
	}
	k_mutex_lock(&meas_lock, K_FOREVER);
	*out = latest[slot];
	latest[slot].valid = 0;
	k_mutex_unlock(&meas_lock);
}

static int clamp_slot(int slot)
{
	return (slot < 0) ? 0 : (slot >= MAX_SIM_SENSORS ? MAX_SIM_SENSORS - 1 : slot);
}

void model_thread_add_instant_food(int slot, uint16_t duration_min, float carbs_g)
{
	double duration = duration_min > 0 ? (double)duration_min : 1.0;

	slot = clamp_slot(slot);
	k_mutex_lock(&instant_lock, K_FOREVER);
	for (int i = 0; i < MAX_INSTANT_EVENTS; i++) {
		if (!instant_food[slot][i].active) {
			instant_food[slot][i].active = true;
			instant_food[slot][i].delivered = false;
			instant_food[slot][i].remaining_min = duration;
			instant_food[slot][i].duration_min = duration;
			instant_food[slot][i].carbs_g = (double)carbs_g;
			k_mutex_unlock(&instant_lock);
			printk("model_thread: slot %d instant food, duration_min=%u carbs_g=%.2f\n",
			       slot, duration_min, (double)carbs_g);
			return;
		}
	}
	k_mutex_unlock(&instant_lock);
	printk("model_thread: slot %d instant food dropped, no free slot\n", slot);
}

void model_thread_add_instant_exercise(int slot, uint16_t duration_min, float intensity_pct)
{
	double duration = duration_min > 0 ? (double)duration_min : 1.0;

	slot = clamp_slot(slot);
	k_mutex_lock(&instant_lock, K_FOREVER);
	for (int i = 0; i < MAX_INSTANT_EVENTS; i++) {
		if (!instant_exercise[slot][i].active) {
			instant_exercise[slot][i].active = true;
			instant_exercise[slot][i].remaining_min = duration;
			instant_exercise[slot][i].intensity_pct = (double)intensity_pct;
			k_mutex_unlock(&instant_lock);
			printk("model_thread: slot %d instant exercise, duration_min=%u intensity=%.1f\n",
			       slot, duration_min, (double)intensity_pct);
			return;
		}
	}
	k_mutex_unlock(&instant_lock);
	printk("model_thread: slot %d instant exercise dropped, no free slot\n", slot);
}

void model_thread_add_instant_pisa(int slot, uint16_t duration_min, float depth_frac)
{
	double duration = duration_min > 0 ? (double)duration_min : 1.0;
	double depth = (double)depth_frac;

	depth = (depth < 0.0) ? 0.0 : (depth > 1.0 ? 1.0 : depth);
	slot = clamp_slot(slot);
	k_mutex_lock(&instant_lock, K_FOREVER);
	for (int i = 0; i < MAX_INSTANT_EVENTS; i++) {
		if (!instant_pisa[slot][i].active) {
			instant_pisa[slot][i].active = true;
			instant_pisa[slot][i].remaining_min = duration;
			instant_pisa[slot][i].duration_min = duration;
			instant_pisa[slot][i].depth = depth;
			k_mutex_unlock(&instant_lock);
			printk("model_thread: slot %d instant PISA, duration_min=%u depth=%.2f\n",
			       slot, duration_min, depth);
			return;
		}
	}
	k_mutex_unlock(&instant_lock);
	printk("model_thread: slot %d instant PISA dropped, no free slot\n", slot);
}
