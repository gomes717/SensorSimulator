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

    def live_sessions(self) -> list:
        """Connected sessions whose GATT link is actually up.

        A session whose link has dropped still sits in the sessions dict and
        still accepts queue_write() — the write is just dropped on the floor
        when nobody is draining the queue. Anything that expects the board to
        act on a write must pick from here, or it fails silently.
        """
        return [s for s in self.sessions().values() if s.is_live]

    def broadcast(self, char_key: str, payload: bytes) -> int:
        """Queue *payload* on *char_key* for every live session.

        Returns how many it reached, so a caller whose write matters (the run
        state — a board that never receives it simply sits idle and produces
        nothing) can tell the difference between "sent" and "sent to nobody".
        """
        live = self.live_sessions()
        for session in live:
            session.queue_write(char_key, payload)
        return len(live)

    def send_to_slot(self, slot: int, char_key: str, payload: bytes) -> bool:
        """Write to one slot: the sensor-select cursor first, then *char_key*.

        Returns False when there is no live session to carry it.
        """
        sess = self.live_sessions()
        if not sess:
            return False
        target = next((s for s in sess if s.slot_index is not None), sess[0])
        target.queue_write("sensor_select", protocol.encode_sensor_select(slot))
        target.queue_write(char_key, payload)
        return True

    def send_instant(self, char_key: str, payload: bytes, slot: int | None) -> int:
        """A one-shot event: to one slot, or (slot=None) to every sensor.

        The firmware applies an instant event only to the sensor-select cursor's
        slot (comm_thread.c: model_thread_add_instant_*(comm_thread_selected_slot())),
        so "all sensors" on a multi-sensor board is cursor+event once per slot,
        not a single broadcast — which would land N times on one slot.

        Returns how many slots it actually reached (0 = nothing was sent).
        """
        if slot is not None:
            return int(self.send_to_slot(slot, char_key, payload))
        if self.multi_slot():
            return sum(
                self.send_to_slot(i, char_key, payload) for i in range(board_layout.MAX_SLOTS)
            )
        live = self.live_sessions()
        self.broadcast(char_key, payload)
        return len(live)

    def restart_all(self) -> None:
        """Nudge every connected board back to RUNNING (call after a config push)."""
        for s in self.sessions().values():
            restart_board(s)
