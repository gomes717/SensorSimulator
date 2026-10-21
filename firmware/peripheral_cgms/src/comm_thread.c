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
#include "dexcom_g6.h"
#include "dexcom_service.h"

/* Defined in main.c — switch the board mode (SIM_COMM_*): identities, advertising, LEDs. */
void main_apply_comm_profile(uint8_t profile);
/* Defined in main.c — the Dexcom transmitter ID changed: re-advertise under the new name. */
void main_apply_dexcom_id(void);
/* Defined in main.c — a new G6 reading was published: open the connect window. */
void main_g6_reading_published(void);

#define COMM_THREAD_STACK_SIZE 4096
#define COMM_THREAD_PRIORITY   5
/* Deep enough for a CSV upload burst AND a full multi-slot Board Layout push
 * (send_board_layout writes sensor_select + person/sensor/data_source/food/
 * exercise per slot, ~24 messages back to back). */
#define CFG_MSGQ_DEPTH         32
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
#define CSV_OP_READ   0x06

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

struct csv_read_op_wire {
	uint8_t op;
	uint8_t track;
	uint32_t offset;
	uint16_t len;
} __packed;

struct csv_data_wire {
	uint32_t offset;
	uint8_t data[]; /* remaining bytes of the message */
} __packed;

K_MSGQ_DEFINE(cfg_msgq, sizeof(struct cfg_msg), CFG_MSGQ_DEPTH, 4);

static K_THREAD_STACK_DEFINE(comm_thread_stack, COMM_THREAD_STACK_SIZE);
static struct k_thread comm_thread_data;

static struct bt_cgms *g_cgms[MAX_SIM_SENSORS];
static K_MUTEX_DEFINE(working_cfg_lock);
static struct sim_config working_cfg;
static uint8_t working_sel; /* sensor-select cursor for per-slot config writes/reads */

uint8_t comm_thread_selected_slot(void)
{
	return working_sel;
}

/* The slot subsequent per-sensor writes target. Held under working_cfg_lock by
 * callers below. */
static struct sensor_slot *sel_slot(void)
{
	return &working_cfg.slots[working_sel];
}

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

/* Read-path helpers for config_service.c: copy just the slice a GATT read
 * needs, so the ~2.85 KB struct sim_config never lands on the BT RX stack. */
void comm_thread_copy_selected_slot(struct sensor_slot *out)
{
	k_mutex_lock(&working_cfg_lock, K_FOREVER);
	*out = working_cfg.slots[working_sel];
	k_mutex_unlock(&working_cfg_lock);
}

void comm_thread_copy_selected_user_name(char out[SIM_USER_NAME_MAX])
{
	k_mutex_lock(&working_cfg_lock, K_FOREVER);
	memcpy(out, working_cfg.user_name[working_sel], SIM_USER_NAME_MAX);
	k_mutex_unlock(&working_cfg_lock);
}

/* The chunk the last CSV_OP_READ staged, served to the csv_read characteristic from RAM (the
 * flash read happens in process_csv_control, on this thread — never in a GATT callback). */
static K_MUTEX_DEFINE(csv_readback_lock);
static struct csv_readback_wire csv_readback;

uint16_t comm_thread_copy_csv_readback(struct csv_readback_wire *out)
{
	k_mutex_lock(&csv_readback_lock, K_FOREVER);
	*out = csv_readback;
	k_mutex_unlock(&csv_readback_lock);
	return CSV_READBACK_HDR + out->len;
}

void comm_thread_copy_dexcom_tx_id(char out[SIM_DEXCOM_ID_BUF])
{
	k_mutex_lock(&working_cfg_lock, K_FOREVER);
	memcpy(out, working_cfg.dexcom_tx_id, SIM_DEXCOM_ID_BUF);
	k_mutex_unlock(&working_cfg_lock);
	out[SIM_DEXCOM_ID_LEN] = '\0';
}

uint8_t comm_thread_comm_profile(void)
{
	k_mutex_lock(&working_cfg_lock, K_FOREVER);
	uint8_t p = working_cfg.comm_profile;
	k_mutex_unlock(&working_cfg_lock);

	return p;
}

float comm_thread_speed_mult(void)
{
	k_mutex_lock(&working_cfg_lock, K_FOREVER);
	float m = working_cfg.speed_mult;
	k_mutex_unlock(&working_cfg_lock);

	return m;
}

static void apply_person_msg(const uint8_t *data, uint16_t len)
{
	struct person_config_wire wire;

	if (len < sizeof(wire)) {
		return;
	}
	memcpy(&wire, data, sizeof(wire));

	k_mutex_lock(&working_cfg_lock, K_FOREVER);
	sel_slot()->model_id = wire.model_id;
	memcpy(sel_slot()->model_params, wire.params, sizeof(wire.params));
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
	sel_slot()->sensor_id = wire.sensor_id;
	memcpy(sel_slot()->sensor_params, wire.params, sizeof(wire.params));
	k_mutex_unlock(&working_cfg_lock);
}

static void apply_sensor_select_msg(const uint8_t *data, uint16_t len)
{
	if (len < 1) {
		return;
	}
	k_mutex_lock(&working_cfg_lock, K_FOREVER);
	uint8_t n = working_cfg.sensor_count ? working_cfg.sensor_count : 1;

	working_sel = (data[0] < n) ? data[0] : 0;
	k_mutex_unlock(&working_cfg_lock);
	printk("comm_thread: sensor_select -> %u\n", working_sel);
}

/* The payload is the bare name (0..SIM_USER_NAME_MAX-2 bytes); anything longer is
 * refused by the GATT write, so this only ever sees a name that fits. */
static void apply_user_name_msg(const uint8_t *data, uint16_t len)
{
	if (len > SIM_USER_NAME_MAX - 1) {
		return;
	}
	k_mutex_lock(&working_cfg_lock, K_FOREVER);
	char *dst = working_cfg.user_name[working_sel];

	memset(dst, 0, SIM_USER_NAME_MAX);
	memcpy(dst, data, len);
	k_mutex_unlock(&working_cfg_lock);
}

static void apply_data_source_msg(const uint8_t *data, uint16_t len)
{
	if (len < 1) {
		return;
	}
	k_mutex_lock(&working_cfg_lock, K_FOREVER);
	sel_slot()->data_source = (data[0] == SIM_DATA_CSV) ? SIM_DATA_CSV : SIM_DATA_MODEL;
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

/* The 5-minute grid and the trend's previous point (Dexcom board mode). */
static struct g6_grid g6_grid;
static bool g6_prev_valid;
static double g6_prev_glucose;
static double g6_prev_sim_s;
/* After a mode switch the model thread applies the new config (clock back to 0) on its next tick,
 * up to a second later; the sample still waiting from before that is stale and must not become a
 * reading. */
static int g6_skip_samples;

static void g6_restart(void)
{
	g6_grid_init(&g6_grid, G6_PERIOD_S);
	g6_prev_valid = false;
	g6_skip_samples = 0;
	dexcom_service_reset();
}

/* A board-mode write (from BLE, or the button via the same queue). Returns true if the mode
 * actually changed — a write of the current mode is not worth a restart. Entering Dexcom mode
 * forces speed x1: xDrip polls in wall-clock 5 minutes. The slot cursor goes back to slot 0, the
 * only sensor that runs in Dexcom mode. */
static bool apply_comm_profile_msg(const uint8_t *data, uint16_t len)
{
	if (len < 1) {
		return false;
	}
	uint8_t p = (data[0] == SIM_COMM_DEXCOM) ? SIM_COMM_DEXCOM : SIM_COMM_SIG_CGMS;

	k_mutex_lock(&working_cfg_lock, K_FOREVER);
	bool changed = working_cfg.comm_profile != p;

	if (changed) {
		working_cfg.comm_profile = p;
		working_cfg.sensor_count = sim_config_sensor_count_for(p);
		working_sel = 0;
		if (p == SIM_COMM_DEXCOM) {
			working_cfg.speed_mult = 1.0f;
		}
	}
	k_mutex_unlock(&working_cfg_lock);
	if (!changed) {
		return false;
	}
	g6_restart();
	g6_skip_samples = 1; /* the sample in flight predates the simulation reset */
	main_apply_comm_profile(p);
	return true;
}

/* CFG_MSG_DEXCOM_ID: the GATT write already refused anything that is not six digits/capitals. */
static void apply_dexcom_id_msg(const uint8_t *data, uint16_t len)
{
	if (!g6_valid_id((const char *)data, len)) {
		return;
	}
	k_mutex_lock(&working_cfg_lock, K_FOREVER);
	memcpy(working_cfg.dexcom_tx_id, data, SIM_DEXCOM_ID_LEN);
	working_cfg.dexcom_tx_id[SIM_DEXCOM_ID_LEN] = '\0';
	k_mutex_unlock(&working_cfg_lock);
	main_apply_dexcom_id();
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
		int err = csv_store_begin(comm_thread_selected_slot(), &hdr);

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
		int err = csv_store_commit(comm_thread_selected_slot(), w.track, NULL);

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
		int err = csv_store_clear(comm_thread_selected_slot(), w.track);

		config_service_notify_csv_control(
			err ? CSV_CTRL_STATUS_ERR : CSV_CTRL_STATUS_OK, 0);
		break;
	}
	case CSV_OP_STATUS:
		config_service_notify_csv_control(CSV_CTRL_STATUS_OK, csv_store_received());
		break;
	case CSV_OP_READ: {
		struct csv_read_op_wire w;
		struct csv_readback_wire chunk;

		if (len < sizeof(w)) {
			config_service_notify_csv_control(CSV_CTRL_STATUS_ERR, 0);
			return;
		}
		memcpy(&w, data, sizeof(w));
		int err = csv_store_readback(comm_thread_selected_slot(), w.track, w.offset, w.len,
					     &chunk);

		if (err) {
			config_service_notify_csv_control(CSV_CTRL_STATUS_ERR, 0);
			break;
		}
		k_mutex_lock(&csv_readback_lock, K_FOREVER);
		csv_readback = chunk;
		k_mutex_unlock(&csv_readback_lock);
		/* The app reads the csv_read characteristic only after this arrives. */
		config_service_notify_csv_control(CSV_CTRL_STATUS_OK, chunk.len);
		break;
	}
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
		sel_slot()->food_count = 0;
	} else if (sel_slot()->food_count < MAX_EVENTS) {
		sel_slot()->food[sel_slot()->food_count++] = ev;
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
		sel_slot()->exercise_count = 0;
	} else if (sel_slot()->exercise_count < MAX_EVENTS) {
		sel_slot()->exercise[sel_slot()->exercise_count++] = ev;
	}
	k_mutex_unlock(&working_cfg_lock);
}

/* ~3 KB, too big for the comm_thread stack. process_cfg_msg() only ever runs on the
 * comm_thread, one message at a time. */
static struct sim_config snapshot;

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
			model_thread_add_instant_food(comm_thread_selected_slot(),
						      wire.duration_min, wire.carbs_g);
		}
		return;
	}

	if (msg->type == CFG_MSG_EXERCISE_INSTANT) {
		struct exercise_instant_wire wire;

		if (msg->len >= sizeof(wire)) {
			memcpy(&wire, msg->data, sizeof(wire));
			model_thread_add_instant_exercise(comm_thread_selected_slot(),
							  wire.duration_min, wire.intensity_pct);
		}
		return;
	}

	if (msg->type == CFG_MSG_PISA_INSTANT) {
		struct pisa_instant_wire wire;

		if (msg->len >= sizeof(wire)) {
			memcpy(&wire, msg->data, sizeof(wire));
			model_thread_add_instant_pisa(comm_thread_selected_slot(),
						      wire.duration_min, wire.depth_frac);
		}
		return;
	}

	if (msg->type == CFG_MSG_SENSOR_SELECT) {
		apply_sensor_select_msg(msg->data, msg->len);
		return; /* cursor only — nothing to persist or re-apply */
	}

	if (msg->type == CFG_MSG_USER_NAME) {
		/* Persisted, but deliberately NOT routed through
		 * model_thread_apply_config(): a name is metadata, and applying would
		 * reset sim_clock_min and the instant events like a model write does. */
		apply_user_name_msg(msg->data, msg->len);
		comm_thread_copy_config(&snapshot);
		sim_config_save_to_flash(&snapshot);
		return;
	}

	if (msg->type == CFG_MSG_DEXCOM_ID) {
		/* Persisted metadata, like the user name: no sim reset. */
		apply_dexcom_id_msg(msg->data, msg->len);
		comm_thread_copy_config(&snapshot);
		sim_config_save_to_flash(&snapshot);
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
		break; /* legacy on/off mode byte — removed from struct, ignored */
	case CFG_MSG_DATA_SOURCE:
		apply_data_source_msg(msg->data, msg->len);
		break;
	case CFG_MSG_SPEED:
		apply_speed_msg(msg->data, msg->len);
		break;
	case CFG_MSG_COMM_PROFILE:
		if (!apply_comm_profile_msg(msg->data, msg->len)) {
			return; /* already in that mode: nothing to restart, nothing to save */
		}
		break;
	case CFG_MSG_FOOD_EVENT:
		apply_food_event_msg(msg->data, msg->len);
		break;
	case CFG_MSG_EXERCISE_EVENT:
		apply_exercise_event_msg(msg->data, msg->len);
		break;
	}

	comm_thread_copy_config(&snapshot);
	sim_config_save_to_flash(&snapshot);
	model_thread_apply_config(&snapshot);
}

/* Adds one glucose value to slot s's CGMS instance (retrying briefly, see below). */
static void cgms_push(int s, float glucose_mg_dl)
{
	struct bt_cgms_measurement result;

	if (!g_cgms[s]) {
		return;
	}
	result.glucose = sfloat_from_float(glucose_mg_dl);
	for (int i = 0; i < MEASUREMENT_RETRY_COUNT; i++) {
		int err = bt_cgms_measurement_add(g_cgms[s], result);

		if (err == 0) {
			printk("comm_thread: pushed slot %d glucose=%.2f\n", s, glucose_mg_dl);
			return;
		}
		/* Short retry: a dropped measurement is re-sent on the next push anyway, and
		 * with N connections this loop must not starve the config message queue
		 * (Board Layout push). */
		k_sleep(K_MSEC(40));
	}
}

/* Dexcom board mode, slot 0: one reading per 5 simulated minutes, held until the next. The same
 * reading goes to the G6 characteristics (xDrip pulls it) and, once, to the CGMS instance the
 * app reads — so both readers see exactly the same points. */
static void dexcom_tick(const struct model_measurement *meas)
{
	struct g6_reading r;

	if (g6_skip_samples > 0) {
		g6_skip_samples--;
		return;
	}
	if (meas->sim_s < g6_prev_sim_s) {
		g6_prev_valid = false; /* the simulated clock restarted */
	}
	double dt_min = (meas->sim_s - g6_prev_sim_s) / 60.0;
	bool has_trend = g6_prev_valid && dt_min > 0.5;
	double trend = has_trend ? ((double)meas->glucose_mg_dl - g6_prev_glucose) / dt_min : 0.0;

	dexcom_service_set_clock((uint32_t)meas->sim_s);
	if (!g6_grid_feed(&g6_grid, meas->sim_s, meas->glucose_mg_dl, has_trend, trend,
			  G6_STATE_OK, &r)) {
		return;
	}
	g6_prev_valid = true;
	g6_prev_glucose = meas->glucose_mg_dl;
	g6_prev_sim_s = meas->sim_s;
	dexcom_service_publish(&r);
	printk("comm_thread: dexcom reading seq=%u ts=%us glucose=%u trend=%d\n", r.seq, r.ts_s,
	       r.glucose, r.trend);
	cgms_push(0, (float)r.glucose);
	main_g6_reading_published();
}

static void push_measurement_and_status(void)
{
	k_mutex_lock(&working_cfg_lock, K_FOREVER);
	uint8_t comm_profile = working_cfg.comm_profile;
	uint8_t count = working_cfg.sensor_count ? working_cfg.sensor_count : 1;
	k_mutex_unlock(&working_cfg_lock);

	if (count > MAX_SIM_SENSORS) {
		count = MAX_SIM_SENSORS;
	}
	bool dexcom = (comm_profile == SIM_COMM_DEXCOM);
	bool cgms_only = model_thread_get_cgms_only();

	for (int s = 0; s < count; s++) {
		struct model_measurement meas;

		model_thread_take_measurement(s, &meas);

		/* meas.valid gates only the glucose push; carbs/exercise in meas
		 * are always current, so the per-slot status notify below fires
		 * every tick regardless. */
		if (meas.valid && dexcom) {
			dexcom_tick(&meas);
		} else if (meas.valid) {
			cgms_push(s, meas.glucose_mg_dl);
		}

		if (cgms_only) {
			continue;
		}
		/* Food/Exercise Status, per slot: {u8 slot; u8 _pad; f32 carbs; f32 ex} */
		uint8_t payload[10];

		payload[0] = (uint8_t)s;
		payload[1] = 0;
		memcpy(payload + 2, &meas.carbs_g_per_min, sizeof(float));
		memcpy(payload + 6, &meas.exercise_pct, sizeof(float));
		config_service_notify_food_exercise_status(payload, sizeof(payload));
	}
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

void comm_thread_start(struct bt_cgms **cgms, const struct sim_config *initial_cfg)
{
	g6_restart();
	for (int i = 0; i < MAX_SIM_SENSORS; i++) {
		g_cgms[i] = cgms[i];
	}

	k_mutex_lock(&working_cfg_lock, K_FOREVER);
	working_cfg = *initial_cfg;
	k_mutex_unlock(&working_cfg_lock);

	k_thread_create(&comm_thread_data, comm_thread_stack, COMM_THREAD_STACK_SIZE,
			 comm_thread_entry, NULL, NULL, NULL, COMM_THREAD_PRIORITY, 0,
			 K_NO_WAIT);
	k_thread_name_set(&comm_thread_data, "comm_thread");
}
