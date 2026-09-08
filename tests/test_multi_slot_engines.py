"""Issue 04: MainWindow runs one engine per occupied board slot and files each
slot's expected line into the matching per-user history bucket.
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication

from models.types import ModelId, PersonProfile


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(app):
    import gui.main_window as mw

    w = mw.MainWindow()
    w._person_profiles = [
        PersonProfile(name="P0", model_id=ModelId.CAMBRIDGE),
        PersonProfile(name="P1", model_id=ModelId.UVA_PADOVA),
        PersonProfile(name="P2", model_id=ModelId.DEICHMANN),
    ]
    yield w
    w._engines.stop_all()
    w.close()


def test_model_only_is_one_slot(win):
    win._model_only = True
    win._active_person = win._person_profiles[0]
    win._restart_engine()
    assert win._engines.slots == [0]
    assert win._per_slot_expected is False
    win._engines.stop_all()


def test_layout_assignments_build_one_engine_per_slot(win):
    win._model_only = False
    win._board_layout.slots[0].person = "P0"
    win._board_layout.slots[2].person = "P2"
    win._restart_engine()
    assert win._engines.slots == [0, 2]
    assert win._per_slot_expected is True
    win._engines.stop_all()


def test_expected_ticks_route_to_per_slot_history(win):
    win._model_only = False
    win._board_layout.slots[0].person = "P0"
    win._board_layout.slots[1].person = "P1"
    win._restart_engine()  # builds a paused pool
    win._per_slot_expected = True

    uid0 = win._slot_user_id(0)
    uid1 = win._slot_user_id(1)
    assert uid0 != uid1

    # hand-drive a few ticks (bypassing the QThread) straight into the handler
    for _ in range(3):
        win._on_expected_reading(0, "2020-01-01T00:00:00+00:00", 101.0, 0.0, 0.0)
        win._on_expected_reading(1, "2020-01-01T00:00:01+00:00", 202.0, 0.0, 0.0)

    assert win._history[uid0]["ex_gy"] == [101.0, 101.0, 101.0]
    assert win._history[uid1]["ex_gy"] == [202.0, 202.0, 202.0]
    assert not win._history[uid0]["gy"]  # received buffer untouched
    win._engines.stop_all()
