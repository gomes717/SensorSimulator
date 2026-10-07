"""A user's profile picture: stored as a square PNG in the user's folder, shown from there (or
as the generated initials disc when there is none)."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QImage, QPixmap

from gui.avatar import avatar_icon
from models import user_store
from models.types import User

PICTURE_NAME = "picture.png"
STORED_SIZE = 256  # pixels per side


def store_picture(dest_dir: Path, source: Path) -> str:
    """Copy *source* into *dest_dir* as a centred square PNG; returns the file name.
    Raises ValueError if *source* is not an image Qt can read."""
    image = QImage(str(source))
    if image.isNull():
        raise ValueError(f"{source.name} is not a picture that can be read")
    side = min(image.width(), image.height())
    square = image.copy((image.width() - side) // 2, (image.height() - side) // 2, side, side)
    square = square.scaled(
        STORED_SIZE,
        STORED_SIZE,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    dest_dir.mkdir(parents=True, exist_ok=True)
    if not square.save(str(dest_dir / PICTURE_NAME), "PNG"):
        raise ValueError("the picture could not be saved")
    return PICTURE_NAME


def stored_pixmap(user: User, size: int) -> QPixmap | None:
    """The user's stored picture at *size* pixels, or None when it has none (or the file is gone)."""
    if not user.picture:
        return None
    pixmap = QPixmap(str(user_store.user_dir(user) / user.picture))
    if pixmap.isNull():
        return None
    return pixmap.scaled(
        QSize(size, size),
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )


def pixmap_for(user: User, size: int, pending: Path | None = None) -> QPixmap:
    """The picture to show for *user* at *size* pixels: the *pending* (chosen, unsaved) file, else
    its stored picture, else the initials disc."""
    candidates = [pending] if pending is not None else []
    if user.picture:
        candidates.append(user_store.user_dir(user) / user.picture)
    for path in candidates:
        pixmap = QPixmap(str(path))
        if not pixmap.isNull():
            return pixmap.scaled(
                QSize(size, size),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
    return avatar_icon(user.id, user.name, size).pixmap(size, size)
