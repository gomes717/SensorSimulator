"""Issue 18: ConfigController is the whole seam between MainWindow and
ConfigurationWindow — a typed signal surface, no shared private members."""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication

from gui.config_controller import ConfigController
from gui.configuration_window import ConfigurationWindow
from gui.person_config_window import PersonConfigWindow
from models.types import ModelId, PersonProfile, SensorProfile

_app = QApplication.instance() or QApplication([])


class _State:
    """Stand-in for the slice of MainWindow the controller reads back."""

    def __init__(self, persons, sensors):
        self.persons = persons
        self.sensors = sensors
        self.active_person: PersonProfile | None = None
        self.active_sensor: SensorProfile | None = None
        self.speed = 1.0


def _wire(persons=None, sensors=None):
    persons = persons if persons is not None else []
    sensors = sensors if sensors is not None else []
    st = _State(persons, sensors)
    c = ConfigController(
        persons,
        sensors,
        lambda: st.active_person,
        lambda: st.active_sensor,
        lambda: st.speed,
    )
    seen: dict[str, list] = {
        k: [] for k in ("person", "sensor", "speed", "model", "cgms", "comm", "editor")
    }
    c.person_selected.connect(lambda p: seen["person"].append(p))
    c.sensor_selected.connect(lambda s: seen["sensor"].append(s))
    c.speed_change_requested.connect(lambda v: seen["speed"].append(v))
    c.model_only_toggled.connect(lambda b: seen["model"].append(b))
    c.cgms_only_toggled.connect(lambda b: seen["cgms"].append(b))
    c.comm_profile_toggled.connect(lambda b: seen["comm"].append(b))
    c.editor_requested.connect(lambda name: seen["editor"].append(name))
    return c, st, seen


def _person(name):
    return PersonProfile(name=name, model_id=ModelId.CAMBRIDGE)


def test_person_editor_list_is_what_selects_the_active_patient():
    """The Configuration window has no Person/Sensor combos any more: the
    editors' own lists are where a profile is picked, and they report outward."""
    alice, bob = _person("Alice"), _person("Bob")
    chosen = []

    class _NoBoards:
        def sessions(self):
            return {}

        def display_name(self, address):
            return address

    win = PersonConfigWindow(
        [alice, bob], lambda: None, _NoBoards, on_selected=chosen.append
    )

    win._list.setCurrentRow(1)

    assert chosen[-1] is bob


def test_mode_toggles_and_editor_buttons_reach_the_app():
    c, _st, seen = _wire()
    win = ConfigurationWindow(c)

    win.model_only_check.setChecked(True)
    win.cgms_only_check.setChecked(True)
    win.comm_profile_combo.setCurrentIndex(1)
    win.person_configure_btn.click()
    win.sensor_configure_btn.click()

    assert seen["model"] == [True]
    assert seen["cgms"] == [True]
    assert seen["comm"] == [True]
    assert seen["editor"] == ["person", "sensor"]


def test_speed_display_from_app_does_not_echo():
    c, _st, seen = _wire()
    win = ConfigurationWindow(c)

    c.set_speed_display(60.0)

    assert win.speed_spin.value() == 60
    assert seen["speed"] == []  # app-driven -> no change request back


def test_speed_slider_move_requests_change():
    c, _st, seen = _wire()
    win = ConfigurationWindow(c)

    win.speed_spin.setValue(30)

    assert seen["speed"] == [30.0]


def test_controls_locked_disables_config_sending_widgets():
    c, _st, _seen = _wire()
    win = ConfigurationWindow(c)

    c.set_controls_locked(True)
    assert not win.speed_slider.isEnabled()
    assert not win.model_only_check.isEnabled()
    assert not win.person_configure_btn.isEnabled()

    c.set_controls_locked(False)
    assert win.speed_slider.isEnabled()
