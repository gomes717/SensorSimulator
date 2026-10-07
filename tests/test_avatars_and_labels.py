"""Picking a profile picture from the bundled avatars, showing it on the sensor tab, naming the
sensors by their user, fitting windows to the screen, and the initials disc (gui/avatar_picker.py,
gui/sensor_tabs.py, gui/users_window.py, gui/user_profile_window.py)."""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QApplication, QWidget

from gui import avatar_picker, user_picture
from gui.avatar import _initials, avatar_icon
from gui.avatar_picker import AvatarDialog, avatar_files
from gui.user_profile_window import ProfileDeps, UserProfileWindow
from gui.users_window import UsersWindow
from gui.widgets import fit_to_screen
from models import user_store


@pytest.fixture(scope="module", autouse=True)
def _app():
    return QApplication.instance() or QApplication([])


def _png(path: Path, color="red", size=64) -> Path:
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(QColor(color))
    assert image.save(str(path), "PNG")
    return path


# -- the avatar gallery ------------------------------


def test_the_bundled_avatars_are_the_images_in_data_profile():
    files = avatar_files()
    assert len(files) >= 6
    assert all(f.suffix.lower() == ".png" for f in files)
    assert avatar_picker.AVATAR_DIR.name == "profile"


def test_avatar_files_lists_images_only_and_in_name_order(tmp_path):
    for name in ("b.png", "a.png", "c.jpg"):
        _png(tmp_path / name)
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    assert [f.name for f in avatar_files(tmp_path)] == ["a.png", "b.png", "c.jpg"]


def test_a_missing_avatar_folder_is_just_no_avatars(tmp_path):
    assert avatar_files(tmp_path / "nope") == []


def test_the_gallery_shows_one_tile_per_avatar_and_nothing_is_chosen_at_first(tmp_path):
    for name in ("a.png", "b.png"):
        _png(tmp_path / name)
    dialog = AvatarDialog(tmp_path)
    assert dialog.list.count() == 2
    assert dialog.selected is None


def test_choosing_a_tile_selects_that_file(tmp_path):
    for name in ("a.png", "b.png"):
        _png(tmp_path / name)
    dialog = AvatarDialog(tmp_path)
    dialog.list.setCurrentRow(1)
    dialog.accept()
    assert dialog.selected == tmp_path / "b.png"


def test_cancelling_the_gallery_selects_nothing(tmp_path):
    _png(tmp_path / "a.png")
    dialog = AvatarDialog(tmp_path)
    dialog.list.setCurrentRow(0)
    dialog.reject()
    assert dialog.selected is None


def test_an_empty_gallery_says_where_the_avatars_should_be(tmp_path):
    dialog = AvatarDialog(tmp_path)
    assert dialog.list.count() == 0
    assert "profile" in dialog.note.text() or str(tmp_path) in dialog.note.text()


def test_an_avatar_becomes_the_users_stored_picture_on_save(tmp_path):
    avatar = _png(tmp_path / "a.png", "green", 512)
    user = user_store.new_user("Ana")
    users = [user]
    win = UserProfileWindow(
        user,
        False,
        ProfileDeps(users, lambda: None, lambda _u, _old: None),
        choose_picture=lambda: avatar,
    )
    win.profile_page.picture_button.click()
    assert win.save() is True
    assert users[0].picture == "picture.png"
    stored = user_picture.stored_pixmap(users[0], 22)
    assert stored is not None and not stored.isNull()


def test_the_picture_button_names_what_it_does():
    user = user_store.new_user("Ana")
    win = UserProfileWindow(user, False, ProfileDeps([user], lambda: None, lambda _u, _old: None))
    assert "avatar" in win.profile_page.picture_button.text().lower()


# -- the stored picture ------------------------------


def test_a_user_with_no_picture_has_no_stored_pixmap():
    assert user_picture.stored_pixmap(user_store.new_user("Ana"), 22) is None


def test_a_user_whose_picture_file_is_gone_has_no_stored_pixmap():
    user = user_store.new_user("Ana")
    user.picture = "picture.png"
    assert user_picture.stored_pixmap(user, 22) is None


# -- the sensor tab ------------------------------


def _tabs(picture_for=None):
    from datetime import UTC, datetime

    from gui.run_clock import RunClock
    from gui.sensor_tabs import SensorTabs

    thresholds = {"tbr2_below": 54.0, "tbr1_below": 70.0, "tar1_above": 180.0, "tar2_above": 250.0}
    names = {"S1": "Nordic Glucose Sensor 1"}
    return (
        SensorTabs(
            thresholds,
            3600.0,
            RunClock(datetime.now(UTC), 1.0),
            lambda key, _dev: names.get(key, key),
            picture_for=picture_for,
        ),
        names,
    )


def test_a_tab_shows_the_users_picture_when_it_has_one():
    from PyQt6.QtGui import QPixmap

    pixmap = QPixmap(22, 22)
    pixmap.fill(QColor("blue"))
    tabs, _names = _tabs(lambda _key, _dev: pixmap)
    tabs.ensure_tab("S1")
    header = tabs.header("S1")
    assert header is not None and header.shows_picture


def test_a_tab_without_a_picture_shows_the_initials_disc():
    tabs, _names = _tabs(lambda _key, _dev: None)
    tabs.ensure_tab("S1")
    header = tabs.header("S1")
    assert header is not None and not header.shows_picture


def test_a_tab_picks_up_a_picture_chosen_later():
    from PyQt6.QtGui import QPixmap

    state = {"pixmap": None}
    tabs, _names = _tabs(lambda _key, _dev: state["pixmap"])
    tabs.ensure_tab("S1")
    header = tabs.header("S1")
    assert header is not None and not header.shows_picture
    pixmap = QPixmap(22, 22)
    pixmap.fill(QColor("blue"))
    state["pixmap"] = pixmap
    tabs.refresh_labels()  # what saving a user does
    assert header.shows_picture
    state["pixmap"] = None
    tabs.refresh_labels()
    assert not header.shows_picture


def test_a_tab_with_no_picture_provider_still_works():
    tabs, _names = _tabs()
    tabs.ensure_tab("S1")
    header = tabs.header("S1")
    assert header is not None and not header.shows_picture


# -- the initials disc ------------------------------


def test_a_users_initials_ignore_the_sensor_suffix_and_the_dash():
    assert _initials("test4 — Sensor 3") == "TE"
    assert _initials("Rafael — Sensor 1") == "RA"
    assert _initials("Ana Rodrigues — Sensor 2") == "AR"
    assert _initials("Nordic Glucose Sensor 2") == "NG"


def test_the_disc_follows_the_label_it_is_given():
    assert avatar_icon("seed", "Ana Rodrigues", 22) is not avatar_icon("seed", "Bo Lee", 22)


# -- windows fit the screen ------------------------------


def test_a_window_is_never_larger_than_the_screen():
    widget = QWidget()
    fit_to_screen(widget, 100_000, 100_000)
    screen = widget.screen().availableGeometry()
    assert widget.width() <= screen.width() and widget.height() <= screen.height()


def test_a_window_smaller_than_the_screen_keeps_its_size():
    widget = QWidget()
    fit_to_screen(widget, 300, 200)
    assert (widget.width(), widget.height()) == (300, 200)


def test_the_profile_screen_can_be_made_small_enough_for_any_screen():
    user = user_store.new_user("Ana")
    win = UserProfileWindow(user, False, ProfileDeps([user], lambda: None, lambda _u, _old: None))
    screen = win.screen().availableGeometry()
    assert win.minimumSizeHint().height() < screen.height()
    assert win.minimumSizeHint().width() < screen.width()
    assert win.height() <= screen.height() and win.width() <= screen.width()


# -- the Users list and its sensors ------------------------------


class _Reader:
    busy = False

    def read(self, session, on_done):
        return True

    def write_name(self, session, slot, name):
        pass


class _Session:
    def __init__(self, user_id):
        self.user_id = user_id


def _users_window(users, **kw):
    return UsersWindow(
        users,
        save=lambda: None,
        live_sessions=kw.pop("live_sessions", lambda: []),
        reader=_Reader(),
        board_busy=lambda: False,
        **kw,
    )


def _texts(window):
    items = [window.list.item(i) for i in range(window.list.count())]
    return [item.text() for item in items if item is not None]


def test_the_list_says_which_sensor_a_user_is_on():
    ana, bo = user_store.new_user("Ana"), user_store.new_user("Bo")
    window = _users_window([ana, bo], where=lambda u: "Sensor 3" if u is ana else "")
    assert _texts(window) == ["Ana  ·  Sensor 3", "Bo"]


def test_the_read_status_names_the_sensor_by_its_label_not_the_stale_session_name():
    window = _users_window([], label=lambda s: "test4 — Sensor 3")
    window.read_from(_Session("Nordic Glucose Sensor 3"))
    assert "test4 — Sensor 3" in window.status.text()
    assert "Nordic" not in window.status.text()
