#ifndef DEXCOM_G6_H
#define DEXCOM_G6_H

/*
 * The Dexcom G6 transmitter protocol as xDrip speaks it: CRC, the AES challenge, request ->
 * reply for every command the simulator answers, and the 5-minute reading grid.
 *
 * Plain C with no Zephyr dependency, so it is built unchanged on the PC for the host tests
 * (firmware/peripheral_cgms/tests/host). src/api/dexcom_g6.py and
 * src/api/dexcom_g6_transmitter.py are the Python twins; both are held to the same golden vectors
 * (tests/data/dexcom_g6_vectors.json). dexcom_service.c is the GATT glue around this module.
 *
 * Little-endian on the wire. Everything except the authentication messages ends in a
 * CRC-16/XMODEM of the preceding bytes. See .scratch/dexcom-g6-transmitter/spec.md.
 */

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define G6_ID_LEN        6
#define G6_ID_BUF        (G6_ID_LEN + 1)
#define G6_NAME_BUF      9 /* "Dexcom" + 2 characters + NUL */
#define G6_TOKEN_LEN     8
#define G6_MAX_REPLY     24
#define G6_PERIOD_S      300u /* one reading per 5 minutes */
#define G6_GLUCOSE_MIN   40
#define G6_GLUCOSE_MAX   400
#define G6_TREND_INVALID 127

#define G6_STATE_STOPPED    0x01
#define G6_STATE_WARMING_UP 0x02
#define G6_STATE_OK         0x06

/* One reading as the transmitter reports it. */
struct g6_reading {
	uint32_t seq;
	uint32_t ts_s;
	uint16_t glucose; /* mg/dL */
	uint8_t state;
	int8_t trend; /* tenths of mg/dL/min; G6_TREND_INVALID = unknown */
	uint8_t status;
};

/* Per-connection authentication state. */
struct g6_session {
	bool authenticated;
	bool has_challenge;
	uint8_t challenge[G6_TOKEN_LEN];
};

enum g6_result {
	G6_RES_REPLY,    /* indicate the reply on Control */
	G6_RES_NOT_AUTH, /* fail the write with ATT error 8 */
	G6_RES_BAD_CRC,  /* drop it */
	G6_RES_UNKNOWN,  /* log it, drop it */
	G6_RES_DISCONNECT,
	G6_RES_ACK, /* accepted, nothing to send */
};

/* ---- primitives -------------------------------------------------------------------------- */

uint16_t g6_crc16(const uint8_t *data, size_t len);

/* Six characters, digits and capital letters. */
bool g6_valid_id(const char *id, size_t len);

/* The BLE name xDrip scans for: "Dexcom" + the last two characters of the ID. */
void g6_adv_name(const char *id, char out[G6_NAME_BUF]);

/* AES-128-ECB(key("00"+id+"00"+id), in || in)[0..7]. `id` is G6_ID_LEN characters. */
void g6_challenge_hash(const char *id, const uint8_t in[G6_TOKEN_LEN], uint8_t out[G6_TOKEN_LEN]);

/* Round half away from zero (what the Python twin does). */
int g6_round_half_away(double v);

/* ---- the two characteristics ------------------------------------------------------------- */

/* A write to the Authentication characteristic (...3535). `random8` is the challenge to issue if
 * one is asked for. Returns true if the message was understood (it may still need no reply:
 * *out_len == 0), false if it is malformed or unknown. */
bool g6_auth_handle(struct g6_session *s, const char *id, const uint8_t *in, size_t len,
		    const uint8_t random8[G6_TOKEN_LEN], uint8_t out[G6_MAX_REPLY], size_t *out_len);

/* A write to the Control characteristic (...3534). `latest` is the held reading (NULL = none yet),
 * `now_s` the transmitter clock. On G6_RES_REPLY the reply is in out[0..*out_len). */
enum g6_result g6_control_handle(const struct g6_session *s, const struct g6_reading *latest,
				 uint32_t now_s, const uint8_t *in, size_t len,
				 uint8_t out[G6_MAX_REPLY], size_t *out_len);

/* ---- the 5-minute grid ------------------------------------------------------------------- */

struct g6_grid {
	uint32_t period_s;
	bool has_k;
	uint32_t k; /* grid index of the latest publication */
	uint32_t seq;
};

void g6_grid_init(struct g6_grid *g, uint32_t period_s);
void g6_grid_reset(struct g6_grid *g);

/* Offer the model's value at simulated time `sim_s`. Returns true and fills *out when a reading is
 * due: the first call, then each time a period boundary has been crossed. A clock that goes
 * backwards restarts the grid. `has_trend` false means "unknown". */
bool g6_grid_feed(struct g6_grid *g, double sim_s, double glucose_mgdl, bool has_trend,
		  double trend_mgdl_min, uint8_t state, struct g6_reading *out);

#endif /* DEXCOM_G6_H */
