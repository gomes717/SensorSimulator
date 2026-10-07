"""Small shared widget subclasses.

:class:`NoWheelSpinBox` / :class:`NoWheelDoubleSpinBox` are the spin boxes every
parameter form uses. Qt's stock ones edit their value on a mouse wheel whenever
the pointer is over them — inside the scrolling parameter lists that means
scrolling past a model's parameters silently rewrites whichever one happened to
be under the cursor, with nothing to show it happened.
"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QObject, Qt, QTimer
from PyQt6.QtGui import QFocusEvent, QWheelEvent
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QLabel,
    QSizePolicy,
    QSpinBox,
    QTimeEdit,
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


class NoWheelComboBox(_NoWheelMixin, QComboBox):
    """A QComboBox the wheel cannot change (a two-finger scroll over it would switch the model)."""


class NoWheelTimeEdit(_NoWheelMixin, QTimeEdit):
    """A QTimeEdit the wheel cannot change."""


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


class CenterOnMain(QObject):
    """Opens every other top-level window (windows, dialogs, message boxes) centred on the main
    window, on the screen the main window is on — whatever each one would otherwise choose.

    Installed once on the application. A window is placed each time it is shown, from its own size
    (so a window that was sized to the screen first is centred at that size) and kept on screen.
    """

    def __init__(self, main: QWidget) -> None:
        super().__init__(main)
        self._main = main

    def eventFilter(self, a0: QObject | None, a1: QEvent | None) -> bool:  # Qt naming
        if (
            a1 is not None
            and a1.type() == QEvent.Type.Show
            and isinstance(a0, QWidget)
            and a0.isWindow()
            and a0 is not self._main
            and not self._is_transient(a0)
        ):
            self.center(a0)
        return False

    @staticmethod
    def _is_transient(widget: QWidget) -> bool:
        """Menus, tooltips and other popups place themselves."""
        kind = widget.windowType()
        return kind in (Qt.WindowType.Popup, Qt.WindowType.ToolTip, Qt.WindowType.SplashScreen)

    def center(self, window: QWidget) -> None:
        main = self._main
        if not main.isVisible():
            return
        frame = main.frameGeometry()
        x = frame.center().x() - window.width() // 2
        y = frame.center().y() - window.height() // 2
        screen = main.screen()
        if screen is not None:
            area = screen.availableGeometry()
            x = max(area.left(), min(x, area.right() - window.width()))
            y = max(area.top(), min(y, area.bottom() - window.height()))
        window.move(x, y)
