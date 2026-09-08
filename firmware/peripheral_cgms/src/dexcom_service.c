#include <string.h>
#include <errno.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/printk.h>
#include <zephyr/sys/byteorder.h>
#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/uuid.h>
#include <zephyr/bluetooth/gatt.h>

#include "dexcom_service.h"

/* 0xFEBC — the short UUID a real Dexcom G6/G7 advertises. */
#define BT_UUID_DEXCOM_SVC_VAL 0xFEBC
static struct bt_uuid_16 dexcom_svc_uuid = BT_UUID_INIT_16(BT_UUID_DEXCOM_SVC_VAL);

/* Base F8083532-849E-531C-C594-30F1F86A4EA5; ...3535 control, ...3538 glucose. */
static struct bt_uuid_128 dexcom_control_uuid = BT_UUID_INIT_128(
	BT_UUID_128_ENCODE(0xf8083535, 0x849e, 0x531c, 0xc594, 0x30f1f86a4ea5));
static struct bt_uuid_128 dexcom_glucose_uuid = BT_UUID_INIT_128(
	BT_UUID_128_ENCODE(0xf8083538, 0x849e, 0x531c, 0xc594, 0x30f1f86a4ea5));

#define DEXCOM_OPCODE_EGV 0x4E
#define DEXCOM_STATE_OK   0x06
#define DEXCOM_MSG_LEN    14

static ssize_t write_control(struct bt_conn *conn, const struct bt_gatt_attr *attr,
			       const void *buf, uint16_t len, uint16_t offset, uint8_t flags)
{
	ARG_UNUSED(conn);
	ARG_UNUSED(attr);
	ARG_UNUSED(buf);
	ARG_UNUSED(flags);

	if (offset != 0) {
		return BT_GATT_ERR(BT_ATT_ERR_INVALID_OFFSET);
	}
	/* No auth handshake in this imitation — just accept and ACK. */
	printk("dexcom: control write len=%u (ignored)\n", len);
	return len;
}

static void control_ccc_changed(const struct bt_gatt_attr *attr, uint16_t value)
{
	ARG_UNUSED(attr);
	ARG_UNUSED(value);
}

static void glucose_ccc_changed(const struct bt_gatt_attr *attr, uint16_t value)
{
	ARG_UNUSED(attr);
	printk("dexcom: glucose notifications %s\n", value ? "enabled" : "disabled");
}

BT_GATT_SERVICE_DEFINE(dexcom_svc,
	BT_GATT_PRIMARY_SERVICE(&dexcom_svc_uuid),

	BT_GATT_CHARACTERISTIC(&dexcom_control_uuid.uuid,
		BT_GATT_CHRC_WRITE | BT_GATT_CHRC_NOTIFY,
		BT_GATT_PERM_WRITE,
		NULL, write_control, NULL),
	BT_GATT_CCC(control_ccc_changed, BT_GATT_PERM_READ | BT_GATT_PERM_WRITE),

	BT_GATT_CHARACTERISTIC(&dexcom_glucose_uuid.uuid,
		BT_GATT_CHRC_NOTIFY,
		0,
		NULL, NULL, NULL),
	BT_GATT_CCC(glucose_ccc_changed, BT_GATT_PERM_READ | BT_GATT_PERM_WRITE),
);

int dexcom_service_notify_glucose(uint16_t glucose_mg_dl, int8_t trend)
{
	static const struct bt_gatt_attr *glucose_attr;
	static uint32_t sequence;
	uint8_t msg[DEXCOM_MSG_LEN];

	if (!glucose_attr) {
		for (size_t i = 0; i < dexcom_svc.attr_count; i++) {
			if (bt_uuid_cmp(dexcom_svc.attrs[i].uuid, &dexcom_glucose_uuid.uuid) == 0) {
				glucose_attr = &dexcom_svc.attrs[i];
				break;
			}
		}
		if (!glucose_attr) {
			return -ENOENT;
		}
	}

	msg[0] = DEXCOM_OPCODE_EGV;
	msg[1] = 0; /* status */
	sys_put_le32(sequence++, &msg[2]);
	sys_put_le32(k_uptime_get_32() / 1000u, &msg[6]);
	sys_put_le16(glucose_mg_dl & 0x0FFF, &msg[10]);
	msg[12] = DEXCOM_STATE_OK;
	msg[13] = (uint8_t)trend;

	/* Negative (e.g. -ENOTCONN / no subscriber) is normal — ignore. */
	return bt_gatt_notify(NULL, glucose_attr, msg, sizeof(msg));
}
