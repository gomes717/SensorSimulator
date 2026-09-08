"""The seam between :class:`MainWindow` (owns the simulation + BLE state) and
:class:`ConfigurationWindow` (owns the selector / mode / threshold widgets).

Before this, ``ConfigurationWindow`` took the whole ``MainWindow`` and wired
every widget to a ``_``-private handler on it, and ``MainWindow`` poked back
into ``self._cfg.<widget>`` for ~11 widgets — a mutable object graph shared
across two classes with no interface (issue 18).

Now both sides hold a ``ConfigController``. It carries no behaviour: it is a
typed Qt signal surface plus read-only access to the profile lists and the
current selection. Widget events travel app-ward on the ``*_requested`` /
``*_toggled`` / ``*_selected`` signals; state the app changes travels
widget-ward on the ``*_display`` / ``profiles_changed`` signals so the window
can re-sync its widgets without either side importing the other.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QObject, pyqtSignal

from models.types import PersonProfile, SensorProfile


class ConfigController(QObject):
    # -- widget -> app -------------------------------------------------
    person_selected = pyqtSignal(object)  # PersonProfile | None
    sensor_selected = pyqtSignal(object)  # SensorProfile | None
    speed_change_requested = pyqtSignal(float)
    model_only_toggled = pyqtSignal(bool)
    cgms_only_toggled = pyqtSignal(bool)
    comm_profile_toggled = pyqtSignal(bool)  # True = Dexcom-style
    editor_requested = pyqtSignal(str)  # "person"|"food"|"exercise"|"sensor"|"board_layout"
    thresholds_saved = pyqtSignal()

    # -- app -> widget ----------------------------------------------------
    profiles_changed = pyqtSignal()  # the profile lists changed: repopulate the combos
    speed_display_changed = pyqtSignal(float)  # move the slider/spin, do not re-emit
    comm_profile_display_changed = pyqtSignal(bool)
    model_only_display_changed = pyqtSignal(bool)
    controls_locked = pyqtSignal(bool)  # CGMS-only: disable every config-sending control

    def __init__(
        self,
        person_profiles: list[PersonProfile],
        sensor_profiles: list[SensorProfile],
        active_person: Callable[[], PersonProfile | None],
        active_sensor: Callable[[], SensorProfile | None],
        speed_mult: Callable[[], float],
    ) -> None:
        super().__init__()
        self.person_profiles = person_profiles
        self.sensor_profiles = sensor_profiles
        self._active_person = active_person
        self._active_sensor = active_sensor
        self._speed_mult = speed_mult

    # Read-only views the window needs while building / repopulating widgets.
    @property
    def active_person(self) -> PersonProfile | None:
        return self._active_person()

    @property
    def active_sensor(self) -> SensorProfile | None:
        return self._active_sensor()

    @property
    def speed_mult(self) -> float:
        return self._speed_mult()

    # -- called by MainWindow; each just fans a state change out to the window --
    def notify_profiles_changed(self) -> None:
        self.profiles_changed.emit()

    def set_speed_display(self, mult: float) -> None:
        self.speed_display_changed.emit(float(mult))

    def set_comm_profile_display(self, dexcom: bool) -> None:
        self.comm_profile_display_changed.emit(bool(dexcom))

    def set_model_only_display(self, on: bool) -> None:
        self.model_only_display_changed.emit(bool(on))

    def set_controls_locked(self, locked: bool) -> None:
        self.controls_locked.emit(bool(locked))
