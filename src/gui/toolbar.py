"""The main window's top toolbar."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMainWindow, QPushButton, QSizePolicy, QToolBar, QWidget

ToolbarItem = tuple[str, str, Callable[[], None] | None]


def _add_buttons(
    toolbar: QToolBar, items: Sequence[ToolbarItem], buttons: dict[str, QPushButton]
) -> None:
    for name, label, handler in items:
        btn = QPushButton(label)
        if handler is not None:
            btn.clicked.connect(handler)
        toolbar.addWidget(btn)
        buttons[name] = btn


def build_toolbar(
    window: QMainWindow,
    *,
    left: Sequence[ToolbarItem] = (),
    right: Sequence[ToolbarItem] = (),
) -> dict[str, QPushButton]:
    """Add a toolbar to *window*: the *left* buttons first, a stretch, then the
    *right* ones. Each item is ``(name, label, handler)``; a ``None`` handler
    leaves the button unconnected for its owner to wire. Returns the buttons by name."""
    toolbar = QToolBar()
    toolbar.setMovable(False)
    toolbar.setFloatable(False)
    buttons: dict[str, QPushButton] = {}
    _add_buttons(toolbar, left, buttons)
    spacer = QWidget()
    spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    toolbar.addWidget(spacer)
    _add_buttons(toolbar, right, buttons)
    window.addToolBar(Qt.ToolBarArea.TopToolBarArea, toolbar)
    return buttons
