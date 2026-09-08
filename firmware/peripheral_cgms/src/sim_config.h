#ifndef SIM_CONFIG_H
#define SIM_CONFIG_H

/*
 * Persisted simulator configuration: which physiological model + params,
 * which sensor noise model + params, mode (normal/fast), and the recurring
 * daily food/exercise schedule. Wire format and field order are specified in
 * SensorSimulator/PROTOCOL_SPEC.md — keep this struct in sync with that doc
 * and with SensorSimulator/src/protocol.py's encode_ and decode_ functions.
 */

#include <stdint.h>
#include <zephyr/toolchain.h>

#define SIM_CONFIG_MAGIC   0x53494D31u /* "SIM1" */
#define SIM_CONFIG_VERSION 4           /* v2: data_source; v3: speed_mult; v4: comm_profile */

#define MAX_MODEL_PARAMS  34 /* >= UVA/Padova's 33 params */
#define MAX_SENSOR_PARAMS 14 /* >= Facchinetti's 13 params */
#define MAX_EVENTS        32

/* Legacy on/off mode byte — superseded by speed_mult (continuous x1..x1000).
 * Kept in struct sim_config for wire compat; model_thread ignores it. */
#define SIM_MODE_NORMAL 0
#define SIM_MODE_FAST   1

/* Simulation speed multiplier bounds. dt_min per 1 Hz tick = (1/60) * speed_mult,
 * so x1 = real time, x60 = the old "fast mode", x1000 = 1 real second per
 * ~16.7 simulated minutes. See PROTOCOL_SPEC.md's "Speed" section. */
#define SIM_SPEED_MIN     1.0f
#define SIM_SPEED_MAX     1000.0f
#define SIM_SPEED_DEFAULT 1.0f

/* Which BLE profile the board streams glucose over — see PROTOCOL_SPEC.md's
 * "Comm profile" section. SIG_CGMS is the Bluetooth SIG standard CGM Service
 * (0x181F). DEXCOM is a basic imitation of a Dexcom transmitter (FEBC service,
 * opcode-tagged glucose messages, no auth handshake). */
#define SIM_COMM_SIG_CGMS 0
#define SIM_COMM_DEXCOM   1

/* Where the streamed glucose comes from — see PROTOCOL_SPEC.md's "CSV playback
 * data source" section and src/csv_store.h. SIM_DATA_CSV makes model_thread
 * emit one row of an uploaded recorded trace per tick instead of stepping a
 * physiological model + sensor noise. */
#define SIM_DATA_MODEL 0
#define SIM_DATA_CSV   1

enum sim_model_id {
	SIM_MODEL_CAMBRIDGE  = 0,
	SIM_MODEL_UVA_PADOVA = 1,
	SIM_MODEL_ROYPARKER  = 2,
	SIM_MODEL_DEICHMANN  = 3,
};

enum sim_sensor_id {
	SIM_SENSOR_IDEAL       = 0,
	SIM_SENSOR_BRETON      = 1,
	SIM_SENSOR_FACCHINETTI = 2,
};

/* Recurring-daily meal: [time_min, time_min + duration_min) each simulated
 * day. time_min == 0xFFFF is the "clear all" write sentinel — never stored. */
struct food_event {
	uint16_t time_min;
	uint16_t duration_min;
	float carbs_g;
} __packed;

/* Recurring-daily exercise bout, same window semantics as food_event. */
struct exercise_event {
	uint16_t time_min;
	uint16_t duration_min;
	float intensity_pct;
} __packed;

struct sim_config {
	uint32_t magic;
	uint16_t version;
	uint8_t mode;
	uint8_t model_id;
	float model_params[MAX_MODEL_PARAMS];
	uint8_t sensor_id;
	float sensor_params[MAX_SENSOR_PARAMS];
	uint8_t food_count;
	struct food_event food[MAX_EVENTS];
	uint8_t exercise_count;
	struct exercise_event exercise[MAX_EVENTS];
	uint8_t data_source; /* SIM_DATA_MODEL / SIM_DATA_CSV */
	float speed_mult;    /* v3: SIM_SPEED_MIN..SIM_SPEED_MAX, default SIM_SPEED_DEFAULT */
	uint8_t comm_profile; /* v4: SIM_COMM_SIG_CGMS / SIM_COMM_DEXCOM */
} __packed;

/* BLE wire-format structs — byte-identical to what SensorSimulator's
 * src/protocol.py packs/unpacks (little-endian target, __packed here). */
struct person_config_wire {
	uint8_t model_id;
	float params[MAX_MODEL_PARAMS];
} __packed;

struct sensor_config_wire {
	uint8_t sensor_id;
	float params[MAX_SENSOR_PARAMS];
} __packed;

struct food_list_wire {
	uint8_t count;
	struct food_event events[MAX_EVENTS];
} __packed;

struct exercise_list_wire {
	uint8_t count;
	struct exercise_event events[MAX_EVENTS];
} __packed;

/* Data-source characteristic wire format (1 byte, read + write). */
struct data_source_wire {
	uint8_t data_source; /* SIM_DATA_MODEL / SIM_DATA_CSV */
} __packed;

/* Speed characteristic wire format (float32 LE, read + write). */
struct speed_wire {
	float mult;
} __packed;

/* Comm-profile characteristic wire format (1 byte, read + write). */
struct comm_profile_wire {
	uint8_t profile; /* SIM_COMM_SIG_CGMS / SIM_COMM_DEXCOM */
} __packed;

/* Fills *cfg with Cambridge + Ideal CGM defaults, no events, normal mode. */
void sim_config_set_defaults(struct sim_config *cfg);

/* Loads from the external-flash storage partition into *cfg. Returns 0 if a
 * valid (magic/version match) config was found; otherwise fills *cfg with
 * defaults and returns a negative errno — callers should treat *cfg as valid
 * to use either way. */
int sim_config_load_from_flash(struct sim_config *cfg);

/* Erases and reprograms the storage partition with *cfg. Returns 0 on success. */
int sim_config_save_to_flash(const struct sim_config *cfg);

#endif /* SIM_CONFIG_H */
