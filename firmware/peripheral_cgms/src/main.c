/*
 * Copyright (c) 2022 Nordic Semiconductor ASA
 *
 * SPDX-License-Identifier: LicenseRef-Nordic-5-Clause
 */

#include <zephyr/types.h>
#include <stddef.h>
#include <string.h>
#include <stdio.h>
#include <errno.h>
#include <zephyr/sys/printk.h>
#include <zephyr/sys/byteorder.h>
#include <zephyr/kernel.h>
#include <zephyr/settings/settings.h>

#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/hci.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/uuid.h>
#include <zephyr/bluetooth/gatt.h>
#include <bluetooth/services/cgms.h>
#include <sfloat.h>
#include <dk_buttons_and_leds.h>

#include "sim_config.h"
#include "config_service.h"
#include "model_thread.h"
#include "comm_thread.h"

/*
 * Single-sensor simulator build (see SensorSimulator/PROTOCOL_SPEC.md): one
 * CGMS instance, one BLE identity, driven by model_thread (physiological
 * model + sensor noise) and comm_thread (BLE + config persistence) — the
 * assignment's two-thread requirement. The original sample's 4-virtual-
 * sensor design (one identity/adv-set/CGMS-instance per sensor) has been
 * trimmed down; see git history / the nrf SDK sample tree for that version.
 */

#define LED_BLINK_INTERVAL_MS 500
#define APP_LED DK_LED1

static void led_blink_work_handler(struct k_work *work);
static K_WORK_DELAYABLE_DEFINE(led_blink_work, led_blink_work_handler);

static struct bt_cgms *g_cgms;
static struct bt_le_ext_adv *g_adv;
static struct k_work adv_work;
static bool g_connected;

static void led_blink_work_handler(struct k_work *work)
{
	static bool led_on;

	ARG_UNUSED(work);

	led_on = !led_on;
	if (!g_connected) {
		dk_set_led(APP_LED, led_on);
	}
	k_work_reschedule(&led_blink_work, K_MSEC(LED_BLINK_INTERVAL_MS));
}

static const struct bt_data ad[] = {
	BT_DATA_BYTES(BT_DATA_FLAGS, (BT_LE_AD_GENERAL | BT_LE_AD_NO_BREDR)),
	BT_DATA_BYTES(BT_DATA_UUID16_ALL,
				BT_UUID_16_ENCODE(BT_UUID_CGMS_VAL),
				BT_UUID_16_ENCODE(BT_UUID_DIS_VAL)),
};
static const struct bt_data sd[] = {
	BT_DATA(BT_DATA_NAME_COMPLETE, CONFIG_BT_DEVICE_NAME, sizeof(CONFIG_BT_DEVICE_NAME) - 1),
};

static void advertising_work_handler(struct k_work *work)
{
	ARG_UNUSED(work);

	int err = bt_le_ext_adv_start(g_adv, BT_LE_EXT_ADV_START_DEFAULT);

	if (err) {
		printk("Advertising failed to start (err %d)\n", err);
		return;
	}
	printk("Advertising successfully started\n");
}

static void advertising_start(void)
{
	k_work_submit(&adv_work);
}

static void connected(struct bt_conn *conn, uint8_t err)
{
	char addr[BT_ADDR_LE_STR_LEN];

	bt_addr_le_to_str(bt_conn_get_dst(conn), addr, sizeof(addr));
	if (err) {
		printk("Failed to connect to %s, err 0x%02x %s\n", addr, err,
		       bt_hci_err_to_str(err));
		return;
	}

	printk("Connected %s\n", addr);
	g_connected = true;
	dk_set_led_on(APP_LED);
}

static void disconnected(struct bt_conn *conn, uint8_t reason)
{
	char addr[BT_ADDR_LE_STR_LEN];

	bt_addr_le_to_str(bt_conn_get_dst(conn), addr, sizeof(addr));
	printk("Disconnected from %s, reason 0x%02x %s\n", addr, reason,
	       bt_hci_err_to_str(reason));

	g_connected = false;
	dk_set_led_off(APP_LED);
	advertising_start();
}

static void security_changed(struct bt_conn *conn, bt_security_t level, enum bt_security_err err)
{
	char addr[BT_ADDR_LE_STR_LEN];

	bt_addr_le_to_str(bt_conn_get_dst(conn), addr, sizeof(addr));
	if (!err) {
		printk("Security changed: %s level %u\n", addr, level);
	} else {
		printk("Security failed: %s level %u err %d %s\n", addr, level, err,
		       bt_security_err_to_str(err));
	}
}

BT_CONN_CB_DEFINE(conn_callbacks) = {
	.connected = connected,
	.disconnected = disconnected,
	.security_changed = security_changed,
};

static void auth_cancel(struct bt_conn *conn)
{
	char addr[BT_ADDR_LE_STR_LEN];

	bt_addr_le_to_str(bt_conn_get_dst(conn), addr, sizeof(addr));
	printk("Pairing cancelled: %s\n", addr);
}

static void auth_passkey_display(struct bt_conn *conn, unsigned int passkey)
{
	ARG_UNUSED(conn);
	printk("Pairing key is %06d.\n", passkey);
}

/* Fixed for testing so the same passkey (123456) can be typed into the host's
 * pairing prompt every time without needing to watch this console for it.
 */
#define TEST_FIXED_PASSKEY 123456

static uint32_t auth_app_passkey(struct bt_conn *conn)
{
	ARG_UNUSED(conn);
	return TEST_FIXED_PASSKEY;
}

static struct bt_conn_auth_cb auth_cb_display = {
	.cancel = auth_cancel,
	.passkey_display = auth_passkey_display,
	.app_passkey = auth_app_passkey,
};

static void cgms_session_state_changed(struct bt_cgms *cgms, const bool state)
{
	ARG_UNUSED(cgms);

	/* Informational only — comm_thread pushes measurements independent of
	 * this (see comm_thread.c's push_measurement_and_status()) since this
	 * event fires once at bt_cgms_init(), before any client ever
	 * connects, and is never re-armed per reconnect. */
	printk("CGMS session %s.\n", state ? "starts" : "stops");
}

int main(void)
{
	int err;
	struct sim_config cfg;
	struct bt_cgms_cb cb = {
		.session_state_changed = cgms_session_state_changed,
	};
	struct bt_cgms_init_param params = {
		.type = BT_CGMS_FEAT_TYPE_CAP_PLASMA,
		.sample_location = BT_CGMS_FEAT_LOC_FINGER,
		/* The session will run 1 hour. */
		.session_run_time = 1,
		/* cgms.c has been patched to interpret this field in seconds
		 * instead of minutes, for faster bench testing; actual push
		 * cadence is driven by comm_thread/model_thread, not this
		 * value (see PROTOCOL_SPEC.md §6).
		 */
		.initial_comm_interval = 5,
		.cb = &cb,
	};

	printk("Starting Bluetooth Peripheral CGM simulator (single sensor)\n");

	err = dk_leds_init();
	if (err) {
		printk("LEDs init failed (err %d)\n", err);
		return 0;
	}

	bt_conn_auth_cb_register(&auth_cb_display);

	err = bt_enable(NULL);
	if (err) {
		printk("Bluetooth init failed (err %d)\n", err);
		return 0;
	}
	printk("Bluetooth initialized\n");

	if (IS_ENABLED(CONFIG_SETTINGS)) {
		settings_load();
	}

	sim_config_load_from_flash(&cfg);

	err = bt_cgms_init(&params, &g_cgms);
	if (err) {
		printk("Error occurred when initializing cgm service (err %d)\n", err);
		return 0;
	}

	k_work_init(&adv_work, advertising_work_handler);

	struct bt_le_adv_param adv_param = BT_LE_ADV_PARAM_INIT(
		BT_LE_ADV_OPT_CONN, BT_GAP_ADV_FAST_INT_MIN_2, BT_GAP_ADV_FAST_INT_MAX_2, NULL);

	err = bt_le_ext_adv_create(&adv_param, NULL, &g_adv);
	if (err) {
		printk("Failed to create advertising set (err %d)\n", err);
		return 0;
	}

	err = bt_le_ext_adv_set_data(g_adv, ad, ARRAY_SIZE(ad), sd, ARRAY_SIZE(sd));
	if (err) {
		printk("Failed to set advertising data (err %d)\n", err);
		return 0;
	}

	config_service_init();
	comm_thread_start(g_cgms, &cfg);
	model_thread_start(&cfg);

	advertising_start();
	k_work_reschedule(&led_blink_work, K_NO_WAIT);

	return 0;
}
