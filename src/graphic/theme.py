"""Apply a light / dark / system palette to the whole Qt application.

"system" restores whatever palette+style the app started with; "light" and
"dark" force a Fusion palette so the look is identical on every OS. Graph
canvases read their colors from ``widget.palette()`` at build time, so call
sites must rebuild / re-theme the matplotlib figures after switching.
"""

from __future__ import annotations

from PyQt6.QtGui import QColor, QPalette

# app instance -> (style object name, original palette) captured on first apply
_ORIGINAL: dict[int, tuple[str, QPalette]] = {}


def _dark_palette() -> QPalette:
    p = QPalette()
    window = QColor(53, 53, 53)
    base = QColor(35, 35, 35)
    text = QColor(220, 220, 220)
    disabled = QColor(127, 127, 127)
    p.setColor(QPalette.ColorRole.Window, window)
    p.setColor(QPalette.ColorRole.WindowText, text)
    p.setColor(QPalette.ColorRole.Base, base)
    p.setColor(QPalette.ColorRole.AlternateBase, window)
    p.setColor(QPalette.ColorRole.ToolTipBase, base)
    p.setColor(QPalette.ColorRole.ToolTipText, text)
    p.setColor(QPalette.ColorRole.Text, text)
    p.setColor(QPalette.ColorRole.Button, window)
    p.setColor(QPalette.ColorRole.ButtonText, text)
    p.setColor(QPalette.ColorRole.BrightText, QColor(255, 80, 80))
    p.setColor(QPalette.ColorRole.Link, QColor(42, 130, 218))
    p.setColor(QPalette.ColorRole.Highlight, QColor(42, 130, 218))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(0, 0, 0))
    for group in (QPalette.ColorGroup.Disabled,):
        p.setColor(group, QPalette.ColorRole.WindowText, disabled)
        p.setColor(group, QPalette.ColorRole.Text, disabled)
        p.setColor(group, QPalette.ColorRole.ButtonText, disabled)
    return p


def _light_palette() -> QPalette:
    p = QPalette()
    window = QColor(240, 240, 240)
    base = QColor(255, 255, 255)
    text = QColor(20, 20, 20)
    disabled = QColor(160, 160, 160)
    p.setColor(QPalette.ColorRole.Window, window)
    p.setColor(QPalette.ColorRole.WindowText, text)
    p.setColor(QPalette.ColorRole.Base, base)
    p.setColor(QPalette.ColorRole.AlternateBase, window)
    p.setColor(QPalette.ColorRole.ToolTipBase, base)
    p.setColor(QPalette.ColorRole.ToolTipText, text)
    p.setColor(QPalette.ColorRole.Text, text)
    p.setColor(QPalette.ColorRole.Button, window)
    p.setColor(QPalette.ColorRole.ButtonText, text)
    p.setColor(QPalette.ColorRole.BrightText, QColor(200, 0, 0))
    p.setColor(QPalette.ColorRole.Link, QColor(0, 90, 200))
    p.setColor(QPalette.ColorRole.Highlight, QColor(0, 120, 215))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    for group in (QPalette.ColorGroup.Disabled,):
        p.setColor(group, QPalette.ColorRole.WindowText, disabled)
        p.setColor(group, QPalette.ColorRole.Text, disabled)
        p.setColor(group, QPalette.ColorRole.ButtonText, disabled)
    return p


def apply_theme(app, mode: str) -> None:
    """Set the application palette for *mode* ("system" | "light" | "dark")."""
    key = id(app)
    if key not in _ORIGINAL:
        _ORIGINAL[key] = (app.style().objectName(), QPalette(app.palette()))

    if mode == "dark":
        app.setStyle("Fusion")
        app.setPalette(_dark_palette())
    elif mode == "light":
        app.setStyle("Fusion")
        app.setPalette(_light_palette())
    else:  # system
        _orig_style, orig_palette = _ORIGINAL[key]
        app.setPalette(orig_palette)
