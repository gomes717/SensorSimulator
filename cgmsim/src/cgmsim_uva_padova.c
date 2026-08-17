#include <math.h>
#include "../inc/cgmsim_uva_padova.h"

UvaPadovaParams uva_padova_default_params(void) {
    UvaPadovaParams p;
    p.BW    = 75.0;
    p.VG    = 1.88;
    p.VI    = 0.05;
    p.k1    = 0.065;
    p.k2    = 0.079;
    p.m1    = 0.190;
    p.m2    = 0.484;
    p.m4    = 0.194;
    p.kmin  = 0.0080;
    p.kmax  = 0.0558;
    p.kgri  = 0.0558;
    p.kabs  = 0.057;
    p.ki    = 0.0079;
    p.Fcns  = 1.0;
    p.Vm0   = 2.5;
    p.Vmx   = 0.047;
    p.Km0   = 225.59;
    p.p2u   = 0.0331;
    p.kp1   = 2.7;
    p.kp2   = 0.0021;
    p.kp3   = 0.009;
    p.ke1   = 0.0005;
    p.ke2   = 339.0;
    p.ka1   = 0.0018;
    p.ka2   = 0.0182;
    p.kd    = 0.0164;
    p.Td    = 10.0;
    p.bmeal = 0.69;
    p.cmeal = 0.17;
    p.f     = 0.90;
    p.HEeq  = 0.6;
    p.Gpeq  = 100.0;
    p.Ib    = 25.0;   /* pmol/l, approximate basal */
    return p;
}

/* Gastric emptying rate [1/min] — depends on stomach contents and meal size */
static double kempt(double Qsto, double D, const UvaPadovaParams *p) {
    double D_  = (D > 1.0) ? D : 1.0;  /* avoid /0 */
    double b   = p->bmeal;
    double c   = p->cmeal;
    double a   = 5.0 / (2.0 * D_ * (1.0 - b));
    double bt  = 5.0 / (2.0 * D_ * c);
    double arg1 = a  * (Qsto - b * D_);
    double arg2 = bt * (Qsto - c * D_);
    /* clamp tanh args to avoid overflow */
    if (arg1 >  20.0) arg1 =  20.0;
    if (arg1 < -20.0) arg1 = -20.0;
    if (arg2 >  20.0) arg2 =  20.0;
    if (arg2 < -20.0) arg2 = -20.0;
    return p->kmin + 0.5 * (p->kmax - p->kmin)
           * (tanh(arg1) - tanh(arg2) + 2.0);
}

void uva_padova_init(UvaPadovaState *s, const UvaPadovaParams *p) {
    double Gp0 = p->Gpeq * p->VG;  /* mg/kg */
    double Gt0 = p->k1 * Gp0 / p->k2;
    /* Hepatic extraction equilibrium: m3eq = HEeq*m1/(1-HEeq) */
    double m3eq = p->HEeq * p->m1 / (1.0 - p->HEeq);
    /* At steady state with zero exogenous insulin, use basal Ib [pmol/l] */
    double Ip0 = p->Ib * p->VI;  /* pmol/kg */
    double Il0 = p->m2 * Ip0 / (p->m1 + m3eq);

    s->Gp   = Gp0;
    s->Gt   = Gt0;
    s->Gs   = Gp0;
    s->Ip   = Ip0;
    s->Il   = Il0;
    s->Qsto1 = 0.0;
    s->Qsto2 = 0.0;
    s->Qgut  = 0.0;
    s->XL    = p->Ib;
    s->I_    = p->Ib;
    s->X     = 0.0;
    s->Isc1  = 0.0;
    s->Isc2  = 0.0;
    s->MealMemory = 1.0;  /* must be > 0 to avoid kempt /0 */
}

void uva_padova_step(UvaPadovaState *s, const UvaPadovaParams *p,
                     double carbs_g_per_min, double iir_u_per_min, double dt) {
    /* Unit conversions */
    double M   = carbs_g_per_min * 1000.0;        /* mg/min  */
    double IIR = iir_u_per_min * 6000.0 / p->BW;  /* pmol/kg/min (6000 pmol/U) */

    /* Update meal memory */
    if (M > 0.0) s->MealMemory = (s->Qsto1 + s->Qsto2) + M * dt;

    /* Gastric emptying */
    double Qsto  = s->Qsto1 + s->Qsto2;
    double ke    = kempt(Qsto, s->MealMemory, p);

    /* Plasma insulin & concentration */
    double I     = s->Ip / p->VI;                  /* pmol/l */
    double m3eq  = p->HEeq * p->m1 / (1.0 - p->HEeq);

    /* Glucose appearance */
    double Ra    = p->f * p->kabs * s->Qgut / p->BW;   /* mg/kg/min */
    /* Subcutaneous insulin appearance */
    double Rai   = p->ka1 * s->Isc1 + p->ka2 * s->Isc2;

    /* Utilization (Michaelis-Menten) */
    double Uid   = (p->Vm0 + p->Vmx * s->X) * s->Gt / (p->Km0 + s->Gt);
    double Uii   = p->Fcns;
    /* Renal excretion */
    double E     = (p->ke1 * (s->Gp - p->ke2) > 0.0)
                   ? p->ke1 * (s->Gp - p->ke2) : 0.0;
    /* EGP */
    double EGP   = p->kp1 - p->kp2 * s->Gp - p->kp3 * s->XL;
    if (EGP < 0.0) EGP = 0.0;

    /* ── ODEs ───────────────────────────────────────────────── */
    double dGp   = EGP + Ra - Uii - E - p->k1 * s->Gp + p->k2 * s->Gt;
    double dGt   = -Uid + p->k1 * s->Gp - p->k2 * s->Gt;
    double dGs   = (s->Gp - s->Gs) / p->Td;

    double dIp   = -(p->m2 + p->m4) * s->Ip + p->m1 * s->Il + Rai;
    double dIl   = p->m2 * s->Ip - (p->m1 + m3eq) * s->Il;

    double dQsto1 = -p->kgri * s->Qsto1 + M;
    double dQsto2 = -ke     * s->Qsto2 + p->kgri * s->Qsto1;
    double dQgut  = -p->kabs * s->Qgut + ke * s->Qsto2;

    double dXL   = -p->ki * (s->XL - s->I_);
    double dI_   = -p->ki * (s->I_  - I);
    double dX    = -p->p2u * s->X + p->p2u * (I - p->Ib);

    double dIsc1 = -(p->kd + p->ka1) * s->Isc1 + IIR;
    double dIsc2 = p->kd * s->Isc1 - p->ka2 * s->Isc2;

    /* ── Euler update ──────────────────────────────────────── */
    s->Gp    += dGp    * dt;  if (s->Gp    < 0.0) s->Gp    = 0.0;
    s->Gt    += dGt    * dt;  if (s->Gt    < 0.0) s->Gt    = 0.0;
    s->Gs    += dGs    * dt;  if (s->Gs    < 0.0) s->Gs    = 0.0;
    s->Ip    += dIp    * dt;  if (s->Ip    < 0.0) s->Ip    = 0.0;
    s->Il    += dIl    * dt;  if (s->Il    < 0.0) s->Il    = 0.0;
    s->Qsto1 += dQsto1 * dt;  if (s->Qsto1 < 0.0) s->Qsto1 = 0.0;
    s->Qsto2 += dQsto2 * dt;  if (s->Qsto2 < 0.0) s->Qsto2 = 0.0;
    s->Qgut  += dQgut  * dt;  if (s->Qgut  < 0.0) s->Qgut  = 0.0;
    s->XL    += dXL    * dt;
    s->I_    += dI_    * dt;
    s->X     += dX     * dt;
    s->Isc1  += dIsc1  * dt;  if (s->Isc1  < 0.0) s->Isc1  = 0.0;
    s->Isc2  += dIsc2  * dt;  if (s->Isc2  < 0.0) s->Isc2  = 0.0;
}

double uva_padova_glucose_mg_dl(const UvaPadovaState *s, const UvaPadovaParams *p) {
    return s->Gp / p->VG;
}

double uva_padova_basal_iir_u_per_h(const UvaPadovaParams *p) {
    /* At steady state Rai_ss = IIR (mass balance through Isc1/Isc2), and
     * dIp/dt = 0, dIl/dt = 0 with Ip_ss = p->Ib * p->VI pin down the
     * exogenous insulin appearance rate that holds plasma insulin there. */
    double m3eq  = p->HEeq * p->m1 / (1.0 - p->HEeq);
    double Ip_ss = p->Ib * p->VI;
    double iir_pmol_kg_min = Ip_ss * ((p->m2 + p->m4) - p->m1 * p->m2 / (p->m1 + m3eq));
    return iir_pmol_kg_min * p->BW / 100.0; /* pmol/kg/min -> U/h */
}
