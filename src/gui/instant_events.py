"""One-shot "insert now" events — carb bolus, exercise bout, PISA fault —
applied on top of a running simulation without resetting it.

Pulled out of :class:`MainWindow` (issue 18): the inject helpers formed a
cohesive cluster with one job — fan a transient event out to the local engine
pool, the connected board, and (for PISA) the graph shading. Deps: the engine
pool, the board write surface and the glucose panel. The numbers come from each
sensor's Commands panel (gui/commands_panel.py), which says which slot.
"""

from __future__ import annotations

from datetime import UTC, datetime

from api import protocol


class InstantEvents:
    def __init__(
        self,
        engines,
        board,
        clock,
        record_pisa_span,
        report=None,
    ) -> None:
        self._engines = engines
        self._board = board
        self._clock = clock
        # Says what actually happened to an inserted event. Without it these
        # injections are silent: a write queued on a session whose link has
        # dropped is discarded, and nothing on screen says the board never
        # got it (the local "expected" line moves either way).
        self._report = report or (lambda _message: None)
        # Shades a PISA interval on the page(s) of the slot(s) it targets.
        self._record_pisa_span = record_pisa_span

    # ------------------------------------------------------------------
    # Injection (also the test scenario runner's entry points)
    # ------------------------------------------------------------------

    def inject_food(self, slot: int | None, duration_min: int, carbs_g: float) -> str:
        """One-shot carb bolus: into the local engine(s) and, if connected, the board."""
        self._engines.add_instant_food(slot, duration_min, carbs_g)
        sent = self._board.send_instant(
            "food_instant", protocol.encode_food_instant(duration_min, carbs_g), slot
        )
        return self._reported(self._outcome(f"{carbs_g:g} g over {duration_min} min", sent))

    def inject_exercise(self, slot: int | None, duration_min: int, intensity_pct: float) -> str:
        """One-shot exercise bout: into the local engine(s) and, if connected, the board."""
        self._engines.add_instant_exercise(slot, duration_min, intensity_pct)
        sent = self._board.send_instant(
            "exercise_instant",
            protocol.encode_exercise_instant(duration_min, intensity_pct),
            slot,
        )
        return self._reported(self._outcome(f"{intensity_pct:g}% for {duration_min} min", sent))

    def inject_fault(self, kind: str, values: tuple, slot: int | None = None) -> str:
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
        message = self._reported(
            self._outcome(f"PISA {depth_frac:.0%} for {duration_min} min", sent)
        )
        # Shade the affected interval: the graph x-axis is *simulated* seconds
        # now, so a sim-minute duration is just * 60. Routed by slot, so a fault
        # sent to one sensor doesn't shade every open sensor's graph.
        t0 = self._clock.elapsed_seconds(datetime.now(UTC).isoformat(timespec="seconds"))
        self._record_pisa_span(slot, t0, t0 + duration_min * 60.0)
        return message

    def _reported(self, message: str) -> str:
        """Report *message* (status bar) and hand it back for the caller's own display."""
        self._report(message)
        return message

    def _outcome(self, what: str, sent: int) -> str:
        if not self._board.connected():
            return f"{what}: applied to the local model (no board connected)."
        if sent == 0:
            return (
                f"⚠ {what}: NOT sent — no live board link. "
                "Reconnect the sensor in the Bluetooth window and try again."
            )
        return f"✓ {what}: sent to {sent} sensor(s)."
