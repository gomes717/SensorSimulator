#include <math.h>
#include <string.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/printk.h>

#include "model_thread.h"
#include "config_service.h"
#include "models/cgmsim_cambridge.h"
#include "models/cgmsim_uva_padova.h"
#include "models/cgmsim_royparker.h"
#include "models/cgmsim_deichmann.h"
#include "models/cgmsim_sensors.h"

/* Every model's Params struct is a plain struct-of-doubles whose field order
 * matches its PARAM_NAMES list in SensorSimulator/src/models/*.py exactly
 * (see PROTOCOL_SPEC.md) — these BUILD_ASSERTs guard the "walk it as a
 * double[]" trick used throughout this file to convert the wire format's
 * float[] into each native double Params struct without hand-listing every
 * field twice. */
BUILD_ASSERT(sizeof(CambridgeParams) == 17 * sizeof(double), "CambridgeParams field count");
BUILD_ASSERT(sizeof(UvaPadovaParams) == 33 * sizeof(double), "UvaPadovaParams field count");
BUILD_ASSERT(sizeof(RoyParkerParams) == 22 * sizeof(double), "RoyParkerParams field count");
BUILD_ASSERT(sizeof(DeichmannParams) == 22 * sizeof(double), "DeichmannParams field count");

#define MODEL_THREAD_STACK_SIZE 4096
#define MODEL_THREAD_PRIORITY   5
#define MINUTES_PER_DAY         1440.0

static K_THREAD_STACK_DEFINE(model_thread_stack, MODEL_THREAD_STACK_SIZE);
static struct k_thread model_thread_data;

/* Config handoff from comm_thread -> model_thread (see apply below). */
static K_MUTEX_DEFINE(cfg_lock);
static struct sim_config pending_cfg;
static bool cfg_pending;

/* Published measurement, read by comm_thread. */
static K_MUTEX_DEFINE(meas_lock);
static struct model_measurement latest;

/* Everything below is only ever touched by the model thread itself. */
static struct sim_config active_cfg;
static double basal_u_per_h;
static double sim_clock_min;
static int last_fired_day[MAX_EVENTS];

/* Instant (one-shot, non-persisted) food/exercise events — added by
 * comm_thread via model_thread_add_instant_{food,exercise}() below,
 * consumed each tick by model_tick(). Protected by instant_lock since
 * "add" is called from comm_thread while model_tick() (model thread) reads
 * and decays them. */
#define MAX_INSTANT_EVENTS 8

struct instant_food_slot {
	bool active;
	bool delivered; /* impulse-fed models: whether the one-shot pulse has fired yet */
	double remaining_min;
	double duration_min;
	double carbs_g;
};

struct instant_exercise_slot {
	bool active;
	double remaining_min;
	double intensity_pct;
};

static K_MUTEX_DEFINE(instant_lock);
static struct instant_food_slot instant_food[MAX_INSTANT_EVENTS];
static struct instant_exercise_slot instant_exercise[MAX_INSTANT_EVENTS];

static union {
	CambridgeState cambridge;
	UvaPadovaState uva_padova;
	RoyParkerState royparker;
	DeichmannState deichmann;
} model_state;

static union {
	CambridgeParams cambridge;
	UvaPadovaParams uva_padova;
	RoyParkerParams royparker;
	DeichmannParams deichmann;
} model_params;

static union {
	BretonState breton;
	FacchinetttiState facchinetti;
} sensor_state;

static bool model_is_rate_fed(uint8_t model_id)
{
	return model_id == SIM_MODEL_CAMBRIDGE || model_id == SIM_MODEL_UVA_PADOVA;
}

/* True if the recurring-daily clock time_of_day_min falls in
 * [start_min, start_min + duration_min) modulo one day. */
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

static void apply_config_locked(const struct sim_config *cfg)
{
	active_cfg = *cfg;

	switch (active_cfg.model_id) {
	case SIM_MODEL_UVA_PADOVA: {
		double *dst = (double *)&model_params.uva_padova;
		size_t n = sizeof(UvaPadovaParams) / sizeof(double);

		for (size_t i = 0; i < n; i++) {
			dst[i] = (double)active_cfg.model_params[i];
		}
		uva_padova_init(&model_state.uva_padova, &model_params.uva_padova);
		basal_u_per_h = uva_padova_basal_iir_u_per_h(&model_params.uva_padova);
		break;
	}
	case SIM_MODEL_ROYPARKER: {
		double *dst = (double *)&model_params.royparker;
		size_t n = sizeof(RoyParkerParams) / sizeof(double);

		for (size_t i = 0; i < n; i++) {
			dst[i] = (double)active_cfg.model_params[i];
		}
		royparker_init(&model_state.royparker, &model_params.royparker);
		basal_u_per_h = model_params.royparker.u1b;
		break;
	}
	case SIM_MODEL_DEICHMANN: {
		double *dst = (double *)&model_params.deichmann;
		size_t n = sizeof(DeichmannParams) / sizeof(double);

		for (size_t i = 0; i < n; i++) {
			dst[i] = (double)active_cfg.model_params[i];
		}
		deichmann_init(&model_state.deichmann, &model_params.deichmann);
		basal_u_per_h = model_params.deichmann.IIRb;
		printk("model_thread: deichmann params Gpeq=%.2f BW=%.2f Gb=%.2f Ib=%.2f "
		       "HRb=%.2f p1=%.6f p2=%.6f p3=%.9f IIRb=%.4f | init glucose=%.2f Ic=%.4f "
		       "x1=%.4f x2=%.4f X=%.6f\n",
		       model_params.deichmann.Gpeq, model_params.deichmann.BW,
		       model_params.deichmann.Gb, model_params.deichmann.Ib,
		       model_params.deichmann.HRb, model_params.deichmann.p1,
		       model_params.deichmann.p2, model_params.deichmann.p3,
		       model_params.deichmann.IIRb, deichmann_glucose_mg_dl(&model_state.deichmann),
		       model_state.deichmann.Ic, model_state.deichmann.x1, model_state.deichmann.x2,
		       model_state.deichmann.X);
		break;
	}
	case SIM_MODEL_CAMBRIDGE:
	default: {
		double *dst = (double *)&model_params.cambridge;
		size_t n = sizeof(CambridgeParams) / sizeof(double);

		for (size_t i = 0; i < n; i++) {
			dst[i] = (double)active_cfg.model_params[i];
		}
		cambridge_init(&model_state.cambridge, &model_params.cambridge);
		basal_u_per_h = cambridge_basal_iir_u_per_h(&model_params.cambridge);
		break;
	}
	}

	switch (active_cfg.sensor_id) {
	case SIM_SENSOR_BRETON:
		breton_init(&sensor_state.breton, k_uptime_get_32());
		sensor_state.breton.pacf = active_cfg.sensor_params[0];
		sensor_state.breton.sigma = active_cfg.sensor_params[1];
		sensor_state.breton.alpha = active_cfg.sensor_params[2];
		sensor_state.breton.beta = active_cfg.sensor_params[3];
		break;
	case SIM_SENSOR_FACCHINETTI:
		facchinetti_init(&sensor_state.facchinetti, k_uptime_get_32());
		sensor_state.facchinetti.a0 = active_cfg.sensor_params[0];
		sensor_state.facchinetti.a1 = active_cfg.sensor_params[1];
		sensor_state.facchinetti.a2 = active_cfg.sensor_params[2];
		sensor_state.facchinetti.b0 = active_cfg.sensor_params[3];
		sensor_state.facchinetti.b1 = active_cfg.sensor_params[4];
		sensor_state.facchinetti.b2 = active_cfg.sensor_params[5];
		sensor_state.facchinetti.aw1 = active_cfg.sensor_params[6];
		sensor_state.facchinetti.aw2 = active_cfg.sensor_params[7];
		sensor_state.facchinetti.sigma_v = active_cfg.sensor_params[8];
		sensor_state.facchinetti.ac1 = active_cfg.sensor_params[9];
		sensor_state.facchinetti.ac2 = active_cfg.sensor_params[10];
		sensor_state.facchinetti.sigma_c = active_cfg.sensor_params[11];
		break;
	case SIM_SENSOR_IDEAL:
	default:
		/* No state to initialize — ideal_cgm_update() is stateless. */
		break;
	}

	sim_clock_min = 0.0;
	for (int i = 0; i < MAX_EVENTS; i++) {
		last_fired_day[i] = -1;
	}

	/* Instant food/exercise events are added outside this reset path (see
	 * model_thread_add_instant_food/exercise()), so a reset must clear
	 * them explicitly — otherwise a still-active slot survives a STOPPED
	 * reset and bleeds its remaining carbs/intensity into the next run. */
	k_mutex_lock(&instant_lock, K_FOREVER);
	memset(instant_food, 0, sizeof(instant_food));
	memset(instant_exercise, 0, sizeof(instant_exercise));
	k_mutex_unlock(&instant_lock);

	printk("model_thread: applied config (model_id=%u, sensor_id=%u, mode=%u)\n",
	       active_cfg.model_id, active_cfg.sensor_id, active_cfg.mode);

	/* Fired the instant the reset actually takes effect — the app anchors
	 * its own t=0 to this notification's arrival instead of to whenever it
	 * sent the write that caused it (BLE round-trip + processing latency
	 * otherwise shows up as a visible phase shift, worse in fast mode), and
	 * also uses its arrival as "the board actually applied what I sent"
	 * confirmation for the config windows' Send to Board buttons. */
	config_service_notify_reset_sync();
}

static double active_hr_baseline(void)
{
	return (active_cfg.model_id == SIM_MODEL_DEICHMANN) ? model_params.deichmann.HRb : 80.0;
}

static void model_tick(void)
{
	double dt_min = (active_cfg.mode == SIM_MODE_FAST) ? 1.0 : (1.0 / 60.0);
	double time_of_day = fmod(sim_clock_min, MINUTES_PER_DAY);
	int day_index = (int)(sim_clock_min / MINUTES_PER_DAY);
	bool rate_fed = model_is_rate_fed(active_cfg.model_id);

	double carbs = 0.0;

	for (int i = 0; i < active_cfg.food_count; i++) {
		const struct food_event *ev = &active_cfg.food[i];

		if (rate_fed) {
			if (in_daily_window(time_of_day, ev->time_min, ev->duration_min)) {
				carbs += ev->carbs_g / ev->duration_min;
			}
		} else if (last_fired_day[i] != day_index &&
			   in_daily_window(time_of_day, ev->time_min, dt_min)) {
			carbs += ev->carbs_g;
			last_fired_day[i] = day_index;
		}
	}

	double exercise_pct = 0.0;
	double hr_bpm = active_hr_baseline();

	for (int i = 0; i < active_cfg.exercise_count; i++) {
		const struct exercise_event *ev = &active_cfg.exercise[i];

		if (in_daily_window(time_of_day, ev->time_min, ev->duration_min)) {
			exercise_pct = ev->intensity_pct;
			hr_bpm = active_hr_baseline() + ev->intensity_pct / 100.0 * 80.0;
			break;
		}
	}

	/* Instant (one-shot, non-recurring) food/exercise events — see
	 * model_thread_add_instant_food/exercise() and PROTOCOL_SPEC.md.
	 * Added on top of whatever the recurring schedule above already gives;
	 * decayed here rather than reset, since these never go through
	 * apply_config_locked(). */
	k_mutex_lock(&instant_lock, K_FOREVER);
	for (int i = 0; i < MAX_INSTANT_EVENTS; i++) {
		struct instant_food_slot *slot = &instant_food[i];

		if (!slot->active) {
			continue;
		}
		if (rate_fed) {
			carbs += slot->carbs_g / slot->duration_min;
		} else if (!slot->delivered) {
			carbs += slot->carbs_g;
			slot->delivered = true;
		}
		slot->remaining_min -= dt_min;
		if (slot->remaining_min <= 0.0) {
			slot->active = false;
		}
	}
	for (int i = 0; i < MAX_INSTANT_EVENTS; i++) {
		struct instant_exercise_slot *slot = &instant_exercise[i];

		if (!slot->active) {
			continue;
		}
		if (slot->intensity_pct > exercise_pct) {
			exercise_pct = slot->intensity_pct;
			hr_bpm = active_hr_baseline() + slot->intensity_pct / 100.0 * 80.0;
		}
		slot->remaining_min -= dt_min;
		if (slot->remaining_min <= 0.0) {
			slot->active = false;
		}
	}
	k_mutex_unlock(&instant_lock);

	double glucose;

	switch (active_cfg.model_id) {
	case SIM_MODEL_UVA_PADOVA:
		uva_padova_step(&model_state.uva_padova, &model_params.uva_padova, carbs,
				 basal_u_per_h / 60.0, dt_min);
		glucose = uva_padova_glucose_mg_dl(&model_state.uva_padova, &model_params.uva_padova);
		break;
	case SIM_MODEL_ROYPARKER:
		royparker_step(&model_state.royparker, &model_params.royparker, carbs,
				basal_u_per_h, exercise_pct, sim_clock_min, dt_min);
		glucose = royparker_glucose_mg_dl(&model_state.royparker);
		break;
	case SIM_MODEL_DEICHMANN:
		deichmann_step(&model_state.deichmann, &model_params.deichmann, carbs,
				basal_u_per_h, hr_bpm, dt_min);
		glucose = deichmann_glucose_mg_dl(&model_state.deichmann);
		printk("deichmann_state: Ic=%.4f Ib=%.2f X=%.6f x1=%.4f x2=%.4f HRint=%.4f "
		       "Y=%.4f Z=%.4f basal=%.4f\n",
		       model_state.deichmann.Ic, model_params.deichmann.Ib, model_state.deichmann.X,
		       model_state.deichmann.x1, model_state.deichmann.x2,
		       model_state.deichmann.HRint, model_state.deichmann.Y, model_state.deichmann.Z,
		       basal_u_per_h);
		break;
	case SIM_MODEL_CAMBRIDGE:
	default:
		cambridge_step(&model_state.cambridge, &model_params.cambridge, carbs,
				basal_u_per_h, dt_min);
		glucose = cambridge_glucose_mg_dl(&model_state.cambridge, &model_params.cambridge);
		break;
	}

	double carbs_rate_display = rate_fed ? carbs : (carbs > 0.0 ? carbs / dt_min : 0.0);

	CGMReading reading;

	switch (active_cfg.sensor_id) {
	case SIM_SENSOR_BRETON:
		reading = breton_update(&sensor_state.breton, glucose, dt_min);
		break;
	case SIM_SENSOR_FACCHINETTI:
		reading = facchinetti_update(&sensor_state.facchinetti, glucose, dt_min);
		break;
	case SIM_SENSOR_IDEAL:
	default:
		reading = ideal_cgm_update(glucose);
		break;
	}

	k_mutex_lock(&meas_lock, K_FOREVER);
	if (reading.valid) {
		latest.glucose_mg_dl = (float)reading.value_mg_dl;
		latest.valid = 1;
	}
	latest.carbs_g_per_min = (float)carbs_rate_display;
	latest.exercise_pct = (float)exercise_pct;
	k_mutex_unlock(&meas_lock);

	printk("model_tick: t_sim=%.2fmin dt=%.4f mode=%u model=%u sensor=%u glucose=%.2f "
	       "reading=%.2f(valid=%d) carbs=%.3f ex=%.1f\n",
	       sim_clock_min, dt_min, active_cfg.mode, active_cfg.model_id, active_cfg.sensor_id,
	       glucose, reading.value_mg_dl, reading.valid, carbs_rate_display, exercise_pct);

	sim_clock_min += dt_min;
}

static K_MUTEX_DEFINE(run_state_lock);
static uint8_t run_state = SIM_RUN_RUNNING; /* autonomous by default, see header */

void model_thread_set_run_state(uint8_t state)
{
	printk("model_thread: run_state -> %u (0=stopped 1=running 2=paused)\n", state);

	if (state == SIM_RUN_STOPPED) {
		/* Re-feed the currently active config through the same
		 * apply/reinit path a real config change uses — cheapest way
		 * to get a full state + sim_clock_min reset without a second
		 * copy of the reinit logic. Takes effect next tick, same as
		 * model_thread_apply_config(). */
		k_mutex_lock(&cfg_lock, K_FOREVER);
		pending_cfg = active_cfg;
		cfg_pending = true;
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
		/* Ensure the simulation is actually ticking — no reset. */
		model_thread_set_run_state(SIM_RUN_RUNNING);
	} else {
		/* Reset and hold; the app must send an explicit RUNNING
		 * write to resume, same as any other STOPPED transition. */
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

void model_thread_take_measurement(struct model_measurement *out)
{
	k_mutex_lock(&meas_lock, K_FOREVER);
	*out = latest;
	latest.valid = 0;
	k_mutex_unlock(&meas_lock);
}

void model_thread_add_instant_food(uint16_t duration_min, float carbs_g)
{
	double duration = duration_min > 0 ? (double)duration_min : 1.0;

	k_mutex_lock(&instant_lock, K_FOREVER);
	for (int i = 0; i < MAX_INSTANT_EVENTS; i++) {
		if (!instant_food[i].active) {
			instant_food[i].active = true;
			instant_food[i].delivered = false;
			instant_food[i].remaining_min = duration;
			instant_food[i].duration_min = duration;
			instant_food[i].carbs_g = (double)carbs_g;
			k_mutex_unlock(&instant_lock);
			printk("model_thread: instant food added, duration_min=%u carbs_g=%.2f\n",
			       duration_min, (double)carbs_g);
			return;
		}
	}
	k_mutex_unlock(&instant_lock);
	printk("model_thread: instant food dropped, no free slot (MAX_INSTANT_EVENTS=%d)\n",
	       MAX_INSTANT_EVENTS);
}

void model_thread_add_instant_exercise(uint16_t duration_min, float intensity_pct)
{
	double duration = duration_min > 0 ? (double)duration_min : 1.0;

	k_mutex_lock(&instant_lock, K_FOREVER);
	for (int i = 0; i < MAX_INSTANT_EVENTS; i++) {
		if (!instant_exercise[i].active) {
			instant_exercise[i].active = true;
			instant_exercise[i].remaining_min = duration;
			instant_exercise[i].intensity_pct = (double)intensity_pct;
			k_mutex_unlock(&instant_lock);
			printk("model_thread: instant exercise added, duration_min=%u intensity_pct=%.1f\n",
			       duration_min, (double)intensity_pct);
			return;
		}
	}
	k_mutex_unlock(&instant_lock);
	printk("model_thread: instant exercise dropped, no free slot (MAX_INSTANT_EVENTS=%d)\n",
	       MAX_INSTANT_EVENTS);
}
