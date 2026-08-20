<#
Copies this project's tracked firmware source into the live NCS checkout
so `west` (which must run from the NCS workspace topdir) picks it up.

- firmware/peripheral_cgms/  -> <NcsPath>/nrf/samples/bluetooth/peripheral_cgms
- firmware/overlay/nrf/...   -> <NcsPath>/nrf/...  (the patched CGMS BLE
  service living in nrf/subsys + nrf/include, shared by all nrf samples)

Build artifacts (build/, build_1/, .west caches) in the NCS checkout are
left untouched.
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
$sampleSrc = Join-Path $repoRoot "peripheral_cgms"
$overlaySrc = Join-Path $repoRoot "overlay\nrf"
$sampleDst = Join-Path $NcsPath "nrf\samples\bluetooth\peripheral_cgms"
$nrfDst = Join-Path $NcsPath "nrf"

if (-not (Test-Path $sampleSrc)) {
    Write-Error "Missing $sampleSrc"
    exit 1
}

Write-Host "Syncing application source -> $sampleDst"
foreach ($item in @("CMakeLists.txt", "Kconfig", "Kconfig.sysbuild", "README.rst", "prj.conf", "sample.yaml")) {
    $s = Join-Path $sampleSrc $item
    if (Test-Path $s) {
        Copy-Item -LiteralPath $s -Destination (Join-Path $sampleDst $item) -Force
    }
}
Copy-Item -LiteralPath (Join-Path $sampleSrc "boards") -Destination $sampleDst -Recurse -Force
Copy-Item -LiteralPath (Join-Path $sampleSrc "src") -Destination $sampleDst -Recurse -Force

Write-Host "Syncing CGMS service overlay -> $nrfDst"
robocopy $overlaySrc $nrfDst /E /NFL /NDL /NJH /NJS | Out-Null
if ($LASTEXITCODE -ge 8) {
    Write-Error "robocopy failed while syncing overlay (exit $LASTEXITCODE)"
    exit 1
}

Write-Host "Sync complete."
exit 0
