#!/usr/bin/env bash
# Launch the desktop app from Git Bash / WSL even when `uv` is not on PATH.
#   ./run.sh                 -> uv run python src/main.py
#   ./run.sh -m pytest -q    -> uv run python -m pytest -q   (args pass through)
set -euo pipefail
cd "$(dirname "$0")"

uv_bin="$(command -v uv || true)"
if [ -z "$uv_bin" ]; then
  for p in "$HOME/.local/bin/uv" "$HOME/.local/bin/uv.exe" \
           "$LOCALAPPDATA/Programs/uv/uv.exe" "$HOME/.cargo/bin/uv"; do
    [ -x "$p" ] && uv_bin="$p" && break
  done
fi
if [ -z "$uv_bin" ]; then
  echo "uv not found. Install from https://docs.astral.sh/uv/ or add ~/.local/bin to PATH and reopen the terminal." >&2
  exit 1
fi

if [ "$#" -eq 0 ]; then exec "$uv_bin" run python src/main.py
else                    exec "$uv_bin" run python "$@"; fi
