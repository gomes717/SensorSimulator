"""The Model page of the profile screen: the user's glucose model and CGM sensor-noise model,
each with its parameters. The weight is the model's ``BW`` and is edited on the Profile page."""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtWidgets import QComboBox, QGroupBox, QScrollArea, QVBoxLayout, QWidget

from gui.param_form import ParamForm
from models import user_edit
from models.types import MODEL_LABELS, SENSOR_LABELS, ModelId, SensorId, User

_BW_TEXT = "{value} kg — set on the Profile page"


class ModelPage(QWidget):
    """Choose the glucose model and the sensor noise, and edit their parameters."""

    def __init__(
        self, user: User, changed: Callable[[], None], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._user = user
        self._changed = changed
        self._syncing = False

        body = QWidget()
        layout = QVBoxLayout(body)

        glucose = QGroupBox("Glucose model")
        glucose_layout = QVBoxLayout(glucose)
        self.model_combo = QComboBox()
        for model_id in ModelId:
            self.model_combo.addItem(MODEL_LABELS[model_id], model_id)
        self.model_combo.currentIndexChanged.connect(self._model_chosen)
        glucose_layout.addWidget(self.model_combo)
        self.model_form = ParamForm()
        self.model_form.edited.connect(self._model_param_edited)
        glucose_layout.addWidget(self.model_form)
        layout.addWidget(glucose)

        sensor = QGroupBox("CGM sensor noise")
        sensor_layout = QVBoxLayout(sensor)
        self.sensor_combo = QComboBox()
        for sensor_id in SensorId:
            self.sensor_combo.addItem(SENSOR_LABELS[sensor_id], sensor_id)
        self.sensor_combo.currentIndexChanged.connect(self._sensor_chosen)
        sensor_layout.addWidget(self.sensor_combo)
        self.sensor_form = ParamForm()
        self.sensor_form.edited.connect(self._sensor_param_edited)
        sensor_layout.addWidget(self.sensor_form)
        layout.addWidget(sensor)
        layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        self._rebuild()

    # -- user -> widgets ------------------------------------------------------------

    def _rebuild(self) -> None:
        """Show the user's model and sensor with a form for each (combos set silently)."""
        user = self._user
        self._syncing = True
        self.model_combo.setCurrentIndex(self.model_combo.findData(user.model_id))
        self.sensor_combo.setCurrentIndex(self.sensor_combo.findData(user.sensor_id))
        self._syncing = False
        self.model_form.set_params(
            user_edit.model_param_names(user), user.model_params, {"BW": _BW_TEXT}
        )
        self.sensor_form.set_params(user_edit.sensor_param_names(user), user.sensor_params)

    def refresh(self) -> None:
        """Follow the user (the weight, a model switch) without disturbing what is being typed."""
        user = self._user
        if (
            self.model_combo.currentData() != user.model_id
            or self.sensor_combo.currentData() != user.sensor_id
        ):
            self._rebuild()
            return
        self.model_form.sync(user.model_params, {"BW": _BW_TEXT})
        self.sensor_form.sync(user.sensor_params)

    # -- widgets -> user ------------------------------------------------------------

    def _model_chosen(self) -> None:
        if self._syncing:
            return
        user_edit.set_model(self._user, self.model_combo.currentData())
        self._rebuild()
        self._changed()

    def _sensor_chosen(self) -> None:
        if self._syncing:
            return
        user_edit.set_sensor(self._user, self.sensor_combo.currentData())
        self._rebuild()
        self._changed()

    def _model_param_edited(self, name: str, value: float) -> None:
        user_edit.set_param(self._user, name, value)
        self._changed()

    def _sensor_param_edited(self, name: str, value: float) -> None:
        user_edit.set_sensor_param(self._user, name, value)
        self._changed()
