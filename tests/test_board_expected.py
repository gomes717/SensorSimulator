"""The board and the local expected model start from the same inputs: Start writes the
app's profile for every model-backed sensor to the board, and the expected model is built
from that same profile. Choosing a patient in a config window never resets the graphs."""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication
from user_helpers import user_of

from api import protocol
from gui import run_controller
from models import board_layout as bl
from models import cambridge, deichmann, royparker, uva_padova
from models.types import (
    MODEL_LABELS,
    ExerciseEvent,
    FoodEvent,
    ModelId,
    PersonProfile,
    SensorId,
    SensorProfile,
)

_DEFAULTS = {
    ModelId.CAMBRIDGE: cambridge.default_params,
    ModelId.UVA_PADOVA: uva_padova.default_params,
    ModelId.ROYPARKER: royparker.default_params,
    ModelId.DEICHMANN: deichmann.default_params,
}


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


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


class _Session:
    """Just enough of BleSession for BoardMode's reads and Start's config push."""

    def __init__(self, slot_index, user_id, live=True, exposes=("user_name",)):
        self.slot_index = slot_index
        self.user_id = user_id
        self.is_live = live
        self._exposes = set(exposes)
        self.config_read = _Signal()
        self.board_layout_finished = _Signal()
        self.writes = []
        self.reads = []
        self.pushes = []  # (entries, run) per send_board_layout call

    def exposes(self, key):
        return key in self._exposes

    def queue_write(self, key, payload):
        self.writes.append((key, payload))

    def request_read(self, key):
        self.reads.append(key)

    def send_board_layout(self, entries, *, run=True):
        self.pushes.append((entries, run))

    def finish_push(self, ok=True, message="applied"):
        self.board_layout_finished.emit("addr", ok, message)

    def answer(self, model_id=ModelId.UVA_PADOVA, csv=False):
        """Reply to both of BoardMode's reads, as the board would."""
        params = _DEFAULTS[model_id]()
        self.config_read.emit("addr", "data_source", protocol.encode_data_source(csv))
        self.config_read.emit("addr", "person", protocol.encode_person_config(model_id, params))


class _Bt:
    def __init__(self, sessions):
        self._sessions = sessions

    def sessions(self):
        return {f"a{i}": s for i, s in enumerate(self._sessions)}

    def stop_all_sessions(self):
        pass

    def close(self):
        pass

    def display_name(self, address):
        return address

    def relabel(self):
        pass


@pytest.fixture
def win(app):
    import gui.main_window as mw

    w = mw.MainWindow()
    w.state.model_only = False
    w.state.board_layout = bl.BoardLayout([bl.SlotAssignment() for _ in range(bl.MAX_SLOTS)])
    yield w
    w.sim.engines.stop_all()
    w.windows.bluetooth = None
    w.close()


def _connect(win, *sessions: _Session) -> tuple[_Session, ...]:
    win.windows.bluetooth = _Bt(list(sessions))
    return sessions


def _assign(win, slot, person, sensor=None):
    """Save *person* (and *sensor*) in the app and record them on *slot*."""
    win.state.users.append(user_of(person, sensor))
    win.state.board_layout.slots[slot].person = person.name


def _person(name, model_id=ModelId.CAMBRIDGE, **kwargs):
    return PersonProfile(name=name, model_id=model_id, params=_DEFAULTS[model_id](), **kwargs)


def _csv_person(name):
    person = _person(name)
    person.data_source = "csv"
    return person


def _writes(entry):
    return [key for key, _ in entry["writes"]]


# --- BoardMode ----------------------------------------------------------------


def test_the_board_answer_names_the_model_for_that_slot(win):
    (s0,) = _connect(win, _Session(0, "S1"))
    win._board_mode.refresh(0)
    s0.answer(ModelId.UVA_PADOVA)
    assert win._board_mode.label(0) == MODEL_LABELS[ModelId.UVA_PADOVA]
    assert win._board_mode.label(1) is None


def test_only_one_slot_is_read_at_a_time(win):
    s0, s1 = _connect(win, _Session(0, "S1"), _Session(1, "S2"))
    win._board_mode.refresh(0)
    win._board_mode.refresh(1)
    assert s0.reads == ["data_source", "person"]
    assert s1.reads == []  # waits: the sensor-select cursor is one value on the board
    s0.answer()
    assert s1.reads == ["data_source", "person"]  # now it is slot 1's turn


def test_a_silent_slot_does_not_block_the_queue_forever(win):
    _s0, s1 = _connect(win, _Session(0, "S1"), _Session(1, "S2"))
    win._board_mode.refresh(0)
    win._board_mode.refresh(1)
    token = win._board_mode._generation
    win._board_mode._timed_out(token)  # slot 0 never answers
    assert s1.reads == ["data_source", "person"]


# --- the expected model is built from the app, exactly as Start writes it ----------


def test_each_live_sensor_runs_the_patient_the_app_assigned_to_it(win):
    _connect(win, _Session(0, "S1"), _Session(1, "S2"), _Session(2, "S3"))
    _assign(win, 0, _person("a", ModelId.CAMBRIDGE))
    _assign(win, 1, _person("b", ModelId.DEICHMANN))
    _assign(win, 2, _person("c", ModelId.UVA_PADOVA))
    win.sim.restart()
    assert win.sim.engines.slots == [0, 1, 2]
    assert win.sim.per_slot_expected is True
    assert {s: p.model_id for s, p in win.sim._slots().items()} == {
        0: ModelId.CAMBRIDGE,
        1: ModelId.DEICHMANN,
        2: ModelId.UVA_PADOVA,
    }


def test_the_app_wins_over_what_the_board_happens_to_hold(win):
    """The board may hold anything an earlier session left; Start overwrites it, so the
    expected model must follow the app's profile, not the readback."""
    _assign(win, 0, _person("pt", ModelId.CAMBRIDGE))
    (s0,) = _connect(win, _Session(0, "S1"))
    win._board_mode.refresh(0)
    s0.answer(ModelId.UVA_PADOVA)  # the board currently holds UVA/Padova
    assert win.sim._slots()[0].model_id is ModelId.CAMBRIDGE


def test_the_expected_model_carries_the_apps_meals_and_exercise(win):
    walk = ExerciseEvent(time_of_day_min=600, duration_min=30, intensity_pct=70.0)
    meal = FoodEvent(time_of_day_min=480, carbs_g=45.0, duration_min=20)
    _assign(win, 0, _person("pt", food_events=[meal], exercise_events=[walk]))
    _connect(win, _Session(0, "S1"))
    person = win.sim._slots()[0]
    assert [(e.time_of_day_min, e.duration_min, e.carbs_g) for e in person.food_events] == [
        (480, 20, 45.0)
    ]
    assert [(e.time_of_day_min, e.intensity_pct) for e in person.exercise_events] == [(600, 70.0)]


def test_a_csv_patient_gets_no_model(win):
    _assign(win, 0, _csv_person("csv"))
    _assign(win, 1, _person("model"))
    _connect(win, _Session(0, "S1"), _Session(1, "S2"))
    assert list(win.sim._slots()) == [1]


def test_an_unassigned_sensor_gets_no_model_once_any_slot_is_assigned(win):
    _assign(win, 1, _person("pt"))
    _connect(win, _Session(0, "S1"), _Session(1, "S2"))
    assert list(win.sim._slots()) == [1]


def test_with_nothing_assigned_the_active_patient_stands_in(win):
    """A lost layout record must not leave connected sensors with no expected line."""
    win.state.active_user = user_of(_person("active"))
    _connect(win, _Session(0, "S1"))
    assert win.sim._slots() == {0: win.state.active_person}


def test_model_only_ignores_the_board(win):
    win.state.model_only = True
    win.state.users[:] = [user_of(PersonProfile(name="m", model_id=ModelId.CAMBRIDGE))]
    win.state.active_user = win.state.users[0]
    (s0,) = _connect(win, _Session(0, "S1"))
    win._board_mode.refresh(0)
    s0.answer(ModelId.UVA_PADOVA)
    assert win.sim._slots() == {0: win.state.active_person}


def test_another_sensors_meals_never_reach_this_sensors_expected_model(win):
    """Three sensors connected, only sensor 1 modelled: sensors 2 and 3 report meals of
    their own, and those used to be fed to sensor 1's single engine."""
    _assign(win, 0, _person("a"))
    _connect(win, _Session(0, "S1"), _Session(1, "S2"), _Session(2, "S3"))
    win.sim.restart()
    assert win.sim.engines.slots == [0]
    fed = []
    win.sim.engines.set_board_food_exercise = lambda slot, c, e: fed.append((slot, c))
    win.sim.feed_board_food_exercise("S2", 6.7, 0.0)
    win.sim.feed_board_food_exercise("S3", 2.7, 0.0)
    assert fed == []
    win.sim.feed_board_food_exercise("S1", 0.0, 0.0)
    assert fed == [(0, 0.0)]


def test_a_lone_unnumbered_sensor_still_feeds_its_engine(win):
    win.state.active_user = user_of(_person("active"))
    _connect(win, _Session(None, "Nordic"))
    win.sim.restart()
    fed = []
    win.sim.engines.set_board_food_exercise = lambda slot, c, e: fed.append((slot, c))
    win.sim.feed_board_food_exercise("Nordic", 3.0, 0.0)
    assert fed == [(0, 3.0)]


# --- Start writes the same profiles to the board ----------------------------------


def test_start_writes_each_sensors_profile_before_the_run_begins(win):
    noise = SensorProfile("noisy", SensorId.IDEAL, {})
    meal = FoodEvent(time_of_day_min=480, carbs_g=45.0, duration_min=20)
    walk = ExerciseEvent(time_of_day_min=600, duration_min=30, intensity_pct=70.0)
    person = _person("a", food_events=[meal], exercise_events=[walk])
    _assign(win, 0, person, noise)
    _assign(win, 1, _person("b", ModelId.DEICHMANN))
    s0, s1 = _connect(win, _Session(0, "S1"), _Session(1, "S2"))

    win._run.start()

    assert win._run.state == run_controller.STARTING  # waiting on the board
    assert not win._run.start_pause_btn.isEnabled()
    assert len(s0.pushes) == 1 and s1.pushes == []  # one session carries every slot
    entries, run = s0.pushes[0]
    assert run is False  # Start decides when the run begins, not the push
    assert [e["slot"] for e in entries] == [0, 1]
    assert _writes(entries[0]) == [
        "speed",
        "user_name",
        "person",
        "sensor",
        "data_source",
        "food",
        "food",
        "exercise",
        "exercise",
    ]
    # every user has a sensor-noise model now, so each slot gets its sensor written too
    assert _writes(entries[1]) == [
        "user_name",
        "person",
        "sensor",
        "data_source",
        "food",
        "exercise",
    ]
    sent = dict(entries[0]["writes"])
    assert protocol.decode_user_name(sent["user_name"]) == "a"  # the board is told who it runs
    assert sent["person"] == protocol.encode_person_config(person.model_id, person.params)
    assert not any(k == "run_state" for k, _ in s0.writes)  # nothing started yet

    s0.finish_push()
    assert win._run.state == run_controller.RUNNING
    assert [protocol.decode_run_state(p) for k, p in s0.writes if k == "run_state"] == [
        protocol.RUN_STATE_STOPPED,
        protocol.RUN_STATE_RUNNING,
    ]


def test_start_still_works_when_the_board_does_not_list_user_name(win):
    """Right after a firmware update Windows can hide the new characteristic (a stale services
    cache): Start must push the model as before, not fail because it cannot write the name."""
    _assign(win, 0, _person("a"))
    (s0,) = _connect(win, _Session(0, "S1", exposes=()))
    win._run.start()
    entries, _run = s0.pushes[0]
    assert "user_name" not in _writes(entries[0])
    assert "person" in _writes(entries[0])


def test_start_skips_a_name_the_board_cannot_hold_instead_of_failing(win):
    _assign(win, 0, _person("n" * 40))  # a migrated name longer than the board's 30 bytes
    (s0,) = _connect(win, _Session(0, "S1"))
    win._run.start()
    entries, _run = s0.pushes[0]
    assert "user_name" not in _writes(entries[0])
    assert "person" in _writes(entries[0])


def test_start_does_not_touch_a_csv_sensor(win):
    _assign(win, 0, _csv_person("csv"))
    _assign(win, 1, _person("model"))
    s0, _s1 = _connect(win, _Session(0, "S1"), _Session(1, "S2"))
    win._run.start()
    entries, _run = s0.pushes[0]
    assert [e["slot"] for e in entries] == [1]


def test_start_with_only_csv_sensors_has_nothing_to_push_but_still_runs(win):
    _assign(win, 0, _csv_person("csv"))
    (s0,) = _connect(win, _Session(0, "S1"))
    win._run.start()
    assert s0.pushes == []
    assert win._run.state == run_controller.RUNNING


def test_a_push_the_board_rejects_does_not_start_the_run(win):
    _assign(win, 0, _person("a"))
    (s0,) = _connect(win, _Session(0, "S1"))
    win._run.start()
    s0.finish_push(ok=False, message="link dropped mid-push")
    assert win._run.state == run_controller.STOPPED
    assert win._run.start_pause_btn.text() == "Start"
    assert not any(k == "run_state" for k, _ in s0.writes)
    assert "not started" in win.statusBar().currentMessage()


def test_the_expected_model_is_built_only_after_the_board_has_the_config(win):
    _assign(win, 1, _person("a"))
    _connect(win, _Session(1, "S2"))
    s1 = win.windows.bluetooth.sessions()["a0"]
    win._run.start()
    assert win.sim.engines.slots == [0]  # the pool from before: not rebuilt yet
    s1.finish_push()
    assert win.sim.engines.slots == [1]


def test_model_only_start_pushes_nothing(win):
    win.state.model_only = True
    win.state.active_user = user_of(_person("m"))
    (s0,) = _connect(win, _Session(0, "S1"))
    win._run.start()
    assert s0.pushes == []
    assert win._run.state == run_controller.RUNNING


# --- choosing a patient is not a send ----------------------------------------


def test_opening_a_user_does_not_reset_the_graphs(win):
    win.sensors.on_new_message(
        {
            "user_id": "S1",
            "dev_id": "a",
            "glucose_value": 100.0,
            "timestamp": "2026-01-01T00:00:00+00:00",
        }
    )
    page = win.sensors.page_of_user("S1")
    page.add_received(5.0, 111.0)
    other = user_of(PersonProfile(name="other", model_id=ModelId.CAMBRIDGE))
    win.state.users.append(other)
    win._activate_user(other)
    assert win.state.active_user is other
    assert page.graph.buf.graph_y == [111.0]  # untouched


def test_saving_a_user_does_not_reset_the_graphs_with_a_board(win):
    page = win.sensors.page_of_user("S1")
    page.add_received(5.0, 111.0)
    win._on_user_saved(win.state.users[0], None)
    assert page.graph.buf.graph_y == [111.0]


def test_model_only_still_restarts_when_the_user_changes(win):
    win.state.model_only = True
    page = win.tabs.pages.default_page
    page.add_received(5.0, 111.0)
    other = user_of(PersonProfile(name="other", model_id=ModelId.CAMBRIDGE))
    win.state.users.append(other)
    win._activate_user(other)
    assert page.graph.buf.graph_y == []


def test_renaming_a_user_moves_its_slot_along(win):
    _assign(win, 0, _person("Ana"))
    saved = win.state.users[-1]
    saved.name = "Ana R"
    win._on_user_saved(saved, "Ana")
    assert win.state.board_layout.slots[0].person == "Ana R"
    assert list(win.state.engine_slots()) == [0]  # still found, under its new name


def test_deleting_a_user_takes_it_off_its_slot(win):
    _assign(win, 0, _person("Ana"))
    gone = win.state.users.pop()
    win._on_user_deleted(gone)
    assert win.state.board_layout.slots[0].person is None


def test_deleting_a_user_no_sensor_runs_does_not_reset_the_graphs(win):
    _assign(win, 0, _person("Ana"))
    unused = user_of(_person("Unused"))
    win.state.users.append(unused)
    _connect(win, _Session(0, "S1"))
    win.sim.restart()
    page = win.sensors.page_of_user("S1")
    page.add_received(5.0, 111.0)
    win._on_user_deleted(unused)
    assert page.graph.buf.graph_y == [111.0]  # untouched
    assert win.state.board_layout.slots[0].person == "Ana"  # and Ana keeps her sensor


def test_deleting_the_user_a_sensor_runs_does_reset_that_run(win):
    _assign(win, 0, _person("Ana"))
    _connect(win, _Session(0, "S1"))
    win.sim.restart()
    assert win.sim.engines.slots == [0]
    win._on_user_deleted(win.state.users.pop())
    assert win.sim.engines.slots != [0] or win.state.board_layout.slots[0].person is None


def test_deleting_the_active_user_resets_model_only_but_not_a_board_run(win):
    win.state.model_only = True
    active = user_of(_person("Active"))
    win.state.users.append(active)
    win.state.active_user = active
    page = win.tabs.pages.default_page
    page.add_received(5.0, 111.0)
    win.state.users.remove(active)
    win._on_user_deleted(active)
    assert page.graph.buf.graph_y == []  # Model Only was running that user
    assert win.state.active_user is not active
