"""Main application window: the toolbar, the sensor tabs, and the app-level actions.

This module is composition and the handful of actions that cut across the rest —
speed, mode toggles, profile changes. Everything else lives in the piece that owns
it: :class:`AppState` (profiles, settings, modes), :class:`SensorTabs` (tabs +
per-sensor pages), :class:`SensorController` (selection, titles, commands, data),
:class:`SimulationCoordinator` (the local model), :class:`RunController`
(Start/Pause/Stop), :class:`ChildWindows` and :class:`ConfigController`.
"""

from __future__ import annotations

from datetime import UTC, datetime

from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import QMainWindow

from api import protocol
from core.ble_message_log import BleMessageLog
from gui import run_controller
from gui.app_state import AppState
from gui.bluetooth_window import BluetoothWindow
from gui.board_link import BoardLink
from gui.board_mode import BoardMode
from gui.child_windows import ChildWindows, UsersDeps, WindowDeps
from gui.config_controller import ConfigController
from gui.configuration_window import ConfigurationWindow
from gui.instant_events import InstantEvents
from gui.run_clock import RunClock
from gui.run_controller import RunController, RunHooks
from gui.sensor_controller import SensorController
from gui.sensor_directory import SensorDirectory
from gui.sensor_tabs import SensorTabs
from gui.simulation import SimulationCoordinator
from gui.start_push import StartPush
from gui.toolbar import build_toolbar
from gui.user_profile_window import ProfileDeps, UserProfileWindow
from gui.user_profiles import UserProfiles
from gui.user_reader import UserReader
from gui.users_window import UsersWindow
from models.types import PersonProfile, SensorProfile, User


class MainWindow(QMainWindow):
    """Top-level window: builds the pieces, wires their signals, and owns the actions
    that touch several of them (speed, Model Only / CGMS Only, profile changes)."""

    def __init__(self) -> None:
        """Build the state, the sensor tabs, the run controls and the child windows."""
        super().__init__()
        self.setWindowTitle("TCC App")
        self.resize(1100, 750)

        self.state = AppState()
        self.directory = SensorDirectory(
            lambda: self.windows.bluetooth, lambda: self.state.board_layout
        )
        # One write surface over the connected board sessions (issue 18).
        self._board = BoardLink(self.directory.sessions)
        self._ble_log = BleMessageLog(self)

        # The shared timeline, and the sensor tabs: one independent page per sensor
        # (graphs, history, stats, commands) under a browser-style tab strip, or a
        # "connect first" start screen while nothing is connected.
        self._clock = RunClock(datetime.now(UTC), self.state.speed_mult)
        self.tabs = SensorTabs(
            self.state.thresholds,
            self.state.view_window_s,
            self._clock,
            self.directory.label,
            show_points=self.state.show_points,
        )
        self.tabs.show_points_changed.connect(self.state.set_show_points)
        self.sim = SimulationCoordinator(
            self.state,
            self.directory,
            self._clock,
            self.tabs.pages,
            is_csv_slot=lambda slot: self._board_mode.is_csv(slot),
            paused=lambda: self._run.state != run_controller.RUNNING,
            on_reset=lambda: self.sensors.show_current_page(),
            on_slots_changed=self._on_slots_changed,
            parent=self,
        )

        self._build_config()
        self._build_run_controls()

        # What the BOARD says each slot is running (see gui/board_mode.py) — the
        # display's source of truth over the app's own slot map.
        self._board_mode = BoardMode(
            lambda: self.windows.bluetooth,
            on_changed=lambda: self.sensors.on_board_mode_changed(),
            on_became_csv=self.sim.drop_expected_line,
        )
        # One-shot Food / Exercise / PISA → engine pool + board + the target page's shading.
        self._events = InstantEvents(
            self.sim.engines,
            self._board,
            self._clock,
            self.sim.record_pisa_span,
            self._show_status,
        )
        self.sensors = SensorController(
            state=self.state,
            tabs=self.tabs,
            directory=self.directory,
            board_mode=self._board_mode,
            run=self._run,
            events=self._events,
            sim=self.sim,
            clock=self._clock,
            disconnect_device=self._disconnect_device,
            parent=self,
        )
        self._ble_log.new_message.connect(self.sensors.on_new_message)
        self._ble_log.device_disconnected.connect(self.tabs.mark_device_offline)
        self.tabs.connect_requested.connect(lambda: self.windows.open("bluetooth"))

        self.setCentralWidget(self.tabs)
        # Populate the Configuration window's combos now that the graphs exist, then
        # build the first set of engines (choosing a patient no longer does).
        self._controller.notify_profiles_changed()
        self.sim.restart()

    def _build_config(self) -> None:
        """The Configuration window (via its controller) and every child window."""
        self._controller = ConfigController(
            self.state.person_profiles,
            self.state.sensor_profiles,
            lambda: self.state.active_person,
            lambda: self.state.active_sensor,
            lambda: self.state.speed_mult,
        )
        c = self._controller
        c.person_selected.connect(self._on_person_selected)
        c.sensor_selected.connect(self._on_sensor_selected)
        c.speed_change_requested.connect(self._on_speed_changed)
        c.model_only_toggled.connect(self._on_model_only_toggled)
        c.cgms_only_toggled.connect(self._on_cgms_only_toggled)
        c.editor_requested.connect(lambda which: self.windows.open(which))
        c.thresholds_saved.connect(self._on_thresholds_changed)
        c.theme_changed.connect(lambda: self.sensors.rebuild_for_theme())
        c.view_window_changed.connect(self._on_view_window_changed)
        self._configuration_window = ConfigurationWindow(c)
        profiles = UserProfiles(
            ProfileDeps(self.state.users, self.state.save_users, self._on_user_saved), self
        )
        self.windows = ChildWindows(
            WindowDeps(
                ble_log=self._ble_log,
                person_profiles=self.state.person_profiles,
                sensor_profiles=self.state.sensor_profiles,
                person_for_slot=self._person_for_slot,
                is_csv_for_slot=self._is_csv_for_slot,
                on_profiles_changed=self._on_profiles_changed,
                record_slot_assignment=self.record_slot_assignment,
                on_person_selected=self._on_person_selected,
                on_sensor_selected=self._on_sensor_selected,
                on_bluetooth_created=lambda bt: bt.session_connected.connect(
                    self._on_session_ready
                ),
                users=UsersDeps(
                    users=self.state.users,
                    save=self.state.save_users,
                    live_sessions=self._board.live_sessions,
                    reader=UserReader(parent=self),
                    board_busy=lambda: self._board_mode.busy,
                    on_open=profiles.open,
                ),
            )
        )
        self.windows.register("configuration", self._configuration_window)

    def _build_run_controls(self) -> None:
        """The toolbar, with Start/Stop on the left (blocked until a sensor is live)."""
        buttons = build_toolbar(
            self,
            left=(("start_pause_btn", "Start", None), ("stop_btn", "Stop", None)),
            right=(
                ("users_btn", "Users", lambda: self.windows.open("users")),
                ("configuration_btn", "Configuration", lambda: self.windows.open("configuration")),
                ("debug_btn", "Debug", lambda: self.windows.open("debug")),
            ),
        )
        self._start_push = StartPush(self.state, self._board, self._show_status, self)
        self._run = RunController(
            RunHooks(
                board=self._board,
                engines=self.sim.engines,
                model_only=lambda: self.state.model_only,
                restart_engine=self.sim.restart,
                stop_engine=self.sim.stop,
                reset_graph_view=self.sim.reset_graph_view,
                anchor_clock=lambda: self._clock.anchor(datetime.now(UTC)),
                push_config=self._push_config,
                show_status=self._show_status,
            ),
            buttons["start_pause_btn"],
            buttons["stop_btn"],
            self,
        )

    # ------------------------------------------------------------------
    # Small helpers
    # ------------------------------------------------------------------

    def _ensure_bluetooth_window(self) -> BluetoothWindow:
        """The Bluetooth window, created (not shown) if needed — scripts and callbacks use it."""
        return self.windows.ensure_bluetooth()

    def _on_session_ready(self, address: str, session) -> None:
        """A sensor connected: open its tab and make sure a write the board refuses is
        reported. A failed write used to vanish — the Commands panel had already said
        "sent", and the local model moved while the board never saw the event."""
        self.sensors.on_session_connected(address, session)
        session.write_failed.connect(
            lambda _address, char_key, error: self._show_status(
                f"⚠ The board refused '{char_key}' ({error})."
            )
        )

    def _push_config(self, on_done) -> None:
        """Start's first step: write the app's profiles to the board (see StartPush),
        then re-ask each written sensor what it runs so its title and CSV view follow
        the board's own answer rather than what it ran before."""

        def pushed(ok: bool) -> None:
            on_done(ok)
            if ok:
                for slot in self.state.board_plan(self._board.live_slots()):
                    self._board_mode.refresh(slot)

        self._start_push.run(pushed)

    def _on_user_saved(self, user: User) -> None:
        """A profile screen saved *user*: refresh the Users list and say so."""
        users_window = self.windows.get("users")
        if isinstance(users_window, UsersWindow):
            users_window.refresh()
        self._show_status(f'Saved "{user.name}".')

    def _disconnect_device(self, address: str) -> None:
        bt = self.windows.bluetooth
        if bt is not None:
            bt.disconnect_device(address)

    def _show_status(self, message: str) -> None:
        """Put *message* in the status bar — the feedback an inserted event or a
        run-state change has."""
        self.statusBar().showMessage(message, 15000)

    def _on_slots_changed(self) -> None:
        """The engine pool was rebuilt: titles and the CSV view may have changed."""
        self.sensors.set_graph_title()
        self.sensors.apply_csv_mode_view()

    def _person_for_slot(self, slot: int | None) -> PersonProfile | None:
        """The patient the Food / Exercise editors work on for the target device's
        *slot*: the one recorded for that slot, else the active patient."""
        if slot is not None and 0 <= slot < len(self.state.board_layout.slots):
            assigned = self.state.person_by_name(self.state.board_layout.slots[slot].person)
            if assigned is not None:
                return assigned
        return self.state.active_person

    def _is_csv_for_slot(self, slot: int | None, person: PersonProfile | None) -> bool:
        """Whether that sensor replays a CSV: the board's answer when it has given one
        (a person saved as CSV but never sent is not replaying anything), else the
        saved profile."""
        reported = self._board_mode.label(slot)
        if reported is not None:
            return reported == "CSV replay"
        return person is not None and getattr(person, "data_source", "model") == "csv"

    # ------------------------------------------------------------------
    # Settings and profiles
    # ------------------------------------------------------------------

    def _on_thresholds_changed(self) -> None:
        """Reload thresholds after a Configuration-window save and redraw the bands/metrics."""
        self.tabs.set_thresholds(self.state.reload_thresholds())

    def _on_view_window_changed(self) -> None:
        """Reload the graph time-window preference and redraw."""
        self.tabs.pages.set_view_window(self.state.reload_view_window())

    def _on_profiles_changed(self) -> None:
        """Persist profiles to disk and refresh everything that depends on them."""
        self.state.save_profiles()
        # Repopulates the Configuration window's combos, keeping the current
        # selection (signals blocked, so no spurious engine restart) and
        # re-syncs its data-source group — otherwise it stays stale after an
        # assignment made elsewhere (e.g. CSV Analysis → "Assign window to
        # person…").
        self._controller.notify_profiles_changed()
        # Keep the per-person editors' CSV locks in sync when the data source
        # changed here or in CSV Analysis.
        person_window = self.windows.get("person")
        if person_window is not None:
            person_window.reload()
        self._refresh_schedule_windows()
        # Saving a profile changes nothing on the board, so with a board connected
        # the running graphs are left alone; Model Only runs the profile itself.
        if self.state.model_only:
            self.sim.restart()

    def _refresh_schedule_windows(self) -> None:
        """Re-read the Food / Exercise editors for the (possibly new) active person."""
        for key in ("food", "exercise"):
            win = self.windows.get(key)
            if win is not None:
                win.refresh()

    def _on_person_selected(self, person: PersonProfile | None) -> None:
        """Switch the active person and restart the parallel simulation for them.

        Fed by ConfigController.person_selected — the Configuration window's
        Person combo, or a repopulate that had to move the selection.

        Only choosing a patient: nothing has been sent anywhere, so with a board
        connected the graphs are not touched. (They are reset by what actually
        changes the run: Start, a Send to Board, speed, Model Only.) Model Only
        runs the chosen patient locally, so it does restart.
        """
        if person is self.state.active_person:
            return
        self.state.active_person = person
        self._refresh_schedule_windows()
        if self.state.model_only:
            self.sim.restart()

    def _on_sensor_selected(self, sensor: SensorProfile | None) -> None:
        """Switch the active sensor (used only when explicitly sent to a board)."""
        self.state.active_sensor = sensor

    def record_slot_assignment(
        self, slot: int | None, *, person: str | None = None, sensor: str | None = None
    ) -> None:
        """Remember which profiles a just-sent config put on *slot* (see
        AppState.record_slot_assignment), then refresh what depends on it."""
        if not self.state.record_slot_assignment(slot, person=person, sensor=sensor):
            return
        self._on_board_layout_changed()
        # A send can flip what the slot is actually running (e.g. model ->
        # cleared off CSV, or a fresh CSV upload) — re-ask the board rather
        # than leaving the title/food-graph on a stale cached answer until the
        # user happens to re-select the tab.
        self._board_mode.refresh(slot)

    def _on_board_layout_changed(self) -> None:
        """Persist the slot assignments, and refresh the Bluetooth device list so
        a newly-assigned patient name shows there (and in the config windows'
        target combo)."""
        self.state.save_layout()
        self.tabs.refresh_labels()
        if self.windows.bluetooth is not None:
            self.windows.bluetooth.relabel()
        # Slots -> profiles changed: rebuild the engine pool wholesale (issue 04).
        self.sim.restart()

    # ------------------------------------------------------------------
    # Speed, modes and board-wide writes
    # ------------------------------------------------------------------

    def _on_speed_changed(self, multiplier: float) -> None:
        """Set the simulation-speed multiplier and broadcast it to connected boards.

        Also nudges the board's run state to RUNNING (like restart_board()
        does for the config windows' Send to Board) — the speed write alone
        is applied and stored, but a board left paused/stopped won't
        visibly speed up/slow down until it's actually ticking again.
        """
        self.state.set_speed(multiplier)
        self._clock.set_speed(self.state.speed_mult)  # sim-time x-axis scale
        self.sim.restart()  # clears + re-anchors the graph at the new scale
        self._board.broadcast("speed", protocol.encode_speed(self.state.speed_mult))
        self._board.restart_all()
        # Keep the Configuration window's slider/spin in step when the change
        # came from elsewhere (a scenario step); a no-op when it came from them.
        self._controller.set_speed_display(self.state.speed_mult)

    def _on_model_only_toggled(self, checked: bool) -> None:
        """Switch between BLE-driven graphs and pure-model-only graphs."""
        self.state.model_only = checked
        self.tabs.set_model_only(checked)
        self.sim.restart()
        self._run.refresh_enabled()

    def _on_cgms_only_toggled(self, checked: bool) -> None:
        """Switch into/out of CGMS-only mode (see PROTOCOL_SPEC.md).

        On: lock every config-sending control, drop the local model (pure passive
        CGM viewer, no "expected" line), tell every board to stream only standard
        CGM Measurements — without resetting it. Off: reset+stop the board, unlock.
        """
        self.state.cgms_only = checked
        if checked:
            if self.state.model_only:
                # Mutually exclusive with CGMS Only — flip it off without
                # letting _on_model_only_toggled transiently spin up an
                # engine we're about to stop anyway (the window re-syncs its
                # checkbox under its own _syncing guard, so no echo).
                self._controller.set_model_only_display(False)
                self.state.model_only = False
                self.tabs.set_model_only(False)
            self.sim.stop()
        self._board.broadcast("cgms_only", protocol.encode_cgms_only(checked))
        self._run.reset_to_stopped()
        self.sim.reset_graph_view()
        self.sensors.show_current_page()
        # Disable every control that would send a now-rejected config write.
        self._controller.set_controls_locked(checked)
        self._run.set_locked(checked)

    # ------------------------------------------------------------------
    # Qt overrides
    # ------------------------------------------------------------------

    def closeEvent(self, event: QCloseEvent) -> None:
        """Close the profile screens (each may ask about unsaved changes — Cancel keeps the
        app open), stop the engine and any live BLE session, then close every child window."""
        for screen in self.findChildren(UserProfileWindow):
            if not screen.close():
                event.ignore()
                return
        self.sim.stop()
        self.windows.close_all()
        super().closeEvent(event)
