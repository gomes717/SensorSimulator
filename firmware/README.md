Firmware
========

Nordic nRF Connect SDK (NCS) firmware for the CGM sensor simulator board,
git-tracked as part of this project.

Layout
------

- `peripheral_cgms/` - the sample application (single simulated CGM sensor:
  `src/main.c`, `model_thread.c`, `comm_thread.c`, `config_service.c`,
  `sim_config.c`, glucose models under `src/models/`). See
  `../PROTOCOL_SPEC.md` for the BLE protocol this implements.
- `overlay/nrf/` - the patched CGMS BLE GATT service
  (`subsys/bluetooth/services/cgms/`, `include/bluetooth/services/cgms.h`).
  This is Nordic's stock CGMS service extended by this project (multi-
  instance support, RACP/SOCP fixes); it's shared library code that lives
  inside the `nrf` module of the NCS checkout, not inside the sample.
- `scripts/` - PowerShell helpers, see below.

Why the sync step
------------------

`west` must be run from the NCS workspace topdir (`C:\ncs\v3.3.1`, the
directory containing `.west`) because that's where the Zephyr/toolchain
modules are registered. The firmware source itself lives outside that
workspace (in this project, under git), so building means:

1. Copy this project's tracked source into place inside the NCS checkout
   (`sync-to-ncs.ps1`).
2. Run `west build -s <source> -d <build-dir>` from the topdir.

This mirrors how the firmware has actually been built and flashed to
hardware throughout this project - it just adds git tracking and a
one-command build/flash from here instead of hand-editing files under
`C:\ncs\...`.

Prerequisites
-------------

- NCS v3.3.1 toolchain installed at `C:\ncs\v3.3.1` (the `nrf` module must
  contain the `peripheral_cgms` sample and `cgms` service - i.e. a normal
  NCS Toolchain Manager install). Different machine/path: pass `-NcsPath`
  to the scripts or set `$env:NCS_PATH`.
- J-Link (SEGGER) for flashing - `west flash --runner jlink` is used because
  the default `nrfutil` runner isn't set up on this machine.
- nRF54L15 DK board.

Build & flash
-------------

From PowerShell, in `firmware/`:

```powershell
.\scripts\build.ps1
.\scripts\flash.ps1
```

Or via `make` (Git Bash, calls the same scripts):

```bash
make build
make flash
```

`build.ps1` always syncs the project's source into the NCS checkout first,
so it stays the source of truth. Add `-Pristine` (or `make pristine`) to
force a clean CMake reconfigure.

Board/toolchain paths default to the values verified for this project
(`nrf54l15dk/nrf54l15/cpuapp`, toolchain root
`C:\ncs\toolchains\936afb6332`); override with `-Board`/`-ToolchainRoot` if
your setup differs.

Note: `build.ps1`/`flash.ps1` dot-source `toolchain-env.ps1`, which prepends
the toolchain's own `python`/`cmake`/`ninja`/ARM GCC to `PATH` for that
process (mirroring the toolchain's `environment.json` - the same setup the
"nRF Connect SDK Command Prompt" does). Without it, plain `west`/`python` on
this machine resolve to this project's own `.venv` first and `west` fails
with `ModuleNotFoundError: No module named 'west'`.

Pulling hardware-session edits back into git
---------------------------------------------

If you end up editing directly under `C:\ncs\v3.3.1\nrf\...` (e.g. while
debugging on the bench), run this before committing to bring those edits
back into the project:

```powershell
.\scripts\sync-from-ncs.ps1
```

Then review with `git status` / `git diff` as usual.

Monitoring serial output
-------------------------

The board exposes two "JLink CDC UART Port" COM ports; on this machine
COM10 carries `printk`/log output at 115200 baud, 8N1 (COM9 is silent).
Read it from PowerShell with `System.IO.Ports.SerialPort` - Git Bash has no
native Windows serial access.
