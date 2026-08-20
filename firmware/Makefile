BOARD ?= nrf54l15dk/nrf54l15/cpuapp
NCS_PATH ?= C:\ncs\v3.3.1

.PHONY: build flash pristine sync-to-ncs sync-from-ncs clean

build:
	@powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build.ps1 -NcsPath "$(NCS_PATH)" -Board "$(BOARD)"

pristine:
	@powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build.ps1 -NcsPath "$(NCS_PATH)" -Board "$(BOARD)" -Pristine

flash:
	@powershell -NoProfile -ExecutionPolicy Bypass -File scripts/flash.ps1

sync-to-ncs:
	@powershell -NoProfile -ExecutionPolicy Bypass -File scripts/sync-to-ncs.ps1 -NcsPath "$(NCS_PATH)"

sync-from-ncs:
	@powershell -NoProfile -ExecutionPolicy Bypass -File scripts/sync-from-ncs.ps1 -NcsPath "$(NCS_PATH)"

clean:
	rm -rf build
