"""Default parameters for the CGM sensor noise models (Breton, Facchinetti) and the
noiseless Ideal CGM — see cgmsim/src/cgmsim_sensors.c for the on-device math this
mirrors. The noise itself only ever runs on the board; this module exists purely so
the GUI can show/edit sensible defaults before sending a SensorProfile to it.
"""

from __future__ import annotations

import math


def ideal_default_params() -> dict[str, float]:
    return {}


def breton_default_params() -> dict[str, float]:
    return {"pacf": 0.70, "sigma": 1.5, "alpha": 1.0, "beta": 0.0}


def facchinetti_default_params() -> dict[str, float]:
    return {
        "a0": 1.1,
        "a1": 2e-4,
        "a2": 0.0,
        "b0": -14.8,
        "b1": 0.04,
        "b2": 0.0,
        "aw1": 1.013,
        "aw2": -0.2135,
        "sigma_v": math.sqrt(14.45),
        "ac1": 1.23,
        "ac2": -0.3995,
        "sigma_c": math.sqrt(11.3),
    }
