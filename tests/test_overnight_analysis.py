"""The overnight test's scoring (scripts/overnight_analysis.py): pure functions of ticks."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import e2e_long_3sensor as lg
import overnight_analysis as oa
from e2e_long_3sensor import Tick

from models import cambridge
from models.types import CsvTrack, ModelId, PersonProfile, SensorId


def _tick(t, t_sim, glucose, reading=None, pisa=1.0, dt=1.0):
    return Tick(t, t_sim, dt, glucose, glucose if reading is None else reading, pisa, 0.0, 0.0)


def _csv_profile(samples, interval_s=300):
    p = PersonProfile("c", ModelId.CAMBRIDGE, cambridge.default_params())
    p.data_source = "csv"
    p.csv_track = CsvTrack(samples=samples, interval_s=interval_s, foodlog=[])
    return p


# -- the board's segments and which app epoch belongs to which ---------------------------


def test_board_segments_split_at_every_clock_reset():
    ticks = [_tick(1, 0, 1), _tick(2, 1, 1), _tick(3, 2, 1), _tick(4, 0, 1), _tick(5, 1, 1)]
    assert [len(s) for s in oa.board_segments(ticks)] == [3, 2]


def test_an_app_epoch_pairs_only_with_a_segment_that_started_with_it():
    segments = [[_tick(100.0, 0, 1)], [_tick(400.0, 0, 1)], [_tick(700.5, 0, 1)]]
    assert oa.pair_epochs([100.5, 700.0], segments) == [(0, 0), (1, 2)]
    assert oa.pair_epochs([250.0], segments) == []  # started at another time: not comparable


# -- the app's model against the board's ---------------------------------------------------


def test_identical_series_agree_exactly():
    board = [_tick(i, i, 100 + i) for i in range(200)]
    app = [oa.AppTick(float(i), 100.0 + i) for i in range(200)]
    result = oa.app_vs_board(app, board, 1.0, [])
    assert result["n"] == 200 and result["max"] == 0.0


def test_a_phase_offset_within_the_slack_is_not_an_error():
    board = [_tick(i, i, 100 + i) for i in range(200)]
    app = [oa.AppTick(float(i), 100.0 + i + 2) for i in range(190)]  # two ticks ahead
    assert oa.app_vs_board(app, board, 1.0, [], slack=2)["max"] == 0.0
    assert oa.app_vs_board(app, board, 1.0, [], slack=0)["max"] == 2.0


def test_a_real_difference_is_reported_and_masks_hide_it():
    board = [_tick(i, i, 100) for i in range(200)]
    app = [oa.AppTick(float(i), 100.0 + (40 if 50 <= i < 60 else 0)) for i in range(200)]
    assert oa.app_vs_board(app, board, 1.0, [])["max"] == 40.0
    assert oa.app_vs_board(app, board, 1.0, [(45, 65)])["max"] == 0.0


def test_the_board_glucose_is_compared_after_pisa():
    board = [_tick(i, i, 100, pisa=0.5) for i in range(50)]
    app = [oa.AppTick(float(i), 50.0) for i in range(50)]
    assert oa.app_vs_board(app, board, 1.0, [])["max"] == 0.0


def test_nothing_to_compare_is_zero_ticks_not_a_pass():
    assert oa.app_vs_board([], [_tick(0, 0, 1)], 1.0, [])["n"] == 0
    assert oa.app_vs_board([oa.AppTick(0, 1)], [], 1.0, [])["n"] == 0


def test_the_limit_never_drops_below_the_floor_and_grows_with_the_tick():
    p = PersonProfile("p", ModelId.CAMBRIDGE, cambridge.default_params())
    assert oa.app_limit(p, [], 60.0, 100.0) == oa.APP_VS_BOARD_P99_MIN
    short = [lg.Instant(0, "food", (2, 50.0), 10.0)]  # a 2-minute window at x60: one tick = 50 %
    assert oa.app_limit(p, short, 60.0, 100.0) == 50.0


# -- a recording that loops ----------------------------------------------------------------


def test_a_run_past_the_end_of_the_window_must_replay_it_from_the_start():
    samples = [100 + (i % 50) for i in range(288)]  # a 24 h window at 5 min
    profile = _csv_profile(samples)
    ticks = []
    for i in range(1500):  # 25 sim hours, one tick per sim minute
        row = int((i * 60 % (288 * 300)) // 300)
        ticks.append(_tick(float(i), float(i), float(samples[row])))
    result = oa.csv_loop(profile, ticks)
    assert result["wrapped"] and result["after"] == 60
    assert result["ok"] == result["after"] and result["worst"] == 0.0


def test_a_recording_that_stops_at_the_end_is_not_a_loop():
    samples = [100 + (i % 50) for i in range(288)]
    ticks = []
    for i in range(1500):
        row = min(int(i * 60 // 300), 287)  # holds the last sample instead of wrapping
        ticks.append(_tick(float(i), float(i), float(samples[row])))
    result = oa.csv_loop(_csv_profile(samples), ticks)
    assert result["wrapped"] and result["ok"] < result["after"] * oa.CSV_LOOP_MIN_SHARE


def test_a_run_that_never_reaches_the_end_has_not_wrapped():
    samples = [100] * 288
    ticks = [_tick(float(i), float(i), 100.0) for i in range(600)]
    assert oa.csv_loop(_csv_profile(samples), ticks)["wrapped"] is False


# -- the sensor models ---------------------------------------------------------------------


def test_the_ideal_sensor_adds_only_a_small_constant_offset():
    ticks = [_tick(i, i, 100 + (i % 7), reading=100 + (i % 7) - 2.0) for i in range(60)]
    assert oa.sensor_noise(SensorId.IDEAL, ticks)["ok"] is True


def test_an_ideal_sensor_that_is_noisy_fails():
    ticks = [_tick(i, i, 100.0, reading=100.0 + (-1) ** i * 4) for i in range(60)]
    assert oa.sensor_noise(SensorId.IDEAL, ticks)["ok"] is False


def test_a_noisy_sensor_must_add_noise():
    flat = [_tick(i, i, 100.0, reading=100.0) for i in range(60)]
    noisy = [_tick(i, i, 100.0, reading=100.0 + (-1) ** i * 4) for i in range(60)]
    assert oa.sensor_noise(SensorId.BRETON, flat)["ok"] is False
    assert oa.sensor_noise(SensorId.BRETON, noisy)["ok"] is True


def test_too_few_ticks_is_unknown_not_a_pass():
    assert oa.sensor_noise(SensorId.BRETON, [_tick(0, 0, 100.0)])["ok"] is None


def test_masks_cover_the_event_and_its_aftermath():
    (lo, hi) = oa.masks_for([lg.Instant(0, "food", (30, 50.0), 100.0)], 1.0)[0]
    assert lo < 100.0 and hi >= 130.0
