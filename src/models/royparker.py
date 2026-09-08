"""Python port of the Roy & Parker exercise model — see cgmsim/src/cgmsim_royparker.c."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RoyParkerState:
    NG: float = 0.0
    PVO2max: float = 0.0
    I: float = 0.0
    X: float = 0.0
    G: float = 0.0
    Gprod: float = 0.0
    Gup: float = 0.0
    Ie: float = 0.0
    Ggly: float = 0.0
    meal_carbs_g: float = 0.0
    meal_start_min: float = -1e9


# Single source of truth for this model's parameter order (api.protocol +
# gui.person_config_window reference it, no copies). Order must match
# RoyParkerParams in firmware/peripheral_cgms/src/models/cgmsim_royparker.h —
# pinned by tests/test_param_order.py against tests/param_order/royparker.golden.
PARAM_NAMES = [
    "Gpeq",
    "BW",
    "VolG",
    "Ib",
    "u1b",
    "p1",
    "p2",
    "p3",
    "p4",
    "n",
    "a1",
    "a2",
    "a3",
    "a4",
    "a5",
    "a6",
    "k",
    "T1",
    "kG",
    "Tasc",
    "Tmax",
    "Tdes",
]


def default_params() -> dict[str, float]:
    return {
        "Gpeq": 100.0,
        "BW": 70.0,
        "VolG": 117.0,
        "Ib": 11.0,
        "u1b": 1.0,
        "p1": 0.035,
        "p2": 0.050,
        "p3": 0.000028,
        "p4": 9.8e-5,
        "n": 0.142,
        "a1": 0.00158,
        "a2": 0.056,
        "a3": 0.00195,
        "a4": 0.0485,
        "a5": 0.00125,
        "a6": 0.075,
        "k": 0.0108,
        "T1": 6.0,
        "kG": 0.022,
        "Tasc": 10.0,
        "Tmax": 35.0,
        "Tdes": 10.0,
    }


def init_state(p: dict[str, float]) -> RoyParkerState:
    """Steady state with no exercise (Gprod = Gup = Ie = Ggly = 0)."""
    IIR_uU_min = p["u1b"] * 1e6 / 60.0
    I = p["p4"] * IIR_uU_min / p["n"]
    X = max(0.0, p["p3"] * (I - p["Ib"]) / p["p2"])
    return RoyParkerState(I=I, X=X, G=p["Gpeq"], meal_start_min=-1e9)


def _gastric_rate(s: RoyParkerState, p: dict[str, float], t_sim: float) -> float:
    """Trapezoidal gastric emptying rate [g/min] for the current meal, if any."""
    if s.meal_carbs_g <= 0.0:
        return 0.0
    elapsed = t_sim - s.meal_start_min
    total = p["Tasc"] + p["Tmax"] + p["Tdes"]
    if elapsed < 0.0 or elapsed > total:
        return 0.0
    area = 0.5 * p["Tasc"] + p["Tmax"] + 0.5 * p["Tdes"]
    peak = s.meal_carbs_g / area
    if elapsed <= p["Tasc"]:
        return peak * elapsed / p["Tasc"]
    if elapsed <= p["Tasc"] + p["Tmax"]:
        return peak
    return peak * (1.0 - (elapsed - p["Tasc"] - p["Tmax"]) / p["Tdes"])


def step(
    s: RoyParkerState,
    p: dict[str, float],
    meal_g: float,
    iir_u_per_h: float,
    exercise_pct: float,
    t_sim_min: float,
    dt_min: float,
) -> None:
    """Advance *s* by dt_min minutes.

    meal_g: carbs ingested this step [g] (0 if no meal starting now — an
    in-progress meal keeps absorbing via its own trapezoidal profile).
    """
    if meal_g > 0.0:
        s.meal_carbs_g = meal_g
        s.meal_start_min = t_sim_min

    IIR_uU_min = iir_u_per_h * 1e6 / 60.0
    Gemp = _gastric_rate(s, p, t_sim_min)

    dNG = Gemp - p["kG"] * s.NG
    dPVO2max = -0.8 * s.PVO2max + 0.8 * exercise_pct
    dI = -p["n"] * s.I + p["p4"] * IIR_uU_min - s.Ie
    dX = -p["p2"] * s.X + p["p3"] * (s.I - p["Ib"])
    dGprod = p["a1"] * s.PVO2max - p["a2"] * s.Gprod
    dGup = p["a3"] * s.PVO2max - p["a4"] * s.Gup
    dIe = p["a5"] * s.PVO2max - p["a6"] * s.Ie
    dGgly = p["k"] * s.PVO2max if exercise_pct > 0.0 else -s.Ggly / p["T1"]

    u2 = p["kG"] * s.NG * 1000.0  # g/min -> mg/min
    dG = (
        -p["p1"] * (s.G - p["Gpeq"])
        - s.X * s.G
        + (p["BW"] / p["VolG"]) * (s.Gprod - s.Ggly - s.Gup)
        + u2 / p["VolG"]
    )

    s.NG = max(0.0, s.NG + dNG * dt_min)
    s.PVO2max = max(0.0, s.PVO2max + dPVO2max * dt_min)
    s.I = max(0.0, s.I + dI * dt_min)
    s.X += dX * dt_min
    s.G = max(0.0, s.G + dG * dt_min)
    s.Gprod = max(0.0, s.Gprod + dGprod * dt_min)
    s.Gup = max(0.0, s.Gup + dGup * dt_min)
    s.Ie = max(0.0, s.Ie + dIe * dt_min)
    s.Ggly = max(0.0, s.Ggly + dGgly * dt_min)


def glucose_mg_dl(s: RoyParkerState) -> float:
    return s.G
