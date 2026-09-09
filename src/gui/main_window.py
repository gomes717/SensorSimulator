"""Main application window: toolbar, user treeview, glucose graph, food/exercise graph,
and the person/sensor/mode selector bar."""

from __future__ import annotations

from datetime import UTC, datetime

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from api import protocol
from core.ble_message_log import BleMessageLog
from gui.bluetooth_window import BluetoothWindow
from gui.board_layout_window import BoardLayoutWindow
from gui.board_link import BoardLink
from gui.config_controller import ConfigController
from gui.configuration_window import ConfigurationWindow
from gui.csv_analysis_window import CsvAnalysisWindow
from gui.debug_window import DebugWindow
from gui.exercise_config_window import ExerciseConfigWindow
from gui.fault_panel import FaultPanel
from gui.food_config_window import FoodConfigWindow
from gui.glucose_graph import GlucoseGraph
from gui.instant_events import InstantEvents
from gui.person_config_window import PersonConfigWindow
from gui.range_stats import RangeStatsPanel
from gui.scenario_dispatch import ScenarioDispatch
from gui.scenario_window import ScenarioWindow
from gui.sensor_config_window import SensorConfigWindow
from gui.user_tree import UserTree
from gui.view_config_window import ViewConfigWindow
from models import app_settings, board_layout, cambridge, profile_store
from models import sensors as sensor_defaults
from models.engine import EnginePool
from models.types import ModelId, PersonProfile, SensorId, SensorProfile


class MainWindow(QMainWindow):  # pylint: disable=too-many-instance-attributes  # see issue 18
    """Top-level window. Owns the app state (profiles, thresholds, run state, the
    per-user history) and wires together the extracted pieces: :class:`UserTree`
    (sensor list), :class:`GlucoseGraph` (the two plots), :class:`ConfigController`
    (⇄ the Configuration window), :class:`BoardLink` (board writes),
    :class:`EnginePool` (the local "expected" model) and :class:`InstantEvents`.
    """

    def __init__(self) -> None:
        """Set up the toolbar, BLE message log, treeview, graphs, selector bar, and layout."""
        super().__init__()
        self.setWindowTitle("TCC App")
        self.resize(1100, 750)

        self._setup_toolbar()
        self._debug_window: DebugWindow | None = None
        self._bluetooth_window: BluetoothWindow | None = None
        self._person_config_window: PersonConfigWindow | None = None
        self._sensor_config_window: SensorConfigWindow | None = None
        self._food_config_window: FoodConfigWindow | None = None
        self._exercise_config_window: ExerciseConfigWindow | None = None
        self._board_layout_window: BoardLayoutWindow | None = None
        self._csv_analysis_window: CsvAnalysisWindow | None = None
        self._view_config_window: ViewConfigWindow | None = None
        self._fault_panel: FaultPanel | None = None
        self._scenario_window: ScenarioWindow | None = None

        # One write surface over the connected board sessions (issue 18).
        self._board = BoardLink(
            lambda: self._bluetooth_window.sessions() if self._bluetooth_window else {}
        )
        self._ble_log = BleMessageLog(self)
        self._ble_log.new_message.connect(self._on_new_message)
        # device_disconnected is wired to the sensor list once it exists, below.

        self._person_profiles, self._sensor_profiles = profile_store.load()
        self._seed_default_profiles()
        # slot -> (person, sensor) for the multi-sensor board (data/board_layout.json).
        self._board_layout = board_layout.load()
        self._active_person: PersonProfile | None = None
        self._active_sensor: SensorProfile | None = None
        # Continuous sim-speed multiplier x1..x1000; applied to dt_min + sent to the board.
        self._speed_mult = float(app_settings.load_pref("speed_mult", 1.0))
        # Rolling view: show only the last N wall-clock seconds (0 = entire run).
        self._view_window_s = float(app_settings.load_pref("view_window_s", 3600.0))
        self._model_only = False
        self._cgms_only = False
        # One SimulationEngine per occupied slot (issue 04); slot 0 alone otherwise.
        self._engines = EnginePool(self)
        self._engines.expected_reading.connect(self._on_expected_reading)
        # True while the pool drives >1 slot: expected lines then live per-user
        # in self._history["ex_g*"], not the single graph.expected_* buffer.
        self._per_slot_expected = False
        self._run_state = "stopped"  # "stopped" | "running" | "paused"

        # Range thresholds (mg/dL) for the graph bands + the alert badges + metrics.
        self._thresholds = app_settings.load()
        # Built eagerly (hidden) so its combos exist for notify_profiles_changed()
        # at the end of __init__, once the graphs the first selection touches are up.
        self._controller = ConfigController(
            self._person_profiles,
            self._sensor_profiles,
            lambda: self._active_person,
            lambda: self._active_sensor,
            lambda: self._speed_mult,
        )
        c = self._controller
        c.person_selected.connect(self._on_person_selected)
        c.sensor_selected.connect(self._on_sensor_selected)
        c.speed_change_requested.connect(self._on_speed_changed)
        c.model_only_toggled.connect(self._on_model_only_toggled)
        c.cgms_only_toggled.connect(self._on_cgms_only_toggled)
        c.comm_profile_toggled.connect(self._on_comm_profile_toggled)
        c.editor_requested.connect(self._open_editor)
        c.thresholds_saved.connect(self._on_thresholds_changed)
        self._configuration_window = ConfigurationWindow(c)

        # The left-hand sensor list owns its own rows + offline state (issue 18)
        # and reports the selected user_id back.
        self.tree = UserTree(self._thresholds)
        self.tree.user_selected.connect(self._on_user_selected)
        self._ble_log.device_disconnected.connect(self.tree.mark_device_offline)
        self._selected_user: str | None = None
        # The two stacked plots (issue 18): GlucoseGraph keeps the fixed graph_t0
        # x-axis origin, the plot buffers and every draw decision.
        self._graph = GlucoseGraph(
            self._thresholds,
            self._view_window_s,
            datetime.now(UTC),
            speed_mult=self._speed_mult,
            on_redraw=self._update_stats_panel,
            fe_title=self._fe_graph_title,
        )

        # Per-user received history, all sharing the one graph_t0 (re)anchored at
        # Start; switching the selected row just rebinds the graph's live buffers
        # onto a user's lists (see _bind_selected_history). Keyed by user_id.
        # {user_id: {"gx","gy","fx","fc","fe","ex_gx","ex_gy": list[float]}}
        self._history: dict[str, dict[str, list[float]]] = {}

        # One-shot "insert now" events → engine pool + board + graph shading (issue 18).
        self._events = InstantEvents(self._engines, self._board, self._graph, self._board_layout)
        # Scenario-step vocabulary (models.scenario.ScenarioRunner owns the timing).
        self._scenario = ScenarioDispatch(self, self._events)

        self._bottom = self._build_bottom_bar()

        right_splitter = self._right_splitter = QSplitter(Qt.Orientation.Vertical)
        right_splitter.addWidget(self._graph.canvas)
        right_splitter.addWidget(self._graph.fe_canvas)
        right_splitter.addWidget(self._bottom)
        right_splitter.setSizes([340, 170, 130])

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.tree)
        splitter.addWidget(right_splitter)
        splitter.setSizes([300, 800])
        # setSizes() is only a hint; give the tree a real floor + no stretch so a
        # narrow window squeezes the graph side, not the tree, down to a sliver.
        self.tree.setMinimumWidth(220)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setCollapsible(0, False)

        self.setCentralWidget(splitter)

        # Populate the Configuration window's combos now that the graphs exist
        # (the first selection default restarts the engine, which draws to them).
        self._controller.notify_profiles_changed()

    # -- Graph state now lives on self._graph (issue 18). These read-only views
    #    keep the hardware harnesses (scripts/e2e.py, scripts/ui_smoke.py), which
    #    poll the plotted series directly, working unchanged.
    @property
    def _graph_x(self) -> list[float]:
        return self._graph.buf.graph_x

    @property
    def _graph_y(self) -> list[float]:
        return self._graph.buf.graph_y

    @property
    def _expected_x(self) -> list[float]:
        return self._graph.buf.expected_x

    @property
    def _expected_y(self) -> list[float]:
        return self._graph.buf.expected_y

    @property
    def _food_ex_carbs_y(self) -> list[float]:
        return self._graph.buf.food_ex_carbs_y

    @property
    def _food_ex_exercise_y(self) -> list[float]:
        return self._graph.buf.food_ex_exercise_y

    @property
    def _pisa_spans(self) -> list[tuple[float, float]]:
        return self._graph.pisa_spans

    @property
    def _pisa_patches(self) -> list:
        return self._graph.pisa_patches

    @property
    def _visible_xlim(self) -> tuple[float, float] | None:
        return self._graph.visible_xlim

    def _in_view(self, xs: list[float], ys: list[float]) -> list[float]:
        return self._graph.in_view(xs, ys)

    # Sensor-list state now lives on self.tree (issue 18); these keep the
    # issue-06 regression test + scripts/e2e_4sensor.py reading it as before.
    @property
    def _user_items(self) -> dict:
        return self.tree._items

    @property
    def _offline_users(self) -> set:
        return self.tree._offline

    @property
    def _stat_value_labels(self) -> dict:
        return self._stats_panel.labels

    def _seed_default_profiles(self) -> None:
        """First run (no saved profiles yet): add one default person/sensor.

        Without this, Model Only mode and the selector bar start empty and
        show nothing until the user manually opens Configure and builds a
        profile from scratch — seeding one makes the graph show data
        immediately.
        """
        changed = False
        if not self._person_profiles:
            self._person_profiles.append(
                PersonProfile(
                    name="Sample Patient",
                    model_id=ModelId.CAMBRIDGE,
                    params=cambridge.default_params(),
                )
            )
            changed = True
        if not self._sensor_profiles:
            self._sensor_profiles.append(
                SensorProfile(
                    name="Sample Sensor",
                    sensor_id=SensorId.IDEAL,
                    params=sensor_defaults.ideal_default_params(),
                )
            )
            changed = True
        if changed:
            profile_store.save(self._person_profiles, self._sensor_profiles)

    # ------------------------------------------------------------------
    # Builder helpers
    # ------------------------------------------------------------------

    _TOOLBAR = (
        ("view_btn", "View", "_open_view_config"),
        ("csv_analysis_btn", "CSV Analysis", "_open_csv_analysis"),
        ("configuration_btn", "Configuration", "_open_configuration"),
        ("faults_btn", "Faults", "_open_faults"),
        ("scenario_btn", "Scenario", "_open_scenario"),
        ("bluetooth_btn", "Connect Bluetooth", "_open_bluetooth"),
        ("debug_btn", "Debug", "_open_debug"),
    )

    def _setup_toolbar(self) -> None:
        """Create the top toolbar — all buttons right-aligned past a stretch spacer."""
        toolbar = QToolBar()
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)
        for attr, label, handler in self._TOOLBAR:
            btn = QPushButton(label)
            btn.clicked.connect(getattr(self, handler))
            toolbar.addWidget(btn)
            setattr(self, attr, btn)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, toolbar)

    def _build_bottom_bar(self) -> QWidget:
        """Create the run controls (Start/Pause, Stop, Insert Now) and the live
        range-metrics panel for the selected user.

        The Person/Sensor selectors and mode toggles that used to live here
        moved to the Configuration window (toolbar → Configuration).
        """
        bar = QWidget()
        outer = QVBoxLayout(bar)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        controls_row = QHBoxLayout()
        controls_row.setSpacing(8)

        self._start_pause_btn = QPushButton("Start")
        self._start_pause_btn.setMinimumWidth(90)
        self._start_pause_btn.clicked.connect(self._on_start_pause_clicked)
        controls_row.addWidget(self._start_pause_btn)
        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setMinimumWidth(90)
        self._stop_btn.clicked.connect(self._on_stop_clicked)
        controls_row.addWidget(self._stop_btn)

        controls_row.addSpacing(24)

        self._insert_food_btn = QPushButton("Insert Food Now…")
        self._insert_food_btn.clicked.connect(self._open_insert_food)
        controls_row.addWidget(self._insert_food_btn)
        self._insert_exercise_btn = QPushButton("Insert Exercise Now…")
        self._insert_exercise_btn.clicked.connect(self._open_insert_exercise)
        controls_row.addWidget(self._insert_exercise_btn)
        self._insert_pisa_btn = QPushButton("Insert PISA Now…")
        self._insert_pisa_btn.clicked.connect(self._open_insert_pisa)
        controls_row.addWidget(self._insert_pisa_btn)

        controls_row.addStretch(1)
        outer.addLayout(controls_row)

        self._stats_panel = RangeStatsPanel()
        outer.addWidget(self._stats_panel)
        return bar

    def _update_stats_panel(self) -> None:
        """Feed the range-metrics panel the glucose series *currently in view*."""
        g, b = self._graph, self._graph.buf
        series = g.in_view(b.graph_x, b.graph_y) or g.in_view(b.expected_x, b.expected_y)
        span_min = None
        if g.visible_xlim is not None:
            lo, hi = g.visible_xlim
            span_min = max(0.0, (hi - lo) / 60.0)
        self._stats_panel.refresh(series, span_min, self._thresholds)

    # ------------------------------------------------------------------
    # Window management
    # ------------------------------------------------------------------

    def _ensure_bluetooth_window(self) -> BluetoothWindow:
        """Lazily create the Bluetooth window without showing it — used by DeviceTargetBar."""
        if self._bluetooth_window is None:
            self._bluetooth_window = BluetoothWindow(self._ble_log)
        return self._bluetooth_window

    @staticmethod
    def _raise(win) -> None:
        win.show()
        win.raise_()
        win.activateWindow()

    def _lazy_window(self, attr: str, factory):
        """Return self.<attr>, building it with *factory* on first access."""
        win = getattr(self, attr)
        if win is None:
            win = factory()
            setattr(self, attr, win)
        return win

    def _open_debug(self) -> None:
        self._raise(self._lazy_window("_debug_window", lambda: DebugWindow(self._ble_log)))

    def _open_bluetooth(self) -> None:
        self._raise(self._ensure_bluetooth_window())

    def _open_configuration(self) -> None:
        self._raise(self._configuration_window)

    def _open_csv_analysis(self) -> None:
        self._raise(
            self._lazy_window(
                "_csv_analysis_window",
                lambda: CsvAnalysisWindow(self._person_profiles, self._on_csv_window_assigned),
            )
        )

    def _open_faults(self) -> None:
        self._raise(self._lazy_window("_fault_panel", lambda: FaultPanel(self)))

    def _open_scenario(self) -> None:
        self._raise(self._lazy_window("_scenario_window", lambda: ScenarioWindow(self)))

    def _open_view_config(self) -> None:
        self._raise(
            self._lazy_window(
                "_view_config_window",
                lambda: ViewConfigWindow(self._on_theme_changed, self._on_view_window_changed),
            )
        )

    def _on_theme_changed(self) -> None:
        """After a palette switch: rebuild the graph canvases so they repaint in
        the new colors (they read the Qt palette only at build time)."""
        self._graph.rebuild_for_theme(self._right_splitter)
        self._set_graph_title()
        self._apply_csv_mode_view()

    def _set_graph_title(self) -> None:
        """Set the glucose-graph title for the current mode / selection."""
        if self._model_only:
            name = self._active_person.name if self._active_person is not None else None
            self._graph.set_glucose_title(
                f"Model — {name}" if name else "Model Only — select a person"
            )
        elif self._selected_user:
            self._graph.set_glucose_title(f"Glucose — {self._selected_user}")

    def _on_thresholds_changed(self) -> None:
        """Reload thresholds after a Configuration-window save and redraw the bands/metrics."""
        self._thresholds = app_settings.load()
        self._graph.set_thresholds(self._thresholds)
        self.tree.set_thresholds(self._thresholds)

    def _open_editor(self, which: str) -> None:
        """Route a ConfigController.editor_requested to the right config window."""
        {
            "person": self._open_person_config,
            "food": self._open_food_config,
            "exercise": self._open_exercise_config,
            "sensor": self._open_sensor_config,
            "board_layout": self._open_board_layout,
        }[which]()

    def _on_comm_profile_toggled(self, dexcom: bool) -> None:
        """Write the chosen BLE comm profile to every connected board, then let each
        board drop the link and re-advertise, and reconnect to it automatically.

        Moved out of ConfigurationWindow with issue 18 — it needs the Bluetooth
        window (sessions + reconnect), which is MainWindow's to hand out.
        """
        bt = self._ensure_bluetooth_window()
        sessions = bt.sessions()
        if not sessions:
            return
        payload = protocol.encode_comm_profile(dexcom)
        for address, session in list(sessions.items()):
            session.queue_write("comm_profile", payload)
            bt.reconnect(address)

    def _open_person_config(self) -> None:
        self._raise(
            self._lazy_window(
                "_person_config_window",
                lambda: PersonConfigWindow(
                    self._person_profiles, self._on_profiles_changed, self._ensure_bluetooth_window
                ),
            )
        )

    def _open_sensor_config(self) -> None:
        self._raise(
            self._lazy_window(
                "_sensor_config_window",
                lambda: SensorConfigWindow(
                    self._sensor_profiles, self._on_profiles_changed, self._ensure_bluetooth_window
                ),
            )
        )

    def _open_food_config(self) -> None:
        self._raise(
            self._lazy_window(
                "_food_config_window",
                lambda: FoodConfigWindow(
                    lambda: self._active_person,
                    self._on_profiles_changed,
                    self._ensure_bluetooth_window,
                ),
            )
        )

    def _open_exercise_config(self) -> None:
        self._raise(
            self._lazy_window(
                "_exercise_config_window",
                lambda: ExerciseConfigWindow(
                    lambda: self._active_person,
                    self._on_profiles_changed,
                    self._ensure_bluetooth_window,
                ),
            )
        )

    def _open_board_layout(self) -> None:
        """Open (or raise) the Board Layout window; refresh its profiles if it already exists."""
        existed = self._board_layout_window is not None
        win = self._lazy_window(
            "_board_layout_window",
            lambda: BoardLayoutWindow(
                self._person_profiles,
                self._sensor_profiles,
                self._board_layout,
                self._on_board_layout_changed,
                self._ensure_bluetooth_window,
            ),
        )
        if existed:
            win.reload_profiles()
        self._raise(win)

    def _on_board_layout_changed(self) -> None:
        """Persist the slot assignments after a Board Layout window edit, and
        refresh the Bluetooth device list so a newly-assigned patient name shows
        there (and in the config windows' target combo)."""
        board_layout.save(self._board_layout)
        if self._bluetooth_window is not None:
            self._bluetooth_window.relabel()
        # Slots -> profiles changed: rebuild the engine pool wholesale (issue 04).
        self._restart_engine()

    def _multi_slot_count(self) -> int:
        """4 if a connected identity is one slot of a multi-sensor board, else 1."""
        return 4 if self._board.multi_slot() else 1

    # One-shot "insert now" events live in gui/instant_events.py (issue 18);
    # these thin wrappers keep the toolbar buttons, FaultPanel and the hardware
    # harnesses calling the same names.
    def _open_insert_food(self) -> None:
        self._events.prompt_food(self)

    def _open_insert_exercise(self) -> None:
        self._events.prompt_exercise(self)

    def _open_insert_pisa(self) -> None:
        self._events.prompt_pisa(self)

    def inject_fault(self, kind: str, values: tuple, slot: int | None = None) -> None:
        self._events.inject_fault(kind, values, slot)

    # ------------------------------------------------------------------
    # Scenario runner dispatch (gui/scenario_window.py)
    # ------------------------------------------------------------------

    def _scenario_dispatch(self, kind: str, args: dict) -> str:
        """Kept for scripts/e2e.py; the vocabulary lives in ScenarioDispatch (issue 18)."""
        return self._scenario.dispatch(kind, args)

    # ------------------------------------------------------------------
    # Person/sensor selection and profile persistence
    # ------------------------------------------------------------------

    def _on_csv_window_assigned(self, person: PersonProfile) -> None:
        """CSV Analysis assigned a 24 h window to *person* — make them the active
        person so the Configuration window's data-source group and the graph
        immediately reflect the new CSV source, then persist + refresh."""
        match = next(
            (p for p in self._person_profiles if p is person or p.name == person.name), None
        )
        if match is not None:
            self._on_person_selected(match)
        self._on_profiles_changed()

    def _on_profiles_changed(self) -> None:
        """Persist profiles to disk and refresh everything that depends on them."""
        profile_store.save(self._person_profiles, self._sensor_profiles)
        # Repopulates the Configuration window's combos, keeping the current
        # selection (signals blocked, so no spurious engine restart) and
        # re-syncs its data-source group — otherwise it stays stale after an
        # assignment made elsewhere (e.g. CSV Analysis → "Assign window to
        # person…").
        self._controller.notify_profiles_changed()
        if self._board_layout_window is not None:
            self._board_layout_window.reload_profiles()
        # Keep the per-person editors' CSV locks in sync when the data source
        # changed here or in CSV Analysis.
        if self._person_config_window is not None:
            self._person_config_window.reload()
        for win in (self._food_config_window, self._exercise_config_window):
            if win is not None:
                win.refresh()
        self._restart_engine()

    def _on_person_selected(self, person: PersonProfile | None) -> None:
        """Switch the active person and restart the parallel simulation for them.

        Fed by ConfigController.person_selected — the Configuration window's
        Person combo, or a repopulate that had to move the selection.
        """
        if person is self._active_person:
            return
        self._active_person = person
        self._restart_engine()

    def _on_sensor_selected(self, sensor: SensorProfile | None) -> None:
        """Switch the active sensor (used only when explicitly sent to a board)."""
        self._active_sensor = sensor

    # ------------------------------------------------------------------
    # Mode toggles
    # ------------------------------------------------------------------

    def _on_speed_changed(self, multiplier: float) -> None:
        """Set the simulation-speed multiplier and broadcast it to connected boards.

        Also nudges the board's run state to RUNNING (like restart_board()
        does for the config windows' Send to Board) — the speed write alone
        is applied and stored, but a board left paused/stopped won't
        visibly speed up/slow down until it's actually ticking again.
        """
        self._speed_mult = max(1.0, min(1000.0, float(multiplier)))
        app_settings.save_pref("speed_mult", self._speed_mult)
        self._graph.set_speed_mult(self._speed_mult)  # sim-time x-axis scale
        self._restart_engine()  # clears + re-anchors the graph at the new scale
        self._board.broadcast("speed", protocol.encode_speed(self._speed_mult))
        self._board.restart_all()
        # Keep the Configuration window's slider/spin in step when the change
        # came from elsewhere (a scenario step); a no-op when it came from them.
        self._controller.set_speed_display(self._speed_mult)

    def _on_model_only_toggled(self, checked: bool) -> None:
        """Switch between BLE-driven graphs and pure-model-only graphs."""
        self._model_only = checked
        self.tree.setEnabled(not checked)
        self._restart_engine()

    def _send_cgms_only(self, enabled: bool) -> None:
        """Broadcast the CGMS-only toggle to every connected board (see PROTOCOL_SPEC.md)."""
        self._board.broadcast("cgms_only", protocol.encode_cgms_only(enabled))

    def _set_locked_for_cgms_only(self, locked: bool) -> None:
        """Disable every control that would send a now-rejected config write. The
        Configuration window locks its own off this signal; here just MainWindow's.
        """
        self._controller.set_controls_locked(locked)
        for widget in (
            self._start_pause_btn,
            self._stop_btn,
            self._insert_food_btn,
            self._insert_exercise_btn,
            self._insert_pisa_btn,
        ):
            widget.setEnabled(not locked)

    def _on_cgms_only_toggled(self, checked: bool) -> None:
        """Switch into/out of CGMS-only mode (see PROTOCOL_SPEC.md).

        On: lock every config-sending control, drop the local model (pure passive
        CGM viewer, no "expected" line), tell every board to stream only standard
        CGM Measurements — without resetting it. Off: reset+stop the board, unlock.
        """
        self._cgms_only = checked
        if checked:
            if self._model_only:
                # Mutually exclusive with CGMS Only — flip it off without
                # letting _on_model_only_toggled transiently spin up an
                # engine we're about to stop anyway (the window re-syncs its
                # checkbox under its own _syncing guard, so no echo).
                self._controller.set_model_only_display(False)
                self._model_only = False
                self.tree.setEnabled(True)
            self._stop_engine()
            self._send_cgms_only(True)
        else:
            self._send_cgms_only(False)
        self._run_state = "stopped"
        self._reset_graph_view()
        self._set_start_pause_label()
        self._set_locked_for_cgms_only(checked)

    # ------------------------------------------------------------------
    # Simulation engine (parallel "expected" model)
    # ------------------------------------------------------------------

    def _reset_graph_view(self) -> None:
        """Clear every graph, drop all per-user history, and re-anchor the shared
        timeline at t=0. Every genuine restart (Start/Stop, switch person/sensor/
        mode, save a profile) goes through here — the one shared reset point that
        keeps the received and expected lines on the same origin. A row switch
        does not (see _on_user_selected); it only rebinds to kept history.
        """
        self._graph.reset(datetime.now(UTC))
        self._history = {}
        self._bind_selected_history()
        self._graph.redraw_glucose()
        self._graph.redraw_food_ex()

    def _hist(self, user_id: str) -> dict[str, list[float]]:
        """Return (creating on first sight) the history buffers for *user_id*.

        ``gx``/``gy`` + ``fx``/``fc``/``fe`` are the received (board) stream;
        ``ex_gx``/``ex_gy`` are that slot's local "expected" line (issue 04).
        """
        h = self._history.get(user_id)
        if h is None:
            h = {"gx": [], "gy": [], "fx": [], "fc": [], "fe": [], "ex_gx": [], "ex_gy": []}
            self._history[user_id] = h
        return h

    def _bind_selected_history(self) -> None:
        """Point the live plot buffers at the selected user's history lists.

        The redraw helpers read self._graph_x/_y and self._food_ex_* directly,
        so aliasing them onto the selected user's history dict entry means
        appends in _on_new_message and a row switch both "just work" without
        copying. In Model Only mode there is no BLE user — the engine owns
        these buffers instead — so leave them alone.
        """
        if self._model_only or not self._selected_user:
            return
        h = self._hist(self._selected_user)
        ex = (h["ex_gx"], h["ex_gy"]) if self._per_slot_expected else (None, None)
        self._graph.bind_buffers(h["gx"], h["gy"], h["fx"], h["fc"], h["fe"], *ex)

    def _stop_engine(self) -> None:
        """Stop and discard every engine in the pool.

        EnginePool.stop_all() disconnects each engine's expected_reading before
        stopping it: a QThread emits on a queued connection, so a last tick fired
        as the engine is interrupted would otherwise arrive after this restart
        sequence finishes — landing at the wrong x against the new self._graph_t0.
        """
        self._engines.stop_all()

    def _person_by_name(self, name: str | None) -> PersonProfile | None:
        if not name:
            return None
        return next((p for p in self._person_profiles if p.name == name), None)

    def _engine_slots(self) -> dict[int, PersonProfile]:
        """slot -> profile for the engine pool. Per-slot when a multi-sensor
        board layout has assignments (and not in Model Only); otherwise a single
        slot 0 for the active person."""
        if not self._model_only:
            assigned = {
                i: self._person_by_name(s.person)
                for i, s in enumerate(self._board_layout.slots)
                if s.person
            }
            assigned = {i: p for i, p in assigned.items() if p is not None}
            if assigned:
                return assigned
        if self._active_person is not None:
            return {0: self._active_person}
        return {}

    def _restart_engine(self) -> None:
        """Stop the pool, reset the graphs, and rebuild one engine per slot."""
        self._stop_engine()
        self._reset_graph_view()

        slots = self._engine_slots()
        self._per_slot_expected = len(slots) > 1
        self._set_graph_title()
        self._apply_csv_mode_view()
        if not slots:
            return

        # A freshly rebuilt pool sits idle unless a run is already in progress,
        # so switching profiles/modes/layout doesn't silently start a comparison.
        # CSV replay only in Model Only mode; with a board connected the expected
        # line stays a live model prediction fed by the board's Food/Exercise
        # Status, whatever the profile's data source says (issue 16 / user
        # report: picking "CSV region" must not desync the two lines until it's
        # actually sent to the board).
        self._engines.rebuild(
            slots,
            self._speed_mult,
            paused=self._run_state != "running",
            allow_csv=self._model_only,
        )

    def _apply_csv_mode_view(self) -> None:
        """In CSV replay (Model Only + a CSV-backed person) the trace is a
        recording, not a simulation — there is no model and the food log is
        report-only, so hide the food/exercise graph entirely (issue 08)."""
        csv_replay = self._model_only and (
            getattr(self._active_person, "data_source", "model") == "csv"
        )
        self._graph.fe_canvas.setVisible(not csv_replay)

    def _set_start_pause_label(self) -> None:
        label = {"stopped": "Start", "running": "Pause", "paused": "Resume"}[self._run_state]
        self._start_pause_btn.setText(label)

    def _send_run_state(self, value: int) -> None:
        """Broadcast a run-state byte to every connected board (see PROTOCOL_SPEC.md)."""
        self._board.broadcast("run_state", protocol.encode_run_state(value))

    def _broadcast_data_source(self) -> None:
        """Tell every connected board whether the active person is model- or CSV-backed.

        The CSV bytes themselves are uploaded separately (Configuration →
        Send CSV to Board); this just flips the board's playback source so it
        matches what the app's own engine is doing.
        """
        is_csv = (
            self._active_person is not None
            and getattr(self._active_person, "data_source", "model") == "csv"
        )
        self._board.broadcast("data_source", protocol.encode_data_source(is_csv))
        self._board.broadcast("speed", protocol.encode_speed(self._speed_mult))

    def _on_start_pause_clicked(self) -> None:
        """Start (from stopped), pause (from running), or resume (from paused)."""
        if self._run_state == "stopped":
            self._start_run()
            return
        if self._run_state == "running":
            self._engines.pause_all()
            self._run_state = "paused"
            self._send_run_state(protocol.RUN_STATE_PAUSED)
        else:  # paused
            self._engines.resume_all()
            self._run_state = "running"
            self._send_run_state(protocol.RUN_STATE_RUNNING)
        self._set_start_pause_label()

    def _start_run(self) -> None:
        """Start a fresh run: reset+resume the local engine and reset+start every
        connected board, both anchored to the moment this is called.
        """
        self._restart_engine()  # preps graphs + a paused pool (run_state still "stopped" here)
        self._graph.graph_t0 = datetime.now(UTC)
        self._engines.resume_all()
        self._run_state = "running"
        self._set_start_pause_label()
        self._broadcast_data_source()
        self._send_run_state(protocol.RUN_STATE_STOPPED)
        self._send_run_state(protocol.RUN_STATE_RUNNING)

    def _on_stop_clicked(self) -> None:
        """Stop and discard the local engine, clear the graphs, and reset the board."""
        self._stop_engine()
        self._run_state = "stopped"
        self._reset_graph_view()
        self._send_run_state(protocol.RUN_STATE_STOPPED)
        self._set_start_pause_label()

    def _slot_user_id(self, slot: int) -> str:
        """The tree-row id the received stream for *slot* uses, so the local
        expected line lands in the same self._history bucket."""
        return board_layout.device_label(board_layout.advert_name(slot), self._board_layout)

    def _feed_board_food_exercise(self, user_id: str, carbs: float, exercise: float) -> None:
        """Hand one sensor's board-reported Food/Exercise Status to its local
        engine so the "expected" model follows the board's meal input."""
        slots = self._engines.slots
        if self._per_slot_expected:
            for s in slots:
                if self._slot_user_id(s) == user_id:
                    self._engines.set_board_food_exercise(s, carbs, exercise)
                    return
        elif slots:  # one board -> the single engine
            self._engines.set_board_food_exercise(slots[0], carbs, exercise)

    def _on_expected_reading(
        self, slot: int, timestamp: str, glucose: float, carbs_rate: float, exercise_pct: float
    ) -> None:
        """Consume one tick from slot *slot*'s engine in the pool."""
        g = self._graph
        t = g.elapsed_seconds(timestamp)
        if self._model_only:
            g.buf.graph_x.append(t)
            g.buf.graph_y.append(glucose)
            g.redraw_glucose()
            g.buf.food_ex_x.append(t)
            g.buf.food_ex_carbs_y.append(carbs_rate)
            g.buf.food_ex_exercise_y.append(exercise_pct)
            g.redraw_food_ex()
        elif self._per_slot_expected:
            h = self._hist(self._slot_user_id(slot))
            h["ex_gx"].append(t)
            h["ex_gy"].append(glucose)
            if self._slot_user_id(slot) == self._selected_user:
                g.redraw_glucose()
        else:
            g.expected_x.append(t)
            g.expected_y.append(glucose)
            g.redraw_glucose()

    # ------------------------------------------------------------------
    # BLE message handling
    # ------------------------------------------------------------------

    def _on_new_message(self, msg: dict) -> None:
        """Record + plot a decoded BLE notification (the row itself is UserTree's).

        ``recording`` gates the history appends: the board keeps sending
        notifications regardless of run state, so without it a Stop/Pause would
        be undone by the next one. CGMS-only has no Start/Stop so it keys on
        self._cgms_only; Model Only ignores BLE entirely (the engine drives the
        plots). Every sensor's stream is recorded while recording, not just the
        selected one's, but only the selected user's buffers get redrawn.
        """
        user_id = msg.get("user_id")
        recording = not self._model_only and (self._cgms_only or self._run_state == "running")
        selected = user_id is not None and user_id == self._selected_user

        self.tree.note_message(msg)

        if "glucose_value" in msg and recording and user_id is not None:
            h = self._hist(user_id)
            h["gx"].append(self._graph.elapsed_seconds(msg["timestamp"]))
            h["gy"].append(msg["glucose_value"])
            if selected:
                self._graph.redraw_glucose()

        if recording and user_id is not None and "carbs_g_per_min" in msg:
            carbs = msg["carbs_g_per_min"]
            exercise = msg.get("exercise_pct", 0.0)
            h = self._hist(user_id)
            h["fx"].append(self._graph.elapsed_seconds(msg["timestamp"]))
            h["fc"].append(carbs)
            h["fe"].append(exercise)
            # Drive the local "expected" model with the board's own food/exercise
            # so the two lines only ever differ by sensor noise, never by a
            # stale local schedule (the board's Food/Exercise Status already
            # folds in its schedule + any instant events).
            self._feed_board_food_exercise(user_id, carbs, exercise)
            if selected:
                self._graph.redraw_food_ex()

    def _on_user_selected(self, user_id: str) -> None:
        """Switch which device's history is plotted, keeping every user's data.

        Every connected sensor's received stream is recorded to self._history
        from Start onward (see _on_new_message), all against the one shared
        graph_t0. Selecting a row just rebinds the plot buffers to that user's
        kept lists and redraws — no reset, no loss, no timeline drift, since the
        origin never moves between Start actions.
        """
        self._selected_user = user_id
        self._bind_selected_history()
        self._graph.redraw_glucose()
        self._graph.redraw_food_ex()
        self._set_graph_title()

    def _on_view_window_changed(self) -> None:
        """Reload the graph time-window preference and redraw."""
        self._view_window_s = float(app_settings.load_pref("view_window_s", 3600.0))
        self._graph.set_view_window(self._view_window_s)

    def _fe_graph_title(self) -> str:
        """Title for the food/exercise graph, passed to GlucoseGraph as a callback.

        In CSV playback (Model Only + a CSV-backed person) there is no model
        running — the food log is replayed report-only and does not affect
        glucose (see issue 08). Say so, so the carb-rate curve isn't read as
        driving the trace above it. With a board connected the model always
        runs, so the normal title stands.
        """
        if self._model_only and getattr(self._active_person, "data_source", "model") == "csv":
            return "Food log — report-only (CSV playback; does not drive glucose)"
        return "Food / Exercise"

    # ------------------------------------------------------------------
    # Qt overrides
    # ------------------------------------------------------------------

    def closeEvent(self, event: QCloseEvent) -> None:
        """Stop the engine and any live BLE session, then close every child window."""
        self._stop_engine()
        if self._bluetooth_window is not None:
            self._bluetooth_window.stop_all_sessions()
            self._bluetooth_window.close()
        for window in (
            self._debug_window,
            self._person_config_window,
            self._sensor_config_window,
            self._food_config_window,
            self._exercise_config_window,
            self._configuration_window,
            self._csv_analysis_window,
            self._view_config_window,
            self._fault_panel,
            self._scenario_window,
        ):
            if window is not None:
                window.close()
        super().closeEvent(event)
