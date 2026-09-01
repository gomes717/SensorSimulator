"""Reader for Dexcom Clarity-style CGM export CSVs (see dataset/Dexcom_*.csv).

Pure stdlib: no Qt, no third-party deps. Only the estimated-glucose-value
(EGV) rows are returned; the header metadata rows (FirstName, Device, Alert,
...) and non-numeric readings ("Low"/"High") are skipped.
"""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

_TS_COL = "Timestamp (YYYY-MM-DDThh:mm:ss)"
_TYPE_COL = "Event Type"
_GLUCOSE_COL = "Glucose Value (mg/dL)"

_TS_FORMATS = ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S")


def _parse_ts(raw: str) -> datetime | None:
    raw = raw.strip()
    for fmt in _TS_FORMATS:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def read_egv(path: str | Path) -> list[tuple[datetime, float]]:
    """Return ``[(timestamp, glucose_mg_dl), ...]`` for every EGV row, sorted by time.

    Raises ``ValueError`` if the file has no recognizable EGV rows (wrong CSV
    kind), so callers can show a clear message.
    """
    out: list[tuple[datetime, float]] = []
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None or _GLUCOSE_COL not in reader.fieldnames:
            raise ValueError("Not a Dexcom CGM export (missing glucose column).")
        for row in reader:
            if (row.get(_TYPE_COL) or "").strip() != "EGV":
                continue
            ts = _parse_ts(row.get(_TS_COL) or "")
            if ts is None:
                continue
            try:
                glucose = float((row.get(_GLUCOSE_COL) or "").strip())
            except ValueError:
                continue  # "Low" / "High" / blank
            out.append((ts, glucose))
    if not out:
        raise ValueError("No EGV glucose rows found in this CSV.")
    out.sort(key=lambda pair: pair[0])
    return out
