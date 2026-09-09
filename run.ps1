# Launch the desktop app. Works from any terminal even when `uv` is not on PATH
# (a stale VS Code / shell environment) — resolves uv by known locations.
#   .\run.ps1                 -> uv run python src/main.py
#   .\run.ps1 -m pytest -q    -> uv run python -m pytest -q   (args pass through)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

$uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $uv) {
    foreach ($p in @(
        "$env:USERPROFILE\.local\bin\uv.exe",
        "$env:LOCALAPPDATA\Programs\uv\uv.exe",
        "$env:USERPROFILE\.cargo\bin\uv.exe"
    )) { if (Test-Path $p) { $uv = $p; break } }
}
if (-not $uv) { Write-Error "uv not found. Install: https://docs.astral.sh/uv/  (or add %USERPROFILE%\.local\bin to PATH and reopen the terminal)"; exit 1 }

if ($args.Count -eq 0) { & $uv run python src/main.py }
else                   { & $uv run python @args }
exit $LASTEXITCODE
