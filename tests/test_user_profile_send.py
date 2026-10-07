"""Users-screen slice 8 (Send to…): the profile screen's Send button — choosing the sensor, the
save-before-send question, and what is sent (gui/user_profile_window.py)."""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication

from gui.user_profile_window import ProfileDeps, SendDeps, UserProfileWindow
from models import user_store
from models.types import CsvTrack


@pytest.fixture(scope="module", autouse=True)
def _app():
    return QApplication.instance() or QApplication([])


class _Session:
    def __init__(self, slot_index=1, user_id="Nordic Glucose Sensor 2"):
        self.slot_index = slot_index
        self.user_id = user_id


class _Env:
    """A profile screen with a fake sensor list, a fake sender and scripted dialogs."""

    def __init__(
        self,
        user=None,
        sessions=None,
        chosen: int | None = 0,
        save_choice: str | None = "send",
        draft=False,
    ):
        self.user = user or user_store.new_user("Ana")
        self.users = [] if draft else [self.user]
        self.sessions = [_Session()] if sessions is None else sessions
        self.chosen = chosen  # which session the chooser picks (None = Cancel)
        self.save_choice = save_choice  # what the save-before-send question answers
        self.sent: list[tuple] = []  # (session, user)
        self.sent_ok: list[tuple] = []  # (slot, user, message)
        self.questions = 0
        self.choices: list[list] = []
        self.send_result: tuple[bool, str] = (True, "Sent it.")
        self.win = UserProfileWindow(
            self.user,
            draft,
            ProfileDeps(
                self.users,
                lambda: None,
                lambda _u, _old: None,
                send=SendDeps(
                    live_sessions=lambda: self.sessions,
                    send=self._send,
                    on_sent=lambda slot, user, message: self.sent_ok.append((slot, user, message)),
                    describe=lambda s: f"{s.user_id} (now empty)",
                ),
            ),
            choose_sensor=self._choose,
            ask_save_before_send=self._ask,
        )

    def _choose(self, sessions, describe):
        self.choices.append([describe(s) for s in sessions])
        return None if self.chosen is None else sessions[self.chosen]

    def _ask(self):
        self.questions += 1
        return self.save_choice

    def _send(self, session, user, on_done):
        self.sent.append((session, user))
        on_done(*self.send_result)
        return True


def test_there_is_no_send_button_when_the_app_gave_it_nothing_to_send_with():
    user = user_store.new_user("Ana")
    win = UserProfileWindow(user, False, ProfileDeps([user], lambda: None, lambda _u, _old: None))
    assert win.send_button.isHidden()


def test_the_send_button_is_there_with_send_deps():
    assert not _Env().win.send_button.isHidden()


# -- choosing the sensor ----------------------------------------------------


def test_the_chooser_lists_the_live_sensors_with_what_is_on_them():
    env = _Env(sessions=[_Session(0, "S1"), _Session(1, "S2")])
    env.win.send()
    assert env.choices == [["S1 (now empty)", "S2 (now empty)"]]


def test_with_no_sensor_connected_it_says_to_connect_one_and_asks_nothing():
    env = _Env(sessions=[])
    env.win.send()
    assert "Connect a sensor" in env.win.error.text()
    assert env.choices == [] and env.sent == []


def test_cancelling_the_chooser_sends_nothing():
    env = _Env(chosen=None)
    env.win.send()
    assert env.sent == [] and env.questions == 0


# -- what is sent ----------------------------------------------------


def test_a_clean_user_is_sent_without_any_question():
    env = _Env()
    env.win.send()
    assert env.questions == 0
    [(session, user)] = env.sent
    assert session is env.sessions[0]
    assert user.name == "Ana"


def test_the_sent_user_is_a_copy_not_the_screens_own_object():
    env = _Env()
    env.win.send()
    assert env.sent[0][1] is not env.win.user


def test_success_is_reported_and_recorded_against_the_sensors_slot():
    env = _Env(sessions=[_Session(slot_index=2)])
    env.win.send()
    [(slot, user, message)] = env.sent_ok
    assert slot == 2 and user.name == "Ana" and message == "Sent it."
    assert env.win.error.text() == "Sent it."


def test_a_failure_is_shown_and_nothing_is_recorded():
    env = _Env()
    env.send_result = (False, "Could not send: the board did not answer in time.")
    env.win.send()
    assert "did not answer" in env.win.error.text()
    assert env.sent_ok == []


def test_a_send_that_could_not_start_is_reported():
    env = _Env()
    deps = env.win._deps.send
    assert deps is not None
    deps.send = lambda *_a: False  # a send is already running
    env.win.send()
    assert "already" in env.win.error.text()


# -- unsaved changes ----------------------------------------------------


def _edit(env):
    env.win.profile_page.weight_spin.setValue(61.0)


def test_unsaved_edits_ask_whether_to_save_first():
    env = _Env()
    _edit(env)
    env.win.send()
    assert env.questions == 1


def test_save_and_send_saves_then_sends_what_was_saved():
    env = _Env(save_choice="save")
    _edit(env)
    env.win.send()
    assert env.users[0].weight_kg == 61.0  # saved
    assert env.sent[0][1].weight_kg == 61.0
    assert not env.win.is_dirty


def test_send_without_saving_sends_what_is_on_screen_and_saves_nothing():
    env = _Env(save_choice="send")
    _edit(env)
    env.win.send()
    assert env.sent[0][1].weight_kg == 61.0  # the edit goes to the board
    assert env.users[0].weight_kg != 61.0  # but not into the saved user
    assert env.win.is_dirty


def test_cancelling_the_question_sends_nothing():
    env = _Env(save_choice=None)
    _edit(env)
    env.win.send()
    assert env.sent == []


def test_a_save_that_fails_stops_the_send():
    other = user_store.new_user("Bo")
    env = _Env(save_choice="save")
    env.users.append(other)
    env.win.profile_page.name_edit.setText("Bo")  # a name another user has
    env.win.profile_page.name_edit.textEdited.emit("Bo")
    env.win.send()
    assert env.sent == []


def test_a_draft_counts_as_unsaved_and_asks():
    env = _Env(draft=True)
    env.win.send()
    assert env.questions == 1


# -- what cannot be sent ----------------------------------------------------


def test_a_blank_name_is_refused_before_anything_is_chosen_or_sent():
    env = _Env()
    env.win.profile_page.name_edit.setText("   ")
    env.win.profile_page.name_edit.textEdited.emit("   ")
    env.win.send()
    assert "name" in env.win.error.text()
    assert env.sent == []


def test_a_name_another_user_has_is_refused():
    env = _Env()
    env.users.append(user_store.new_user("Bo"))
    env.win.profile_page.name_edit.setText("Bo")
    env.win.profile_page.name_edit.textEdited.emit("Bo")
    env.win.send()
    assert "already" in env.win.error.text()
    assert env.sent == []


def test_a_csv_user_with_no_window_is_refused():
    env = _Env()
    env.win.profile_page.csv_radio.setChecked(True)
    env.win.send()
    assert "CSV" in env.win.error.text()
    assert env.sent == []


def test_a_csv_user_with_a_window_is_sent():
    env = _Env()
    env.win.user.csv = CsvTrack(samples=[100] * 288, interval_s=300, foodlog=[])
    env.win.profile_page.csv_radio.setChecked(True)
    env.win.send()
    assert env.sent and env.sent[0][1].mode == "csv"
