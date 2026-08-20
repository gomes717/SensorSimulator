#include <math.h>
#include "cgmsim_cambridge.h"

CambridgeParams cambridge_default_params(void) {
    CambridgeParams p;
    p.BW    = 75.0;
    p.VG    = 0.16;
    p.VI    = 0.12;
    p.k12   = 0.066;
    p.ka1   = 0.006;
    p.ka2   = 0.060;
    p.ka3   = 0.030;
    p.SIT   = 51.2e-4;
    p.SID   = 8.2e-4;
    p.SIE   = 520.0e-4;
    p.ke    = 0.138;
    p.tmaxI = 55.0;
    p.tmaxG = 40.0;
    p.AG    = 0.8;
    p.EGP0  = 0.0161;
    p.F01   = 0.0097;
    p.Gpeq  = 100.0;
    return p;
}

/* Residual of the Q1 steady-state equation as a function of plasma insulin I_ss.
 * f(I) = 0 when dQ1/dt = 0 at the target glucose. */
static double eq_residual(double I_ss, const CambridgeParams *p,
                           double Q1_ss, double F01c_ss, double FR_ss) {
    double x1  = p->SIT * I_ss;
    double x2  = p->SID * I_ss;
    double x3  = p->SIE * I_ss;
    double denom = p->k12 + x2;
    double egp = p->EGP0 * p->BW * (1.0 - x3);
    if (egp < 0.0) egp = 0.0;
    /* dQ1/dt = 0 rearranged, with Q2_ss = x1*Q1/(k12+x2) substituted */
    return -F01c_ss - x1 * Q1_ss * x2 / denom - FR_ss + egp;
}

void cambridge_init(CambridgeState *s, const CambridgeParams *p) {
    double G_ss    = p->Gpeq / 18.016;
    double Q1_ss   = G_ss * p->VG * p->BW;
    double F01c_ss = p->F01 * p->BW * (G_ss < 4.5 ? G_ss / 4.5 : 1.0);
    double FR_ss   = G_ss > 9.0 ? 0.003 * (G_ss - 9.0) : 0.0;

    /* Bisection: find I_ss in [0, 200] mU/l satisfying equilibrium */
    double lo = 0.0, hi = 200.0, mid = 0.0;
    for (int i = 0; i < 64; i++) {
        mid = 0.5 * (lo + hi);
        if (eq_residual(mid, p, Q1_ss, F01c_ss, FR_ss) > 0.0)
            lo = mid;
        else
            hi = mid;
    }

    double I_ss  = mid;
    double x1_ss = p->SIT * I_ss;
    double x2_ss = p->SID * I_ss;
    double x3_ss = p->SIE * I_ss;
    double IIR_b = I_ss * p->ke * p->VI * p->BW; /* basal IIR in mU/min */
    double Q2_ss = (p->k12 + x2_ss > 0.0)
                   ? x1_ss * Q1_ss / (p->k12 + x2_ss) : 0.0;

    s->Q1 = Q1_ss;
    s->Q2 = Q2_ss;
    s->S1 = IIR_b * p->tmaxI;
    s->S2 = IIR_b * p->tmaxI;
    s->I  = I_ss;
    s->x1 = x1_ss;
    s->x2 = x2_ss;
    s->x3 = x3_ss;
    s->D1 = 0.0;
    s->D2 = 0.0;
}

void cambridge_step(CambridgeState *s, const CambridgeParams *p,
                    double carbs_g_per_min, double iir_u_per_h, double dt) {
    double G    = s->Q1 / (p->VG * p->BW);
    double F01c = p->F01 * p->BW * (G < 4.5 ? G / 4.5 : 1.0);
    double FR   = G > 9.0 ? 0.003 * (G - 9.0) : 0.0;
    double UG   = s->D2 / p->tmaxG;
    double M    = carbs_g_per_min * 1000.0 / 180.16; /* mmol/min */
    double IIR  = iir_u_per_h * 1000.0 / 60.0;       /* mU/min   */
    double x3c  = s->x3 > 1.0 ? 1.0 : s->x3;         /* EGP clamped */

    double dQ1 = (-F01c - s->x1 * s->Q1 + p->k12 * s->Q2 - FR + UG
                  + p->EGP0 * p->BW * (1.0 - x3c));
    double dQ2 = s->x1 * s->Q1 - (p->k12 + s->x2) * s->Q2;
    double dS1 = -s->S1 / p->tmaxI + IIR;
    double dS2 = (s->S1 - s->S2) / p->tmaxI;
    double dI  = s->S2 / (p->tmaxI * p->VI * p->BW) - p->ke * s->I;
    double dx1 = p->ka1 * (p->SIT * s->I - s->x1);
    double dx2 = p->ka2 * (p->SID * s->I - s->x2);
    double dx3 = p->ka3 * (p->SIE * s->I - s->x3);
    double dD1 = -s->D1 / p->tmaxG + M * p->AG;
    double dD2 = (s->D1 - s->D2) / p->tmaxG;

    s->Q1 += dQ1 * dt;  if (s->Q1 < 0.0) s->Q1 = 0.0;
    s->Q2 += dQ2 * dt;  if (s->Q2 < 0.0) s->Q2 = 0.0;
    s->S1 += dS1 * dt;  if (s->S1 < 0.0) s->S1 = 0.0;
    s->S2 += dS2 * dt;  if (s->S2 < 0.0) s->S2 = 0.0;
    s->I  += dI  * dt;  if (s->I  < 0.0) s->I  = 0.0;
    s->x1 += dx1 * dt;  if (s->x1 < 0.0) s->x1 = 0.0;
    s->x2 += dx2 * dt;  if (s->x2 < 0.0) s->x2 = 0.0;
    s->x3 += dx3 * dt;  if (s->x3 < 0.0) s->x3 = 0.0;
    s->D1 += dD1 * dt;  if (s->D1 < 0.0) s->D1 = 0.0;
    s->D2 += dD2 * dt;  if (s->D2 < 0.0) s->D2 = 0.0;
}

double cambridge_glucose_mg_dl(const CambridgeState *s, const CambridgeParams *p) {
    return s->Q1 / (p->VG * p->BW) * 18.016;
}

double cambridge_basal_iir_u_per_h(const CambridgeParams *p) {
    CambridgeState s;
    cambridge_init(&s, p);
    return s.I * p->ke * p->VI * p->BW * 60.0 / 1000.0; /* mU/min -> U/h */
}
