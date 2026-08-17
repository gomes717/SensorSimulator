#ifndef CGMSIM_UVA_PADOVA_H
#define CGMSIM_UVA_PADOVA_H

/*
 * UVA/Padova Type-1 Diabetes Metabolic Simulator (T1DMS).
 * 13 core state variables (glucagon subsystem omitted).
 *
 * Reference: Dalla Man et al., "The UVA/PADOVA type 1 diabetes simulator",
 * J. Diabetes Sci. Technol. 2014; Visentin et al. 2015.
 */

/* ── State ────────────────────────────────────────────────────────────── */
typedef struct {
    double Gp;          /* plasma glucose                         [mg/kg]    */
    double Gt;          /* tissue glucose                         [mg/kg]    */
    double Gs;          /* subcutaneous glucose                   [mg/kg]    */
    double Ip;          /* plasma insulin                         [pmol/kg]  */
    double Il;          /* liver insulin                          [pmol/kg]  */
    double Qsto1;       /* stomach glucose, solid phase           [mg]       */
    double Qsto2;       /* stomach glucose, liquid phase          [mg]       */
    double Qgut;        /* intestinal glucose                     [mg]       */
    double XL;          /* insulin delay compartment 2            [pmol/l]   */
    double I_;          /* insulin delay compartment 1            [pmol/l]   */
    double X;           /* insulin in interstitial fluid          [pmol/l]   */
    double Isc1;        /* subcutaneous insulin, cpt 1            [pmol/kg]  */
    double Isc2;        /* subcutaneous insulin, cpt 2            [pmol/kg]  */
    double MealMemory;  /* total meal amount for kempt            [mg]       */
} UvaPadovaState;

/* ── Parameters ───────────────────────────────────────────────────────── */
typedef struct {
    double BW;          /* body weight                [kg]               */
    double VG;          /* glucose distribution vol   [dl/kg]            */
    double VI;          /* insulin distribution vol   [l/kg]             */
    double k1;          /* Gp→Gt transfer rate        [1/min]            */
    double k2;          /* Gt→Gp transfer rate        [1/min]            */
    double m1;          /* Il→Ip transfer rate        [1/min]            */
    double m2;          /* Ip→Il transfer rate        [1/min]            */
    double m4;          /* Ip clearance rate          [1/min]            */
    double kmin;        /* min gastric emptying rate  [1/min]            */
    double kmax;        /* max gastric emptying rate  [1/min]            */
    double kgri;        /* stomach grinding rate      [1/min]            */
    double kabs;        /* intestinal absorption rate [1/min]            */
    double ki;          /* insulin action delay rate  [1/min]            */
    double Fcns;        /* brain+RBC glucose uptake   [mg/kg/min]        */
    double Vm0;         /* Michaelis-Menten offset    [mg/kg/min]        */
    double Vmx;         /* Michaelis-Menten slope     [mg/kg/min·pmol/l] */
    double Km0;         /* Michaelis-Menten constant  [mg/kg]            */
    double p2u;         /* peripheral insulin action  [1/min]            */
    double kp1;         /* EGP baseline               [mg/kg/min]        */
    double kp2;         /* EGP glucose feedback       [1/min]            */
    double kp3;         /* EGP insulin feedback       [mg/kg/min·pmol/l] */
    double ke1;         /* glomerular filtration      [1/min]            */
    double ke2;         /* renal glucose threshold    [mg/kg]            */
    double ka1;         /* insulin absorption, slow   [1/min]            */
    double ka2;         /* insulin absorption, fast   [1/min]            */
    double kd;          /* insulin dissociation       [1/min]            */
    double Td;          /* subcutaneous glucose lag   [min]              */
    double bmeal;       /* gastric emptying shape b   [-]                */
    double cmeal;       /* gastric emptying shape c   [-]                */
    double f;           /* intestinal absorption frac [-]                */
    double HEeq;        /* equilibrium hepatic extr.  [-]                */
    double Gpeq;        /* target fasting glucose     [mg/dl]            */
    double Ib;          /* basal plasma insulin       [pmol/l]           */
} UvaPadovaParams;

/* ── API ──────────────────────────────────────────────────────────────── */

UvaPadovaParams uva_padova_default_params(void);

/* Initialize state to approximate steady state at params.Gpeq. */
void uva_padova_init(UvaPadovaState *s, const UvaPadovaParams *p);

/* Advance by dt minutes.
 *   carbs_g_per_min : meal input rate [g/min]
 *   iir_u_per_min   : insulin infusion [U/min] */
void uva_padova_step(UvaPadovaState *s, const UvaPadovaParams *p,
                     double carbs_g_per_min, double iir_u_per_min, double dt);

/* Plasma glucose [mg/dl] from current state. */
double uva_padova_glucose_mg_dl(const UvaPadovaState *s,
                                const UvaPadovaParams *p);

/* Constant basal insulin infusion [U/h] that keeps plasma insulin at the
 * steady state uva_padova_init assumes (Ip = p->Ib * p->VI). */
double uva_padova_basal_iir_u_per_h(const UvaPadovaParams *p);

#endif /* CGMSIM_UVA_PADOVA_H */
