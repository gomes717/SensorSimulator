"""Issue 04: EnginePool runs one engine per slot, independently, on a shared
speed, and routes instant events by slot.
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

pytest.importorskip("PyQt6.QtCore")

from PyQt6.QtCore import QCoreApplication
from PyQt6.QtTest import QTest

from models.engine import EnginePool, ModelStepper
from models.types import ModelId, PersonProfile

FIXED_TS = "2020-01-01T00:00:00+00:00"


def test_two_steppers_run_independently():
    a = ModelStepper(PersonProfile(name="a", model_id=ModelId.CAMBRIDGE))
    b = ModelStepper(PersonProfile(name="b", model_id=ModelId.DEICHMANN))
    ga = [a.tick(1.0, FIXED_TS).glucose for _ in range(120)]
    gb = [b.tick(1.0, FIXED_TS).glucose for _ in range(120)]
    # different models from the same nominal start -> different trajectories
    assert ga != gb
    # and stepping one never perturbed the other: re-running a in isolation matches
    a2 = ModelStepper(PersonProfile(name="a", model_id=ModelId.CAMBRIDGE))
    assert [round(x, 6) for x in ga] == [
        round(a2.tick(1.0, FIXED_TS).glucose, 6) for _ in range(120)
    ]


@pytest.fixture(scope="module")
def app():
    return QCoreApplication.instance() or QCoreApplication([])


def test_pool_emits_per_slot_and_instants_route_by_slot(app):
    pool = EnginePool()
    got: list[tuple[int, float]] = []
    pool.expected_reading.connect(lambda s, _ts, g, _c, _e: got.append((s, g)))

    pool.rebuild(
        {
            1: PersonProfile(name="p1", model_id=ModelId.CAMBRIDGE),
            3: PersonProfile(name="p3", model_id=ModelId.CAMBRIDGE),
        },
        speed_mult=60.0,
        paused=False,
    )
    assert pool.slots == [1, 3]
    try:
        QTest.qWait(4000)  # pyright: ignore[reportCallIssue]  (PyQt6 QTest stub)
        base_by_slot = {s: [g for sl, g in got if sl == s] for s in (1, 3)}
        assert base_by_slot[1] and base_by_slot[3]  # both slots ticked

        n_before = len(got)
        pool.add_instant_pisa(1, 6.0, 0.5)  # slot 1 only
        QTest.qWait(6000)  # pyright: ignore[reportCallIssue]
        after = got[n_before:]
        s1 = [g for s, g in after if s == 1]
        s3 = [g for s, g in after if s == 3]
        assert min(s1) < base_by_slot[1][-1] - 10.0  # slot 1 dipped
        assert min(s3) > base_by_slot[3][-1] - 5.0  # slot 3 untouched
    finally:
        pool.stop_all()
    assert pool.is_empty()
