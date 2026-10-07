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
from models import board_layout, user_store
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
    # each entry starts with the user's name (and may say which sensor it is on)
    assert [text.split("  ·  ")[0] for text in _texts(window)] == [u.name for u in win.state.users]
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
    screen._dialogs.ask_unsaved = lambda: asked.append(1)  # Cancel
    screen.profile_page.weight_spin.setValue(99.0)
    assert win.close() is False
    assert asked == [1]
    screen._dialogs.ask_unsaved = lambda: "discard"
    assert win.close() is True


# -- sharing the Sensor-select cursor with BoardMode --------------------------------


class _Signal:
    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)

    def disconnect(self, slot):
        self.slots.remove(slot)

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

    def relabel(self):
        pass

    def display_name(self, address):
        return address

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


# -- Send to… through the main window ------------------------------------


class _SendSession(_LiveSession):
    """A live sensor that records what Send pushed and lets the test play the board's answer."""

    def __init__(self, slot_index):
        super().__init__(slot_index)
        self.board_layout_finished = _Signal()
        self.pushes: list = []

    def exposes(self, key):
        return key == "user_name"

    def send_board_layout(self, entries, *, run=True):
        self.pushes.append((entries, run))

    def finish(self, ok=True, message="done"):
        self.board_layout_finished.emit("a", ok, message)


def _screen_with_sensor(win, slot):
    """An open profile screen for a model user of our own (not whatever is saved on this machine)
    and a live fake sensor, with an empty slot record."""
    win.state.board_layout = board_layout.BoardLayout()
    user = user_store.new_user("Sender")
    win.state.users.append(user)
    session = _SendSession(slot)
    win.windows.bluetooth = _Bluetooth({"a": session})
    screen = _open(win, user)
    screen._dialogs.choose_sensor = lambda sessions, _describe: sessions[0]
    return screen, session, user


def test_the_profile_screen_has_a_send_button_in_the_real_app(win):
    screen = _open(win, win.state.users[0])
    assert not screen.send_button.isHidden()
    screen.close()


def test_sending_pushes_the_user_to_the_chosen_sensor_and_records_the_slot(win):
    screen, session, user = _screen_with_sensor(win, slot=1)
    screen.send()
    [(entries, run)] = session.pushes
    assert [e["slot"] for e in entries] == [1] and run is True
    assert win.state.board_layout.slots[1].person is None  # nothing is recorded before it lands
    session.finish(True)
    assert win.state.board_layout.slots[1].person == user.name
    assert f'Sent "{user.name}" to sensor 2' in win.statusBar().currentMessage()
    screen.close()


def test_a_failed_send_records_nothing(win):
    screen, session, _user = _screen_with_sensor(win, slot=1)
    screen.send()
    session.finish(False, "the board said no")
    assert win.state.board_layout.slots[1].person is None
    assert "the board said no" in screen.error.text()
    screen.close()


def test_the_chooser_tells_what_each_sensor_is_running_now(win):
    win.state.board_layout = board_layout.BoardLayout()
    win.state.board_layout.slots[2].person = "Someone"
    assert "Someone" in win._describe_sensor(_LiveSession(2))
    assert "no user" in win._describe_sensor(_LiveSession(0))
    assert "Sensor 3" in win._describe_sensor(_LiveSession(2))
