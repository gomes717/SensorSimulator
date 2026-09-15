"""Shared 'Target device' combo box used by the four simulator config windows."""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QWidget

from api import ble_uuids, protocol
from gui.bluetooth_window import BluetoothWindow
from services.ble_session import BleSession

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


def await_send_confirmation(
    session: BleSession, status_label: QLabel, on_confirmed: Callable[[], None] | None = None
) -> None:
    """Show live feedback that a just-sent config write actually reached and was
    applied by the board, instead of leaving the user unsure whether it worked.

    *on_confirmed* runs only if the board actually acknowledges — it is how
    callers commit anything that should describe the board's real state (the
    slot -> patient rename), rather than what was merely put on the wire.

    Reuses the board's reset_sync notification (see ble_uuids.RESET_SYNC_UUID)
    — it fires the instant ANY config write takes effect, so "the next one to
    arrive after I sent this" is a reliable (if not perfectly attributed, when
    multiple writes are in flight) confirmation signal. Falls back to a
    timeout warning if nothing arrives, e.g. because the connection dropped.
    """
    if not session.notifies(ble_uuids.RESET_SYNC_UUID):
        # A congested 3rd/4th link can subscribe some characteristics and not
        # others. Waiting on a channel this session never got would always time
        # out and read as a dead connection, which is the wrong thing to go
        # debug — the write itself was queued normally.
        status_label.setText("Sent — this link has no confirmation channel (not re-subscribed)")
        return
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
        if on_confirmed is not None:
            on_confirmed()

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
    """A 'Target device: [combo]' row backed by BluetoothWindow's live sessions.

    The target slot is *not* a separate choice: each of a multi-sensor board's
    BLE identities already represents one specific slot (the advertised name
    ends in its number, e.g. "... Sensor 2" == slot 1), so picking the device
    picks the slot. :meth:`begin` writes the sensor-select cursor to that slot
    before the window's own write/read. Single-sensor targets have no slot and
    get no cursor write. The device list refreshes itself (on show and on a
    short timer), so sensors that connect after this window opened appear
    without a manual rescan.
    """

    def __init__(self, get_bluetooth_window: Callable[[], BluetoothWindow], parent=None) -> None:
        """*get_bluetooth_window* is called lazily so the window need not exist yet."""
        super().__init__(parent)
        self._get_bluetooth_window = get_bluetooth_window
        self._addresses: list[str] = []
        self._entries: list[tuple[str, str]] = []  # (address, label) last shown

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel("Target device:"))
        self.combo = QComboBox()
        layout.addWidget(self.combo, 1)

        self._poll = QTimer(self)
        self._poll.timeout.connect(self.refresh)
        self._poll.start(2000)
        self.refresh()

    def showEvent(self, event) -> None:
        self.refresh()
        super().showEvent(event)

    def refresh(self) -> None:
        """Repopulate the combo from currently connected BLE sessions.

        Compares labels, not just addresses: re-pairing a slot to a different
        patient renames the device without changing the connection, and the
        combo would otherwise keep showing the old "patient — Sensor N".
        """
        bt_window = self._get_bluetooth_window()
        entries = [(address, bt_window.display_name(address)) for address in bt_window.sessions()]
        if entries == self._entries:
            return
        current = self.combo.currentData()
        self._entries = entries
        self._addresses = [address for address, _ in entries]
        self.combo.blockSignals(True)
        self.combo.clear()
        for address, label in entries:
            self.combo.addItem(label, address)
        if current in self._addresses:
            self.combo.setCurrentIndex(self._addresses.index(current))
        self.combo.blockSignals(False)

    def selected_session(self) -> BleSession | None:
        """Return the currently selected target's live BleSession, or None if none connected."""
        address = self.combo.currentData()
        if address is None:
            return None
        return self._get_bluetooth_window().sessions().get(address)

    def selected_slot(self) -> int | None:
        """The target slot, derived from the selected device's own identity
        (0-based), or None for a single-sensor target (no cursor write)."""
        session = self.selected_session()
        return session.slot_index if session is not None else None

    def begin(self) -> BleSession | None:
        """Resolve the target session and, for a multi-sensor board, queue the
        sensor-select cursor write for that device's own slot. Call at the top
        of every _send_to_board / _read_from_board in place of selected_session().

        Returns None when the link is down: a dropped session still accepts
        queue_write(), but nothing drains its queue, so the caller would report
        a successful send that never happened.
        """
        session = self.selected_session()
        if session is not None and not session.is_live:
            return None
        slot = self.selected_slot()
        if session is not None and slot is not None:
            session.queue_write("sensor_select", protocol.encode_sensor_select(slot))
        return session
