<#
Sets up the environment variables the NCS Toolchain Manager's own
"nRF Connect SDK Command Prompt" would set, so plain `west`/`cmake`/`ninja`/
the ARM GCC resolve to the toolchain install rather than whatever happens to
be first on the user's PATH (e.g. this repo's own Python .venv, which
otherwise shadows `python` and breaks `west` with
"ModuleNotFoundError: No module named 'west'").

Mirrors <ToolchainRoot>\environment.json. Dot-source this from other scripts:
  . "$PSScriptRoot\toolchain-env.ps1" -ToolchainRoot $ToolchainRoot
#>
param(
    [string]$ToolchainRoot = "C:\ncs\toolchains\936afb6332"
)

if (-not (Test-Path $ToolchainRoot)) {
    Write-Error "NCS toolchain not found: $ToolchainRoot"
    exit 1
}

$relativePathDirs = @(
    ".",
    "mingw64\bin",
    "bin",
    "opt\bin",
    "opt\bin\Scripts",
    "opt\nanopb\generator-bin",
    "nrfutil\bin",
    "opt\zephyr-sdk\arm-zephyr-eabi\bin",
    "opt\zephyr-sdk\riscv64-zephyr-elf\bin"
)
$absPathDirs = $relativePathDirs | ForEach-Object { Join-Path $ToolchainRoot $_ }
$env:PATH = ($absPathDirs -join ";") + ";" + $env:PATH

$pythonPathDirs = @("opt\bin", "opt\bin\Lib", "opt\bin\Lib\site-packages") |
    ForEach-Object { Join-Path $ToolchainRoot $_ }
$env:PYTHONPATH = $pythonPathDirs -join ";"

$env:NRFUTIL_HOME = Join-Path $ToolchainRoot "nrfutil\home"
$env:ZEPHYR_TOOLCHAIN_VARIANT = "zephyr"
$env:ZEPHYR_SDK_INSTALL_DIR = Join-Path $ToolchainRoot "opt\zephyr-sdk"
