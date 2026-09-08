"""App-wide settings persisted to data/settings.json: the glucose range thresholds
used for the graph bands / range-metrics panels, and the UI theme.

Mirrors profile_store.py's plain json + pathlib style — no external deps.
"""
from __future__ import annotations

import json
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_SETTINGS_FILE = _DATA_DIR / "settings.json"

DEFAULTS: dict[str, float] = {
    "tbr2_below": 54.0,
    "tbr1_below": 70.0,
    "tar1_above": 180.0,
    "tar2_above": 250.0,
}

THEME_DEFAULT = "system"
VALID_THEMES = ("system", "light", "dark")


def _read_raw() -> dict:
    if not _SETTINGS_FILE.exists():
        return {}
    try:
        data = json.loads(_SETTINGS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_raw(data: dict) -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _SETTINGS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load() -> dict[str, float]:
    """Return the saved range thresholds merged over DEFAULTS (so new keys always exist)."""
    settings = dict(DEFAULTS)
    data = _read_raw()
    for key in DEFAULTS:
        if isinstance(data.get(key), (int, float)):
            settings[key] = float(data[key])
    return settings


def save(settings: dict[str, float]) -> None:
    """Persist the range thresholds, leaving any other keys (e.g. theme) untouched."""
    data = _read_raw()
    for key in DEFAULTS:
        if isinstance(settings.get(key), (int, float)):
            data[key] = float(settings[key])
    _write_raw(data)


def load_pref(key: str, default):
    """Return an arbitrary persisted preference (speed multiplier, graph window …).

    Kept separate from the range-threshold load()/save() so new scalar UI
    preferences don't need their own file plumbing. Returns *default* if the
    key is absent or the stored value isn't the same basic type.
    """
    value = _read_raw().get(key)
    if isinstance(default, bool):
        return bool(value) if isinstance(value, bool) else default
    if isinstance(default, (int, float)):
        return type(default)(value) if isinstance(value, (int, float)) else default
    if isinstance(default, str):
        return value if isinstance(value, str) else default
    return value if value is not None else default


def save_pref(key: str, value) -> None:
    """Persist a single preference, leaving every other key untouched."""
    data = _read_raw()
    data[key] = value
    _write_raw(data)


def load_theme() -> str:
    """Return the saved UI theme: "system", "light" or "dark"."""
    theme = _read_raw().get("theme")
    return theme if theme in VALID_THEMES else THEME_DEFAULT


def save_theme(mode: str) -> None:
    """Persist the UI theme, leaving the range thresholds untouched."""
    if mode not in VALID_THEMES:
        mode = THEME_DEFAULT
    data = _read_raw()
    data["theme"] = mode
    _write_raw(data)
