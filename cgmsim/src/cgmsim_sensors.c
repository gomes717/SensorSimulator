#include <math.h>
#include "../inc/cgmsim_sensors.h"

/* ── Minimal Gaussian RNG (Box-Muller, LCG state) ──────────────────────── */
static double lcg_randn(unsigned int *state) {
    /* Two uniform samples via LCG */
    *state = *state * 1664525u + 1013904223u;
    double u1 = (*state / (double)0xFFFFFFFFu);
    *state = *state * 1664525u + 1013904223u;
    double u2 = (*state / (double)0xFFFFFFFFu);
    if (u1 < 1e-10) u1 = 1e-10;
    return sqrt(-2.0 * log(u1)) * cos(2.0 * 3.14159265358979323846 * u2);
}

/* ══════════════════════════════════════════════════════════════════════════
 * Ideal CGM — no noise, no lag
 * ══════════════════════════════════════════════════════════════════════════ */

void ideal_cgm_init(IdealCGMState *s, double sampling_time_min) {
    s->sampling_time_min = sampling_time_min > 0.0 ? sampling_time_min : 5.0;
    s->t_since_sample    = s->sampling_time_min; /* trigger immediately */
}

CGMReading ideal_cgm_update(IdealCGMState *s, double gp_mg_dl, double dt) {
    CGMReading r;
    s->t_since_sample += dt;
    if (s->t_since_sample >= s->sampling_time_min) {
        s->t_since_sample = 0.0;
        r.value_mg_dl = gp_mg_dl;
        r.valid       = 1;
    } else {
        r.value_mg_dl = 0.0;
        r.valid       = 0;
    }
    return r;
}

/* ══════════════════════════════════════════════════════════════════════════
 * Ideal SMBG — noiseless point measurement on demand
 * ══════════════════════════════════════════════════════════════════════════ */

CGMReading ideal_smbg_measure(double gp_mg_dl) {
    CGMReading r;
    r.value_mg_dl = gp_mg_dl;
    r.valid       = 1;
    return r;
}

/* ══════════════════════════════════════════════════════════════════════════
 * Breton & Kovatchev 2008 — AR(1) noise + linear calibration
 * ══════════════════════════════════════════════════════════════════════════ */

void breton_init(BretonState *s, unsigned int seed) {
    s->pacf              = 0.70;
    s->sigma             = 1.5;    /* mg/dl */
    s->alpha             = 1.0;
    s->beta              = 0.0;
    s->sampling_time_min = 5.0;
    s->noise             = 0.0;
    s->t_since_sample    = s->sampling_time_min;
    s->rng_state         = seed ^ 0xDEADBEEFu;
}

CGMReading breton_update(BretonState *s, double gp_mg_dl, double dt) {
    /* Update AR(1) noise every call (typically 1-min steps) */
    double w    = lcg_randn(&s->rng_state);
    s->noise    = s->pacf * s->noise + s->sigma * w;

    CGMReading r;
    s->t_since_sample += dt;
    if (s->t_since_sample >= s->sampling_time_min) {
        s->t_since_sample = 0.0;
        double raw        = s->alpha * gp_mg_dl + s->beta + s->noise;
        if (raw < 0.0)    raw = 0.0;
        if (raw > 1000.0) raw = 1000.0;
        r.value_mg_dl = raw;
        r.valid       = 1;
    } else {
        r.value_mg_dl = 0.0;
        r.valid       = 0;
    }
    return r;
}

/* ══════════════════════════════════════════════════════════════════════════
 * Facchinetti et al. 2014 — AR(2) noise + time-varying calibration
 * ══════════════════════════════════════════════════════════════════════════ */

void facchinetti_init(FacchinetttiState *s, unsigned int seed) {
    /* Calibration polynomial coefficients */
    s->a0 = 1.1;    s->a1 = 2e-4;  s->a2 = 0.0;
    s->b0 = -14.8;  s->b1 = 0.04;  s->b2 = 0.0;
    /* AR(2) measurement noise */
    s->aw1 = 1.013;   s->aw2 = -0.2135;  s->sigma_v = sqrt(14.45);
    /* AR(2) common component */
    s->ac1 = 1.23;    s->ac2 = -0.3995;  s->sigma_c = sqrt(11.3);
    s->sampling_time_min = 5.0;
    s->v1 = 0.0;  s->v2 = 0.0;
    s->c1 = 0.0;  s->c2 = 0.0;
    s->t_calib_days   = 0.0;
    s->t_since_sample = s->sampling_time_min;
    s->rng_state      = seed ^ 0xCAFEBABEu;
}

void facchinetti_recalibrate(FacchinetttiState *s) {
    s->t_calib_days = 0.0;
}

CGMReading facchinetti_update(FacchinetttiState *s, double gp_mg_dl, double dt) {
    /* Advance calibration clock */
    s->t_calib_days += dt / 1440.0;  /* min → days */

    /* Time-varying calibration */
    double dt_d = s->t_calib_days;
    double a    = s->a0 + s->a1 * dt_d + s->a2 * dt_d * dt_d;
    double b    = s->b0 + s->b1 * dt_d + s->b2 * dt_d * dt_d;

    /* AR(2) measurement noise */
    double wv  = lcg_randn(&s->rng_state);
    double v_new = s->aw1 * s->v1 + s->aw2 * s->v2 + s->sigma_v * wv;

    /* AR(2) common component */
    double wc  = lcg_randn(&s->rng_state);
    double c_new = s->ac1 * s->c1 + s->ac2 * s->c2 + s->sigma_c * wc;

    double noise = v_new + c_new;

    /* Shift noise history */
    s->v2 = s->v1;  s->v1 = v_new;
    s->c2 = s->c1;  s->c1 = c_new;

    CGMReading r;
    s->t_since_sample += dt;
    if (s->t_since_sample >= s->sampling_time_min) {
        s->t_since_sample = 0.0;
        double raw = a * gp_mg_dl + b + noise;
        if (raw < 0.0)    raw = 0.0;
        if (raw > 1000.0) raw = 1000.0;
        r.value_mg_dl = raw;
        r.valid       = 1;
    } else {
        r.value_mg_dl = 0.0;
        r.valid       = 0;
    }
    return r;
}
