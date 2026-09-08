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
	cfg->sensor_count = SIM_SENSOR_COUNT;
	cfg->comm_profile = SIM_COMM_SIG_CGMS;
	cfg->speed_mult = SIM_SPEED_DEFAULT;

	/* CambridgeParams is a plain struct-of-doubles whose field order
	 * matches PARAM_NAMES in SensorSimulator/src/models/cambridge.py
	 * exactly (see PROTOCOL_SPEC.md) — safe to walk as a double[] here. */
	CambridgeParams p = cambridge_default_params();
	const double *src = (const double *)&p;
	size_t n = sizeof(CambridgeParams) / sizeof(double);

	for (int s = 0; s < MAX_SIM_SENSORS; s++) {
		struct sensor_slot *slot = &cfg->slots[s];

		slot->data_source = SIM_DATA_MODEL;
		slot->model_id = SIM_MODEL_CAMBRIDGE;
		slot->sensor_id = SIM_SENSOR_IDEAL; /* stateless, no params to default */
		slot->food_count = 0;
		slot->exercise_count = 0;
		for (size_t i = 0; i < n && i < MAX_MODEL_PARAMS; i++) {
			slot->model_params[i] = (float)src[i];
		}
	}
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

#if 0  /* one-shot factory reset: set to 1 + reflash to wipe the persisted
	* sim_config (it lives in the external mx25r64, which a J-Link mass-
	* erase does not touch), then set back to 0 + reflash. */
	{
		int erc = flash_area_erase(fa, 0, fa->fa_size);

		printk("sim_config: FACTORY RESET - erased storage partition (%d)\n", erc);
	}
#endif

	err = flash_area_read(fa, 0, cfg, sizeof(*cfg));
	flash_area_close(fa);

	if (err || cfg->magic != SIM_CONFIG_MAGIC || cfg->version != SIM_CONFIG_VERSION) {
		printk("sim_config: no valid config in flash (err=%d), using defaults\n", err);
		sim_config_set_defaults(cfg);
		return err ? err : -ENOENT;
	}

	/* sensor_count is fixed by the build (CONFIG_APP_SENSOR_COUNT), not a
	 * runtime-writable field — always force it to this firmware's value so a
	 * flash image saved by a different build (e.g. the =1 regression build)
	 * can't leave =4 running only one model slot while 4 identities advertise. */
	cfg->sensor_count = SIM_SENSOR_COUNT;

	printk("sim_config: loaded from flash (sensor_count=%u, comm_profile=%u, "
	       "slot0 model_id=%u sensor_id=%u data_source=%u)\n",
	       cfg->sensor_count, cfg->comm_profile, cfg->slots[0].model_id,
	       cfg->slots[0].sensor_id, cfg->slots[0].data_source);
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
