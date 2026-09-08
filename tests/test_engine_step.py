"""Regression pin for issue 01: the engine's per-tick logic runs without a
QThread, without sleeping, with an injected clock.

Also the deterministic base that issues 02 (PISA) and 03 (speed/ODE) build on.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from models import cambridge, royparker
from models.engine import ModelStepper, TickResult
from models.types import ExerciseEvent, FoodEvent, ModelId, PersonProfile

FIXED_TS = "2020-01-01T00:00:00+00:00"


def _steps(stepper: ModelStepper, n: int, dt_min: float = 1.0) -> list[TickResult]:
    return [stepper.tick(dt_min, FIXED_TS) for _ in range(n)]


@pytest.mark.parametrize(
    "model_id",
    [ModelId.CAMBRIDGE, ModelId.UVA_PADOVA, ModelId.ROYPARKER, ModelId.DEICHMANN],
)
def test_mode_and_channels_with_no_input(model_id):
    """No meals/exercise/events -> carbs and exercise channels stay at zero."""
    s = ModelStepper(PersonProfile(name="t", model_id=model_id))
    assert s.mode == "model"
    out = _steps(s, 60)
    assert all(r.carbs_rate == 0.0 and r.exercise_pct == 0.0 for r in out)
    assert all(r.glucose > 0.0 for r in out)


def test_stepper_matches_raw_cambridge():
    """The rate-fed adapter path is a faithful wrapper of cambridge.step()."""
    s = ModelStepper(PersonProfile(name="t", model_id=ModelId.CAMBRIDGE))
    params = {**cambridge.default_params()}
    state = cambridge.init_state(params)
    basal = cambridge.basal_iir_u_per_h(params)
    for _ in range(120):
        got = s.tick(1.0, FIXED_TS).glucose
        cambridge.step(state, params, 0.0, basal, 1.0)
        assert got == pytest.approx(cambridge.glucose_mg_dl(state, params), abs=1e-9)


def test_stepper_matches_raw_royparker():
    """The impulse-fed adapter path is a faithful wrapper of royparker.step()."""
    s = ModelStepper(PersonProfile(name="t", model_id=ModelId.ROYPARKER))
    params = {**royparker.default_params()}
    state = royparker.init_state(params)
    basal = params["u1b"]
    t = 0.0
    for _ in range(120):
        got = s.tick(1.0, FIXED_TS).glucose
        royparker.step(state, params, 0.0, basal, 0.0, t, 1.0)
        t += 1.0
        assert got == pytest.approx(royparker.glucose_mg_dl(state), abs=1e-9)


def test_sim_clock_advances_by_dt():
    s = ModelStepper(PersonProfile(name="t", model_id=ModelId.CAMBRIDGE))
    _steps(s, 10, dt_min=0.5)
    assert s.sim_clock_min == pytest.approx(5.0)
    _steps(s, 3, dt_min=2.0)
    assert s.sim_clock_min == pytest.approx(11.0)


def test_deterministic_across_instances():
    def run():
        s = ModelStepper(PersonProfile(name="t", model_id=ModelId.CAMBRIDGE))
        return [round(r.glucose, 6) for r in _steps(s, 120)]

    assert run() == run()


def test_timestamp_is_passed_through():
    s = ModelStepper(PersonProfile(name="t", model_id=ModelId.CAMBRIDGE))
    assert s.tick(1.0, "abc").timestamp == "abc"


def test_instant_pisa_envelope():
    """40 % / 10 min PISA drives the reading to 0.6x at the midpoint and back to 1x."""
    s = ModelStepper(PersonProfile(name="t", model_id=ModelId.CAMBRIDGE))
    base = s.tick(1.0, FIXED_TS).glucose  # settle one tick, read the flat line
    s.add_instant_pisa(10.0, 0.40)
    vals = [r.glucose for r in _steps(s, 11)]
    assert min(vals) == pytest.approx(base * 0.60, abs=0.5)  # sin(pi/2) = 1
    assert vals[-1] == pytest.approx(base, abs=0.5)  # fully recovered
    # underlying model glucose never actually moved
    assert s.tick(1.0, FIXED_TS).glucose == pytest.approx(base, abs=0.5)


def test_instant_food_raises_then_decays_carbs_rate():
    s = ModelStepper(PersonProfile(name="t", model_id=ModelId.CAMBRIDGE))  # rate-fed
    _steps(s, 2)
    s.add_instant_food(10.0, 40.0)  # 40 g over 10 min -> 4 g/min while active
    active = _steps(s, 10)
    assert active[0].carbs_rate == pytest.approx(4.0, abs=1e-6)
    after = _steps(s, 3)
    assert all(r.carbs_rate == 0.0 for r in after)


def test_scheduled_meal_bumps_glucose():
    p = PersonProfile(
        name="t",
        model_id=ModelId.CAMBRIDGE,
        food_events=[FoodEvent(time_of_day_min=0, carbs_g=60.0, duration_min=30)],
    )
    s = ModelStepper(p)
    peak = max(r.glucose for r in _steps(s, 180))
    assert peak > 110.0  # a 60 g breakfast has to move the line


def test_scheduled_exercise_reports_intensity():
    p = PersonProfile(
        name="t",
        model_id=ModelId.ROYPARKER,
        exercise_events=[ExerciseEvent(time_of_day_min=0, duration_min=30, intensity_pct=60.0)],
    )
    s = ModelStepper(p)
    out = _steps(s, 20)
    assert out[0].exercise_pct == pytest.approx(60.0)
