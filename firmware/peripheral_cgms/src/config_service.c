#include <string.h>
#include <errno.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/printk.h>
#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/att.h>
#include <zephyr/bluetooth/uuid.h>
#include <zephyr/bluetooth/gatt.h>

#include "config_service.h"
#include "comm_thread.h"
#include "model_thread.h"
#include "sim_config.h"

/* UUIDs must match SensorSimulator/src/ble_uuids.py exactly (see
 * PROTOCOL_SPEC.md §2). */
static struct bt_uuid_128 config_service_uuid = BT_UUID_INIT_128(
	BT_UUID_128_ENCODE(0x5b2c0001, 0x0d6d, 0x4a3a, 0x8c1e, 0x3f9b6e7a1a00));
static struct bt_uuid_128 person_config_uuid = BT_UUID_INIT_128(
	BT_UUID_128_ENCODE(0x5b2c0002, 0x0d6d, 0x4a3a, 0x8c1e, 0x3f9b6e7a1a00));
static struct bt_uuid_128 sensor_config_uuid = BT_UUID_INIT_128(
	BT_UUID_128_ENCODE(0x5b2c0003, 0x0d6d, 0x4a3a, 0x8c1e, 0x3f9b6e7a1a00));
static struct bt_uuid_128 mode_config_uuid = BT_UUID_INIT_128(
	BT_UUID_128_ENCODE(0x5b2c0004, 0x0d6d, 0x4a3a, 0x8c1e, 0x3f9b6e7a1a00));
static struct bt_uuid_128 food_event_uuid = BT_UUID_INIT_128(
	BT_UUID_128_ENCODE(0x5b2c0005, 0x0d6d, 0x4a3a, 0x8c1e, 0x3f9b6e7a1a00));
static struct bt_uuid_128 exercise_event_uuid = BT_UUID_INIT_128(
	BT_UUID_128_ENCODE(0x5b2c0006, 0x0d6d, 0x4a3a, 0x8c1e, 0x3f9b6e7a1a00));
static struct bt_uuid_128 food_exercise_status_uuid = BT_UUID_INIT_128(
	BT_UUID_128_ENCODE(0x5b2c0007, 0x0d6d, 0x4a3a, 0x8c1e, 0x3f9b6e7a1a00));
static struct bt_uuid_128 food_events_readback_uuid = BT_UUID_INIT_128(
	BT_UUID_128_ENCODE(0x5b2c0008, 0x0d6d, 0x4a3a, 0x8c1e, 0x3f9b6e7a1a00));
static struct bt_uuid_128 exercise_events_readback_uuid = BT_UUID_INIT_128(
	BT_UUID_128_ENCODE(0x5b2c0009, 0x0d6d, 0x4a3a, 0x8c1e, 0x3f9b6e7a1a00));
static struct bt_uuid_128 run_state_uuid = BT_UUID_INIT_128(
	BT_UUID_128_ENCODE(0x5b2c000a, 0x0d6d, 0x4a3a, 0x8c1e, 0x3f9b6e7a1a00));
static struct bt_uuid_128 reset_sync_uuid = BT_UUID_INIT_128(
	BT_UUID_128_ENCODE(0x5b2c000b, 0x0d6d, 0x4a3a, 0x8c1e, 0x3f9b6e7a1a00));
static struct bt_uuid_128 food_instant_uuid = BT_UUID_INIT_128(
	BT_UUID_128_ENCODE(0x5b2c000c, 0x0d6d, 0x4a3a, 0x8c1e, 0x3f9b6e7a1a00));
static struct bt_uuid_128 exercise_instant_uuid = BT_UUID_INIT_128(
	BT_UUID_128_ENCODE(0x5b2c000d, 0x0d6d, 0x4a3a, 0x8c1e, 0x3f9b6e7a1a00));
static struct bt_uuid_128 cgms_only_uuid = BT_UUID_INIT_128(
	BT_UUID_128_ENCODE(0x5b2c000e, 0x0d6d, 0x4a3a, 0x8c1e, 0x3f9b6e7a1a00));

/* Config writes are rejected while CGMS-only mode is active — see
 * model_thread.h's model_thread_set_cgms_only() comment. Deliberately does
 * NOT gate write_run_state/write_cgms_only themselves: those are the only
 * way to leave the mode. */
static bool cfg_write_blocked(void)
{
	return model_thread_get_cgms_only();
}

/* ── Person / Sensor / Mode: read + write ────────────────────────────── */

static ssize_t read_person_config(struct bt_conn *conn, const struct bt_gatt_attr *attr,
				   void *buf, uint16_t len, uint16_t offset)
{
	struct sim_config cfg;
	struct person_config_wire wire;

	comm_thread_copy_config(&cfg);
	wire.model_id = cfg.model_id;
	memcpy(wire.params, cfg.model_params, sizeof(wire.params));

	return bt_gatt_attr_read(conn, attr, buf, len, offset, &wire, sizeof(wire));
}

static ssize_t write_person_config(struct bt_conn *conn, const struct bt_gatt_attr *attr,
				    const void *buf, uint16_t len, uint16_t offset,
				    uint8_t flags)
{
	ARG_UNUSED(conn);
	ARG_UNUSED(attr);
	ARG_UNUSED(flags);

	if (cfg_write_blocked()) {
		return BT_GATT_ERR(BT_ATT_ERR_WRITE_NOT_PERMITTED);
	}
	if (offset != 0) {
		return BT_GATT_ERR(BT_ATT_ERR_INVALID_OFFSET);
	}
	if (comm_thread_enqueue_config(CFG_MSG_PERSON, buf, len) != 0) {
		return BT_GATT_ERR(BT_ATT_ERR_INSUFFICIENT_RESOURCES);
	}
	return len;
}

static ssize_t read_sensor_config(struct bt_conn *conn, const struct bt_gatt_attr *attr,
				   void *buf, uint16_t len, uint16_t offset)
{
	struct sim_config cfg;
	struct sensor_config_wire wire;

	comm_thread_copy_config(&cfg);
	wire.sensor_id = cfg.sensor_id;
	memcpy(wire.params, cfg.sensor_params, sizeof(wire.params));

	return bt_gatt_attr_read(conn, attr, buf, len, offset, &wire, sizeof(wire));
}

static ssize_t write_sensor_config(struct bt_conn *conn, const struct bt_gatt_attr *attr,
				    const void *buf, uint16_t len, uint16_t offset,
				    uint8_t flags)
{
	ARG_UNUSED(conn);
	ARG_UNUSED(attr);
	ARG_UNUSED(flags);

	if (cfg_write_blocked()) {
		return BT_GATT_ERR(BT_ATT_ERR_WRITE_NOT_PERMITTED);
	}
	if (offset != 0) {
		return BT_GATT_ERR(BT_ATT_ERR_INVALID_OFFSET);
	}
	if (comm_thread_enqueue_config(CFG_MSG_SENSOR, buf, len) != 0) {
		return BT_GATT_ERR(BT_ATT_ERR_INSUFFICIENT_RESOURCES);
	}
	return len;
}

static ssize_t read_mode(struct bt_conn *conn, const struct bt_gatt_attr *attr, void *buf,
			  uint16_t len, uint16_t offset)
{
	struct sim_config cfg;

	comm_thread_copy_config(&cfg);
	return bt_gatt_attr_read(conn, attr, buf, len, offset, &cfg.mode, sizeof(cfg.mode));
}

static ssize_t write_mode(struct bt_conn *conn, const struct bt_gatt_attr *attr,
			   const void *buf, uint16_t len, uint16_t offset, uint8_t flags)
{
	ARG_UNUSED(conn);
	ARG_UNUSED(attr);
	ARG_UNUSED(flags);

	if (cfg_write_blocked()) {
		return BT_GATT_ERR(BT_ATT_ERR_WRITE_NOT_PERMITTED);
	}
	if (offset != 0) {
		return BT_GATT_ERR(BT_ATT_ERR_INVALID_OFFSET);
	}
	if (len < 1) {
		return BT_GATT_ERR(BT_ATT_ERR_INVALID_ATTRIBUTE_LEN);
	}
	if (comm_thread_enqueue_config(CFG_MSG_MODE, buf, len) != 0) {
		return BT_GATT_ERR(BT_ATT_ERR_INSUFFICIENT_RESOURCES);
	}
	return len;
}

/* ── Food / Exercise events: write-only "append one" ─────────────────── */

static ssize_t write_food_event(struct bt_conn *conn, const struct bt_gatt_attr *attr,
				  const void *buf, uint16_t len, uint16_t offset,
				  uint8_t flags)
{
	ARG_UNUSED(conn);
	ARG_UNUSED(attr);
	ARG_UNUSED(flags);

	if (cfg_write_blocked()) {
		return BT_GATT_ERR(BT_ATT_ERR_WRITE_NOT_PERMITTED);
	}
	if (offset != 0) {
		return BT_GATT_ERR(BT_ATT_ERR_INVALID_OFFSET);
	}
	if (len < sizeof(struct food_event)) {
		return BT_GATT_ERR(BT_ATT_ERR_INVALID_ATTRIBUTE_LEN);
	}
	if (comm_thread_enqueue_config(CFG_MSG_FOOD_EVENT, buf, len) != 0) {
		return BT_GATT_ERR(BT_ATT_ERR_INSUFFICIENT_RESOURCES);
	}
	return len;
}

static ssize_t write_exercise_event(struct bt_conn *conn, const struct bt_gatt_attr *attr,
				      const void *buf, uint16_t len, uint16_t offset,
				      uint8_t flags)
{
	ARG_UNUSED(conn);
	ARG_UNUSED(attr);
	ARG_UNUSED(flags);

	if (cfg_write_blocked()) {
		return BT_GATT_ERR(BT_ATT_ERR_WRITE_NOT_PERMITTED);
	}
	if (offset != 0) {
		return BT_GATT_ERR(BT_ATT_ERR_INVALID_OFFSET);
	}
	if (len < sizeof(struct exercise_event)) {
		return BT_GATT_ERR(BT_ATT_ERR_INVALID_ATTRIBUTE_LEN);
	}
	if (comm_thread_enqueue_config(CFG_MSG_EXERCISE_EVENT, buf, len) != 0) {
		return BT_GATT_ERR(BT_ATT_ERR_INSUFFICIENT_RESOURCES);
	}
	return len;
}

/* ── Instant food / exercise events: write-only, one-shot, non-persisted.
 * Unlike write_food_event/write_exercise_event above, these do NOT go
 * through the config-apply/reset path — see model_thread.h's
 * struct food_instant_wire comment and PROTOCOL_SPEC.md. ───────────────── */

static ssize_t write_food_instant(struct bt_conn *conn, const struct bt_gatt_attr *attr,
				    const void *buf, uint16_t len, uint16_t offset,
				    uint8_t flags)
{
	ARG_UNUSED(conn);
	ARG_UNUSED(attr);
	ARG_UNUSED(flags);

	if (cfg_write_blocked()) {
		return BT_GATT_ERR(BT_ATT_ERR_WRITE_NOT_PERMITTED);
	}
	if (offset != 0) {
		return BT_GATT_ERR(BT_ATT_ERR_INVALID_OFFSET);
	}
	if (len < sizeof(struct food_instant_wire)) {
		return BT_GATT_ERR(BT_ATT_ERR_INVALID_ATTRIBUTE_LEN);
	}
	if (comm_thread_enqueue_config(CFG_MSG_FOOD_INSTANT, buf, len) != 0) {
		return BT_GATT_ERR(BT_ATT_ERR_INSUFFICIENT_RESOURCES);
	}
	return len;
}

static ssize_t write_exercise_instant(struct bt_conn *conn, const struct bt_gatt_attr *attr,
					const void *buf, uint16_t len, uint16_t offset,
					uint8_t flags)
{
	ARG_UNUSED(conn);
	ARG_UNUSED(attr);
	ARG_UNUSED(flags);

	if (cfg_write_blocked()) {
		return BT_GATT_ERR(BT_ATT_ERR_WRITE_NOT_PERMITTED);
	}
	if (offset != 0) {
		return BT_GATT_ERR(BT_ATT_ERR_INVALID_OFFSET);
	}
	if (len < sizeof(struct exercise_instant_wire)) {
		return BT_GATT_ERR(BT_ATT_ERR_INVALID_ATTRIBUTE_LEN);
	}
	if (comm_thread_enqueue_config(CFG_MSG_EXERCISE_INSTANT, buf, len) != 0) {
		return BT_GATT_ERR(BT_ATT_ERR_INSUFFICIENT_RESOURCES);
	}
	return len;
}

/* ── Food / Exercise events: read-only full-list readback ────────────── */

static ssize_t read_food_events(struct bt_conn *conn, const struct bt_gatt_attr *attr,
				  void *buf, uint16_t len, uint16_t offset)
{
	struct sim_config cfg;
	struct food_list_wire wire;

	comm_thread_copy_config(&cfg);
	wire.count = cfg.food_count;
	memcpy(wire.events, cfg.food, sizeof(wire.events));

	uint16_t actual_len = sizeof(wire.count) + cfg.food_count * sizeof(struct food_event);

	return bt_gatt_attr_read(conn, attr, buf, len, offset, &wire, actual_len);
}

static ssize_t read_exercise_events(struct bt_conn *conn, const struct bt_gatt_attr *attr,
				      void *buf, uint16_t len, uint16_t offset)
{
	struct sim_config cfg;
	struct exercise_list_wire wire;

	comm_thread_copy_config(&cfg);
	wire.count = cfg.exercise_count;
	memcpy(wire.events, cfg.exercise, sizeof(wire.events));

	uint16_t actual_len = sizeof(wire.count) + cfg.exercise_count * sizeof(struct exercise_event);

	return bt_gatt_attr_read(conn, attr, buf, len, offset, &wire, actual_len);
}

/* ── Run state: read + write, live session control only (see
 * model_thread.h's sim_run_state — deliberately not part of struct
 * sim_config, never touches flash). ─────────────────────────────────── */

static ssize_t read_run_state(struct bt_conn *conn, const struct bt_gatt_attr *attr,
				void *buf, uint16_t len, uint16_t offset)
{
	uint8_t state = model_thread_get_run_state();

	return bt_gatt_attr_read(conn, attr, buf, len, offset, &state, sizeof(state));
}

static ssize_t write_run_state(struct bt_conn *conn, const struct bt_gatt_attr *attr,
				 const void *buf, uint16_t len, uint16_t offset,
				 uint8_t flags)
{
	ARG_UNUSED(conn);
	ARG_UNUSED(attr);
	ARG_UNUSED(flags);

	if (offset != 0) {
		return BT_GATT_ERR(BT_ATT_ERR_INVALID_OFFSET);
	}
	if (len < 1) {
		return BT_GATT_ERR(BT_ATT_ERR_INVALID_ATTRIBUTE_LEN);
	}
	if (comm_thread_enqueue_config(CFG_MSG_RUN_STATE, buf, len) != 0) {
		return BT_GATT_ERR(BT_ATT_ERR_INSUFFICIENT_RESOURCES);
	}
	return len;
}

/* ── CGMS-only mode: read + write, live session control only (see
 * model_thread.h's model_thread_set_cgms_only() comment). Deliberately
 * never gated by cfg_write_blocked() — this and run_state are the only
 * writes that must keep working while CGMS-only is active. ─────────────── */

static ssize_t read_cgms_only(struct bt_conn *conn, const struct bt_gatt_attr *attr,
				void *buf, uint16_t len, uint16_t offset)
{
	uint8_t enabled = model_thread_get_cgms_only() ? 1 : 0;

	return bt_gatt_attr_read(conn, attr, buf, len, offset, &enabled, sizeof(enabled));
}

static ssize_t write_cgms_only(struct bt_conn *conn, const struct bt_gatt_attr *attr,
				 const void *buf, uint16_t len, uint16_t offset,
				 uint8_t flags)
{
	ARG_UNUSED(conn);
	ARG_UNUSED(attr);
	ARG_UNUSED(flags);

	if (offset != 0) {
		return BT_GATT_ERR(BT_ATT_ERR_INVALID_OFFSET);
	}
	if (len < 1) {
		return BT_GATT_ERR(BT_ATT_ERR_INVALID_ATTRIBUTE_LEN);
	}
	if (comm_thread_enqueue_config(CFG_MSG_CGMS_ONLY, buf, len) != 0) {
		return BT_GATT_ERR(BT_ATT_ERR_INSUFFICIENT_RESOURCES);
	}
	return len;
}

/* ── Food/Exercise status: notify-only ────────────────────────────────── */

static void food_exercise_status_ccc_changed(const struct bt_gatt_attr *attr, uint16_t value)
{
	ARG_UNUSED(attr);
	ARG_UNUSED(value);
}

/* ── Reset sync: notify-only, see config_service.h for what this is for. */

static void reset_sync_ccc_changed(const struct bt_gatt_attr *attr, uint16_t value)
{
	ARG_UNUSED(attr);
	ARG_UNUSED(value);
}

BT_GATT_SERVICE_DEFINE(sim_config_svc,
	BT_GATT_PRIMARY_SERVICE(&config_service_uuid),

	BT_GATT_CHARACTERISTIC(&person_config_uuid.uuid,
		BT_GATT_CHRC_READ | BT_GATT_CHRC_WRITE,
		BT_GATT_PERM_READ | BT_GATT_PERM_WRITE,
		read_person_config, write_person_config, NULL),

	BT_GATT_CHARACTERISTIC(&sensor_config_uuid.uuid,
		BT_GATT_CHRC_READ | BT_GATT_CHRC_WRITE,
		BT_GATT_PERM_READ | BT_GATT_PERM_WRITE,
		read_sensor_config, write_sensor_config, NULL),

	BT_GATT_CHARACTERISTIC(&mode_config_uuid.uuid,
		BT_GATT_CHRC_READ | BT_GATT_CHRC_WRITE,
		BT_GATT_PERM_READ | BT_GATT_PERM_WRITE,
		read_mode, write_mode, NULL),

	BT_GATT_CHARACTERISTIC(&food_event_uuid.uuid,
		BT_GATT_CHRC_WRITE,
		BT_GATT_PERM_WRITE,
		NULL, write_food_event, NULL),

	BT_GATT_CHARACTERISTIC(&exercise_event_uuid.uuid,
		BT_GATT_CHRC_WRITE,
		BT_GATT_PERM_WRITE,
		NULL, write_exercise_event, NULL),

	BT_GATT_CHARACTERISTIC(&food_instant_uuid.uuid,
		BT_GATT_CHRC_WRITE,
		BT_GATT_PERM_WRITE,
		NULL, write_food_instant, NULL),

	BT_GATT_CHARACTERISTIC(&exercise_instant_uuid.uuid,
		BT_GATT_CHRC_WRITE,
		BT_GATT_PERM_WRITE,
		NULL, write_exercise_instant, NULL),

	BT_GATT_CHARACTERISTIC(&food_exercise_status_uuid.uuid,
		BT_GATT_CHRC_NOTIFY,
		0,
		NULL, NULL, NULL),
	BT_GATT_CCC(food_exercise_status_ccc_changed,
		BT_GATT_PERM_READ | BT_GATT_PERM_WRITE),

	BT_GATT_CHARACTERISTIC(&food_events_readback_uuid.uuid,
		BT_GATT_CHRC_READ,
		BT_GATT_PERM_READ,
		read_food_events, NULL, NULL),

	BT_GATT_CHARACTERISTIC(&exercise_events_readback_uuid.uuid,
		BT_GATT_CHRC_READ,
		BT_GATT_PERM_READ,
		read_exercise_events, NULL, NULL),

	BT_GATT_CHARACTERISTIC(&run_state_uuid.uuid,
		BT_GATT_CHRC_READ | BT_GATT_CHRC_WRITE,
		BT_GATT_PERM_READ | BT_GATT_PERM_WRITE,
		read_run_state, write_run_state, NULL),

	BT_GATT_CHARACTERISTIC(&reset_sync_uuid.uuid,
		BT_GATT_CHRC_NOTIFY,
		0,
		NULL, NULL, NULL),
	BT_GATT_CCC(reset_sync_ccc_changed,
		BT_GATT_PERM_READ | BT_GATT_PERM_WRITE),

	BT_GATT_CHARACTERISTIC(&cgms_only_uuid.uuid,
		BT_GATT_CHRC_READ | BT_GATT_CHRC_WRITE,
		BT_GATT_PERM_READ | BT_GATT_PERM_WRITE,
		read_cgms_only, write_cgms_only, NULL),
);

/* Finds the VALUE attribute (not the declaration attribute) for a
 * characteristic by UUID, so notify helpers stay correct even if the
 * service definition above is reordered. *cache is a caller-owned static
 * so each notify characteristic only pays the linear scan once. */
static const struct bt_gatt_attr *find_value_attr(const struct bt_uuid *uuid,
						     const struct bt_gatt_attr **cache)
{
	if (!*cache) {
		for (size_t i = 0; i < sim_config_svc.attr_count; i++) {
			if (bt_uuid_cmp(sim_config_svc.attrs[i].uuid, uuid) == 0) {
				*cache = &sim_config_svc.attrs[i];
				break;
			}
		}
	}
	return *cache;
}

int config_service_notify_food_exercise_status(const void *data, uint16_t len)
{
	static const struct bt_gatt_attr *attr;
	const struct bt_gatt_attr *found = find_value_attr(&food_exercise_status_uuid.uuid, &attr);

	if (!found) {
		return -ENOENT;
	}
	/* A negative return here (e.g. -ENOTCONN/-EACCES) commonly just means
	 * nobody has subscribed yet — normal, not logged as an error. */
	return bt_gatt_notify(NULL, found, data, len);
}

int config_service_notify_reset_sync(void)
{
	static const struct bt_gatt_attr *attr;
	static uint8_t generation;

	const struct bt_gatt_attr *found = find_value_attr(&reset_sync_uuid.uuid, &attr);

	if (!found) {
		return -ENOENT;
	}
	generation++;
	return bt_gatt_notify(NULL, found, &generation, sizeof(generation));
}

void config_service_init(void)
{
	printk("config_service: simulator config service registered (%zu attributes)\n",
	       sim_config_svc.attr_count);
}
