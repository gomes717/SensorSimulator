#include <errno.h>
#include <string.h>
#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/crypto.h>
#include <zephyr/bluetooth/gatt.h>
#include <zephyr/bluetooth/uuid.h>
#include <zephyr/init.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/printk.h>

#include "comm_thread.h"
#include "dexcom_g6.h"
#include "dexcom_service.h"
#include "sim_config.h"

/* Base F8083532-849E-531C-C594-30F1F86A4EA5 (xDrip's BluetoothServices). */
#define G6_UUID(w) BT_UUID_128_ENCODE(w, 0x849e, 0x531c, 0xc594, 0x30f1f86a4ea5)

static struct bt_uuid_128 g6_svc_uuid = BT_UUID_INIT_128(G6_UUID(0xf8083532));
static struct bt_uuid_128 g6_comm_uuid = BT_UUID_INIT_128(G6_UUID(0xf8083533));
static struct bt_uuid_128 g6_control_uuid = BT_UUID_INIT_128(G6_UUID(0xf8083534));
static struct bt_uuid_128 g6_auth_uuid = BT_UUID_INIT_128(G6_UUID(0xf8083535));
static struct bt_uuid_128 g6_backfill_uuid = BT_UUID_INIT_128(G6_UUID(0xf8083536));
static struct bt_uuid_128 g6_extra_uuid = BT_UUID_INIT_128(G6_UUID(0xf8083538));

/* ---- the held reading --------------------------------------------------------------------- */

static K_MUTEX_DEFINE(state_lock);
static struct g6_reading held;
static bool have_held;
static uint32_t clock_s;

void dexcom_service_reset(void)
{
	k_mutex_lock(&state_lock, K_FOREVER);
	have_held = false;
	clock_s = 0;
	k_mutex_unlock(&state_lock);
}

void dexcom_service_publish(const struct g6_reading *r)
{
	k_mutex_lock(&state_lock, K_FOREVER);
	held = *r;
	have_held = true;
	k_mutex_unlock(&state_lock);
}

void dexcom_service_set_clock(uint32_t now_s)
{
	k_mutex_lock(&state_lock, K_FOREVER);
	clock_s = now_s;
	k_mutex_unlock(&state_lock);
}

/* ---- per-connection state ----------------------------------------------------------------- */

/* A reply is sent from the system work queue, not from the write callback: an indication must
 * follow the write response, and sending it from inside the callback would queue it first. */
struct g6_conn {
	struct g6_session session;
	struct k_work work;
	struct bt_conn *conn; /* a reference, held while a reply is pending */
	const struct bt_gatt_attr *attr;
	bool pending;
	bool disconnect_after;
	uint8_t reply[G6_MAX_REPLY];
	size_t reply_len;
	struct bt_gatt_indicate_params ind;
	uint8_t last_auth[G6_MAX_REPLY]; /* what a read of the Authentication characteristic returns */
	size_t last_auth_len;
};

static struct g6_conn conns[CONFIG_BT_MAX_CONN];

static bool dexcom_mode(void)
{
	return comm_thread_comm_profile() == SIM_COMM_DEXCOM;
}

static void indicate_done(struct bt_conn *conn, struct bt_gatt_indicate_params *params, uint8_t err)
{
	struct g6_conn *c = &conns[bt_conn_index(conn)];

	ARG_UNUSED(params);
	if (err) {
		printk("dexcom: indication failed (ATT err %u)\n", err);
	}
	c->pending = false;
}

static void reply_work_handler(struct k_work *work)
{
	struct g6_conn *c = CONTAINER_OF(work, struct g6_conn, work);
	struct bt_conn *conn = c->conn;

	if (!conn) {
		return;
	}
	if (c->reply_len > 0) {
		memset(&c->ind, 0, sizeof(c->ind));
		c->ind.attr = c->attr;
		c->ind.data = c->reply;
		c->ind.len = (uint16_t)c->reply_len;
		c->ind.func = indicate_done;
		int err = bt_gatt_indicate(conn, &c->ind);

		if (err) {
			printk("dexcom: indicate failed (%d)\n", err);
			c->pending = false;
		}
	} else {
		c->pending = false;
	}
	if (c->disconnect_after) {
		c->disconnect_after = false;
		(void)bt_conn_disconnect(conn, BT_HCI_ERR_REMOTE_USER_TERM_CONN);
	}
	c->conn = NULL;
	bt_conn_unref(conn);
}

/* Queues c->reply (already filled) to be indicated on `attr` once the write is answered. */
static void queue_reply(struct g6_conn *c, struct bt_conn *conn, const struct bt_gatt_attr *attr)
{
	c->attr = attr;
	c->conn = bt_conn_ref(conn);
	c->pending = true;
	k_work_submit(&c->work);
}

/* ---- Authentication (...3535) ------------------------------------------------------------- */

static ssize_t write_auth(struct bt_conn *conn, const struct bt_gatt_attr *attr, const void *buf,
			  uint16_t len, uint16_t offset, uint8_t flags)
{
	struct g6_conn *c = &conns[bt_conn_index(conn)];
	char id[SIM_DEXCOM_ID_BUF];
	uint8_t rnd[G6_TOKEN_LEN];

	ARG_UNUSED(flags);
	if (!dexcom_mode()) {
		return BT_GATT_ERR(BT_ATT_ERR_WRITE_NOT_PERMITTED);
	}
	if (offset != 0) {
		return BT_GATT_ERR(BT_ATT_ERR_INVALID_OFFSET);
	}
	if (len > 20 || c->pending) {
		return BT_GATT_ERR(len > 20 ? BT_ATT_ERR_INVALID_ATTRIBUTE_LEN
					    : BT_ATT_ERR_PREPARE_QUEUE_FULL);
	}

	comm_thread_copy_dexcom_tx_id(id);
	if (bt_rand(rnd, sizeof(rnd)) != 0) {
		uint32_t t = k_cycle_get_32();

		memcpy(rnd, &t, 4);
		memcpy(rnd + 4, &t, 4);
	}

	size_t n = 0;
	bool known = g6_auth_handle(&c->session, id, buf, len, rnd, c->reply, &n);

	if (!known) {
		printk("dexcom: unknown auth write op 0x%02x len %u\n", len ? ((uint8_t *)buf)[0] : 0,
		       len);
		return len;
	}
	printk("dexcom: auth op 0x%02x -> %u bytes (authenticated=%d)\n", ((uint8_t *)buf)[0],
	       (unsigned)n, c->session.authenticated);
	if (n > 0) {
		memcpy(c->last_auth, c->reply, n);
		c->last_auth_len = n;
		c->reply_len = n;
		queue_reply(c, conn, attr);
	}
	return len;
}

static ssize_t read_auth(struct bt_conn *conn, const struct bt_gatt_attr *attr, void *buf,
			 uint16_t len, uint16_t offset)
{
	struct g6_conn *c = &conns[bt_conn_index(conn)];

	return bt_gatt_attr_read(conn, attr, buf, len, offset, c->last_auth, c->last_auth_len);
}

/* ---- Control (...3534) -------------------------------------------------------------------- */

static ssize_t write_control(struct bt_conn *conn, const struct bt_gatt_attr *attr, const void *buf,
			     uint16_t len, uint16_t offset, uint8_t flags)
{
	struct g6_conn *c = &conns[bt_conn_index(conn)];
	struct g6_reading latest;
	bool have;
	uint32_t now_s;

	ARG_UNUSED(flags);
	if (!dexcom_mode()) {
		return BT_GATT_ERR(BT_ATT_ERR_WRITE_NOT_PERMITTED);
	}
	if (offset != 0) {
		return BT_GATT_ERR(BT_ATT_ERR_INVALID_OFFSET);
	}
	if (len > 20) {
		return BT_GATT_ERR(BT_ATT_ERR_INVALID_ATTRIBUTE_LEN);
	}
	if (c->pending) {
		return BT_GATT_ERR(BT_ATT_ERR_PREPARE_QUEUE_FULL);
	}

	k_mutex_lock(&state_lock, K_FOREVER);
	latest = held;
	have = have_held;
	now_s = clock_s;
	k_mutex_unlock(&state_lock);

	size_t n = 0;
	enum g6_result res =
		g6_control_handle(&c->session, have ? &latest : NULL, now_s, buf, len, c->reply, &n);

	switch (res) {
	case G6_RES_REPLY:
		c->reply_len = n;
		queue_reply(c, conn, attr);
		break;
	case G6_RES_NOT_AUTH:
		printk("dexcom: control write before authentication (op 0x%02x)\n",
		       len ? ((uint8_t *)buf)[0] : 0);
		return BT_GATT_ERR(BT_ATT_ERR_AUTHORIZATION);
	case G6_RES_DISCONNECT:
		c->reply_len = 0;
		c->disconnect_after = true;
		queue_reply(c, conn, attr);
		break;
	case G6_RES_BAD_CRC:
		printk("dexcom: bad crc on control op 0x%02x len %u\n", ((uint8_t *)buf)[0], len);
		break;
	case G6_RES_UNKNOWN:
		printk("dexcom: unknown op 0x%02x len %u\n", len ? ((uint8_t *)buf)[0] : 0, len);
		break;
	case G6_RES_ACK:
		break;
	}
	return len;
}

/* ---- the rest ----------------------------------------------------------------------------- */

static ssize_t read_comm(struct bt_conn *conn, const struct bt_gatt_attr *attr, void *buf,
			 uint16_t len, uint16_t offset)
{
	return bt_gatt_attr_read(conn, attr, buf, len, offset, NULL, 0);
}

static void ccc_changed(const struct bt_gatt_attr *attr, uint16_t value)
{
	ARG_UNUSED(attr);
	printk("dexcom: subscription value 0x%04x\n", value);
}

BT_GATT_SERVICE_DEFINE(dexcom_svc,
	BT_GATT_PRIMARY_SERVICE(&g6_svc_uuid),

	BT_GATT_CHARACTERISTIC(&g6_comm_uuid.uuid,
		BT_GATT_CHRC_READ | BT_GATT_CHRC_NOTIFY,
		BT_GATT_PERM_READ,
		read_comm, NULL, NULL),
	BT_GATT_CCC(ccc_changed, BT_GATT_PERM_READ | BT_GATT_PERM_WRITE),

	BT_GATT_CHARACTERISTIC(&g6_control_uuid.uuid,
		BT_GATT_CHRC_WRITE | BT_GATT_CHRC_INDICATE,
		BT_GATT_PERM_WRITE,
		NULL, write_control, NULL),
	BT_GATT_CCC(ccc_changed, BT_GATT_PERM_READ | BT_GATT_PERM_WRITE),

	BT_GATT_CHARACTERISTIC(&g6_auth_uuid.uuid,
		BT_GATT_CHRC_READ | BT_GATT_CHRC_WRITE | BT_GATT_CHRC_INDICATE,
		BT_GATT_PERM_READ | BT_GATT_PERM_WRITE,
		read_auth, write_auth, NULL),
	BT_GATT_CCC(ccc_changed, BT_GATT_PERM_READ | BT_GATT_PERM_WRITE),

	BT_GATT_CHARACTERISTIC(&g6_backfill_uuid.uuid,
		BT_GATT_CHRC_NOTIFY,
		BT_GATT_PERM_NONE,
		NULL, NULL, NULL),
	BT_GATT_CCC(ccc_changed, BT_GATT_PERM_READ | BT_GATT_PERM_WRITE),

	BT_GATT_CHARACTERISTIC(&g6_extra_uuid.uuid,
		BT_GATT_CHRC_NOTIFY,
		BT_GATT_PERM_NONE,
		NULL, NULL, NULL),
	BT_GATT_CCC(ccc_changed, BT_GATT_PERM_READ | BT_GATT_PERM_WRITE),
);

static void conn_gone(struct bt_conn *conn, uint8_t reason)
{
	struct g6_conn *c = &conns[bt_conn_index(conn)];

	ARG_UNUSED(reason);
	memset(&c->session, 0, sizeof(c->session));
	c->last_auth_len = 0;
	c->disconnect_after = false;
}

static void conn_up(struct bt_conn *conn, uint8_t err)
{
	if (!err) {
		conn_gone(conn, 0); /* a fresh link starts unauthenticated */
	}
}

BT_CONN_CB_DEFINE(dexcom_conn_cb) = {
	.connected = conn_up,
	.disconnected = conn_gone,
};

static int dexcom_service_init(void)
{
	for (size_t i = 0; i < ARRAY_SIZE(conns); i++) {
		k_work_init(&conns[i].work, reply_work_handler);
	}
	return 0;
}

SYS_INIT(dexcom_service_init, APPLICATION, 90);
