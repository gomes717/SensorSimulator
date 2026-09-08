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
from models.types import ModelId, PersonProfile, SensorId, SensorProfile

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


def test_profiles_changed_populates_and_defaults_to_first():
    alice, bob = _person("Alice"), _person("Bob")
    c, _st, seen = _wire(
        persons=[alice, bob], sensors=[SensorProfile(name="S1", sensor_id=SensorId.IDEAL)]
    )
    win = ConfigurationWindow(c)

    c.notify_profiles_changed()

    # combo has (none) + 2 people, first real profile auto-selected, app told once
    assert win.person_combo.count() == 3
    assert win.person_combo.currentData() is alice
    assert seen["person"] == [alice]


def test_user_combo_change_emits_selection():
    alice, bob = _person("Alice"), _person("Bob")
    c, st, seen = _wire(persons=[alice, bob])
    win = ConfigurationWindow(c)
    c.notify_profiles_changed()
    st.active_person = alice
    seen["person"].clear()

    win.person_combo.setCurrentIndex(2)  # Bob

    assert seen["person"] == [bob]


def test_repopulate_keeps_selection_and_stays_silent():
    alice, bob = _person("Alice"), _person("Bob")
    c, st, seen = _wire(persons=[alice, bob])
    win = ConfigurationWindow(c)
    c.notify_profiles_changed()
    st.active_person = alice
    seen["person"].clear()

    c.notify_profiles_changed()  # e.g. a profile edit elsewhere

    assert win.person_combo.currentData() is alice
    assert seen["person"] == []  # selection unchanged -> no echo


def test_mode_toggles_and_editor_buttons_reach_the_app():
    c, _st, seen = _wire()
    win = ConfigurationWindow(c)

    win.model_only_check.setChecked(True)
    win.cgms_only_check.setChecked(True)
    win.comm_profile_combo.setCurrentIndex(1)
    win.person_configure_btn.click()
    win.board_layout_btn.click()

    assert seen["model"] == [True]
    assert seen["cgms"] == [True]
    assert seen["comm"] == [True]
    assert seen["editor"] == ["person", "board_layout"]


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
