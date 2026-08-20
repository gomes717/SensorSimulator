<#
Reverse of sync-to-ncs.ps1: pulls the current state of the firmware from the
live NCS checkout back into this project, so edits made directly against
C:\ncs\v3.3.1 (the usual hardware-debugging workflow) get captured into git.

Run this before committing after a hands-on-hardware session.
#>
param(
    [string]$NcsPath = $env:NCS_PATH
)

if (-not $NcsPath) {
    $NcsPath = "C:\ncs\v3.3.1"
}

if (-not (Test-Path $NcsPath)) {
    Write-Error "NCS workspace topdir not found: $NcsPath (set -NcsPath or `$env:NCS_PATH)"
    exit 1
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$sampleSrc = Join-Path $NcsPath "nrf\samples\bluetooth\peripheral_cgms"
$overlaySrc = Join-Path $NcsPath "nrf"
$sampleDst = Join-Path $repoRoot "peripheral_cgms"
$overlayDst = Join-Path $repoRoot "overlay\nrf"

if (-not (Test-Path $sampleSrc)) {
    Write-Error "Missing $sampleSrc"
    exit 1
}

Write-Host "Pulling application source <- $sampleSrc"
foreach ($item in @("CMakeLists.txt", "Kconfig", "Kconfig.sysbuild", "README.rst", "prj.conf", "sample.yaml")) {
    $s = Join-Path $sampleSrc $item
    if (Test-Path $s) {
        Copy-Item -LiteralPath $s -Destination (Join-Path $sampleDst $item) -Force
    }
}
Copy-Item -LiteralPath (Join-Path $sampleSrc "boards") -Destination $sampleDst -Recurse -Force
Copy-Item -LiteralPath (Join-Path $sampleSrc "src") -Destination $sampleDst -Recurse -Force

Write-Host "Pulling CGMS service overlay <- $overlaySrc"
Copy-Item -LiteralPath (Join-Path $overlaySrc "include\bluetooth\services\cgms.h") `
    -Destination (Join-Path $overlayDst "include\bluetooth\services\cgms.h") -Force
robocopy (Join-Path $overlaySrc "subsys\bluetooth\services\cgms") (Join-Path $overlayDst "subsys\bluetooth\services\cgms") /E /NFL /NDL /NJH /NJS | Out-Null
if ($LASTEXITCODE -ge 8) {
    Write-Error "robocopy failed while pulling overlay (exit $LASTEXITCODE)"
    exit 1
}

Write-Host "Pull complete. Review 'git status' / 'git diff' in firmware/ before committing."
exit 0
