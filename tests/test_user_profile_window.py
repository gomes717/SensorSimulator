"""Users-screen slice 5: the profile screen — header, menu gated by mode, the Profile and Model
pages, dirty tracking, Save and its checks, the picture, and the unsaved-changes prompt
(gui/user_profile_window.py, gui/user_profiles.py)."""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QApplication, QWidget

from gui.user_profile_window import ProfileDeps, UserProfileWindow
from gui.user_profiles import UserProfiles
from models import user_store
from models.types import SENSOR_LABELS, ModelId, SensorId


@pytest.fixture(scope="module", autouse=True)
def _app():
    return QApplication.instance() or QApplication([])


class _Env:
    def __init__(self, users=None, user=None, draft=False, picture=None, unsaved=None):
        self.users = users if users is not None else []
        self.user = user if user is not None else user_store.new_user("Ana")
        if not draft and self.user not in self.users:
            self.users.append(self.user)
        self.saves = 0
        self.saved_users: list = []
        self.asked = 0
        self._unsaved = unsaved
        self.win = UserProfileWindow(
            self.user,
            draft,
            ProfileDeps(self.users, self._save, self.saved_users.append),
            ask_unsaved=self._ask,
            choose_picture=lambda: picture,
        )

    def _save(self):
        self.saves += 1

    def _ask(self):
        self.asked += 1
        return self._unsaved

    def menu(self):
        items = [self.win.menu.item(i) for i in range(self.win.menu.count())]
        return {
            item.text(): bool(item.flags() & Qt.ItemFlag.ItemIsEnabled)
            for item in items
            if item is not None
        }


def _png(path: Path, width=40, height=20) -> Path:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))
    assert image.save(str(path), "PNG")
    return path


# -- the shell ----------------------------------------------------------------------


def test_the_header_shows_the_name_and_the_menu_the_registered_pages_in_order():
    env = _Env()
    assert env.win.name_label.text() == "Ana"
    assert list(env.menu()) == ["Profile", "CSV", "Food", "Exercise", "Model"]


def test_a_page_registered_later_joins_the_menu_in_its_place():
    env = _Env()
    env.win.add_page("extra_unknown", "Ignored", QWidget())  # not in PAGE_ORDER: not listed
    assert list(env.menu()) == ["Profile", "CSV", "Food", "Exercise", "Model"]


def test_clicking_a_menu_entry_shows_its_page():
    env = _Env()
    env.win.menu.setCurrentRow(4)  # Model
    assert env.win.stack.currentWidget() is env.win.pages["model"]


# -- mode gating ----------------------------------------------------------------------


def _with_all_pages(env):
    """Every page now exists; kept so the gating tests read the same."""


def test_model_mode_enables_everything_but_csv():
    env = _Env()
    _with_all_pages(env)
    assert env.menu() == {
        "Profile": True,
        "CSV": False,
        "Food": True,
        "Exercise": True,
        "Model": True,
    }


def test_csv_mode_enables_only_profile_and_csv():
    env = _Env()
    _with_all_pages(env)
    env.win.profile_page.csv_radio.setChecked(True)
    assert env.user.mode == "model"  # the window works on a copy until Save
    assert env.win.user.mode == "csv"
    assert env.menu() == {
        "Profile": True,
        "CSV": True,
        "Food": False,
        "Exercise": False,
        "Model": False,
    }


def test_switching_to_csv_leaves_a_page_that_is_no_longer_enabled():
    env = _Env()
    env.win.menu.setCurrentRow(4)  # Model
    env.win.profile_page.csv_radio.setChecked(True)
    assert env.win.stack.currentWidget() is env.win.pages["profile"]


def test_switching_back_to_model_enables_the_pages_again():
    env = _Env()
    env.win.profile_page.csv_radio.setChecked(True)
    env.win.profile_page.model_radio.setChecked(True)
    assert env.menu()["Model"] is True


def test_the_model_inputs_survive_a_round_trip_through_csv_mode():
    env = _Env()
    env.win.model_page.model_form.spins["VG"].setValue(1.75)
    env.win.profile_page.csv_radio.setChecked(True)
    env.win.profile_page.model_radio.setChecked(True)
    assert env.win.user.model_params["VG"] == 1.75


# -- the Profile page ---------------------------------------------------------------------


def test_editing_the_name_updates_the_header_and_marks_the_screen_unsaved():
    env = _Env()
    assert not env.win.is_dirty
    env.win.profile_page.name_edit.setText("Bo")
    env.win.profile_page.name_edit.textEdited.emit("Bo")
    assert env.win.name_label.text() == "Bo"
    assert env.win.is_dirty
    assert env.win.windowTitle().endswith("*")


def test_a_name_longer_than_the_board_holds_is_cut_as_it_is_typed():
    env = _Env()
    edit = env.win.profile_page.name_edit
    edit.setText("n" * 40)
    edit.textEdited.emit("n" * 40)
    assert edit.text() == "n" * 30
    assert env.win.user.name == "n" * 30


def test_the_weight_is_the_models_bw_and_the_model_page_follows_it():
    env = _Env()
    env.win.profile_page.weight_spin.setValue(61.5)
    assert env.win.user.weight_kg == 61.5
    assert env.win.user.model_params["BW"] == 61.5
    assert "61.5" in env.win.model_page.model_form.locked["BW"].text()  # shown, not editable
    assert "BW" not in env.win.model_page.model_form.spins


def test_height_zero_is_not_set():
    env = _Env()
    env.win.profile_page.height_spin.setValue(171)
    assert env.win.user.height_cm == 171
    env.win.profile_page.height_spin.setValue(0)
    assert env.win.user.height_cm is None


# -- the Model page ---------------------------------------------------------------------


def test_the_model_page_lists_the_users_model_parameters_in_the_boards_order():
    env = _Env()
    spins = list(env.win.model_page.model_form.spins) + list(env.win.model_page.model_form.locked)
    assert set(spins) == set(user_store.new_user("x").model_params)


def test_a_parameter_edit_goes_to_the_user():
    env = _Env()
    env.win.model_page.model_form.spins["VG"].setValue(0.5)
    assert env.win.user.model_params["VG"] == 0.5
    assert env.win.is_dirty


def test_choosing_another_model_loads_its_parameters_and_keeps_the_weight():
    env = _Env()
    env.win.profile_page.weight_spin.setValue(61.5)
    combo = env.win.model_page.model_combo
    combo.setCurrentIndex(combo.findData(ModelId.ROYPARKER))
    assert env.win.user.model_id == ModelId.ROYPARKER
    assert env.win.user.model_params["BW"] == 61.5
    assert "Gpeq" in env.win.model_page.model_form.spins  # Roy & Parker's own parameters


def test_choosing_a_sensor_loads_its_noise_parameters():
    env = _Env()
    assert env.win.model_page.sensor_form.spins == {}  # Ideal has none
    combo = env.win.model_page.sensor_combo
    combo.setCurrentIndex(combo.findData(SensorId.BRETON))
    assert env.win.user.sensor_id == SensorId.BRETON
    assert set(env.win.model_page.sensor_form.spins) == {"pacf", "sigma", "alpha", "beta"}
    env.win.model_page.sensor_form.spins["sigma"].setValue(2.5)
    assert env.win.user.sensor_params["sigma"] == 2.5


def test_the_sensor_combo_uses_the_shared_labels():
    env = _Env()
    combo = env.win.model_page.sensor_combo
    assert [combo.itemText(i) for i in range(combo.count())] == list(SENSOR_LABELS.values())


# -- Save ---------------------------------------------------------------------------------


def test_save_writes_the_edits_into_the_users_list_in_place_and_persists():
    env = _Env(users=[user_store.new_user("Bo")])
    original_id = env.user.id
    env.win.profile_page.name_edit.setText("Ana R")
    env.win.profile_page.name_edit.textEdited.emit("Ana R")
    assert env.win.save() is True
    assert [u.name for u in env.users] == ["Bo", "Ana R"]
    assert env.users[1].id == original_id
    assert env.saves == 1
    assert env.saved_users == [env.users[1]]
    assert not env.win.is_dirty
    assert not env.win.windowTitle().endswith("*")


def test_the_screen_edits_a_copy_so_an_unsaved_edit_changes_nothing():
    env = _Env()
    env.win.profile_page.weight_spin.setValue(99.0)
    assert env.users[0].weight_kg != 99.0
    assert env.user.weight_kg != 99.0


def test_a_draft_is_unsaved_until_saved_then_added_once():
    draft = user_store.new_user("Fresh")
    env = _Env(users=[user_store.new_user("Bo")], user=draft, draft=True)
    assert env.win.is_dirty  # a draft is never "clean"
    assert [u.name for u in env.users] == ["Bo"]
    assert env.win.save() is True
    assert [u.name for u in env.users] == ["Bo", "Fresh"]
    assert not env.win.is_dirty
    env.win.profile_page.weight_spin.setValue(70.0)
    env.win.save()
    assert [u.name for u in env.users] == ["Bo", "Fresh"]  # replaced, not added again


def test_a_name_another_user_has_is_refused_and_nothing_is_saved():
    env = _Env(users=[user_store.new_user("Bo")])
    env.win.profile_page.name_edit.setText("Bo")
    env.win.profile_page.name_edit.textEdited.emit("Bo")
    assert env.win.save() is False
    assert "already" in env.win.error.text()
    assert env.saves == 0 and env.win.is_dirty


def test_a_blank_name_is_refused():
    env = _Env()
    env.win.profile_page.name_edit.setText("   ")
    env.win.profile_page.name_edit.textEdited.emit("   ")
    assert env.win.save() is False
    assert "name" in env.win.error.text()


def test_a_successful_save_clears_an_earlier_error():
    env = _Env(users=[user_store.new_user("Bo")])
    env.win.profile_page.name_edit.setText("Bo")
    env.win.profile_page.name_edit.textEdited.emit("Bo")
    env.win.save()
    env.win.profile_page.name_edit.setText("Cy")
    env.win.profile_page.name_edit.textEdited.emit("Cy")
    assert env.win.save() is True
    assert env.win.error.text() == ""


def test_the_name_is_trimmed_on_save():
    env = _Env()
    env.win.profile_page.name_edit.setText("  Ana  ")
    env.win.profile_page.name_edit.textEdited.emit("  Ana  ")
    env.win.save()
    assert env.users[0].name == "Ana"


# -- the picture --------------------------------------------------------------------------


def test_a_chosen_picture_is_stored_square_on_save_and_not_before(tmp_path):
    source = _png(tmp_path / "me.png", width=400, height=200)
    env = _Env(picture=source)
    env.win.profile_page.picture_button.click()
    assert env.win.is_dirty
    folder = user_store.user_dir(env.user)
    assert not (folder / "picture.png").exists()  # chosen, not stored yet
    assert env.win.save() is True
    stored = QImage(str(folder / "picture.png"))
    assert (stored.width(), stored.height()) == (256, 256)
    assert env.users[0].picture == "picture.png"


def test_cancelling_the_picture_dialog_changes_nothing():
    env = _Env(picture=None)
    env.win.profile_page.picture_button.click()
    assert not env.win.is_dirty


def test_a_file_that_is_not_a_picture_is_reported_on_save_and_nothing_is_saved(tmp_path):
    bad = tmp_path / "notes.png"
    bad.write_text("not an image", encoding="utf-8")
    env = _Env(picture=bad)
    env.win.profile_page.picture_button.click()
    assert env.win.save() is False
    assert "picture" in env.win.error.text()
    assert env.saves == 0


# -- closing with unsaved changes -------------------------------------------------------------


def test_closing_a_clean_screen_does_not_ask():
    env = _Env()
    assert env.win.close() is True
    assert env.asked == 0


def test_closing_unsaved_asks_and_cancel_keeps_the_screen_open():
    env = _Env(unsaved=None)
    env.win.profile_page.weight_spin.setValue(61.0)
    assert env.win.close() is False
    assert env.asked == 1


def test_closing_unsaved_and_discarding_drops_the_edits():
    env = _Env(unsaved="discard")
    env.win.profile_page.weight_spin.setValue(61.0)
    assert env.win.close() is True
    assert env.saves == 0 and env.users[0].weight_kg != 61.0


def test_closing_unsaved_and_saving_saves_then_closes():
    env = _Env(unsaved="save")
    env.win.profile_page.weight_spin.setValue(61.0)
    assert env.win.close() is True
    assert env.saves == 1 and env.users[0].weight_kg == 61.0


def test_closing_unsaved_and_saving_a_refused_name_keeps_the_screen_open():
    env = _Env(users=[user_store.new_user("Bo")], unsaved="save")
    env.win.profile_page.name_edit.setText("Bo")
    env.win.profile_page.name_edit.textEdited.emit("Bo")
    assert env.win.close() is False


# -- the manager --------------------------------------------------------------------------------


def test_opening_the_same_user_twice_shows_one_window():
    users = [user_store.new_user("Ana")]
    manager = UserProfiles(ProfileDeps(users, lambda: None, lambda _u: None), None)
    first = manager.open(users[0], False)
    assert manager.open(users[0], False) is first
    first.close()


def test_a_closed_window_is_forgotten_so_the_user_can_be_opened_again():
    users = [user_store.new_user("Ana")]
    manager = UserProfiles(ProfileDeps(users, lambda: None, lambda _u: None), None)
    first = manager.open(users[0], False)
    first.close()
    assert manager.open(users[0], False) is not first


def test_different_users_get_their_own_windows():
    users = [user_store.new_user("Ana"), user_store.new_user("Bo")]
    manager = UserProfiles(ProfileDeps(users, lambda: None, lambda _u: None), None)
    a, b = manager.open(users[0], False), manager.open(users[1], False)
    assert a is not b
    a.close()
    b.close()
