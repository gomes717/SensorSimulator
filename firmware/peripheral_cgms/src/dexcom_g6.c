#include <math.h>
#include <string.h>

#include "aes128.h"
#include "dexcom_g6.h"

/* Opcodes (see src/api/dexcom_g6.py for the full table). */
#define OP_AUTH_REQUEST         0x01
#define OP_AUTH_CHALLENGE       0x03
#define OP_AUTH_CHALLENGE_REPLY 0x04
#define OP_AUTH_STATUS          0x05
#define OP_KEEP_ALIVE           0x06
#define OP_BOND_REQUEST         0x07
#define OP_DISCONNECT           0x09
#define OP_UNBOND               0x0A
#define OP_VERSION              0x20
#define OP_VERSION_REPLY        0x21
#define OP_BATTERY              0x22
#define OP_BATTERY_REPLY        0x23
#define OP_TIME                 0x24
#define OP_TIME_REPLY           0x25
#define OP_SESSION_START        0x26
#define OP_SESSION_START_REPLY  0x27
#define OP_SESSION_STOP         0x28
#define OP_SESSION_STOP_REPLY   0x29
#define OP_SENSOR               0x2E
#define OP_SENSOR_REPLY         0x2F
#define OP_GLUCOSE              0x30
#define OP_GLUCOSE_REPLY        0x31
#define OP_CALIBRATE            0x34
#define OP_CALIBRATE_REPLY      0x35
#define OP_EGLUCOSE             0x4E
#define OP_EGLUCOSE_REPLY       0x4F
#define OP_VERSION1             0x4A
#define OP_VERSION1_REPLY       0x4B
#define OP_VERSION2             0x52
#define OP_VERSION2_REPLY       0x53

#define BONDED 1 /* we always claim to be bonded already: no SMP, no pairing */

/* Fixed answers for what the simulator does not model. */
static const uint8_t FIRMWARE[4] = {1, 6, 5, 25};
static const uint8_t BT_FIRMWARE[4] = {1, 0, 0, 0};
#define BATTERY_A          3000
#define BATTERY_B          2900
#define TEMPERATURE_C      25
#define MAX_RUNTIME_DAYS   112
#define TYPICAL_SENSOR_DAY 10
#define WARMUP_S           7200

static void put16(uint8_t *p, uint32_t v)
{
	p[0] = (uint8_t)v;
	p[1] = (uint8_t)(v >> 8);
}

static void put32(uint8_t *p, uint32_t v)
{
	put16(p, v);
	put16(p + 2, v >> 16);
}

static uint32_t get32(const uint8_t *p)
{
	return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) |
	       ((uint32_t)p[3] << 24);
}

uint16_t g6_crc16(const uint8_t *data, size_t len)
{
	uint16_t crc = 0;

	for (size_t i = 0; i < len; i++) {
		crc ^= (uint16_t)((uint16_t)data[i] << 8);
		for (int b = 0; b < 8; b++) {
			crc = (crc & 0x8000) ? (uint16_t)((crc << 1) ^ 0x1021) : (uint16_t)(crc << 1);
		}
	}
	return crc;
}

/* Appends the CRC of out[0..len) and returns the new length. */
static size_t seal(uint8_t *out, size_t len)
{
	put16(out + len, g6_crc16(out, len));
	return len + 2;
}

static bool crc_ok(const uint8_t *msg, size_t len)
{
	return len >= 3 && g6_crc16(msg, len - 2) == (uint16_t)(msg[len - 2] | (msg[len - 1] << 8));
}

bool g6_valid_id(const char *id, size_t len)
{
	if (len != G6_ID_LEN) {
		return false;
	}
	for (size_t i = 0; i < len; i++) {
		bool digit = id[i] >= '0' && id[i] <= '9';
		bool upper = id[i] >= 'A' && id[i] <= 'Z';

		if (!digit && !upper) {
			return false;
		}
	}
	return true;
}

void g6_adv_name(const char *id, char out[G6_NAME_BUF])
{
	memcpy(out, "Dexcom", 6);
	out[6] = id[4];
	out[7] = id[5];
	out[8] = '\0';
}

void g6_challenge_hash(const char *id, const uint8_t in[G6_TOKEN_LEN], uint8_t out[G6_TOKEN_LEN])
{
	uint8_t key[16];
	uint8_t plain[16];
	uint8_t enc[16];

	key[0] = '0';
	key[1] = '0';
	memcpy(key + 2, id, G6_ID_LEN);
	key[8] = '0';
	key[9] = '0';
	memcpy(key + 10, id, G6_ID_LEN);
	memcpy(plain, in, G6_TOKEN_LEN);
	memcpy(plain + G6_TOKEN_LEN, in, G6_TOKEN_LEN);
	aes128_encrypt_block(key, plain, enc);
	memcpy(out, enc, G6_TOKEN_LEN);
}

int g6_round_half_away(double v)
{
	return (int)(v < 0 ? -floor(-v + 0.5) : floor(v + 0.5));
}

/* ---- authentication --------------------------------------------------------------------- */

bool g6_auth_handle(struct g6_session *s, const char *id, const uint8_t *in, size_t len,
		    const uint8_t random8[G6_TOKEN_LEN], uint8_t out[G6_MAX_REPLY], size_t *out_len)
{
	*out_len = 0;
	if (len == 0) {
		return false;
	}
	if (in[0] == OP_AUTH_REQUEST && len == 10) {
		s->authenticated = false;
		s->has_challenge = true;
		memcpy(s->challenge, random8, G6_TOKEN_LEN);
		out[0] = OP_AUTH_CHALLENGE;
		g6_challenge_hash(id, in + 1, out + 1);
		memcpy(out + 1 + G6_TOKEN_LEN, random8, G6_TOKEN_LEN);
		*out_len = 1 + 2 * G6_TOKEN_LEN;
		return true;
	}
	if (in[0] == OP_AUTH_CHALLENGE_REPLY && len == 9) {
		bool ok = false;

		if (s->has_challenge) {
			uint8_t want[G6_TOKEN_LEN];

			g6_challenge_hash(id, s->challenge, want);
			ok = memcmp(want, in + 1, G6_TOKEN_LEN) == 0;
		}
		s->authenticated = ok;
		s->has_challenge = false;
		out[0] = OP_AUTH_STATUS;
		out[1] = ok ? 1 : 0;
		out[2] = BONDED;
		*out_len = 3;
		return true;
	}
	if (in[0] == OP_KEEP_ALIVE || in[0] == OP_BOND_REQUEST) {
		return true; /* we claim to be bonded already; nothing to send */
	}
	return false;
}

/* ---- control ----------------------------------------------------------------------------- */

static size_t glucose_reply(const struct g6_reading *r, bool extended, uint8_t *out)
{
	out[0] = extended ? OP_EGLUCOSE_REPLY : OP_GLUCOSE_REPLY;
	out[1] = r->status;
	put32(out + 2, r->seq);
	put32(out + 6, r->ts_s);
	put16(out + 10, r->glucose & 0x0FFF);
	out[12] = r->state;
	out[13] = (uint8_t)r->trend;
	if (!extended) {
		return seal(out, 14);
	}
	put16(out + 14, r->glucose & 0x03FF); /* predicted glucose = the reading itself */
	return seal(out, 16);
}

static size_t sensor_reply(const struct g6_reading *r, uint8_t *out)
{
	uint32_t raw = (uint32_t)r->glucose * 1000u;

	out[0] = OP_SENSOR_REPLY;
	out[1] = r->status;
	put32(out + 2, r->ts_s);
	put32(out + 6, raw);
	put32(out + 10, raw);
	return seal(out, 14);
}

static size_t time_reply(uint32_t now_s, uint8_t *out)
{
	out[0] = OP_TIME_REPLY;
	out[1] = 0;
	put32(out + 2, now_s);
	put32(out + 6, 0); /* session start */
	return seal(out, 10);
}

static size_t version_reply(uint8_t *out)
{
	out[0] = OP_VERSION_REPLY;
	out[1] = 0;
	memcpy(out + 2, FIRMWARE, 4);
	memcpy(out + 6, BT_FIRMWARE, 4);
	out[10] = 1; /* hardware revision */
	memset(out + 11, 0, 3);
	put16(out + 14, 0); /* asic */
	return seal(out, 16);
}

static size_t version1_reply(uint8_t *out)
{
	out[0] = OP_VERSION1_REPLY;
	out[1] = 0;
	memcpy(out + 2, FIRMWARE, 4);
	put32(out + 6, 0);                /* build */
	put16(out + 10, 0);               /* inactive days */
	out[12] = 0;                      /* version code */
	put16(out + 13, MAX_RUNTIME_DAYS); /* max runtime days */
	put16(out + 15, 0);               /* max inactive days */
	return seal(out, 17);
}

static size_t version2_reply(uint8_t *out)
{
	out[0] = OP_VERSION2_REPLY;
	out[1] = 0;
	out[2] = TYPICAL_SENSOR_DAY;
	put16(out + 3, 0);        /* feature bits */
	put16(out + 5, WARMUP_S); /* warm-up seconds */
	return seal(out, 7);
}

static size_t battery_reply(uint8_t *out)
{
	out[0] = OP_BATTERY_REPLY;
	out[1] = 0;
	put16(out + 2, BATTERY_A);
	put16(out + 4, BATTERY_B);
	out[6] = 0; /* runtime */
	out[7] = (uint8_t)TEMPERATURE_C;
	return seal(out, 8);
}

static size_t session_start_reply(const uint8_t *in, size_t len, uint32_t now_s, uint8_t *out)
{
	out[0] = OP_SESSION_START_REPLY;
	out[1] = 0;
	out[2] = 0x01;                              /* info: OK */
	put32(out + 3, len >= 9 ? get32(in + 5) : 0); /* requested start, echoed */
	put32(out + 7, 0);                          /* session start */
	put32(out + 11, now_s);                     /* transmitter time */
	return seal(out, 15);
}

static size_t session_stop_reply(uint32_t now_s, uint8_t *out)
{
	out[0] = OP_SESSION_STOP_REPLY;
	out[1] = 0;
	out[2] = 0x01;
	put32(out + 3, now_s);  /* stop */
	put32(out + 7, 0);      /* start */
	put32(out + 11, now_s); /* transmitter time */
	return seal(out, 15);
}

static size_t calibrate_reply(uint8_t *out)
{
	out[0] = OP_CALIBRATE_REPLY;
	out[1] = 0;
	out[2] = 0x00; /* accepted */
	return seal(out, 3);
}

enum g6_result g6_control_handle(const struct g6_session *s, const struct g6_reading *latest,
				 uint32_t now_s, const uint8_t *in, size_t len,
				 uint8_t out[G6_MAX_REPLY], size_t *out_len)
{
	static const struct g6_reading none = {
		.state = G6_STATE_STOPPED, .trend = G6_TREND_INVALID};
	const struct g6_reading *r = latest ? latest : &none;

	*out_len = 0;
	if (len == 0) {
		return G6_RES_UNKNOWN;
	}
	if (!s->authenticated) {
		return G6_RES_NOT_AUTH;
	}
	if (in[0] == OP_DISCONNECT) {
		return G6_RES_DISCONNECT;
	}
	if (in[0] == OP_UNBOND) {
		return G6_RES_ACK;
	}
	if (len >= 3 && !crc_ok(in, len)) {
		return G6_RES_BAD_CRC;
	}

	switch (in[0]) {
	case OP_EGLUCOSE:
		*out_len = glucose_reply(r, true, out);
		break;
	case OP_GLUCOSE:
		*out_len = glucose_reply(r, false, out);
		break;
	case OP_SENSOR:
		*out_len = sensor_reply(r, out);
		break;
	case OP_TIME:
		*out_len = time_reply(now_s, out);
		break;
	case OP_VERSION:
		*out_len = version_reply(out);
		break;
	case OP_VERSION1:
		*out_len = version1_reply(out);
		break;
	case OP_VERSION2:
		*out_len = version2_reply(out);
		break;
	case OP_BATTERY:
		*out_len = battery_reply(out);
		break;
	case OP_SESSION_START:
		*out_len = session_start_reply(in, len, now_s, out);
		break;
	case OP_SESSION_STOP:
		*out_len = session_stop_reply(now_s, out);
		break;
	case OP_CALIBRATE:
		*out_len = calibrate_reply(out);
		break;
	default:
		return G6_RES_UNKNOWN;
	}
	return G6_RES_REPLY;
}

/* ---- the 5-minute grid ------------------------------------------------------------------- */

void g6_grid_init(struct g6_grid *g, uint32_t period_s)
{
	g->period_s = period_s ? period_s : G6_PERIOD_S;
	g6_grid_reset(g);
}

void g6_grid_reset(struct g6_grid *g)
{
	g->has_k = false;
	g->k = 0;
	g->seq = 0;
}

static int clamp_int(int v, int lo, int hi)
{
	return v < lo ? lo : (v > hi ? hi : v);
}

bool g6_grid_feed(struct g6_grid *g, double sim_s, double glucose_mgdl, bool has_trend,
		  double trend_mgdl_min, uint8_t state, struct g6_reading *out)
{
	uint32_t k = sim_s > 0 ? (uint32_t)(sim_s / (double)g->period_s) : 0;

	if (g->has_k && k < g->k) {
		g6_grid_reset(g);
	}
	if (g->has_k && k == g->k) {
		return false;
	}
	g->has_k = true;
	g->k = k;

	out->seq = g->seq++;
	out->ts_s = k * g->period_s;
	out->glucose = (uint16_t)clamp_int(g6_round_half_away(glucose_mgdl), G6_GLUCOSE_MIN,
					   G6_GLUCOSE_MAX);
	out->state = state;
	out->status = 0;
	out->trend = has_trend ? (int8_t)clamp_int(g6_round_half_away(trend_mgdl_min * 10.0), -126, 126)
			       : (int8_t)G6_TREND_INVALID;
	return true;
}
