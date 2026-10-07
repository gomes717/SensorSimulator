"""The local "expected" model: one engine per occupied slot, feeding the sensor pages.

Owns the engine pool and the rules for where its output goes — each slot's
expected line (and PISA shading) lands on that slot's own page — and the single
restart point every profile / mode / layout change goes through. What a restart
means for the surrounding UI (which page shows, titles, CSV view) is reported
through two hooks rather than reached into.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from PyQt6.QtCore import QObject

from gui.app_state import AppState
from gui.run_clock import RunClock
from gui.sensor_directory import SensorDirectory
from gui.sensor_page import SensorPage
from gui.sensor_pages import SensorPages
from models import board_layout
from models.engine import EnginePool
from models.types import PersonProfile


class SimulationCoordinator(QObject):
    """Engine pool + clock reset + routing of expected readings to pages."""

    def __init__(
        self,
        state: AppState,
        directory: SensorDirectory,
        clock: RunClock,
        pages: SensorPages,
        *,
        is_csv_slot: Callable[[int], bool],
        paused: Callable[[], bool],
        on_reset: Callable[[], None],
        on_slots_changed: Callable[[], None],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._state = state
        self._directory = directory
        self._clock = clock
        self._pages = pages
        self._is_csv_slot = is_csv_slot
        self._paused = paused
        self._on_reset = on_reset
        self._on_slots_changed = on_slots_changed
        # One SimulationEngine per occupied slot (issue 04); slot 0 alone otherwise.
        self.engines = EnginePool(self)
        self.engines.expected_reading.connect(self.on_expected_reading)
        # True while the pool drives >1 slot (one expected line per sensor page).
        self.per_slot_expected = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def stop(self) -> None:
        """Stop and discard every engine in the pool.

        EnginePool.stop_all() disconnects each engine's expected_reading before
        stopping it: a QThread emits on a queued connection, so a last tick fired
        as the engine is interrupted would otherwise arrive after this restart
        sequence finishes — landing at the wrong x against the re-anchored clock.
        """
        self.engines.stop_all()

    def reset_graph_view(self) -> None:
        """Clear every page and re-anchor the shared timeline at t=0. Every
        genuine restart (Start/Stop, switch person/sensor/mode, save a profile)
        goes through here — the one shared reset point that keeps each sensor's
        received and expected lines on the same origin. Switching sensors does
        not: pages keep their own history.
        """
        self._clock.anchor(datetime.now(UTC))
        self._pages.clear_all()

    def restart(self) -> None:
        """Stop the pool, reset the graphs, and rebuild one engine per slot."""
        self.stop()
        self.reset_graph_view()
        self._on_reset()

        slots = self._slots()
        self.per_slot_expected = len(slots) > 1
        self._on_slots_changed()
        if not slots:
            return

        # A freshly rebuilt pool sits idle unless a run is already in progress,
        # so switching profiles/modes/layout doesn't silently start a comparison.
        # A CSV-backed person replays its recording (or emits nothing if no
        # window is assigned) — the physiological model never runs for it.
        self.engines.rebuild(slots, self._state.speed_mult, paused=self._paused())

    def _slots(self) -> dict[int, PersonProfile]:
        """slot -> the profile each local engine runs.

        With a board connected this is exactly what Start writes to it
        (:meth:`AppState.board_plan`): the app's person for each live sensor — model,
        parameters, meal and exercise schedules — so the expected line and the
        sensor start from the same inputs. A slot that replays a CSV, or has no patient
        assigned, gets no model. Model Only, and no board, use the app's own choice.
        """
        if self._state.model_only:
            return self._state.engine_slots()
        live = board_layout.live_slots(self._directory.sessions().values())
        if not live:
            return self._state.engine_slots()
        return {slot: person for slot, (person, _) in self._state.board_plan(live).items()}

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    def expected_key(self, slot: int) -> tuple[str, int | None]:
        """(page key, page slot) for *slot*'s expected line and PISA shading.

        With one engine and one connected device the device's page is the target
        whatever numbering the device uses; with several engines each slot has its
        own page, keyed like its received stream (see SensorDirectory.slot_user_id).
        """
        sessions = list(self._directory.sessions().values())
        if not self.per_slot_expected and len(sessions) == 1:
            return sessions[0].user_id, sessions[0].slot_index
        return self._directory.slot_user_id(slot), slot

    def expected_page(self, slot: int) -> SensorPage:
        """The page *slot*'s expected line belongs to, created if needed."""
        key, page_slot = self.expected_key(slot)
        return self._pages.ensure(key, page_slot)

    def on_expected_reading(
        self, slot: int, timestamp: str, glucose: float, carbs_rate: float, exercise_pct: float
    ) -> None:
        """Consume one tick from slot *slot*'s engine in the pool."""
        if self._is_csv_slot(slot):
            # The board told us this slot is replaying a recording, so there is
            # no model behind its trace and nothing for an "expected model" line
            # to mean. The app's own profile for the slot may still say model
            # (that is how the two disagree in the first place), so this has to
            # key off the board's answer, not the profile.
            return
        t = self._clock.elapsed_seconds(timestamp)
        if self._state.model_only:
            page = self._pages.default_page
            page.add_received(t, glucose)
            page.add_food_ex(t, carbs_rate, exercise_pct)
        else:
            self.expected_page(slot).add_expected(t, glucose)

    def drop_expected_line(self, slot: int) -> None:
        """Discard the expected-model curve drawn for *slot* before the board
        revealed it is replaying a CSV — there is no model behind that trace."""
        page = self._pages.get(self.expected_key(slot)[0])
        if page is not None:
            page.drop_expected()

    def record_pisa_span(self, slot: int | None, t_start_s: float, t_end_s: float) -> None:
        """Shade a PISA interval on the page(s) it targets — and only those: a
        fault inserted for slot 3 must not shade slot 1's graph just because
        slot 1 is on screen at the time."""
        if self._state.model_only:
            pages = [self._pages.default_page]
        elif slot is None and self.per_slot_expected:
            pages = [self.expected_page(s) for s in range(board_layout.MAX_SLOTS)]
        elif slot is None:
            pages = self._pages.sensor_pages() or [self._pages.default_page]
        else:
            pages = [self.expected_page(slot)]
        for page in pages:
            page.add_pisa(t_start_s, t_end_s)

    def feed_board_food_exercise(self, user_id: str, carbs: float, exercise: float) -> None:
        """Hand one sensor's board-reported Food/Exercise Status to ITS local engine
        so the "expected" model follows that sensor's own meal input.

        Only the sensor the status came from: with one engine running (say sensor 1)
        and two more sensors connected, their statuses used to be fed to that one
        engine as well, so sensor 1's expected line ran on meals it never had. A
        status from a sensor without an engine is dropped. A lone unnumbered
        (single-sensor) board has no slot to look up; it is the only sensor there is.
        """
        slot = self._directory.slot_of_user(user_id)
        if slot is None and len(self._directory.sessions()) == 1 and self.engines.slots:
            slot = self.engines.slots[0]
        if slot in self.engines.slots:
            self.engines.set_board_food_exercise(slot, carbs, exercise)
