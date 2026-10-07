"""The app's secondary windows: lazy creation, raising, and closing.

Pulled out of :class:`MainWindow`, which used to hold eight ``_<name>_window``
attributes, a ``_lazy_window``/``_raise`` pair, nine ``_open_*`` methods and the
close-everything loop in ``closeEvent``. Everything that is only about *which
window exists and is it showing* lives here; what a window needs from the app
(profiles, callbacks) is handed in once at construction.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PyQt6.QtWidgets import QWidget

from core.ble_message_log import BleMessageLog
from gui.bluetooth_window import BluetoothWindow
from gui.debug_window import DebugWindow
from gui.exercise_config_window import ExerciseConfigWindow
from gui.food_config_window import FoodConfigWindow
from gui.person_config_window import PersonConfigWindow
from gui.sensor_config_window import SensorConfigWindow
from models.types import PersonProfile


@dataclass
class WindowDeps:
    """What the secondary windows need from the app, handed over once."""

    ble_log: BleMessageLog
    person_profiles: list
    sensor_profiles: list
    person_for_slot: Callable[[int | None], PersonProfile | None]
    is_csv_for_slot: Callable[[int | None, PersonProfile | None], bool]
    on_profiles_changed: Callable[[], None]
    record_slot_assignment: Callable[..., None]
    on_person_selected: Callable[[PersonProfile | None], None]
    on_sensor_selected: Callable
    on_bluetooth_created: Callable[[BluetoothWindow], None]


class ChildWindows:
    """Owns every secondary window; builds each one the first time it is asked for."""

    def __init__(self, deps: WindowDeps) -> None:
        self._windows: dict[str, QWidget | None] = {}
        self._on_bluetooth_created = deps.on_bluetooth_created
        d = deps
        self._factories: dict[str, Callable[[], QWidget]] = {
            "debug": lambda: DebugWindow(d.ble_log),
            "bluetooth": lambda: BluetoothWindow(d.ble_log),
            "person": lambda: PersonConfigWindow(
                d.person_profiles,
                d.on_profiles_changed,
                self.ensure_bluetooth,
                d.record_slot_assignment,
                d.on_person_selected,
            ),
            "sensor": lambda: SensorConfigWindow(
                d.sensor_profiles,
                d.on_profiles_changed,
                self.ensure_bluetooth,
                d.record_slot_assignment,
                d.on_sensor_selected,
            ),
            "food": lambda: FoodConfigWindow(
                d.person_for_slot, d.is_csv_for_slot, d.on_profiles_changed, self.ensure_bluetooth
            ),
            "exercise": lambda: ExerciseConfigWindow(
                d.person_for_slot, d.is_csv_for_slot, d.on_profiles_changed, self.ensure_bluetooth
            ),
        }

    # -- access ---------------------------------------------------------

    def get(self, key: str) -> QWidget | None:
        """The window for *key* if it has been created, else None. Never creates one."""
        return self._windows.get(key)

    def register(self, key: str, window: QWidget) -> None:
        """Adopt a window the app builds eagerly (the Configuration window)."""
        self._windows[key] = window

    def ensure(self, key: str) -> QWidget:
        """The window for *key*, built on first access but not shown."""
        win = self._windows.get(key)
        if win is None:
            win = self._factories[key]()
            self._windows[key] = win
            if key == "bluetooth":
                self._on_bluetooth_created(win)
        return win

    def open(self, key: str) -> QWidget:
        """Create-if-needed, show and focus the window for *key*."""
        win = self.ensure(key)
        win.show()
        win.raise_()
        win.activateWindow()
        return win

    # -- the Bluetooth window is special: sessions live in it ------------

    @property
    def bluetooth(self) -> BluetoothWindow | None:
        """The Bluetooth window, or None if it was never opened. Assignable so a
        test can stand a fake in for it."""
        return self._windows.get("bluetooth")

    @bluetooth.setter
    def bluetooth(self, window) -> None:
        self._windows["bluetooth"] = window

    def ensure_bluetooth(self) -> BluetoothWindow:
        """Create the Bluetooth window without showing it (its sessions must exist
        for the config windows' target picker even when it was never opened)."""
        return self.ensure("bluetooth")

    # -- shutdown -------------------------------------------------------

    def close_all(self) -> None:
        """Stop every BLE session, then close every window that was created."""
        bt = self.bluetooth
        if bt is not None:
            bt.stop_all_sessions()
            bt.close()
        for key, window in self._windows.items():
            if key != "bluetooth" and window is not None:
                window.close()
