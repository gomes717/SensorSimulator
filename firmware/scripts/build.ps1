<#
Builds the peripheral_cgms firmware using the NCS toolchain, from this
project's tracked source. `west` must be invoked from the workspace topdir
with explicit -s (source) / -d (build dir) since the source lives outside
the topdir tree.
#>
param(
    [string]$NcsPath = $env:NCS_PATH,
    [string]$Board = "nrf54l15dk/nrf54l15/cpuapp",
    [string]$ToolchainRoot = "C:\ncs\toolchains\936afb6332",
    [switch]$Pristine
)

if (-not $NcsPath) {
    $NcsPath = "C:\ncs\v3.3.1"
}

$repoRoot = Split-Path -Parent $PSScriptRoot
. "$PSScriptRoot\toolchain-env.ps1" -ToolchainRoot $ToolchainRoot

& (Join-Path $PSScriptRoot "sync-to-ncs.ps1") -NcsPath $NcsPath
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$sourceDir = Join-Path $NcsPath "nrf\samples\bluetooth\peripheral_cgms"
$buildDir = Join-Path $repoRoot "build"

Push-Location $NcsPath
try {
    $westArgs = @("build", "-b", $Board, "-s", $sourceDir, "-d", $buildDir)
    if ($Pristine) { $westArgs += "-p" }
    west @westArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Error "west build failed (exit $LASTEXITCODE)"
        exit $LASTEXITCODE
    }
} finally {
    Pop-Location
}

Write-Host "Build complete: $buildDir"
