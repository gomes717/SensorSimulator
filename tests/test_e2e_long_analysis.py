"""The long run's analysis passes a board that follows its model and catches one that does not."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import e2e_long_3sensor as lg

from models import royparker
from models.engine import ModelStepper
from models.types import FoodEvent, ModelId, PersonProfile

ISO = "2020-01-01T00:00:00+00:00"


def _profile():
    p = PersonProfile("t", ModelId.ROYPARKER, royparker.default_params())
    p.food_events = [FoodEvent(30, 50.0, 15)]
    return p


def _board_ticks(profile, n=900, dt=1.0, tamper=None):
    """What a perfect board would print: the host model's own trajectory."""
    s = ModelStepper(profile)
    out = []
    for i in range(n):
        res = s.tick(dt, ISO)
        g = res.glucose + (tamper(i) if tamper else 0.0)
        out.append(lg.Tick(float(i), s.sim_clock_min, dt, round(g, 2), g, 1.0, 0.0, 0.0))
    return out


def test_a_board_that_follows_the_model_has_zero_parity_error():
    p = lg.parity(_profile(), _board_ticks(_profile()), [])
    assert p["n"] == 900 and p["max"] < 0.01


def test_a_drifting_board_is_caught():
    p = lg.parity(_profile(), _board_ticks(_profile(), tamper=lambda i: i * 0.02), [])
    assert p["p99"] > lg.PARITY_P99_ABS


def test_parity_only_looks_at_the_run_since_the_last_reset():
    old = _board_ticks(_profile(), n=50)
    new = _board_ticks(_profile(), n=300)  # sim clock restarts from 0
    assert lg.parity(_profile(), old + new, [])["n"] == 300


def test_instant_meal_is_replayed_into_the_host_model():
    prof = _profile()
    s = ModelStepper(prof)
    out = []
    for i in range(400):
        if i == 100:
            s.add_instant_food(30, 60.0)
        res = s.tick(1.0, ISO)
        out.append(lg.Tick(float(i), s.sim_clock_min, 1.0, round(res.glucose, 2), 0, 1.0, 0, 0))
    ev = lg.Instant(0, "food", (30, 60.0), t_sim=100.0)
    assert lg.parity(prof, out, [ev])["max"] < 1.5
    assert lg.parity(prof, out, [])["max"] > 5.0  # without the replay it would be flagged


def test_console_lines_are_parsed(tmp_path):
    log = tmp_path / "serial.log"
    log.write_text(
        "  12.50  model_tick[1]: t_sim=60.00min dt=1.0000 model=1 sensor=0 ds=0 "
        "glucose=101.50 reading=102.00 pisa=1.000 carbs=0.000 ex=0.0\n"
        "  12.60  comm_thread: pushed slot 1 glucose=101.50\n",
        encoding="utf-8",
    )
    tk = lg.read_ticks(log)[1][0]
    assert (tk.t_sim, tk.glucose, tk.reading) == (60.0, 101.5, 102.0)
    assert lg.read_pushes(log)[1] == [(12.6, 101.5)]


def test_ble_values_must_equal_a_board_push():
    pushes = [(float(t), 100.0 + t) for t in range(0, 60)]
    ble = [(10.2, 110.0), (20.4, 120.3), (30.1, 999.0)]  # last one was never pushed
    matched, total, bad = lg.ble_match(ble, pushes)
    assert (matched, total) == (2, 3) and bad == [(30.1, 999.0)]


def test_completeness_and_worst_gap():
    times = [i * 5.0 for i in range(100)]
    assert lg.completeness(times)[2] > 0.99
    del times[50:60]  # a 55 s hole
    _n, _, share, gap = lg.completeness(times)
    assert gap == 55.0 and share < 0.95


def test_pisa_dip_is_compared_as_true_glucose_times_the_pisa_factor():
    """The board prints its true glucose and the PISA factor separately; the host
    model's output already includes the attenuation."""
    prof = _profile()
    clean, dipped = ModelStepper(prof), ModelStepper(prof)
    out = []
    for i in range(300):
        if i == 50:
            dipped.add_instant_pisa(20, 0.45)
        true = clean.tick(1.0, ISO).glucose
        att = dipped.tick(1.0, ISO).glucose
        # an Ideal sensor also adds its per-slot offset to `reading`; parity must ignore it
        out.append(lg.Tick(float(i), clean.sim_clock_min, 1.0, true, att - 2.0, att / true, 0, 0))
    ev = lg.Instant(0, "pisa", (20, 0.45), t_sim=50.0)
    assert lg.parity(prof, out, [ev])["max"] < 0.5
    plain = [lg.Tick(t.t, t.t_sim, t.dt, t.glucose, t.reading, 1.0, 0, 0) for t in out]
    assert lg.parity(prof, plain, [ev])["max"] > 10.0  # ignoring the factor would be flagged


def test_event_checks_can_be_replayed_from_the_console_log():
    def tk(t, t_sim, glucose, carbs=0.0):
        return lg.Tick(t, t_sim, 1.0, glucose, glucose, 1.0, carbs, 0.0)

    meal = lg.Instant(1, "food", (45, 60.0), t_sim=10.0)
    rising = [
        tk(i, float(i), 100.0 + max(0, i - 10) * 2, 1.0 if i > 11 else 0.0) for i in range(30)
    ]
    assert lg.replay_check(meal, rising).result == "PASS"
    flat = [tk(i, float(i), 100.0) for i in range(30)]
    assert lg.replay_check(meal, flat).result.startswith("FAIL")


def test_a_late_issue_timestamp_is_realigned_to_the_boards_own_onset():
    """The write lands half a sim-minute after the console line it was stamped with;
    replaying from the stamp misplaces the PISA edge, the observed onset does not."""
    prof = _profile()
    clean, dipped = ModelStepper(prof), ModelStepper(prof)
    out = []
    for i in range(300):
        if i == 100:
            dipped.add_instant_pisa(20, 0.45)
        true = clean.tick(0.2, ISO).glucose
        att = dipped.tick(0.2, ISO).glucose
        out.append(lg.Tick(float(i), clean.sim_clock_min, 0.2, true, att, att / true, 0, 0))
    onset_t_sim = out[100].t_sim
    stamped_early = lg.Instant(0, "pisa", (20, 0.45), t_sim=onset_t_sim - 0.5)
    assert lg.parity(prof, out, [stamped_early])["max"] < 0.5
