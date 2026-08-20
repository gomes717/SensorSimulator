#include <math.h>
#include "cgmsim_royparker.h"

RoyParkerParams royparker_default_params(void) {
    RoyParkerParams p;
    p.Gpeq  = 100.0;
    p.BW    = 70.0;
    p.VolG  = 117.0;  /* dl */
    p.Ib    = 11.0;   /* µU/ml, typical fasting value */
    p.u1b   = 1.0;    /* U/h basal */
    p.p1    = 0.035;
    p.p2    = 0.050;
    p.p3    = 0.000028;
    p.p4    = 9.8e-5;
    p.n     = 0.142;
    p.a1    = 0.00158;
    p.a2    = 0.056;
    p.a3    = 0.00195;
    p.a4    = 0.0485;
    p.a5    = 0.00125;
    p.a6    = 0.075;
    p.k     = 0.0108;
    p.T1    = 6.0;
    p.kG    = 0.022;
    p.Tasc  = 10.0;
    p.Tmax  = 35.0;
    p.Tdes  = 10.0;
    return p;
}

void royparker_init(RoyParkerState *s, const RoyParkerParams *p) {
    /* Steady state with no exercise: Gprod=Gup=Ie=Ggly=0 */
    double IIR_uU_min = p->u1b * 1e6 / 60.0; /* µU/min */
    s->I         = p->p4 * IIR_uU_min / p->n;
    s->X         = p->p3 * (s->I - p->Ib) / p->p2;
    if (s->X < 0.0) s->X = 0.0;
    s->G         = p->Gpeq;
    s->NG        = 0.0;
    s->PVO2max   = 0.0;
    s->Gprod     = 0.0;
    s->Gup       = 0.0;
    s->Ie        = 0.0;
    s->Ggly      = 0.0;
    s->meal_carbs_g   = 0.0;
    s->meal_start_min = -1e9;
}

/* Trapezoidal gastric emptying rate [g/min] for a meal that started at meal_start_min */
static double gastric_rate(const RoyParkerState *s, const RoyParkerParams *p, double t_sim) {
    if (s->meal_carbs_g <= 0.0) return 0.0;
    double elapsed = t_sim - s->meal_start_min;
    double total   = p->Tasc + p->Tmax + p->Tdes;
    if (elapsed < 0.0 || elapsed > total) return 0.0;

    double area = 0.5 * p->Tasc + p->Tmax + 0.5 * p->Tdes;
    double peak = s->meal_carbs_g / area;

    if (elapsed <= p->Tasc)
        return peak * elapsed / p->Tasc;
    else if (elapsed <= p->Tasc + p->Tmax)
        return peak;
    else
        return peak * (1.0 - (elapsed - p->Tasc - p->Tmax) / p->Tdes);
}

void royparker_step(RoyParkerState *s, const RoyParkerParams *p,
                    double meal_g, double iir_u_per_h,
                    double exercise_pct, double t_sim, double dt) {
    /* Register a new meal (replaces any ongoing meal) */
    if (meal_g > 0.0) {
        s->meal_carbs_g   = meal_g;
        s->meal_start_min = t_sim;
    }

    double IIR_uU_min = iir_u_per_h * 1e6 / 60.0;
    double Gemp = gastric_rate(s, p, t_sim); /* g/min */

    /* ODEs */
    double dNG      = Gemp - p->kG * s->NG;
    double dPVO2max = -0.8 * s->PVO2max + 0.8 * exercise_pct;
    double dI       = -p->n * s->I + p->p4 * IIR_uU_min - s->Ie;
    double dX       = -p->p2 * s->X + p->p3 * (s->I - p->Ib);
    double dGprod   = p->a1 * s->PVO2max - p->a2 * s->Gprod;
    double dGup     = p->a3 * s->PVO2max - p->a4 * s->Gup;
    double dIe      = p->a5 * s->PVO2max - p->a6 * s->Ie;

    /* Glycogenolysis: rises during exercise, decays during recovery */
    double dGgly = (exercise_pct > 0.0)
                   ? p->k * s->PVO2max
                   : -s->Ggly / p->T1;

    /* Gut glucose absorption into blood [mg/dl/min] */
    double u2 = p->kG * s->NG * 1000.0; /* g/min -> mg/min */

    double dG = -p->p1 * (s->G - p->Gpeq)
                - s->X * s->G
                + (p->BW / p->VolG) * (s->Gprod - s->Ggly - s->Gup)
                + u2 / p->VolG;

    s->NG      += dNG      * dt;  if (s->NG     < 0.0) s->NG     = 0.0;
    s->PVO2max += dPVO2max * dt;  if (s->PVO2max< 0.0) s->PVO2max= 0.0;
    s->I       += dI       * dt;  if (s->I      < 0.0) s->I      = 0.0;
    s->X       += dX       * dt;
    s->G       += dG       * dt;  if (s->G      < 0.0) s->G      = 0.0;
    s->Gprod   += dGprod   * dt;  if (s->Gprod  < 0.0) s->Gprod  = 0.0;
    s->Gup     += dGup     * dt;  if (s->Gup    < 0.0) s->Gup    = 0.0;
    s->Ie      += dIe      * dt;  if (s->Ie     < 0.0) s->Ie     = 0.0;
    s->Ggly    += dGgly    * dt;  if (s->Ggly   < 0.0) s->Ggly   = 0.0;
}

double royparker_glucose_mg_dl(const RoyParkerState *s) {
    return s->G;
}
