#ifndef CGMSIM_SENSORS_H
#define CGMSIM_SENSORS_H

/*
 * CGM / SMBG sensor models.
 *
 * All sensors receive the true plasma glucose [mg/dl] every simulation
 * step and return a CGMReading with (value_mg_dl, valid). Every sensor
 * emits a fresh reading on every call — a real CGM's own "how often does
 * a value change" is a transport/reporting-cadence question, not something
 * this model layer throttles; that cadence is entirely up to whatever
 * drains these readings (comm_thread's own poll/notify interval). valid == 0
 * is not used by any of these three sensors (kept in CGMReading for callers
 * that may not always have a fresh value, e.g. a future on-demand
 * SMBG-style sensor).
 *
 * Ported unchanged from SensorSimulator/cgmsim/inc/cgmsim_sensors.h — this
 * noise math only ever runs here (on-device); the Python app's "expected"
 * trace is deliberately noiseless (see SensorSimulator/src/models/engine.py).
 */

#include "cgmsim_types.h"

/* ── Ideal CGM ─────────────────────────────────────────────────────────
 * Noiseless, no lag, no internal state — always returns the true value.  */

CGMReading ideal_cgm_update(double gp_mg_dl);

/* ── Ideal SMBG ─────────────────────────────────────────────────────────
 * Noiseless point measurement, valid = 1 every call (user-triggered).  */

CGMReading ideal_smbg_measure(double gp_mg_dl);

/* ── Breton & Kovatchev 2008 ─────────────────────────────────────────────
 * AR(1) noise model with linear calibration error.
 * Reference: Breton M, Kovatchev B. J Diabetes Sci Technol 2008.         */

typedef struct {
    double pacf;                /* AR coefficient,    default 0.70        */
    double sigma;               /* noise std dev,     default 1.5 mg/dl   */
    double alpha;               /* calibration gain,  default 1.0         */
    double beta;                /* calibration offset, default 0.0 mg/dl  */
    /* internal */
    double noise;               /* current noise state                    */
    unsigned int rng_state;     /* simple LCG seed                        */
} BretonState;

void       breton_init(BretonState *s, unsigned int seed);
CGMReading breton_update(BretonState *s, double gp_mg_dl, double dt);

/* ── Facchinetti et al. 2014 ─────────────────────────────────────────────
 * Time-varying linear calibration + two AR(2) noise components.
 * Reference: Facchinetti A et al. IEEE Trans Biomed Eng 2014.            */

typedef struct {
    /* calibration polynomial coefficients (Eq. 5-6) */
    double a0, a1, a2;          /* relative-error: a(t) = a0+a1*dt+a2*dt^2 */
    double b0, b1, b2;          /* bias/drift:     b(t) = b0+b1*dt+b2*dt^2 */
    /* AR(2) measurement noise (Eq. 7) */
    double aw1, aw2;            /* AR coefficients: 1.013, -0.2135          */
    double sigma_v;             /* noise std dev: sqrt(14.45) mg/dl         */
    /* AR(2) common component (Eq. 13) */
    double ac1, ac2;            /* AR coefficients: 1.23, -0.3995           */
    double sigma_c;             /* noise std dev: sqrt(11.3) mg/dl          */
    /* internal state */
    double v1, v2;              /* AR(2) noise history                      */
    double c1, c2;              /* AR(2) common component history           */
    double t_calib_days;        /* time since last calibration [days]       */
    unsigned int rng_state;     /* simple LCG seed                          */
} FacchinetttiState;

void       facchinetti_init(FacchinetttiState *s, unsigned int seed);
CGMReading facchinetti_update(FacchinetttiState *s, double gp_mg_dl, double dt);

/* Reset calibration time to zero (simulates a recalibration event). */
void facchinetti_recalibrate(FacchinetttiState *s);

#endif /* CGMSIM_SENSORS_H */
