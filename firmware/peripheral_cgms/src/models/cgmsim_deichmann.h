#ifndef CGMSIM_DEICHMANN_H
#define CGMSIM_DEICHMANN_H

/*
 * Deichmann 2021 – Exercise-augmented Bergman minimal model.
 * Heart-rate-driven exercise effect on insulin sensitivity and glucose uptake.
 *
 * Reference: Deichmann J et al. "Simulation environment for testing closed-loop
 * insulin delivery systems", J. Diabetes Sci. Technol. 2021.
 *
 * Ported unchanged from SensorSimulator/cgmsim/inc/cgmsim_deichmann.h.
 */

/* ── State ────────────────────────────────────────────────────────────── */
typedef struct {
    double x1;      /* subcutaneous insulin, cpt 1     [U]          */
    double x2;      /* subcutaneous insulin, cpt 2     [U]          */
    double Ic;      /* plasma insulin concentration    [µU/ml]      */
    double D1;      /* meal, absorption cpt 1          [g]          */
    double D2;      /* meal, absorption cpt 2          [g]          */
    double X;       /* insulin action (remote)         [1/min]      */
    double G;       /* plasma glucose                  [mg/dl]      */
    double Y;       /* heart-rate deviation            [bpm]        */
    double Z;       /* exercise recovery state         [-]          */
    double HRint;   /* integrated heart-rate deviation [bpm·min]    */
} DeichmannState;

/* ── Parameters ───────────────────────────────────────────────────────── */
typedef struct {
    double Gpeq;    /* target fasting glucose   [mg/dl]          */
    double BW;      /* body weight              [kg]             */
    double Gb;      /* basal glucose            [mg/dl]          */
    double Ib;      /* basal plasma insulin     [µU/ml]          */
    double HRb;     /* basal heart rate         [bpm]            */
    double p1;      /* glucose effectiveness    [1/min]          */
    double p2;      /* insulin action rate      [1/min]          */
    double p3;      /* insulin sensitivity      [1/min²·µU/ml]  */
    double alpha;   /* exercise coefficient     [1/bpm]          */
    double beta;    /* HR exercise coefficient  [1/bpm]          */
    double tauHR;   /* HR time constant         [min]            */
    double tau;     /* recovery time constant   [min]            */
    double f;       /* exercise parameter       [-]              */
    double AG;      /* carb bioavailability     [-]              */
    double Vg;      /* glucose volume           [dl/kg]          */
    double tau_m;   /* meal absorption time     [min]            */
    double k21;     /* insulin transfer rate    [1/min]          */
    double kd;      /* insulin degradation rate [1/min]          */
    double ka;      /* insulin absorption rate  [1/min]          */
    double ke;      /* insulin elimination rate [1/min]          */
    double Vi;      /* insulin volume           [L/kg]           */
    double IIRb;    /* basal insulin infusion   [U/h]            */
} DeichmannParams;

/* ── API ──────────────────────────────────────────────────────────────── */

DeichmannParams deichmann_default_params(void);

void deichmann_init(DeichmannState *s, const DeichmannParams *p);

/* Advance by dt minutes.
 *   carbs_g     : meal carbohydrates this step [g]  (0 if no meal)
 *   iir_u_per_h : insulin infusion rate        [U/h]
 *   hr_bpm      : current heart rate           [bpm] */
void deichmann_step(DeichmannState *s, const DeichmannParams *p,
                    double carbs_g, double iir_u_per_h,
                    double hr_bpm, double dt);

double deichmann_glucose_mg_dl(const DeichmannState *s);

#endif /* CGMSIM_DEICHMANN_H */
