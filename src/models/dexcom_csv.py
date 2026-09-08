"""Reader for Dexcom Clarity-style CGM export CSVs (see dataset/Dexcom_*.csv).

Pure stdlib: no Qt, no third-party deps. Only the estimated-glucose-value
(EGV) rows are returned; the header metadata rows (FirstName, Device, Alert,
...) and non-numeric readings ("Low"/"High") are skipped.
"""

from __future__ import annotations

import csv
from datetime import datetime, timedelta
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


DEFAULT_INTERVAL_S = 300  # Dexcom EGV cadence
DEFAULT_WINDOW_HOURS = 24.0


def read_egv(path: str | Path) -> list[tuple[datetime, float]]:
    """Return ``[(timestamp, glucose_mg_dl), ...]`` for every EGV row, sorted by time.

    Raises ``ValueError`` if the file has no recognizable EGV rows (wrong CSV
    kind), so callers can show a clear message.
    """
    out: list[tuple[datetime, float]] = []
    with open(path, encoding="utf-8-sig", newline="") as fh:
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


def slice_window(
    rows: list[tuple[datetime, float]],
    start: datetime,
    hours: float = DEFAULT_WINDOW_HOURS,
) -> list[tuple[datetime, float]]:
    """Return the EGV rows inside ``[start, start + hours)``."""
    end = start + timedelta(hours=hours)
    return [(ts, g) for ts, g in rows if start <= ts < end]


def resample(
    rows: list[tuple[datetime, float]],
    start: datetime,
    interval_s: int = DEFAULT_INTERVAL_S,
    hours: float = DEFAULT_WINDOW_HOURS,
) -> list[int]:
    """Resample *rows* onto a fixed ``interval_s`` grid over ``hours`` from *start*.

    Returns ``round(hours * 3600 / interval_s)`` integer mg/dL samples. Each grid
    point takes the most recent reading at or before it (forward-fill); leading
    grid points with no prior reading take the first reading. Raises
    ``ValueError`` if *rows* is empty.
    """
    if not rows:
        raise ValueError("No readings to resample.")
    ordered = sorted(rows, key=lambda pair: pair[0])
    n = max(1, round(hours * 3600.0 / interval_s))
    out: list[int] = []
    idx = 0
    last = ordered[0][1]
    for k in range(n):
        grid_ts = start.timestamp() + k * interval_s
        while idx < len(ordered) and ordered[idx][0].timestamp() <= grid_ts:
            last = ordered[idx][1]
            idx += 1
        out.append(int(round(last)))
    return out
