"""The connected board BLE sessions, as one small write surface.

Concentrates the "for every connected session, queue_write(...)" fan-out that
was spread across ~6 MainWindow methods (issue 18). Constructed with a provider
that returns the live ``{address: BleSession}`` dict (``{}`` when nothing is
connected — e.g. the Bluetooth window was never opened).
"""

from __future__ import annotations

from collections.abc import Callable

from api import protocol
from gui.device_target import restart_board
from models import board_layout


class BoardLink:
    def __init__(self, sessions_provider: Callable[[], dict | None]) -> None:
        self._sessions_provider = sessions_provider

    def sessions(self) -> dict:
        return self._sessions_provider() or {}

    def connected(self) -> bool:
        return bool(self.sessions())

    def multi_slot(self) -> bool:
        """True if any connected session is one slot of a numbered multi-sensor board."""
        return any(s.slot_index is not None for s in self.sessions().values())

    def broadcast(self, char_key: str, payload: bytes) -> None:
        """Queue *payload* on *char_key* for every connected session."""
        for s in self.sessions().values():
            s.queue_write(char_key, payload)

    def send_to_slot(self, slot: int, char_key: str, payload: bytes) -> None:
        """Write to one slot: the sensor-select cursor first, then *char_key*."""
        sess = list(self.sessions().values())
        if not sess:
            return
        target = next((s for s in sess if s.slot_index is not None), sess[0])
        target.queue_write("sensor_select", protocol.encode_sensor_select(slot))
        target.queue_write(char_key, payload)

    def send_instant(self, char_key: str, payload: bytes, slot: int | None) -> None:
        """A one-shot event: to one slot, or (slot=None) to every sensor.

        The firmware applies an instant event only to the sensor-select cursor's
        slot (comm_thread.c: model_thread_add_instant_*(comm_thread_selected_slot())),
        so "all sensors" on a multi-sensor board is cursor+event once per slot,
        not a single broadcast — which would land N times on one slot.
        """
        if slot is not None:
            self.send_to_slot(slot, char_key, payload)
        elif self.multi_slot():
            for i in range(board_layout.MAX_SLOTS):
                self.send_to_slot(i, char_key, payload)
        else:
            self.broadcast(char_key, payload)

    def restart_all(self) -> None:
        """Nudge every connected board back to RUNNING (call after a config push)."""
        for s in self.sessions().values():
            restart_board(s)
