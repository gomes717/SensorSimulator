"""Users-screen slice 5, pure part: editing a user — weight is the model's BW, switching model
keeps the weight, mode gates the pages, and what Save checks (models/user_edit.py)."""

import pytest

from models import user_edit, user_store
from models.types import ModelId, SensorId


def _user(name="Ana"):
    return user_store.new_user(name)


# -- name -----------------------------------------------------------------------


def test_set_name_stores_it():
    user = _user()
    assert user_edit.set_name(user, "Bo") == "Bo"
    assert user.name == "Bo"


def test_set_name_clips_to_the_boards_30_bytes_and_returns_what_it_kept():
    user = _user()
    assert user_edit.set_name(user, "n" * 40) == "n" * 30
    assert user.name == "n" * 30
    assert user_edit.set_name(user, "é" * 20) == "é" * 15  # 2 bytes each


# -- weight and height -------------------------------------------------------------


def test_weight_is_the_models_bw():
    user = _user()
    user_edit.set_weight(user, 82.5)
    assert user.weight_kg == 82.5
    assert user.model_params["BW"] == 82.5


def test_height_zero_means_not_set():
    user = _user()
    user_edit.set_height(user, 171.0)
    assert user.height_cm == 171.0
    user_edit.set_height(user, 0.0)
    assert user.height_cm is None
    user_edit.set_height(user, None)
    assert user.height_cm is None


# -- models ---------------------------------------------------------------------------


@pytest.mark.parametrize("model_id", list(ModelId))
def test_switching_model_loads_its_defaults_but_keeps_the_users_weight(model_id):
    user = _user()
    user_edit.set_weight(user, 61.0)
    user_edit.set_model(user, model_id)
    assert user.model_id == model_id
    assert user.model_params["BW"] == 61.0  # not the new model's own default weight
    other = user_edit.default_model_params(model_id)
    assert {k: v for k, v in user.model_params.items() if k != "BW"} == {
        k: v for k, v in other.items() if k != "BW"
    }
    assert user.weight_kg == 61.0


def test_a_user_with_no_weight_takes_the_new_models_default_weight():
    user = _user()
    user.weight_kg = None
    user_edit.set_model(user, ModelId.ROYPARKER)
    assert user.weight_kg == user.model_params["BW"] == 70.0


def test_a_parameter_edit_changes_only_that_parameter():
    user = _user()
    before = dict(user.model_params)
    user_edit.set_param(user, "VG", 1.234)
    assert user.model_params["VG"] == 1.234
    assert {k: v for k, v in user.model_params.items() if k != "VG"} == {
        k: v for k, v in before.items() if k != "VG"
    }


def test_editing_bw_as_a_parameter_is_the_weight():
    user = _user()
    user_edit.set_param(user, "BW", 90.0)
    assert user.weight_kg == 90.0


def test_switching_sensor_loads_its_defaults():
    user = _user()
    user_edit.set_sensor(user, SensorId.BRETON)
    assert user.sensor_id == SensorId.BRETON
    assert user.sensor_params == user_edit.default_sensor_params(SensorId.BRETON)
    assert user.sensor_params  # Breton has parameters


def test_a_sensor_parameter_edit():
    user = _user()
    user_edit.set_sensor(user, SensorId.BRETON)
    user_edit.set_sensor_param(user, "sigma", 3.0)
    assert user.sensor_params["sigma"] == 3.0


# -- mode and the pages it enables ----------------------------------------------------


def test_model_mode_enables_profile_food_exercise_and_model():
    assert user_edit.pages_for("model") == {"profile", "food", "exercise", "model"}


def test_csv_mode_enables_only_profile_and_csv():
    assert user_edit.pages_for("csv") == {"profile", "csv"}


def test_set_mode_keeps_the_other_modes_inputs():
    user = _user()
    user_edit.set_param(user, "VG", 1.5)
    user_edit.set_mode(user, "csv")
    user_edit.set_mode(user, "model")
    assert user.model_params["VG"] == 1.5


def test_an_unknown_mode_is_refused():
    with pytest.raises(ValueError):
        user_edit.set_mode(_user(), "both")


# -- what Save checks -----------------------------------------------------------------


def test_a_good_user_has_no_problem():
    assert user_edit.validate(_user("Ana"), [_user("Bo")]) is None


def test_an_empty_or_blank_name_is_refused():
    assert "name" in (user_edit.validate(_user("   "), []) or "")
    assert "name" in (user_edit.validate(_user(""), []) or "")


def test_a_name_another_user_has_is_refused():
    problem = user_edit.validate(_user("Ana"), [_user("Ana")]) or ""
    assert "Ana" in problem and "already" in problem


def test_a_name_over_30_bytes_is_refused():
    user = _user()
    user.name = "n" * 31  # e.g. a name that arrived another way
    assert "30" in (user_edit.validate(user, []) or "")


def test_the_name_is_compared_after_trimming():
    other = _user("Ana")
    assert user_edit.validate(_user("  Ana "), [other]) is not None


def test_normalize_trims_the_name():
    user = _user("  Ana  ")
    user_edit.normalize(user)
    assert user.name == "Ana"
