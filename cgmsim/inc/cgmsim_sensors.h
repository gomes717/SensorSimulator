#ifndef CGMSIM_SENSORS_H
#define CGMSIM_SENSORS_H

/*
 * CGM / SMBG sensor models.
 *
 * All sensors receive the true plasma glucose [mg/dl] every simulation
 * step and return a CGMReading with (value_mg_dl, valid).
 * valid == 1 only when the sensor actually emits a measurement.
 */

#include "cgmsim_types.h"

/* ── Ideal CGM ─────────────────────────────────────────────────────────
 * Noiseless, no lag. Outputs Gp exactly every sampling_time_min.       */

typedef struct {
    double sampling_time_min;   /* default 5 min */
    double t_since_sample;      /* internal timer */
} IdealCGMState;

void     ideal_cgm_init(IdealCGMState *s, double sampling_time_min);
CGMReading ideal_cgm_update(IdealCGMState *s, double gp_mg_dl, double dt);

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
    double sampling_time_min;   /* output interval,   default 5 min       */
    /* internal */
    double noise;               /* current noise state                    */
    double t_since_sample;      /* timer for output throttle              */
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
    double sampling_time_min;   /* output interval, default 5 min           */
    /* internal state */
    double v1, v2;              /* AR(2) noise history                      */
    double c1, c2;              /* AR(2) common component history           */
    double t_calib_days;        /* time since last calibration [days]       */
    double t_since_sample;      /* timer for output throttle                */
    unsigned int rng_state;     /* simple LCG seed                          */
} FacchinetttiState;

void       facchinetti_init(FacchinetttiState *s, unsigned int seed);
CGMReading facchinetti_update(FacchinetttiState *s, double gp_mg_dl, double dt);

/* Reset calibration time to zero (simulates a recalibration event). */
void facchinetti_recalibrate(FacchinetttiState *s);

#endif /* CGMSIM_SENSORS_H */
