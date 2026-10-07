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
from gui.user_profile_window import UserProfileWindow
from gui.users_window import UsersWindow
from models import user_store
from models.types import ModelId


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


# -- opening a user opens its profile screen --------------------------------------------


def _open(win, user, draft=False):
    """Open *user* the way the Users window does, and return the profile screen."""
    win.windows.ensure("users")
    users_window = win.windows.get("users")
    assert isinstance(users_window, UsersWindow)
    users_window.open_requested.emit(user, draft)
    screens = win.findChildren(UserProfileWindow)
    return next(s for s in screens if s.user.id == user.id)


def test_opening_a_saved_user_shows_its_profile_screen(win):
    user = win.state.users[0]
    screen = _open(win, user)
    assert screen.user.name == user.name
    assert screen.isVisible()
    assert not screen.is_dirty
    screen.close()


def test_opening_the_same_user_again_shows_the_same_screen(win):
    user = win.state.users[0]
    first = _open(win, user)
    assert _open(win, user) is first
    assert len([s for s in win.findChildren(UserProfileWindow) if s.user.id == user.id]) == 1
    first.close()


def test_a_draft_is_added_only_when_the_screen_saves_it(win):
    draft = user_store.new_user("From the board")
    n = len(win.state.users)
    screen = _open(win, draft, draft=True)
    assert len(win.state.users) == n  # opening a draft saves nothing
    assert screen.save() is True
    assert win.state.users[-1].name == "From the board"
    assert "From the board" in [u.name for u in user_store.load()]  # on disk
    screen.close()


def test_saving_refreshes_the_users_list_and_says_so(win):
    win.windows.ensure("users")
    users_window = win.windows.get("users")
    assert isinstance(users_window, UsersWindow)
    screen = _open(win, win.state.users[0])
    screen.profile_page.name_edit.setText("Renamed")
    screen.profile_page.name_edit.textEdited.emit("Renamed")
    assert screen.save() is True
    assert "Renamed" in _texts(users_window)
    assert "Renamed" in win.statusBar().currentMessage()
    screen.close()


def test_closing_the_app_asks_about_an_unsaved_profile_screen_and_cancel_keeps_it_open(win):
    screen = _open(win, win.state.users[0])
    asked = []
    screen._ask_unsaved = lambda: asked.append(1)  # Cancel
    screen.profile_page.weight_spin.setValue(99.0)
    assert win.close() is False
    assert asked == [1]
    screen._ask_unsaved = lambda: "discard"
    assert win.close() is True


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
