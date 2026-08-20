#include <string.h>
#include <errno.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/printk.h>
#include <bluetooth/services/cgms.h>
#include <sfloat.h>

#include "comm_thread.h"
#include "model_thread.h"
#include "config_service.h"
#include "sim_config.h"

#define COMM_THREAD_STACK_SIZE 4096
#define COMM_THREAD_PRIORITY   5
#define CFG_MSGQ_DEPTH         8
#define MEASUREMENT_RETRY_COUNT 3

struct cfg_msg {
	enum cfg_msg_type type;
	uint16_t len;
	uint8_t data[sizeof(struct person_config_wire)]; /* largest payload of any msg type */
};

K_MSGQ_DEFINE(cfg_msgq, sizeof(struct cfg_msg), CFG_MSGQ_DEPTH, 4);

static K_THREAD_STACK_DEFINE(comm_thread_stack, COMM_THREAD_STACK_SIZE);
static struct k_thread comm_thread_data;

static struct bt_cgms *g_cgms;
static K_MUTEX_DEFINE(working_cfg_lock);
static struct sim_config working_cfg;

int comm_thread_enqueue_config(enum cfg_msg_type type, const void *data, uint16_t len)
{
	struct cfg_msg msg = {0};

	if (len > sizeof(msg.data)) {
		return -EINVAL;
	}
	msg.type = type;
	msg.len = len;
	memcpy(msg.data, data, len);

	return (k_msgq_put(&cfg_msgq, &msg, K_NO_WAIT) == 0) ? 0 : -ENOMSG;
}

void comm_thread_copy_config(struct sim_config *out)
{
	k_mutex_lock(&working_cfg_lock, K_FOREVER);
	*out = working_cfg;
	k_mutex_unlock(&working_cfg_lock);
}

static void apply_person_msg(const uint8_t *data, uint16_t len)
{
	struct person_config_wire wire;

	if (len < sizeof(wire)) {
		return;
	}
	memcpy(&wire, data, sizeof(wire));

	k_mutex_lock(&working_cfg_lock, K_FOREVER);
	working_cfg.model_id = wire.model_id;
	memcpy(working_cfg.model_params, wire.params, sizeof(wire.params));
	k_mutex_unlock(&working_cfg_lock);
}

static void apply_sensor_msg(const uint8_t *data, uint16_t len)
{
	struct sensor_config_wire wire;

	if (len < sizeof(wire)) {
		return;
	}
	memcpy(&wire, data, sizeof(wire));

	k_mutex_lock(&working_cfg_lock, K_FOREVER);
	working_cfg.sensor_id = wire.sensor_id;
	memcpy(working_cfg.sensor_params, wire.params, sizeof(wire.params));
	k_mutex_unlock(&working_cfg_lock);
}

static void apply_mode_msg(const uint8_t *data, uint16_t len)
{
	if (len < 1) {
		return;
	}
	k_mutex_lock(&working_cfg_lock, K_FOREVER);
	working_cfg.mode = data[0];
	k_mutex_unlock(&working_cfg_lock);
}

static void apply_food_event_msg(const uint8_t *data, uint16_t len)
{
	struct food_event ev;

	if (len < sizeof(ev)) {
		return;
	}
	memcpy(&ev, data, sizeof(ev));

	k_mutex_lock(&working_cfg_lock, K_FOREVER);
	if (ev.time_min == 0xFFFF) {
		working_cfg.food_count = 0;
	} else if (working_cfg.food_count < MAX_EVENTS) {
		working_cfg.food[working_cfg.food_count++] = ev;
	}
	k_mutex_unlock(&working_cfg_lock);
}

static void apply_exercise_event_msg(const uint8_t *data, uint16_t len)
{
	struct exercise_event ev;

	if (len < sizeof(ev)) {
		return;
	}
	memcpy(&ev, data, sizeof(ev));

	k_mutex_lock(&working_cfg_lock, K_FOREVER);
	if (ev.time_min == 0xFFFF) {
		working_cfg.exercise_count = 0;
	} else if (working_cfg.exercise_count < MAX_EVENTS) {
		working_cfg.exercise[working_cfg.exercise_count++] = ev;
	}
	k_mutex_unlock(&working_cfg_lock);
}

static void process_cfg_msg(const struct cfg_msg *msg)
{
	printk("comm_thread: cfg write received, type=%d len=%u\n", msg->type, msg->len);

	if (msg->type == CFG_MSG_RUN_STATE) {
		/* Live session control, not part of sim_config — apply directly,
		 * skip the flash-save path below entirely. */
		if (msg->len >= 1) {
			model_thread_set_run_state(msg->data[0]);
		}
		return;
	}

	if (msg->type == CFG_MSG_FOOD_INSTANT) {
		/* Not part of sim_config, and deliberately NOT routed through
		 * model_thread_apply_config() below — that would reset
		 * sim_clock_min/model state, defeating the point of an instant,
		 * non-disruptive mid-run injection. */
		struct food_instant_wire wire;

		if (msg->len >= sizeof(wire)) {
			memcpy(&wire, msg->data, sizeof(wire));
			model_thread_add_instant_food(wire.duration_min, wire.carbs_g);
		}
		return;
	}

	if (msg->type == CFG_MSG_EXERCISE_INSTANT) {
		struct exercise_instant_wire wire;

		if (msg->len >= sizeof(wire)) {
			memcpy(&wire, msg->data, sizeof(wire));
			model_thread_add_instant_exercise(wire.duration_min, wire.intensity_pct);
		}
		return;
	}

	if (msg->type == CFG_MSG_CGMS_ONLY) {
		if (msg->len >= 1) {
			model_thread_set_cgms_only(msg->data[0] != 0);
		}
		return;
	}

	switch (msg->type) {
	case CFG_MSG_PERSON:
		apply_person_msg(msg->data, msg->len);
		break;
	case CFG_MSG_SENSOR:
		apply_sensor_msg(msg->data, msg->len);
		break;
	case CFG_MSG_MODE:
		apply_mode_msg(msg->data, msg->len);
		break;
	case CFG_MSG_FOOD_EVENT:
		apply_food_event_msg(msg->data, msg->len);
		break;
	case CFG_MSG_EXERCISE_EVENT:
		apply_exercise_event_msg(msg->data, msg->len);
		break;
	}

	struct sim_config snapshot;

	comm_thread_copy_config(&snapshot);
	sim_config_save_to_flash(&snapshot);
	model_thread_apply_config(&snapshot);
}

static void push_measurement_and_status(void)
{
	struct model_measurement meas;

	model_thread_take_measurement(&meas);

	/* Deliberately NOT gated on session_active here: that flag mirrors the
	 * CGMS library's own "collector formally started a session" concept,
	 * which fires once (at bt_cgms_init, before any client ever connects)
	 * and is never re-armed per reconnect — main.c's disconnected()
	 * callback clears it on every disconnect, so after the very first
	 * reconnect this would permanently block every future push, while the
	 * library's own periodic report_meas() work item kept re-notifying
	 * whatever stale record last got through. bt_cgms_measurement_add()
	 * already does its own (correct, per-call) session-stopped check
	 * internally, so gating on session_active here was redundant AND
	 * broken — see PROTOCOL_SPEC.md for how this was diagnosed. */
	if (meas.valid) {
		printk("comm_thread: fresh reading glucose=%.2f g_cgms=%p\n",
		       meas.glucose_mg_dl, (void *)g_cgms);
	}

	if (meas.valid && g_cgms) {
		struct bt_cgms_measurement result;
		int err = -1;

		result.glucose = sfloat_from_float(meas.glucose_mg_dl);

		for (int i = 0; i < MEASUREMENT_RETRY_COUNT; i++) {
			err = bt_cgms_measurement_add(g_cgms, result);
			if (err == 0) {
				printk("comm_thread: pushed measurement glucose=%.2f "
				       "(sfloat raw=0x%04x)\n",
				       meas.glucose_mg_dl, result.glucose.val);
				break;
			}
			printk("comm_thread: bt_cgms_measurement_add failed err=%d (attempt %d)\n",
			       err, i);
			k_sleep(K_SECONDS(1));
		}
		if (err) {
			printk("comm_thread: measurement submit failed, discarded\n");
		}
	}

	/* CGMS-only mode: stream nothing but standard CGM Measurement
	 * notifications (pushed above, unconditionally) — see
	 * model_thread.h's model_thread_set_cgms_only() comment. */
	if (model_thread_get_cgms_only()) {
		return;
	}

	uint8_t payload[8];

	memcpy(payload, &meas.carbs_g_per_min, sizeof(float));
	memcpy(payload + sizeof(float), &meas.exercise_pct, sizeof(float));
	config_service_notify_food_exercise_status(payload, sizeof(payload));
}

static void comm_thread_entry(void *p1, void *p2, void *p3)
{
	ARG_UNUSED(p1);
	ARG_UNUSED(p2);
	ARG_UNUSED(p3);

	while (1) {
		struct cfg_msg msg;
		int err = k_msgq_get(&cfg_msgq, &msg, K_MSEC(500));

		if (err == 0) {
			process_cfg_msg(&msg);
		}

		push_measurement_and_status();
	}
}

void comm_thread_start(struct bt_cgms *cgms, const struct sim_config *initial_cfg)
{
	g_cgms = cgms;

	k_mutex_lock(&working_cfg_lock, K_FOREVER);
	working_cfg = *initial_cfg;
	k_mutex_unlock(&working_cfg_lock);

	k_thread_create(&comm_thread_data, comm_thread_stack, COMM_THREAD_STACK_SIZE,
			 comm_thread_entry, NULL, NULL, NULL, COMM_THREAD_PRIORITY, 0,
			 K_NO_WAIT);
	k_thread_name_set(&comm_thread_data, "comm_thread");
}
