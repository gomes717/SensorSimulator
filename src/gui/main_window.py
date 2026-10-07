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
from PyQt6.QtWidgets import QApplication, QMainWindow

from api import protocol
from core.ble_message_log import BleMessageLog
from gui import run_controller, user_picture
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
from gui.user_profile_window import ProfileDeps, SendDeps, UserProfileWindow
from gui.user_profiles import UserProfiles
from gui.user_reader import UserReader
from gui.user_sender import UserSender
from gui.users_window import UsersWindow
from gui.widgets import CenterOnMain
from models import board_layout
from models.types import User


class MainWindow(QMainWindow):
    """Top-level window: builds the pieces, wires their signals, and owns the actions
    that touch several of them (speed, Model Only / CGMS Only, profile changes)."""

    def __init__(self) -> None:
        """Build the state, the sensor tabs, the run controls and the child windows."""
        super().__init__()
        self.setWindowTitle("TCC App")
        self.resize(1100, 750)
        # Every other window and dialog opens centred on this one.
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(CenterOnMain(self))

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
            picture_for=self._tab_picture,
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
        self.sim.restart()

    def _build_config(self) -> None:
        """The Configuration window (via its controller) and every child window."""
        self._controller = ConfigController(lambda: self.state.speed_mult)
        c = self._controller
        c.speed_change_requested.connect(self._on_speed_changed)
        c.model_only_toggled.connect(self._on_model_only_toggled)
        c.cgms_only_toggled.connect(self._on_cgms_only_toggled)
        c.thresholds_saved.connect(self._on_thresholds_changed)
        c.theme_changed.connect(lambda: self.sensors.rebuild_for_theme())
        c.view_window_changed.connect(self._on_view_window_changed)
        self._configuration_window = ConfigurationWindow(c)
        sender = UserSender(parent=self)
        profiles = UserProfiles(
            ProfileDeps(
                self.state.users,
                self.state.save_users,
                self._on_user_saved,
                send=SendDeps(
                    live_sessions=self._board.live_sessions,
                    send=sender.send,
                    on_sent=self._on_user_sent,
                    describe=self._describe_sensor,
                ),
            ),
            self,
        )
        self.windows = ChildWindows(
            WindowDeps(
                ble_log=self._ble_log,
                on_bluetooth_created=lambda bt: bt.session_connected.connect(
                    self._on_session_ready
                ),
                users=UsersDeps(
                    users=self.state.users,
                    save=self.state.save_users,
                    live_sessions=self._board.live_sessions,
                    reader=UserReader(parent=self),
                    board_busy=lambda: self._board_mode.busy,
                    on_open=lambda user, draft: self._open_user(profiles, user, draft),
                    on_deleted=self._on_user_deleted,
                    label=self._sensor_label,
                    where=self._where_is,
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

    def _open_user(self, profiles: UserProfiles, user: User, is_draft: bool) -> None:
        """Open *user*'s profile screen; a saved user becomes the one Model Only runs."""
        if not is_draft:
            self._activate_user(user)
        profiles.open(user, is_draft)

    def _activate_user(self, user: User) -> None:
        """Make *user* the active one (Model Only runs it; a lone board with nothing assigned too)."""
        if self.state.active_user is user:
            return
        self.state.active_user = user
        if self.state.model_only:
            self.sim.restart()

    def _on_user_saved(self, user: User, previous_name: str | None) -> None:
        """A profile screen saved *user*: follow a rename on its slot, refresh the Users list, the
        sensor labels and (in Model Only, where the profile itself runs) the engine."""
        if previous_name is not None and previous_name != user.name:
            self.state.rename_user_in_layout(previous_name, user.name)
            self.state.save_layout()
        if self.state.active_user is not None and self.state.active_user.id == user.id:
            self.state.active_user = user  # the list holds the saved copy now
        users_window = self.windows.get("users")
        if isinstance(users_window, UsersWindow):
            users_window.refresh()
        self._refresh_sensor_labels()
        # Saving changes nothing on the board, so with a board connected the running graphs are
        # left alone; Model Only runs the user itself.
        if self.state.model_only:
            self.sim.restart()
        self._show_status(f'Saved "{user.name}".')

    def _describe_sensor(self, session) -> str:
        """How the Send chooser names a sensor: its number and the user recorded on it."""
        slot = session.slot_index if session.slot_index is not None else 0
        running = (
            self.state.board_layout.slots[slot].person if slot < board_layout.MAX_SLOTS else None
        )
        return (
            f"Sensor {slot + 1} — now running {running}"
            if running
            else f"Sensor {slot + 1} — no user"
        )

    def _on_user_sent(self, slot: int, user: User, message: str) -> None:
        """A user landed on *slot*'s sensor: record it (which also restarts the expected line and
        re-asks the board what it runs) and say so."""
        self.record_slot_assignment(slot, person=user.name)
        self._show_status(message)

    def _on_user_deleted(self, user: User) -> None:
        """A user was deleted from the Users window: take it off its slot. Only a user the run was
        using restarts it — deleting one nothing runs must not reset the graphs."""
        was_running = self.state.forget_user(user)
        self.state.save_layout()
        self._refresh_sensor_labels()
        if was_running:
            self.sim.restart()

    def _refresh_sensor_labels(self) -> None:
        """Show the users' current names and pictures on the tabs, in the Bluetooth list and in
        the Users list (which says which sensor each user is on)."""
        self.tabs.refresh_labels()
        if self.windows.bluetooth is not None:
            self.windows.bluetooth.relabel()
        users_window = self.windows.get("users")
        if isinstance(users_window, UsersWindow):
            users_window.refresh()

    def _sensor_label(self, session) -> str:
        """A sensor's current name: its user and number ("Rafael — Sensor 1"), or its advertised
        name while no user is recorded on it. (The session's own name is frozen at connect.)"""
        if session.slot_index is None:
            return session.user_id
        return board_layout.device_label(
            board_layout.advert_name(session.slot_index), self.state.board_layout
        )

    def _where_is(self, user: User) -> str:
        """Which sensor(s) the slot record has *user* on: "Sensor 3", "Sensors 1, 3" or ""."""
        slots = [
            str(i + 1) for i, s in enumerate(self.state.board_layout.slots) if s.person == user.name
        ]
        if not slots:
            return ""
        return f"Sensor{'s' if len(slots) > 1 else ''} {', '.join(slots)}"

    def _tab_picture(self, key: str, _address: str | None):
        """The picture of the user on the sensor behind tab *key*, or None (initials disc)."""
        slot = self.directory.slot_of_user(key)
        if slot is None or not 0 <= slot < len(self.state.board_layout.slots):
            return None
        user = self.state.user_by_name(self.state.board_layout.slots[slot].person)
        return user_picture.stored_pixmap(user, 22) if user is not None else None

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

    # ------------------------------------------------------------------
    # Settings, users and slots
    # ------------------------------------------------------------------

    def _on_thresholds_changed(self) -> None:
        """Reload thresholds after a Configuration-window save and redraw the bands/metrics."""
        self.tabs.set_thresholds(self.state.reload_thresholds())

    def _on_view_window_changed(self) -> None:
        """Reload the graph time-window preference and redraw."""
        self.tabs.pages.set_view_window(self.state.reload_view_window())

    def record_slot_assignment(self, slot: int | None, *, person: str | None = None) -> None:
        """Remember which user a just-sent config put on *slot* (see
        AppState.record_slot_assignment), then refresh what depends on it."""
        if not self.state.record_slot_assignment(slot, person=person):
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
        self._refresh_sensor_labels()
        # Slots -> users changed: rebuild the engine pool wholesale (issue 04).
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
