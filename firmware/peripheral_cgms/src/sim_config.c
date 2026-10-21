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

uint8_t sim_config_sensor_count_for(uint8_t comm_profile)
{
	return comm_profile == SIM_COMM_DEXCOM ? 1 : SIM_SENSOR_COUNT;
}

void sim_config_set_defaults(struct sim_config *cfg)
{
	memset(cfg, 0, sizeof(*cfg));
	cfg->magic = SIM_CONFIG_MAGIC;
	cfg->version = SIM_CONFIG_VERSION;
	cfg->sensor_count = SIM_SENSOR_COUNT;
	cfg->comm_profile = SIM_COMM_SIG_CGMS;
	cfg->speed_mult = SIM_SPEED_DEFAULT;
	memcpy(cfg->dexcom_tx_id, SIM_DEXCOM_ID_DEFAULT, SIM_DEXCOM_ID_BUF);

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

	bool is_v5 = !err && cfg->magic == SIM_CONFIG_MAGIC &&
		     cfg->version == SIM_CONFIG_VERSION_V5;
	bool is_v6 = !err && cfg->magic == SIM_CONFIG_MAGIC &&
		     cfg->version == SIM_CONFIG_VERSION_V6;

	if (err || cfg->magic != SIM_CONFIG_MAGIC ||
	    (cfg->version != SIM_CONFIG_VERSION && !is_v5 && !is_v6)) {
		printk("sim_config: no valid config in flash (err=%d), using defaults\n", err);
		sim_config_set_defaults(cfg);
		return err ? err : -ENOENT;
	}

	if (is_v5) {
		/* A v5 image ends where user_name[] begins; what we just read past
		 * it is erased flash (0xFF), not names. Keep every slot, add empty
		 * names; the next save writes v6. */
		memset(cfg->user_name, 0, sizeof(cfg->user_name));
		printk("sim_config: migrated v5 -> v%u (user names empty)\n", SIM_CONFIG_VERSION);
	}
	if (is_v5 || is_v6) {
		/* A v5/v6 image ends before dexcom_tx_id[]: what we read past it is erased
		 * flash. Keep everything, default the transmitter ID; the next save writes v7. */
		memcpy(cfg->dexcom_tx_id, SIM_DEXCOM_ID_DEFAULT, SIM_DEXCOM_ID_BUF);
		cfg->version = SIM_CONFIG_VERSION;
		if (is_v6) {
			printk("sim_config: migrated v6 -> v%u (default Dexcom ID)\n",
			       SIM_CONFIG_VERSION);
		}
	}
	/* A name is always NUL-terminated, whatever the flash held. */
	for (int i = 0; i < MAX_SIM_SENSORS; i++) {
		cfg->user_name[i][SIM_USER_NAME_MAX - 1] = '\0';
	}
	/* The transmitter ID is exactly six digits / capitals; anything else is the default. */
	cfg->dexcom_tx_id[SIM_DEXCOM_ID_LEN] = '\0';
	for (int i = 0; i < SIM_DEXCOM_ID_LEN; i++) {
		char c = cfg->dexcom_tx_id[i];

		if (!((c >= '0' && c <= '9') || (c >= 'A' && c <= 'Z'))) {
			memcpy(cfg->dexcom_tx_id, SIM_DEXCOM_ID_DEFAULT, SIM_DEXCOM_ID_BUF);
			break;
		}
	}

	/* The board mode decides how many sensors run: Dexcom -> 1, SIG CGMS -> the
	 * build's CONFIG_APP_SENSOR_COUNT. Never trust a stored count: a flash image
	 * saved by a different build must not leave N identities advertising for fewer
	 * model slots. The data of the slots that are not running stays in flash. */
	if (cfg->comm_profile != SIM_COMM_DEXCOM) {
		cfg->comm_profile = SIM_COMM_SIG_CGMS;
	}
	cfg->sensor_count = sim_config_sensor_count_for(cfg->comm_profile);

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
