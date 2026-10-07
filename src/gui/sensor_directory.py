"""Who is who: the sensors behind the connected BLE sessions.

Answers the "which slot / which label / is it live" questions that the rest of the
window asks about a sensor, from the connected sessions and the board layout, so
none of them has to walk the Bluetooth window's session dict itself.
"""

from __future__ import annotations

from collections.abc import Callable

from models import board_layout


class SensorDirectory:
    """Read-only view of the connected sensors (sessions) and their slots."""

    def __init__(
        self,
        bluetooth: Callable[[], object | None],
        layout: Callable[[], board_layout.BoardLayout],
    ) -> None:
        self._bluetooth = bluetooth
        self._layout = layout

    def sessions(self) -> dict:
        """The connected sessions by address ({} when the Bluetooth window never opened)."""
        bt = self._bluetooth()
        return bt.sessions() if bt is not None else {}

    def session_for(self, user_id: str):
        """The session labelled *user_id*, or None."""
        return next((s for s in self.sessions().values() if s.user_id == user_id), None)

    def slot_of_user(self, user_id: str) -> int | None:
        """The board slot behind the session *user_id* (None if unnumbered or not connected)."""
        session = self.session_for(user_id)
        return session.slot_index if session is not None else None

    def is_live(self, user_id: str) -> bool:
        """Whether *user_id*'s GATT link is actually up right now."""
        session = self.session_for(user_id)
        return session is not None and session.is_live

    def slot_user_id(self, slot: int) -> str:
        """The history key the received stream for *slot* uses.

        Taken from the live session for that slot, because a session's user_id
        is frozen when it connects. Re-deriving a label from the board layout
        here instead would silently split the two series the moment a slot is
        re-assigned: the expected line would go to "new patient — Sensor N"
        while the board's readings keep arriving under the old name, so the
        sensor's page would plot a received trace with no model line.
        """
        for session in self.sessions().values():
            if session.slot_index == slot:
                return session.user_id
        return board_layout.device_label(board_layout.advert_name(slot), self._layout())

    def label(self, user_id: str, dev_id: str | None) -> str:
        """Visible name for a sensor's tab / graph title.

        A session's user_id is fixed when it connects — it can be a bare
        address (the scan returned no name yet) and it still names whichever
        patient the slot carried back then. Both go stale, so the visible name
        comes from the Bluetooth window's current, board-layout-resolved label.

        The exception is a generic multi-instance device fanned out to one tab
        per slot: those share a dev_id and are told apart only by the
        "· Sensor N" their user_id carries, so they keep it.
        """
        bt = self._bluetooth()
        if dev_id is None or bt is None or " · Sensor " in user_id:
            return user_id
        return bt.display_name(dev_id) or user_id
