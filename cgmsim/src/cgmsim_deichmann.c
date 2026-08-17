#include <math.h>
#include "../inc/cgmsim_deichmann.h"

DeichmannParams deichmann_default_params(void) {
    DeichmannParams p;
    p.Gpeq  = 100.0;
    p.BW    = 70.0;
    p.Gb    = 172.0;  /* basal glucose from paper */
    p.Ib    = 10.0;   /* basal insulin [µU/ml] */
    p.HRb   = 80.0;
    p.p1    = 0.0041;
    p.p2    = 0.0155;
    p.p3    = 6.913e-6;
    p.alpha = 2.59e-4;
    p.beta  = 3.39e-4;
    p.tauHR = 5.0;
    p.tau   = 600.0;
    p.f     = 0.1;
    p.AG    = 0.8;
    p.Vg    = 1.6;    /* dl/kg */
    p.tau_m = 60.0;
    p.k21   = 0.0085;
    p.kd    = 0.0247;
    p.ka    = 0.011;
    p.ke    = 0.0357;
    p.Vi    = 0.104;  /* L/kg  (104 ml/kg converted) */
    p.IIRb  = 0.5;    /* basal infusion [U/h] */
    return p;
}

void deichmann_init(DeichmannState *s, const DeichmannParams *p) {
    /* Subcutaneous insulin compartments at steady state.
     * Units: IIR in mU/min, x1/x2 in mU, Vi in L/kg → Ic in mU/L = µU/ml */
    double IIR_mU_min = p->IIRb * 1000.0 / 60.0;
    double x1_ss = IIR_mU_min / p->k21;
    double x2_ss = p->k21 * x1_ss / (p->kd + p->ka);
    double Ic_ss = p->ka * x2_ss / (p->ke * p->Vi * p->BW);

    s->x1    = x1_ss;
    s->x2    = x2_ss;
    s->Ic    = Ic_ss;
    s->D1    = 0.0;
    s->D2    = 0.0;
    s->X     = p->p3 * (Ic_ss - p->Ib) / p->p2; /* SS insulin action */
    s->G     = p->Gpeq;
    s->Y     = 0.0;
    s->Z     = 0.0;
    s->HRint = 0.0;
}

void deichmann_step(DeichmannState *s, const DeichmannParams *p,
                    double carbs_g, double iir_u_per_h,
                    double hr_bpm, double dt) {
    /* mU/min; Vi in L/kg keeps Ic in mU/L = µU/ml */
    double IIR_mU_min = iir_u_per_h * 1000.0 / 60.0;

    /* Subcutaneous insulin PK — total IIR drives the chain */
    double dx1 = -p->k21 * s->x1 + IIR_mU_min;
    double dx2 = p->k21 * s->x1 - (p->kd + p->ka) * s->x2;
    double dIc = p->ka / (p->Vi * p->BW) * s->x2 - p->ke * s->Ic;

    /* Meal absorption (bilinear, single-compartment approximation) */
    double M   = carbs_g / dt;                     /* g/min this step */
    double dD1 = -s->D1 / p->tau_m + M * p->AG;
    double dD2 = (s->D1 - s->D2) / p->tau_m;

    /* Insulin action */
    double dI  = s->Ic - p->Ib;
    double dX  = -p->p2 * s->X + p->p3 * dI;

    /* Glucose appearance from gut [mg/dl/min] */
    double Ra = s->D2 / p->tau_m * 1000.0 / (p->Vg * p->BW);

    /* Exercise states */
    double HR_dev = hr_bpm - p->HRb;
    double dY     = (HR_dev - s->Y) / p->tauHR;
    double dZ     = -(p->f + 1.0 / p->tau) * s->Z + p->f;
    double dHRint = HR_dev;

    /* Basal insulin action offset */
    double Xb = p->p3 * p->Ib / p->p2;

    /* Glucose ODE */
    double dG = -p->p1 * (s->G - p->Gb)
                - s->X * s->G
                - p->alpha * s->HRint * s->Z * (s->X + Xb) * s->G
                - p->beta  * s->Y * s->G
                + Ra;

    s->x1    += dx1    * dt;  if (s->x1  < 0.0) s->x1  = 0.0;
    s->x2    += dx2    * dt;  if (s->x2  < 0.0) s->x2  = 0.0;
    s->Ic    += dIc    * dt;  if (s->Ic  < 0.0) s->Ic  = 0.0;
    s->D1    += dD1    * dt;  if (s->D1  < 0.0) s->D1  = 0.0;
    s->D2    += dD2    * dt;  if (s->D2  < 0.0) s->D2  = 0.0;
    s->X     += dX     * dt;
    s->G     += dG     * dt;  if (s->G   < 0.0) s->G   = 0.0;
    s->Y     += dY     * dt;
    s->Z     += dZ     * dt;  if (s->Z   < 0.0) s->Z   = 0.0;
    s->HRint += dHRint * dt;  if (s->HRint < 0.0) s->HRint = 0.0;
}

double deichmann_glucose_mg_dl(const DeichmannState *s) {
    return s->G;
}
