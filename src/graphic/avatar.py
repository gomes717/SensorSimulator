"""Runtime-generated avatar icons for the user tree — a colored disc with initials.

Deterministic per *seed* (the user id), so the same user always gets the same
color across reconnects. No image assets.
"""
from __future__ import annotations

import hashlib

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QIcon, QPainter, QPixmap

_CACHE: dict[tuple[str, str, int], QIcon] = {}


def _initials(label: str) -> str:
    parts = [p for p in label.replace("·", " ").split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[1][0]).upper()


def avatar_icon(seed: str, label: str, size: int = 22) -> QIcon:
    """Return a cached QIcon: a filled circle (hue from *seed*) with *label*'s initials."""
    key = (seed, label, size)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached

    digest = int(hashlib.md5(seed.encode("utf-8")).hexdigest(), 16)
    hue = digest % 360
    bg = QColor.fromHsv(hue, 160, 200)
    fg = QColor.fromHsv(hue, 60, 60) if bg.lightnessF() > 0.6 else QColor("white")

    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(bg))
    painter.drawEllipse(QRectF(0.5, 0.5, size - 1.0, size - 1.0))

    font = QFont()
    font.setPixelSize(int(size * 0.44))
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(fg)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, _initials(label))
    painter.end()

    icon = QIcon(pixmap)
    _CACHE[key] = icon
    return icon
