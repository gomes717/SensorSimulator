"""models.alerts: one definition of normal / warning / critical, both directions."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from models.alerts import NORMAL, Alert, Level, alert_for

T = {"tbr2_below": 54.0, "tbr1_below": 70.0, "tar1_above": 180.0, "tar2_above": 250.0}


@pytest.mark.parametrize(
    ("glucose", "level", "direction"),
    [
        (53.9, Level.CRITICAL, "low"),
        (54.0, Level.WARNING, "low"),  # exactly tbr2: not yet critical
        (69.9, Level.WARNING, "low"),
        (70.0, Level.NORMAL, None),  # exactly tbr1: in range
        (120.0, Level.NORMAL, None),
        (180.0, Level.NORMAL, None),  # exactly tar1: in range
        (180.1, Level.WARNING, "high"),
        (250.0, Level.WARNING, "high"),  # exactly tar2: not yet critical
        (250.1, Level.CRITICAL, "high"),
        (399.0, Level.CRITICAL, "high"),
        (0.0, Level.CRITICAL, "low"),
    ],
)
def test_boundaries_at_the_default_thresholds(glucose, level, direction):
    assert alert_for(glucose, T) == Alert(level, direction)


def test_changed_thresholds_move_the_levels():
    wide = {**T, "tar1_above": 200.0, "tar2_above": 300.0}
    assert alert_for(190.0, T).level is Level.WARNING
    assert alert_for(190.0, wide) is NORMAL
    assert alert_for(280.0, wide).level is Level.WARNING


def test_describe_spells_out_level_and_direction():
    assert alert_for(262.0, T).describe(262.0) == "262 mg/dL — critically high"
    assert alert_for(65.0, T).describe(65.0) == "65 mg/dL — low"
    assert alert_for(100.0, T).describe(100.0) == "100 mg/dL — in range"


def test_the_graph_colours_agree_with_the_levels():
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from datetime import UTC, datetime

    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtWidgets import QApplication

    from gui.glucose_graph import GlucoseGraph
    from gui.run_clock import RunClock

    _app = QApplication.instance() or QApplication([])
    graph = GlucoseGraph(T, 0.0, RunClock(datetime.now(UTC)))
    for g, cat in [(40, "r"), (60, "y"), (100, "g"), (200, "y"), (300, "r")]:
        assert graph._category(g) == cat
