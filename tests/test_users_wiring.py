"""Users-screen slice 4: the Users button, the window it opens, and the interim "open a user"
handler in the main window (gui/main_window.py, gui/user_summary.py)."""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication, QPushButton

from api import protocol
from gui import user_summary
from gui.users_window import UsersWindow
from models import user_store
from models.types import CsvTrack, ExerciseEvent, FoodEvent, ModelId, SensorId


@pytest.fixture(scope="module", autouse=True)
def _app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win():
    from gui.main_window import MainWindow

    w = MainWindow()
    yield w
    w.sim.engines.stop_all()
    w.close()


def _texts(window: UsersWindow) -> list[str]:
    items = [window.list.item(i) for i in range(window.list.count())]
    return [item.text() for item in items if item is not None]


def _buttons(win) -> list[str]:
    return [b.text() for b in win.findChildren(QPushButton)]


# -- the button and the window -------------------------------------------------


def test_the_toolbar_has_users_next_to_configuration_and_debug(win):
    labels = _buttons(win)
    assert {"Users", "Configuration", "Debug"} <= set(labels)
    assert labels.index("Users") < labels.index("Configuration") < labels.index("Debug")


def test_the_users_button_opens_the_users_window_listing_the_users(win):
    users_button = next(b for b in win.findChildren(QPushButton) if b.text() == "Users")
    users_button.click()
    window = win.windows.get("users")
    assert isinstance(window, UsersWindow)
    assert window.isVisible()
    assert _texts(window) == [u.name for u in win.state.users]
    window.close()


def test_the_app_state_has_the_migrated_users(win):
    assert win.state.users  # migrated from the saved profiles on the first run
    assert all(u.id for u in win.state.users)


# -- opening a user (interim, until the profile screen) --------------------------


def test_opening_a_saved_user_shows_it_and_changes_nothing(win, monkeypatch):
    shown = []
    monkeypatch.setattr(user_summary, "show", lambda _p, user, draft: shown.append((user, draft)))
    before = list(win.state.users)
    win._on_user_open_requested(before[0], False)
    assert shown == [(before[0], False)]
    assert win.state.users == before


def test_a_draft_the_person_saves_is_added_and_persisted(win, monkeypatch):
    monkeypatch.setattr(user_summary, "show", lambda *_a: True)
    draft = user_store.new_user("Ana (from the board)")
    n = len(win.state.users)
    win._on_user_open_requested(draft, True)
    assert len(win.state.users) == n + 1
    assert win.state.users[-1].name == "Ana (from the board)"
    assert draft.name in [u.name for u in user_store.load()]  # on disk


def test_a_draft_that_is_not_saved_is_dropped(win, monkeypatch):
    monkeypatch.setattr(user_summary, "show", lambda *_a: False)
    n = len(win.state.users)
    win._on_user_open_requested(user_store.new_user("Ana"), True)
    assert len(win.state.users) == n


def test_a_saved_draft_with_a_taken_name_gets_a_free_one(win, monkeypatch):
    monkeypatch.setattr(user_summary, "show", lambda *_a: True)
    taken = win.state.users[0].name
    win._on_user_open_requested(user_store.new_user(taken), True)
    assert win.state.users[-1].name == f"{taken}#2"


def test_the_window_list_refreshes_when_a_draft_is_saved(win, monkeypatch):
    monkeypatch.setattr(user_summary, "show", lambda *_a: True)
    win.windows.ensure("users")
    window = win.windows.get("users")
    assert isinstance(window, UsersWindow)
    win._on_user_open_requested(user_store.new_user("Fresh"), True)
    assert _texts(window)[-1] == "Fresh"


# -- the summary text ---------------------------------------------------------


def test_a_model_user_summary_names_its_model_sensor_and_schedule():
    user = user_store.new_user("Ana")
    user.model_id = ModelId.UVA_PADOVA
    user.sensor_id = SensorId.BRETON
    user.food_events = [FoodEvent(480, 60.0, 20), FoodEvent(720, 30.0, 15)]
    user.exercise_events = [ExerciseEvent(1080, 45, 70.0)]
    user.height_cm = 171.0
    text = user_summary.summarize(user)
    assert "Name: Ana" in text
    assert "UVA/Padova" in text and "Breton" in text
    assert "Meals: 2" in text and "Exercise: 1" in text
    assert "Height: 171 cm" in text


def test_a_csv_user_summary_says_when_the_window_is_not_available():
    user = user_store.new_user("Csv")
    user.mode = "csv"
    assert "not available" in user_summary.summarize(user)
    user.csv = CsvTrack(samples=[100] * 288, interval_s=300, foodlog=[])
    text = user_summary.summarize(user)
    assert "288 samples (24 h)" in text and "Meals" not in text


# -- sharing the Sensor-select cursor with BoardMode --------------------------------


class _Signal:
    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)

    def emit(self, *args):
        for slot in list(self.slots):
            slot(*args)


class _LiveSession:
    def __init__(self, slot_index):
        self.slot_index = slot_index
        self.user_id = f"Sensor {slot_index + 1}"
        self.is_live = True
        self.config_read = _Signal()
        self.write_failed = _Signal()

    def queue_write(self, _key, _payload):
        pass

    def request_read(self, _key):
        pass


class _Bluetooth:
    def __init__(self, sessions):
        self._sessions = sessions

    def sessions(self):
        return self._sessions

    def stop_all_sessions(self):
        pass

    def close(self):
        pass


def test_board_mode_is_busy_from_a_refresh_until_the_board_answers(win):
    session = _LiveSession(0)
    win.windows.bluetooth = _Bluetooth({"a": session})
    assert not win._board_mode.busy
    win._board_mode.refresh(0)
    assert win._board_mode.busy  # a read is in flight: the cursor is not free
    session.config_read.emit("a", "data_source", protocol.encode_data_source(False))
    session.config_read.emit("a", "person", protocol.encode_person_config(ModelId.CAMBRIDGE, {}))
    assert not win._board_mode.busy
