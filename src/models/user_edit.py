"""Editing a :class:`~models.types.User`: the rules the profile screen applies, kept apart from
Qt so they are tested on their own.

* **Weight is the model's ``BW``.** Setting one sets the other, and switching model keeps the
  user's weight (the new model's own default weight is only used for a user with none).
* **Mode gates the pages.** Model mode enables the Profile, Food, Exercise and Model pages; CSV
  mode only Profile (always, so the mode can be switched back) and CSV. The inputs of the mode
  not in use are kept, not cleared.
* **The name is what the board is told**, so it is clipped to the board's 30 UTF-8 bytes and
  must be unique (users are recognised by name when read from a board).

Every function changes the user it is given, in place.
"""

from __future__ import annotations

from types import ModuleType

from api import protocol
from models import cambridge, deichmann, royparker, user_match, uva_padova
from models import sensors as sensor_defaults
from models.types import ModelId, SensorId, User

MODEL_MODULES: dict[ModelId, ModuleType] = {
    ModelId.CAMBRIDGE: cambridge,
    ModelId.UVA_PADOVA: uva_padova,
    ModelId.ROYPARKER: royparker,
    ModelId.DEICHMANN: deichmann,
}
_SENSOR_DEFAULTS = {
    SensorId.IDEAL: sensor_defaults.ideal_default_params,
    SensorId.BRETON: sensor_defaults.breton_default_params,
    SensorId.FACCHINETTI: sensor_defaults.facchinetti_default_params,
}

# Which pages of the profile screen are usable in each mode.
_PAGES_BY_MODE = {
    "model": frozenset({"profile", "food", "exercise", "model"}),
    "csv": frozenset({"profile", "csv"}),
}


def default_model_params(model_id: ModelId) -> dict[str, float]:
    """The model's own default parameters (a fresh dict)."""
    return dict(MODEL_MODULES[model_id].default_params())


def default_sensor_params(sensor_id: SensorId) -> dict[str, float]:
    """The sensor-noise model's default parameters (a fresh dict)."""
    return dict(_SENSOR_DEFAULTS[sensor_id]())


def pages_for(mode: str) -> frozenset[str]:
    """The pages usable in *mode* (``"model"`` or ``"csv"``)."""
    return _PAGES_BY_MODE[mode]


# -- identity ----------------------------------------------------------------------


def set_name(user: User, name: str) -> str:
    """Set the name, clipped to what the board holds; returns the name actually kept."""
    user.name = user_match.clip_name(name)
    return user.name


def set_height(user: User, cm: float | None) -> None:
    """Set the height; ``None`` or 0 means not set. App-only: nothing uses it yet."""
    user.height_cm = cm if cm else None


def set_weight(user: User, kg: float) -> None:
    """Set the weight, which is also the model's ``BW`` parameter."""
    user.weight_kg = kg
    user.model_params["BW"] = kg


def set_mode(user: User, mode: str) -> None:
    """Switch between ``"model"`` and ``"csv"``; the other mode's inputs are kept."""
    if mode not in _PAGES_BY_MODE:
        raise ValueError(f"unknown mode {mode!r}")
    user.mode = mode


# -- models ------------------------------------------------------------------------


def set_model(user: User, model_id: ModelId) -> None:
    """Switch the physiological model: its default parameters, the user's weight as ``BW``."""
    user.model_id = model_id
    params = default_model_params(model_id)
    if user.weight_kg is None:
        user.weight_kg = params["BW"]
    params["BW"] = user.weight_kg
    user.model_params = params


def set_param(user: User, name: str, value: float) -> None:
    """Set one model parameter (``BW`` is the weight)."""
    if name == "BW":
        set_weight(user, value)
    else:
        user.model_params[name] = value


def set_sensor(user: User, sensor_id: SensorId) -> None:
    """Switch the sensor-noise model to its default parameters."""
    user.sensor_id = sensor_id
    user.sensor_params = default_sensor_params(sensor_id)


def set_sensor_param(user: User, name: str, value: float) -> None:
    """Set one sensor-noise parameter."""
    user.sensor_params[name] = value


def model_param_names(user: User) -> list[str]:
    """The parameter names of the user's model, in the order the board expects."""
    return protocol.model_param_names(user.model_id)


def sensor_param_names(user: User) -> list[str]:
    """The parameter names of the user's sensor-noise model, in the board's order."""
    return protocol.sensor_param_names(user.sensor_id)


# -- saving ------------------------------------------------------------------------


def normalize(user: User) -> None:
    """Tidy the user before it is checked and saved (trim the name)."""
    user.name = user.name.strip()


def validate(user: User, others: list[User]) -> str | None:
    """Why *user* cannot be saved next to *others*, or None. The name is compared after
    trimming: it must be present, fit the board, and not be another user's (users are
    recognised by name when read from a board)."""
    name = user.name.strip()
    if not name:
        return "Give the user a name."
    if len(name.encode("utf-8")) > protocol.MAX_USER_NAME_BYTES:
        return f"The name is longer than the board's {protocol.MAX_USER_NAME_BYTES} bytes."
    if any(other.name.strip() == name for other in others):
        return f'Another user is already called "{name}".'
    return None
