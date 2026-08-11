"""Window that scans for nearby BLE devices and lets the user connect to one."""
from __future__ import annotations

from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ble_message_log import BleMessageLog
from ble_session import BleSession
from bluetooth_scanner import BluetoothScanThread


class BluetoothWindow(QWidget):
    """Top-level window listing nearby BLE devices with the ability to connect to one.

    A scan starts automatically when the window opens and can be repeated
    with the Rescan button. Selecting a row and pressing Connect opens a
    persistent BLE session to that device; every notification it receives is
    forwarded to the shared :class:`BleMessageLog` so the Debug window can
    display it. The connection is kept alive even if this window is closed,
    and is only stopped when connecting to a different device or on app exit
    (see :meth:`stop_session`).
    """

    def __init__(self, ble_log: BleMessageLog) -> None:
        """Build the device table and controls, then kick off the first scan."""
        super().__init__()
        self.setWindowTitle("Bluetooth Devices")
        self.resize(520, 400)

        self._ble_log = ble_log
        self._scan_thread: BluetoothScanThread | None = None
        self._session: BleSession | None = None
        self._addresses: list[str] = []

        layout = QVBoxLayout(self)

        self._status = QLabel("Scanning for nearby BLE devices…")
        layout.addWidget(self._status)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["Name", "Address", "RSSI"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self._table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        layout.addWidget(self._table)

        buttons = QHBoxLayout()
        self._rescan_btn = QPushButton("Rescan")
        self._rescan_btn.clicked.connect(self._start_scan)
        buttons.addWidget(self._rescan_btn)
        self._connect_btn = QPushButton("Connect")
        self._connect_btn.clicked.connect(self._connect_selected)
        buttons.addWidget(self._connect_btn)
        self._disconnect_btn = QPushButton("Disconnect")
        self._disconnect_btn.setEnabled(False)
        self._disconnect_btn.clicked.connect(self._disconnect_clicked)
        buttons.addWidget(self._disconnect_btn)
        layout.addLayout(buttons)

        self._start_scan()

    # ------------------------------------------------------------------
    # Scanning
    # ------------------------------------------------------------------

    def _start_scan(self) -> None:
        """Clear the table and start a fresh BLE scan in the background."""
        self._table.setRowCount(0)
        self._addresses = []
        self._status.setText("Scanning for nearby BLE devices…")
        self._rescan_btn.setEnabled(False)

        self._scan_thread = BluetoothScanThread(self)
        self._scan_thread.device_found.connect(self._add_device)
        self._scan_thread.scan_failed.connect(self._on_scan_failed)
        self._scan_thread.finished.connect(self._on_scan_finished)
        self._scan_thread.start()

    def _add_device(self, name: str, address: str, rssi: int) -> None:
        """Append a row for a newly discovered device, skipping duplicates."""
        if address in self._addresses:
            return
        self._addresses.append(address)
        row = self._table.rowCount()
        self._table.insertRow(row)
        self._table.setItem(row, 0, QTableWidgetItem(name))
        self._table.setItem(row, 1, QTableWidgetItem(address))
        self._table.setItem(row, 2, QTableWidgetItem(str(rssi)))

    def _on_scan_failed(self, message: str) -> None:
        """Show the scan error to the user."""
        self._status.setText(f"Scan failed: {message}")

    def _on_scan_finished(self) -> None:
        """Re-enable the Rescan button and report how many devices were found."""
        self._rescan_btn.setEnabled(True)
        if "failed" not in self._status.text().lower():
            count = self._table.rowCount()
            self._status.setText(f"Found {count} device(s). Select one and press Connect.")

    # ------------------------------------------------------------------
    # Connecting
    # ------------------------------------------------------------------

    def _connect_selected(self) -> None:
        """Stop any existing session and open a new persistent BLE connection."""
        row = self._table.currentRow()
        if row < 0:
            self._status.setText("Select a device first.")
            return

        self.stop_session()

        address = self._addresses[row]
        name = self._table.item(row, 0).text()
        self._status.setText(f"Connecting to {name}…")
        self._connect_btn.setEnabled(False)

        self._session = BleSession(address, name, self)
        self._session.connected.connect(self._on_connected)
        self._session.connect_failed.connect(self._on_connect_failed)
        self._session.disconnected.connect(self._on_disconnected)
        self._session.new_message.connect(self._ble_log.add_message)
        self._session.finished.connect(self._on_session_finished)
        self._session.start()
        self._disconnect_btn.setEnabled(True)

    def _on_connected(self, address: str, subscribed: int, notify_total: int, last_error: str) -> None:
        """Report a successful connection and whether the device can push any data at all."""
        if notify_total == 0:
            self._status.setText(
                f"Connected to {address}, but it exposes no notify/indicate characteristics — "
                "it will not send data on its own."
            )
        elif subscribed == 0 and "authentication" in last_error.lower():
            self._status.setText(
                f"Connected to {address}, but its data requires a paired/authenticated "
                "connection (device likely shows a passkey on its own screen or serial "
                "console). Pair it first in Windows Settings > Bluetooth & devices, "
                "entering that passkey, then reconnect here."
            )
        elif subscribed == 0:
            self._status.setText(
                f"Connected to {address}. Found {notify_total} notification characteristic(s) "
                f"but could not subscribe to any ({last_error})."
            )
        else:
            self._status.setText(
                f"Connected to {address}. Subscribed to {subscribed}/{notify_total} "
                "notification characteristic(s) — waiting for data in Debug."
            )

    def _on_connect_failed(self, address: str, error: str) -> None:
        """Report a failed connection attempt."""
        self._status.setText(f"Failed to connect to {address}: {error}")

    def _on_disconnected(self, address: str) -> None:
        """Report that a previously connected device disconnected."""
        self._status.setText(f"Disconnected from {address}.")

    def _on_session_finished(self) -> None:
        """Re-enable Connect and disable Disconnect once the session thread has stopped."""
        self._connect_btn.setEnabled(True)
        self._disconnect_btn.setEnabled(False)

    def _disconnect_clicked(self) -> None:
        """Disconnect the active BLE session, if any."""
        if self._session is None or not self._session.isRunning():
            return
        self._disconnect_btn.setEnabled(False)
        self._status.setText("Disconnecting…")
        self.stop_session()

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def stop_session(self) -> None:
        """Stop the active BLE session, if any, and wait for it to fully close.

        Called before starting a new connection, and by :class:`MainWindow`
        on app close — deliberately *not* called from :meth:`closeEvent` so
        that closing this window alone leaves the connection (and Debug
        logging) running.
        """
        if self._session is not None and self._session.isRunning():
            self._session.stop()
            self._session.wait(2000)
        self._session = None

    # ------------------------------------------------------------------
    # Qt overrides
    # ------------------------------------------------------------------

    def closeEvent(self, event: QCloseEvent) -> None:
        """Wait for the scan thread to finish; the BLE session is left running."""
        if self._scan_thread is not None and self._scan_thread.isRunning():
            self._scan_thread.wait(2000)
        super().closeEvent(event)
