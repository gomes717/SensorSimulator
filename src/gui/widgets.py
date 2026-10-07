"""Small shared widget subclasses.

:class:`NoWheelSpinBox` / :class:`NoWheelDoubleSpinBox` are the spin boxes every
parameter form uses. Qt's stock ones edit their value on a mouse wheel whenever
the pointer is over them — inside the scrolling parameter lists that means
scrolling past a model's parameters silently rewrites whichever one happened to
be under the cursor, with nothing to show it happened.
"""

from __future__ import annotations

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QFocusEvent, QWheelEvent
from PyQt6.QtWidgets import (
    QApplication,
    QDoubleSpinBox,
    QLabel,
    QSizePolicy,
    QSpinBox,
    QWidget,
)


def wrapped_label(text: str = "", *, muted: bool = False) -> QLabel:
    """A word-wrapping QLabel that is actually given room for every line.

    ``setWordWrap(True)`` alone is not enough: a layout asks the label for one
    height without knowing the width it will end up with, so a sentence that
    wraps to three lines gets the height of one or two and the tail is clipped.
    Turning on heightForWidth makes the layout re-ask once the width is known.
    """
    label = QLabel(text)
    label.setWordWrap(True)
    policy = label.sizePolicy()
    policy.setHeightForWidth(True)
    policy.setVerticalPolicy(QSizePolicy.Policy.MinimumExpanding)
    label.setSizePolicy(policy)
    if muted:
        label.setEnabled(False)
    return label


class _NoWheelMixin:
    """Ignore wheel events so they bubble up to the enclosing scroll area.

    ``ignore()`` (rather than swallowing the event) is what lets the parameter
    list still scroll normally with the pointer over a field.
    """

    def wheelEvent(self, event: QWheelEvent) -> None:  # Qt naming
        event.ignore()


class NoWheelSpinBox(_NoWheelMixin, QSpinBox):
    """A QSpinBox whose value the mouse wheel cannot change."""


class NoWheelDoubleSpinBox(_NoWheelMixin, QDoubleSpinBox):
    """A QDoubleSpinBox whose value the mouse wheel cannot change."""


class OptionalDoubleSpinBox(NoWheelDoubleSpinBox):
    """A spin box whose minimum shows a word ("not set") instead of a number.

    A click puts the cursor *inside* that word, and the digits typed there are rejected — you
    had to step the value up once before you could type. Selecting the text when the box gets
    focus (after the click has placed its cursor) makes typing replace it.
    """

    def focusInEvent(self, event: QFocusEvent | None) -> None:  # Qt naming
        super().focusInEvent(event)
        QTimer.singleShot(0, self.selectAll)


def fit_to_screen(window: QWidget, width: int, height: int, share: float = 0.9) -> None:
    """Resize *window* to *width* x *height*, but never beyond *share* of the screen it is on, so a
    window that was comfortable on a big monitor is not taller than a laptop's screen."""
    screen = window.screen() or QApplication.primaryScreen()
    if screen is None:
        window.resize(width, height)
        return
    area = screen.availableGeometry()
    window.resize(min(width, int(area.width() * share)), min(height, int(area.height() * share)))
