"""Main application window: toolbar, user treeview, glucose graph, food/exercise graph,
and the person/sensor/mode selector bar."""

from __future__ import annotations

from datetime import UTC, datetime

import matplotlib  # pylint: disable=wrong-import-order
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QBrush, QCloseEvent, QColor, QPalette
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QGridLayout,
    QGroupBox,
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

matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from api import protocol
from core.ble_message_log import BleMessageLog
from graphic.avatar import avatar_icon
from graphic.bluetooth_window import BluetoothWindow
from graphic.board_layout_window import BoardLayoutWindow
from graphic.configuration_window import ConfigurationWindow
from graphic.csv_analysis_window import CsvAnalysisWindow
from graphic.debug_window import DebugWindow
from graphic.device_target import restart_board
from graphic.exercise_config_window import ExerciseConfigWindow
from graphic.fault_panel import FaultPanel
from graphic.food_config_window import FoodConfigWindow
from graphic.instant_event_dialog import ExerciseInstantDialog, FoodInstantDialog, PisaInstantDialog
from graphic.person_config_window import PersonConfigWindow
from graphic.scenario_window import ScenarioWindow
from graphic.sensor_config_window import SensorConfigWindow
from graphic.view_config_window import ViewConfigWindow
from models import app_settings, board_layout, cambridge, cgm_metrics, profile_store
from models import sensors as sensor_defaults
from models.engine import EnginePool
from models.types import ModelId, PersonProfile, SensorId, SensorProfile


class MainWindow(QMainWindow):  # pylint: disable=too-many-instance-attributes  # see issue 18
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
        self._board_layout_window: BoardLayoutWindow | None = None
        self._csv_analysis_window: CsvAnalysisWindow | None = None
        self._view_config_window: ViewConfigWindow | None = None
        self._fault_panel: FaultPanel | None = None
        self._scenario_window: ScenarioWindow | None = None

        self._ble_log = BleMessageLog(self)
        self._ble_log.new_message.connect(self._on_new_message)
        self._ble_log.device_disconnected.connect(self._on_device_disconnected)

        self._person_profiles, self._sensor_profiles = profile_store.load()
        self._seed_default_profiles()
        # slot -> (person name, sensor name) mapping for the multi-sensor board;
        # persisted to data/board_layout.json, applied via the Board Layout window.
        self._board_layout = board_layout.load()
        self._active_person: PersonProfile | None = None
        self._active_sensor: SensorProfile | None = None
        # Continuous simulation-speed multiplier (x1..x1000); replaces the old
        # on/off Fast mode. Applied to the engine's dt_min and sent to the board.
        self._speed_mult = float(app_settings.load_pref("speed_mult", 1.0))
        # Rolling view: show only the last N wall-clock seconds of the graphs
        # (0 = entire run). Chosen in the View window, persisted.
        self._view_window_s = float(app_settings.load_pref("view_window_s", 3600.0))
        self._visible_xlim: tuple[float, float] | None = None
        # PISA-shaded intervals on the glucose graph: (t_start_s, t_end_s) in
        # graph time; matching matplotlib patches so they can be cleared.
        self._pisa_spans: list[tuple[float, float]] = []
        self._pisa_patches: list = []
        self._model_only = False
        self._cgms_only = False
        # One SimulationEngine per occupied board slot (issue 04). A single
        # slot 0 when there's no multi-sensor layout / in Model Only mode.
        self._engines = EnginePool(self)
        self._engines.expected_reading.connect(self._on_expected_reading)
        # True while the pool is driving >1 slot: expected lines are then kept
        # per user in self._history["ex_g*"], not in the single self._expected_*.
        self._per_slot_expected = False
        self._run_state = "stopped"  # "stopped" | "running" | "paused"

        # Glucose range thresholds (mg/dL) for the graph bands + metrics panels;
        # edited in the Configuration window, persisted to data/settings.json.
        self._thresholds = app_settings.load()
        # Built eagerly (hidden) so its Person/Sensor combos exist for the
        # _refresh_*_combo() calls at the end of __init__ — the Configuration
        # window hosts the widgets, MainWindow still owns the state.
        self._configuration_window = ConfigurationWindow(self, self._on_thresholds_changed)
        self._cfg = self._configuration_window
        # user_id -> stable small integer shown next to the avatar in the tree
        self._user_ids: dict[str, int] = {}

        # Fixed reference point for every graph's x-axis: real elapsed wall-clock
        # seconds since the app launched. Using one never-reset reference (instead
        # of a per-line step counter) is what keeps the received line (board
        # pushes every ~5s) and the expected line (local engine ticks every 1s)
        # correctly aligned on the same time axis despite their different
        # cadences — a step-index axis made the faster line look compressed.
        self._graph_t0 = datetime.now(UTC)

        self._user_items: dict[str, QTreeWidgetItem] = {}
        # user_id -> BLE address, and the set of user_ids whose device has
        # disconnected (their tree row is marked offline until a message with
        # the same dev_id arrives again). See issue 06.
        self._user_dev: dict[str, str] = {}
        self._offline_users: set[str] = set()
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
        # Per-user received history, all sharing the one self._graph_t0 that is
        # (re)anchored at Start. On a multi-sensor board every slot streams from
        # the same board clock and the same Start, so one shared origin is
        # correct — switching the selected tree row just rebinds the live
        # buffers below to that user's lists (see _bind_selected_history), it
        # does not wipe or replay anything. Keys are user_id (tree row id).
        # {user_id: {"gx","gy","fx","fc","fe": list[float]}}
        self._history: dict[str, dict[str, list[float]]] = {}
        (
            self._fe_figure,
            self._fe_canvas,
            self._fe_ax,
            self._fe_ax2,
            self._carbs_line,
            self._exercise_line,
        ) = self._build_food_exercise_graph()

        self._bottom = self._build_bottom_bar()

        right_splitter = self._right_splitter = QSplitter(Qt.Orientation.Vertical)
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
        self.view_btn = QPushButton("View")
        self.view_btn.clicked.connect(self._open_view_config)
        toolbar.addWidget(self.view_btn)
        self.csv_analysis_btn = QPushButton("CSV Analysis")
        self.csv_analysis_btn.clicked.connect(self._open_csv_analysis)
        toolbar.addWidget(self.csv_analysis_btn)
        self.configuration_btn = QPushButton("Configuration")
        self.configuration_btn.clicked.connect(self._open_configuration)
        toolbar.addWidget(self.configuration_btn)
        self.faults_btn = QPushButton("Faults")
        self.faults_btn.clicked.connect(self._open_faults)
        toolbar.addWidget(self.faults_btn)
        self.scenario_btn = QPushButton("Scenario")
        self.scenario_btn.clicked.connect(self._open_scenario)
        toolbar.addWidget(self.scenario_btn)
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

        Reads the *application* palette (not self.palette()) so a rebuild
        triggered right after a theme switch sees the new colors immediately,
        without waiting for the queued ApplicationPaletteChange event.
        """
        palette = QApplication.instance().palette()
        bg = palette.color(QPalette.ColorRole.Window).name()
        fg = self._graph_fg = palette.color(QPalette.ColorRole.WindowText).name()
        accent = palette.color(QPalette.ColorRole.Highlight).name()

        figure = Figure(facecolor=bg)
        # Fixed margins (not tight_layout) so this plot box lines up horizontally
        # with the food/exercise plot box below — see the *_MARGINS class attrs.
        figure.subplots_adjust(**self._GLUCOSE_MARGINS)
        canvas = FigureCanvas(figure)
        ax = figure.add_subplot(111, facecolor=bg)
        ax.set_title("Select a user", color=fg)
        # No x-label here — this graph shares its time axis with the
        # food/exercise graph below, which carries the single "Time (s)" label.
        ax.set_ylabel("Glucose (mg/dL)", color=fg)
        ax.tick_params(colors=fg)
        for spine in ax.spines.values():
            spine.set_color(fg)
        ax.grid(True, color=fg, alpha=0.15)
        self._range_bands: list = []
        self._mean_line = ax.axhline(0.0, color=fg, lw=1.0, linestyle="-.", alpha=0.0, label="Mean")
        # "Received" = faint continuous base + one colored overlay per range
        # category (red out of range, yellow borderline, green in target); see
        # _recolor_main_trace(). "Expected" stays a single dashed line.
        (line,) = ax.plot([], [], lw=0.8, color=fg, alpha=0.35)  # faint base for the colored segs
        _seg_labels = {"g": "In range", "y": "Borderline", "r": "Low / High"}
        self._seg_lines = {
            cat: ax.plot(
                [], [], lw=1.7, color=color, solid_capstyle="round", label=_seg_labels[cat]
            )[0]
            for cat, color in self._LINE_COLORS.items()
        }
        (expected_line,) = ax.plot(
            [], [], lw=1.5, color=accent, linestyle="--", alpha=0.7, label="Expected (model)"
        )
        legend = ax.legend(
            loc="upper center",
            bbox_to_anchor=(0.5, -0.16),
            ncol=3,
            fontsize=8,
            frameon=False,
        )
        for text in legend.get_texts():
            text.set_color(fg)
        self._apply_range_bands(ax)
        ax.set_ylim(*self._EMPTY_YLIM)  # until real data arrives (see _fit_glucose_ylim)
        return figure, canvas, ax, line, expected_line

    # red = TBR2/TAR2 (out of range), yellow = TBR1/TAR1 (borderline), green = TIR
    _RED = "#d32f2f"
    _YELLOW = "#f4b400"
    _GREEN = "#2e7d32"
    _LINE_COLORS = {"r": _RED, "y": _YELLOW, "g": _GREEN}
    # Clinical range band colors (translucent), same 5-band order: TBR2, TBR1, TIR, TAR1, TAR2.
    _BAND_COLORS = (_RED, _YELLOW, _GREEN, _YELLOW, _RED)
    # Left/right are shared so the two stacked plot boxes line up on the time
    # axis; top/bottom differ — the glucose graph only reserves room for its
    # legend, the food/exercise graph also carries the "Time (s)" label.
    _GLUCOSE_MARGINS = dict(left=0.09, right=0.91, top=0.90, bottom=0.18)
    _FOODEX_MARGINS = dict(left=0.09, right=0.91, top=0.86, bottom=0.32)

    def _apply_range_bands(self, ax=None) -> None:
        """(Re)draw the 5 horizontal range bands from the current thresholds.

        axhspan patches don't feed the data limits, so the y-axis still
        autoscales to the glucose lines alone.
        """
        ax = ax or self._ax
        for patch in getattr(self, "_range_bands", []):
            patch.remove()
        t = self._thresholds
        edges = [0.0, t["tbr2_below"], t["tbr1_below"], t["tar1_above"], t["tar2_above"], 600.0]
        self._range_bands = [
            ax.axhspan(lo, hi, color=color, alpha=0.09, zorder=0)
            for lo, hi, color in zip(edges, edges[1:], self._BAND_COLORS)
        ]

    def _category(self, value: float) -> str:
        """'r' out of range (low/high), 'y' borderline, 'g' in target — vs current thresholds."""
        t = self._thresholds
        if value < t["tbr2_below"] or value > t["tar2_above"]:
            return "r"
        if value < t["tbr1_below"] or value > t["tar1_above"]:
            return "y"
        return "g"

    def _recolor_main_trace(self) -> None:
        """Split the Received series into red/yellow/green overlays by range category."""
        xs, ys = self._graph_x, self._graph_y
        n = len(ys)
        nan = float("nan")
        arrs = {"r": [nan] * n, "y": [nan] * n, "g": [nan] * n}
        if n:
            cats = [self._category(v) for v in ys]
            for i, v in enumerate(ys):
                here = cats[i]
                arrs[here][i] = v
                if i > 0 and cats[i - 1] != here:
                    arrs[cats[i - 1]][i] = v
                if i < n - 1 and cats[i + 1] != here:
                    arrs[cats[i + 1]][i] = v
        for cat, line in self._seg_lines.items():
            line.set_data(xs, arrs[cat])

    def _build_food_exercise_graph(self):
        """Create the food/exercise figure: carbs rate (left axis) and exercise % (right axis)."""
        palette = QApplication.instance().palette()
        bg = palette.color(QPalette.ColorRole.Window).name()
        fg = palette.color(QPalette.ColorRole.WindowText).name()
        carbs_color = palette.color(QPalette.ColorRole.Highlight).name()
        exercise_color = "#e0813f"  # fixed accent, distinguishable from the theme highlight color

        figure = Figure(facecolor=bg)
        figure.subplots_adjust(**self._FOODEX_MARGINS)
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
        legend = ax.legend(
            lines,
            [line.get_label() for line in lines],
            loc="upper center",
            bbox_to_anchor=(0.5, -0.34),
            ncol=2,
            fontsize=8,
            frameon=False,
        )
        for text in legend.get_texts():
            text.set_color(fg)
        return figure, canvas, ax, ax2, carbs_line, exercise_line

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

        outer.addWidget(self._build_stats_panel())

        return bar

    def _build_stats_panel(self) -> QGroupBox:
        """Live TIR/TBR/TAR (as time, h:mm), mean and variance for the plotted series."""
        group = QGroupBox("Time in range — current view (h:mm)")
        grid = QGridLayout(group)
        self._stat_value_labels: dict[str, QLabel] = {}
        cells = (
            ("tir", "TIR"),
            ("tbr", "TBR"),
            ("tbr1", "TBR1"),
            ("tbr2", "TBR2"),
            ("tar", "TAR"),
            ("tar1", "TAR1"),
            ("tar2", "TAR2"),
            ("mean", "Mean"),
            ("variance", "Variance"),
        )
        for i, (key, caption) in enumerate(cells):
            row, col = divmod(i, 5)
            box = QVBoxLayout()
            cap = QLabel(caption)
            cap.setStyleSheet("font-size: 10px;")
            val = QLabel("—")
            self._stat_value_labels[key] = val
            box.addWidget(cap)
            box.addWidget(val)
            grid.addLayout(box, row, col)
        return group

    def _update_stats_panel(self) -> None:
        """Recompute the range-metrics panel from the glucose series *currently in view*.

        TIR/TBR/TAR are shown as time in each band (h:mm) over the visible
        window, not a percentage (issue 13).
        """
        series = self._in_view(self._graph_x, self._graph_y) or self._in_view(
            self._expected_x, self._expected_y
        )
        span_min = None
        if self._visible_xlim is not None:
            lo, hi = self._visible_xlim
            span_min = max(0.0, (hi - lo) / 60.0)
        m = cgm_metrics.compute(
            series,
            tbr2_below=self._thresholds["tbr2_below"],
            tbr1_below=self._thresholds["tbr1_below"],
            tar1_above=self._thresholds["tar1_above"],
            tar2_above=self._thresholds["tar2_above"],
            span_minutes=span_min,
        )
        if m.n == 0:
            for val in self._stat_value_labels.values():
                val.setText("—")
            return
        fmt = cgm_metrics.fmt_hm
        self._stat_value_labels["tir"].setText(fmt(m.tir_min))
        self._stat_value_labels["tbr"].setText(fmt(m.tbr_min))
        self._stat_value_labels["tbr1"].setText(fmt(m.tbr1_min))
        self._stat_value_labels["tbr2"].setText(fmt(m.tbr2_min))
        self._stat_value_labels["tar"].setText(fmt(m.tar_min))
        self._stat_value_labels["tar1"].setText(fmt(m.tar1_min))
        self._stat_value_labels["tar2"].setText(fmt(m.tar2_min))
        self._stat_value_labels["mean"].setText(f"{m.mean:.0f}")
        self._stat_value_labels["variance"].setText(f"{m.variance:.0f}")

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

    def _open_configuration(self) -> None:
        """Open (or raise) the Configuration window (selectors, modes, thresholds)."""
        self._configuration_window.show()
        self._configuration_window.raise_()
        self._configuration_window.activateWindow()

    def _open_csv_analysis(self) -> None:
        """Open (or raise) the CSV Analysis window."""
        if self._csv_analysis_window is None:
            self._csv_analysis_window = CsvAnalysisWindow(
                self._person_profiles, self._on_csv_window_assigned
            )
        self._csv_analysis_window.show()
        self._csv_analysis_window.raise_()
        self._csv_analysis_window.activateWindow()

    def _open_faults(self) -> None:
        """Open (or raise) the Fault-injection panel."""
        if self._fault_panel is None:
            self._fault_panel = FaultPanel(self)
        self._fault_panel.show()
        self._fault_panel.raise_()
        self._fault_panel.activateWindow()

    def _open_scenario(self) -> None:
        """Open (or raise) the Scenario window."""
        if self._scenario_window is None:
            self._scenario_window = ScenarioWindow(self)
        self._scenario_window.show()
        self._scenario_window.raise_()
        self._scenario_window.activateWindow()

    def _open_view_config(self) -> None:
        """Open (or raise) the View window (theme / appearance)."""
        if self._view_config_window is None:
            self._view_config_window = ViewConfigWindow(
                self._on_theme_changed, self._on_view_window_changed
            )
        self._view_config_window.show()
        self._view_config_window.raise_()
        self._view_config_window.activateWindow()

    def _on_theme_changed(self) -> None:
        """After a palette switch: rebuild the graph canvases so they repaint in
        the new colors (they read the Qt palette only at build time)."""
        self._rebuild_graphs()

    def _rebuild_graphs(self) -> None:
        """Recreate both matplotlib canvases in place, preserving the current data."""
        sizes = self._right_splitter.sizes()
        (self._figure, self._canvas, self._ax, self._line, self._expected_line) = (
            self._build_graph()
        )
        (
            self._fe_figure,
            self._fe_canvas,
            self._fe_ax,
            self._fe_ax2,
            self._carbs_line,
            self._exercise_line,
        ) = self._build_food_exercise_graph()
        old_g = self._right_splitter.replaceWidget(0, self._canvas)
        old_fe = self._right_splitter.replaceWidget(1, self._fe_canvas)
        for old in (old_g, old_fe):
            if old is not None:
                old.deleteLater()
        self._right_splitter.setSizes(sizes)

        if self._model_only and self._active_person is not None:
            self._ax.set_title(
                f"Model — {self._active_person.name}",
                color=self._graph_fg,
                fontweight="bold",
                fontsize=11,
            )
        elif not self._model_only and self._selected_user:
            self._ax.set_title(
                f"Glucose — {self._selected_user}",
                color=self._graph_fg,
                fontweight="bold",
                fontsize=11,
            )
        self._redraw_graph()
        self._redraw_food_ex_graph()

    def _on_thresholds_changed(self) -> None:
        """Reload thresholds after a Configuration-window save and redraw the bands/metrics."""
        self._thresholds = app_settings.load()
        self._apply_range_bands()
        self._redraw_graph()

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
                lambda: self._active_person,
                self._on_profiles_changed,
                self._ensure_bluetooth_window,
            )
        self._food_config_window.show()
        self._food_config_window.raise_()
        self._food_config_window.activateWindow()

    def _open_exercise_config(self) -> None:
        """Open (or raise) the Exercise configuration window for the active person."""
        if self._exercise_config_window is None:
            self._exercise_config_window = ExerciseConfigWindow(
                lambda: self._active_person,
                self._on_profiles_changed,
                self._ensure_bluetooth_window,
            )
        self._exercise_config_window.show()
        self._exercise_config_window.raise_()
        self._exercise_config_window.activateWindow()

    def _open_board_layout(self) -> None:
        """Open (or raise) the Board Layout window (per-slot person/sensor assignment)."""
        if self._board_layout_window is None:
            self._board_layout_window = BoardLayoutWindow(
                self._person_profiles,
                self._sensor_profiles,
                self._board_layout,
                self._on_board_layout_changed,
                self._ensure_bluetooth_window,
            )
        else:
            self._board_layout_window.reload_profiles()
        self._board_layout_window.show()
        self._board_layout_window.raise_()
        self._board_layout_window.activateWindow()

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
        """4 if a connected identity is one slot of a multi-sensor board (its
        advertised name is numbered), else 1 — passed to the instant-event
        dialogs so they show a Slot picker only when it means something."""
        if self._bluetooth_window is None:
            return 1
        return (
            4
            if any(s.slot_index is not None for s in self._bluetooth_window.sessions().values())
            else 1
        )

    def _instant_slot_choices(self) -> list[tuple[int | None, str]]:
        """(value, label) for the instant-event dialogs' Target combo — empty
        (no combo) unless a multi-sensor board is connected, then "All sensors"
        plus one entry per slot named from the board layout (issue 04)."""
        if self._multi_slot_count() <= 1:
            return []
        out: list[tuple[int | None, str]] = [(None, "All sensors")]
        for i in range(board_layout.MAX_SLOTS):
            person = self._board_layout.slots[i].person
            out.append((i, f"Sensor {i + 1} — {person}" if person else f"Sensor {i + 1}"))
        return out

    def _send_instant(self, char_key: str, payload: bytes, slot: int | None) -> None:
        """Send a one-shot event over BLE: to one session with a sensor-select
        prefix when *slot* is given (multi-sensor), else broadcast to every
        connected session (single-sensor / "all slots")."""
        if self._bluetooth_window is None:
            return
        sessions = list(self._bluetooth_window.sessions().values())
        if slot is not None and sessions:
            target = next((s for s in sessions if s.slot_index is not None), sessions[0])
            target.queue_write("sensor_select", protocol.encode_sensor_select(slot))
            target.queue_write(char_key, payload)
        else:
            for session in sessions:
                session.queue_write(char_key, payload)

    def _open_insert_food(self) -> None:
        """Prompt for a one-shot carb bolus and inject it into the running simulation now.

        Unlike Food…'s "Send to Board" (which edits the recurring-daily
        schedule and, like every other config write, resets the board's
        sim clock/model state), this uses the food_instant characteristic —
        applied on top of whatever's already running, no reset. See
        PROTOCOL_SPEC.md's "Instant food/exercise events" section.
        """
        if self._engines.is_empty() and (
            self._bluetooth_window is None or not self._bluetooth_window.sessions()
        ):
            QMessageBox.information(
                self, "Insert Food Now", "Nothing running to insert into — start a run first."
            )
            return
        dialog = FoodInstantDialog(self, slot_choices=self._instant_slot_choices())
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        carbs_g, duration_min = dialog.values()
        slot = dialog.selected_slot()
        self._engines.add_instant_food(slot, duration_min, carbs_g)
        self._send_instant(
            "food_instant", protocol.encode_food_instant(duration_min, carbs_g), slot
        )

    def _open_insert_exercise(self) -> None:
        """Prompt for a one-shot exercise bout and inject it into the running simulation now.

        Same non-disruptive semantics as _open_insert_food, via the
        exercise_instant characteristic.
        """
        if self._engines.is_empty() and (
            self._bluetooth_window is None or not self._bluetooth_window.sessions()
        ):
            QMessageBox.information(
                self, "Insert Exercise Now", "Nothing running to insert into — start a run first."
            )
            return
        dialog = ExerciseInstantDialog(self, slot_choices=self._instant_slot_choices())
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        duration_min, intensity_pct = dialog.values()
        slot = dialog.selected_slot()
        self._engines.add_instant_exercise(slot, duration_min, intensity_pct)
        self._send_instant(
            "exercise_instant", protocol.encode_exercise_instant(duration_min, intensity_pct), slot
        )

    def _open_insert_pisa(self) -> None:
        """Prompt for a one-shot PISA fault and inject it now (via inject_fault)."""
        if self._engines.is_empty() and (
            self._bluetooth_window is None or not self._bluetooth_window.sessions()
        ):
            QMessageBox.information(
                self, "Insert PISA Now", "Nothing running to insert into — start a run first."
            )
            return
        dialog = PisaInstantDialog(self, slot_choices=self._instant_slot_choices())
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.inject_fault("pisa", dialog.values(), slot=dialog.selected_slot())

    def inject_fault(self, kind: str, values: tuple, slot: int | None = None) -> None:
        """Inject a sensor fault into the running simulation without resetting it.

        The extensible entry point behind both the "Insert PISA Now…" button and
        the Faults panel (graphic/fault_panel.py). Only ``"pisa"`` is wired for
        now — a transient false low: the board/engine multiply the sensor
        reading by ``1 - depth*sin(pi*elapsed/duration)`` while active, leaving
        the underlying glucose untouched; the interval is shaded on the graph.
        """
        if kind != "pisa":
            raise ValueError(f"unknown fault kind {kind!r}")
        duration_min, depth_frac = values
        self._engines.add_instant_pisa(slot, duration_min, depth_frac)
        self._send_instant(
            "pisa_instant", protocol.encode_pisa_instant(duration_min, depth_frac), slot
        )
        # Shade the affected interval: duration is simulated minutes; the graph
        # x-axis is wall-clock seconds, so scale by the current speed multiplier.
        t0 = self._elapsed_seconds(datetime.now(UTC).isoformat(timespec="seconds"))
        self._pisa_spans.append((t0, t0 + duration_min * 60.0 / self._speed_mult))
        self._redraw_graph()

    # ------------------------------------------------------------------
    # Scenario runner dispatch (graphic/scenario_window.py)
    # ------------------------------------------------------------------

    def _scenario_dispatch(self, kind: str, args: dict) -> str:
        """Execute one scenario action on the GUI thread; return a log line.

        Supported kinds: speed, run_state, person, data_source, comm_profile,
        insert_food, insert_exercise, inject_fault.
        """
        if kind == "speed":
            mult = float(args.get("multiplier", 1))
            self._cfg.speed_slider.setValue(self._cfg._speed_to_slider(mult))
            return f"speed → x{int(self._speed_mult)}"

        if kind == "run_state":
            state = str(args.get("state", "")).lower()
            if state == "start" and self._run_state == "stopped":
                self._start_run()
            elif state == "stop":
                self._on_stop_clicked()
            elif state in ("pause", "resume"):
                self._on_start_pause_clicked()
            return f"run_state → {state}"

        if kind in ("person", "data_source"):
            name = args.get("person")
            for i in range(self._cfg.person_combo.count()):
                data = self._cfg.person_combo.itemData(i)
                if data is not None and data.name == name:
                    self._cfg.person_combo.setCurrentIndex(i)
                    break
            self._broadcast_data_source()
            src = (
                getattr(self._active_person, "data_source", "model") if self._active_person else "?"
            )
            return f"person → {name} ({src})"

        if kind == "comm_profile":
            dexcom = str(args.get("profile", "sig")).lower() == "dexcom"
            self._cfg.comm_profile_combo.setCurrentIndex(1 if dexcom else 0)
            return f"comm_profile → {'dexcom' if dexcom else 'sig'}"

        if kind == "insert_food":
            carbs_g = float(args.get("carbs_g", 50))
            duration_min = int(args.get("duration_min", 15))
            slot = args.get("slot")
            self._engines.add_instant_food(slot, duration_min, carbs_g)
            self._send_instant(
                "food_instant", protocol.encode_food_instant(duration_min, carbs_g), slot
            )
            return f"insert_food {carbs_g:g} g / {duration_min} min" + (
                f" @slot {slot}" if slot is not None else ""
            )

        if kind == "insert_exercise":
            duration_min = int(args.get("duration_min", 30))
            intensity_pct = float(args.get("intensity_pct", 50))
            slot = args.get("slot")
            self._engines.add_instant_exercise(slot, duration_min, intensity_pct)
            self._send_instant(
                "exercise_instant",
                protocol.encode_exercise_instant(duration_min, intensity_pct),
                slot,
            )
            return f"insert_exercise {duration_min} min / {intensity_pct:g} %" + (
                f" @slot {slot}" if slot is not None else ""
            )

        if kind == "inject_fault":
            fault = str(args.get("fault", "pisa"))
            duration_min = int(args.get("duration_min", 10))
            depth_frac = float(args.get("depth_frac", 0.4))
            self.inject_fault(fault, (duration_min, depth_frac), slot=args.get("slot"))
            return f"inject_fault {fault} {int(depth_frac * 100)} % / {duration_min} min"

        return f"(unknown action {kind!r})"

    # ------------------------------------------------------------------
    # Person/sensor selection and profile persistence
    # ------------------------------------------------------------------

    def _on_csv_window_assigned(self, person: PersonProfile) -> None:
        """CSV Analysis assigned a 24 h window to *person* — make them the active
        person so the Configuration window's data-source group and the graph
        immediately reflect the new CSV source, then persist + refresh."""
        combo = self._cfg.person_combo
        for i in range(combo.count()):
            data = combo.itemData(i)
            if data is person or (data is not None and data.name == person.name):
                combo.setCurrentIndex(i)  # fires _on_person_selected -> _load_data_source
                break
        self._on_profiles_changed()

    def _on_profiles_changed(self) -> None:
        """Persist profiles to disk and refresh everything that depends on them."""
        profile_store.save(self._person_profiles, self._sensor_profiles)
        self._refresh_person_combo()
        self._refresh_sensor_combo()
        # _refresh_person_combo() re-selects the same person with signals
        # blocked, so currentIndexChanged does NOT fire — the Configuration
        # window's data-source group would otherwise stay stale after an
        # assignment made elsewhere (e.g. CSV Analysis → "Assign window to
        # person…"). Sync it explicitly.
        self._cfg.reload_data_source()
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

    def _refresh_person_combo(self) -> None:
        """Repopulate the Person combo, keeping the current selection if it still exists.

        If nothing has ever been explicitly selected (as opposed to the user
        deliberately picking "(none)"), defaults to the first saved profile
        so the graph shows data right away instead of sitting empty.
        """
        combo = self._cfg.person_combo
        had_active = self._active_person is not None
        current_name = self._active_person.name if self._active_person else None
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("(none)", None)
        select_index = 0
        for i, profile in enumerate(self._person_profiles):
            combo.addItem(profile.name, profile)
            if profile.name == current_name:
                select_index = i + 1
        if select_index == 0 and not had_active and self._person_profiles:
            select_index = 1
        combo.setCurrentIndex(select_index)
        combo.blockSignals(False)
        self._active_person = combo.currentData()

    def _refresh_sensor_combo(self) -> None:
        """Repopulate the Sensor combo, keeping the current selection if it still exists.

        Defaults to the first saved profile on first load, same reasoning as
        _refresh_person_combo.
        """
        combo = self._cfg.sensor_combo
        had_active = self._active_sensor is not None
        current_name = self._active_sensor.name if self._active_sensor else None
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("(none)", None)
        select_index = 0
        for i, profile in enumerate(self._sensor_profiles):
            combo.addItem(profile.name, profile)
            if profile.name == current_name:
                select_index = i + 1
        if select_index == 0 and not had_active and self._sensor_profiles:
            select_index = 1
        combo.setCurrentIndex(select_index)
        combo.blockSignals(False)
        self._active_sensor = combo.currentData()

    def _on_person_selected(self, _index: int) -> None:
        """Switch the active person and restart the parallel simulation for them."""
        self._active_person = self._cfg.person_combo.currentData()
        self._restart_engine()

    def _on_sensor_selected(self, _index: int) -> None:
        """Switch the active sensor (used only when explicitly sent to a board)."""
        self._active_sensor = self._cfg.sensor_combo.currentData()

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
        self._restart_engine()
        if self._bluetooth_window is not None:
            payload = protocol.encode_speed(self._speed_mult)
            for session in self._bluetooth_window.sessions().values():
                session.queue_write("speed", payload)
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
            self._cfg.person_configure_btn,
            self._cfg.food_btn,
            self._cfg.exercise_btn,
            self._cfg.sensor_configure_btn,
            self._cfg.speed_slider,
            self._cfg.model_only_check,
            self._start_pause_btn,
            self._stop_btn,
            self._insert_food_btn,
            self._insert_exercise_btn,
            self._insert_pisa_btn,
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
            if self._cfg.model_only_check.isChecked():
                # Mutually exclusive with CGMS Only — flip it off without
                # letting _on_model_only_toggled transiently spin up an
                # engine we're about to stop anyway.
                self._cfg.model_only_check.blockSignals(True)
                self._cfg.model_only_check.setChecked(False)
                self._cfg.model_only_check.blockSignals(False)
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
        """Clear every graph and re-anchor the shared timeline at t=0.

        Called by every genuine "restart" action: Start, Stop, switching the
        active person/sensor/mode, toggling Model Only, or editing/saving a
        profile. A single shared reset point is what keeps the received and
        expected lines aligned on the same time origin. This also drops the
        per-user history (self._history) — a restart begins a new run for
        everyone. Switching the selected tree row does NOT come through here
        (see _on_user_selected); it only rebinds to that user's kept history.
        """
        self._graph_t0 = datetime.now(UTC)
        self._history = {}
        self._graph_x, self._graph_y = [], []
        self._reset_expected()
        self._reset_food_ex()
        self._pisa_spans = []
        self._bind_selected_history()
        self._redraw_graph()
        self._redraw_food_ex_graph()

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
        self._graph_x, self._graph_y = h["gx"], h["gy"]
        self._food_ex_x = h["fx"]
        self._food_ex_carbs_y = h["fc"]
        self._food_ex_exercise_y = h["fe"]
        if self._per_slot_expected:
            self._expected_x, self._expected_y = h["ex_gx"], h["ex_gy"]

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

        if not slots:
            if self._model_only:
                self._ax.set_title(
                    "Model Only — select a person",
                    color=self._graph_fg,
                    fontweight="bold",
                    fontsize=11,
                )
                self._canvas.draw_idle()
            return

        if self._model_only and self._active_person is not None:
            self._ax.set_title(
                f"Model — {self._active_person.name}",
                color=self._graph_fg,
                fontweight="bold",
                fontsize=11,
            )
            self._canvas.draw_idle()

        # A freshly rebuilt pool sits idle unless a run is already in progress,
        # so switching profiles/modes/layout doesn't silently start a comparison.
        self._engines.rebuild(slots, self._speed_mult, paused=self._run_state != "running")

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

    def _broadcast_data_source(self) -> None:
        """Tell every connected board whether the active person is model- or CSV-backed.

        The CSV bytes themselves are uploaded separately (Configuration →
        Send CSV to Board); this just flips the board's playback source so it
        matches what the app's own engine is doing.
        """
        if self._bluetooth_window is None:
            return
        is_csv = (
            self._active_person is not None
            and getattr(self._active_person, "data_source", "model") == "csv"
        )
        ds_payload = protocol.encode_data_source(is_csv)
        speed_payload = protocol.encode_speed(self._speed_mult)
        for session in self._bluetooth_window.sessions().values():
            session.queue_write("data_source", ds_payload)
            session.queue_write("speed", speed_payload)

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
        self._graph_t0 = datetime.now(UTC)
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

    def _on_expected_reading(
        self, slot: int, timestamp: str, glucose: float, carbs_rate: float, exercise_pct: float
    ) -> None:
        """Consume one tick from slot *slot*'s engine in the pool."""
        t = self._elapsed_seconds(timestamp)
        if self._model_only:
            self._graph_x.append(t)
            self._graph_y.append(glucose)
            self._redraw_graph()
            self._food_ex_x.append(t)
            self._food_ex_carbs_y.append(carbs_rate)
            self._food_ex_exercise_y.append(exercise_pct)
            self._redraw_food_ex_graph()
        elif self._per_slot_expected:
            h = self._hist(self._slot_user_id(slot))
            h["ex_gx"].append(t)
            h["ex_gy"].append(glucose)
            if self._slot_user_id(slot) == self._selected_user:
                self._redraw_graph()
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

        Every connected sensor's stream is recorded to self._history while
        recording, not only the selected one's, so switching the tree row
        shows that sensor's full history since Start instead of an empty
        graph. Only the selected user's buffers get redrawn (they are the
        lists _bind_selected_history aliased onto self._graph_x / etc.).
        """
        user_id = msg.get("user_id")
        recording = not self._model_only and (self._cgms_only or self._run_state == "running")
        selected = user_id is not None and user_id == self._selected_user

        if user_id is not None:
            dev_id = msg.get("dev_id")
            if dev_id is not None:
                self._user_dev[user_id] = dev_id
            if user_id in self._offline_users:  # a message means it's back
                self._offline_users.discard(user_id)
                self._set_row_offline(user_id, offline=False)

        if "glucose_value" in msg:
            glucose = msg["glucose_value"]
            item = self._ensure_user_item(user_id)
            item.setText(1, f"{glucose:.2f}")
            self._update_user_alert(item, user_id, glucose)

            if recording and user_id is not None:
                h = self._hist(user_id)
                h["gx"].append(self._elapsed_seconds(msg["timestamp"]))
                h["gy"].append(glucose)
                if selected:
                    self._redraw_graph()

        if recording and user_id is not None and "carbs_g_per_min" in msg:
            h = self._hist(user_id)
            h["fx"].append(self._elapsed_seconds(msg["timestamp"]))
            h["fc"].append(msg["carbs_g_per_min"])
            h["fe"].append(msg.get("exercise_pct", 0.0))
            if selected:
                self._redraw_food_ex_graph()

    def _ensure_user_item(self, user_id: str) -> QTreeWidgetItem:
        """Return the tree row for *user_id*, creating it with an ID + avatar on first sight."""
        item = self._user_items.get(user_id)
        if item is not None:
            return item
        uid = self._user_ids.setdefault(user_id, len(self._user_ids) + 1)
        item = QTreeWidgetItem([f"#{uid}  {user_id}", "—"])
        item.setIcon(0, avatar_icon(user_id, user_id))
        item.setData(0, Qt.ItemDataRole.UserRole, user_id)
        self.tree.addTopLevelItem(item)
        self._user_items[user_id] = item
        # Auto-select the first sensor to appear so its graph shows without an
        # extra click; later rows don't steal the selection.
        if self._selected_user is None:
            self.tree.setCurrentItem(item)
        return item

    def _update_user_alert(self, item: QTreeWidgetItem, user_id: str, glucose: float) -> None:
        """Show a LOW/HIGH badge beside the user's name when out of the target range."""
        uid = self._user_ids.get(user_id, 0)
        base = f"#{uid}  {user_id}"
        if glucose < self._thresholds["tbr1_below"]:
            item.setText(0, f"{base}   ▼ LOW")
            item.setForeground(0, QBrush(QColor("#c0392b")))
        elif glucose > self._thresholds["tar1_above"]:
            item.setText(0, f"{base}   ▲ HIGH")
            item.setForeground(0, QBrush(QColor("#e67e22")))
        else:
            item.setText(0, base)
            item.setForeground(0, QBrush())

    def _set_row_offline(self, user_id: str, *, offline: bool) -> None:
        """Mark (or clear) the tree row for *user_id* as disconnected."""
        item = self._user_items.get(user_id)
        if item is None:
            return
        uid = self._user_ids.get(user_id, 0)
        base = f"#{uid}  {user_id}"
        if offline:
            item.setText(0, f"{base}   ⚊ offline")
            item.setForeground(0, QBrush(QColor("#7f8c8d")))
            item.setText(1, "—")
        else:
            item.setText(0, base)
            item.setForeground(0, QBrush())

    def _on_device_disconnected(self, address: str) -> None:
        """A BLE session ended — mark every tree row fed by that device offline (issue 06)."""
        for user_id, dev_id in self._user_dev.items():
            if dev_id == address:
                self._offline_users.add(user_id)
                self._set_row_offline(user_id, offline=True)

    def _on_user_selected(self, current: QTreeWidgetItem | None, _prev) -> None:
        """Switch which device's history is plotted, keeping every user's data.

        Every connected sensor's received stream is recorded to self._history
        from Start onward (see _on_new_message), all against the one shared
        self._graph_t0. Selecting a row just rebinds the plot buffers to that
        user's kept lists and redraws — no reset, no loss, no timeline drift,
        since the origin never moves between Start actions.
        """
        if current is None:
            return
        self._selected_user = current.data(0, Qt.ItemDataRole.UserRole) or current.text(0)
        self._bind_selected_history()
        self._redraw_graph()
        self._redraw_food_ex_graph()
        if not self._model_only:
            self._ax.set_title(
                f"Glucose — {self._selected_user}",
                color=self._graph_fg,
                fontweight="bold",
                fontsize=11,
            )
            self._canvas.draw_idle()

    # ------------------------------------------------------------------
    # Graph redraw helpers
    # ------------------------------------------------------------------

    def _sync_time_axis(self) -> None:
        """Give both stacked graphs the same x-range so points at the same time line up.

        Honours the rolling view window (self._view_window_s): when set, only the
        last N seconds are shown, while the full data arrays are kept so
        switching to "Entire run" reveals everything again.
        """
        xs = self._graph_x + self._expected_x + self._food_ex_x
        if not xs:
            self._visible_xlim = None
            return
        lo, hi = min(xs), max(xs)
        if hi <= lo:
            hi = lo + 1.0
        if self._view_window_s and (hi - lo) > self._view_window_s:
            lo = hi - self._view_window_s
        self._visible_xlim = (lo, hi)
        self._ax.set_xlim(lo, hi)
        self._fe_ax.set_xlim(lo, hi)

    def _in_view(self, xs: list[float], ys: list[float]) -> list[float]:
        """Return the ys whose x is inside the currently visible window (NaNs dropped)."""
        lo = self._visible_xlim[0] if self._visible_xlim else float("-inf")
        return [y for x, y in zip(xs, ys) if x >= lo and y == y]

    def _draw_pisa_spans(self) -> None:
        """(Re)shade the PISA intervals on the glucose graph."""
        for patch in self._pisa_patches:
            try:
                patch.remove()
            except (ValueError, AttributeError):
                pass
        self._pisa_patches = [
            self._ax.axvspan(a, b, color="#8e44ad", alpha=0.10, zorder=0)
            for a, b in self._pisa_spans
        ]

    def _on_view_window_changed(self) -> None:
        """Reload the graph time-window preference and redraw."""
        self._view_window_s = float(app_settings.load_pref("view_window_s", 3600.0))
        self._redraw_graph()
        self._redraw_food_ex_graph()

    # y-axis when the glucose graph has no data yet (mg/dL)
    _EMPTY_YLIM = (40.0, 200.0)

    def _fit_glucose_ylim(self) -> None:
        """Set the glucose y-axis to the data's own min/max plus a small margin.

        Explicit instead of autoscale so the range bands (which extend well
        past any real reading) can't stretch the axis up to 600.
        """
        ys = self._in_view(self._graph_x, self._graph_y) + self._in_view(
            self._expected_x, self._expected_y
        )
        if not ys:
            self._ax.set_ylim(*self._EMPTY_YLIM)
            return
        lo, hi = min(ys), max(ys)
        pad = max(10.0, (hi - lo) * 0.10)
        self._ax.set_ylim(max(0.0, lo - pad), hi + pad)

    def _redraw_graph(self) -> None:
        """Push updated x/y data to both line artists and request a canvas refresh."""
        self._line.set_data(self._graph_x, self._graph_y)
        self._recolor_main_trace()
        self._expected_line.set_data(self._expected_x, self._expected_y)
        self._sync_time_axis()  # sets self._visible_xlim, used by the helpers below
        self._draw_pisa_spans()
        self._fit_glucose_ylim()
        # Mean of the *received* (board) series only — never the expected line.
        # A freshly-selected sensor with an empty history would otherwise show
        # the mean of the Python model, which reads as "the board is tracking
        # the model".
        series = self._in_view(self._graph_x, self._graph_y)
        if series:
            mean = sum(series) / len(series)
            self._mean_line.set_ydata([mean, mean])
            self._mean_line.set_alpha(0.6)
        else:
            self._mean_line.set_alpha(0.0)
        self._update_stats_panel()
        self._canvas.draw_idle()
        self._fe_canvas.draw_idle()

    def _fe_graph_title(self) -> str:
        """Title for the food/exercise graph.

        In CSV playback there is no model running — the food log is replayed
        report-only and does not affect glucose (see issue 08). Say so, so the
        carb-rate curve isn't read as driving the trace above it.
        """
        if getattr(self._active_person, "data_source", "model") == "csv":
            return "Food log — report-only (CSV playback; does not drive glucose)"
        return "Food / Exercise"

    def _redraw_food_ex_graph(self) -> None:
        """Push updated x/y data to the food/exercise line artists and request a canvas refresh."""
        self._fe_ax.set_title(self._fe_graph_title(), color=self._graph_fg)
        self._carbs_line.set_data(self._food_ex_x, self._food_ex_carbs_y)
        self._exercise_line.set_data(self._food_ex_x, self._food_ex_exercise_y)
        self._sync_time_axis()
        cvis = self._in_view(self._food_ex_x, self._food_ex_carbs_y)
        evis = self._in_view(self._food_ex_x, self._food_ex_exercise_y)
        self._fe_ax.set_ylim(0.0, max(1.0, (max(cvis) if cvis else 0.0) * 1.15))
        self._fe_ax2.set_ylim(0.0, max(1.0, (max(evis) if evis else 0.0) * 1.15))
        self._fe_canvas.draw_idle()
        self._canvas.draw_idle()

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
