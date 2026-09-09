"""E2E-ish: every Dexcom CSV in dataset/ replays verbatim through the engine,
and the app plots exactly those values (issue: "check it matches the csv data").

Runs against the real ModelStepper (the code path the app's engine uses) and,
once, the real MainWindow. No board, no QThread timing — deterministic.
"""

import os
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication

from models import dexcom_csv
from models.engine import ModelStepper, load_csv_window
from models.types import ModelId, PersonProfile

_app = QApplication.instance() or QApplication([])  # module-level: robust when run alone
_ROOT = Path(__file__).resolve().parent.parent
FIXED_TS = "2020-01-01T00:00:00+00:00"
_DEXCOM_CSVS = sorted((_ROOT / "dataset").glob("Dexcom_*.csv"))
_TICK_MIN = dexcom_csv.DEFAULT_INTERVAL_S / 60.0  # one grid sample per tick


def _csv_profile(path: Path) -> PersonProfile:
    rows = dexcom_csv.read_egv(path)
    return PersonProfile(
        name=path.stem,
        model_id=ModelId.CAMBRIDGE,
        data_source="csv",
        csv_path=str(path),
        csv_window_start_iso=rows[0][0].isoformat(),
    )


def test_dataset_has_the_dexcom_csvs():
    assert len(_DEXCOM_CSVS) >= 10, f"expected the Dexcom_*.csv set, found {_DEXCOM_CSVS}"


@pytest.mark.parametrize("path", _DEXCOM_CSVS, ids=lambda p: p.stem)
def test_csv_window_replays_verbatim(path: Path):
    profile = _csv_profile(path)
    samples, interval_s, _foodlog = load_csv_window(profile)
    assert interval_s == dexcom_csv.DEFAULT_INTERVAL_S
    assert len(samples) == round(dexcom_csv.DEFAULT_WINDOW_HOURS * 3600 / interval_s)  # 288

    s = ModelStepper(profile)
    assert s.mode == "csv"  # no physiological model runs for a CSV person

    n = min(len(samples), 288)
    got = [round(s.tick(_TICK_MIN, FIXED_TS).glucose) for _ in range(n)]
    assert got == samples[:n]  # the plotted line IS the recorded 24 h window


def test_app_csv_mode_hides_the_food_graph_and_runs_no_model():
    """The real MainWindow, Model Only + a CSV person: the engine the app builds
    is a CSV replay (no physiological model), and the food/exercise graph is
    hidden. (isHidden(), not isVisible() — the window is never shown here.)"""
    import gui.main_window as mw

    w = mw.MainWindow()
    try:
        csv_person = _csv_profile(_DEXCOM_CSVS[0])

        w._model_only = True
        w._active_person = csv_person
        assert w._engine_slots() == {0: csv_person}
        assert ModelStepper(csv_person, allow_csv=True).mode == "csv"
        w._apply_csv_mode_view()
        assert w._graph.fe_canvas.isHidden()

        # ...and it flips back for a normal model person.
        w._active_person = PersonProfile(name="m", model_id=ModelId.CAMBRIDGE)
        w._apply_csv_mode_view()
        assert not w._graph.fe_canvas.isHidden()
    finally:
        w.close()
        subprocess.run(
            ["git", "checkout", "--", "data/profiles.json", "data/settings.json"],
            cwd=str(_ROOT),
            check=False,
        )
