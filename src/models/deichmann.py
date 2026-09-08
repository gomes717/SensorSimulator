"""Python port of the Deichmann exercise-augmented model — see cgmsim/src/cgmsim_deichmann.c."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DeichmannState:
    x1: float = 0.0
    x2: float = 0.0
    Ic: float = 0.0
    D1: float = 0.0
    D2: float = 0.0
    X: float = 0.0
    G: float = 0.0
    Y: float = 0.0
    Z: float = 0.0
    HRint: float = 0.0


# Field order matches DeichmannParams in cgmsim/inc/cgmsim_deichmann.h exactly.
PARAM_NAMES = [
    "Gpeq",
    "BW",
    "Gb",
    "Ib",
    "HRb",
    "p1",
    "p2",
    "p3",
    "alpha",
    "beta",
    "tauHR",
    "tau",
    "f",
    "AG",
    "Vg",
    "tau_m",
    "k21",
    "kd",
    "ka",
    "ke",
    "Vi",
    "IIRb",
]


def default_params() -> dict[str, float]:
    return {
        "Gpeq": 100.0,
        "BW": 70.0,
        "Gb": 172.0,
        "Ib": 10.0,
        "HRb": 80.0,
        "p1": 0.0041,
        "p2": 0.0155,
        "p3": 6.913e-6,
        "alpha": 2.59e-4,
        "beta": 3.39e-4,
        "tauHR": 5.0,
        "tau": 600.0,
        "f": 0.1,
        "AG": 0.8,
        "Vg": 1.6,
        "tau_m": 60.0,
        "k21": 0.0085,
        "kd": 0.0247,
        "ka": 0.011,
        "ke": 0.0357,
        "Vi": 0.104,
        "IIRb": 0.5,
    }


def init_state(p: dict[str, float]) -> DeichmannState:
    IIR_mU_min = p["IIRb"] * 1000.0 / 60.0
    x1_ss = IIR_mU_min / p["k21"]
    x2_ss = p["k21"] * x1_ss / (p["kd"] + p["ka"])
    Ic_ss = p["ka"] * x2_ss / (p["ke"] * p["Vi"] * p["BW"])
    X = p["p3"] * (Ic_ss - p["Ib"]) / p["p2"]
    return DeichmannState(x1=x1_ss, x2=x2_ss, Ic=Ic_ss, X=X, G=p["Gpeq"])


def step(
    s: DeichmannState,
    p: dict[str, float],
    carbs_g_this_step: float,
    iir_u_per_h: float,
    hr_bpm: float,
    dt_min: float,
) -> None:
    """Advance *s* by dt_min minutes.

    carbs_g_this_step: grams ingested during this step (0 if no meal now) — the
    model's own D1/D2 exponential compartments spread it out over tau_m.
    """
    IIR_mU_min = iir_u_per_h * 1000.0 / 60.0

    dx1 = -p["k21"] * s.x1 + IIR_mU_min
    dx2 = p["k21"] * s.x1 - (p["kd"] + p["ka"]) * s.x2
    dIc = p["ka"] / (p["Vi"] * p["BW"]) * s.x2 - p["ke"] * s.Ic

    M = carbs_g_this_step / dt_min
    dD1 = -s.D1 / p["tau_m"] + M * p["AG"]
    dD2 = (s.D1 - s.D2) / p["tau_m"]

    dI = s.Ic - p["Ib"]
    dX = -p["p2"] * s.X + p["p3"] * dI

    Ra = s.D2 / p["tau_m"] * 1000.0 / (p["Vg"] * p["BW"])

    HR_dev = hr_bpm - p["HRb"]
    dY = (HR_dev - s.Y) / p["tauHR"]
    dZ = -(p["f"] + 1.0 / p["tau"]) * s.Z + p["f"]
    dHRint = HR_dev

    Xb = p["p3"] * p["Ib"] / p["p2"]

    dG = (
        -p["p1"] * (s.G - p["Gb"])
        - s.X * s.G
        - p["alpha"] * s.HRint * s.Z * (s.X + Xb) * s.G
        - p["beta"] * s.Y * s.G
        + Ra
    )

    s.x1 = max(0.0, s.x1 + dx1 * dt_min)
    s.x2 = max(0.0, s.x2 + dx2 * dt_min)
    s.Ic = max(0.0, s.Ic + dIc * dt_min)
    s.D1 = max(0.0, s.D1 + dD1 * dt_min)
    s.D2 = max(0.0, s.D2 + dD2 * dt_min)
    s.X += dX * dt_min
    s.G = max(0.0, s.G + dG * dt_min)
    s.Y += dY * dt_min
    s.Z = max(0.0, s.Z + dZ * dt_min)
    s.HRint = max(0.0, s.HRint + dHRint * dt_min)


def glucose_mg_dl(s: DeichmannState) -> float:
    return s.G
