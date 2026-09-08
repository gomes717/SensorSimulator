/*
 * Copyright (c) 2022 Nordic Semiconductor ASA
 *
 * SPDX-License-Identifier: LicenseRef-Nordic-5-Clause
 */

#include <zephyr/types.h>
#include <stddef.h>
#include <string.h>
#include <errno.h>
#include <zephyr/kernel.h>
#include <zephyr/net_buf.h>
#include <zephyr/logging/log.h>

#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/hci.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/uuid.h>
#include <zephyr/bluetooth/gatt.h>
#include <bluetooth/services/cgms.h>

#include "cgms_internal.h"

LOG_MODULE_REGISTER(cgms, CONFIG_BT_CGMS_LOG_LEVEL);

#define CGMS_SVC_MEAS_ATTR_IDX     1
#define CGMS_SVC_RACP_ATTR_IDX     12
#define CGMS_SVC_SOCP_ATTR_IDX     15

#define CGMS_FEAT_LENGTH           6
#define CGMS_MEAS_LENGTH           15
#define CGMS_STATUS_LENGTH         5
#define CGMS_SST_LENGTH            9

/* The fastest communication interval supported by the device */
#define CGMS_COMM_INTERVAL_MIN     1

/* We instantiate up to CONFIG_BT_CGMS_INSTANCE_COUNT CGMS servers, each
 * holding the parameters that describe its own state. Per the Bluetooth
 * CGMS specification a single physical sensor should only expose one
 * instance; multiple instances here represent multiple independent virtual
 * sensors (e.g. for a multi-sensor test rig), each with its own GATT
 * service, session, and RACP database.
 */
static struct bt_cgms cgms_insts[CONFIG_BT_CGMS_INSTANCE_COUNT];
static uint32_t cgms_inst_cnt;

static void cgms_meas_ccc_cfg_changed(const struct bt_gatt_attr *attr, uint16_t value)
{
	ARG_UNUSED(attr);
	LOG_INF("CGMS Measurement: notification %s",
			value == BT_GATT_CCC_NOTIFY ? "enabled" : "disabled");
}

static ssize_t read_feature(struct bt_conn *conn, const struct bt_gatt_attr *attr,
			void *buf, uint16_t len, uint16_t offset)
{
	struct bt_cgms *cgms = (struct bt_cgms *)attr->user_data;
	struct net_buf_simple *feature_buf = NET_BUF_SIMPLE(CGMS_FEAT_LENGTH);

	net_buf_simple_init(feature_buf, 0);

	net_buf_simple_add_le24(feature_buf, cgms->feature.feature);
	net_buf_simple_add_u8(feature_buf,
		(cgms->feature.type & 0xF) |
		((cgms->feature.sample_location & 0xF) << 4));
	net_buf_simple_add_le16(feature_buf, 0xFFFF);

	return bt_gatt_attr_read(conn, attr, buf, len, offset, feature_buf->data, feature_buf->len);
}

static ssize_t read_session_start_time(struct bt_conn *conn, const struct bt_gatt_attr *attr,
			void *buf, uint16_t len, uint16_t offset)
{
	struct bt_cgms *cgms = (struct bt_cgms *)attr->user_data;
	struct net_buf_simple *sst_buf = NET_BUF_SIMPLE(CGMS_SST_LENGTH);

	net_buf_simple_init(sst_buf, 0);

	net_buf_simple_add_le16(sst_buf, cgms->sst.year);
	net_buf_simple_add_u8(sst_buf, cgms->sst.month);
	net_buf_simple_add_u8(sst_buf, cgms->sst.day);
	net_buf_simple_add_u8(sst_buf, cgms->sst.hour);
	net_buf_simple_add_u8(sst_buf, cgms->sst.minute);
	net_buf_simple_add_u8(sst_buf, cgms->sst.second);
	net_buf_simple_add_u8(sst_buf, cgms->sst.timezone);
	net_buf_simple_add_u8(sst_buf, cgms->sst.dst_offset);

	return bt_gatt_attr_read(conn, attr, buf, len, offset, sst_buf->data, sst_buf->len);
}

static bool leap_year_check(uint16_t year)
{
	if (year % 400 == 0) {
		return true;
	} else if (year % 100 == 0) {
		return false;
	} else if (year % 4 == 0) {
		return true;
	} else {
		return false;
	}
}

static bool valid_datetime_check(struct cgms_session_start_time *sst)
{
	int day_of_month[13] = {0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};
	int day_of_month_leap[13] = {0, 31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};

	if (sst->year < 1582 || sst->year > 9999) {
		LOG_WRN("Invalid SST year");
		return false;
	}
	if (sst->month < 1 || sst->month > 12) {
		LOG_WRN("Invalid SST month");
		return false;
	}
	if (leap_year_check(sst->year)) {
		if (sst->day < 1 || sst->day > day_of_month_leap[sst->month]) {
			LOG_WRN("Invalid SST day");
			return false;
		}
	} else {
		if (sst->day < 1 || sst->day > day_of_month[sst->month]) {
			LOG_WRN("Invalid SST day");
			return false;
		}
	}
	if (sst->hour > 23) {
		LOG_WRN("Invalid SST hour");
		return false;
	}
	if (sst->minute > 59) {
		LOG_WRN("Invalid SST minute");
		return false;
	}
	if (sst->second > 59) {
		LOG_WRN("Invalid SST second");
		return false;
	}
	if (!(sst->timezone == -128 || (sst->timezone >= -48 && sst->timezone <= 56))) {
		LOG_WRN("Invalid SST timezone");
		return false;
	}
	if (!(sst->dst_offset == 0 || sst->dst_offset == 2 || sst->dst_offset == 4
		|| sst->dst_offset == 8 || sst->dst_offset == 255)) {
		LOG_WRN("Invalid SST DST offset");
		return false;
	}
	return true;
}

static int cal_sst_offset(struct bt_cgms *cgms, struct net_buf_simple *sst_buffer)
{
	struct cgms_session_start_time sst;
	uint32_t time_offset;
	uint32_t day_to_change;
	uint32_t second_to_change;
	uint32_t residual_day;
	uint32_t residual_second;
	int day_of_year[13] = {0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334, 365};
	int day_of_leap_year[13] = {0, 31, 60, 91, 121, 152, 182, 213, 244, 274, 305, 335, 366};

	if (!sst_buffer || sst_buffer->len < CGMS_SST_LENGTH) {
		return -EINVAL;
	}

	sst.year = net_buf_simple_pull_le16(sst_buffer);
	sst.month = net_buf_simple_pull_u8(sst_buffer);
	sst.day = net_buf_simple_pull_u8(sst_buffer);
	sst.hour = net_buf_simple_pull_u8(sst_buffer);
	sst.minute = net_buf_simple_pull_u8(sst_buffer);
	sst.second = net_buf_simple_pull_u8(sst_buffer);
	sst.timezone = net_buf_simple_pull_u8(sst_buffer);
	sst.dst_offset = net_buf_simple_pull_u8(sst_buffer);

	if (!valid_datetime_check(&sst)) {
		LOG_WRN("Invalid SST");
		return -EINVAL;
	}

	/* Get elapsed time in second */
	time_offset = (k_uptime_get_32() - cgms->local_start_time) / MSEC_PER_SEC;
	day_to_change = time_offset / SEC_PER_DAY;
	second_to_change = time_offset % SEC_PER_DAY;
	residual_second = sst.hour * 3600 + sst.minute * 60 + sst.second;
	if (residual_second < second_to_change) {
		residual_second += SEC_PER_DAY;
		day_to_change++;
	}
	/* hh:mm:ss */
	sst.hour = (residual_second - second_to_change) / 3600;
	sst.minute = (residual_second - second_to_change - sst.hour * 3600) / 60;
	sst.second = residual_second - second_to_change - sst.hour * 3600 - sst.minute * 60;

	/* yyyy:mm:dd */
	residual_day = leap_year_check(sst.year) ?
		day_of_leap_year[sst.month - 1] : day_of_year[sst.month - 1];
	residual_day += sst.day;

	while ((residual_day - day_to_change) <= 0) {
		residual_day += leap_year_check(sst.year - 1) ? 366 : 365;
		sst.year--;
	}

	sst.month = 0;
	sst.day = 0;

	if (leap_year_check(sst.year)) {
		while ((residual_day - day_to_change) > day_of_leap_year[sst.month]) {
			sst.month++;
		}
		sst.day = (residual_day - day_to_change) - day_of_leap_year[sst.month - 1];
	} else {
		while ((residual_day - day_to_change) > day_of_year[sst.month]) {
			sst.month++;
		}
		sst.day = (residual_day - day_to_change) - day_of_year[sst.month - 1];
	}

	memcpy(&cgms->sst, &sst, sizeof(sst));
	return 0;
}

static ssize_t write_session_start_time(struct bt_conn *conn, const struct bt_gatt_attr *attr,
			const void *buf, uint16_t len, uint16_t offset, uint8_t flags)
{
	struct bt_cgms *cgms = (struct bt_cgms *)attr->user_data;
	int rc;
	struct net_buf_simple write_buf;

	net_buf_simple_init_with_data(&write_buf, (void *)buf, len);

	/* Currently we use our own implementation to calculate the time.
	 * Use API in "time.h" in the future if "time.h" has a full implementation.
	 */
	rc = cal_sst_offset(cgms, &write_buf);
	if (rc < 0) {
		LOG_WRN("Error occurs when calculating sst offset, err = %d", rc);
		return 0;
	}

	return len;
}

static ssize_t read_status(struct bt_conn *conn, const struct bt_gatt_attr *attr,
			void *buf, uint16_t len, uint16_t offset)
{
	struct bt_cgms *cgms = (struct bt_cgms *)attr->user_data;
	struct net_buf_simple *status_buf = NET_BUF_SIMPLE(CGMS_STATUS_LENGTH);
	uint16_t time_offset;

	net_buf_simple_init(status_buf, 0);

	time_offset = (k_uptime_get_32() - cgms->local_start_time) / MSEC_PER_SEC / 60;
	net_buf_simple_add_le16(status_buf, time_offset);

	net_buf_simple_add_u8(status_buf, atomic_get(&cgms->status));
	net_buf_simple_add_u8(status_buf, atomic_get(&cgms->calib_temp));
	net_buf_simple_add_u8(status_buf, atomic_get(&cgms->warning));

	return bt_gatt_attr_read(conn, attr, buf, len, offset, status_buf->data, status_buf->len);
}

static ssize_t read_session_run_time(struct bt_conn *conn, const struct bt_gatt_attr *attr,
			void *buf, uint16_t len, uint16_t offset)
{
	struct bt_cgms *cgms = (struct bt_cgms *)attr->user_data;

	return bt_gatt_attr_read(conn, attr, buf, len, offset,
				&cgms->srt, sizeof(cgms->srt));
}

static ssize_t racp_on_receive(struct bt_conn *conn,
			const struct bt_gatt_attr *attr,
			const void *buf,
			uint16_t len, uint16_t offset, uint8_t flags)
{
	struct bt_cgms *cgms = (struct bt_cgms *)attr->user_data;
	int rc;

	if (offset != 0) {
		return BT_GATT_ERR(BT_ATT_ERR_INVALID_OFFSET);
	}

	if (len > CGMS_RACP_MAX_LENGTH) {
		return BT_GATT_ERR(BT_ATT_ERR_INVALID_ATTRIBUTE_LEN);
	}

	rc = cgms_racp_recv_request(conn, cgms, buf, len);
	if (rc < 0) {
		LOG_WRN("Internal Error during RACP Handling: %d", rc);
	}

	return len;
}

static void cgms_racp_ccc_cfg_changed(const struct bt_gatt_attr *attr, uint16_t value)
{
	ARG_UNUSED(attr);
	LOG_INF("CGMS RACP: indication %s",
			value == BT_GATT_CCC_INDICATE ? "enabled" : "disabled");
}

static ssize_t socp_on_receive(struct bt_conn *conn,
			const struct bt_gatt_attr *attr,
			const void *buf,
			uint16_t len, uint16_t offset, uint8_t flags)
{
	struct bt_cgms *cgms = (struct bt_cgms *)attr->user_data;
	int rc;

	rc = cgms_socp_recv_request(conn, cgms, buf, len);
	if (rc != 0) {
		LOG_WRN("SOCP request not handled properly: err = %d", rc);
		return 0;
	}

	return len;
}

static void cgms_socp_ccc_cfg_changed(const struct bt_gatt_attr *attr, uint16_t value)
{
	ARG_UNUSED(attr);
	LOG_INF("CGMS SOCP: indication %s",
			value == BT_GATT_CCC_INDICATE ? "enabled" : "disabled");
}

/* Project patch: when CONFIG_APP_CGMS_NO_AUTH is set (Kconfig, default n) the
 * CGMS characteristics drop the authenticated-pairing requirement so the board
 * can stream with no bonding at all. This is the "Option A" fallback for the
 * multi-sensor build, where Windows will not complete LE Secure Connections
 * pairing against the peripheral's non-default BLE identities. With the option
 * unset these expand to the stock *_AUTHEN perms — byte-identical to upstream. */
#if defined(CONFIG_APP_CGMS_NO_AUTH)
#define CGMS_PERM_R  BT_GATT_PERM_READ
#define CGMS_PERM_W  BT_GATT_PERM_WRITE
#define CGMS_PERM_RW (BT_GATT_PERM_READ | BT_GATT_PERM_WRITE)
#else
#define CGMS_PERM_R  BT_GATT_PERM_READ_AUTHEN
#define CGMS_PERM_W  BT_GATT_PERM_WRITE_AUTHEN
#define CGMS_PERM_RW (BT_GATT_PERM_READ_AUTHEN | BT_GATT_PERM_WRITE_AUTHEN)
#endif

#define CGMS_ATTRS(_cgms) {						\
	BT_GATT_PRIMARY_SERVICE(BT_UUID_CGMS),				\
	/*CGM Measurement*/						\
	BT_GATT_CHARACTERISTIC(BT_UUID_CGM_MEASUREMENT, BT_GATT_CHRC_NOTIFY,\
				CGMS_PERM_R, NULL, NULL, NULL),		\
	/* CGM Measurement CCCD */					\
	BT_GATT_CCC(cgms_meas_ccc_cfg_changed, CGMS_PERM_RW),		\
	/* CGM Feature */						\
	BT_GATT_CHARACTERISTIC(BT_UUID_CGM_FEATURE, BT_GATT_CHRC_READ,	\
				CGMS_PERM_R,				\
				read_feature, NULL, &_cgms),		\
	/* CGM Status */						\
	BT_GATT_CHARACTERISTIC(BT_UUID_CGM_STATUS, BT_GATT_CHRC_READ,	\
				CGMS_PERM_R,				\
				read_status, NULL, &_cgms),		\
	/* CGM Session Start Time */					\
	BT_GATT_CHARACTERISTIC(BT_UUID_CGM_SESSION_START_TIME,		\
				BT_GATT_CHRC_READ  | BT_GATT_CHRC_WRITE,\
				CGMS_PERM_RW,				\
				read_session_start_time, write_session_start_time, &_cgms),\
	/* CGM Session Run Time */					\
	BT_GATT_CHARACTERISTIC(BT_UUID_CGM_SESSION_RUN_TIME, BT_GATT_CHRC_READ,\
				CGMS_PERM_R,				\
				read_session_run_time, NULL, &_cgms),	\
	/* Record Access Control Point */				\
	BT_GATT_CHARACTERISTIC(BT_UUID_RECORD_ACCESS_CONTROL_POINT,	\
				BT_GATT_CHRC_WRITE | BT_GATT_CHRC_INDICATE,\
				CGMS_PERM_W,				\
				NULL, racp_on_receive, &_cgms),	\
	/* Record Access Control Point CCCD */				\
	BT_GATT_CCC(cgms_racp_ccc_cfg_changed, CGMS_PERM_RW),		\
	/* CGM Specific Ops Control Point */				\
	BT_GATT_CHARACTERISTIC(BT_UUID_CGM_SPECIFIC_OPS_CONTROL_POINT,	\
				BT_GATT_CHRC_WRITE | BT_GATT_CHRC_INDICATE,\
				CGMS_PERM_W,				\
				NULL, socp_on_receive, &_cgms),	\
	/* CGM Specific Ops Control Point CCCD */			\
	BT_GATT_CCC(cgms_socp_ccc_cfg_changed, CGMS_PERM_RW),		\
}

BT_GATT_SERVICE_INSTANCE_DEFINE(cgms_svc_list, cgms_insts, CONFIG_BT_CGMS_INSTANCE_COUNT,
				CGMS_ATTRS);

static void bt_cgms_notify_meas(struct bt_cgms *cgms, struct bt_conn *conn, void *data)
{
	struct cgms_meas *meas = (struct cgms_meas *)data;
	uint8_t meas_size;
	struct net_buf_simple *meas_buf = NET_BUF_SIMPLE(CGMS_MEAS_LENGTH);
	struct bt_gatt_attr *meas_attr = &cgms->service->attrs[CGMS_SVC_MEAS_ATTR_IDX];

	net_buf_simple_init(meas_buf, sizeof(meas_size));

	net_buf_simple_add_u8(meas_buf, meas->flag);
	net_buf_simple_add_le16(meas_buf, meas->glucose_concentration);
	net_buf_simple_add_le16(meas_buf, meas->time_offset);
	if (meas->sensor_status_annunciation.status != 0) {
		net_buf_simple_add_u8(meas_buf, meas->sensor_status_annunciation.status);
	}
	if (meas->sensor_status_annunciation.calib_temp != 0) {
		net_buf_simple_add_u8(meas_buf, meas->sensor_status_annunciation.calib_temp);
	}
	if (meas->sensor_status_annunciation.warning != 0) {
		net_buf_simple_add_u8(meas_buf, meas->sensor_status_annunciation.warning);
	}

	meas_size = meas_buf->len + 1;
	net_buf_simple_push_u8(meas_buf, meas_size);

	/* If conn is NULL, it implies this is a periodic notification.
	 * Send it to all peers.
	 * If conn is set, verify if the notification of this conn is enabled,
	 * and send notification accordingly.
	 */
	if (conn == NULL) {
		bt_gatt_notify(NULL, meas_attr, meas_buf->data, meas_size);
	} else if (bt_gatt_is_subscribed(conn, meas_attr, BT_GATT_CCC_NOTIFY)) {
		bt_gatt_notify(conn, meas_attr, meas_buf->data, meas_size);
	} else {
		LOG_INF("Client disabled the measurement notification");
	}
}

int cgms_racp_send_response(struct bt_conn *peer, struct bt_cgms *cgms, struct net_buf_simple *rsp)
{
	cgms->racp_indicate_params.attr = &cgms->service->attrs[CGMS_SVC_RACP_ATTR_IDX];
	cgms->racp_indicate_params.data = rsp->data;
	cgms->racp_indicate_params.len = rsp->len;

	return bt_gatt_indicate(peer, &cgms->racp_indicate_params);
}

int cgms_racp_send_record(struct bt_conn *peer, struct bt_cgms *cgms, struct cgms_meas *entry)
{
	bt_cgms_notify_meas(cgms, peer, entry);
	return 0;
}

int cgms_socp_send_response(struct bt_conn *peer, struct bt_cgms *cgms, struct net_buf_simple *rsp)
{
	cgms->socp_indicate_params.attr = &cgms->service->attrs[CGMS_SVC_SOCP_ATTR_IDX];
	cgms->socp_indicate_params.data = rsp->data;
	cgms->socp_indicate_params.len = rsp->len;

	return bt_gatt_indicate(peer, &cgms->socp_indicate_params);
}

int bt_cgms_measurement_add(struct bt_cgms *cgms, struct bt_cgms_measurement measurement)
{
	/*Prepare the measurement */
	struct cgms_meas meas;

	if (!cgms) {
		return -EINVAL;
	}

	if (atomic_test_bit(&cgms->status, CGMS_STATUS_POS_SESSION_STOPPED)) {
		LOG_DBG("Session stopped. Cannot add new measurement.");
		return -ENOENT;
	}

	meas.flag = 0;
	meas.glucose_concentration = measurement.glucose.val;
	meas.time_offset =
		(k_uptime_get_32() - cgms->local_start_time) / MSEC_PER_SEC / 60;
	meas.sensor_status_annunciation.warning = 0;
	meas.sensor_status_annunciation.calib_temp = 0;
	meas.sensor_status_annunciation.status = 0;

	/*Add the measurement into DB */
	return cgms_racp_meas_add(cgms, meas);
}

static void report_meas(struct k_work *item)
{
	struct k_work_delayable *dwork = k_work_delayable_from_work(item);
	struct bt_cgms *cgms = CONTAINER_OF(dwork, struct bt_cgms, report_meas_work);
	struct cgms_meas meas;

	/* If session stops, skip rescheduling periodic notification and return. */
	if (atomic_test_bit(&cgms->status, CGMS_STATUS_POS_SESSION_STOPPED)) {
		LOG_DBG("Session stopped. Cannot notify measurement.");
		return;
	}

	/* If comm_interval is zero, we should not notify the client.
	 * But we still reschedule the task with minimum
	 * value of comm_interval to monitor it is enabled
	 * in the future.
	 */
	if (cgms->comm_interval > 0) {
		if (cgms_racp_meas_get_latest(cgms, &meas) == 0) {
			/* Notify all connected peers when it is periodic notification.*/
			bt_cgms_notify_meas(cgms, NULL, &meas);
		}
	}
	/* NOTE: the Bluetooth CGMS spec defines this field in whole minutes;
	 * using K_SECONDS() here instead is a deliberate deviation so this test
	 * rig can report much faster than the spec allows for real sensors.
	 */
	k_work_reschedule(&cgms->report_meas_work, K_SECONDS(
		cgms->comm_interval > 0 ?
			cgms->comm_interval : cgms->comm_interval_minimum));
}

static void stop_session(struct k_timer *timer_id)
{
	struct bt_cgms *cgms = CONTAINER_OF(timer_id, struct bt_cgms, session_expiry_timer);

	atomic_set_bit(&cgms->status, CGMS_STATUS_POS_SESSION_STOPPED);
	if (cgms->cb.session_state_changed) {
		cgms->cb.session_state_changed(cgms, false);
	}
}

int bt_cgms_init(const struct bt_cgms_init_param *init_params, struct bt_cgms **cgms_out)
{
	int rc;
	struct bt_cgms *cgms;

	if (!init_params || !cgms_out) {
		return -EINVAL;
	}

	if (cgms_inst_cnt >= ARRAY_SIZE(cgms_insts)) {
		LOG_WRN("No free CGMS instance available. Increase CONFIG_BT_CGMS_INSTANCE_COUNT.");
		return -ENOMEM;
	}

	cgms = &cgms_insts[cgms_inst_cnt];
	cgms->service = &cgms_svc_list[cgms_inst_cnt];
	cgms_inst_cnt++;

	/* Set the caller's handle now, before anything below can invoke
	 * cb->session_state_changed(cgms, ...): the callback identifies its
	 * instance by comparing against this handle, so the app must be able
	 * to resolve it back to its own bookkeeping before the callback ever
	 * fires (it fires synchronously later in this function).
	 */
	*cgms_out = cgms;

	cgms->feature.type = init_params->type;
	cgms->feature.sample_location = init_params->sample_location;
	cgms->feature.feature = 0;

	memset(&cgms->status, 0, sizeof(cgms->status));

	/* Check if session run time is positive */
	if (init_params->session_run_time == 0) {
		LOG_WRN("Invalid session run time.");
		return -EINVAL;
	}
	cgms->srt = init_params->session_run_time;

	cgms->comm_interval_minimum = CGMS_COMM_INTERVAL_MIN;

	/* Check if the initial communication interval is valid.
	 * Set it to the smallest value allowed if necessary.
	 */
	if (init_params->initial_comm_interval < cgms->comm_interval_minimum) {
		cgms->comm_interval = cgms->comm_interval_minimum;
		LOG_INF("Communication interval too small. Changed to minimum value allowed.");
	} else {
		cgms->comm_interval = init_params->initial_comm_interval;
	}

	if (!init_params->cb) {
		LOG_WRN("Callback is NULL.");
		return -EINVAL;
	}
	cgms->cb.session_state_changed = init_params->cb->session_state_changed;

	cgms_racp_init(cgms);

	rc = bt_gatt_service_register(cgms->service);
	if (rc) {
		LOG_WRN("Cannot register CGMS instance (err %d).", rc);
		return rc;
	}

	k_work_init_delayable(&cgms->report_meas_work, report_meas);
	rc = k_work_reschedule(&cgms->report_meas_work, K_SECONDS(cgms->comm_interval));
	if (rc < 0) {
		LOG_WRN("Cannot schedule notification task.");
		return rc;
	}

	k_timer_init(&cgms->session_expiry_timer, stop_session, NULL);
	k_timer_start(&cgms->session_expiry_timer, K_HOURS(cgms->srt), K_NO_WAIT);

	/* Start a session and notify the application. */
	cgms->local_start_time = k_uptime_get_32();
	atomic_clear_bit(&cgms->status, CGMS_STATUS_POS_SESSION_STOPPED);
	if (cgms->cb.session_state_changed) {
		cgms->cb.session_state_changed(cgms, true);
	}

	return 0;
}
