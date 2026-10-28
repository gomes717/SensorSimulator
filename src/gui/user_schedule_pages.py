"""The Food and Exercise pages of the profile screen: one class, two configurations.

Each page shows a table of the user's recurring daily events and a row of fields to add one; the
graph of the schedule is the profile screen's overview (:mod:`gui.user_overview`), which follows
every edit. Events are kept in time order and the board holds 32 of each.
Edits go straight into the user the screen is working on (see :mod:`models.user_edit`).

Selecting a row loads it into the fields, and changing a field changes that row at once (the list
stays in time order and the row stays selected). With no row selected the fields are for a new
event: **Add** puts it in and clears the selection.
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
    QVBoxLayout,
    QWidget,
)

from gui.widgets import NoWheelDoubleSpinBox, NoWheelTimeEdit, wrapped_label
from models import user_edit
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
    default_time: QTime
    first: FieldSpec
    second: FieldSpec
    events: Callable[[User], list]
    make: Callable[[int, float, float], Any]  # (minute of day, first, second) -> an event
    put: Callable[[User, Any], None]  # add an event in time order (raises past the board's 32)
    remove: Callable[[User, int], None]
    values: Callable[[Any], tuple[float, float]]  # an event's (first, second) field values
    row: Callable[[Any], tuple[str, str]]
    note: str


def _hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


FOOD = ScheduleKind(
    title="Meals",
    default_time=QTime(8, 0),
    first=FieldSpec("Carbs (g)", 0.0, 500.0, 50.0, 1),
    second=FieldSpec("Spread over (min)", 1, 240, 15, 0),
    events=lambda user: user.food_events,
    make=lambda minute, carbs, spread: FoodEvent(
        time_of_day_min=minute, carbs_g=carbs, duration_min=int(spread)
    ),
    put=user_edit.add_food_event,
    remove=user_edit.remove_food_event,
    values=lambda e: (e.carbs_g, float(e.duration_min)),
    row=lambda e: (f"{e.carbs_g:g}", str(e.duration_min)),
    note=(
        "Meals recur every day. Drawn spread over their duration; Roy & Parker and Deichmann "
        "take the whole meal at its start time."
    ),
)

EXERCISE = ScheduleKind(
    title="Exercise",
    default_time=QTime(18, 0),
    first=FieldSpec("Duration (min)", 1, 300, 30, 0),
    second=FieldSpec("Intensity (%)", 0.0, 100.0, 50.0, 1),
    events=lambda user: user.exercise_events,
    make=lambda minute, duration, intensity: ExerciseEvent(
        time_of_day_min=minute, duration_min=int(duration), intensity_pct=intensity
    ),
    put=user_edit.add_exercise_event,
    remove=user_edit.remove_exercise_event,
    values=lambda e: (float(e.duration_min), e.intensity_pct),
    row=lambda e: (str(e.duration_min), f"{e.intensity_pct:g}"),
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
        self._row = -1  # the selected row (-1 = none), kept across a rebuild of the table
        self._loading = False  # the fields are being filled from a row: not an edit of it

        layout = QVBoxLayout(self)
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
        self.table.itemSelectionChanged.connect(self._on_selection)
        layout.addWidget(self.table, 1)

        add_row = QHBoxLayout()
        self.time_edit = NoWheelTimeEdit(kind.default_time)
        add_row.addWidget(QLabel("Time:"))
        add_row.addWidget(self.time_edit)
        self.first_spin = _spin(kind.first)
        add_row.addWidget(QLabel(f"{kind.first.label}:"))
        add_row.addWidget(self.first_spin)
        self.second_spin = _spin(kind.second)
        add_row.addWidget(QLabel(f"{kind.second.label}:"))
        add_row.addWidget(self.second_spin)
        self.time_edit.timeChanged.connect(self._edit_selected)
        self.first_spin.valueChanged.connect(self._edit_selected)
        self.second_spin.valueChanged.connect(self._edit_selected)
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
        """Redraw the table from the user's events, keeping the selected row selected."""
        events = self._kind.events(self._user)
        self._loading = True
        self.table.setRowCount(len(events))
        for row, event in enumerate(events):
            first, second = self._kind.row(event)
            for column, text in enumerate((_hhmm(event.time_of_day_min), first, second)):
                self.table.setItem(row, column, QTableWidgetItem(text))
        if 0 <= self._row < len(events):
            self.table.selectRow(self._row)
            self._fill_fields(events[self._row])
        else:
            self._row = -1
            self.table.clearSelection()
        self._loading = False

    def _fill_fields(self, event: Any) -> None:
        """Show *event* in the fields (not an edit of it)."""
        first, second = self._kind.values(event)
        self.time_edit.setTime(QTime(event.time_of_day_min // 60, event.time_of_day_min % 60))
        self.first_spin.setValue(first)
        self.second_spin.setValue(second)

    def _event_from_fields(self) -> Any:
        time = self.time_edit.time()
        return self._kind.make(
            time.hour() * 60 + time.minute(), self.first_spin.value(), self.second_spin.value()
        )

    def _on_selection(self) -> None:
        """A row was clicked: show it in the fields."""
        if self._loading:
            return
        selected = self.table.selectionModel().selectedRows()
        self._row = selected[0].row() if selected else -1
        events = self._kind.events(self._user)
        if 0 <= self._row < len(events):
            self._loading = True
            self._fill_fields(events[self._row])
            self._loading = False

    # -- widgets -> user ------------------------------------------------------------

    def _put(self, event: Any) -> bool:
        """Add *event* in time order; False (with the reason shown) if the board's limit is
        reached."""
        try:
            self._kind.put(self._user, event)
        except ValueError as exc:
            self.message.setText(str(exc))
            return False
        self.message.setText("")
        return True

    def _edit_selected(self) -> None:
        """A field changed while a row is selected: that row takes the new values."""
        events = self._kind.events(self._user)
        if self._loading or not 0 <= self._row < len(events):
            return
        self._kind.remove(self._user, self._row)  # one out, one in: never over the board's limit
        event = self._event_from_fields()
        self._put(event)
        self._row = next(i for i, e in enumerate(self._kind.events(self._user)) if e is event)
        self.refresh()
        self._changed()

    def _add(self) -> None:
        if not self._put(self._event_from_fields()):
            return
        self._row = -1  # nothing selected, so the fields are free for the next new event
        self.refresh()
        self._changed()

    def _remove(self) -> None:
        if self._row < 0:
            return
        self._kind.remove(self._user, self._row)
        self._row = -1
        self.message.setText("")
        self.refresh()
        self._changed()
