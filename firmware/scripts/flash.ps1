<#
Flashes the most recent build (see build.ps1) via J-Link.
#>
param(
    [string]$NcsPath = $env:NCS_PATH,
    [string]$ToolchainRoot = "C:\ncs\toolchains\936afb6332"
)

if (-not $NcsPath) {
    $NcsPath = "C:\ncs\v3.3.1"
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$buildDir = Join-Path $repoRoot "build"

if (-not (Test-Path $buildDir)) {
    Write-Error "No build found at $buildDir - run build.ps1 first."
    exit 1
}

. "$PSScriptRoot\toolchain-env.ps1" -ToolchainRoot $ToolchainRoot

# `west flash` (an extension command) needs to run from inside the west
# workspace (where .west lives) to be discovered at all.
Push-Location $NcsPath
try {
    west flash --runner jlink -d $buildDir
    if ($LASTEXITCODE -ne 0) {
        Write-Error "west flash failed (exit $LASTEXITCODE)"
        exit $LASTEXITCODE
    }
} finally {
    Pop-Location
}

Write-Host "Flash complete."
