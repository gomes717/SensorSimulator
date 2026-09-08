"""Reader for D1NAMO-style Food Log CSVs (see dataset/Food_Log_*.csv).

Pure stdlib. Each row is one logged food item with a carbohydrate amount;
several rows can share a timestamp (one meal, many items), so entries are
summed per ``time_begin``. Only the timestamp and carb grams are kept — the
firmware treats the food log as report-only (it never feeds a model in CSV
playback mode), so calories/protein/fat are dropped.
"""

from __future__ import annotations

import csv
import re
from datetime import datetime, timedelta
from pathlib import Path

_TS_COL = "time_begin"
_CARB_COL = "total_carb"
_TS_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S")


def _parse_ts(raw: str) -> datetime | None:
    raw = (raw or "").strip()
    for fmt in _TS_FORMATS:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def matching_food_log_path(glucose_csv_path: str | Path) -> str | None:
    """Find the Food Log CSV that shares an ID with a Dexcom glucose CSV.

    The D1NAMO dataset pairs files by number: ``Dexcom_001.csv`` ↔
    ``Food_Log_001.csv`` in the same directory. Returns the sibling path if it
    exists, else ``None`` — callers use this so the food log is picked up
    automatically without a second file chooser.
    """
    p = Path(glucose_csv_path)
    m = re.search(r"(\d+)", p.stem)
    if not m:
        return None
    num = m.group(1)
    cand = p.with_name(f"Food_Log_{num}{p.suffix}")
    if cand.is_file():
        return str(cand)
    for sibling in p.parent.glob(f"*[Ff]ood*[Ll]og*{num}*{p.suffix}"):
        return str(sibling)
    return None


def read_food_log(path: str | Path) -> list[tuple[datetime, float]]:
    """Return ``[(timestamp, carbs_g), ...]`` summed per meal timestamp, sorted.

    Raises ``ValueError`` if the file has no recognizable rows (wrong CSV kind).
    """
    totals: dict[datetime, float] = {}
    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None or _CARB_COL not in reader.fieldnames:
            raise ValueError("Not a Food Log export (missing total_carb column).")
        for row in reader:
            ts = _parse_ts(row.get(_TS_COL, ""))
            if ts is None:
                continue
            try:
                carbs = float((row.get(_CARB_COL) or "").strip())
            except ValueError:
                continue
            if carbs <= 0.0:
                continue
            totals[ts] = totals.get(ts, 0.0) + carbs
    if not totals:
        raise ValueError("No carbohydrate rows found in this food log.")
    return sorted(totals.items())


def slice_window(
    rows: list[tuple[datetime, float]],
    start: datetime,
    hours: float = 24.0,
) -> list[tuple[int, float]]:
    """Return ``[(offset_s, carbs_g), ...]`` for meals inside ``[start, start+hours)``.

    ``offset_s`` is whole seconds from *start* — the form the firmware's
    csv_store foodlog track expects.
    """
    end = start + timedelta(hours=hours)
    return [(int((ts - start).total_seconds()), carbs) for ts, carbs in rows if start <= ts < end]
