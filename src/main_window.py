"""Main application window: toolbar, user treeview, live glucose graph."""
from __future__ import annotations

from PyQt6.QtGui import QCloseEvent, QPalette
from PyQt6.QtWidgets import (
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QToolBar,
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
)
from PyQt6.QtCore import Qt

import matplotlib  # pylint: disable=wrong-import-order
matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402

from ble_message_log import BleMessageLog
from bluetooth_window import BluetoothWindow
from debug_window import DebugWindow


class MainWindow(QMainWindow):
    """Top-level window containing the toolbar, user treeview, and glucose graph.

    The toolbar provides access to the :class:`DebugWindow` and the
    :class:`BluetoothWindow`. The treeview and graph are populated live from
    :class:`BleMessageLog` — every BLE message carrying a decoded
    ``glucose_value`` (see :class:`BleSession`) updates the selected user's
    row and, if that user's history is currently shown, extends the graph.
    """

    def __init__(self) -> None:
        """Set up the toolbar, BLE message log, treeview, graph, and layout."""
        super().__init__()
        self.setWindowTitle("TCC App")
        self.resize(1000, 600)

        self._setup_toolbar()
        self._debug_window: DebugWindow | None = None
        self._bluetooth_window: BluetoothWindow | None = None
        self._ble_log = BleMessageLog(self)
        self._ble_log.new_message.connect(self._on_new_message)

        self._user_items: dict[str, QTreeWidgetItem] = {}
        self.tree = self._build_tree()

        self._graph_x: list[int] = []
        self._graph_y: list[float] = []
        self._selected_user: str | None = None
        self._figure, self._canvas, self._ax, self._line = self._build_graph()

        self._bottom = QWidget()

        right_splitter = QSplitter(Qt.Orientation.Vertical)
        right_splitter.addWidget(self._canvas)
        right_splitter.addWidget(self._bottom)
        right_splitter.setSizes([400, 200])

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.tree)
        splitter.addWidget(right_splitter)
        splitter.setSizes([300, 700])

        self.setCentralWidget(splitter)

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
        """Create and return the matplotlib figure, canvas, axes, and line objects.

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
        ax.set_xlabel("Reading #", color=fg)
        ax.set_ylabel("Glucose (mg/dL)", color=fg)
        ax.tick_params(colors=fg)
        for spine in ax.spines.values():
            spine.set_color(fg)
        ax.grid(True, color=fg, alpha=0.15)
        (line,) = ax.plot([], [], lw=1.5, color=accent)
        return figure, canvas, ax, line

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _open_debug(self) -> None:
        """Open (or raise) the debug messages window."""
        if self._debug_window is None:
            self._debug_window = DebugWindow(self._ble_log)
        self._debug_window.show()
        self._debug_window.raise_()
        self._debug_window.activateWindow()

    def _open_bluetooth(self) -> None:
        """Open (or raise) the Bluetooth devices window, starting a scan if new."""
        if self._bluetooth_window is None:
            self._bluetooth_window = BluetoothWindow(self._ble_log)
        self._bluetooth_window.show()
        self._bluetooth_window.raise_()
        self._bluetooth_window.activateWindow()

    def _on_new_message(self, msg: dict) -> None:
        """Update the treeview row for the message's user and extend the graph if selected.

        Ignores BLE messages that aren't decoded glucose readings (e.g.
        control-point responses from other characteristics).
        """
        if "glucose_value" not in msg:
            return
        user_id = msg["user_id"]
        glucose = msg["glucose_value"]

        item = self._user_items.get(user_id)
        if item is None:
            item = QTreeWidgetItem([user_id, "—"])
            self.tree.addTopLevelItem(item)
            self._user_items[user_id] = item
        item.setText(1, f"{glucose:.1f}")

        if user_id == self._selected_user:
            self._graph_x.append(len(self._graph_x) + 1)
            self._graph_y.append(glucose)
            self._redraw_graph()

    def _on_user_selected(self, current: QTreeWidgetItem | None, _prev) -> None:
        """Load the full reading history for the selected user into the graph."""
        if current is None:
            return
        user_id = current.text(0)
        self._selected_user = user_id

        history = [
            msg["glucose_value"]
            for msg in self._ble_log.get_messages()
            if msg.get("user_id") == user_id and "glucose_value" in msg
        ]
        self._graph_x = list(range(1, len(history) + 1))
        self._graph_y = history
        self._ax.set_title(f"Glucose — {user_id}", color=self._graph_fg)
        self._redraw_graph()

    # ------------------------------------------------------------------
    # Graph helpers
    # ------------------------------------------------------------------

    def _redraw_graph(self) -> None:
        """Push updated x/y data to the line artist and request a canvas refresh."""
        self._line.set_data(self._graph_x, self._graph_y)
        self._ax.relim()
        self._ax.autoscale_view()
        self._canvas.draw_idle()

    # ------------------------------------------------------------------
    # Qt overrides
    # ------------------------------------------------------------------

    def closeEvent(self, event: QCloseEvent) -> None:
        """Stop any live BLE session, then close every child window."""
        if self._bluetooth_window is not None:
            self._bluetooth_window.stop_session()
            self._bluetooth_window.close()
        if self._debug_window is not None:
            self._debug_window.close()
        super().closeEvent(event)
