"""Food / Exercise editors follow the TARGET device's patient, and are locked only when
that sensor replays a CSV — not because the globally active patient happens to."""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication

from models import board_layout as bl
from models.types import FoodEvent, ModelId, PersonProfile


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class _Signal:
    def connect(self, _slot):
        pass

    def disconnect(self, _slot):
        pass


class _Session:
    def __init__(self, slot_index, user_id):
        self.slot_index = slot_index
        self.user_id = user_id
        self.is_live = True
        self.config_read = _Signal()
        self.reset_sync = _Signal()

    def queue_write(self, *_a):
        pass

    def request_read(self, _key):
        pass

    def notifies(self, _uuid):
        return True


class _Bt:
    def __init__(self, sessions):
        self._sessions = {f"a{i}": s for i, s in enumerate(sessions)}

    def sessions(self):
        return self._sessions

    def display_name(self, address):
        return address

    def stop_all_sessions(self):
        pass

    def close(self):
        pass


@pytest.fixture
def win(app):
    import gui.main_window as mw

    w = mw.MainWindow()
    csv_pt = PersonProfile(name="Sample Patient", model_id=ModelId.CAMBRIDGE, data_source="csv")
    model_pt = PersonProfile(
        name="test2",
        model_id=ModelId.CAMBRIDGE,
        food_events=[FoodEvent(time_of_day_min=480, carbs_g=40.0, duration_min=30)],
    )
    w.state.person_profiles[:] = [csv_pt, model_pt]
    w.state.active_person = csv_pt  # the globally active patient is the CSV one
    w.state.board_layout = bl.BoardLayout(
        [
            bl.SlotAssignment(person="test2"),
            bl.SlotAssignment(person="Sample Patient"),
            bl.SlotAssignment(),
            bl.SlotAssignment(),
        ]
    )
    w.windows.bluetooth = _Bt([_Session(0, "S1"), _Session(1, "S2")])
    yield w
    w.windows.bluetooth = None
    w.close()


def test_the_editor_works_on_the_target_devices_patient(win):
    food = win.windows.ensure("food")
    food._target_bar.combo.setCurrentIndex(0)  # device on slot 0 -> test2
    food.refresh()
    assert "test2" in food._active_label.text()
    assert "CSV" not in food._active_label.text()
    assert food._add_btn.isEnabled()
    assert len(food._events) == 1  # test2's meal, not the CSV patient's


def test_a_csv_active_patient_does_not_lock_a_model_sensor(win):
    """The reported bug: 'Sample Patient replays a recorded CSV' on a sensor that runs a model."""
    win.windows.ensure("exercise")._target_bar.combo.setCurrentIndex(0)
    exercise = win.windows.get("exercise")
    exercise.refresh()
    assert exercise._add_btn.isEnabled()
    assert "replays a recorded CSV" not in exercise._active_label.text()


def test_the_board_saying_csv_locks_that_sensors_editor(win):
    win._board_mode._is_csv[1] = True
    food = win.windows.ensure("food")
    food._target_bar.combo.setCurrentIndex(1)  # device on slot 1 -> Sample Patient
    food.refresh()
    assert not food._add_btn.isEnabled()
    assert "CSV" in food._active_label.text()


def test_the_board_saying_model_unlocks_a_patient_saved_as_csv(win):
    win._board_mode._model[1] = "Cambridge (Hovorka)"  # the board runs a model there
    food = win.windows.ensure("food")
    food._target_bar.combo.setCurrentIndex(1)
    food.refresh()
    assert food._add_btn.isEnabled()


def test_switching_the_target_device_switches_the_patient(win):
    win._board_mode._is_csv[1] = True
    food = win.windows.ensure("food")
    food._target_bar.combo.setCurrentIndex(1)
    assert not food._add_btn.isEnabled()
    food._target_bar.combo.setCurrentIndex(0)  # no manual refresh: the change signal does it
    assert food._add_btn.isEnabled() and "test2" in food._active_label.text()


def test_a_slot_with_no_record_falls_back_to_the_active_patient(win):
    assert win._person_for_slot(2) is win.state.active_person
    assert win._person_for_slot(None) is win.state.active_person
