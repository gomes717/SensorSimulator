"""Which saved Person/Sensor profile drives each of the board's sensor slots.

The multi-sensor firmware runs up to :data:`MAX_SLOTS` fully independent sensor
slots (see ``PROTOCOL_SPEC.md`` §7); this is the app-side record of "slot *i* =
person X + sensor noise Y". Persisted to ``data/board_layout.json`` by name, so
it survives a restart and follows a renamed/edited profile by identity of name.
Applied to a board by :meth:`services.ble_session.BleSession.send_board_layout`.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

MAX_SLOTS = 4

# "Nordic Glucose Sensor 3" -> slot 2 (the firmware numbers identities from 1;
# CGMS instances / slots are 0-based — same convention as
# services/ble_session.py's _own_instance_index).
_TRAILING_NUM = re.compile(r"(\d+)\s*$")

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_LAYOUT_FILE = _DATA_DIR / "board_layout.json"


@dataclass
class SlotAssignment:
    """One slot's assignment: the saved profile names, or None when unused."""

    person: str | None = None
    sensor: str | None = None


@dataclass
class BoardLayout:
    """The full slot -> (person, sensor) mapping, always :data:`MAX_SLOTS` long."""

    slots: list[SlotAssignment] = field(
        default_factory=lambda: [SlotAssignment() for _ in range(MAX_SLOTS)]
    )

    def __post_init__(self) -> None:
        # Normalise length so callers can always index [0, MAX_SLOTS).
        self.slots = (self.slots + [SlotAssignment() for _ in range(MAX_SLOTS)])[:MAX_SLOTS]

    def assigned_count(self) -> int:
        return sum(1 for s in self.slots if s.person)


def load() -> BoardLayout:
    """Load the saved layout, or a blank one if nothing has been saved yet."""
    if not _LAYOUT_FILE.exists():
        return BoardLayout()
    try:
        data = json.loads(_LAYOUT_FILE.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return BoardLayout()
    slots = [
        SlotAssignment(person=d.get("person"), sensor=d.get("sensor"))
        for d in data.get("slots", [])
    ]
    return BoardLayout(slots=slots)


def save(layout: BoardLayout) -> None:
    """Persist *layout* to ``data/board_layout.json``."""
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _LAYOUT_FILE.write_text(
        json.dumps({"slots": [asdict(s) for s in layout.slots]}, indent=2),
        encoding="utf-8",
    )


def slot_of(advertised_name: str) -> int | None:
    """0-based sensor slot an advertised name maps to ("... Sensor 3" -> 2), or
    None for an unnumbered (single-sensor) name."""
    m = _TRAILING_NUM.search(advertised_name or "")
    if not m:
        return None
    slot = int(m.group(1)) - 1
    return slot if 0 <= slot < MAX_SLOTS else None


def person_for(advertised_name: str, layout: BoardLayout | None = None) -> str | None:
    """The patient name assigned to the slot *advertised_name* represents, or
    None if that slot is unassigned / the name isn't a numbered identity.
    Loads the saved layout when one isn't passed."""
    slot = slot_of(advertised_name)
    if slot is None:
        return None
    layout = layout if layout is not None else load()
    return layout.slots[slot].person or None


def device_label(advertised_name: str, layout: BoardLayout | None = None) -> str:
    """What to show for this identity in the UI: the assigned patient (with the
    physical sensor number kept for clarity), or the raw advertised name when
    no patient is configured for that slot."""
    person = person_for(advertised_name, layout)
    if not person:
        return advertised_name
    slot = slot_of(advertised_name)
    return f"{person} — Sensor {slot + 1}" if slot is not None else person


def session_name(advertised_name: str, layout: BoardLayout | None = None) -> str:
    """The name a BleSession should carry for tree-row / graph display: just the
    assigned patient (the row already shows the address suffix), else the raw
    advertised name."""
    return person_for(advertised_name, layout) or advertised_name
