"""Main application window: toolbar, user treeview, glucose graph, food/exercise graph,
and the person/sensor/mode selector bar."""
from __future__ import annotations

from datetime import datetime, timezone

from PyQt6.QtGui import QCloseEvent, QPalette
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QToolBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import Qt

import matplotlib  # pylint: disable=wrong-import-order
matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402

from api import protocol
from core.ble_message_log import BleMessageLog
from graphic.bluetooth_window import BluetoothWindow
from graphic.debug_window import DebugWindow
from graphic.device_target import restart_board
from graphic.exercise_config_window import ExerciseConfigWindow
from graphic.food_config_window import FoodConfigWindow
from graphic.instant_event_dialog import ExerciseInstantDialog, FoodInstantDialog
from graphic.person_config_window import PersonConfigWindow
from graphic.sensor_config_window import SensorConfigWindow
from models import cambridge, profile_store
from models import sensors as sensor_defaults
from models.engine import SimulationEngine
from models.types import ModelId, PersonProfile, SensorId, SensorProfile


class MainWindow(QMainWindow):
    """Top-level window: toolbar, user treeview, glucose graph, food/exercise graph, selector bar.

    The treeview and glucose graph are populated live from :class:`BleMessageLog`
    for whichever device is selected, exactly as before. On top of that, an
    "active person" profile (chosen in the bottom bar) drives a
    :class:`SimulationEngine` running the same physiological model purely in
    Python (no sensor noise) as a dashed "expected" line overlaid on the solid
    "received" line — or, in Model Only mode, as the sole line, with no BLE
    device required at all. The food/exercise graph below the main graph shows
    carb intake rate and exercise intensity: from the board's own Food/Exercise
    Status notifications when connected, or from the same local engine in
    Model Only mode.
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

        self._ble_log = BleMessageLog(self)
        self._ble_log.new_message.connect(self._on_new_message)

        self._person_profiles, self._sensor_profiles = profile_store.load()
        self._seed_default_profiles()
        self._active_person: PersonProfile | None = None
        self._active_sensor: SensorProfile | None = None
        self._fast_mode = False
        self._model_only = False
        self._cgms_only = False
        self._engine: SimulationEngine | None = None
        self._run_state = "stopped"  # "stopped" | "running" | "paused"

        # Fixed reference point for every graph's x-axis: real elapsed wall-clock
        # seconds since the app launched. Using one never-reset reference (instead
        # of a per-line step counter) is what keeps the received line (board
        # pushes every ~5s) and the expected line (local engine ticks every 1s)
        # correctly aligned on the same time axis despite their different
        # cadences — a step-index axis made the faster line look compressed.
        self._graph_t0 = datetime.now(timezone.utc)

        self._user_items: dict[str, QTreeWidgetItem] = {}
        self.tree = self._build_tree()

        self._graph_x: list[float] = []
        self._graph_y: list[float] = []
        self._expected_x: list[float] = []
        self._expected_y: list[float] = []
        self._selected_user: str | None = None
        self._figure, self._canvas, self._ax, self._line, self._expected_line = self._build_graph()

        self._food_ex_x: list[float] = []
        self._food_ex_carbs_y: list[float] = []
        self._food_ex_exercise_y: list[float] = []
        (
            self._fe_figure,
            self._fe_canvas,
            self._fe_ax,
            self._fe_ax2,
            self._carbs_line,
            self._exercise_line,
        ) = self._build_food_exercise_graph()

        self._bottom = self._build_bottom_bar()

        right_splitter = QSplitter(Qt.Orientation.Vertical)
        right_splitter.addWidget(self._canvas)
        right_splitter.addWidget(self._fe_canvas)
        right_splitter.addWidget(self._bottom)
        right_splitter.setSizes([340, 170, 130])

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.tree)
        splitter.addWidget(right_splitter)
        splitter.setSizes([300, 800])
        # setSizes() above is only an initial hint sized to the resize(1100, 750)
        # call below; without a real minimum the tree has nothing stopping it from
        # being squeezed to a sliver when the actual window is narrower than that
        # (different DPI scaling, smaller display, not maximized) — the graph
        # canvases on the right have their own effective minimum, so all the
        # deficit lands on the tree. Give the tree a floor and make the graph
        # side absorb slack instead.
        self.tree.setMinimumWidth(220)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setCollapsible(0, False)

        self.setCentralWidget(splitter)

        self._refresh_person_combo()
        self._refresh_sensor_combo()

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

    def _setup_toolbar(self) -> None:
        """Create the top toolbar with right-aligned Bluetooth and Debug buttons."""
        toolbar = QToolBar()
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)
        self.bluetooth_btn = QPushButton("Connect Bluetooth")
        self.bluetooth_btn.clicked.connect(self._open_bluetooth)
        toolbar.addWidget(self.bluetooth_btn)
        self.debug_btn = QPushButton("Debug")
        self.debug_btn.clicked.connect(self._open_debug)
        toolbar.addWidget(self.debug_btn)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, toolbar)

    def _build_tree(self) -> QTreeWidget:
        """Create and return the user treeview (starts empty, populated on first message)."""
        tree = QTreeWidget()
        tree.setHeaderLabels(["User", "Glucose"])
        tree.header().setStretchLastSection(True)
        tree.currentItemChanged.connect(self._on_user_selected)
        return tree

    def _build_graph(self):
        """Create the glucose figure/canvas/axes plus a solid "received" and dashed "expected" line.

        Colors are pulled from the live Qt palette rather than hardcoded, so
        the chart matches whichever theme (dark or light) is actually active
        instead of always rendering with matplotlib's white default.
        """
        palette = self.palette()
        bg = palette.color(QPalette.ColorRole.Window).name()
        fg = self._graph_fg = palette.color(QPalette.ColorRole.WindowText).name()
        accent = palette.color(QPalette.ColorRole.Highlight).name()

        figure = Figure(tight_layout=True, facecolor=bg)
        canvas = FigureCanvas(figure)
        ax = figure.add_subplot(111, facecolor=bg)
        ax.set_title("Select a user", color=fg)
        ax.set_xlabel("Time (s)", color=fg)
        ax.set_ylabel("Glucose (mg/dL)", color=fg)
        ax.tick_params(colors=fg)
        for spine in ax.spines.values():
            spine.set_color(fg)
        ax.grid(True, color=fg, alpha=0.15)
        (line,) = ax.plot([], [], lw=1.5, color=accent, label="Received")
        (expected_line,) = ax.plot(
            [], [], lw=1.5, color=accent, linestyle="--", alpha=0.7, label="Expected (model)"
        )
        legend = ax.legend(loc="upper left", fontsize=8, facecolor=bg)
        for text in legend.get_texts():
            text.set_color(fg)
        return figure, canvas, ax, line, expected_line

    def _build_food_exercise_graph(self):
        """Create the food/exercise figure: carbs rate (left axis) and exercise % (right axis)."""
        palette = self.palette()
        bg = palette.color(QPalette.ColorRole.Window).name()
        fg = palette.color(QPalette.ColorRole.WindowText).name()
        carbs_color = palette.color(QPalette.ColorRole.Highlight).name()
        exercise_color = "#e0813f"  # fixed accent, distinguishable from the theme highlight color

        figure = Figure(tight_layout=True, facecolor=bg)
        canvas = FigureCanvas(figure)
        ax = figure.add_subplot(111, facecolor=bg)
        ax.set_title("Food / Exercise", color=fg)
        ax.set_xlabel("Time (s)", color=fg)
        ax.set_ylabel("Carbs (g/min)", color=fg)
        ax.tick_params(colors=fg)
        for spine in ax.spines.values():
            spine.set_color(fg)
        ax.grid(True, color=fg, alpha=0.15)
        (carbs_line,) = ax.plot([], [], lw=1.5, color=carbs_color, label="Carbs (g/min)")

        ax2 = ax.twinx()
        ax2.set_ylabel("Exercise (%)", color=fg)
        ax2.tick_params(colors=fg)
        (exercise_line,) = ax2.plot(
            [], [], lw=1.5, color=exercise_color, linestyle=":", label="Exercise (%)"
        )

        lines = [carbs_line, exercise_line]
        legend = ax.legend(lines, [line.get_label() for line in lines], loc="upper left", fontsize=8, facecolor=bg)
        for text in legend.get_texts():
            text.set_color(fg)
        return figure, canvas, ax, ax2, carbs_line, exercise_line

    def _build_bottom_bar(self) -> QWidget:
        """Create the Person/Sensor selectors, their config buttons, and the mode toggles.

        Laid out as two rows (selectors on top, toggles/run controls below)
        with real margins/spacing so it reads as a proper control panel
        rather than a single cramped strip of widgets.
        """
        bar = QWidget()
        outer = QVBoxLayout(bar)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        selectors_row = QHBoxLayout()
        selectors_row.setSpacing(8)

        selectors_row.addWidget(QLabel("Person:"))
        self._person_combo = QComboBox()
        self._person_combo.setMinimumWidth(140)
        self._person_combo.currentIndexChanged.connect(self._on_person_selected)
        selectors_row.addWidget(self._person_combo)
        self._person_configure_btn = QPushButton("Configure…")
        self._person_configure_btn.clicked.connect(self._open_person_config)
        selectors_row.addWidget(self._person_configure_btn)
        self._food_btn = QPushButton("Food…")
        self._food_btn.clicked.connect(self._open_food_config)
        selectors_row.addWidget(self._food_btn)
        self._exercise_btn = QPushButton("Exercise…")
        self._exercise_btn.clicked.connect(self._open_exercise_config)
        selectors_row.addWidget(self._exercise_btn)

        selectors_row.addSpacing(24)

        selectors_row.addWidget(QLabel("Sensor:"))
        self._sensor_combo = QComboBox()
        self._sensor_combo.setMinimumWidth(140)
        self._sensor_combo.currentIndexChanged.connect(self._on_sensor_selected)
        selectors_row.addWidget(self._sensor_combo)
        self._sensor_configure_btn = QPushButton("Configure…")
        self._sensor_configure_btn.clicked.connect(self._open_sensor_config)
        selectors_row.addWidget(self._sensor_configure_btn)

        selectors_row.addStretch(1)
        outer.addLayout(selectors_row)

        controls_row = QHBoxLayout()
        controls_row.setSpacing(8)

        self._fast_mode_check = QCheckBox("Fast mode (1 s = 1 sim-minute)")
        self._fast_mode_check.toggled.connect(self._on_fast_mode_toggled)
        controls_row.addWidget(self._fast_mode_check)

        self._model_only_check = QCheckBox("Model Only (no device)")
        self._model_only_check.toggled.connect(self._on_model_only_toggled)
        controls_row.addWidget(self._model_only_check)

        self._cgms_only_check = QCheckBox("CGMS Only (standard CGM stream only)")
        self._cgms_only_check.toggled.connect(self._on_cgms_only_toggled)
        controls_row.addWidget(self._cgms_only_check)

        controls_row.addSpacing(24)

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

        controls_row.addStretch(1)
        outer.addLayout(controls_row)

        return bar

    # ------------------------------------------------------------------
    # Window management
    # ------------------------------------------------------------------

    def _ensure_bluetooth_window(self) -> BluetoothWindow:
        """Lazily create the Bluetooth window without showing it — used by DeviceTargetBar."""
        if self._bluetooth_window is None:
            self._bluetooth_window = BluetoothWindow(self._ble_log)
        return self._bluetooth_window

    def _open_debug(self) -> None:
        """Open (or raise) the debug messages window."""
        if self._debug_window is None:
            self._debug_window = DebugWindow(self._ble_log)
        self._debug_window.show()
        self._debug_window.raise_()
        self._debug_window.activateWindow()

    def _open_bluetooth(self) -> None:
        """Open (or raise) the Bluetooth devices window, starting a scan if new."""
        window = self._ensure_bluetooth_window()
        window.show()
        window.raise_()
        window.activateWindow()

    def _open_person_config(self) -> None:
        """Open (or raise) the Person configuration window."""
        if self._person_config_window is None:
            self._person_config_window = PersonConfigWindow(
                self._person_profiles, self._on_profiles_changed, self._ensure_bluetooth_window
            )
        self._person_config_window.show()
        self._person_config_window.raise_()
        self._person_config_window.activateWindow()

    def _open_sensor_config(self) -> None:
        """Open (or raise) the Sensor configuration window."""
        if self._sensor_config_window is None:
            self._sensor_config_window = SensorConfigWindow(
                self._sensor_profiles, self._on_profiles_changed, self._ensure_bluetooth_window
            )
        self._sensor_config_window.show()
        self._sensor_config_window.raise_()
        self._sensor_config_window.activateWindow()

    def _open_food_config(self) -> None:
        """Open (or raise) the Food configuration window for the active person."""
        if self._food_config_window is None:
            self._food_config_window = FoodConfigWindow(
                lambda: self._active_person, self._on_profiles_changed, self._ensure_bluetooth_window
            )
        self._food_config_window.show()
        self._food_config_window.raise_()
        self._food_config_window.activateWindow()

    def _open_exercise_config(self) -> None:
        """Open (or raise) the Exercise configuration window for the active person."""
        if self._exercise_config_window is None:
            self._exercise_config_window = ExerciseConfigWindow(
                lambda: self._active_person, self._on_profiles_changed, self._ensure_bluetooth_window
            )
        self._exercise_config_window.show()
        self._exercise_config_window.raise_()
        self._exercise_config_window.activateWindow()

    def _open_insert_food(self) -> None:
        """Prompt for a one-shot carb bolus and inject it into the running simulation now.

        Unlike Food…'s "Send to Board" (which edits the recurring-daily
        schedule and, like every other config write, resets the board's
        sim clock/model state), this uses the food_instant characteristic —
        applied on top of whatever's already running, no reset. See
        PROTOCOL_SPEC.md's "Instant food/exercise events" section.
        """
        if self._engine is None and (self._bluetooth_window is None or not self._bluetooth_window.sessions()):
            QMessageBox.information(self, "Insert Food Now", "Nothing running to insert into — start a run first.")
            return
        dialog = FoodInstantDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        carbs_g, duration_min = dialog.values()
        if self._engine is not None:
            self._engine.add_instant_food(duration_min, carbs_g)
        if self._bluetooth_window is not None:
            payload = protocol.encode_food_instant(duration_min, carbs_g)
            for session in self._bluetooth_window.sessions().values():
                session.queue_write("food_instant", payload)

    def _open_insert_exercise(self) -> None:
        """Prompt for a one-shot exercise bout and inject it into the running simulation now.

        Same non-disruptive semantics as _open_insert_food, via the
        exercise_instant characteristic.
        """
        if self._engine is None and (self._bluetooth_window is None or not self._bluetooth_window.sessions()):
            QMessageBox.information(
                self, "Insert Exercise Now", "Nothing running to insert into — start a run first."
            )
            return
        dialog = ExerciseInstantDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        duration_min, intensity_pct = dialog.values()
        if self._engine is not None:
            self._engine.add_instant_exercise(duration_min, intensity_pct)
        if self._bluetooth_window is not None:
            payload = protocol.encode_exercise_instant(duration_min, intensity_pct)
            for session in self._bluetooth_window.sessions().values():
                session.queue_write("exercise_instant", payload)

    # ------------------------------------------------------------------
    # Person/sensor selection and profile persistence
    # ------------------------------------------------------------------

    def _on_profiles_changed(self) -> None:
        """Persist profiles to disk and refresh everything that depends on them."""
        profile_store.save(self._person_profiles, self._sensor_profiles)
        self._refresh_person_combo()
        self._refresh_sensor_combo()
        self._restart_engine()

    def _refresh_person_combo(self) -> None:
        """Repopulate the Person combo, keeping the current selection if it still exists.

        If nothing has ever been explicitly selected (as opposed to the user
        deliberately picking "(none)"), defaults to the first saved profile
        so the graph shows data right away instead of sitting empty.
        """
        had_active = self._active_person is not None
        current_name = self._active_person.name if self._active_person else None
        self._person_combo.blockSignals(True)
        self._person_combo.clear()
        self._person_combo.addItem("(none)", None)
        select_index = 0
        for i, profile in enumerate(self._person_profiles):
            self._person_combo.addItem(profile.name, profile)
            if profile.name == current_name:
                select_index = i + 1
        if select_index == 0 and not had_active and self._person_profiles:
            select_index = 1
        self._person_combo.setCurrentIndex(select_index)
        self._person_combo.blockSignals(False)
        self._active_person = self._person_combo.currentData()

    def _refresh_sensor_combo(self) -> None:
        """Repopulate the Sensor combo, keeping the current selection if it still exists.

        Defaults to the first saved profile on first load, same reasoning as
        _refresh_person_combo.
        """
        had_active = self._active_sensor is not None
        current_name = self._active_sensor.name if self._active_sensor else None
        self._sensor_combo.blockSignals(True)
        self._sensor_combo.clear()
        self._sensor_combo.addItem("(none)", None)
        select_index = 0
        for i, profile in enumerate(self._sensor_profiles):
            self._sensor_combo.addItem(profile.name, profile)
            if profile.name == current_name:
                select_index = i + 1
        if select_index == 0 and not had_active and self._sensor_profiles:
            select_index = 1
        self._sensor_combo.setCurrentIndex(select_index)
        self._sensor_combo.blockSignals(False)
        self._active_sensor = self._sensor_combo.currentData()

    def _on_person_selected(self, _index: int) -> None:
        """Switch the active person and restart the parallel simulation for them."""
        self._active_person = self._person_combo.currentData()
        self._restart_engine()

    def _on_sensor_selected(self, _index: int) -> None:
        """Switch the active sensor (used only when explicitly sent to a board)."""
        self._active_sensor = self._sensor_combo.currentData()

    # ------------------------------------------------------------------
    # Mode toggles
    # ------------------------------------------------------------------

    def _on_fast_mode_toggled(self, checked: bool) -> None:
        """Switch the local engine's pacing and broadcast the new mode to connected boards.

        Also nudges the board's run state to RUNNING (like restart_board()
        does for the config windows' Send to Board) — the mode write alone
        is applied and stored, but a board left paused/stopped won't
        visibly speed up/slow down until it's actually ticking again.
        """
        self._fast_mode = checked
        self._restart_engine()
        if self._bluetooth_window is not None:
            payload = protocol.encode_mode(checked)
            for session in self._bluetooth_window.sessions().values():
                session.queue_write("mode", payload)
                restart_board(session)

    def _on_model_only_toggled(self, checked: bool) -> None:
        """Switch between BLE-driven graphs and pure-model-only graphs."""
        self._model_only = checked
        self.tree.setEnabled(not checked)
        self._restart_engine()

    def _send_cgms_only(self, enabled: bool) -> None:
        """Broadcast the CGMS-only toggle to every connected board (see PROTOCOL_SPEC.md)."""
        if self._bluetooth_window is None:
            return
        payload = protocol.encode_cgms_only(enabled)
        for session in self._bluetooth_window.sessions().values():
            session.queue_write("cgms_only", payload)

    def _set_locked_for_cgms_only(self, locked: bool) -> None:
        """Enable/disable every control that would send a now-rejected config write.

        While CGMS Only is active the board refuses every person/sensor/
        mode/food/exercise/instant-event write (see PROTOCOL_SPEC.md), and
        the app runs no local model — so all of the "extra features" that
        would touch either are simply unavailable until it's turned off
        again.
        """
        for widget in (
            self._person_configure_btn,
            self._food_btn,
            self._exercise_btn,
            self._sensor_configure_btn,
            self._fast_mode_check,
            self._model_only_check,
            self._start_pause_btn,
            self._stop_btn,
            self._insert_food_btn,
            self._insert_exercise_btn,
        ):
            widget.setEnabled(not locked)

    def _on_cgms_only_toggled(self, checked: bool) -> None:
        """Switch into/out of CGMS-only mode.

        On: locks every config-sending control, discards the local model
        entirely (no "expected" line — this is a pure passive CGM stream
        viewer), and tells every connected board to stream nothing but
        standard CGM Measurement notifications. Does not reset the board —
        whatever's currently running just keeps running under the new
        restrictions.

        Off: tells the board to reset and stop, waiting for an explicit
        Start (same as PROTOCOL_SPEC.md's run-state STOPPED), and unlocks
        the app again. Checking CGMS Only again after this picks up right
        back where it left off — restricted streaming, simulation running.
        """
        self._cgms_only = checked
        if checked:
            if self._model_only_check.isChecked():
                # Mutually exclusive with CGMS Only — flip it off without
                # letting _on_model_only_toggled transiently spin up an
                # engine we're about to stop anyway.
                self._model_only_check.blockSignals(True)
                self._model_only_check.setChecked(False)
                self._model_only_check.blockSignals(False)
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

    def _elapsed_seconds(self, timestamp_str: str) -> float:
        """Convert an ISO timestamp (from a BLE message or SimulationEngine tick) to
        seconds elapsed since the current view started (self._graph_t0) — the shared
        x-axis unit for every graph."""
        return (datetime.fromisoformat(timestamp_str) - self._graph_t0).total_seconds()

    def _reset_expected(self) -> None:
        self._expected_x, self._expected_y = [], []

    def _reset_food_ex(self) -> None:
        self._food_ex_x = []
        self._food_ex_carbs_y = []
        self._food_ex_exercise_y = []

    def _reset_graph_view(self) -> None:
        """Clear both graphs and re-anchor the shared timeline at t=0.

        Called by every "restart" action: Start, Stop, switching the active
        person/sensor/mode, toggling Model Only, editing/saving a profile, or
        picking a different device in the tree. A single shared reset point
        is what keeps the received and expected lines aligned on the same
        time origin instead of drifting onto different implicit timelines —
        resetting only one of them (or replaying old history against a
        freshly-reset origin) is exactly what caused points to land at
        confusing offsets instead of starting back at 0.
        """
        self._graph_t0 = datetime.now(timezone.utc)
        self._graph_x, self._graph_y = [], []
        self._reset_expected()
        self._reset_food_ex()
        self._redraw_graph()
        self._redraw_food_ex_graph()

    def _stop_engine(self) -> None:
        """Disconnect, stop, and discard the current engine, if any.

        Disconnecting expected_reading *before* stopping matters: a QThread
        emits its signals on a queued connection, so if the engine emits its
        last tick(s) right as it's being interrupted, that emission is
        already queued for delivery to the main thread and arrives *after*
        this whole restart sequence finishes — by which point a new engine
        and a new self._graph_t0 already exist, so the stale tick would land
        at the wrong x position (seen as a short, offset duplicate dashed
        segment). Disconnecting first makes Qt drop any such pending queued
        call instead of delivering it.
        """
        if self._engine is None:
            return
        try:
            self._engine.expected_reading.disconnect(self._on_expected_reading)
        except TypeError:
            pass
        self._engine.stop()
        self._engine.wait(2000)
        self._engine = None

    def _restart_engine(self) -> None:
        """Stop any running engine, reset both graphs, and start a fresh one."""
        self._stop_engine()

        self._reset_graph_view()

        if self._active_person is None:
            if self._model_only:
                self._ax.set_title("Model Only — select a person", color=self._graph_fg)
                self._canvas.draw_idle()
            return

        if self._model_only:
            self._ax.set_title(f"Model — {self._active_person.name}", color=self._graph_fg)
            self._canvas.draw_idle()

        self._engine = SimulationEngine(self._active_person, self._fast_mode, self)
        self._engine.expected_reading.connect(self._on_expected_reading)
        # A freshly (re)created engine sits idle unless a run is already in
        # progress (e.g. the active person changed mid-run) — otherwise it
        # only starts ticking once the user presses Start, so switching
        # profiles/modes doesn't silently kick off a comparison run.
        self._engine.set_paused(self._run_state != "running")
        self._engine.start()

    def _set_start_pause_label(self) -> None:
        label = {"stopped": "Start", "running": "Pause", "paused": "Resume"}[self._run_state]
        self._start_pause_btn.setText(label)

    def _send_run_state(self, value: int) -> None:
        """Broadcast a run-state byte to every connected board (see PROTOCOL_SPEC.md)."""
        if self._bluetooth_window is None:
            return
        payload = protocol.encode_run_state(value)
        for session in self._bluetooth_window.sessions().values():
            session.queue_write("run_state", payload)

    def _on_start_pause_clicked(self) -> None:
        """Start (from stopped), pause (from running), or resume (from paused)."""
        if self._run_state == "stopped":
            self._start_run()
            return
        if self._run_state == "running":
            if self._engine is not None:
                self._engine.pause()
            self._run_state = "paused"
            self._send_run_state(protocol.RUN_STATE_PAUSED)
        else:  # paused
            if self._engine is not None:
                self._engine.resume()
            self._run_state = "running"
            self._send_run_state(protocol.RUN_STATE_RUNNING)
        self._set_start_pause_label()

    def _start_run(self) -> None:
        """Start a fresh run: reset+resume the local engine and reset+start every
        connected board, both anchored to the moment this is called.
        """
        self._restart_engine()  # preps graphs + a paused engine (run_state still "stopped" here)
        self._graph_t0 = datetime.now(timezone.utc)
        if self._engine is not None:
            self._engine.resume()
        self._run_state = "running"
        self._set_start_pause_label()
        self._send_run_state(protocol.RUN_STATE_STOPPED)
        self._send_run_state(protocol.RUN_STATE_RUNNING)

    def _on_stop_clicked(self) -> None:
        """Stop and discard the local engine, clear the graphs, and reset the board."""
        self._stop_engine()
        self._run_state = "stopped"
        self._reset_graph_view()
        self._send_run_state(protocol.RUN_STATE_STOPPED)
        self._set_start_pause_label()

    def _on_expected_reading(self, timestamp: str, glucose: float, carbs_rate: float, exercise_pct: float) -> None:
        """Consume one tick from the parallel SimulationEngine."""
        t = self._elapsed_seconds(timestamp)
        if self._model_only:
            self._graph_x.append(t)
            self._graph_y.append(glucose)
            self._redraw_graph()
            self._food_ex_x.append(t)
            self._food_ex_carbs_y.append(carbs_rate)
            self._food_ex_exercise_y.append(exercise_pct)
            self._redraw_food_ex_graph()
        else:
            self._expected_x.append(t)
            self._expected_y.append(glucose)
            self._redraw_graph()

    # ------------------------------------------------------------------
    # BLE message handling
    # ------------------------------------------------------------------

    def _on_new_message(self, msg: dict) -> None:
        """Update the treeview row and graphs for the message's user, if relevant.

        Graph appends are gated on self._run_state == "running": the board
        keeps sending Food/Exercise Status notifications (and CGM
        measurements, while a session is active) regardless of run state, so
        without this gate a Stop/Pause would get immediately undone by the
        next notification refilling the just-cleared/frozen graphs. In CGMS
        Only mode there's no Start/Stop to gate on at all (the board manages
        its own run state autonomously), so plotting is keyed on
        self._cgms_only instead. Ignored entirely in Model Only mode, since
        the graphs are driven by the local engine there instead of BLE
        traffic. The treeview row itself still updates unconditionally —
        it's just a live status readout.
        """
        user_id = msg.get("user_id")
        plotting = (
            not self._model_only
            and (self._cgms_only or self._run_state == "running")
            and user_id == self._selected_user
        )

        if "glucose_value" in msg:
            glucose = msg["glucose_value"]
            item = self._user_items.get(user_id)
            if item is None:
                item = QTreeWidgetItem([user_id, "—"])
                self.tree.addTopLevelItem(item)
                self._user_items[user_id] = item
            item.setText(1, f"{glucose:.2f}")

            if plotting:
                self._graph_x.append(self._elapsed_seconds(msg["timestamp"]))
                self._graph_y.append(glucose)
                self._redraw_graph()

        if plotting and "carbs_g_per_min" in msg:
            self._food_ex_x.append(self._elapsed_seconds(msg["timestamp"]))
            self._food_ex_carbs_y.append(msg["carbs_g_per_min"])
            self._food_ex_exercise_y.append(msg.get("exercise_pct", 0.0))
            self._redraw_food_ex_graph()

    def _on_user_selected(self, current: QTreeWidgetItem | None, _prev) -> None:
        """Switch which device's live data is plotted, resetting both graphs to t=0.

        Deliberately does not replay that device's prior BLE history — doing
        so would require its own time origin (anchored to its first message)
        distinct from whatever the expected line is using, which is exactly
        the kind of per-line timeline drift _reset_graph_view() exists to
        avoid. Only live data from here on is plotted.
        """
        if current is None:
            return
        self._selected_user = current.text(0)
        self._reset_graph_view()
        if not self._model_only:
            self._ax.set_title(f"Glucose — {self._selected_user}", color=self._graph_fg)
            self._canvas.draw_idle()

    # ------------------------------------------------------------------
    # Graph redraw helpers
    # ------------------------------------------------------------------

    def _redraw_graph(self) -> None:
        """Push updated x/y data to both line artists and request a canvas refresh."""
        self._line.set_data(self._graph_x, self._graph_y)
        self._expected_line.set_data(self._expected_x, self._expected_y)
        self._ax.relim()
        self._ax.autoscale_view()
        self._canvas.draw_idle()

    def _redraw_food_ex_graph(self) -> None:
        """Push updated x/y data to the food/exercise line artists and request a canvas refresh."""
        self._carbs_line.set_data(self._food_ex_x, self._food_ex_carbs_y)
        self._exercise_line.set_data(self._food_ex_x, self._food_ex_exercise_y)
        self._fe_ax.relim()
        self._fe_ax.autoscale_view()
        self._fe_ax2.relim()
        self._fe_ax2.autoscale_view()
        self._fe_canvas.draw_idle()

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
        ):
            if window is not None:
                window.close()
        super().closeEvent(event)
