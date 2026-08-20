#ifndef CGMSIM_CAMBRIDGE_H
#define CGMSIM_CAMBRIDGE_H

/*
 * Cambridge (Hovorka) glucose-insulin model for T1D.
 * 10 state variables, Euler integration.
 *
 * Reference: Hovorka et al., "Nonlinear model predictive control of glucose
 * concentration in subjects with type 1 diabetes", Physiol. Meas. 2004.
 *
 * Ported unchanged from SensorSimulator/cgmsim/inc/cgmsim_cambridge.h — see
 * that repo's PROTOCOL_SPEC.md for the BLE wire format that feeds this
 * model's params.
 */

/* ── State ────────────────────────────────────────────────────────────── */
typedef struct {
    double Q1;          /* glucose mass, accessible compartment  [mmol]    */
    double Q2;          /* glucose mass, non-accessible compartment [mmol] */
    double S1;          /* subcutaneous insulin, cpt 1           [mU]      */
    double S2;          /* subcutaneous insulin, cpt 2           [mU]      */
    double I;           /* plasma insulin concentration          [mU/l]    */
    double x1;          /* insulin action on glucose transport   [1/min]   */
    double x2;          /* insulin action on glucose disposal    [1/min]   */
    double x3;          /* insulin action on EGP suppression     [-]       */
    double D1;          /* meal glucose, absorption cpt 1        [mmol]    */
    double D2;          /* meal glucose, absorption cpt 2        [mmol]    */
} CambridgeState;

/* ── Parameters ───────────────────────────────────────────────────────── */
typedef struct {
    double BW;          /* body weight                           [kg]          */
    double VG;          /* glucose distribution volume           [l/kg]        */
    double VI;          /* insulin distribution volume           [l/kg]        */
    double k12;         /* glucose inter-compartment rate        [1/min]       */
    double ka1;         /* x1 deactivation rate                  [1/min]       */
    double ka2;         /* x2 deactivation rate                  [1/min]       */
    double ka3;         /* x3 deactivation rate                  [1/min]       */
    double SIT;         /* insulin sensitivity (transport)       [1/min·mU/l]  */
    double SID;         /* insulin sensitivity (disposal)        [1/min·mU/l]  */
    double SIE;         /* insulin sensitivity (EGP)             [1/mU/l]      */
    double ke;          /* insulin elimination                   [1/min]       */
    double tmaxI;       /* insulin absorption time constant      [min]         */
    double tmaxG;       /* meal glucose absorption time constant [min]         */
    double AG;          /* carbohydrate bioavailability          [-]           */
    double EGP0;        /* endogenous glucose production rate    [mmol/kg/min] */
    double F01;         /* non-insulin-dependent glucose flux    [mmol/kg/min] */
    double Gpeq;        /* target fasting glucose                [mg/dl]       */
} CambridgeParams;

/* ── API ──────────────────────────────────────────────────────────────── */

/* Fill p with literature defaults. */
CambridgeParams cambridge_default_params(void);

/* Set s to an approximate steady-state for the given p. */
void cambridge_init(CambridgeState *s, const CambridgeParams *p);

/* Advance simulation by dt minutes.
 *   carbs_g_per_min : meal input rate  [g/min]   (0 if no meal this step)
 *   iir_u_per_h     : insulin infusion [U/h]     */
void cambridge_step(CambridgeState *s, const CambridgeParams *p,
                    double carbs_g_per_min, double iir_u_per_h, double dt);

/* Return plasma glucose [mg/dl] from current state. */
double cambridge_glucose_mg_dl(const CambridgeState *s, const CambridgeParams *p);

/* Constant basal insulin infusion [U/h] that keeps glucose at p->Gpeq
 * indefinitely (the same rate cambridge_init's steady state assumes). */
double cambridge_basal_iir_u_per_h(const CambridgeParams *p);

#endif /* CGMSIM_CAMBRIDGE_H */
