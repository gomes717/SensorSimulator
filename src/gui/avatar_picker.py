"""Choosing a profile picture from the avatars that ship with the app.

The avatars are the images in ``data/profile/``: a gallery of tiles, pick one and it becomes the
user's picture (stored in the user's own folder on Save, like any picture — see
:mod:`gui.user_picture`). No hunting for images on the computer.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QListView,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gui.widgets import wrapped_label

AVATAR_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "profile"
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"}
_TILE = 96


def avatar_files(directory: Path = AVATAR_DIR) -> list[Path]:
    """The avatar images in *directory*, by name (none if the folder is missing)."""
    if not directory.is_dir():
        return []
    return sorted(
        (p for p in directory.iterdir() if p.suffix.lower() in _IMAGE_SUFFIXES),
        key=lambda p: p.name.lower(),
    )


class AvatarDialog(QDialog):
    """A gallery of avatars; :attr:`selected` is the chosen file after OK / a double-click."""

    def __init__(self, directory: Path = AVATAR_DIR, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Choose an avatar")
        self.selected: Path | None = None
        self._files = avatar_files(directory)

        layout = QVBoxLayout(self)
        self.list = QListWidget()
        self.list.setViewMode(QListView.ViewMode.IconMode)
        self.list.setIconSize(QSize(_TILE, _TILE))
        self.list.setGridSize(QSize(_TILE + 24, _TILE + 24))
        self.list.setResizeMode(QListView.ResizeMode.Adjust)
        self.list.setMovement(QListView.Movement.Static)
        self.list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        for path in self._files:
            item = QListWidgetItem(QIcon(str(path)), "")
            item.setToolTip(path.stem)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.list.addItem(item)
        self.list.itemDoubleClicked.connect(lambda _item: self.accept())
        layout.addWidget(self.list, 1)

        self.note = wrapped_label(
            "" if self._files else f"No avatar images found in {directory}.", muted=True
        )
        layout.addWidget(self.note)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(4 * (_TILE + 30), 3 * (_TILE + 40))

    def accept(self) -> None:
        """Take the highlighted tile (nothing highlighted: nothing chosen)."""
        row = self.list.currentRow()
        self.selected = self._files[row] if 0 <= row < len(self._files) else None
        super().accept()


def choose_avatar(directory: Path = AVATAR_DIR) -> Path | None:
    """Show the gallery; the chosen avatar file, or None if cancelled."""
    dialog = AvatarDialog(directory)
    return dialog.selected if dialog.exec() == QDialog.DialogCode.Accepted else None
