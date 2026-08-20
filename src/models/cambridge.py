"""Python port of the Cambridge (Hovorka) model — see cgmsim/src/cgmsim_cambridge.c.

Kept numerically identical to the C source (same equations, same Euler
integration, same clamping) so this module's output can serve as the
noiseless "expected" glucose trace compared against the board's own copy of
this model (which additionally runs a sensor noise model on top).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CambridgeState:
    Q1: float = 0.0
    Q2: float = 0.0
    S1: float = 0.0
    S2: float = 0.0
    I: float = 0.0
    x1: float = 0.0
    x2: float = 0.0
    x3: float = 0.0
    D1: float = 0.0
    D2: float = 0.0


# Field order matches CambridgeParams in cgmsim/inc/cgmsim_cambridge.h exactly —
# this is also the wire order used by src/protocol.py's person-config encoding.
PARAM_NAMES = [
    "BW", "VG", "VI", "k12", "ka1", "ka2", "ka3", "SIT", "SID", "SIE",
    "ke", "tmaxI", "tmaxG", "AG", "EGP0", "F01", "Gpeq",
]


def default_params() -> dict[str, float]:
    return {
        "BW": 75.0, "VG": 0.16, "VI": 0.12, "k12": 0.066,
        "ka1": 0.006, "ka2": 0.060, "ka3": 0.030,
        "SIT": 51.2e-4, "SID": 8.2e-4, "SIE": 520.0e-4,
        "ke": 0.138, "tmaxI": 55.0, "tmaxG": 40.0, "AG": 0.8,
        "EGP0": 0.0161, "F01": 0.0097, "Gpeq": 100.0,
    }


def _eq_residual(I_ss: float, p: dict[str, float], Q1_ss: float, F01c_ss: float, FR_ss: float) -> float:
    x1 = p["SIT"] * I_ss
    x2 = p["SID"] * I_ss
    x3 = p["SIE"] * I_ss
    denom = p["k12"] + x2
    egp = max(0.0, p["EGP0"] * p["BW"] * (1.0 - x3))
    return -F01c_ss - x1 * Q1_ss * x2 / denom - FR_ss + egp


def init_state(p: dict[str, float]) -> CambridgeState:
    """Approximate steady state at p['Gpeq'] via bisection on plasma insulin."""
    G_ss = p["Gpeq"] / 18.016
    Q1_ss = G_ss * p["VG"] * p["BW"]
    F01c_ss = p["F01"] * p["BW"] * (G_ss / 4.5 if G_ss < 4.5 else 1.0)
    FR_ss = 0.003 * (G_ss - 9.0) if G_ss > 9.0 else 0.0

    lo, hi, mid = 0.0, 200.0, 0.0
    for _ in range(64):
        mid = 0.5 * (lo + hi)
        if _eq_residual(mid, p, Q1_ss, F01c_ss, FR_ss) > 0.0:
            lo = mid
        else:
            hi = mid

    I_ss = mid
    x1_ss = p["SIT"] * I_ss
    x2_ss = p["SID"] * I_ss
    x3_ss = p["SIE"] * I_ss
    IIR_b = I_ss * p["ke"] * p["VI"] * p["BW"]
    denom = p["k12"] + x2_ss
    Q2_ss = x1_ss * Q1_ss / denom if denom > 0.0 else 0.0

    return CambridgeState(
        Q1=Q1_ss, Q2=Q2_ss, S1=IIR_b * p["tmaxI"], S2=IIR_b * p["tmaxI"],
        I=I_ss, x1=x1_ss, x2=x2_ss, x3=x3_ss, D1=0.0, D2=0.0,
    )


def step(s: CambridgeState, p: dict[str, float], carbs_g_per_min: float,
         iir_u_per_h: float, dt_min: float) -> None:
    """Advance *s* by dt_min minutes given a carb intake rate and insulin infusion."""
    G = s.Q1 / (p["VG"] * p["BW"])
    F01c = p["F01"] * p["BW"] * (G / 4.5 if G < 4.5 else 1.0)
    FR = 0.003 * (G - 9.0) if G > 9.0 else 0.0
    UG = s.D2 / p["tmaxG"]
    M = carbs_g_per_min * 1000.0 / 180.16
    IIR = iir_u_per_h * 1000.0 / 60.0
    x3c = min(s.x3, 1.0)

    dQ1 = -F01c - s.x1 * s.Q1 + p["k12"] * s.Q2 - FR + UG + p["EGP0"] * p["BW"] * (1.0 - x3c)
    dQ2 = s.x1 * s.Q1 - (p["k12"] + s.x2) * s.Q2
    dS1 = -s.S1 / p["tmaxI"] + IIR
    dS2 = (s.S1 - s.S2) / p["tmaxI"]
    dI = s.S2 / (p["tmaxI"] * p["VI"] * p["BW"]) - p["ke"] * s.I
    dx1 = p["ka1"] * (p["SIT"] * s.I - s.x1)
    dx2 = p["ka2"] * (p["SID"] * s.I - s.x2)
    dx3 = p["ka3"] * (p["SIE"] * s.I - s.x3)
    dD1 = -s.D1 / p["tmaxG"] + M * p["AG"]
    dD2 = (s.D1 - s.D2) / p["tmaxG"]

    s.Q1 = max(0.0, s.Q1 + dQ1 * dt_min)
    s.Q2 = max(0.0, s.Q2 + dQ2 * dt_min)
    s.S1 = max(0.0, s.S1 + dS1 * dt_min)
    s.S2 = max(0.0, s.S2 + dS2 * dt_min)
    s.I = max(0.0, s.I + dI * dt_min)
    s.x1 = max(0.0, s.x1 + dx1 * dt_min)
    s.x2 = max(0.0, s.x2 + dx2 * dt_min)
    s.x3 = max(0.0, s.x3 + dx3 * dt_min)
    s.D1 = max(0.0, s.D1 + dD1 * dt_min)
    s.D2 = max(0.0, s.D2 + dD2 * dt_min)


def glucose_mg_dl(s: CambridgeState, p: dict[str, float]) -> float:
    return s.Q1 / (p["VG"] * p["BW"]) * 18.016


def basal_iir_u_per_h(p: dict[str, float]) -> float:
    """Constant basal infusion [U/h] that holds glucose at p['Gpeq'] indefinitely."""
    s = init_state(p)
    return s.I * p["ke"] * p["VI"] * p["BW"] * 60.0 / 1000.0
