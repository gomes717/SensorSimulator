"""What each slot's board actually says it is running — CSV replay or a named
model — read back from its own Data Source / Person Config characteristics.

Pulled out of :class:`MainWindow` (issue 18, and to stop compounding the
module's line-count debt): the app's local profile only records what it
*tried* to send. A CSV uploaded in an earlier session, a config changed from
elsewhere, or a send that silently failed all leave the local guess and the
board disagreeing — and the board is the one actually producing the trace, so
every display decision (the graph title, the food/exercise panel, hiding the
food/exercise graph for a CSV sensor) must ask this, not the profile.
"""

from __future__ import annotations

from collections.abc import Callable

from api import protocol
from models.types import MODEL_LABELS


class BoardMode:
    """Per-slot: what the board last confirmed, kept fresh by asking on selection.

    The two facts (CSV vs model, and which model) arrive as separate GATT reads
    and are cached separately: a "running a model" answer must not discard the
    model's name learned earlier, or re-selecting a row drops the label back to
    "not confirmed" until the second read lands, which reads as a flicker.
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

    def refresh(self, slot: int | None) -> None:
        """Ask *slot*'s own board session what it is actually running."""
        bt = self._get_bt()
        if slot is None or bt is None:
            return
        session = next((s for s in bt.sessions().values() if s.slot_index == slot), None)
        if session is None or not session.is_live:
            return
        if session not in self._wired_sessions:
            # Bind the slot at connect time, from the session's OWN slot_index —
            # not by re-asking "what's selected right now" inside the handler.
            # Each session is one fixed BLE identity = one fixed slot, so the
            # read this triggers always answers for `slot` regardless of
            # whichever row the user has selected by the time the response
            # arrives (previously: a CSV upload's data_source read-back for
            # slot 3 could land on slot 1 if the user switched the tree
            # selection to slot 1 in the meantime).
            session.config_read.connect(
                lambda _address, char_key, data, s=session: self._on_read(
                    s.slot_index, char_key, data
                )
            )
            self._wired_sessions.add(session)
        # The caller has already queued the sensor-select cursor for this slot;
        # these reads ride the same FIFO, so they answer for the slot asked about.
        session.queue_write("sensor_select", protocol.encode_sensor_select(slot))
        session.request_read("data_source")
        session.request_read("person")

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
