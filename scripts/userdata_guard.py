"""Protect the app's data/ files while a harness drives the real app.

The harnesses push profiles, assignments and settings through the real GUI, and
the GUI persists all of it into data/. They used to put data/ back with
`git checkout`, which also threw away the user's own uncommitted edits to those
files. This snapshots the bytes before the run and puts exactly those back
afterwards (deleting a file the run created that did not exist before).
"""

from __future__ import annotations

from pathlib import Path

_DATA = Path(__file__).resolve().parent.parent / "data"
_FILES = ("profiles.json", "settings.json", "board_layout.json")


def snapshot() -> dict[str, bytes | None]:
    """Current content of each guarded file; None when it does not exist."""
    return {
        name: (_DATA / name).read_bytes() if (_DATA / name).exists() else None for name in _FILES
    }


def restore(snap: dict[str, bytes | None]) -> None:
    for name, content in snap.items():
        path = _DATA / name
        if content is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(content)
