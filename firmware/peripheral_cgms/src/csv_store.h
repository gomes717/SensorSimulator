#ifndef CSV_STORE_H
#define CSV_STORE_H

/*
 * Uploaded-CSV storage + playback lookup, backed by the external SPI-NOR
 * sim_csv_partition (see boards/nrf54l15dk_nrf54l15_cpuapp.overlay). The app
 * uploads two "tracks" over BLE (see PROTOCOL_SPEC.md's "CSV playback data
 * source" section):
 *
 *   - CSV_TRACK_GLUCOSE : int16 mg/dL samples, evenly spaced interval_s apart,
 *                         starting at base_epoch_s. When sim_config.data_source
 *                         is SIM_DATA_CSV, model_thread emits one of these per
 *                         tick as the CGM Measurement (verbatim, no sensor
 *                         noise), looping when the window ends.
 *   - CSV_TRACK_FOODLOG : {u32 offset_s; float carbs_g} meal entries (seconds
 *                         from base_epoch_s). Report-only — surfaced in the
 *                         Food/Exercise Status notification, never fed to a
 *                         model.
 *
 * Upload is chunked: csv_store_begin() (erase + header), repeated
 * csv_store_write() (offset + bytes), csv_store_commit() (CRC check + manifest
 * write). The manifest lives in the last used sector of the partition so
 * playback is autonomous across reboots (csv_store_load_manifest() at boot).
 */

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define CSV_TRACK_GLUCOSE 0
#define CSV_TRACK_FOODLOG 1
#define CSV_TRACK_COUNT   2

/* Parsed CSV control BEGIN payload (see PROTOCOL_SPEC.md). */
struct csv_upload_header {
	uint8_t track;
	uint32_t row_count;
	uint32_t base_epoch_s;
	uint16_t interval_s; /* glucose sample spacing; unused for foodlog */
	uint32_t total_bytes;
	uint32_t crc32;
};

/* One report-only meal, returned by csv_foodlog_window(). */
struct csv_food_hit {
	float carbs_g;
};

/* Begin an upload: validates the header, erases just enough sectors of the
 * track's flash region for total_bytes, and arms the write cursor. Returns 0
 * on success, negative errno otherwise. */
int csv_store_begin(const struct csv_upload_header *hdr);

/* Program len bytes at offset within the in-progress track's flash region.
 * offset is relative to the track, not the partition. Returns 0 on success. */
int csv_store_write(uint32_t offset, const uint8_t *data, uint16_t len);

/* Finish the in-progress upload for *track*: checks received byte count and
 * CRC32 against the header, and on success writes the manifest and refreshes
 * the in-RAM playback state. *crc_ok (may be NULL) reports the CRC result.
 * Returns 0 on success, negative errno on mismatch / flash error. */
int csv_store_commit(uint8_t track, bool *crc_ok);

/* Discard the in-progress upload without touching the committed manifest. */
void csv_store_abort(void);

/* Wipe *track*'s manifest entry (playback reverts to whatever remains).
 * Returns 0 on success. */
int csv_store_clear(uint8_t track);

/* Bytes received so far for the in-progress upload (for the STATUS opcode). */
uint32_t csv_store_received(void);

/* Load the manifest from flash and populate the in-RAM playback state. Call
 * once at boot, after sim_config_load_from_flash(). */
void csv_store_load_manifest(void);

/* True if a committed glucose track is available for playback. */
bool csv_glucose_available(void);

/* Look up the glucose sample for simulated-minutes-since-start sim_clock_min,
 * looping at the end of the window. Returns false (and leaves *out_mg_dl
 * untouched) if no glucose track is committed. */
bool csv_glucose_lookup(double sim_clock_min, float *out_mg_dl);

/* Fill up to max entries for meals whose offset falls in the simulated-seconds
 * half-open window [t0_s, t1_s), taken modulo the playback span so the food
 * log loops in lockstep with the glucose track. Returns the number written;
 * 0 if no foodlog track is committed. */
int csv_foodlog_window(double t0_s, double t1_s, struct csv_food_hit *out, int max);

#endif /* CSV_STORE_H */
