"""A read-only summary of a user — what opening one shows until the profile screen exists
(slice 5 of .scratch/users-screen/spec.md replaces this).

An unsaved *draft* (a user read from a board whose name nobody has) gets a Save button, so a
read is never a dead end.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QMessageBox, QWidget

from models.types import MODEL_LABELS, User


def summarize(user: User) -> str:
    """The user's name, mode and inputs as plain text."""
    lines = [f"Name: {user.name}", f"Source: {'CSV replay' if user.mode == 'csv' else 'model'}"]
    if user.weight_kg is not None:
        lines.append(f"Weight: {user.weight_kg:g} kg")
    if user.height_cm is not None:
        lines.append(f"Height: {user.height_cm:g} cm")
    if user.mode == "csv":
        if user.csv is None:
            lines.append("CSV window: not available (the board cannot send it back yet)")
        else:
            hours = len(user.csv.samples) * user.csv.interval_s / 3600
            lines.append(f"CSV window: {len(user.csv.samples)} samples ({hours:g} h)")
    else:
        lines += [
            f"Glucose model: {MODEL_LABELS[user.model_id]}",
            f"Sensor noise: {user.sensor_id.name.title()}",
            f"Meals: {len(user.food_events)}",
            f"Exercise: {len(user.exercise_events)}",
        ]
    return "\n".join(lines)


def show(parent: QWidget | None, user: User, is_draft: bool) -> bool:
    """Show *user*; True if it is a draft and the person chose to save it."""
    box = QMessageBox(parent)
    box.setWindowTitle(f"User — {user.name}")
    box.setText(summarize(user))
    if is_draft:
        box.setInformativeText("This user is not saved yet.")
        save = box.addButton("Save", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()
        return box.clickedButton() is save
    box.addButton(QMessageBox.StandardButton.Close)
    box.exec()
    return False
