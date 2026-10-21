"""The board's two modes, as the app talks about them.

One firmware image, one setting stored on the board:

* **Multi** — three sensors, the standard Bluetooth CGM service, no pairing (what the app has
  always done).
* **Dexcom** — one sensor, advertised as a Dexcom G6 transmitter that xDrip can read, plus
  "Nordic Glucose Sensor 1" for this app. The transmitter ID is the secret of the G6 challenge.

Changing it drops every link and restarts the simulation; the app rescans and reconnects. The
board is the authority on which mode it is in (the app reads it back); this module is only the
vocabulary: the enum, the labels and how many sensor slots each mode runs.
"""

from __future__ import annotations

import enum

from api import dexcom_g6
from models import board_layout


class BoardProfile(enum.StrEnum):
    """Which of the board's two modes is active."""

    MULTI = "multi"
    DEXCOM = "dexcom"

    @classmethod
    def from_wire(cls, dexcom: bool) -> BoardProfile:
        """The profile for the board-mode byte (``protocol.decode_comm_profile``'s answer)."""
        return cls.DEXCOM if dexcom else cls.MULTI

    @property
    def is_dexcom(self) -> bool:
        """Whether this is the Dexcom mode (the argument of ``protocol.encode_comm_profile``)."""
        return self is BoardProfile.DEXCOM


LABELS = {
    BoardProfile.MULTI: "3 sensors · standard Bluetooth, no pairing",
    BoardProfile.DEXCOM: "1 sensor · Dexcom G6 (for xDrip)",
}


def active_slots(profile: BoardProfile) -> int:
    """How many sensor slots run in *profile* (the board keeps the other slots' data untouched)."""
    return 1 if profile is BoardProfile.DEXCOM else board_layout.MAX_SLOTS


def switch_warning(profile: BoardProfile) -> str:
    """What the user must know before the board changes to *profile*."""
    return (
        f"Switch the board to: {LABELS[profile]}?\n\n"
        "The board restarts: every Bluetooth link drops and the simulation starts over. "
        "The app reconnects by itself."
    )


def speed_note(profile: BoardProfile, speed_mult: float) -> str | None:
    """A reminder for the Dexcom mode when the speed is not real time (xDrip polls in real time)."""
    if profile is BoardProfile.DEXCOM and speed_mult != 1.0:
        return (
            f"Speed is x{speed_mult:g}. xDrip reads the board in real time (one reading every "
            "5 minutes); set the speed to x1 for xDrip."
        )
    return None


def check_transmitter_id(tx_id: str) -> str | None:
    """None if *tx_id* is a valid Dexcom transmitter ID, else what is wrong with it."""
    if dexcom_g6.valid_transmitter_id(tx_id):
        return None
    return "The transmitter ID is 6 characters: digits and capital letters (for example 8SIM00)."
