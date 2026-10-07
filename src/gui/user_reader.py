"""Reading one board slot into a :class:`~models.user_board.BoardReading`, and writing a
user's name back.

A read is the Sensor-select cursor write followed by six GATT reads (name, data source,
person config, sensor config, food list, exercise list). They ride the session's FIFO right
behind the cursor write, so they answer for the slot asked about — the cursor is ONE value
on the board shared by every connection, which is why only one read runs at a time and why
the answers are taken only from the session that was asked.

The read is all-or-nothing: a missing or unreadable answer fails it with a message that says
what was missing, rather than building a user from a guess (a half-read user that "matched"
would let an overwrite silently wipe the saved one's missing parts).
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QObject, QTimer

from api import protocol
from models.user_board import BoardReading

READS = ("user_name", "data_source", "person", "sensor", "food_list", "exercise_list")
_DEFAULT_TIMEOUT_MS = 8000  # six reads behind a cursor write, at the board's own pace

DoneCallback = Callable[[BoardReading | None, str], None]


class UserReader(QObject):
    """One slot read at a time; the result goes to the callback given to :meth:`read`."""

    def __init__(self, timeout_ms: int = _DEFAULT_TIMEOUT_MS, parent: QObject | None = None):
        super().__init__(parent)
        self._timeout_ms = timeout_ms
        self._wired: set[int] = set()  # id() of sessions already connected to
        self._session: object | None = None
        self._on_done: DoneCallback | None = None
        self._got: dict[str, bytes] = {}
        self._generation = 0

    @property
    def busy(self) -> bool:
        """True while a read is waiting for its answers."""
        return self._on_done is not None

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def read(self, session, on_done: DoneCallback) -> bool:
        """Start reading *session*'s slot. False (and *on_done* never called) if a read is
        already running; otherwise True and *on_done(reading, error)* is called exactly once —
        with the reading and ``""``, or ``None`` and why it failed."""
        if self.busy:
            return False
        if not session.is_live:
            on_done(None, "The sensor is not connected.")
            return True
        self._wire(session)
        self._session = session
        self._on_done = on_done
        self._got = {}
        self._generation += 1
        token = self._generation
        slot = session.slot_index if session.slot_index is not None else 0
        session.queue_write("sensor_select", protocol.encode_sensor_select(slot))
        for key in READS:
            session.request_read(key)
        QTimer.singleShot(self._timeout_ms, lambda: self._timed_out(token))
        return True

    def write_name(self, session, slot: int, name: str) -> None:
        """Tell the board the name of the user on *slot*. Raises ValueError, before anything
        is sent, if the name does not fit the board's field."""
        payload = protocol.encode_user_name(name)
        session.queue_write("sensor_select", protocol.encode_sensor_select(slot))
        session.queue_write("user_name", payload)

    # ------------------------------------------------------------------
    # Answers
    # ------------------------------------------------------------------

    def _wire(self, session) -> None:
        if id(session) in self._wired:
            return
        session.config_read.connect(
            lambda _address, key, data, s=session: self._on_read(s, key, data)
        )
        session.write_failed.connect(
            lambda _address, key, error, s=session: self._on_failed(s, key, error)
        )
        self._wired.add(id(session))

    def _on_read(self, session, key: str, data: bytes) -> None:
        if not self.busy or session is not self._session or key not in READS:
            return
        self._got[key] = data
        if len(self._got) == len(READS):
            self._finish()

    def _on_failed(self, session, key: str, error: str) -> None:
        if not (self.busy and session is self._session and key in READS):
            return
        if "does not expose" in error:
            # Windows serves a cached copy of a board's services; after a firmware update
            # that added a characteristic, the first connection still sees the old copy and
            # refreshes it only afterwards (seen with 'user_name', 2026-10).
            self._fail(
                f"This sensor does not expose '{key}'. If its firmware was just updated, "
                "Windows may still be showing the old list of services — disconnect and "
                "reconnect the sensor, then read again."
            )
            return
        self._fail(f"The board would not give '{key}' ({error}).")

    def _timed_out(self, token: int) -> None:
        if token != self._generation or not self.busy:
            return
        missing = ", ".join(key for key in READS if key not in self._got)
        self._fail(f"The board did not answer in time (missing: {missing}).")

    # ------------------------------------------------------------------
    # Finishing
    # ------------------------------------------------------------------

    def _finish(self) -> None:
        got = self._got
        person = protocol.decode_person_config(got["person"])
        sensor = protocol.decode_sensor_config(got["sensor"])
        is_csv = protocol.decode_data_source(got["data_source"])
        if person is None or sensor is None or is_csv is None:
            unreadable = [
                key
                for key, value in (("person", person), ("sensor", sensor), ("data_source", is_csv))
                if value is None
            ]
            self._fail(f"The board's answer for {', '.join(unreadable)} could not be read.")
            return
        reading = BoardReading(
            name=protocol.decode_user_name(got["user_name"]),
            is_csv=is_csv,
            model_id=person[0],
            model_params=person[1],
            sensor_id=sensor[0],
            sensor_params=sensor[1],
            food_events=protocol.decode_food_events(got["food_list"]),
            exercise_events=protocol.decode_exercise_events(got["exercise_list"]),
        )
        self._complete(reading, "")

    def _fail(self, message: str) -> None:
        self._complete(None, message)

    def _complete(self, reading: BoardReading | None, error: str) -> None:
        on_done = self._on_done
        self._on_done = None
        self._session = None
        self._got = {}
        if on_done is not None:
            on_done(reading, error)
