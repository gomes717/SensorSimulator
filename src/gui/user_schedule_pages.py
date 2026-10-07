"""The Food and Exercise pages of the profile screen: one class, two configurations.

Each page shows a 24 h graph of the user's recurring daily schedule above a table of its events,
and a row of fields to add one. Events are kept in time order and the board holds 32 of each.
Edits go straight into the user the screen is working on (see :mod:`models.user_edit`).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from PyQt6.QtCore import QTime
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from gui.schedule_graph import ScheduleGraph
from gui.widgets import NoWheelDoubleSpinBox, wrapped_label
from models import user_edit, user_schedule
from models.types import ExerciseEvent, FoodEvent, User


@dataclass(frozen=True)
class FieldSpec:
    """One numeric field of an event: its label, range, default and decimals."""

    label: str
    low: float
    high: float
    default: float
    decimals: int


@dataclass(frozen=True)
class ScheduleKind:
    """Everything that differs between the Food page and the Exercise page."""

    title: str
    graph_title: str
    ylabel: str
    color: str
    default_time: QTime
    first: FieldSpec
    second: FieldSpec
    events: Callable[[User], list]
    add: Callable[[User, int, float, float], None]
    remove: Callable[[User, int], None]
    row: Callable[[Any], tuple[str, str]]
    series: Callable[[User], list[float]]
    note: str


def _hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


FOOD = ScheduleKind(
    title="Meals",
    graph_title="Carbohydrate intake over the day",
    ylabel="Carbs (g/min)",
    color="#3f8fd0",
    default_time=QTime(8, 0),
    first=FieldSpec("Carbs (g)", 0.0, 500.0, 50.0, 1),
    second=FieldSpec("Spread over (min)", 1, 240, 15, 0),
    events=lambda user: user.food_events,
    add=lambda user, minute, carbs, spread: user_edit.add_food_event(
        user, FoodEvent(time_of_day_min=minute, carbs_g=carbs, duration_min=int(spread))
    ),
    remove=user_edit.remove_food_event,
    row=lambda e: (f"{e.carbs_g:g}", str(e.duration_min)),
    series=lambda user: user_schedule.food_rate_series(user.food_events),
    note=(
        "Meals recur every day. Drawn spread over their duration; Roy & Parker and Deichmann "
        "take the whole meal at its start time."
    ),
)

EXERCISE = ScheduleKind(
    title="Exercise",
    graph_title="Exercise intensity over the day",
    ylabel="Intensity (%)",
    color="#e0813f",
    default_time=QTime(18, 0),
    first=FieldSpec("Duration (min)", 1, 300, 30, 0),
    second=FieldSpec("Intensity (%)", 0.0, 100.0, 50.0, 1),
    events=lambda user: user.exercise_events,
    add=lambda user, minute, duration, intensity: user_edit.add_exercise_event(
        user,
        ExerciseEvent(time_of_day_min=minute, duration_min=int(duration), intensity_pct=intensity),
    ),
    remove=user_edit.remove_exercise_event,
    row=lambda e: (str(e.duration_min), f"{e.intensity_pct:g}"),
    series=lambda user: user_schedule.exercise_series(user.exercise_events),
    note="Exercise bouts recur every day; where two overlap the stronger one counts.",
)


def _spin(spec: FieldSpec) -> NoWheelDoubleSpinBox:
    spin = NoWheelDoubleSpinBox()
    spin.setDecimals(spec.decimals)
    spin.setRange(spec.low, spec.high)
    spin.setValue(spec.default)
    return spin


class SchedulePage(QWidget):
    """Graph, table and add form for one kind of daily schedule of the user being edited."""

    def __init__(
        self,
        user: User,
        kind: ScheduleKind,
        changed: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._user = user
        self._kind = kind
        self._changed = changed

        layout = QVBoxLayout(self)
        self.graph = ScheduleGraph(kind.graph_title, kind.ylabel, kind.color)
        layout.addWidget(self.graph)
        layout.addWidget(wrapped_label(kind.note, muted=True))

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Time of day", kind.first.label, kind.second.label])
        header = self.table.horizontalHeader()
        if header is not None:
            for column in range(3):
                header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        row_header = self.table.verticalHeader()
        if row_header is not None:
            row_header.setVisible(False)
        self.table.setAlternatingRowColors(True)
        layout.addWidget(self.table, 1)

        add_row = QHBoxLayout()
        self.time_edit = QTimeEdit(kind.default_time)
        add_row.addWidget(QLabel("Time:"))
        add_row.addWidget(self.time_edit)
        self.first_spin = _spin(kind.first)
        add_row.addWidget(QLabel(f"{kind.first.label}:"))
        add_row.addWidget(self.first_spin)
        self.second_spin = _spin(kind.second)
        add_row.addWidget(QLabel(f"{kind.second.label}:"))
        add_row.addWidget(self.second_spin)
        self.add_button = QPushButton("Add")
        self.add_button.clicked.connect(self._add)
        add_row.addWidget(self.add_button)
        self.remove_button = QPushButton("Remove selected")
        self.remove_button.clicked.connect(self._remove)
        add_row.addWidget(self.remove_button)
        layout.addLayout(add_row)

        self.message = QLabel("")
        self.message.setWordWrap(True)
        self.message.setStyleSheet("color: #b00020;")
        layout.addWidget(self.message)
        self.refresh()

    # -- user -> widgets ------------------------------------------------------------

    def refresh(self) -> None:
        """Redraw the table and the graph from the user's events."""
        events = self._kind.events(self._user)
        self.table.setRowCount(len(events))
        for row, event in enumerate(events):
            first, second = self._kind.row(event)
            for column, text in enumerate((_hhmm(event.time_of_day_min), first, second)):
                self.table.setItem(row, column, QTableWidgetItem(text))
        self.graph.set_values(self._kind.series(self._user))

    # -- widgets -> user ------------------------------------------------------------

    def _add(self) -> None:
        time = self.time_edit.time()
        try:
            self._kind.add(
                self._user,
                time.hour() * 60 + time.minute(),
                self.first_spin.value(),
                self.second_spin.value(),
            )
        except ValueError as exc:
            self.message.setText(str(exc))
            return
        self.message.setText("")
        self.refresh()
        self._changed()

    def _remove(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        self._kind.remove(self._user, row)
        self.message.setText("")
        self.refresh()
        self._changed()
