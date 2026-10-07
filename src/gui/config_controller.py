"""The seam between :class:`MainWindow` (owns the simulation + BLE state) and
:class:`ConfigurationWindow` (owns the mode / appearance / threshold widgets).

Before this, ``ConfigurationWindow`` took the whole ``MainWindow`` and wired
every widget to a ``_``-private handler on it, and ``MainWindow`` poked back
into ``self._cfg.<widget>`` for ~11 widgets — a mutable object graph shared
across two classes with no interface (issue 18).

Now both sides hold a ``ConfigController``. It carries no behaviour: it is a
typed Qt signal surface plus read-only access to the profile lists and the
current selection. Widget events travel app-ward on the ``*_requested`` /
``*_toggled`` / ``*_selected`` signals; state the app changes travels
widget-ward on the ``*_display`` signals so the window
can re-sync its widgets without either side importing the other.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QObject, pyqtSignal


class ConfigController(QObject):
    # -- widget -> app -------------------------------------------------
    speed_change_requested = pyqtSignal(float)
    model_only_toggled = pyqtSignal(bool)
    cgms_only_toggled = pyqtSignal(bool)
    thresholds_saved = pyqtSignal()
    theme_changed = pyqtSignal()  # the palette was switched: rebuild what cached the colors
    view_window_changed = pyqtSignal()  # the graph time-window preference changed

    # -- app -> widget ----------------------------------------------------
    speed_display_changed = pyqtSignal(float)  # move the slider/spin, do not re-emit
    model_only_display_changed = pyqtSignal(bool)
    controls_locked = pyqtSignal(bool)  # CGMS-only: disable every config-sending control

    def __init__(self, speed_mult: Callable[[], float]) -> None:
        super().__init__()
        self._speed_mult = speed_mult

    # Read-only view the window needs while building its widgets.
    @property
    def speed_mult(self) -> float:
        return self._speed_mult()

    # -- called by MainWindow; each just fans a state change out to the window --
    def set_speed_display(self, mult: float) -> None:
        self.speed_display_changed.emit(float(mult))

    def set_model_only_display(self, on: bool) -> None:
        self.model_only_display_changed.emit(bool(on))

    def set_controls_locked(self, locked: bool) -> None:
        self.controls_locked.emit(bool(locked))
