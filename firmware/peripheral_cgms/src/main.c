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
#include "dexcom_g6.h"
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

/* LEDs (DK library numbering, in board order) and the mode button. One place, so a different
 * layout is a one-line change. The 1st LED is the connection indicator; the 2nd blinks in Multi
 * mode (3 sensors, standard BLE), the 3rd in Dexcom mode (1 sensor, G6). Holding Button 1 for
 * BUTTON_HOLD_MS toggles the mode. A short press of Button 2 injects a meal, of Button 3 a bout of
 * exercise, of Button 4 a PISA attenuation, into every sensor (the same instant events a BLE write
 * makes, for all slots). */
#define APP_LED         DK_LED1
#define LED_MULTI       DK_LED2
#define LED_DEXCOM      DK_LED3
#define MODE_BUTTON_MSK DK_BTN1_MSK
#define BUTTON_HOLD_MS  3000
#define FOOD_BUTTON_MSK     DK_BTN2_MSK
#define EXERCISE_BUTTON_MSK DK_BTN3_MSK
#define PISA_BUTTON_MSK     DK_BTN4_MSK
#define BUTTON_PISA_DURATION_MIN       10
#define BUTTON_PISA_DEPTH_FRAC         0.6f /* peak attenuation, at the middle of the bout */
#define BUTTON_FOOD_CARBS_G        30.0f
#define BUTTON_FOOD_DURATION_MIN   10
#define BUTTON_EXERCISE_DURATION_MIN   30
#define BUTTON_EXERCISE_INTENSITY_PCT  70.0f
#define SWITCH_LED_MS   1000 /* both mode LEDs steady while the board switches */
#define LED_TICK_MS     100
#define LED_SLOW_TICKS  5 /* 5 ticks = 500 ms: a 1 Hz blink at 50 % duty */

/* The board mode: SIM_COMM_SIG_CGMS ("Multi": N sensors, standard CGMS, no pairing) or
 * SIM_COMM_DEXCOM (1 sensor, the G6 protocol). Persisted in sim_config.comm_profile; changed only
 * through main_apply_comm_profile(), whether the request came over BLE or from the button. */
static volatile uint8_t g_mode = SIM_COMM_SIG_CGMS;
static volatile bool g_button_armed;
static volatile int64_t g_switch_until_ms;

static void led_blink_work_handler(struct k_work *work);
static K_WORK_DELAYABLE_DEFINE(led_blink_work, led_blink_work_handler);

static struct bt_cgms *g_cgms[MAX_SIM_SENSORS];
static struct bt_le_ext_adv *g_adv[MAX_SIM_SENSORS];
static struct bt_conn *g_conn[MAX_SIM_SENSORS]; /* per-identity active central */
static struct k_work adv_work;
static atomic_t adv_pending; /* bit i set => (re)start g_adv[i] from adv_work */

/* Re-advertise backoff for links that die young. A central that connects and
 * immediately hangs up (seen on hardware: this PC's Windows stack reconnecting
 * to identity 1 roughly twice a second, reason 0x13, on state that survived
 * unpairing and board resets) would otherwise get a fresh advertisement the
 * instant it disconnects, and every one of those cycles spends connection
 * events the other identities' links need. A link shorter than SHORT_LINK_MS
 * doubles the delay before that identity advertises again (capped); a link
 * that survives resets it, so a real client is never slowed down. */
#define SHORT_LINK_MS       2000
#define ADV_BACKOFF_BASE_MS 1000
#define ADV_BACKOFF_MAX_MS  30000
static int64_t g_link_start_ms[MAX_SIM_SENSORS];
static uint8_t g_short_links[MAX_SIM_SENSORS];
static struct k_work_delayable g_adv_retry[MAX_SIM_SENSORS];

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
	static uint32_t tick;
	static bool slow_on;

	ARG_UNUSED(work);
	tick++;
	if (tick % LED_SLOW_TICKS == 0) {
		slow_on = !slow_on;
		if (!any_connected()) {
			dk_set_led(APP_LED, slow_on);
		}
	}

	if (k_uptime_get() < g_switch_until_ms) {
		/* switching: both mode LEDs steady */
		dk_set_led_on(LED_MULTI);
		dk_set_led_on(LED_DEXCOM);
	} else if (g_button_armed) {
		/* the button is held: both flash fast until it is released or the hold completes */
		dk_set_led(LED_MULTI, tick & 1);
		dk_set_led(LED_DEXCOM, tick & 1);
	} else {
		dk_set_led(LED_MULTI, g_mode == SIM_COMM_SIG_CGMS && slow_on);
		dk_set_led(LED_DEXCOM, g_mode == SIM_COMM_DEXCOM && slow_on);
	}
	k_work_reschedule(&led_blink_work, K_MSEC(LED_TICK_MS));
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

/* The Dexcom board mode advertises 0xFEBC and the name xDrip scans for ("Dexcom" + the last two
 * characters of the transmitter ID). */
static const struct bt_data ad_dexcom[] = {
	BT_DATA_BYTES(BT_DATA_FLAGS, (BT_LE_AD_GENERAL | BT_LE_AD_NO_BREDR)),
	BT_DATA_BYTES(BT_DATA_UUID16_ALL, BT_UUID_16_ENCODE(BT_UUID_DEXCOM_VAL)),
};

/* The transmitter ID (a copy of sim_config.dexcom_tx_id; changed by main_apply_dexcom_id). */
static char g_tx_id[SIM_DEXCOM_ID_BUF] = SIM_DEXCOM_ID_DEFAULT;

/* ---- who advertises what, per board mode ---------------------------------------------------
 *   Multi  : identity i = "Nordic Glucose Sensor i+1", standard CGMS, every identity
 *   Dexcom : identity 0 = the G6 transmitter for the phone, identity 1 = "Nordic Glucose Sensor 1"
 *            for the PC app (the app binds a session to a slot by the digit in the name),
 *            identity 2 silent
 * The G6 identity is connectable only in a window around each reading (see g6_open_window). */

static bool g_g6_window_open = true;
static struct k_work_delayable g6_close_work;

static bool identity_is_g6(int i)
{
	return g_mode == SIM_COMM_DEXCOM && i == 0;
}

static bool identity_active(int i)
{
	if (i >= N_SENSORS) {
		return false;
	}
	return g_mode == SIM_COMM_DEXCOM ? i <= 1 : true;
}

static bool identity_wants_adv(int i)
{
	return identity_active(i) && (!identity_is_g6(i) || g_g6_window_open);
}

static void rebuild_names(void)
{
	for (int i = 0; i < N_SENSORS; i++) {
		if (identity_is_g6(i)) {
			g6_adv_name(g_tx_id, g_adv_name[i]);
		} else if (g_mode == SIM_COMM_DEXCOM) {
			snprintk(g_adv_name[i], sizeof(g_adv_name[i]), "%s 1", CONFIG_BT_DEVICE_NAME);
		} else if (N_SENSORS == 1) {
			strcpy(g_adv_name[i], CONFIG_BT_DEVICE_NAME);
		} else {
			snprintk(g_adv_name[i], sizeof(g_adv_name[i]), "%s %d",
				 CONFIG_BT_DEVICE_NAME, i + 1);
		}
	}
}

static int set_adv_data(int i)
{
	const struct bt_data sd[] = {
		BT_DATA(BT_DATA_NAME_COMPLETE, g_adv_name[i], strlen(g_adv_name[i])),
	};

	if (identity_is_g6(i)) {
		return bt_le_ext_adv_set_data(g_adv[i], ad_dexcom, ARRAY_SIZE(ad_dexcom), sd,
					      ARRAY_SIZE(sd));
	}
	return bt_le_ext_adv_set_data(g_adv[i], ad_cgms, ARRAY_SIZE(ad_cgms), sd, ARRAY_SIZE(sd));
}

static void arm_adv(int i)
{
	atomic_or(&adv_pending, BIT(i));
	k_work_submit(&adv_work);
}

static void adv_retry_handler(struct k_work *work)
{
	struct k_work_delayable *dwork = k_work_delayable_from_work(work);

	arm_adv((int)(dwork - g_adv_retry));
}

static void advertising_work_handler(struct k_work *work)
{
	ARG_UNUSED(work);

	atomic_val_t pending = atomic_clear(&adv_pending);

	for (int i = 0; i < N_SENSORS; i++) {
		if (!(pending & BIT(i))) {
			continue;
		}

		/* Always stop first: a switch re-arms sets that may still be advertising under the
		 * old mode's data, and an identity that no longer advertises must go quiet. */
		(void)bt_le_ext_adv_stop(g_adv[i]);
		if (!identity_wants_adv(i)) {
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

/* G6 connect window: a real transmitter is connectable only around each reading, and xDrip, which
 * schedules its next connection from the last one, stays locked to that grid. The window opens when
 * a reading is published (and at boot / mode entry) and closes CONFIG_APP_G6_WINDOW_S later unless a
 * client is still connected. 0, or a grid shorter than the window (speed multiplier), means always
 * open. */
static bool g6_always_open(void)
{
	float speed = comm_thread_speed_mult();

	return CONFIG_APP_G6_WINDOW_S == 0 || speed * (float)CONFIG_APP_G6_WINDOW_S >= 300.0f;
}

static void g6_open_window(void)
{
	bool was_open = g_g6_window_open;

	g_g6_window_open = true;
	if (!was_open && !g_conn[0]) {
		arm_adv(0);
	}
	if (g6_always_open()) {
		k_work_cancel_delayable(&g6_close_work);
	} else {
		k_work_reschedule(&g6_close_work, K_SECONDS(CONFIG_APP_G6_WINDOW_S));
	}
}

static void g6_close_handler(struct k_work *work)
{
	ARG_UNUSED(work);
	if (g_mode != SIM_COMM_DEXCOM) {
		return;
	}
	if (g_conn[0]) {
		/* a client is mid-session: never cut it, look again shortly */
		k_work_reschedule(&g6_close_work, K_SECONDS(5));
		return;
	}
	g_g6_window_open = false;
	arm_adv(0); /* the handler sees that identity 0 no longer wants advertising and stops it */
	printk("dexcom: connect window closed\n");
}

/* Called by comm_thread each time a new G6 reading is published. */
void main_g6_reading_published(void)
{
	if (g_mode == SIM_COMM_DEXCOM) {
		g6_open_window();
	}
}

/* Switches the board mode (SIM_COMM_*): the one entry point, reached from a BLE write to the
 * Board-mode characteristic or from the button (both via comm_thread, which has already updated and
 * will persist the config). Every link is dropped and every advertising set re-armed under the new
 * layout; disconnected() re-arms the sets whose link ended. */
void main_apply_comm_profile(uint8_t profile)
{
	if (profile == g_mode) {
		return;
	}
	g_mode = profile;
	g_switch_until_ms = k_uptime_get() + SWITCH_LED_MS;
	rebuild_names();
	printk("main: board mode -> %s\n", profile == SIM_COMM_DEXCOM ? "dexcom (G6, 1 sensor)"
								   : "multi (standard BLE)");

	k_work_cancel_delayable(&g6_close_work);
	g_g6_window_open = true; /* the first window opens at once */
	if (profile == SIM_COMM_DEXCOM && !g6_always_open()) {
		k_work_reschedule(&g6_close_work, K_SECONDS(CONFIG_APP_G6_WINDOW_S));
	}

	for (int i = 0; i < N_SENSORS; i++) {
		g_short_links[i] = 0;
		k_work_cancel_delayable(&g_adv_retry[i]);
		if (g_conn[i]) {
			(void)bt_conn_disconnect(g_conn[i], BT_HCI_ERR_REMOTE_USER_TERM_CONN);
		} else {
			arm_adv(i);
		}
	}
}

/* The transmitter ID changed: the advertised name derives from it, so re-advertise (Dexcom mode). */
void main_apply_dexcom_id(void)
{
	comm_thread_copy_dexcom_tx_id(g_tx_id);
	if (g_mode != SIM_COMM_DEXCOM) {
		return;
	}
	rebuild_names();
	if (g_conn[0]) {
		(void)bt_conn_disconnect(g_conn[0], BT_HCI_ERR_REMOTE_USER_TERM_CONN);
	} else {
		arm_adv(0);
	}
}

/* ---- the mode button: hold Button 1 for BUTTON_HOLD_MS --------------------------------------- */

static struct k_work_delayable button_hold_work;

static void button_hold_handler(struct k_work *work)
{
	uint8_t next = g_mode == SIM_COMM_DEXCOM ? SIM_COMM_SIG_CGMS : SIM_COMM_DEXCOM;

	ARG_UNUSED(work);
	g_button_armed = false;
	printk("main: mode button held -> switching\n");
	/* The same queue a BLE write goes through: works with no client, and in CGMS-only mode. */
	if (comm_thread_enqueue_config(CFG_MSG_COMM_PROFILE, &next, 1) != 0) {
		printk("main: mode switch request dropped (config queue full)\n");
	}
}

/* A press of the food or exercise button: the same payload a BLE write carries, but for
 * every sensor slot at once; it works with no client connected. Ignored while the mode button
 * is held, so a slip of the finger during the hold does not also inject an event. */
static void button_inject(uint32_t button_state, uint32_t has_changed)
{
	if (g_button_armed || !(button_state & has_changed)) {
		return; /* a release, or the mode hold is in progress */
	}
	if (has_changed & FOOD_BUTTON_MSK) {
		struct food_instant_wire wire = {
			.duration_min = BUTTON_FOOD_DURATION_MIN,
			.carbs_g = BUTTON_FOOD_CARBS_G,
		};

		printk("main: food button -> %.0f g over %u min\n", (double)wire.carbs_g,
		       wire.duration_min);
		if (comm_thread_enqueue_config(CFG_MSG_FOOD_INSTANT_ALL, &wire, sizeof(wire)) != 0) {
			printk("main: food request dropped (config queue full)\n");
		}
	}
	if (has_changed & EXERCISE_BUTTON_MSK) {
		struct exercise_instant_wire wire = {
			.duration_min = BUTTON_EXERCISE_DURATION_MIN,
			.intensity_pct = BUTTON_EXERCISE_INTENSITY_PCT,
		};

		printk("main: exercise button -> %u min at %.0f %%\n", wire.duration_min,
		       (double)wire.intensity_pct);
		if (comm_thread_enqueue_config(CFG_MSG_EXERCISE_INSTANT_ALL, &wire, sizeof(wire)) != 0) {
			printk("main: exercise request dropped (config queue full)\n");
		}
	}
	if (has_changed & PISA_BUTTON_MSK) {
		struct pisa_instant_wire wire = {
			.duration_min = BUTTON_PISA_DURATION_MIN,
			.depth_frac = BUTTON_PISA_DEPTH_FRAC,
		};

		printk("main: PISA button -> %u min, %.0f %% attenuation\n", wire.duration_min,
		       (double)wire.depth_frac * 100.0);
		if (comm_thread_enqueue_config(CFG_MSG_PISA_INSTANT_ALL, &wire, sizeof(wire)) != 0) {
			printk("main: PISA request dropped (config queue full)\n");
		}
	}
}

static void button_changed(uint32_t button_state, uint32_t has_changed)
{
	button_inject(button_state,
		      has_changed & (FOOD_BUTTON_MSK | EXERCISE_BUTTON_MSK | PISA_BUTTON_MSK));
	if (!(has_changed & MODE_BUTTON_MSK)) {
		return;
	}
	if (button_state & MODE_BUTTON_MSK) {
		g_button_armed = true;
		k_work_reschedule(&button_hold_work, K_MSEC(BUTTON_HOLD_MS));
	} else {
		g_button_armed = false; /* released early: a short press does nothing */
		k_work_cancel_delayable(&button_hold_work);
	}
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
	g_link_start_ms[id] = k_uptime_get();
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

	if (identity_is_g6(id)) {
		/* A G6 poll is a short link by design, not a connect storm: no backoff. Whether it
		 * advertises again depends on the connect window. */
		arm_adv(id);
		return;
	}
	if (!identity_active(id)) {
		return; /* this identity is silent in the current board mode */
	}

	if (k_uptime_get() - g_link_start_ms[id] >= SHORT_LINK_MS) {
		g_short_links[id] = 0;
		arm_adv(id);
		return;
	}
	if (g_short_links[id] < 16) {
		g_short_links[id]++;
	}
	int64_t delay_ms = (int64_t)ADV_BACKOFF_BASE_MS << (g_short_links[id] - 1);

	if (delay_ms > ADV_BACKOFF_MAX_MS) {
		delay_ms = ADV_BACKOFF_MAX_MS;
	}
	printk("identity %d: short link #%u, re-advertising in %lld ms\n", id, g_short_links[id],
	       delay_ms);
	k_work_reschedule(&g_adv_retry[id], K_MSEC(delay_ms));
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

/* Address generation for the secondary identities. Bumping it moves every
 * secondary identity to a fresh address, which a host treats as a brand-new
 * device: whatever it cached for the old address (GATT service device nodes,
 * pairing records) can no longer be reached, because nothing advertises there
 * any more. Bumped 0x9E -> 0x9F after this PC's Windows stack kept
 * reconnecting to identity 1's old address twice a second on stale state that
 * survived unpairing, a pnputil removal and board resets. */
#define SECONDARY_ID_ADDR_GEN 0x9F

static void secondary_identity_addr(size_t i, bt_addr_le_t *addr)
{
	addr->type = BT_ADDR_LE_RANDOM;
	addr->a.val[0] = 0xA0 + (uint8_t)i;
	addr->a.val[1] = SECONDARY_ID_ADDR_GEN;
	addr->a.val[2] = 0x2C;
	addr->a.val[3] = 0x5B;
	addr->a.val[4] = 0x11;
	addr->a.val[5] = 0xC0 | (uint8_t)i; /* MSB 11xxxxxx => static random */
}

/* Creates the N_SENSORS - 1 secondary BLE identities (identity 0 is the
 * factory default). Each gets a stable static random address (top two MSB
 * bits 11), deterministic so a host re-pairs to the same address after a
 * board reboot. CONFIG_BT_SETTINGS restores identities across reboots, so an
 * already-stored identity whose address no longer matches is re-addressed with
 * bt_id_reset() (which also drops its pairing keys) — without that, changing
 * SECONDARY_ID_ADDR_GEN would do nothing on an already-provisioned board.
 * No-op when N_SENSORS == 1. */
static void create_identities(void)
{
	bt_addr_le_t ids[CONFIG_BT_ID_MAX];
	size_t count = ARRAY_SIZE(ids);

	bt_id_get(ids, &count);

	for (size_t i = 1; i < N_SENSORS; i++) {
		bt_addr_le_t addr;

		secondary_identity_addr(i, &addr);
		if (i >= count) {
			int id = bt_id_create(&addr, NULL);

			if (id < 0) {
				printk("bt_id_create(%zu) failed (err %d)\n", i, id);
			}
		} else if (!bt_addr_le_eq(&ids[i], &addr)) {
			int err = bt_id_reset((uint8_t)i, &addr, NULL);

			printk("identity %zu: re-addressed to generation 0x%02X (err %d)\n", i,
			       SECONDARY_ID_ADDR_GEN, err < 0 ? err : 0);
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
		/* CGMS session length, in HOURS (cgms.c: k_timer_start(...,
		 * K_HOURS(srt))). When it expires, stop_session() latches
		 * CGMS_STATUS_POS_SESSION_STOPPED and the instance goes silent for
		 * good: bt_cgms_measurement_add() returns -ENOENT and report_meas()
		 * stops rescheduling itself. Nothing restarts it — the only place
		 * that clears the bit is bt_cgms_init() — so the board keeps
		 * advertising, keeps accepting connections and keeps running its
		 * model while never notifying another measurement, which reads as
		 * "the sensor connects but sends no data" and needs a reboot to
		 * clear. This was 1 hour, which is shorter than a long bench run
		 * (and far shorter than an overnight soak: see scripts/ble_soak.py).
		 * A week is past any plausible session; the simulator has no reason
		 * to model sensor end-of-life. */
		.session_run_time = 24 * 7,
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
	k_work_init_delayable(&button_hold_work, button_hold_handler);
	k_work_init_delayable(&g6_close_work, g6_close_handler);
	err = dk_buttons_init(button_changed);
	if (err) {
		printk("Buttons init failed (err %d); the buttons are unavailable\n", err);
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
	g_mode = cfg.comm_profile;
	memcpy(g_tx_id, cfg.dexcom_tx_id, sizeof(g_tx_id));
	rebuild_names();
	printk("Board mode: %s\n", g_mode == SIM_COMM_DEXCOM ? "dexcom (G6, 1 sensor)"
							       : "multi (standard BLE)");

	for (int i = 0; i < N_SENSORS; i++) {
		err = bt_cgms_init(&params, &g_cgms[i]);
		if (err) {
			printk("cgms %d: init failed (err %d)\n", i, err);
			return 0;
		}
	}

	k_work_init(&adv_work, advertising_work_handler);
	for (int i = 0; i < N_SENSORS; i++) {
		k_work_init_delayable(&g_adv_retry[i], adv_retry_handler);
	}

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

	/* In Dexcom mode the first G6 connect window opens now. */
	g_g6_window_open = true;
	if (g_mode == SIM_COMM_DEXCOM && !g6_always_open()) {
		k_work_reschedule(&g6_close_work, K_SECONDS(CONFIG_APP_G6_WINDOW_S));
	}

	atomic_set(&adv_pending, BIT_MASK(N_SENSORS));
	k_work_submit(&adv_work);
	k_work_reschedule(&led_blink_work, K_NO_WAIT);

	return 0;
}
