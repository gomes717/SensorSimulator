"""The silent-subscribe policy: when to re-arm, when to tell the user, when to stay quiet."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from services.silence_watchdog import Action, SilenceWatchdog


def _armed(limit_s=20.0, report_after=3):
    wd = SilenceWatchdog(limit_s, report_after)
    wd.arm(0.0)
    return wd


def test_nothing_happens_before_the_subscription_is_armed():
    assert SilenceWatchdog(20.0).poll(1000.0) is Action.NONE


def test_a_healthy_link_is_never_touched():
    wd = _armed()
    for t in range(5, 300, 5):
        wd.on_data(float(t))
        assert wd.poll(float(t)) is Action.NONE


def test_silence_is_rearmed_once_per_limit_not_every_tick():
    wd = _armed()
    assert wd.poll(19.9) is Action.NONE
    assert wd.poll(20.0) is Action.REARM
    assert wd.poll(20.2) is Action.NONE  # grace period restarted by the re-arm
    assert wd.poll(40.0) is Action.REARM


def test_third_consecutive_silent_stretch_is_reported_exactly_once():
    wd = _armed()
    assert [wd.poll(t) for t in (20.0, 40.0, 60.0)] == [
        Action.REARM,
        Action.REARM,
        Action.REPORT,
    ]
    assert wd.poll(80.0) is Action.REARM  # keeps re-arming, does not report again


def test_data_after_a_report_says_the_silence_is_over_and_resets_the_count():
    wd = _armed()
    for t in (20.0, 40.0, 60.0):
        wd.poll(t)
    assert wd.on_data(65.0) is True
    assert wd.on_data(70.0) is False  # only the first notification announces recovery
    assert wd.poll(90.0) is Action.REARM  # counting starts from scratch
    assert wd.poll(110.0) is Action.REARM


def test_data_between_re_arms_resets_the_count():
    wd = _armed()
    assert wd.poll(20.0) is Action.REARM
    assert wd.poll(40.0) is Action.REARM
    wd.on_data(45.0)
    assert wd.poll(65.0) is Action.REARM  # first stretch again, not a report


def test_a_stopped_board_is_not_silent_and_resuming_gets_a_grace_period():
    wd = _armed()
    wd.on_data(0.0)
    wd.set_running(False, 10.0)
    assert wd.poll(500.0) is Action.NONE
    wd.set_running(True, 500.0)
    assert wd.poll(510.0) is Action.NONE
    assert wd.poll(520.0) is Action.REARM


def test_restart_clock_gives_a_fresh_grace_period():
    wd = _armed()
    wd.restart_clock(15.0)  # e.g. the board just reset
    assert wd.poll(30.0) is Action.NONE
    assert wd.poll(35.0) is Action.REARM
