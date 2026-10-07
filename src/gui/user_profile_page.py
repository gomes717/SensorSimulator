"""The Profile page of the profile screen: picture, name, height, weight and the source
(model or CSV). Edits go straight into the user the screen is working on."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PyQt6.QtWidgets import (
    QButtonGroup,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from gui.widgets import NoWheelDoubleSpinBox, wrapped_label
from models import user_edit
from models.types import User

_MODE_HELP = (
    "Model: the glucose model, food and exercise drive the user. "
    "CSV: a recorded 24 h window is replayed instead (the model pages are switched off, "
    "their values are kept)."
)


def choose_picture_file() -> Path | None:
    """Ask for an image file (None if cancelled)."""
    name, _filter = QFileDialog.getOpenFileName(
        None, "Choose a picture", "", "Images (*.png *.jpg *.jpeg *.bmp *.gif)"
    )
    return Path(name) if name else None


class ProfilePage(QWidget):
    """Name, height, weight, source and the picture button for the user being edited."""

    def __init__(
        self,
        user: User,
        changed: Callable[[], None],
        on_picture: Callable[[Path], None],
        choose_picture: Callable[[], Path | None] = choose_picture_file,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._user = user
        self._changed = changed
        self._on_picture = on_picture
        self._choose_picture = choose_picture
        self._syncing = False

        outer = QVBoxLayout(self)
        form = QFormLayout()
        outer.addLayout(form)
        self.picture_button = QPushButton("Choose picture…")
        self.picture_button.clicked.connect(self._pick_picture)
        form.addRow("Picture:", self.picture_button)

        self.name_edit = QLineEdit()
        self.name_edit.setToolTip("What the board is told: at most 30 bytes of text.")
        self.name_edit.textEdited.connect(self._name_edited)
        form.addRow("Name:", self.name_edit)

        self.height_spin = NoWheelDoubleSpinBox()
        self.height_spin.setRange(0.0, 250.0)
        self.height_spin.setDecimals(0)
        self.height_spin.setSuffix(" cm")
        self.height_spin.setSpecialValueText("not set")
        self.height_spin.setToolTip("Shown only; nothing uses the height yet.")
        self.height_spin.valueChanged.connect(self._height_edited)
        form.addRow("Height:", self.height_spin)

        self.weight_spin = NoWheelDoubleSpinBox()
        self.weight_spin.setRange(20.0, 300.0)
        self.weight_spin.setDecimals(1)
        self.weight_spin.setSuffix(" kg")
        self.weight_spin.setToolTip("Also the glucose model's BW parameter.")
        self.weight_spin.valueChanged.connect(self._weight_edited)
        form.addRow("Weight:", self.weight_spin)

        self.model_radio = QRadioButton("Model")
        self.csv_radio = QRadioButton("CSV")
        group = QButtonGroup(self)
        group.addButton(self.model_radio)
        group.addButton(self.csv_radio)
        self.model_radio.toggled.connect(self._mode_toggled)
        self.csv_radio.toggled.connect(self._mode_toggled)
        modes = QHBoxLayout()
        modes.addWidget(self.model_radio)
        modes.addWidget(self.csv_radio)
        modes.addStretch(1)
        form.addRow("Source:", modes)
        outer.addWidget(wrapped_label(_MODE_HELP, muted=True))
        outer.addStretch(1)
        self.refresh()

    # -- user -> widgets ------------------------------------------------------------

    def refresh(self) -> None:
        """Show the user's values (leaves a widget alone if it already shows its value, so
        typing is not disturbed)."""
        user = self._user
        self._syncing = True
        if self.name_edit.text() != user.name:
            self.name_edit.setText(user.name)
        self.height_spin.setValue(user.height_cm or 0.0)
        if user.weight_kg is not None:
            self.weight_spin.setValue(user.weight_kg)
        self.model_radio.setChecked(user.mode == "model")
        self.csv_radio.setChecked(user.mode == "csv")
        self._syncing = False

    # -- widgets -> user ------------------------------------------------------------

    def _name_edited(self, text: str) -> None:
        kept = user_edit.set_name(self._user, text)
        if kept != text:
            self.name_edit.setText(kept)
        self._changed()

    def _height_edited(self, value: float) -> None:
        if not self._syncing:
            user_edit.set_height(self._user, value)
            self._changed()

    def _weight_edited(self, value: float) -> None:
        if not self._syncing:
            user_edit.set_weight(self._user, value)
            self._changed()

    def _mode_toggled(self, checked: bool) -> None:
        if self._syncing or not checked:
            return
        user_edit.set_mode(self._user, "csv" if self.csv_radio.isChecked() else "model")
        self._changed()

    def _pick_picture(self) -> None:
        path = self._choose_picture()
        if path is not None:
            self._on_picture(path)
