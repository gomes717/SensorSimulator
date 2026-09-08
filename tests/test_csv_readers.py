"""Issue 10: the pure CSV readers (models.dexcom_csv, models.food_log_csv)."""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from models import dexcom_csv, food_log_csv

_DEXCOM_HEADER = (
    "Index,Timestamp (YYYY-MM-DDThh:mm:ss),Event Type,Event Subtype,Patient Info,"
    "Device Info,Source Device ID,Glucose Value (mg/dL),Insulin Value (u),"
    "Carb Value (grams),Duration (hh:mm:ss),Glucose Rate of Change (mg/dL/min),"
    "Transmitter Time (Long Integer)"
)


def _dexcom_csv(tmp_path: Path, rows: list[tuple[str, str]]) -> Path:
    lines = [_DEXCOM_HEADER, "1,,FirstName,,,,,,,,,,", "2,,Device,,,,,,,,,,"]
    for i, (ts, glu) in enumerate(rows, start=3):
        lines.append(f"{i},{ts},EGV,,,,iPhone G6,{glu},,,,,")
    p = tmp_path / "Dexcom_042.csv"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def test_read_egv_skips_preamble_and_non_numeric(tmp_path):
    p = _dexcom_csv(
        tmp_path,
        [
            ("2020-01-01 00:00:00", "100"),
            ("2020-01-01 00:05:00", "Low"),  # dropped
            ("2020-01-01 00:10:00", "120"),
        ],
    )
    rows = dexcom_csv.read_egv(p)
    assert [g for _, g in rows] == [100.0, 120.0]
    assert rows[0][0] == datetime(2020, 1, 1, 0, 0, 0)


def test_read_egv_rejects_wrong_csv(tmp_path):
    p = tmp_path / "notdexcom.csv"
    p.write_text("a,b,c\n1,2,3\n", encoding="utf-8")
    with pytest.raises(ValueError):
        dexcom_csv.read_egv(p)


def test_resample_forward_fills_onto_the_grid(tmp_path):
    rows = [
        (datetime(2020, 1, 1, 0, 0, 0), 100.0),
        (datetime(2020, 1, 1, 0, 10, 0), 160.0),
    ]
    out = dexcom_csv.resample(rows, datetime(2020, 1, 1, 0, 0, 0), interval_s=300, hours=0.5)
    assert out == [100, 100, 160, 160, 160, 160]  # 0,5,10,15,20,25 min


def test_resample_raises_on_empty():
    with pytest.raises(ValueError):
        dexcom_csv.resample([], datetime(2020, 1, 1), interval_s=300)


def test_slice_window_is_half_open(tmp_path):
    rows = [(datetime(2020, 1, 1, h, 0, 0), float(h)) for h in range(6)]
    got = dexcom_csv.slice_window(rows, datetime(2020, 1, 1, 1, 0, 0), hours=3.0)
    assert [g for _, g in got] == [1.0, 2.0, 3.0]  # [1:00, 4:00)


# --- food log ---------------------------------------------------------------

_FOOD_HEADER = (
    "date,time,time_begin,time_end,logged_food,amount,unit,searched_food,"
    "calorie,total_carb,dietary_fiber,sugar,protein,total_fat"
)


def _food_csv(tmp_path: Path, rows: list[tuple[str, str]], name="Food_Log_042.csv") -> Path:
    lines = [_FOOD_HEADER]
    for ts, carb in rows:
        lines.append(f",,{ts},,Food,1,unit,Food,0,{carb},0,0,0,0")
    p = tmp_path / name
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def test_read_food_log_sums_per_timestamp(tmp_path):
    p = _food_csv(
        tmp_path,
        [
            ("2020-01-01 12:00:00", "30"),
            ("2020-01-01 12:00:00", "15"),  # same meal
            ("2020-01-01 18:00:00", "50"),
            ("2020-01-01 19:00:00", "0"),  # dropped (<= 0)
        ],
    )
    rows = food_log_csv.read_food_log(p)
    assert rows == [
        (datetime(2020, 1, 1, 12, 0, 0), 45.0),
        (datetime(2020, 1, 1, 18, 0, 0), 50.0),
    ]


def test_read_food_log_rejects_wrong_csv(tmp_path):
    p = tmp_path / "nope.csv"
    p.write_text("x,y\n1,2\n", encoding="utf-8")
    with pytest.raises(ValueError):
        food_log_csv.read_food_log(p)


def test_food_log_slice_window_returns_second_offsets(tmp_path):
    rows = [
        (datetime(2020, 1, 1, 0, 0, 0), 10.0),
        (datetime(2020, 1, 1, 1, 0, 0), 20.0),
        (datetime(2020, 1, 2, 0, 0, 0), 99.0),  # outside a 24 h window
    ]
    got = food_log_csv.slice_window(rows, datetime(2020, 1, 1, 0, 0, 0), hours=24.0)
    assert got == [(0, 10.0), (3600, 20.0)]


def test_matching_food_log_path_finds_the_sibling(tmp_path):
    _food_csv(tmp_path, [("2020-01-01 12:00:00", "30")], name="Food_Log_042.csv")
    glucose = tmp_path / "Dexcom_042.csv"
    glucose.write_text("x\n", encoding="utf-8")
    assert food_log_csv.matching_food_log_path(glucose) == str(tmp_path / "Food_Log_042.csv")


def test_matching_food_log_path_none_when_absent(tmp_path):
    glucose = tmp_path / "Dexcom_777.csv"
    glucose.write_text("x\n", encoding="utf-8")
    assert food_log_csv.matching_food_log_path(glucose) is None
