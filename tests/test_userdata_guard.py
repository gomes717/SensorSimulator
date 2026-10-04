"""The harnesses must give data/ back exactly as the user had it."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import userdata_guard as ug


def test_restore_returns_uncommitted_edits_and_removes_files_the_run_created(tmp_path, monkeypatch):
    monkeypatch.setattr(ug, "_DATA", tmp_path)
    (tmp_path / "profiles.json").write_bytes(b"user edits, never committed")

    saved = ug.snapshot()
    (tmp_path / "profiles.json").write_bytes(b"what the run wrote")
    (tmp_path / "board_layout.json").write_bytes(b"created by the run")
    ug.restore(saved)

    assert (tmp_path / "profiles.json").read_bytes() == b"user edits, never committed"
    assert not (tmp_path / "board_layout.json").exists()
    assert not (tmp_path / "settings.json").exists()
