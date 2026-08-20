"""Shared 'Target device' combo box used by the four simulator config windows."""
from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QWidget

from api import protocol
from services.ble_session import BleSession
from graphic.bluetooth_window import BluetoothWindow

SEND_CONFIRMATION_TIMEOUT_MS = 3000


def restart_board(session: BleSession) -> None:
    """Tell *session*'s board to (re)start ticking now — call after any config push.

    The firmware already reinitializes model/sensor state and resets its
    simulation clock on every config write it receives (person, sensor,
    food/exercise event) — but it only resumes *advancing* that state while
    its run state is RUNNING. Without this, a board left paused/stopped from
    an earlier Stop/Pause sits frozen on the freshly-applied config instead
    of visibly restarting, which is what "send config" should do.
    """
    session.queue_write("run_state", protocol.encode_run_state(protocol.RUN_STATE_RUNNING))


def await_send_confirmation(session: BleSession, status_label: QLabel) -> None:
    """Show live feedback that a just-sent config write actually reached and was
    applied by the board, instead of leaving the user unsure whether it worked.

    Reuses the board's reset_sync notification (see ble_uuids.RESET_SYNC_UUID)
    — it fires the instant ANY config write takes effect, so "the next one to
    arrive after I sent this" is a reliable (if not perfectly attributed, when
    multiple writes are in flight) confirmation signal. Falls back to a
    timeout warning if nothing arrives, e.g. because the connection dropped.
    """
    status_label.setText("Sending to board…")
    state = {"done": False}

    def on_sync(_address: str) -> None:
        if state["done"]:
            return
        state["done"] = True
        try:
            session.reset_sync.disconnect(on_sync)
        except TypeError:
            pass
        status_label.setText("✓ Applied on board")

    def on_timeout() -> None:
        if state["done"]:
            return
        state["done"] = True
        try:
            session.reset_sync.disconnect(on_sync)
        except TypeError:
            pass
        status_label.setText("⚠ No confirmation from board (check connection)")

    session.reset_sync.connect(on_sync)
    QTimer.singleShot(SEND_CONFIRMATION_TIMEOUT_MS, on_timeout)


class DeviceTargetBar(QWidget):
    """A 'Target device: [combo] [Refresh]' row backed by BluetoothWindow's live sessions."""

    def __init__(self, get_bluetooth_window: Callable[[], BluetoothWindow], parent=None) -> None:
        """*get_bluetooth_window* is called lazily so the window need not exist yet."""
        super().__init__(parent)
        self._get_bluetooth_window = get_bluetooth_window
        self._addresses: list[str] = []

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel("Target device:"))
        self.combo = QComboBox()
        layout.addWidget(self.combo, 1)
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh)
        layout.addWidget(refresh_btn)

        self.refresh()

    def refresh(self) -> None:
        """Repopulate the combo from currently connected BLE sessions."""
        bt_window = self._get_bluetooth_window()
        sessions = bt_window.sessions()
        current = self.combo.currentData()
        self.combo.clear()
        self._addresses = list(sessions.keys())
        for address in self._addresses:
            self.combo.addItem(bt_window.display_name(address), address)
        if current in self._addresses:
            self.combo.setCurrentIndex(self._addresses.index(current))

    def selected_session(self) -> BleSession | None:
        """Return the currently selected target's live BleSession, or None if none connected."""
        address = self.combo.currentData()
        if address is None:
            return None
        return self._get_bluetooth_window().sessions().get(address)
