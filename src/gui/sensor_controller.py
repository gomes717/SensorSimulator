"""What happens around the sensor tabs: selection, titles, CSV view, commands, data.

The glue between the sensor tabs/pages and everything that feeds or follows them:
BLE messages are recorded onto their sensor's page, a selected tab brings its page
forward, each page's title and CSV view follow what the *board* says the sensor
runs, and each page's Commands panel is wired to the sensor it belongs to.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QObject

from gui import run_controller
from gui.app_state import AppState
from gui.board_mode import BoardMode
from gui.instant_events import InstantEvents
from gui.run_clock import RunClock
from gui.run_controller import RunController
from gui.sensor_directory import SensorDirectory
from gui.sensor_page import SensorPage
from gui.sensor_tabs import SensorTabs
from gui.simulation import SimulationCoordinator
from models.types import MODEL_LABELS, PersonProfile


class SensorController(QObject):
    """Selection, presentation and data routing for the sensor pages."""

    def __init__(
        self,
        *,
        state: AppState,
        tabs: SensorTabs,
        directory: SensorDirectory,
        board_mode: BoardMode,
        run: RunController,
        events: InstantEvents,
        sim: SimulationCoordinator,
        clock: RunClock,
        disconnect_device: Callable[[str], None],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._state = state
        self._tabs = tabs
        self._pages = tabs.pages
        self._directory = directory
        self._board_mode = board_mode
        self._run = run
        self._events = events
        self._sim = sim
        self._clock = clock
        self._disconnect_device = disconnect_device
        # keys of tabs the user closed: their late readings must not re-create them
        self._closed: set[str] = set()
        self.selected_user: str | None = None

        tabs.user_selected.connect(self.on_user_selected)
        tabs.selection_cleared.connect(self.on_selection_cleared)
        tabs.close_requested.connect(self.on_tab_close_requested)
        self._pages.page_created.connect(self.wire_page)
        self.wire_page(self._pages.default_page)
        run.refreshed.connect(self.refresh_commands)

    # ------------------------------------------------------------------
    # Which page is showing
    # ------------------------------------------------------------------

    def current_page(self) -> SensorPage:
        """The sensor page on screen (the default page for Model Only / no selection)."""
        return self._pages.current_page()

    def page_of_user(self, user_id: str) -> SensorPage:
        """The page for the session *user_id*, created on first sight."""
        return self._pages.ensure(user_id, self._directory.slot_of_user(user_id))

    def selected_slot(self) -> int | None:
        """The board slot behind the selected sensor, or None when the selection
        isn't a slot of a multi-sensor board."""
        if not self.selected_user:
            return None
        return self._directory.slot_of_user(self.selected_user)

    def show_current_page(self) -> None:
        """Put the right page on screen: Model Only's, the selected sensor's, or the
        empty default."""
        if self._state.model_only or not self.selected_user:
            page = self._pages.default_page
        else:
            page = self.page_of_user(self.selected_user)
        self._pages.show_page(page)

    # ------------------------------------------------------------------
    # Presentation: who runs what, per page
    # ------------------------------------------------------------------

    def person_for_page(self, page: SensorPage) -> PersonProfile | None:
        """The person whose data_source should govern *page*.

        In Model Only mode (or on the empty default page) that's simply the active
        person. Board-connected with a multi-sensor layout, each sensor can carry
        a different person (one CSV, one model) — so it follows the page's own
        sensor, not the single active person (which Model Only alone updates).
        """
        if self._state.model_only or page.key is None:
            return self._state.active_person
        for slot, person in self._state.engine_slots().items():
            if self._directory.slot_user_id(slot) == page.key:
                return person
        return self._state.active_person

    def data_source_label(self, page: SensorPage | None = None) -> str:
        """What is driving *page*'s sensor (default: the one on screen): "CSV
        replay" or the model's name.

        Model Only has no board at all — the saved profile is not a guess
        there, it IS what is running, so that mode alone reads it directly.
        Everywhere else this is the BOARD's own answer, and only the board's —
        never the app's saved profile. A CSV uploaded in an earlier session, a
        config changed from elsewhere, or a send that silently failed all
        leave the app's saved intent disagreeing with what the board is
        actually running, and the board is the one producing the trace.
        Guessing from the saved profile when the board hasn't answered yet was
        worse than saying nothing: it showed a specific, confident answer that
        was sometimes flat wrong. Waits instead — the label says so until a
        real answer arrives, and does not estimate one in the meantime.

        The same carve-out as person_for_page(): the Model Only / empty page
        has no live board answer to even be waiting for, so the saved profile is
        the only thing there is to show.
        """
        page = page or self.current_page()
        if self._state.model_only or page.key is None:
            person = self.person_for_page(page)
            if person is None:
                return "no patient assigned"
            return (
                "CSV replay"
                if getattr(person, "data_source", "model") == "csv"
                else MODEL_LABELS.get(person.model_id, "model")
            )
        reported = self._board_mode.label(page.slot)
        if reported is not None:
            return reported
        if self.person_for_page(page) is None:
            return "no patient assigned"
        return "waiting for the board to confirm…"

    def _title_for(self, page: SensorPage) -> str:
        """The glucose-graph title for *page*."""
        if page.key is None:
            if not self._state.model_only:
                return "Select a user"
            person = self._state.active_person
            if person is None:
                return "Model Only — select a person"
            return f"Model — {person.name} · {self.data_source_label(page)}"
        return f"Glucose — {self._tabs.label_of(page.key)} · {self.data_source_label(page)}"

    def set_graph_title(self) -> None:
        """Refresh every page's title for the current mode / board answers."""
        for page in self._pages.all():
            page.set_title(self._title_for(page))

    def is_csv_page(self, page: SensorPage) -> bool:
        """Whether *page*'s sensor is CSV-backed right now.

        Model Only, or the empty page, has no live board answer to defer to —
        same carve-out as data_source_label — so the saved profile is read
        directly there. Otherwise this is the BOARD's confirmed answer alone,
        never the app's saved profile: a person saved as CSV but never sent to
        the board is not actually replaying anything, and asserting it was is
        what let a panel and the main title disagree. Reads False (food/exercise
        shown normally) until the board itself says otherwise — it does not
        guess in the meantime.
        """
        if self._state.model_only or page.key is None:
            return getattr(self.person_for_page(page), "data_source", "model") == "csv"
        return self._board_mode.label(page.slot) == "CSV replay"

    def apply_csv_mode_view(self) -> None:
        """A CSV-backed sensor has no model and no meal input that drives glucose
        (the food log is report-only), so its page hides the food/exercise graph
        and the Commands panel (issue 08)."""
        for page in self._pages.all():
            page.set_csv(self.is_csv_page(page))

    def on_board_mode_changed(self) -> None:
        """The board answered a mode read (see gui/board_mode.py) — refresh
        whatever the titles / food-exercise views show for it."""
        self.set_graph_title()
        self.apply_csv_mode_view()

    def rebuild_for_theme(self) -> None:
        """After a palette switch: rebuild every page's canvases so they repaint in
        the new colors (they read the Qt palette only at build time)."""
        self._pages.rebuild_for_theme()
        self.set_graph_title()
        self.apply_csv_mode_view()

    # ------------------------------------------------------------------
    # Commands (one panel per page, aimed at that page's sensor)
    # ------------------------------------------------------------------

    def wire_page(self, page: SensorPage) -> None:
        """Connect a new page's Commands panel: a command goes to that page's
        sensor — never to whatever tab happens to be selected."""
        cmds = page.commands
        cmds.food_requested.connect(
            lambda carbs, spread, p=page: self._show_result(
                p, self._events.inject_food(p.slot, spread, carbs)
            )
        )
        cmds.exercise_requested.connect(
            lambda duration, pct, p=page: self._show_result(
                p, self._events.inject_exercise(p.slot, duration, pct)
            )
        )
        cmds.pisa_requested.connect(
            lambda duration, depth, p=page: self._show_result(
                p, self._events.inject_fault("pisa", (duration, depth), p.slot)
            )
        )
        self._refresh_page_commands(page)

    @staticmethod
    def _show_result(page: SensorPage, message: str | None) -> None:
        if message:
            page.commands.set_result(message)

    def _commands_reason(self, page: SensorPage) -> str:
        """Why *page*'s commands are blocked, or "" when they can be sent: the run
        must be going, and that page's own sensor must have a live link."""
        reason = self._run.commands_block_reason()
        if reason or page.key is None or self._directory.is_live(page.key):
            return reason
        return "This sensor is offline — reconnect it to send commands."

    def _refresh_page_commands(self, page: SensorPage) -> None:
        reason = self._commands_reason(page)
        if reason != page.commands.blocked_reason:
            page.commands.set_blocked(reason)

    def refresh_commands(self) -> None:
        """Re-check every page's Commands panel (run state or a link changed)."""
        for page in self._pages.all():
            self._refresh_page_commands(page)

    # ------------------------------------------------------------------
    # Data in
    # ------------------------------------------------------------------

    def on_new_message(self, msg: dict) -> None:
        """Record + plot a decoded BLE notification (the tab itself is SensorTabs').

        ``recording`` gates the history appends: the board keeps sending
        notifications regardless of run state, so without it a Stop/Pause would
        be undone by the next one. CGMS-only has no Start/Stop so it keys on
        state.cgms_only; Model Only ignores BLE entirely (the engine drives the
        plots). Every sensor's stream is recorded while recording, each into its
        own page; a page that is not on screen defers its redraw.
        """
        user_id = msg.get("user_id")
        if user_id in self._closed:
            return  # a reading already on its way from a sensor whose tab was just closed
        recording = not self._state.model_only and (
            self._state.cgms_only or self._run.state == run_controller.RUNNING
        )

        self._tabs.note_message(msg)
        if not (recording and user_id is not None):
            return
        page = self.page_of_user(user_id)

        if "glucose_value" in msg:
            page.add_received(self._clock.elapsed_seconds(msg["timestamp"]), msg["glucose_value"])

        if "carbs_g_per_min" in msg:
            carbs = msg["carbs_g_per_min"]
            exercise = msg.get("exercise_pct", 0.0)
            page.add_food_ex(self._clock.elapsed_seconds(msg["timestamp"]), carbs, exercise)
            # Drive the local "expected" model with the board's own food/exercise
            # so the two lines only ever differ by sensor noise, never by a
            # stale local schedule (the board's Food/Exercise Status already
            # folds in its schedule + any instant events).
            self._sim.feed_board_food_exercise(user_id, carbs, exercise)

    # ------------------------------------------------------------------
    # Tab events
    # ------------------------------------------------------------------

    def on_session_connected(self, address: str, session) -> None:
        """A sensor finished connecting: open (or revive) its tab right away.

        A sensor that reconnects after a power cycle keeps its tab and history
        even if its session id changed meanwhile — matched by board slot, or by
        device address for an unnumbered board."""
        key = session.user_id
        self._closed.discard(key)  # connecting again is how a closed sensor comes back
        old = self._tabs.find_key(address=address, slot=session.slot_index)
        if old is not None and old != key:
            self._tabs.rekey(old, key)
            if self.selected_user == old:
                self.selected_user = key
        self._tabs.ensure_tab(key, address, session.slot_index)
        self._tabs.set_offline(key, False)
        # Learn what the board runs now, so the expected model can be built from
        # it before the user presses Start.
        self._board_mode.refresh(session.slot_index)

    def on_tab_close_requested(self, key: str) -> None:
        """Close button on a tab: disconnect that sensor and close its tab."""
        # Remember it BEFORE disconnecting: stopping the session waits for it, and a reading it had
        # already queued is delivered after this returns — which used to re-create the tab, which
        # the disconnect notice then greyed ("closing only makes it offline").
        self._closed.add(key)
        address = self._tabs.address_of(key)
        if address is not None:
            self._disconnect_device(address)
        self._tabs.remove_tab(key)
        if self.selected_user == key:
            self.selected_user = self._tabs.selected_user
        self.show_current_page()
        self.set_graph_title()

    def on_selection_cleared(self) -> None:
        """The last tab was closed: back to the empty page."""
        self.selected_user = None
        self.show_current_page()

    def on_user_selected(self, user_id: str) -> None:
        """Bring *user_id*'s page forward. Every sensor's page keeps its own history
        against the one shared timeline, so switching loses nothing and moves no
        origin."""
        self.selected_user = user_id
        page = self.page_of_user(user_id)
        self.show_current_page()
        self._board_mode.refresh(self.selected_slot())
        self.apply_csv_mode_view()
        page.redraw()
        self.set_graph_title()
