"""Central log of messages received over BLE connections, shared across windows."""

from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal


class BleMessageLog(QObject):
    """Collects messages emitted by :class:`BleSession` so the Debug window can display them.

    Owned by :class:`MainWindow` for the lifetime of the app so that messages
    keep accumulating regardless of which windows are currently open.
    """

    new_message = pyqtSignal(dict)
    device_disconnected = pyqtSignal(str)  # BLE address — the session for it has ended

    def __init__(self, parent=None) -> None:
        """Start with an empty message history."""
        super().__init__(parent)
        self._messages: list[dict] = []

    def get_messages(self) -> list[dict]:
        """Return a snapshot of every message received so far, in arrival order."""
        return list(self._messages)

    def add_message(self, message: dict) -> None:
        """Record *message* and notify subscribers."""
        self._messages.append(message)
        self.new_message.emit(message)

    def note_disconnected(self, address: str) -> None:
        """Relay a BleSession disconnect so views can mark that device's rows offline."""
        self.device_disconnected.emit(address)
