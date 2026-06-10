"""Main application window: toolbar, user treeview, live glucose graph."""
from __future__ import annotations

from PyQt6.QtGui import QCloseEvent
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

from data_thread import DataThread
from debug_window import DebugWindow


class MainWindow(QMainWindow):
    """Top-level window containing the toolbar, user treeview, and glucose graph.

    The toolbar provides access to the :class:`DebugWindow`.
    The treeview is populated dynamically as :class:`DataThread` emits messages;
    selecting a user loads their full reading history into the graph and
    subsequent readings for that user are appended live.
    """

    def __init__(self) -> None:
        """Set up the toolbar, data thread, treeview, graph, and layout."""
        super().__init__()
        self.setWindowTitle("TCC App")
        self.resize(1000, 600)

        self._setup_toolbar()
        self._debug_window: DebugWindow | None = None

        self._data_thread = DataThread(self)
        self._data_thread.new_message.connect(self._on_new_message)
        self._data_thread.start()

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
        """Create the top toolbar with a right-aligned Debug button."""
        toolbar = QToolBar()
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)
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
        """Create and return the matplotlib figure, canvas, axes, and line objects."""
        figure = Figure(tight_layout=True)
        canvas = FigureCanvas(figure)
        ax = figure.add_subplot(111)
        ax.set_title("Select a user")
        ax.set_xlabel("Reading #")
        ax.set_ylabel("Glucose (mg/dL)")
        (line,) = ax.plot([], [], lw=1.5)
        return figure, canvas, ax, line

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _open_debug(self) -> None:
        """Open (or raise) the debug messages window."""
        if self._debug_window is None:
            self._debug_window = DebugWindow(self._data_thread)
        self._debug_window.show()
        self._debug_window.raise_()
        self._debug_window.activateWindow()

    def _on_new_message(self, msg: dict) -> None:
        """Update the treeview row for the message's user and extend the graph if selected."""
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

        history = self._data_thread.get_user_data(user_id)
        self._graph_x = list(range(1, len(history) + 1))
        self._graph_y = [d["glucose_value"] for d in history]
        self._ax.set_title(f"Glucose — {user_id}")
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
        """Stop the data thread cleanly before the window closes."""
        self._data_thread.requestInterruption()
        self._data_thread.wait()
        super().closeEvent(event)
