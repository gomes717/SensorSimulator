#include <string.h>
#include <errno.h>
#include <zephyr/kernel.h>
#include <zephyr/storage/flash_map.h>
#include <zephyr/sys/printk.h>

#include "sim_config.h"
#include "models/cgmsim_cambridge.h"

/* Must fit within the 4 KB storage_partition defined in
 * boards/nrf54l15dk_nrf54l15_cpuapp.overlay. */
BUILD_ASSERT(sizeof(struct sim_config) <= 4096,
	     "struct sim_config no longer fits the storage_partition");

#define STORAGE_PARTITION_ID FIXED_PARTITION_ID(sim_storage_partition)

void sim_config_set_defaults(struct sim_config *cfg)
{
	memset(cfg, 0, sizeof(*cfg));
	cfg->magic = SIM_CONFIG_MAGIC;
	cfg->version = SIM_CONFIG_VERSION;
	cfg->mode = SIM_MODE_NORMAL;
	cfg->model_id = SIM_MODEL_CAMBRIDGE;

	/* CambridgeParams is a plain struct-of-doubles whose field order
	 * matches PARAM_NAMES in SensorSimulator/src/models/cambridge.py
	 * exactly (see PROTOCOL_SPEC.md) — safe to walk as a double[] here. */
	CambridgeParams p = cambridge_default_params();
	const double *src = (const double *)&p;
	size_t n = sizeof(CambridgeParams) / sizeof(double);

	for (size_t i = 0; i < n && i < MAX_MODEL_PARAMS; i++) {
		cfg->model_params[i] = (float)src[i];
	}

	cfg->sensor_id = SIM_SENSOR_IDEAL; /* stateless, no params to default */

	cfg->food_count = 0;
	cfg->exercise_count = 0;
}

int sim_config_load_from_flash(struct sim_config *cfg)
{
	const struct flash_area *fa;
	int err;

	err = flash_area_open(STORAGE_PARTITION_ID, &fa);
	if (err) {
		printk("sim_config: flash_area_open failed (%d), using defaults\n", err);
		sim_config_set_defaults(cfg);
		return err;
	}

	err = flash_area_read(fa, 0, cfg, sizeof(*cfg));
	flash_area_close(fa);

	if (err || cfg->magic != SIM_CONFIG_MAGIC || cfg->version != SIM_CONFIG_VERSION) {
		printk("sim_config: no valid config in flash (err=%d), using defaults\n", err);
		sim_config_set_defaults(cfg);
		return err ? err : -ENOENT;
	}

	printk("sim_config: loaded from flash (model_id=%u, sensor_id=%u, mode=%u, "
	       "food=%u, exercise=%u)\n",
	       cfg->model_id, cfg->sensor_id, cfg->mode, cfg->food_count, cfg->exercise_count);
	return 0;
}

int sim_config_save_to_flash(const struct sim_config *cfg)
{
	const struct flash_area *fa;
	int err;

	err = flash_area_open(STORAGE_PARTITION_ID, &fa);
	if (err) {
		printk("sim_config: flash_area_open failed (%d)\n", err);
		return err;
	}

	err = flash_area_erase(fa, 0, fa->fa_size);
	if (err) {
		printk("sim_config: flash_area_erase failed (%d)\n", err);
		flash_area_close(fa);
		return err;
	}

	err = flash_area_write(fa, 0, cfg, sizeof(*cfg));
	flash_area_close(fa);
	if (err) {
		printk("sim_config: flash_area_write failed (%d)\n", err);
		return err;
	}

	return 0;
}
