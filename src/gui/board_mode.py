"""What each slot's board actually says it is running — CSV replay or a named
model — read back from the board's own characteristics.

Pulled out of :class:`MainWindow` (issue 18, and to stop compounding the
module's line-count debt): the app's local profile only records what it
*tried* to send. A CSV uploaded in an earlier session, a config changed from
elsewhere, or a send that silently failed all leave the local guess and the
board disagreeing — and the board is the one actually producing the trace, so
every display decision (the graph title, hiding the food/exercise graph for a CSV
sensor) must ask this, not the profile.

The local "expected" model does NOT come from here. Start writes the app's profiles
to the board and builds the expected model from those same profiles
(:meth:`AppState.board_plan`), so the two start from identical inputs; reading the
board back to build it would make the comparison depend on a readback.

The Sensor-select cursor is ONE value on the board, shared by every connection,
so two slots cannot be read at the same time without one read landing on the
other's slot. Reads are therefore serialized: one slot at a time, the next
starting when the previous has answered (or timed out).
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QTimer

from api import protocol
from models.types import MODEL_LABELS

_READ_TIMEOUT_MS = 3000
_READS = ("data_source", "person")
_NEEDED = frozenset(_READS)


class BoardMode:
    """Per-slot: what the board last confirmed, kept fresh by asking.

    The facts (CSV vs model, which model) arrive as separate GATT reads and are
    cached separately: a "running a model" answer must not discard the model's
    name learned earlier, or re-selecting a tab drops the label back to "not
    confirmed" until the next read lands, which reads as a flicker.
    """

    def __init__(
        self,
        get_bluetooth_window: Callable[[], object | None],
        on_changed: Callable[[], None],
        on_became_csv: Callable[[int], None],
    ) -> None:
        self._get_bt = get_bluetooth_window
        # Called after any update, to refresh whatever the title/graph show.
        self._on_changed = on_changed
        # Called the moment a slot is FIRST confirmed CSV, so the caller can
        # discard an expected-model line that is now known to be meaningless.
        self._on_became_csv = on_became_csv
        self._is_csv: dict[int, bool] = {}
        self._model: dict[int, str] = {}
        self._wired_sessions: set = set()
        self._pending: list[int] = []
        self._inflight: int | None = None
        self._got: set[str] = set()
        self._generation = 0

    # ------------------------------------------------------------------
    # What we know
    # ------------------------------------------------------------------

    def label(self, slot: int | None) -> str | None:
        """What the board last reported *slot* is running, or None if unknown."""
        if slot is None:
            return None
        if self._is_csv.get(slot):
            return "CSV replay"
        return self._model.get(slot)

    def is_csv(self, slot: int | None) -> bool:
        """True if the board has confirmed *slot* is replaying a CSV."""
        return slot is not None and self._is_csv.get(slot, False)

    # ------------------------------------------------------------------
    # Asking the board
    # ------------------------------------------------------------------

    def refresh(self, slot: int | None) -> None:
        """Queue a read of *slot*'s mode and model (one slot at a time)."""
        if slot is None or self._get_bt() is None:
            return
        if slot not in self._pending:
            self._pending.append(slot)
        self._pump()

    def _session_for(self, slot: int):
        bt = self._get_bt()
        if bt is None:
            return None
        session = next((s for s in bt.sessions().values() if s.slot_index == slot), None)
        return session if session is not None and session.is_live else None

    def _pump(self) -> None:
        """Start the next queued read if none is in flight."""
        while self._inflight is None and self._pending:
            slot = self._pending.pop(0)
            session = self._session_for(slot)
            if session is None:
                continue
            if session not in self._wired_sessions:
                # Bind the slot at connect time, from the session's OWN slot_index —
                # not by re-asking "what's selected right now" inside the handler.
                # Each session is one fixed BLE identity = one fixed slot, so the
                # read this triggers always answers for `slot` regardless of
                # whichever tab the user has selected by the time it arrives.
                session.config_read.connect(
                    lambda _address, char_key, data, s=session: self._on_read(
                        s.slot_index, char_key, data
                    )
                )
                self._wired_sessions.add(session)
            self._inflight = slot
            self._got = set()
            self._generation += 1
            token = self._generation
            # These reads ride the session's FIFO right behind the cursor write, so
            # they answer for the slot asked about.
            session.queue_write("sensor_select", protocol.encode_sensor_select(slot))
            for key in _READS:
                session.request_read(key)
            QTimer.singleShot(_READ_TIMEOUT_MS, lambda t=token: self._timed_out(t))

    def _timed_out(self, token: int) -> None:
        if token == self._generation and self._inflight is not None:
            self._finish()

    def _finish(self) -> None:
        """This slot's reads are done (or gave up): start the next queued one."""
        self._inflight = None
        self._pump()

    def _on_read(self, slot: int | None, char_key: str, data: bytes) -> None:
        if slot is None:
            return
        if char_key == "data_source":
            is_csv = bool(protocol.decode_data_source(data))
            if is_csv and not self._is_csv.get(slot):
                self._on_became_csv(slot)
            self._is_csv[slot] = is_csv
        elif char_key == "person":
            decoded = protocol.decode_person_config(data)
            if decoded is not None:
                self._model[slot] = MODEL_LABELS.get(decoded[0], "model")
        else:
            return
        self._on_changed()
        if slot == self._inflight:
            self._got.add(char_key)
            if self._got >= _NEEDED:
                self._finish()
