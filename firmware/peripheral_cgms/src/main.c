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
#include <zephyr/sys/atomic.h>
#include <zephyr/sys/util.h>
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
#include "csv_store.h"
#include "config_service.h"
#include "dexcom_service.h"
#include "model_thread.h"
#include "comm_thread.h"

/*
 * Multi-sensor simulator build (see SensorSimulator/PROTOCOL_SPEC.md §7): the
 * board runs SIM_SENSOR_COUNT (CONFIG_APP_SENSOR_COUNT, 1..MAX_SIM_SENSORS)
 * fully independent CGM sensors, each its own BLE identity + advertising set +
 * CGMS service instance, all driven by model_thread (per-slot physiological
 * model + sensor noise, or CSV playback) and comm_thread (BLE + config
 * persistence) — the assignment's two-thread split. N == 1 is the original
 * single-sensor build and is behaviourally identical to it (one default
 * identity, one adv set, the Dexcom comm-profile path still available).
 *
 * Pairing note (see prj.conf): Windows aborts LE Secure Connections against a
 * peripheral's non-default identities when they use RPAs, so CONFIG_BT_PRIVACY
 * is off and the secondary identities below get stable static random
 * addresses. If SC pairing still fails on identities 1..N-1, build with
 * CONFIG_APP_CGMS_NO_AUTH=y to drop the auth requirement on the CGMS
 * characteristics entirely (Option A fallback).
 */

#define N_SENSORS SIM_SENSOR_COUNT

#define LED_BLINK_INTERVAL_MS 500
#define APP_LED DK_LED1

static void led_blink_work_handler(struct k_work *work);
static K_WORK_DELAYABLE_DEFINE(led_blink_work, led_blink_work_handler);

static struct bt_cgms *g_cgms[MAX_SIM_SENSORS];
static struct bt_le_ext_adv *g_adv[MAX_SIM_SENSORS];
static struct bt_conn *g_conn[MAX_SIM_SENSORS]; /* per-identity active central */
static struct k_work adv_work;
static atomic_t adv_pending; /* bit i set => (re)start g_adv[i] from adv_work */

/* Per-identity scan-response name. For N == 1 this is exactly
 * CONFIG_BT_DEVICE_NAME (no suffix) so the single-sensor build advertises the
 * same name it always has; for N > 1 each identity gets a trailing index the
 * app parses to bind that connection to one sensor row (see
 * services/ble_session.py's _own_instance_index). */
static char g_adv_name[MAX_SIM_SENSORS][sizeof(CONFIG_BT_DEVICE_NAME) + 4];

static bool any_connected(void)
{
	for (int i = 0; i < N_SENSORS; i++) {
		if (g_conn[i]) {
			return true;
		}
	}
	return false;
}

static void led_blink_work_handler(struct k_work *work)
{
	static bool led_on;

	ARG_UNUSED(work);

	led_on = !led_on;
	if (!any_connected()) {
		dk_set_led(APP_LED, led_on);
	}
	k_work_reschedule(&led_blink_work, K_MSEC(LED_BLINK_INTERVAL_MS));
}

/* SIG CGMS advertises the standard 0x181F service + DIS; the per-identity name
 * goes in the scan response, built in set_adv_data(). */
#define BT_UUID_DEXCOM_VAL 0xFEBC

static const struct bt_data ad_cgms[] = {
	BT_DATA_BYTES(BT_DATA_FLAGS, (BT_LE_AD_GENERAL | BT_LE_AD_NO_BREDR)),
	BT_DATA_BYTES(BT_DATA_UUID16_ALL,
				BT_UUID_16_ENCODE(BT_UUID_CGMS_VAL),
				BT_UUID_16_ENCODE(BT_UUID_DIS_VAL)),
};

#if N_SENSORS == 1
/* The Dexcom-style comm profile is a single-sensor-only feature (see
 * PROTOCOL_SPEC.md's "Comm profile" section): it advertises 0xFEBC + a
 * "DXCM01" name so a Dexcom-style client recognises it. Multi-sensor builds
 * always advertise SIG CGMS. */
#define DEXCOM_ADV_NAME "DXCM01"
static const struct bt_data ad_dexcom[] = {
	BT_DATA_BYTES(BT_DATA_FLAGS, (BT_LE_AD_GENERAL | BT_LE_AD_NO_BREDR)),
	BT_DATA_BYTES(BT_DATA_UUID16_ALL, BT_UUID_16_ENCODE(BT_UUID_DEXCOM_VAL)),
};
static const struct bt_data sd_dexcom[] = {
	BT_DATA(BT_DATA_NAME_COMPLETE, DEXCOM_ADV_NAME, sizeof(DEXCOM_ADV_NAME) - 1),
};
static uint8_t g_comm_profile = SIM_COMM_SIG_CGMS;
#endif /* N_SENSORS == 1 */

static int set_adv_data(int i)
{
	const struct bt_data sd[] = {
		BT_DATA(BT_DATA_NAME_COMPLETE, g_adv_name[i], strlen(g_adv_name[i])),
	};

#if N_SENSORS == 1
	if (g_comm_profile == SIM_COMM_DEXCOM) {
		return bt_le_ext_adv_set_data(g_adv[i], ad_dexcom, ARRAY_SIZE(ad_dexcom),
					      sd_dexcom, ARRAY_SIZE(sd_dexcom));
	}
#endif
	return bt_le_ext_adv_set_data(g_adv[i], ad_cgms, ARRAY_SIZE(ad_cgms),
				      sd, ARRAY_SIZE(sd));
}

static void arm_adv(int i)
{
	atomic_or(&adv_pending, BIT(i));
	k_work_submit(&adv_work);
}

static void advertising_work_handler(struct k_work *work)
{
	ARG_UNUSED(work);

	atomic_val_t pending = atomic_clear(&adv_pending);

	for (int i = 0; i < N_SENSORS; i++) {
		if (!(pending & BIT(i))) {
			continue;
		}

		int err = set_adv_data(i);

		if (err) {
			printk("adv %d: failed to set data (err %d)\n", i, err);
			continue;
		}

		err = bt_le_ext_adv_start(g_adv[i], BT_LE_EXT_ADV_START_DEFAULT);
		if (err) {
			printk("adv %d: failed to start (err %d)\n", i, err);
			continue;
		}
		printk("Advertising started: identity %d \"%s\"\n", i, g_adv_name[i]);
	}
}

/* Called by comm_thread when a Comm-profile config write is applied. Only
 * meaningful for the single-sensor build — multi-sensor always streams SIG
 * CGMS. A connectable adv set cannot restart while its connection slot is
 * occupied, so if a client is connected, drop it: disconnected() then
 * re-advertises under the new profile and the app reconnects automatically. */
void main_apply_comm_profile(uint8_t profile)
{
#if N_SENSORS == 1
	if (profile == g_comm_profile) {
		return;
	}
	g_comm_profile = profile;
	printk("main: comm_profile -> %s\n", profile == SIM_COMM_DEXCOM ? "dexcom" : "sig-cgms");

	if (g_conn[0]) {
		printk("main: dropping client to re-advertise under new profile\n");
		(void)bt_conn_disconnect(g_conn[0], BT_HCI_ERR_REMOTE_USER_TERM_CONN);
	} else {
		(void)bt_le_ext_adv_stop(g_adv[0]);
		arm_adv(0);
	}
#else
	ARG_UNUSED(profile); /* comm profile is fixed to SIG CGMS when N_SENSORS > 1 */
#endif
}

static int conn_identity(struct bt_conn *conn)
{
	struct bt_conn_info info;

	if (bt_conn_get_info(conn, &info) == 0 && info.id < N_SENSORS) {
		return info.id;
	}
	return 0;
}

static void connected(struct bt_conn *conn, uint8_t err)
{
	char addr[BT_ADDR_LE_STR_LEN];
	int id = conn_identity(conn);

	bt_addr_le_to_str(bt_conn_get_dst(conn), addr, sizeof(addr));
	if (err) {
		printk("Failed to connect to %s (identity %d), err 0x%02x %s\n", addr, id, err,
		       bt_hci_err_to_str(err));
		arm_adv(id);
		return;
	}

	printk("Connected %s (identity %d)\n", addr, id);
	g_conn[id] = conn;
	dk_set_led_on(APP_LED);

	/* With up to N_SENSORS links open at once, the radio cannot service them
	 * all at the ~15-30 ms interval a central picks for service discovery —
	 * the newest link gets starved of connection events and dropped mid-
	 * subscribe ("could not subscribe to any (Not connected)" app-side).
	 * Ask for a relaxed 30-50 ms interval right away; measurements are only
	 * every 5 s so nothing needs faster, and 4 * 50 ms of event budget fits
	 * comfortably. The central may defer the update until discovery settles,
	 * which is fine. */
	static const struct bt_le_conn_param relaxed = BT_LE_CONN_PARAM_INIT(24, 40, 0, 400);
	int perr = bt_conn_le_param_update(conn, &relaxed);

	if (perr && perr != -EALREADY) {
		printk("conn param update (identity %d) requested, err %d\n", id, perr);
	}

	/* Windows will not spontaneously start LE-SC pairing when it first
	 * touches an AUTHEN-gated attribute on a *non-default* identity — it
	 * just drops the link (reason 0x13). Sending an SMP Security Request
	 * from the peripheral the moment the link is up makes the central begin
	 * the pairing ceremony instead. Harmless on identity 0 (already pairs
	 * fine) and on an already-bonded reconnect (encrypts from the stored
	 * keys). Skipped under CONFIG_APP_CGMS_NO_AUTH (nothing needs
	 * encryption then). */
#if !defined(CONFIG_APP_CGMS_NO_AUTH)
	int serr = bt_conn_set_security(conn, BT_SECURITY_L2);

	if (serr) {
		printk("bt_conn_set_security (identity %d) failed (err %d)\n", id, serr);
	}
#endif
}

static void disconnected(struct bt_conn *conn, uint8_t reason)
{
	char addr[BT_ADDR_LE_STR_LEN];
	int id = conn_identity(conn);

	bt_addr_le_to_str(bt_conn_get_dst(conn), addr, sizeof(addr));
	printk("Disconnected from %s (identity %d), reason 0x%02x %s\n", addr, id, reason,
	       bt_hci_err_to_str(reason));

	g_conn[id] = NULL;
	if (!any_connected()) {
		dk_set_led_off(APP_LED);
	}
	arm_adv(id);
}

static void security_changed(struct bt_conn *conn, bt_security_t level, enum bt_security_err err)
{
	char addr[BT_ADDR_LE_STR_LEN];
	int id = conn_identity(conn);

	bt_addr_le_to_str(bt_conn_get_dst(conn), addr, sizeof(addr));
	if (!err) {
		printk("Security changed: %s (identity %d) level %u\n", addr, id, level);
	} else {
		printk("Security failed: %s (identity %d) level %u err %d %s\n", addr, id, level, err,
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

/* Creates the N_SENSORS - 1 secondary BLE identities (identity 0 is the
 * factory default). Each gets a stable static random address (top two MSB
 * bits 11), deterministic so a host re-pairs to the same address after a
 * board reboot. Skips identities that already exist (CONFIG_BT_SETTINGS
 * restores them across reboots). No-op when N_SENSORS == 1. */
static void create_identities(void)
{
	bt_addr_le_t ids[CONFIG_BT_ID_MAX];
	size_t count = ARRAY_SIZE(ids);

	bt_id_get(ids, &count);

	for (size_t i = count; i < N_SENSORS; i++) {
		bt_addr_le_t addr = { .type = BT_ADDR_LE_RANDOM };

		addr.a.val[0] = 0xA0 + (uint8_t)i;
		addr.a.val[1] = 0x9E;
		addr.a.val[2] = 0x2C;
		addr.a.val[3] = 0x5B;
		addr.a.val[4] = 0x11;
		addr.a.val[5] = 0xC0 | (uint8_t)i; /* MSB 11xxxxxx => static random */

		int id = bt_id_create(&addr, NULL);

		if (id < 0) {
			printk("bt_id_create(%zu) failed (err %d)\n", i, id);
		}
	}

	count = ARRAY_SIZE(ids);
	bt_id_get(ids, &count);
	for (size_t i = 0; i < count; i++) {
		char s[BT_ADDR_LE_STR_LEN];

		bt_addr_le_to_str(&ids[i], s, sizeof(s));
		printk("Identity %zu: %s\n", i, s);
	}
}

int main(void)
{
	int err;
	/* static: struct sim_config is ~2.85 KB with four sensor slots — too big
	 * to sit on the main thread's stack. main() runs once, no reentrancy. */
	static struct sim_config cfg;
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

	printk("Starting Bluetooth Peripheral CGM simulator (%d sensor%s)\n", N_SENSORS,
	       N_SENSORS == 1 ? "" : "s");

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

	create_identities();

	sim_config_load_from_flash(&cfg);
	csv_store_load_manifest();
#if N_SENSORS == 1
	g_comm_profile = cfg.comm_profile;
#endif

	for (int i = 0; i < N_SENSORS; i++) {
		if (N_SENSORS == 1) {
			strcpy(g_adv_name[i], CONFIG_BT_DEVICE_NAME);
		} else {
			snprintk(g_adv_name[i], sizeof(g_adv_name[i]), "%s %d",
				 CONFIG_BT_DEVICE_NAME, i + 1);
		}

		err = bt_cgms_init(&params, &g_cgms[i]);
		if (err) {
			printk("cgms %d: init failed (err %d)\n", i, err);
			return 0;
		}
	}

	k_work_init(&adv_work, advertising_work_handler);

	struct bt_le_adv_param adv_param = BT_LE_ADV_PARAM_INIT(
		BT_LE_ADV_OPT_CONN, BT_GAP_ADV_FAST_INT_MIN_2, BT_GAP_ADV_FAST_INT_MAX_2, NULL);

	for (int i = 0; i < N_SENSORS; i++) {
		adv_param.id = i;
		err = bt_le_ext_adv_create(&adv_param, NULL, &g_adv[i]);
		if (err) {
			printk("adv %d: create failed (err %d)\n", i, err);
			return 0;
		}
	}
	/* Advertising data is set per identity/comm_profile in set_adv_data(). */

	config_service_init();
	comm_thread_start(g_cgms, &cfg);
	model_thread_start(&cfg);

	atomic_set(&adv_pending, BIT_MASK(N_SENSORS));
	k_work_submit(&adv_work);
	k_work_reschedule(&led_blink_work, K_NO_WAIT);

	return 0;
}
