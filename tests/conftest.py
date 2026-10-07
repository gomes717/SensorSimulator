"""Keep the test suite away from the user's real data/ folder.

The app persists profiles, settings and the board layout under data/, and
several tests drive code that saves them. Without this, a plain `pytest` run
rewrote the user's own uncommitted edits to those files. Each test now gets a
private copy of data/ and the three persistence modules are pointed at it.
"""

import shutil
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))


@pytest.fixture(autouse=True)
def _private_data_dir(tmp_path, monkeypatch):
    from models import app_settings, board_layout, profile_store, user_store

    data = tmp_path / "data"
    real = _ROOT / "data"
    if real.exists():
        shutil.copytree(real, data)
    else:
        data.mkdir()
    for module, file_attr, name in (
        (app_settings, "_SETTINGS_FILE", "settings.json"),
        (board_layout, "_LAYOUT_FILE", "board_layout.json"),
        (profile_store, "_PROFILES_FILE", "profiles.json"),
        (user_store, "_USERS_FILE", "users.json"),
    ):
        monkeypatch.setattr(module, "_DATA_DIR", data)
        monkeypatch.setattr(module, file_attr, data / name)
    monkeypatch.setattr(user_store, "_USERS_DIR", data / "users")
