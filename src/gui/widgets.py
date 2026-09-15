"""Small shared widget subclasses.

:class:`NoWheelSpinBox` / :class:`NoWheelDoubleSpinBox` are the spin boxes every
parameter form uses. Qt's stock ones edit their value on a mouse wheel whenever
the pointer is over them — inside the scrolling parameter lists that means
scrolling past a model's parameters silently rewrites whichever one happened to
be under the cursor, with nothing to show it happened.
"""

from __future__ import annotations

from PyQt6.QtGui import QWheelEvent
from PyQt6.QtWidgets import QDoubleSpinBox, QLabel, QSizePolicy, QSpinBox


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
