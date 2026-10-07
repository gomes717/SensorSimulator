"""Users-screen slice 7, pure part: turning a chosen Dexcom file and a start time into the 24 h
window a user keeps (models/user_csv.py), and the per-minute curve the CSV page draws."""

from datetime import datetime
from pathlib import Path

import pytest

from models import user_csv, user_edit, user_schedule, user_store
from models.types import CsvTrack

_HEADER = (
    "Index,Timestamp (YYYY-MM-DDThh:mm:ss),Event Type,Event Subtype,Patient Info,"
    "Device Info,Source Device ID,Glucose Value (mg/dL),Insulin Value (u),"
    "Carb Value (grams),Duration (hh:mm:ss),Glucose Rate of Change (mg/dL/min),"
    "Transmitter Time (Long Integer)"
)
_START = datetime(2020, 1, 1, 0, 0, 0)


def _dexcom(tmp_path: Path, name="Dexcom_007.csv", days=1) -> Path:
    lines = [_HEADER]
    for i in range(288 * days):
        minutes = i * 5
        day, rest = divmod(minutes, 1440)
        stamp = f"2020-01-{1 + day:02d} {rest // 60:02d}:{rest % 60:02d}:00"
        lines.append(f"{i + 1},{stamp},EGV,,,,iPhone G6,{100 + i % 50},,,,,")
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _food_log(tmp_path: Path, name="Food_Log_007.csv") -> Path:
    path = tmp_path / name
    path.write_text(
        "date,time,time_begin,time_end,logged_food,amount,unit,searched_food,calorie,"
        "total_carb,dietary_fiber,sugar,protein,total_fat\n"
        "2020-01-01,08:00:00,2020-01-01 08:00:00,,Toast,1,,toast,100,30.0,1,2,3,4\n"
        "2020-01-01,08:00:00,2020-01-01 08:00:00,,Jam,1,,jam,50,15.0,0,12,0,0\n"
        "2020-01-01,13:30:00,2020-01-01 13:30:00,,Rice,1,,rice,200,60.0,1,0,4,1\n"
        "2020-01-05,13:30:00,2020-01-05 13:30:00,,Later,1,,x,100,99.0,1,0,4,1\n",
        encoding="utf-8",
    )
    return path


# -- load_track ---------------------------------------------------------------------------


def test_a_window_is_288_samples_at_the_dexcom_cadence(tmp_path):
    track = user_csv.load_track(_dexcom(tmp_path), _START)
    assert len(track.samples) == 288
    assert track.interval_s == 300
    assert track.samples[:3] == [100, 101, 102]


def test_the_track_remembers_where_it_came_from(tmp_path):
    track = user_csv.load_track(_dexcom(tmp_path), _START)
    assert track.start_iso == _START.isoformat()
    assert track.source_name == "Dexcom_007.csv"


def test_a_window_starting_later_in_the_recording_is_cut_from_there(tmp_path):
    path = _dexcom(tmp_path, days=2)
    track = user_csv.load_track(path, datetime(2020, 1, 2, 0, 0, 0))
    assert track.samples[0] == 100 + 288 % 50  # the first sample of day two


def test_the_matching_food_log_is_picked_up_without_asking(tmp_path):
    track = user_csv.load_track(_dexcom(tmp_path), _START)
    assert track.foodlog == []  # no Food_Log_007.csv yet
    _food_log(tmp_path)
    track = user_csv.load_track(_dexcom(tmp_path), _START)
    # items sharing a timestamp are one meal; a meal outside the window is left out
    assert track.foodlog == [(8 * 3600, 45.0), (13 * 3600 + 1800, 60.0)]


def test_a_broken_food_log_does_not_stop_the_glucose_window(tmp_path):
    path = _dexcom(tmp_path)
    (tmp_path / "Food_Log_007.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    assert user_csv.load_track(path, _START).foodlog == []


def test_a_file_that_is_not_a_dexcom_export_is_refused_with_a_reason(tmp_path):
    bad = tmp_path / "Dexcom_001.csv"
    bad.write_text("a,b,c\n1,2,3\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Dexcom"):
        user_csv.load_track(bad, _START)


def test_a_missing_file_is_refused_with_a_reason(tmp_path):
    with pytest.raises(ValueError, match="read"):
        user_csv.load_track(tmp_path / "gone.csv", _START)


def test_a_window_with_no_readings_is_refused(tmp_path):
    path = _dexcom(tmp_path)
    with pytest.raises(ValueError, match="24 h"):
        user_csv.load_track(path, datetime(2021, 6, 1, 0, 0, 0))


# -- setting it on a user ------------------------------------------------------------------


def test_set_csv_replaces_the_users_window_and_keeps_everything_else():
    user = user_store.new_user("Ana")
    track = CsvTrack(samples=[100, 110], interval_s=300, foodlog=[], start_iso=None)
    user_edit.set_csv(user, track)
    assert user.csv is track
    assert user.mode == "model"  # choosing a file does not switch the source by itself


# -- the curve --------------------------------------------------------------------------------


def test_the_csv_curve_has_one_value_per_minute_holding_each_sample():
    track = CsvTrack(samples=[100, 110, 120], interval_s=300, foodlog=[])
    series = user_schedule.csv_series(track)
    assert len(series) == 1440
    assert series[0] == series[4] == 100  # a sample lasts its 5 minutes
    assert series[5] == 110
    assert series[10] == 120
    assert series[1439] == 120  # past the data the last value holds


def test_an_empty_window_draws_nothing():
    assert (
        user_schedule.csv_series(CsvTrack(samples=[], interval_s=300, foodlog=[])) == [0.0] * 1440
    )
