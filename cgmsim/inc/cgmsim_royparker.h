#ifndef CGMSIM_ROYPARKER_H
#define CGMSIM_ROYPARKER_H

/*
 * Roy & Parker 2007 – Extended Bergman minimal model with exercise
 * and a two-compartment meal model.
 *
 * Reference: Roy A, Parker RS. "Dynamic modeling of exercise effects on
 * plasma glucose and insulin levels", J. Diabetes Sci. Technol. 2007.
 */

/* ── State ────────────────────────────────────────────────────────────── */
typedef struct {
    double NG;          /* meal glucose in gut             [g]          */
    double PVO2max;     /* normalised exercise intensity   [%]          */
    double I;           /* plasma insulin                  [µU/ml]      */
    double X;           /* remote insulin effect           [1/min]      */
    double G;           /* plasma glucose                  [mg/dl]      */
    double Gprod;       /* hepatic glucose production      [mg/kg/min]  */
    double Gup;         /* exercise glucose uptake         [mg/kg/min]  */
    double Ie;          /* exercise insulin removal        [µU/ml/min]  */
    double Ggly;        /* glycogenolysis rate             [mg/kg/min]  */
    /* meal bookkeeping (non-ODE) */
    double meal_carbs_g;       /* last meal size [g] */
    double meal_start_min;     /* simulation time of last meal [min] */
} RoyParkerState;

/* ── Parameters ───────────────────────────────────────────────────────── */
typedef struct {
    double Gpeq;    /* equilibrium glucose         [mg/dl]           */
    double BW;      /* body weight                 [kg]              */
    double VolG;    /* glucose distribution vol    [dl]              */
    double Ib;      /* basal plasma insulin        [µU/ml]           */
    double u1b;     /* basal insulin infusion      [U/h]             */
    double p1;      /* glucose effectiveness       [1/min]           */
    double p2;      /* insulin action dynamics     [1/min]           */
    double p3;      /* insulin sensitivity         [1/min²·µU/ml]   */
    double p4;      /* insulin dynamics coeff      [-]               */
    double n;       /* insulin elimination rate    [1/min]           */
    double a1, a2;  /* exercise: hepatic prod      [-]               */
    double a3, a4;  /* exercise: glucose uptake    [-]               */
    double a5, a6;  /* exercise: insulin removal   [-]               */
    double k;       /* glycogenolysis increase     [-]               */
    double T1;      /* glycogenolysis decay const  [min]             */
    double kG;      /* gut glucose absorption rate [1/min]           */
    double Tasc;    /* meal ascent phase           [min]             */
    double Tmax;    /* meal plateau phase          [min]             */
    double Tdes;    /* meal descent phase          [min]             */
} RoyParkerParams;

/* ── API ──────────────────────────────────────────────────────────────── */

RoyParkerParams royparker_default_params(void);

void royparker_init(RoyParkerState *s, const RoyParkerParams *p);

/* Advance by dt minutes.
 *   meal_g      : carbs ingested this step [g]   (0 if no meal)
 *   iir_u_per_h : insulin infusion rate  [U/h]
 *   exercise_pct: exercise intensity     [0-100 %VO2max]
 *   t_sim       : current simulation time [min] (needed for meal timing) */
void royparker_step(RoyParkerState *s, const RoyParkerParams *p,
                    double meal_g, double iir_u_per_h,
                    double exercise_pct, double t_sim, double dt);

double royparker_glucose_mg_dl(const RoyParkerState *s);

#endif /* CGMSIM_ROYPARKER_H */
