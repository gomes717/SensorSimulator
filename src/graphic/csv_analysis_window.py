"""CSV Analysis window: load a Dexcom CGM export, pick a 24 h window with a slider,
zoom the trace, and read the range metrics (TIR/TBR/TAR, mean, variance) for it.

The picked 24 h window can also be assigned to a patient as their data source
("Assign window to person…"), after which the app's engine and the board both
replay it instead of running a physiological model (see docs/TODO.md,
PROTOCOL_SPEC.md's "CSV playback data source" section).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from models.types import PersonProfile

import matplotlib  # pylint: disable=wrong-import-order

matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas  # noqa: E402
from matplotlib.backends.backend_qtagg import NavigationToolbar2QT  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402

from models import app_settings, cgm_metrics, dexcom_csv, food_log_csv

_DATASET_DIR = Path(__file__).resolve().parent.parent.parent / "dataset"
_WINDOW_HOURS = 24.0
_SLIDER_STEPS = 1000  # slider resolution over the movable range


class CsvAnalysisWindow(QWidget):
    """Load a CGM CSV, slide a 24 h window over it, and show that window's metrics."""

    def __init__(
        self,
        persons: list[PersonProfile] | None = None,
        on_assign: Callable[[PersonProfile], None] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("CSV Analysis")
        self.resize(900, 640)

        self._persons = persons if persons is not None else []
        self._on_assign = on_assign
        self._csv_path: str | None = None
        self._sel_start_dt: datetime | None = None

        self._times: list[datetime] = []
        self._values: list[float] = []
        self._hours: list[float] = []  # elapsed hours from the first reading
        self._start_hour = 0.0  # left edge of the selected 24 h window

        layout = QVBoxLayout(self)

        top = QHBoxLayout()
        open_btn = QPushButton("Open CSV…")
        open_btn.clicked.connect(self._open_csv)
        top.addWidget(open_btn)
        self._file_label = QLabel("No file loaded")
        top.addWidget(self._file_label, 1)
        layout.addLayout(top)

        self._figure, self._canvas, self._ax, self._span = self._build_graph()
        layout.addWidget(NavigationToolbar2QT(self._canvas, self))
        layout.addWidget(self._canvas, 1)

        slider_row = QHBoxLayout()
        slider_row.addWidget(QLabel("24 h window start:"))
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, _SLIDER_STEPS)
        self._slider.setEnabled(False)
        self._slider.valueChanged.connect(self._on_slider)
        slider_row.addWidget(self._slider, 1)
        layout.addLayout(slider_row)

        self._range_label = QLabel("—")
        layout.addWidget(self._range_label)

        assign_row = QHBoxLayout()
        self._assign_btn = QPushButton("Assign window to person…")
        self._assign_btn.setEnabled(False)
        self._assign_btn.clicked.connect(self._assign_to_person)
        assign_row.addWidget(self._assign_btn)
        self._assign_status = QLabel("")
        assign_row.addWidget(self._assign_status, 1)
        layout.addLayout(assign_row)

        layout.addWidget(self._build_stats_group())

    # ------------------------------------------------------------------
    # Builders
    # ------------------------------------------------------------------

    def _build_graph(self):
        palette = QApplication.instance().palette()
        bg = palette.color(QPalette.ColorRole.Window).name()
        fg = palette.color(QPalette.ColorRole.WindowText).name()
        self._accent = palette.color(QPalette.ColorRole.Highlight).name()  # 24 h selection span

        figure = Figure(facecolor=bg)
        figure.subplots_adjust(left=0.09, right=0.97, top=0.92, bottom=0.15)
        canvas = FigureCanvas(figure)
        ax = figure.add_subplot(111, facecolor=bg)
        ax.set_title("Open a CGM CSV", color=fg)
        ax.set_xlabel("Hours from start", color=fg)
        ax.set_ylabel("Glucose (mg/dL)", color=fg)
        ax.tick_params(colors=fg)
        for spine in ax.spines.values():
            spine.set_color(fg)
        ax.grid(True, color=fg, alpha=0.15)
        self._range_bands: list = []
        # Faint continuous base line, then one colored overlay per range category
        # (red = low/high, yellow = borderline, green = in target). The overlays
        # carry NaN outside their category so each shows only its own segments.
        (self._line,) = ax.plot([], [], lw=0.8, color=fg, alpha=0.35)
        self._seg_lines = {
            cat: ax.plot([], [], lw=1.6, color=color, solid_capstyle="round")[0]
            for cat, color in self._LINE_COLORS.items()
        }
        self._apply_range_bands(ax)
        ax.set_ylim(40.0, 200.0)  # replaced with data min/max ± margin on load
        return figure, canvas, ax, None

    # red = TBR2/TAR2 (danger), yellow = TBR1/TAR1 (borderline), green = TIR (target)
    _RED = "#d32f2f"
    _YELLOW = "#f4b400"
    _GREEN = "#2e7d32"
    _LINE_COLORS = {"r": _RED, "y": _YELLOW, "g": _GREEN}
    _BAND_COLORS = (_RED, _YELLOW, _GREEN, _YELLOW, _RED)

    def _category(self, value: float, t: dict) -> str:
        """'r' if out of range (low/high), 'y' if borderline, 'g' if in target."""
        if value < t["tbr2_below"] or value > t["tar2_above"]:
            return "r"
        if value < t["tbr1_below"] or value > t["tar1_above"]:
            return "y"
        return "g"

    def _apply_range_bands(self, ax=None) -> None:
        """Draw the 5 horizontal range bands from the current saved thresholds."""
        ax = ax or self._ax
        for patch in getattr(self, "_range_bands", []):
            patch.remove()
        t = app_settings.load()
        edges = [0.0, t["tbr2_below"], t["tbr1_below"], t["tar1_above"], t["tar2_above"], 600.0]
        self._range_bands = [
            ax.axhspan(lo, hi, color=color, alpha=0.16, zorder=0)
            for lo, hi, color in zip(edges, edges[1:], self._BAND_COLORS)
        ]

    def _recolor_trace(self) -> None:
        """Split the loaded trace into red/yellow/green overlays by range category."""
        n = len(self._values)
        if n == 0:
            return
        t = app_settings.load()
        cats = [self._category(v, t) for v in self._values]
        nan = float("nan")
        arrs = {"r": [nan] * n, "y": [nan] * n, "g": [nan] * n}
        for i, v in enumerate(self._values):
            here = cats[i]
            arrs[here][i] = v
            # also emit this point into a neighbor's colour so the crossing
            # segment is drawn (otherwise a 1-sample gap appears at each edge)
            if i > 0 and cats[i - 1] != here:
                arrs[cats[i - 1]][i] = v
            if i < n - 1 and cats[i + 1] != here:
                arrs[cats[i + 1]][i] = v
        for cat, line in self._seg_lines.items():
            line.set_data(self._hours, arrs[cat])

    def _build_stats_group(self) -> QGroupBox:
        group = QGroupBox("Selected 24 h window")
        form = QFormLayout(group)
        self._stat_labels: dict[str, QLabel] = {}
        for key, caption in (
            ("n", "Readings"),
            ("mean", "Mean glucose (mg/dL)"),
            ("variance", "Variance"),
            ("sd", "Std. deviation"),
            ("cv", "CV (%)"),
            ("tir", "TIR (%)"),
            ("tbr", "TBR (%)  [TBR1 / TBR2]"),
            ("tar", "TAR (%)  [TAR1 / TAR2]"),
        ):
            lbl = QLabel("—")
            self._stat_labels[key] = lbl
            form.addRow(f"{caption}:", lbl)
        return group

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _open_csv(self) -> None:
        start_dir = str(_DATASET_DIR) if _DATASET_DIR.is_dir() else ""
        path, _ = QFileDialog.getOpenFileName(
            self, "Open CGM CSV", start_dir, "CSV files (*.csv);;All files (*)"
        )
        if not path:
            return
        try:
            rows = dexcom_csv.read_egv(path)
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "CSV Analysis", f"Could not read that CSV:\n{exc}")
            return

        self._csv_path = path
        self._assign_btn.setEnabled(True)
        self._times = [ts for ts, _ in rows]
        self._values = [g for _, g in rows]
        t0 = self._times[0]
        self._hours = [(ts - t0).total_seconds() / 3600.0 for ts in self._times]
        self._file_label.setText(f"{Path(path).name}  ({len(rows)} readings)")

        self._line.set_data(self._hours, self._values)
        self._apply_range_bands()  # pick up any threshold edits since the window opened
        self._recolor_trace()
        self._ax.set_title(Path(path).name, color=QApplication.instance().palette().color(QPalette.ColorRole.WindowText).name())
        # Fit the axes to the data (min - extra .. max + extra), not to the
        # range bands, which run far past any real reading.
        lo, hi = min(self._values), max(self._values)
        pad = max(10.0, (hi - lo) * 0.08)
        self._ax.set_xlim(self._hours[0], self._hours[-1])
        self._ax.set_ylim(max(0.0, lo - pad), hi + pad)

        total = self._hours[-1]
        movable = max(0.0, total - _WINDOW_HOURS)
        self._slider.blockSignals(True)
        self._slider.setValue(0)
        self._slider.setEnabled(movable > 0.0)
        self._slider.blockSignals(False)
        self._start_hour = 0.0
        self._refresh_selection()

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def _on_slider(self, step: int) -> None:
        if not self._hours:
            return
        movable = max(0.0, self._hours[-1] - _WINDOW_HOURS)
        self._start_hour = movable * (step / _SLIDER_STEPS)
        self._refresh_selection()

    def _refresh_selection(self) -> None:
        if not self._hours:
            return
        total = self._hours[-1]
        lo = self._start_hour
        hi = lo + _WINDOW_HOURS if total >= _WINDOW_HOURS else total

        if self._span is not None:
            self._span.remove()
        self._span = self._ax.axvspan(lo, hi, color=self._accent, alpha=0.15)
        self._canvas.draw_idle()

        t0 = self._times[0]
        start_dt = t0 + timedelta(hours=lo)
        end_dt = t0 + timedelta(hours=hi)
        self._sel_start_dt = start_dt
        self._range_label.setText(
            f"{start_dt:%Y-%m-%d %H:%M}  →  {end_dt:%Y-%m-%d %H:%M}"
        )

        sel = [g for h, g in zip(self._hours, self._values) if lo <= h <= hi]
        thresholds = app_settings.load()
        m = cgm_metrics.compute(
            sel,
            tbr2_below=thresholds["tbr2_below"],
            tbr1_below=thresholds["tbr1_below"],
            tar1_above=thresholds["tar1_above"],
            tar2_above=thresholds["tar2_above"],
        )
        self._stat_labels["n"].setText(str(m.n))
        self._stat_labels["mean"].setText(f"{m.mean:.1f}")
        self._stat_labels["variance"].setText(f"{m.variance:.1f}")
        self._stat_labels["sd"].setText(f"{m.sd:.1f}")
        self._stat_labels["cv"].setText(f"{m.cv:.1f}")
        self._stat_labels["tir"].setText(f"{m.tir_pct:.1f}")
        self._stat_labels["tbr"].setText(f"{m.tbr_pct:.1f}   [{m.tbr1_pct:.1f} / {m.tbr2_pct:.1f}]")
        self._stat_labels["tar"].setText(f"{m.tar_pct:.1f}   [{m.tar1_pct:.1f} / {m.tar2_pct:.1f}]")

    # ------------------------------------------------------------------
    # Assign the selected 24 h window to a patient as their data source
    # ------------------------------------------------------------------

    def _assign_to_person(self) -> None:
        """Make the picked 24 h window a patient's CSV data source.

        Sets data_source/csv_path/csv_window_start_iso on the chosen
        PersonProfile (optionally a matching Food Log CSV too) and calls the
        on_assign callback so MainWindow persists and restarts its engine.
        """
        if not self._persons:
            QMessageBox.information(self, "CSV Analysis", "No patients to assign to.")
            return
        if self._csv_path is None or self._sel_start_dt is None:
            QMessageBox.information(self, "CSV Analysis", "Load a CSV and pick a window first.")
            return

        names = [p.name for p in self._persons]
        name, ok = QInputDialog.getItem(
            self, "Assign window", "Assign this 24 h window to patient:", names, 0, False
        )
        if not ok:
            return
        person = self._persons[names.index(name)]

        # The Food Log is paired by ID (Dexcom_001.csv <-> Food_Log_001.csv),
        # so it is picked up automatically — no separate file chooser.
        food_log = food_log_csv.matching_food_log_path(self._csv_path)

        person.data_source = "csv"
        person.csv_path = self._csv_path
        person.csv_window_start_iso = self._sel_start_dt.isoformat()
        person.food_log_path = food_log

        self._assign_status.setText(
            f"✓ Assigned to {person.name}"
            + (f"  (+ food log {Path(food_log).name})" if food_log else "  (no matching food log)")
        )
        if self._on_assign is not None:
            self._on_assign(person)
