#include <math.h>
#include <string.h>
#include <errno.h>
#include <zephyr/kernel.h>
#include <zephyr/storage/flash_map.h>
#include <zephyr/sys/crc.h>
#include <zephyr/sys/printk.h>

#include "csv_store.h"

/*
 * sim_csv_partition layout (252 KB total, see the board overlay). Per-slot,
 * 4 KB-aligned so flash_area_erase() can act on any region:
 *
 *   slot i:  base = i * CSV_SLOT_STRIDE
 *            + 0x00000  glucose region  (48 KB -> up to 24576 int16 samples)
 *            + 0x0C000  foodlog region  (4 KB  -> up to 512 entries)
 *   0x34000  manifest   (4 KB, one sector, rewritten whole; all slots)
 */
#define CSV_PARTITION_ID FIXED_PARTITION_ID(sim_csv_partition)

#define CSV_SECTOR_SIZE 4096u

#define CSV_GLUCOSE_REGION_SIZE 0x0000C000u /* 48 KB */
#define CSV_FOODLOG_REGION_SIZE 0x00001000u /* 4 KB */
#define CSV_SLOT_STRIDE         (CSV_GLUCOSE_REGION_SIZE + CSV_FOODLOG_REGION_SIZE)
#define CSV_MANIFEST_OFF        (CSV_SLOT_STRIDE * MAX_SIM_SENSORS)
#define CSV_MANIFEST_SIZE       0x00001000u

#define CSV_MANIFEST_MAGIC   0x32565343u /* "CSV2" */
#define CSV_MANIFEST_VERSION 2

#define CSV_FOODLOG_MAX 128 /* per slot, in RAM for per-tick lookup */

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
	struct csv_track_manifest track[MAX_SIM_SENSORS][CSV_TRACK_COUNT];
} __packed;

BUILD_ASSERT(sizeof(struct csv_manifest) <= CSV_MANIFEST_SIZE, "csv_manifest too large");
BUILD_ASSERT(CSV_MANIFEST_OFF + CSV_MANIFEST_SIZE <= 0x0003F000u,
	     "sim_csv_partition too small for MAX_SIM_SENSORS slots");

/* ── In-RAM playback state per slot (rebuilt from the manifest) ── */

struct slot_playback {
	bool glu_present;
	uint16_t glu_interval_s;
	uint32_t glu_row_count;
	double span_s; /* glucose window length, or 24 h if only a foodlog */
	bool foodlog_present;
	uint32_t foodlog_count;
	struct csv_foodlog_row foodlog[CSV_FOODLOG_MAX];
};

static struct slot_playback pb[MAX_SIM_SENSORS];

/* ── In-progress upload staging (one at a time, keyed by slot+track) ── */

static struct {
	bool active;
	uint8_t slot;
	uint8_t track;
	uint32_t byte_len;
	uint32_t crc32;
	uint32_t received;
	uint16_t interval_s;
	uint32_t row_count;
	uint32_t base_epoch_s;
} staging;

static uint32_t region_off(uint8_t slot, uint8_t track)
{
	uint32_t base = (uint32_t)slot * CSV_SLOT_STRIDE;

	return (track == CSV_TRACK_FOODLOG) ? base + CSV_GLUCOSE_REGION_SIZE : base;
}

static uint32_t region_size(uint8_t track)
{
	return (track == CSV_TRACK_FOODLOG) ? CSV_FOODLOG_REGION_SIZE : CSV_GLUCOSE_REGION_SIZE;
}

static void recompute_span(struct slot_playback *s)
{
	if (s->glu_present && s->glu_interval_s > 0) {
		s->span_s = (double)s->glu_row_count * (double)s->glu_interval_s;
	} else {
		s->span_s = 86400.0;
	}
	if (s->span_s <= 0.0) {
		s->span_s = 86400.0;
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

static void load_slot_from_manifest(const struct csv_manifest *m, uint8_t slot)
{
	struct slot_playback *s = &pb[slot];
	const struct csv_track_manifest *g = &m->track[slot][CSV_TRACK_GLUCOSE];
	const struct csv_track_manifest *f = &m->track[slot][CSV_TRACK_FOODLOG];

	s->glu_present = g->present && g->row_count > 0;
	s->glu_interval_s = g->interval_s;
	s->glu_row_count = g->row_count;

	s->foodlog_present = f->present && f->row_count > 0;
	s->foodlog_count = 0;
	if (s->foodlog_present) {
		const struct flash_area *fa;
		uint32_t n = MIN(f->row_count, (uint32_t)CSV_FOODLOG_MAX);

		if (flash_area_open(CSV_PARTITION_ID, &fa) == 0) {
			if (flash_area_read(fa, region_off(slot, CSV_TRACK_FOODLOG), s->foodlog,
					    n * CSV_FOODLOG_ROW_BYTES) == 0) {
				s->foodlog_count = n;
			}
			flash_area_close(fa);
		}
	}
	recompute_span(s);
}

void csv_store_load_manifest(void)
{
	struct csv_manifest m;

	if (manifest_read(&m) != 0 || m.magic != CSV_MANIFEST_MAGIC ||
	    m.version != CSV_MANIFEST_VERSION) {
		printk("csv_store: no valid manifest, CSV playback unavailable on all slots\n");
		memset(pb, 0, sizeof(pb));
		for (int i = 0; i < MAX_SIM_SENSORS; i++) {
			recompute_span(&pb[i]);
		}
		return;
	}
	for (int i = 0; i < MAX_SIM_SENSORS; i++) {
		load_slot_from_manifest(&m, i);
		printk("csv_store: slot %d manifest (glucose present=%d rows=%u interval=%us, "
		       "foodlog present=%d rows=%u)\n",
		       i, pb[i].glu_present, pb[i].glu_row_count, pb[i].glu_interval_s,
		       pb[i].foodlog_present, pb[i].foodlog_count);
	}
}

int csv_store_begin(uint8_t slot, const struct csv_upload_header *hdr)
{
	const struct flash_area *fa;
	int err;

	if (slot >= MAX_SIM_SENSORS || hdr->track >= CSV_TRACK_COUNT) {
		return -EINVAL;
	}
	if (hdr->total_bytes == 0 || hdr->total_bytes > region_size(hdr->track)) {
		return -EFBIG;
	}

	uint32_t off = region_off(slot, hdr->track);
	uint32_t erase_len = ROUND_UP(hdr->total_bytes, CSV_SECTOR_SIZE);

	err = flash_area_open(CSV_PARTITION_ID, &fa);
	if (err) {
		printk("csv_store: flash_area_open failed (%d)\n", err);
		return err;
	}
	err = flash_area_erase(fa, off, erase_len);
	flash_area_close(fa);
	if (err) {
		printk("csv_store: erase failed (%d)\n", err);
		return err;
	}

	staging.active = true;
	staging.slot = slot;
	staging.track = hdr->track;
	staging.byte_len = hdr->total_bytes;
	staging.crc32 = hdr->crc32;
	staging.received = 0;
	staging.interval_s = hdr->interval_s;
	staging.row_count = hdr->row_count;
	staging.base_epoch_s = hdr->base_epoch_s;

	printk("csv_store: begin slot=%u track=%u bytes=%u rows=%u interval=%us\n",
	       slot, hdr->track, hdr->total_bytes, hdr->row_count, hdr->interval_s);
	return 0;
}

int csv_store_write(uint32_t offset, const uint8_t *data, uint16_t len)
{
	const struct flash_area *fa;
	int err;

	if (!staging.active) {
		return -EPERM;
	}
	if (len == 0) {
		return 0;
	}
	if ((uint64_t)offset + len > staging.byte_len) {
		return -EINVAL;
	}

	err = flash_area_open(CSV_PARTITION_ID, &fa);
	if (err) {
		return err;
	}
	err = flash_area_write(fa, region_off(staging.slot, staging.track) + offset, data, len);
	flash_area_close(fa);
	if (err) {
		printk("csv_store: write @%u len=%u failed (%d)\n", offset, len, err);
		return err;
	}
	if (offset + len > staging.received) {
		staging.received = offset + len;
	}
	return 0;
}

static int flash_crc32(uint32_t off, uint32_t len, uint32_t *out_crc)
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

		err = flash_area_read(fa, off + done, buf, chunk);
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

static void manifest_defaults(struct csv_manifest *m)
{
	memset(m, 0, sizeof(*m));
	m->magic = CSV_MANIFEST_MAGIC;
	m->version = CSV_MANIFEST_VERSION;
}

int csv_store_commit(uint8_t slot, uint8_t track, bool *crc_ok)
{
	uint32_t crc = 0;
	struct csv_manifest m;
	int err;

	if (crc_ok) {
		*crc_ok = false;
	}
	if (!staging.active || staging.slot != slot || staging.track != track) {
		return -EPERM;
	}
	if (staging.received != staging.byte_len) {
		printk("csv_store: commit short (%u/%u bytes)\n", staging.received, staging.byte_len);
		return -EIO;
	}

	err = flash_crc32(region_off(slot, track), staging.byte_len, &crc);
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

	if (manifest_read(&m) != 0 || m.magic != CSV_MANIFEST_MAGIC ||
	    m.version != CSV_MANIFEST_VERSION) {
		manifest_defaults(&m);
	}
	struct csv_track_manifest *tm = &m.track[slot][track];

	tm->present = 1;
	tm->_pad = 0;
	tm->interval_s = staging.interval_s;
	tm->row_count = staging.row_count;
	tm->base_epoch_s = staging.base_epoch_s;
	tm->byte_len = staging.byte_len;
	tm->crc32 = staging.crc32;

	err = manifest_write(&m);
	if (err) {
		printk("csv_store: manifest write failed (%d)\n", err);
		return err;
	}

	staging.active = false;
	load_slot_from_manifest(&m, slot);
	printk("csv_store: commit ok slot=%u track=%u\n", slot, track);
	return 0;
}

void csv_store_abort(void)
{
	if (staging.active) {
		printk("csv_store: upload aborted (slot=%u track=%u)\n", staging.slot, staging.track);
	}
	staging.active = false;
}

int csv_store_clear(uint8_t slot, uint8_t track)
{
	struct csv_manifest m;
	int err;

	if (slot >= MAX_SIM_SENSORS || track >= CSV_TRACK_COUNT) {
		return -EINVAL;
	}
	if (manifest_read(&m) != 0 || m.magic != CSV_MANIFEST_MAGIC) {
		manifest_defaults(&m);
	}
	memset(&m.track[slot][track], 0, sizeof(m.track[slot][track]));
	err = manifest_write(&m);
	if (err) {
		return err;
	}
	load_slot_from_manifest(&m, slot);
	printk("csv_store: cleared slot=%u track=%u\n", slot, track);
	return 0;
}

uint32_t csv_store_received(void)
{
	return staging.active ? staging.received : 0;
}

bool csv_glucose_available(uint8_t slot)
{
	return slot < MAX_SIM_SENSORS && pb[slot].glu_present;
}

bool csv_glucose_lookup(uint8_t slot, double sim_clock_min, float *out_mg_dl)
{
	const struct flash_area *fa;
	int16_t sample = 0;
	uint32_t row;

	if (slot >= MAX_SIM_SENSORS) {
		return false;
	}
	struct slot_playback *s = &pb[slot];

	if (!s->glu_present || s->glu_interval_s == 0 || s->glu_row_count == 0) {
		return false;
	}
	if (sim_clock_min < 0.0) {
		sim_clock_min = 0.0;
	}
	row = (uint32_t)(sim_clock_min * 60.0 / (double)s->glu_interval_s);
	row %= s->glu_row_count;

	if (flash_area_open(CSV_PARTITION_ID, &fa) != 0) {
		return false;
	}
	if (flash_area_read(fa, region_off(slot, CSV_TRACK_GLUCOSE) +
			    (off_t)row * CSV_GLUCOSE_ROW_BYTES, &sample, sizeof(sample)) != 0) {
		flash_area_close(fa);
		return false;
	}
	flash_area_close(fa);

	*out_mg_dl = (float)sample;
	return true;
}

int csv_foodlog_window(uint8_t slot, double t0_s, double t1_s,
		       struct csv_food_hit *out, int max)
{
	int n = 0;

	if (slot >= MAX_SIM_SENSORS) {
		return 0;
	}
	struct slot_playback *s = &pb[slot];

	if (!s->foodlog_present || s->foodlog_count == 0 || max <= 0 || s->span_s <= 0.0) {
		return 0;
	}

	double a = fmod(t0_s, s->span_s);

	if (a < 0.0) {
		a += s->span_s;
	}
	double b = a + (t1_s - t0_s);

	for (uint32_t i = 0; i < s->foodlog_count && n < max; i++) {
		double off = (double)s->foodlog[i].offset_s;

		if ((off >= a && off < b) || (b > s->span_s && off < (b - s->span_s))) {
			out[n++].carbs_g = s->foodlog[i].carbs_g;
		}
	}
	return n;
}
