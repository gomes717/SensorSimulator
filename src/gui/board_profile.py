"""Following and changing the board's mode (3 sensors · standard Bluetooth, or 1 sensor · Dexcom G6).

The board is the authority on its mode, which is stored on it: it can change without the app (the
board's button), so this reads the mode back whenever a sensor connects and shows what the board
says. Changing it is a write to the board that drops every Bluetooth link; this then waits for the
links to go, rescans, and reconnects the sensors that were connected — by name, because "Nordic
Glucose Sensor 1" moves to another Bluetooth identity when the mode changes (see
``models/board_profile.py`` and ``.scratch/dexcom-g6-transmitter/spec.md``).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from api import protocol
from gui.config_controller import ConfigController
from models import board_profile
from models.board_profile import BoardProfile

if TYPE_CHECKING:
    from gui.bluetooth_window import BluetoothWindow

_LINKS_DROP_TIMEOUT_MS = 6000
_POLL_MS = 400


class BoardProfileController(QObject):
    """Reads the board's mode and ID, shows them, and switches the board when asked."""

    # The board's own speed multiplier, read back whenever a sensor connects: entering the Dexcom
    # mode puts the board back to x1 (xDrip polls in real time), so the app must follow it.
    speed_seen = pyqtSignal(float)

    def __init__(
        self,
        controller: ConfigController,
        get_bluetooth_window: Callable[[], BluetoothWindow | None],
        show_status: Callable[[str], None],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._c = controller
        self._get_bt = get_bluetooth_window
        self._show_status = show_status
        self.profile: BoardProfile | None = None  # None until the board has said
        self.tx_id: str | None = None
        self._wired: set[int] = set()
        self._switching = False
        controller.board_profile_requested.connect(self.request_switch)

    # ---- reading ---------------------------------------------------------------------------

    def on_session_connected(self, session) -> None:
        """A sensor connected: ask the board which mode it is in."""
        if id(session) not in self._wired:
            self._wired.add(id(session))
            session.config_read.connect(lambda _address, key, data: self._on_read(key, data))
        session.request_read("comm_profile")
        session.request_read("dexcom_id")
        session.request_read("speed")

    def _on_read(self, char_key: str, data: bytes) -> None:
        if char_key == "comm_profile":
            dexcom = protocol.decode_comm_profile(data)
            if dexcom is None:
                return
            self.profile = BoardProfile.from_wire(dexcom)
        elif char_key == "dexcom_id":
            tx_id = protocol.decode_dexcom_id(data)
            if tx_id is None:
                return
            self.tx_id = tx_id
        elif char_key == "speed":
            mult = protocol.decode_speed(data)
            if mult is not None:
                self.speed_seen.emit(mult)
            return
        else:
            return
        self._publish()

    def _publish(self) -> None:
        if self.profile is None:
            return
        self._c.set_board_profile_display(self.profile.value, self.tx_id or "")
        if not self._switching:
            self._c.set_board_status(
                f"The board is in the mode: {board_profile.LABELS[self.profile]}."
            )

    # ---- switching -------------------------------------------------------------------------

    def _live_sessions(self) -> list:
        bt = self._get_bt()
        if bt is None:
            return []
        return [s for s in bt.sessions().values() if s.is_live]

    def request_switch(self, profile_value: str, tx_id: str) -> None:
        """Write the mode (and, for Dexcom, the transmitter ID) to the board."""
        profile = BoardProfile(profile_value)
        live = self._live_sessions()
        if not live:
            self._c.set_board_status("Connect a sensor first: the mode is stored on the board.")
            return
        if profile.is_dexcom:
            problem = board_profile.check_transmitter_id(tx_id)
            if problem is not None:
                self._c.set_board_status(problem)
                return

        id_changed = profile.is_dexcom and tx_id != self.tx_id
        mode_changed = profile != self.profile
        if not id_changed and not mode_changed:
            self._c.set_board_status("The board is already set like that.")
            return

        writer = live[0]
        if id_changed:
            writer.queue_write("dexcom_id", protocol.encode_dexcom_id(tx_id))
            self.tx_id = tx_id
        if not mode_changed:
            self._c.set_board_status("Transmitter ID sent. Enter it in xDrip.")
            return
        writer.queue_write("comm_profile", protocol.encode_comm_profile(profile.is_dexcom))
        self.profile = profile
        slots = {s.slot_index for s in live if s.slot_index is not None} or {0}
        self._begin_switch(profile, slots, live)

    def _begin_switch(self, profile: BoardProfile, slots: set[int], live: list) -> None:
        """The write is on its way: wait for the board to drop the links, then reconnect."""
        self._switching = True
        self._c.set_board_status("The board is restarting; the app reconnects by itself…")
        self._show_status(f"Switching the board to: {board_profile.LABELS[profile]}")
        wanted = {s for s in slots if s < board_profile.active_slots(profile)} or {0}
        waited = 0

        def poll() -> None:
            nonlocal waited
            waited += _POLL_MS
            if any(s.is_live for s in live) and waited < _LINKS_DROP_TIMEOUT_MS:
                QTimer.singleShot(_POLL_MS, poll)
                return
            self._links_gone(wanted)

        QTimer.singleShot(_POLL_MS, poll)

    def _links_gone(self, wanted: set[int]) -> None:
        bt = self._get_bt()
        if bt is None:
            self._switching = False
            return
        bt.stop_all_sessions()  # a straggler must not reconnect to its old address
        bt.reconnect_slots(wanted)
        self._switching = False
        self._publish()

    def slots(self) -> int:
        """How many sensor slots the board runs now (3 until it says it is in the Dexcom mode)."""
        return board_profile.active_slots(self.profile or BoardProfile.MULTI)
