#include <math.h>
#include <string.h>
#include <errno.h>
#include <zephyr/kernel.h>
#include <zephyr/storage/flash_map.h>
#include <zephyr/sys/crc.h>
#include <zephyr/sys/printk.h>

#include "csv_store.h"

/*
 * sim_csv_partition layout (252 KB total, see the board overlay). Region and
 * manifest offsets are 4 KB-aligned so flash_area_erase() can act on them.
 *
 *   0x00000  glucose region  (240 KB -> up to 122880 int16 samples)
 *   0x3C000  foodlog region  (4 KB   -> up to 512 entries)
 *   0x3D000  manifest        (4 KB, one sector, rewritten whole)
 *   0x3E000  spare
 */
#define CSV_PARTITION_ID FIXED_PARTITION_ID(sim_csv_partition)

#define CSV_SECTOR_SIZE 4096u

#define CSV_GLUCOSE_REGION_OFF  0x00000000u
#define CSV_GLUCOSE_REGION_SIZE 0x0003C000u
#define CSV_FOODLOG_REGION_OFF  0x0003C000u
#define CSV_FOODLOG_REGION_SIZE 0x00001000u
#define CSV_MANIFEST_OFF        0x0003D000u
#define CSV_MANIFEST_SIZE       0x00001000u

#define CSV_MANIFEST_MAGIC   0x31565343u /* "CSV1" */
#define CSV_MANIFEST_VERSION 1

/* Report-only meals kept in RAM for per-tick lookup. 512 entries fit the
 * flash region but a day rarely has more than a handful of logged meals. */
#define CSV_FOODLOG_MAX 256

#define CSV_GLUCOSE_ROW_BYTES 2 /* int16 mg/dL */
#define CSV_FOODLOG_ROW_BYTES 8 /* u32 offset_s + f32 carbs_g */

struct csv_foodlog_row {
	uint32_t offset_s;
	float carbs_g;
} __packed;

struct csv_track_manifest {
	uint8_t present;
	uint8_t _pad;
	uint16_t interval_s;
	uint32_t row_count;
	uint32_t base_epoch_s;
	uint32_t byte_len;
	uint32_t crc32;
} __packed;

struct csv_manifest {
	uint32_t magic;
	uint16_t version;
	uint16_t _pad;
	struct csv_track_manifest track[CSV_TRACK_COUNT];
} __packed;

BUILD_ASSERT(sizeof(struct csv_manifest) <= CSV_MANIFEST_SIZE, "csv_manifest too large");

/* ── In-RAM playback state (rebuilt from the manifest on commit / boot) ── */

static struct {
	bool present;
	uint16_t interval_s;
	uint32_t row_count;
	uint32_t base_epoch_s;
} glu;

static struct csv_foodlog_row foodlog_cache[CSV_FOODLOG_MAX];
static uint32_t foodlog_count;
static bool foodlog_present;

/* Playback span (seconds) — the glucose window length, or 24 h if only a
 * foodlog is present. The food log loops on this so it stays in step with the
 * looping glucose track. */
static double playback_span_s = 86400.0;

/* ── In-progress upload staging ── */

static struct {
	bool active;
	uint8_t track;
	uint32_t byte_len;
	uint32_t crc32;
	uint32_t received;
	uint16_t interval_s;
	uint32_t row_count;
	uint32_t base_epoch_s;
} staging;

static void track_region(uint8_t track, uint32_t *off, uint32_t *size)
{
	if (track == CSV_TRACK_FOODLOG) {
		*off = CSV_FOODLOG_REGION_OFF;
		*size = CSV_FOODLOG_REGION_SIZE;
	} else {
		*off = CSV_GLUCOSE_REGION_OFF;
		*size = CSV_GLUCOSE_REGION_SIZE;
	}
}

static void recompute_span(void)
{
	if (glu.present && glu.interval_s > 0) {
		playback_span_s = (double)glu.row_count * (double)glu.interval_s;
	} else {
		playback_span_s = 86400.0;
	}
	if (playback_span_s <= 0.0) {
		playback_span_s = 86400.0;
	}
}

static int manifest_read(struct csv_manifest *out)
{
	const struct flash_area *fa;
	int err = flash_area_open(CSV_PARTITION_ID, &fa);

	if (err) {
		return err;
	}
	err = flash_area_read(fa, CSV_MANIFEST_OFF, out, sizeof(*out));
	flash_area_close(fa);
	return err;
}

static int manifest_write(const struct csv_manifest *m)
{
	const struct flash_area *fa;
	int err = flash_area_open(CSV_PARTITION_ID, &fa);

	if (err) {
		return err;
	}
	err = flash_area_erase(fa, CSV_MANIFEST_OFF, CSV_MANIFEST_SIZE);
	if (!err) {
		err = flash_area_write(fa, CSV_MANIFEST_OFF, m, sizeof(*m));
	}
	flash_area_close(fa);
	return err;
}

static void load_manifest_into_ram(const struct csv_manifest *m)
{
	const struct csv_track_manifest *g = &m->track[CSV_TRACK_GLUCOSE];
	const struct csv_track_manifest *f = &m->track[CSV_TRACK_FOODLOG];

	glu.present = g->present && g->row_count > 0;
	glu.interval_s = g->interval_s;
	glu.row_count = g->row_count;
	glu.base_epoch_s = g->base_epoch_s;

	foodlog_present = f->present && f->row_count > 0;
	foodlog_count = 0;
	if (foodlog_present) {
		const struct flash_area *fa;
		uint32_t n = MIN(f->row_count, (uint32_t)CSV_FOODLOG_MAX);

		if (flash_area_open(CSV_PARTITION_ID, &fa) == 0) {
			if (flash_area_read(fa, CSV_FOODLOG_REGION_OFF, foodlog_cache,
					    n * CSV_FOODLOG_ROW_BYTES) == 0) {
				foodlog_count = n;
			}
			flash_area_close(fa);
		}
	}

	recompute_span();
	printk("csv_store: manifest loaded (glucose present=%d rows=%u interval=%us, "
	       "foodlog present=%d rows=%u)\n",
	       glu.present, glu.row_count, glu.interval_s, foodlog_present, foodlog_count);
}

void csv_store_load_manifest(void)
{
	struct csv_manifest m;

	if (manifest_read(&m) != 0 || m.magic != CSV_MANIFEST_MAGIC ||
	    m.version != CSV_MANIFEST_VERSION) {
		printk("csv_store: no valid manifest in flash, CSV playback unavailable\n");
		glu.present = false;
		foodlog_present = false;
		foodlog_count = 0;
		recompute_span();
		return;
	}
	load_manifest_into_ram(&m);
}

int csv_store_begin(const struct csv_upload_header *hdr)
{
	uint32_t region_off, region_size;
	const struct flash_area *fa;
	int err;

	if (hdr->track >= CSV_TRACK_COUNT) {
		return -EINVAL;
	}
	track_region(hdr->track, &region_off, &region_size);
	if (hdr->total_bytes == 0 || hdr->total_bytes > region_size) {
		return -EFBIG;
	}

	uint32_t erase_len = ROUND_UP(hdr->total_bytes, CSV_SECTOR_SIZE);

	err = flash_area_open(CSV_PARTITION_ID, &fa);
	if (err) {
		printk("csv_store: flash_area_open failed (%d)\n", err);
		return err;
	}
	err = flash_area_erase(fa, region_off, erase_len);
	flash_area_close(fa);
	if (err) {
		printk("csv_store: erase failed (%d)\n", err);
		return err;
	}

	staging.active = true;
	staging.track = hdr->track;
	staging.byte_len = hdr->total_bytes;
	staging.crc32 = hdr->crc32;
	staging.received = 0;
	staging.interval_s = hdr->interval_s;
	staging.row_count = hdr->row_count;
	staging.base_epoch_s = hdr->base_epoch_s;

	printk("csv_store: begin track=%u bytes=%u rows=%u interval=%us (erased %u B)\n",
	       hdr->track, hdr->total_bytes, hdr->row_count, hdr->interval_s, erase_len);
	return 0;
}

int csv_store_write(uint32_t offset, const uint8_t *data, uint16_t len)
{
	uint32_t region_off, region_size;
	const struct flash_area *fa;
	int err;

	if (!staging.active) {
		return -EPERM;
	}
	if (len == 0) {
		return 0;
	}
	track_region(staging.track, &region_off, &region_size);
	if ((uint64_t)offset + len > staging.byte_len) {
		return -EINVAL;
	}

	err = flash_area_open(CSV_PARTITION_ID, &fa);
	if (err) {
		return err;
	}
	err = flash_area_write(fa, region_off + offset, data, len);
	flash_area_close(fa);
	if (err) {
		printk("csv_store: write @%u len=%u failed (%d)\n", offset, len, err);
		return err;
	}

	/* offset need not be strictly monotonic, but the app streams it that way;
	 * received tracks the high-water mark so STATUS/COMMIT can sanity-check. */
	if (offset + len > staging.received) {
		staging.received = offset + len;
	}
	return 0;
}

static int flash_crc32(uint32_t region_off, uint32_t len, uint32_t *out_crc)
{
	const struct flash_area *fa;
	int err = flash_area_open(CSV_PARTITION_ID, &fa);
	uint8_t buf[128];
	uint32_t crc = 0;
	uint32_t done = 0;

	if (err) {
		return err;
	}
	while (done < len) {
		uint32_t chunk = MIN((uint32_t)sizeof(buf), len - done);

		err = flash_area_read(fa, region_off + done, buf, chunk);
		if (err) {
			break;
		}
		crc = crc32_ieee_update(crc, buf, chunk);
		done += chunk;
	}
	flash_area_close(fa);
	if (err) {
		return err;
	}
	*out_crc = crc;
	return 0;
}

int csv_store_commit(uint8_t track, bool *crc_ok)
{
	uint32_t region_off, region_size;
	uint32_t crc = 0;
	struct csv_manifest m;
	int err;

	if (crc_ok) {
		*crc_ok = false;
	}
	if (!staging.active || staging.track != track) {
		return -EPERM;
	}
	if (staging.received != staging.byte_len) {
		printk("csv_store: commit short (%u/%u bytes)\n", staging.received, staging.byte_len);
		return -EIO;
	}
	track_region(track, &region_off, &region_size);

	err = flash_crc32(region_off, staging.byte_len, &crc);
	if (err) {
		return err;
	}
	if (crc != staging.crc32) {
		printk("csv_store: commit CRC mismatch (got 0x%08x want 0x%08x)\n", crc,
		       staging.crc32);
		return -EIO;
	}
	if (crc_ok) {
		*crc_ok = true;
	}

	/* Merge into the existing manifest so the other track is preserved. */
	if (manifest_read(&m) != 0 || m.magic != CSV_MANIFEST_MAGIC ||
	    m.version != CSV_MANIFEST_VERSION) {
		memset(&m, 0, sizeof(m));
		m.magic = CSV_MANIFEST_MAGIC;
		m.version = CSV_MANIFEST_VERSION;
	}
	m.track[track].present = 1;
	m.track[track]._pad = 0;
	m.track[track].interval_s = staging.interval_s;
	m.track[track].row_count = staging.row_count;
	m.track[track].base_epoch_s = staging.base_epoch_s;
	m.track[track].byte_len = staging.byte_len;
	m.track[track].crc32 = staging.crc32;

	err = manifest_write(&m);
	if (err) {
		printk("csv_store: manifest write failed (%d)\n", err);
		return err;
	}

	staging.active = false;
	load_manifest_into_ram(&m);
	printk("csv_store: commit ok track=%u\n", track);
	return 0;
}

void csv_store_abort(void)
{
	if (staging.active) {
		printk("csv_store: upload aborted (track=%u)\n", staging.track);
	}
	staging.active = false;
}

int csv_store_clear(uint8_t track)
{
	struct csv_manifest m;
	int err;

	if (track >= CSV_TRACK_COUNT) {
		return -EINVAL;
	}
	if (manifest_read(&m) != 0 || m.magic != CSV_MANIFEST_MAGIC) {
		memset(&m, 0, sizeof(m));
		m.magic = CSV_MANIFEST_MAGIC;
		m.version = CSV_MANIFEST_VERSION;
	}
	memset(&m.track[track], 0, sizeof(m.track[track]));
	err = manifest_write(&m);
	if (err) {
		return err;
	}
	load_manifest_into_ram(&m);
	printk("csv_store: cleared track=%u\n", track);
	return 0;
}

uint32_t csv_store_received(void)
{
	return staging.active ? staging.received : 0;
}

bool csv_glucose_available(void)
{
	return glu.present;
}

bool csv_glucose_lookup(double sim_clock_min, float *out_mg_dl)
{
	const struct flash_area *fa;
	int16_t sample = 0;
	uint32_t row;

	if (!glu.present || glu.interval_s == 0 || glu.row_count == 0) {
		return false;
	}
	if (sim_clock_min < 0.0) {
		sim_clock_min = 0.0;
	}
	row = (uint32_t)(sim_clock_min * 60.0 / (double)glu.interval_s);
	row %= glu.row_count;

	if (flash_area_open(CSV_PARTITION_ID, &fa) != 0) {
		return false;
	}
	if (flash_area_read(fa, CSV_GLUCOSE_REGION_OFF + (off_t)row * CSV_GLUCOSE_ROW_BYTES,
			    &sample, sizeof(sample)) != 0) {
		flash_area_close(fa);
		return false;
	}
	flash_area_close(fa);

	*out_mg_dl = (float)sample;
	return true;
}

int csv_foodlog_window(double t0_s, double t1_s, struct csv_food_hit *out, int max)
{
	int n = 0;

	if (!foodlog_present || foodlog_count == 0 || max <= 0 || playback_span_s <= 0.0) {
		return 0;
	}

	double a = fmod(t0_s, playback_span_s);

	if (a < 0.0) {
		a += playback_span_s;
	}
	double b = a + (t1_s - t0_s);

	for (uint32_t i = 0; i < foodlog_count && n < max; i++) {
		double off = (double)foodlog_cache[i].offset_s;

		/* half-open [a, b); also catch the wrapped tail of a window that
		 * crosses the span boundary */
		if ((off >= a && off < b) || (b > playback_span_s && off < (b - playback_span_s))) {
			out[n++].carbs_g = foodlog_cache[i].carbs_g;
		}
	}
	return n;
}
