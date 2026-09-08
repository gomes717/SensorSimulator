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
#include "csv_store.h"
#include "dexcom_service.h"

/* Defined in main.c — re-advertise under the given SIM_COMM_* profile. */
void main_apply_comm_profile(uint8_t profile);

#define COMM_THREAD_STACK_SIZE 4096
#define COMM_THREAD_PRIORITY   5
/* Deeper than the config path needs (8) because a CSV upload bursts a run of
 * CSV_DATA writes through here back to back. */
#define CFG_MSGQ_DEPTH         16
#define MEASUREMENT_RETRY_COUNT 3

/* Big enough for a CSV_DATA chunk (u32 offset + bytes) at ATT MTU 247, and
 * still covers every config payload (person config, 137 B, is next largest). */
#define CFG_MSG_MAX_DATA 244

struct cfg_msg {
	enum cfg_msg_type type;
	uint16_t len;
	uint8_t data[CFG_MSG_MAX_DATA];
};

/* CSV control opcodes — first byte of a CFG_MSG_CSV_CONTROL payload. Must match
 * api/protocol.py's encode_csv_* helpers. */
#define CSV_OP_BEGIN  0x01
#define CSV_OP_COMMIT 0x02
#define CSV_OP_ABORT  0x03
#define CSV_OP_CLEAR  0x04
#define CSV_OP_STATUS 0x05

struct csv_begin_wire {
	uint8_t op;
	uint8_t track;
	uint16_t row_count;
	uint32_t base_epoch_s;
	uint16_t interval_s;
	uint32_t total_bytes;
	uint32_t crc32;
} __packed;

struct csv_track_op_wire {
	uint8_t op;
	uint8_t track;
} __packed;

struct csv_data_wire {
	uint32_t offset;
	uint8_t data[]; /* remaining bytes of the message */
} __packed;

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

static void apply_data_source_msg(const uint8_t *data, uint16_t len)
{
	if (len < 1) {
		return;
	}
	k_mutex_lock(&working_cfg_lock, K_FOREVER);
	working_cfg.data_source = (data[0] == SIM_DATA_CSV) ? SIM_DATA_CSV : SIM_DATA_MODEL;
	k_mutex_unlock(&working_cfg_lock);
}

static void apply_speed_msg(const uint8_t *data, uint16_t len)
{
	struct speed_wire w;

	if (len < sizeof(w)) {
		return;
	}
	memcpy(&w, data, sizeof(w));
	if (!(w.mult >= SIM_SPEED_MIN && w.mult <= SIM_SPEED_MAX)) {
		w.mult = (w.mult < SIM_SPEED_MIN) ? SIM_SPEED_MIN : SIM_SPEED_MAX;
	}
	k_mutex_lock(&working_cfg_lock, K_FOREVER);
	working_cfg.speed_mult = w.mult;
	k_mutex_unlock(&working_cfg_lock);
}

static void apply_comm_profile_msg(const uint8_t *data, uint16_t len)
{
	if (len < 1) {
		return;
	}
	uint8_t p = (data[0] == SIM_COMM_DEXCOM) ? SIM_COMM_DEXCOM : SIM_COMM_SIG_CGMS;

	k_mutex_lock(&working_cfg_lock, K_FOREVER);
	working_cfg.comm_profile = p;
	k_mutex_unlock(&working_cfg_lock);
	main_apply_comm_profile(p);
}

/* CFG_MSG_CSV_CONTROL — opcode-tagged, handled straight through csv_store_*
 * (erase/commit run here, off the BT host context, like sim_config_save_to_flash).
 * BEGIN/COMMIT/STATUS reply on the CSV control notify. */
static void process_csv_control(const uint8_t *data, uint16_t len)
{
	if (len < 1) {
		return;
	}

	switch (data[0]) {
	case CSV_OP_BEGIN: {
		struct csv_begin_wire w;

		if (len < sizeof(w)) {
			config_service_notify_csv_control(CSV_CTRL_STATUS_ERR, 0);
			return;
		}
		memcpy(&w, data, sizeof(w));
		struct csv_upload_header hdr = {
			.track = w.track,
			.row_count = w.row_count,
			.base_epoch_s = w.base_epoch_s,
			.interval_s = w.interval_s,
			.total_bytes = w.total_bytes,
			.crc32 = w.crc32,
		};
		int err = csv_store_begin(&hdr);

		config_service_notify_csv_control(
			err ? CSV_CTRL_STATUS_ERR : CSV_CTRL_STATUS_OK, csv_store_received());
		break;
	}
	case CSV_OP_COMMIT: {
		struct csv_track_op_wire w;

		if (len < sizeof(w)) {
			config_service_notify_csv_control(CSV_CTRL_STATUS_ERR, 0);
			return;
		}
		memcpy(&w, data, sizeof(w));
		int err = csv_store_commit(w.track, NULL);

		config_service_notify_csv_control(
			err ? CSV_CTRL_STATUS_ERR : CSV_CTRL_STATUS_OK, csv_store_received());
		break;
	}
	case CSV_OP_ABORT:
		csv_store_abort();
		config_service_notify_csv_control(CSV_CTRL_STATUS_OK, 0);
		break;
	case CSV_OP_CLEAR: {
		struct csv_track_op_wire w;

		if (len < sizeof(w)) {
			config_service_notify_csv_control(CSV_CTRL_STATUS_ERR, 0);
			return;
		}
		memcpy(&w, data, sizeof(w));
		int err = csv_store_clear(w.track);

		config_service_notify_csv_control(
			err ? CSV_CTRL_STATUS_ERR : CSV_CTRL_STATUS_OK, 0);
		break;
	}
	case CSV_OP_STATUS:
		config_service_notify_csv_control(CSV_CTRL_STATUS_OK, csv_store_received());
		break;
	default:
		config_service_notify_csv_control(CSV_CTRL_STATUS_ERR, 0);
		break;
	}
}

static void process_csv_data(const uint8_t *data, uint16_t len)
{
	struct csv_data_wire hdr;

	if (len <= sizeof(hdr)) {
		return;
	}
	memcpy(&hdr, data, sizeof(hdr));
	csv_store_write(hdr.offset, data + sizeof(hdr), len - sizeof(hdr));
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

	if (msg->type == CFG_MSG_PISA_INSTANT) {
		struct pisa_instant_wire wire;

		if (msg->len >= sizeof(wire)) {
			memcpy(&wire, msg->data, sizeof(wire));
			model_thread_add_instant_pisa(wire.duration_min, wire.depth_frac);
		}
		return;
	}

	if (msg->type == CFG_MSG_CGMS_ONLY) {
		if (msg->len >= 1) {
			model_thread_set_cgms_only(msg->data[0] != 0);
		}
		return;
	}

	if (msg->type == CFG_MSG_CSV_CONTROL) {
		process_csv_control(msg->data, msg->len);
		return;
	}

	if (msg->type == CFG_MSG_CSV_DATA) {
		process_csv_data(msg->data, msg->len);
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
	case CFG_MSG_DATA_SOURCE:
		apply_data_source_msg(msg->data, msg->len);
		break;
	case CFG_MSG_SPEED:
		apply_speed_msg(msg->data, msg->len);
		break;
	case CFG_MSG_COMM_PROFILE:
		apply_comm_profile_msg(msg->data, msg->len);
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
	k_mutex_lock(&working_cfg_lock, K_FOREVER);
	uint8_t comm_profile = working_cfg.comm_profile;
	k_mutex_unlock(&working_cfg_lock);

	if (meas.valid) {
		printk("comm_thread: fresh reading glucose=%.2f profile=%s\n",
		       meas.glucose_mg_dl, comm_profile == SIM_COMM_DEXCOM ? "dexcom" : "sig");
	}

	if (meas.valid && comm_profile == SIM_COMM_DEXCOM) {
		int err = dexcom_service_notify_glucose((uint16_t)(meas.glucose_mg_dl + 0.5f), 0);

		if (err == 0) {
			printk("comm_thread: pushed dexcom glucose=%.2f\n", meas.glucose_mg_dl);
		}
	} else if (meas.valid && g_cgms) {
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
