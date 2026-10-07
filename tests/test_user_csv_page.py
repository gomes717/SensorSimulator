"""Users-screen slice 7: the CSV page of the profile screen (gui/user_csv_page.py)."""

import os
import sys
from datetime import datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from gui.user_csv_page import CsvPage
from gui.user_profile_window import ProfileDeps, UserProfileWindow
from models import user_store
from models.types import CsvTrack

_HEADER = (
    "Index,Timestamp (YYYY-MM-DDThh:mm:ss),Event Type,Event Subtype,Patient Info,"
    "Device Info,Source Device ID,Glucose Value (mg/dL),Insulin Value (u),"
    "Carb Value (grams),Duration (hh:mm:ss),Glucose Rate of Change (mg/dL/min),"
    "Transmitter Time (Long Integer)"
)


@pytest.fixture(scope="module", autouse=True)
def _app():
    return QApplication.instance() or QApplication([])


def _dexcom(tmp_path: Path) -> Path:
    lines = [_HEADER]
    for i in range(288):
        minutes = i * 5
        lines.append(
            f"{i + 1},2020-01-01 {minutes // 60:02d}:{minutes % 60:02d}:00,EGV,,,,iPhone G6,"
            f"{100 + i % 50},,,,,"
        )
    path = tmp_path / "Dexcom_007.csv"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class _Env:
    """A page whose 'Choose CSV file…' picker is faked: the test plays the person picking."""

    def __init__(self, user=None):
        self.user = user or user_store.new_user("Ana")
        self.changes = 0
        self.on_pick = None
        self.page = CsvPage(self.user, self._changed, open_picker=self._open)

    def _changed(self):
        self.changes += 1

    def _open(self, on_pick):
        self.on_pick = on_pick
        return object()  # stands in for the picker window

    def choose(self, path, start=datetime(2020, 1, 1)):
        self.page.choose_button.click()
        assert self.on_pick is not None
        self.on_pick(str(path), start)


def test_a_user_with_no_window_is_told_to_choose_one():
    env = _Env()
    assert "No CSV window" in env.page.info.text()
    assert env.page.graph.values == [0.0] * 1440


def test_the_choose_button_opens_the_picker():
    env = _Env()
    env.page.choose_button.click()
    assert env.on_pick is not None


def test_a_picked_window_is_copied_into_the_user(tmp_path):
    env = _Env()
    env.choose(_dexcom(tmp_path))
    track = env.user.csv
    assert track is not None
    assert len(track.samples) == 288
    assert track.source_name == "Dexcom_007.csv"
    assert track.start_iso == datetime(2020, 1, 1).isoformat()
    assert env.changes == 1


def test_the_page_describes_the_window_and_draws_it(tmp_path):
    env = _Env()
    env.choose(_dexcom(tmp_path))
    text = env.page.info.text()
    assert "Dexcom_007.csv" in text and "288 samples" in text and "2020-01-01" in text
    assert env.page.graph.values[0] == 100.0
    assert env.page.graph.values[5] == 101.0


def test_the_page_does_not_depend_on_the_original_file_afterwards(tmp_path):
    env = _Env()
    path = _dexcom(tmp_path)
    env.choose(path)
    path.unlink()
    env.page.refresh()  # the window is in the user, the file is not needed
    assert env.user.csv is not None and env.page.graph.values[0] == 100.0


def test_a_file_that_cannot_be_used_is_reported_and_changes_nothing(tmp_path):
    env = _Env()
    bad = tmp_path / "Dexcom_001.csv"
    bad.write_text("a,b\n1,2\n", encoding="utf-8")
    env.choose(bad)
    assert env.user.csv is None
    assert "Dexcom" in env.page.message.text()
    assert env.changes == 0


def test_a_good_pick_clears_an_earlier_message(tmp_path):
    env = _Env()
    bad = tmp_path / "Dexcom_001.csv"
    bad.write_text("a,b\n1,2\n", encoding="utf-8")
    env.choose(bad)
    env.choose(_dexcom(tmp_path))
    assert env.page.message.text() == ""


def test_a_user_read_from_a_csv_board_explains_the_missing_window():
    user = user_store.new_user("From board")
    user.mode = "csv"
    env = _Env(user)
    assert "no recording" in env.page.info.text()


def test_a_window_already_in_the_user_is_shown():
    user = user_store.new_user("Ana")
    user.csv = CsvTrack(
        samples=[120] * 288,
        interval_s=300,
        foodlog=[(3600, 30.0), (7200, 20.0)],
        start_iso="2020-02-01T06:00:00",
        source_name="Dexcom_002.csv",
    )
    env = _Env(user)
    text = env.page.info.text()
    assert "Dexcom_002.csv" in text and "2 meals" in text and "2020-02-01 06:00" in text


# -- inside the profile screen ----------------------------------------------------------------


def _menu_items(win) -> dict:
    items = [win.menu.item(i) for i in range(win.menu.count())]
    return {item.text(): item for item in items if item is not None}


def test_the_profile_screen_has_a_csv_page_that_is_only_usable_in_csv_mode():
    user = user_store.new_user("Ana")
    win = UserProfileWindow(user, False, ProfileDeps([user], lambda: None, lambda _u, _old: None))
    assert isinstance(win.pages["csv"], CsvPage)
    items = _menu_items(win)
    assert list(items) == ["Profile", "CSV", "Food", "Exercise", "Model"]
    assert not items["CSV"].flags() & Qt.ItemFlag.ItemIsEnabled
    win.profile_page.csv_radio.setChecked(True)
    assert items["CSV"].flags() & Qt.ItemFlag.ItemIsEnabled


def test_a_chosen_window_is_saved_with_the_user(tmp_path):
    user = user_store.new_user("Ana")
    users = [user]
    deps = ProfileDeps(users, lambda: user_store.save(users), lambda _u, _old: None)
    win = UserProfileWindow(user, False, deps)
    page = win.pages["csv"]
    assert isinstance(page, CsvPage)
    track = CsvTrack(samples=[100] * 288, interval_s=300, foodlog=[], source_name="x.csv")
    win.user.csv = track
    page.refresh()
    win.profile_page.csv_radio.setChecked(True)
    assert win.save() is True
    assert users[0].csv == track
    assert user_store.load()[0].csv == track  # on disk, with the user
