"""CommandsPanel: three buttons, each opening a modal form; typed signals; blocked reasons."""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication, QDialog

from gui.command_dialogs import ExerciseDialog, FoodDialog, PisaDialog
from gui.commands_panel import CommandsPanel

_APP = QApplication.instance() or QApplication([])


@pytest.fixture
def panel():
    return CommandsPanel()


def _accept(monkeypatch, dialog_class, **fields):
    """Make *dialog_class*.exec() fill in *fields* and accept, like a user would."""

    def fake_exec(self):
        for name, value in fields.items():
            getattr(self, name).setValue(value)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(dialog_class, "exec", fake_exec)


def test_the_panel_is_just_three_buttons(panel):
    assert [b.text() for b in panel.send_buttons()] == ["Food…", "Exercise…", "PISA…"]


def test_the_pisa_label_does_not_say_false_low(panel):
    assert all("false" not in b.text().lower() for b in panel.send_buttons())
    assert "false" not in PisaDialog().windowTitle().lower()


def test_the_food_button_asks_then_emits(panel, monkeypatch):
    got = []
    panel.food_requested.connect(lambda carbs, spread: got.append((carbs, spread)))
    _accept(monkeypatch, FoodDialog, _carbs=80.0, _spread=10)
    panel.send_buttons()[0].click()
    assert got == [(80.0, 10)]


def test_the_exercise_button_asks_then_emits(panel, monkeypatch):
    got = []
    panel.exercise_requested.connect(lambda dur, pct: got.append((dur, pct)))
    _accept(monkeypatch, ExerciseDialog, _duration=30, _intensity=70.0)
    panel.send_buttons()[1].click()
    assert got == [(30, 70.0)]


def test_the_pisa_button_asks_then_emits_depth_as_a_fraction(panel, monkeypatch):
    got = []
    panel.pisa_requested.connect(lambda dur, depth: got.append((dur, depth)))
    _accept(monkeypatch, PisaDialog, _duration=15, _depth=30.0)
    panel.send_buttons()[2].click()
    assert got == [(15, pytest.approx(0.30))]


def test_cancelling_a_dialog_sends_nothing(panel, monkeypatch):
    got = []
    panel.food_requested.connect(lambda *a: got.append(a))
    monkeypatch.setattr(FoodDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    panel.send_buttons()[0].click()
    assert got == []


def test_the_send_helpers_emit_like_the_dialogs_do(panel):
    seen = []
    panel.food_requested.connect(lambda *a: seen.append(("food", *a)))
    panel.exercise_requested.connect(lambda *a: seen.append(("ex", *a)))
    panel.pisa_requested.connect(lambda *a: seen.append(("pisa", *a)))
    assert panel.send_food(55.0, 40) and panel.send_exercise(20, 60.0) and panel.send_pisa(12, 35.0)
    assert seen == [("food", 55.0, 40), ("ex", 20, 60.0), ("pisa", 12, pytest.approx(0.35))]


def test_a_blocked_panel_is_disabled_says_why_and_sends_nothing(panel):
    seen = []
    panel.food_requested.connect(lambda *a: seen.append(a))
    panel.set_blocked("Start a run to send commands.")
    assert not any(b.isEnabled() for b in panel.send_buttons())
    assert panel.result_text() == "Start a run to send commands."
    assert panel.blocked_reason
    assert panel.send_food(50.0, 15) is False and seen == []


def test_unblocking_clears_the_reason_but_keeps_a_real_result(panel):
    panel.set_blocked("No live sensor.")
    panel.set_blocked("")
    assert all(b.isEnabled() for b in panel.send_buttons())
    assert panel.result_text() == ""

    panel.set_result("✓ Food 80 g sent to 1 sensor(s).")
    panel.set_blocked("Start a run.")
    panel.set_blocked("")
    assert panel.result_text() == ""  # the reason replaced the result while blocked
    panel.set_result("✓ PISA sent.")
    panel.set_blocked("")
    assert panel.result_text() == "✓ PISA sent."
