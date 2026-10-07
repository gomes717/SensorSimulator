"""Users-screen slice 4: the Users window — the list, + New, Delete, and + Read from… with its
three outcomes (gui/users_window.py). The reader is faked; the board is not involved."""

import os
import struct
import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication

from gui.users_window import UsersWindow
from models import user_board, user_store
from models.types import CsvTrack, ExerciseEvent, FoodEvent, ModelId, SensorId


@pytest.fixture(scope="module", autouse=True)
def _app():
    return QApplication.instance() or QApplication([])


def _f32(x: float) -> float:
    return struct.unpack("<f", struct.pack("<f", x))[0]


def _reading(**changes) -> user_board.BoardReading:
    base = user_board.BoardReading(
        name="Ana",
        is_csv=False,
        model_id=ModelId.UVA_PADOVA,
        model_params={"BW": _f32(82.5)},
        sensor_id=SensorId.IDEAL,
        sensor_params={},
        food_events=[FoodEvent(480, _f32(60.0), 20)],
        exercise_events=[ExerciseEvent(1080, 45, _f32(70.0))],
    )
    return replace(base, **changes)


class _Session:
    def __init__(self, label="Nordic Glucose Sensor 2", slot_index=1):
        self.user_id = label
        self.slot_index = slot_index
        self.is_live = True


class _Reader:
    """Plays the board: read() answers with whatever the test set (or waits)."""

    def __init__(self):
        self.reply: tuple | None = None  # (reading, error); None = never answers
        self.busy = False
        self.reads: list = []
        self.names: list[tuple] = []
        self._pending: Callable | None = None

    def read(self, session, on_done):
        self.reads.append(session)
        if self.reply is None:
            self._pending = on_done
            self.busy = True
            return True
        on_done(*self.reply)
        return True

    def answer(self, reading, error=""):
        self.busy = False
        cb, self._pending = self._pending, None
        assert cb is not None
        cb(reading, error)

    def write_name(self, session, slot, name):
        self.names.append((session, slot, name))


class _Env:
    def __init__(self, users=None, sessions=(), busy=False, choice=None, confirm=True):
        self.users = users if users is not None else []
        self.saves = 0
        self.reader = _Reader()
        self.sessions = list(sessions)
        self.board_busy = busy
        self.questions: list = []
        self.choice = choice
        self.confirm = confirm
        self.opened: list[tuple] = []
        self.win = UsersWindow(
            self.users,
            save=self._save,
            live_sessions=lambda: self.sessions,
            reader=self.reader,
            board_busy=lambda: self.board_busy,
            ask_differs=self._ask,
            confirm_delete=lambda user: self.confirm,
        )
        self.win.open_requested.connect(lambda u, draft: self.opened.append((u, draft)))

    def _save(self):
        self.saves += 1

    def _ask(self, outcome):
        self.questions.append(outcome)
        return self.choice

    def names(self):
        items = [self.win.list.item(i) for i in range(self.win.list.count())]
        return [item.text() for item in items if item is not None]


# -- the list -----------------------------------------------------------------


def test_the_list_shows_every_user_by_name():
    env = _Env([user_store.new_user("Ana"), user_store.new_user("Bo")])
    assert env.names() == ["Ana", "Bo"]


def test_new_adds_a_saved_user_and_opens_it():
    env = _Env([user_store.new_user("Ana")])
    env.win.new_user()
    assert [u.name for u in env.users] == ["Ana", "New user"]
    assert env.saves == 1
    assert env.names() == ["Ana", "New user"]
    assert env.opened == [(env.users[1], False)]


def test_new_picks_a_free_name():
    env = _Env([user_store.new_user("New user")])
    env.win.new_user()
    assert env.users[1].name == "New user#2"


def test_open_opens_the_selected_user():
    env = _Env([user_store.new_user("Ana"), user_store.new_user("Bo")])
    env.win.list.setCurrentRow(1)
    env.win.open_selected()
    assert env.opened == [(env.users[1], False)]


def test_open_with_nothing_selected_does_nothing():
    env = _Env([user_store.new_user("Ana")])
    env.win.list.setCurrentRow(-1)
    env.win.open_selected()
    assert env.opened == []


def test_delete_removes_the_user_and_its_folder_after_confirming():
    gone = user_store.new_user("Gone")
    gone.csv = CsvTrack(samples=[1], interval_s=300, foodlog=[], start_iso=None)
    keep = user_store.new_user("Keep")
    user_store.save([gone, keep])
    env = _Env([gone, keep])
    env.win.list.setCurrentRow(0)
    env.win.delete_selected()
    assert [u.name for u in env.users] == ["Keep"]
    assert env.names() == ["Keep"]
    assert env.saves == 1
    assert not user_store.user_dir(gone).exists()


def test_delete_declined_changes_nothing():
    env = _Env([user_store.new_user("Ana")], confirm=False)
    env.win.list.setCurrentRow(0)
    env.win.delete_selected()
    assert [u.name for u in env.users] == ["Ana"]
    assert env.saves == 0


# -- Read from… ---------------------------------------------------------------


def test_read_from_with_no_sensor_says_to_connect_one():
    env = _Env(sessions=[])
    env.win.pick_session_and_read()
    assert env.reader.reads == []
    assert "connect" in env.win.status.text().lower()


def test_read_from_with_one_live_sensor_reads_it_without_asking():
    session = _Session()
    env = _Env(sessions=[session])
    env.reader.reply = (_reading(), "")
    env.win.pick_session_and_read()
    assert env.reader.reads == [session]


def test_read_from_refuses_while_the_board_is_busy():
    session = _Session()
    env = _Env(sessions=[session], busy=True)
    env.win.read_from(session)
    assert env.reader.reads == []
    assert "busy" in env.win.status.text().lower()


def test_a_failed_read_is_shown_and_opens_nothing():
    session = _Session()
    env = _Env(sessions=[session])
    env.reader.reply = (None, "The board did not answer in time (missing: person).")
    env.win.read_from(session)
    assert "did not answer" in env.win.status.text()
    assert env.opened == []
    assert env.win.read_button.isEnabled()


def test_the_read_button_is_off_while_a_read_runs():
    session = _Session()
    env = _Env(sessions=[session])
    env.win.read_from(session)  # the fake reader waits
    assert not env.win.read_button.isEnabled()
    env.reader.answer(_reading())
    assert env.win.read_button.isEnabled()


def test_an_unknown_board_user_opens_as_an_unsaved_draft_and_is_not_added():
    session = _Session()
    env = _Env([user_store.new_user("Bo")], sessions=[session])
    env.reader.reply = (_reading(name="Ana"), "")
    env.win.read_from(session)
    [(user, is_draft)] = env.opened
    assert is_draft is True
    assert user.name == "Ana"
    assert [u.name for u in env.users] == ["Bo"]  # nothing saved until the profile screen's Save
    assert env.saves == 0


def test_a_matching_board_user_opens_the_saved_one():
    saved = replace(user_board.user_from_reading(_reading()), id="saved-id")
    env = _Env([saved], sessions=[_Session()])
    env.reader.reply = (_reading(), "")
    env.win.read_from(env.sessions[0])
    assert env.opened == [(saved, False)]
    assert env.questions == []  # nothing to ask


def _differing_setup(choice):
    saved = replace(
        user_board.user_from_reading(_reading()), id="saved-id", height_cm=171.0, picture="p.png"
    )
    session = _Session(slot_index=2)
    env = _Env([saved], sessions=[session], choice=choice)
    env.reader.reply = (_reading(food_events=[]), "")
    env.win.read_from(session)
    return env, saved, session


def test_a_differing_board_user_asks_what_to_do():
    env, saved, _ = _differing_setup(choice=None)
    [outcome] = env.questions
    assert isinstance(outcome, user_board.Differs)
    assert outcome.saved is saved
    assert env.opened == [] and env.saves == 0  # cancelled: nothing changed
    assert env.users == [saved] and saved.food_events != []


def test_overwrite_replaces_the_saved_user_with_the_boards_content():
    env, _, _ = _differing_setup(choice="overwrite")
    assert len(env.users) == 1
    [user] = env.users
    assert (user.id, user.height_cm, user.picture) == ("saved-id", 171.0, "p.png")
    assert user.food_events == []
    assert env.saves == 1
    assert env.opened == [(user, False)]
    assert env.reader.names == []  # the board already has this name


def test_create_adds_a_numbered_copy_and_tells_the_board_its_new_name():
    env, saved, session = _differing_setup(choice="create")
    assert [u.name for u in env.users] == ["Ana", "Ana#2"]
    assert saved.food_events != []  # the saved one is untouched
    copy = env.users[1]
    assert copy.food_events == []
    assert env.saves == 1
    assert env.reader.names == [(session, 2, "Ana#2")]  # the slot that was read
    assert env.opened == [(copy, False)]
    assert env.names() == ["Ana", "Ana#2"]
