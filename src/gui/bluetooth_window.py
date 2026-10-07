"""Window that scans for nearby BLE devices and lets the user connect to one."""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
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

from core.ble_message_log import BleMessageLog
from models import board_layout
from services.ble_session import BleSession
from services.bluetooth_scanner import UNKNOWN_DEVICE_NAME, BluetoothScanThread


class BluetoothWindow(QWidget):
    """Top-level window listing nearby BLE devices with the ability to connect to several.

    A scan starts automatically when the window opens and can be repeated
    with the Rescan button. Selecting a row and pressing Connect opens a
    persistent BLE session to that device *in addition to* any others already
    connected — up to one per distinct address — so multiple sensors (e.g.
    a batch of Nordic CGM boards) can stream at once. Every notification is
    forwarded to the shared :class:`BleMessageLog` so the Debug window can
    display it. Connections are kept alive even if this window is closed,
    and are only stopped by selecting a connected device and pressing
    Disconnect, or on app exit (see :meth:`stop_all_sessions`).
    """

    # A session finished connecting: (address, the BleSession). The main window
    # opens that sensor's tab from this, not from its first glucose reading.
    session_connected = pyqtSignal(str, object)

    def __init__(self, ble_log: BleMessageLog) -> None:
        """Build the device table and controls, then kick off the first scan."""
        super().__init__()
        self.setWindowTitle("Bluetooth Devices")
        self.resize(560, 400)

        self._ble_log = ble_log
        self._scan_thread: BluetoothScanThread | None = None
        self._sessions: dict[str, BleSession] = {}  # address -> active session
        # address -> UI label: the assigned patient (board_layout.json) when the
        # advertised name maps to a configured slot, else the raw advertised name.
        self._names: dict[str, str] = {}
        self._advertised: dict[str, str] = {}  # address -> raw advertised BLE name
        self._statuses: dict[str, str] = {}  # address -> last known Status cell text
        self._addresses: list[str] = []

        layout = QVBoxLayout(self)

        self._status = QLabel("Scanning for nearby BLE devices…")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["Name", "Address", "RSSI", "Status"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self._table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self._table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.ResizeToContents
        )
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.currentCellChanged.connect(self._on_selection_changed)
        layout.addWidget(self._table)

        buttons = QHBoxLayout()
        self._rescan_btn = QPushButton("Rescan")
        self._rescan_btn.clicked.connect(self._start_scan)
        buttons.addWidget(self._rescan_btn)
        self._connect_btn = QPushButton("Connect")
        self._connect_btn.setEnabled(False)
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
        """Clear the table and start a fresh BLE scan in the background.

        Rows for already-connected devices are re-seeded immediately after
        clearing: many BLE peripherals (including the Nordic CGM boards)
        stop advertising once connected, so they would otherwise vanish
        from the list on rescan even though their session is still live.
        """
        self._table.setRowCount(0)
        self._addresses = []
        for address in list(self._sessions):
            self._add_device(self._advertised.get(address, address), address, None)
        self._status.setText("Scanning for nearby BLE devices…")
        self._rescan_btn.setEnabled(False)

        self._scan_thread = BluetoothScanThread(self)
        self._scan_thread.device_found.connect(self._add_device)
        self._scan_thread.scan_failed.connect(self._on_scan_failed)
        self._scan_thread.finished.connect(self._on_scan_finished)
        self._scan_thread.start()

    def _add_device(self, name: str, address: str, rssi: int | None) -> None:
        """Append a row for a newly discovered device, skipping duplicates.

        *rssi* is ``None`` when re-seeding a row for an already-connected
        device that wasn't actually found in this scan (see
        :meth:`_start_scan`).
        """
        if address in self._addresses:
            return
        if name == UNKNOWN_DEVICE_NAME and address in self._advertised:
            # A rescan that missed this device's name must not forget the one we
            # already learned: the numbered name is what picks the sensor slot.
            name = self._advertised[address]
        self._advertised[address] = name
        label = board_layout.device_label(name)
        self._addresses.append(address)
        row = self._table.rowCount()
        self._table.insertRow(row)
        self._table.setItem(row, 0, QTableWidgetItem(label))
        self._table.setItem(row, 1, QTableWidgetItem(address))
        self._table.setItem(row, 2, QTableWidgetItem(str(rssi) if rssi is not None else "–"))
        self._table.setItem(row, 3, QTableWidgetItem(self._statuses.get(address, "")))

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
        """Open a persistent BLE connection to the selected device, alongside any others."""
        row = self._table.currentRow()
        if row < 0:
            self._status.setText("Select a device first.")
            return

        address = self._addresses[row]
        if address in self._sessions:
            self._status.setText(f"Already connected to {address}.")
            return

        self._open_session(address, self._advertised.get(address, address))

    def _open_session(self, address: str, advertised: str) -> None:
        """Wire up and start a BleSession for *address*, tracked in self._sessions.

        *advertised* is the raw BLE name (drives the per-slot demux + pairing
        decision); the tree/graph show the assigned patient when one is
        configured for this slot in board_layout.json (see models/board_layout)."""
        label = board_layout.device_label(advertised)
        self._names[address] = label
        self._advertised[address] = advertised
        self._status.setText(f"Connecting to {label}…")
        self._set_status_cell(address, "Connecting…")

        session = BleSession(
            address, advertised, self, display_name=board_layout.device_label(advertised)
        )
        session.connected.connect(self._on_connected)
        session.connect_failed.connect(self._on_connect_failed)
        session.link_silent.connect(self._on_link_silent)
        session.disconnected.connect(
            lambda addr, mine=session: self._on_session_disconnected(addr, mine)
        )
        session.new_message.connect(self._ble_log.add_message)
        session.finished.connect(
            lambda addr=address, ended=session: self._on_session_finished(addr, ended)
        )
        self._sessions[address] = session
        session.start()
        self._update_button_states()

    def reconnect(self, address: str, delay_ms: int = 2500) -> None:
        """Drop the session for *address* and reopen it after *delay_ms*.

        Used after the board is told to change something that forces it to
        re-advertise (e.g. the BLE comm profile) — the firmware drops the link
        and this re-establishes it under the new advertising without the user
        having to go through the device list again.
        """
        advertised = self._advertised.get(address)
        if advertised is None:
            return
        self._stop_session(address)
        from PyQt6.QtCore import QTimer

        QTimer.singleShot(delay_ms, lambda: self._open_session(address, advertised))

    def _on_connected(
        self, address: str, subscribed: int, notify_total: int, last_error: str
    ) -> None:
        """Report a successful connection and whether the device can push any data at all."""
        self._set_status_cell(address, "Connected")
        session = self._sessions.get(address)
        if session is not None:
            self.session_connected.emit(address, session)
        if notify_total == 0:
            self._status.setText(
                f"Connected to {address}, but it exposes no notify/indicate characteristics — "
                "it will not send data on its own."
            )
        elif subscribed == 0 and (
            "auto-pairing failed" in last_error.lower() or "authentication" in last_error.lower()
        ):
            self._status.setText(
                f"Connected to {address}, but its data requires a paired/authenticated "
                f"connection and the app's automatic pairing failed ({last_error}). "
                "Disconnect and press Connect again to retry — every attempt clears any "
                "stale pairing first — or confirm the sensor's fixed-passkey firmware is "
                "flashed if it keeps failing."
            )
        elif subscribed == 0:
            self._status.setText(
                f"Connected to {address}. Found {notify_total} notification characteristic(s) "
                f"but could not subscribe to any ({last_error})."
            )
        else:
            # subscribed is intentionally a small subset of notify_total on a
            # multi-sensor board — this session only subscribes to its own
            # slot's stream plus the shared chars (see ble_session.py's
            # _FUNCTIONAL_NOTIFY_UUIDS), not every sibling instance.
            tail = f" ({last_error})" if last_error else ""
            self._status.setText(
                f"Connected to {address}. Streaming from {subscribed} characteristic(s){tail}."
            )

    def _on_connect_failed(self, address: str, error: str) -> None:
        """Report a failed connection attempt."""
        self._set_status_cell(address, "Failed")
        self._status.setText(f"Failed to connect to {address}: {error}")

    def _on_link_silent(self, address: str, silent: bool) -> None:
        """Show a link that is up but delivering nothing, and clear it when data resumes.

        The row would otherwise read "Connected" while the sensor never draws a
        point — the failure that is invisible from connection state alone."""
        label = self._names.get(address, address)
        if silent:
            self._set_status_cell(address, "No data")
            self._status.setText(
                f"{label} is connected but sending no measurements. The app keeps re-arming "
                "its subscription; if it stays silent, disconnect and press Connect again."
            )
        else:
            self._set_status_cell(address, "Connected")
            self._status.setText(f"{label}: measurements are flowing again.")

    def _on_session_disconnected(self, address: str, ended: BleSession) -> None:
        """Handle a session ending — unless a newer one already replaced it.

        A reconnect opens the new session before the old one finishes closing, so
        its late ``disconnected`` must not blank the new row's status or badge the
        live sensor offline."""
        if self._sessions.get(address, ended) is not ended:
            return
        self._on_disconnected(address)
        self._ble_log.note_disconnected(address)

    def _on_disconnected(self, address: str) -> None:
        """Report that a previously connected device disconnected."""
        self._set_status_cell(address, "")
        self._status.setText(f"Disconnected from {address}.")

    def _on_session_finished(self, address: str, ended: BleSession | None = None) -> None:
        """Drop the finished session and refresh button state once its thread has stopped.

        *ended* is the session whose thread just stopped. A Disconnect (or the
        automatic reconnect) followed by a new Connect to the same address puts
        a NEW session under that key before the old thread's ``finished`` arrives;
        cleaning up by address alone then wiped the new session's entry, name and
        status — the row went back to "Connect", and the next Connect re-opened
        it under the bare MAC instead of the advertised name."""
        current = self._sessions.get(address)
        if ended is not None and current is not None and current is not ended:
            return
        # _advertised is deliberately kept: it is the listed row's name, not
        # session state. Dropping it here (a failed or ended connection) made the
        # next Connect fall back to the bare MAC, which has no sensor slot, so the
        # session streamed every sensor's data into one row named after the MAC.
        self._sessions.pop(address, None)
        self._names.pop(address, None)
        self._statuses.pop(address, None)
        self._update_button_states()

    def _disconnect_clicked(self) -> None:
        """Disconnect the BLE session for the selected device, if it is connected."""
        row = self._table.currentRow()
        if row >= 0:
            self.disconnect_device(self._addresses[row])

    def disconnect_device(self, address: str) -> None:
        """Disconnect the session for *address*, if there is one (also the tab's close button)."""
        if address not in self._sessions:
            return
        self._status.setText(f"Disconnecting from {address}…")
        self._stop_session(address)
        self._update_button_states()

    def _on_selection_changed(self, *_args) -> None:
        """Refresh Connect/Disconnect enabled state for the newly selected row."""
        self._update_button_states()

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def _stop_session(self, address: str) -> None:
        """Stop the session for *address*, if any, and wait for it to fully close."""
        session = self._sessions.pop(address, None)
        if session is not None and session.isRunning():
            session.stop()
            session.wait(2000)

    def stop_all_sessions(self) -> None:
        """Stop every active BLE session and wait for each to fully close.

        Called by :class:`MainWindow` on app close — deliberately *not*
        called from :meth:`closeEvent` so that closing this window alone
        leaves connections (and Debug logging) running.
        """
        for address in list(self._sessions):
            self._stop_session(address)

    def sessions(self) -> dict[str, BleSession]:
        """Return the currently connected BLE sessions, keyed by address.

        Used to populate a
        "send to this device" combo without needing their own session tracking.
        """
        return dict(self._sessions)

    def display_name(self, address: str) -> str:
        """Return the display name for *address*, falling back to the address itself."""
        return self._names.get(address, address)

    def relabel(self) -> None:
        """Re-resolve every listed device's label against the current board
        layout (call after the Board Layout window changes an assignment). The
        already-connected sessions' tree rows keep their name until reconnect —
        this only refreshes the device list and the config windows' target combo."""
        for address, advertised in self._advertised.items():
            label = board_layout.device_label(advertised)
            if address in self._names:
                self._names[address] = label
            row = self._row_for_address(address)
            if row >= 0 and self._table.item(row, 0) is not None:
                self._table.item(row, 0).setText(label)

    def _row_for_address(self, address: str) -> int:
        """Return the table row index for *address*, or -1 if not present."""
        try:
            return self._addresses.index(address)
        except ValueError:
            return -1

    def _set_status_cell(self, address: str, status: str) -> None:
        """Record *status* for *address* and reflect it in the Status column if listed."""
        self._statuses[address] = status
        row = self._row_for_address(address)
        if row < 0:
            return
        item = self._table.item(row, 3)
        if item is None:
            self._table.setItem(row, 3, QTableWidgetItem(status))
        else:
            item.setText(status)

    def _update_button_states(self) -> None:
        """Enable Connect/Disconnect based on whether the selected row is connected."""
        row = self._table.currentRow()
        if row < 0:
            self._connect_btn.setEnabled(False)
            self._disconnect_btn.setEnabled(False)
            return
        address = self._addresses[row]
        connected = address in self._sessions
        self._connect_btn.setEnabled(not connected)
        self._disconnect_btn.setEnabled(connected)

    # ------------------------------------------------------------------
    # Qt overrides
    # ------------------------------------------------------------------

    def closeEvent(self, event: QCloseEvent) -> None:
        """Wait for the scan thread to finish; the BLE session is left running."""
        if self._scan_thread is not None and self._scan_thread.isRunning():
            self._scan_thread.wait(2000)
        super().closeEvent(event)
