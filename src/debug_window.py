"""Window that shows all BLE messages received so far and lets the user inspect each one."""
from __future__ import annotations

from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import Qt

from ble_message_log import BleMessageLog
from message_detail_window import MessageDetailWindow


class DebugWindow(QWidget):
    """Top-level window that lists every message received over BLE connections.

    Rows are added live as new messages arrive from :class:`BleMessageLog`.
    Auto-scroll follows the latest entry unless the user scrolls upward;
    scrolling back to the bottom re-enables auto-scroll.  Clicking any row
    opens a :class:`MessageDetailWindow` for that message.
    """

    def __init__(self, ble_log: BleMessageLog) -> None:
        """Initialise the window, bulk-load existing messages, and subscribe to new ones."""
        super().__init__()
        self.setWindowTitle("Debug Messages")
        self.resize(720, 450)

        self._messages: list[dict] = []
        self._detail_windows: list[MessageDetailWindow] = []

        layout = QVBoxLayout(self)

        info = QLabel("Messages received over the connected BLE device. Click a row to inspect it.")
        info.setAlignment(Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(info)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["#", "User", "Timestamp"])
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self._table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self._table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.itemClicked.connect(self._open_detail)
        layout.addWidget(self._table)

        self._auto_scroll = True
        self._table.verticalScrollBar().valueChanged.connect(self._on_scroll)

        self._bulk_load(ble_log.get_messages())
        ble_log.new_message.connect(self._on_new_message)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _bulk_load(self, messages: list[dict]) -> None:
        """Pre-allocate all rows for *messages* in a single repaint cycle."""
        if not messages:
            return
        self._table.setUpdatesEnabled(False)
        self._table.setSortingEnabled(False)
        start = self._table.rowCount()
        self._table.setRowCount(start + len(messages))
        for i, msg in enumerate(messages):
            row = start + i
            self._messages.append(msg)
            self._table.setItem(row, 0, QTableWidgetItem(str(row + 1)))
            self._table.setItem(row, 1, QTableWidgetItem(msg.get("user_id", "")))
            self._table.setItem(row, 2, QTableWidgetItem(msg.get("timestamp", "")))
        self._table.setUpdatesEnabled(True)
        if self._auto_scroll:
            self._table.scrollToBottom()

    def _append_row(self, msg: dict) -> None:
        """Insert a single row for *msg* and optionally scroll to it."""
        self._messages.append(msg)
        row = self._table.rowCount()
        self._table.insertRow(row)
        self._table.setItem(row, 0, QTableWidgetItem(str(row + 1)))
        self._table.setItem(row, 1, QTableWidgetItem(msg.get("user_id", "")))
        self._table.setItem(row, 2, QTableWidgetItem(msg.get("timestamp", "")))
        if self._auto_scroll:
            self._table.scrollToBottom()

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_scroll(self, value: int) -> None:
        """Enable auto-scroll when the user reaches the bottom, disable otherwise."""
        self._auto_scroll = value == self._table.verticalScrollBar().maximum()

    def _on_new_message(self, msg: dict) -> None:
        """Slot connected to BleMessageLog.new_message — appends the row live."""
        self._append_row(msg)

    def _open_detail(self, item: QTableWidgetItem) -> None:
        """Open a detail window for the message corresponding to the clicked row."""
        msg = self._messages[item.row()]
        window = MessageDetailWindow(msg)
        window.show()
        self._detail_windows.append(window)

    # ------------------------------------------------------------------
    # Qt overrides
    # ------------------------------------------------------------------

    def closeEvent(self, event: QCloseEvent) -> None:
        """Close every open message detail window before this window closes."""
        for window in self._detail_windows:
            window.close()
        super().closeEvent(event)
