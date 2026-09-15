"""One-shot "insert now" events — carb bolus, exercise bout, PISA fault —
applied on top of a running simulation without resetting it.

Pulled out of :class:`MainWindow` (issue 18): the inject helpers, the slot
picker, and the three button handlers formed a cohesive cluster with one job —
fan a transient event out to the local engine pool, the connected board, and
(for PISA) the graph shading. Deps: the engine pool, the board write surface,
the glucose panel, the board layout, and the current speed multiplier.
"""

from __future__ import annotations

from datetime import UTC, datetime

from PyQt6.QtWidgets import QDialog, QMessageBox, QWidget

from api import protocol
from gui.instant_event_dialog import ExerciseInstantDialog, FoodInstantDialog, PisaInstantDialog
from models import board_layout as board_layout_mod


class InstantEvents:
    def __init__(self, engines, board, graph, board_layout, report=None, current_slot=None) -> None:
        self._engines = engines
        self._board = board
        self._graph = graph
        self._board_layout = board_layout
        # Says what actually happened to an inserted event. Without it these
        # injections are silent: a write queued on a session whose link has
        # dropped is discarded, and nothing on screen says the board never
        # got it (the local "expected" line moves either way).
        self._report = report or (lambda _message: None)
        # Which sensor row the user is looking at, so an inserted event targets
        # that one by default instead of every slot at once.
        self._current_slot = current_slot or (lambda: None)

    # ------------------------------------------------------------------
    # Injection (also the scenario-runner entry points)
    # ------------------------------------------------------------------

    def inject_food(self, slot: int | None, duration_min: int, carbs_g: float) -> None:
        """One-shot carb bolus: into the local engine(s) and, if connected, the board."""
        self._engines.add_instant_food(slot, duration_min, carbs_g)
        sent = self._board.send_instant(
            "food_instant", protocol.encode_food_instant(duration_min, carbs_g), slot
        )
        self._report(self._outcome(f"{carbs_g:g} g over {duration_min} min", sent))

    def inject_exercise(self, slot: int | None, duration_min: int, intensity_pct: float) -> None:
        """One-shot exercise bout: into the local engine(s) and, if connected, the board."""
        self._engines.add_instant_exercise(slot, duration_min, intensity_pct)
        sent = self._board.send_instant(
            "exercise_instant",
            protocol.encode_exercise_instant(duration_min, intensity_pct),
            slot,
        )
        self._report(self._outcome(f"{intensity_pct:g}% for {duration_min} min", sent))

    def inject_fault(self, kind: str, values: tuple, slot: int | None = None) -> None:
        """Inject a sensor fault without resetting the run.

        The entry point behind "Insert PISA Now…". Only ``"pisa"`` is wired — a transient
        false low: the board/engine multiply the sensor reading by
        ``1 - depth*sin(pi*elapsed/duration)`` while active; the interval is
        shaded on the graph.
        """
        if kind != "pisa":
            raise ValueError(f"unknown fault kind {kind!r}")
        duration_min, depth_frac = values
        self._engines.add_instant_pisa(slot, duration_min, depth_frac)
        sent = self._board.send_instant(
            "pisa_instant", protocol.encode_pisa_instant(duration_min, depth_frac), slot
        )
        self._report(self._outcome(f"PISA {depth_frac:.0%} for {duration_min} min", sent))
        # Shade the affected interval: the graph x-axis is *simulated* seconds
        # now, so a sim-minute duration is just * 60.
        t0 = self._graph.elapsed_seconds(datetime.now(UTC).isoformat(timespec="seconds"))
        self._graph.add_pisa_span(t0, t0 + duration_min * 60.0)
        self._graph.redraw_glucose()

    def _outcome(self, what: str, sent: int) -> str:
        if not self._board.connected():
            return f"{what}: applied to the local model (no board connected)."
        if sent == 0:
            return (
                f"⚠ {what}: NOT sent — no live board link. "
                "Reconnect the sensor in the Bluetooth window and try again."
            )
        return f"✓ {what}: sent to {sent} sensor(s)."

    # ------------------------------------------------------------------
    # Button handlers (prompt, then inject)
    # ------------------------------------------------------------------

    def _slot_choices(self) -> list[tuple[int | None, str]]:
        """(value, label) for the dialogs' Target combo — empty (no combo) unless
        a multi-sensor board is connected, then "All sensors" + one per slot."""
        if not self._board.multi_slot():
            return []
        out: list[tuple[int | None, str]] = [(None, "All sensors")]
        for i in range(board_layout_mod.MAX_SLOTS):
            person = self._board_layout.slots[i].person
            out.append((i, f"Sensor {i + 1} — {person}" if person else f"Sensor {i + 1}"))
        return out

    def _ready(self, parent: QWidget, title: str) -> bool:
        if self._engines.is_empty() and not self._board.connected():
            QMessageBox.information(
                parent, title, "Nothing running to insert into — start a run first."
            )
            return False
        return True

    def prompt_food(self, parent: QWidget) -> None:
        if not self._ready(parent, "Insert Food Now"):
            return
        dialog = FoodInstantDialog(
            parent, slot_choices=self._slot_choices(), default_slot=self._current_slot()
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        carbs_g, duration_min = dialog.values()
        self.inject_food(dialog.selected_slot(), duration_min, carbs_g)

    def prompt_exercise(self, parent: QWidget) -> None:
        if not self._ready(parent, "Insert Exercise Now"):
            return
        dialog = ExerciseInstantDialog(
            parent, slot_choices=self._slot_choices(), default_slot=self._current_slot()
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        duration_min, intensity_pct = dialog.values()
        self.inject_exercise(dialog.selected_slot(), duration_min, intensity_pct)

    def prompt_pisa(self, parent: QWidget) -> None:
        if not self._ready(parent, "Insert PISA Now"):
            return
        dialog = PisaInstantDialog(
            parent, slot_choices=self._slot_choices(), default_slot=self._current_slot()
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.inject_fault("pisa", dialog.values(), slot=dialog.selected_slot())
