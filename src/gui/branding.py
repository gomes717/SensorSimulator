"""The application's name and icon.

The icon ships in two inks (``src/assets/icons/``, made by ``scripts/make_app_icon.py``): a dark
one for light title bars and taskbars, a light one for dark ones. The title bar and the taskbar
follow the *operating system's* colour scheme, not the in-app Theme setting, so that is what picks
the variant — and it follows the OS when the user switches while the app is open.
"""

from __future__ import annotations

import ctypes
import sys
from functools import cache
from pathlib import Path

from PyQt6.QtCore import QEvent, QRectF, Qt, QTimer
from PyQt6.QtGui import QGuiApplication, QIcon, QPainter, QPaintEvent, QPixmap
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QWidget

APP_NAME = "GlucoEcho"
# The Windows taskbar groups windows (and picks their icon) by this id; without one it
# shows python.exe's.
APP_USER_MODEL_ID = "GlucoEcho.GlucoEcho"

ICON_DIR = Path(__file__).resolve().parent.parent / "assets" / "icons"
_ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)


@cache
def _renderer_for(dark: bool) -> QSvgRenderer:
    """The vector icon for a dark (*dark* True: light ink) or light (dark ink) background."""
    return QSvgRenderer(str(ICON_DIR / f"glucoecho_{'dark' if dark else 'light'}.svg"))


@cache
def icon_for(dark: bool) -> QIcon:
    """The app icon for a dark (*dark* True: light ink) or light (dark ink) title bar."""
    renderer = _renderer_for(dark)
    icon = QIcon()
    for size in _ICON_SIZES:
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter, QRectF(0, 0, size, size))
        painter.end()
        icon.addPixmap(pixmap)
    return icon


def system_is_dark(app: QGuiApplication) -> bool:
    """Whether the OS is in dark mode (the window background decides when it won't say)."""
    hints = app.styleHints()
    scheme = hints.colorScheme() if hints is not None else Qt.ColorScheme.Unknown
    if scheme == Qt.ColorScheme.Unknown:
        return app.palette().window().color().lightness() < 128
    return scheme == Qt.ColorScheme.Dark


def install(app: QGuiApplication) -> None:
    """Name the application, give it the icon for the current OS colour scheme, and re-pick
    the icon whenever that scheme changes."""
    app.setApplicationName(APP_NAME)
    if sys.platform == "win32":
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)

    def refresh() -> None:
        app.setWindowIcon(icon_for(system_is_dark(app)))

    refresh()
    # Deferred: the palette (the fallback above) is updated just after the signal.
    hints = app.styleHints()
    if hints is not None:
        hints.colorSchemeChanged.connect(lambda _scheme: QTimer.singleShot(0, refresh))


class LogoWidget(QWidget):
    """The icon drawn faded inside a window (the start screen). Unlike the title-bar icon it sits
    on the app's own background, so its ink follows the in-app Theme, not the OS."""

    def __init__(self, size: int = 112, opacity: float = 0.3) -> None:
        super().__init__()
        self._size = size
        self._opacity = opacity
        self.setFixedSize(size, size)

    def paintEvent(self, event: QPaintEvent) -> None:  # Qt naming
        """Draw the icon whose ink contrasts with the current window colour."""
        dark = self.palette().window().color().lightness() < 128
        # Straight from the SVG at whatever size the screen really draws it: the icon's pixmaps
        # stop at 256 px and would be scaled (soft, jagged) on a larger or scaled display.
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setOpacity(self._opacity)
        _renderer_for(dark).render(painter, QRectF(0, 0, self._size, self._size))
        painter.end()

    def changeEvent(self, event: QEvent | None) -> None:  # Qt naming
        """Repaint when the theme switches the palette."""
        if event is not None and event.type() == QEvent.Type.PaletteChange:
            self.update()
        super().changeEvent(event)
