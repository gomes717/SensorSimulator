"""Python port of the UVA/Padova T1DMS model — see cgmsim/src/cgmsim_uva_padova.c."""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class UvaPadovaState:
    Gp: float = 0.0
    Gt: float = 0.0
    Gs: float = 0.0
    Ip: float = 0.0
    Il: float = 0.0
    Qsto1: float = 0.0
    Qsto2: float = 0.0
    Qgut: float = 0.0
    XL: float = 0.0
    I_: float = 0.0
    X: float = 0.0
    Isc1: float = 0.0
    Isc2: float = 0.0
    MealMemory: float = 1.0


# Field order matches UvaPadovaParams in cgmsim/inc/cgmsim_uva_padova.h exactly.
PARAM_NAMES = [
    "BW", "VG", "VI", "k1", "k2", "m1", "m2", "m4", "kmin", "kmax",
    "kgri", "kabs", "ki", "Fcns", "Vm0", "Vmx", "Km0", "p2u", "kp1",
    "kp2", "kp3", "ke1", "ke2", "ka1", "ka2", "kd", "Td", "bmeal",
    "cmeal", "f", "HEeq", "Gpeq", "Ib",
]


def default_params() -> dict[str, float]:
    return {
        "BW": 75.0, "VG": 1.88, "VI": 0.05, "k1": 0.065, "k2": 0.079,
        "m1": 0.190, "m2": 0.484, "m4": 0.194, "kmin": 0.0080, "kmax": 0.0558,
        "kgri": 0.0558, "kabs": 0.057, "ki": 0.0079, "Fcns": 1.0,
        "Vm0": 2.5, "Vmx": 0.047, "Km0": 225.59, "p2u": 0.0331,
        "kp1": 2.7, "kp2": 0.0021, "kp3": 0.009, "ke1": 0.0005, "ke2": 339.0,
        "ka1": 0.0018, "ka2": 0.0182, "kd": 0.0164, "Td": 10.0,
        "bmeal": 0.69, "cmeal": 0.17, "f": 0.90, "HEeq": 0.6,
        "Gpeq": 100.0, "Ib": 25.0,
    }


def _kempt(Qsto: float, D: float, p: dict[str, float]) -> float:
    """Gastric emptying rate [1/min], depends on stomach contents and meal size."""
    D_ = D if D > 1.0 else 1.0
    b, c = p["bmeal"], p["cmeal"]
    a = 5.0 / (2.0 * D_ * (1.0 - b))
    bt = 5.0 / (2.0 * D_ * c)
    arg1 = max(-20.0, min(20.0, a * (Qsto - b * D_)))
    arg2 = max(-20.0, min(20.0, bt * (Qsto - c * D_)))
    return p["kmin"] + 0.5 * (p["kmax"] - p["kmin"]) * (math.tanh(arg1) - math.tanh(arg2) + 2.0)


def init_state(p: dict[str, float]) -> UvaPadovaState:
    Gp0 = p["Gpeq"] * p["VG"]
    Gt0 = p["k1"] * Gp0 / p["k2"]
    m3eq = p["HEeq"] * p["m1"] / (1.0 - p["HEeq"])
    Ip0 = p["Ib"] * p["VI"]
    Il0 = p["m2"] * Ip0 / (p["m1"] + m3eq)
    return UvaPadovaState(
        Gp=Gp0, Gt=Gt0, Gs=Gp0, Ip=Ip0, Il=Il0,
        Qsto1=0.0, Qsto2=0.0, Qgut=0.0,
        XL=p["Ib"], I_=p["Ib"], X=0.0, Isc1=0.0, Isc2=0.0, MealMemory=1.0,
    )


def step(s: UvaPadovaState, p: dict[str, float], carbs_g_per_min: float,
         iir_u_per_min: float, dt_min: float) -> None:
    """Advance *s* by dt_min minutes given a carb intake rate [g/min] and insulin [U/min]."""
    M = carbs_g_per_min * 1000.0
    IIR = iir_u_per_min * 6000.0 / p["BW"]

    if M > 0.0:
        s.MealMemory = (s.Qsto1 + s.Qsto2) + M * dt_min

    Qsto = s.Qsto1 + s.Qsto2
    ke = _kempt(Qsto, s.MealMemory, p)

    I = s.Ip / p["VI"]
    m3eq = p["HEeq"] * p["m1"] / (1.0 - p["HEeq"])

    Ra = p["f"] * p["kabs"] * s.Qgut / p["BW"]
    Rai = p["ka1"] * s.Isc1 + p["ka2"] * s.Isc2

    Uid = (p["Vm0"] + p["Vmx"] * s.X) * s.Gt / (p["Km0"] + s.Gt)
    Uii = p["Fcns"]
    E = max(0.0, p["ke1"] * (s.Gp - p["ke2"]))
    EGP = max(0.0, p["kp1"] - p["kp2"] * s.Gp - p["kp3"] * s.XL)

    dGp = EGP + Ra - Uii - E - p["k1"] * s.Gp + p["k2"] * s.Gt
    dGt = -Uid + p["k1"] * s.Gp - p["k2"] * s.Gt
    dGs = (s.Gp - s.Gs) / p["Td"]

    dIp = -(p["m2"] + p["m4"]) * s.Ip + p["m1"] * s.Il + Rai
    dIl = p["m2"] * s.Ip - (p["m1"] + m3eq) * s.Il

    dQsto1 = -p["kgri"] * s.Qsto1 + M
    dQsto2 = -ke * s.Qsto2 + p["kgri"] * s.Qsto1
    dQgut = -p["kabs"] * s.Qgut + ke * s.Qsto2

    dXL = -p["ki"] * (s.XL - s.I_)
    dI_ = -p["ki"] * (s.I_ - I)
    dX = -p["p2u"] * s.X + p["p2u"] * (I - p["Ib"])

    dIsc1 = -(p["kd"] + p["ka1"]) * s.Isc1 + IIR
    dIsc2 = p["kd"] * s.Isc1 - p["ka2"] * s.Isc2

    s.Gp = max(0.0, s.Gp + dGp * dt_min)
    s.Gt = max(0.0, s.Gt + dGt * dt_min)
    s.Gs = max(0.0, s.Gs + dGs * dt_min)
    s.Ip = max(0.0, s.Ip + dIp * dt_min)
    s.Il = max(0.0, s.Il + dIl * dt_min)
    s.Qsto1 = max(0.0, s.Qsto1 + dQsto1 * dt_min)
    s.Qsto2 = max(0.0, s.Qsto2 + dQsto2 * dt_min)
    s.Qgut = max(0.0, s.Qgut + dQgut * dt_min)
    s.XL += dXL * dt_min
    s.I_ += dI_ * dt_min
    s.X += dX * dt_min
    s.Isc1 = max(0.0, s.Isc1 + dIsc1 * dt_min)
    s.Isc2 = max(0.0, s.Isc2 + dIsc2 * dt_min)


def glucose_mg_dl(s: UvaPadovaState, p: dict[str, float]) -> float:
    return s.Gp / p["VG"]


def basal_iir_u_per_h(p: dict[str, float]) -> float:
    """Constant basal infusion [U/h] that keeps plasma insulin at p['Ib']."""
    m3eq = p["HEeq"] * p["m1"] / (1.0 - p["HEeq"])
    Ip_ss = p["Ib"] * p["VI"]
    iir_pmol_kg_min = Ip_ss * ((p["m2"] + p["m4"]) - p["m1"] * p["m2"] / (p["m1"] + m3eq))
    return iir_pmol_kg_min * p["BW"] / 100.0
