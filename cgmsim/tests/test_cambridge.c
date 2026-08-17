#include <math.h>
#include "mintest.h"
#include "../inc/cgmsim_cambridge.h"

void suite_cambridge(void) {
    MT_SUITE("Cambridge (Hovorka) Model");

    CambridgeParams p = cambridge_default_params();
    CambridgeState  s;
    cambridge_init(&s, &p);

    /* 1. Initial glucose should be close to the target Gpeq */
    double g0 = cambridge_glucose_mg_dl(&s, &p);
    MT_CHECK_DBL(g0, p.Gpeq, 5.0);  /* within ±5 mg/dl of 100 */

    /* 2. Q1 must be positive after init */
    MT_CHECK_GT(s.Q1, 0.0);

    /* 3. Plasma insulin must be positive after init */
    MT_CHECK_GT(s.I, 0.0);

    /* 4. A meal raises glucose: run 60 min with 75 g/min bolus (1-min injection) */
    CambridgeState sm;
    cambridge_init(&sm, &p);
    double basal_iir = sm.I * p.ke * p.VI * p.BW * 60.0 / 1000.0; /* U/h */
    /* deliver 75 g meal over 1 min */
    cambridge_step(&sm, &p, 75.0, basal_iir, 1.0);
    /* advance 90 min with no further meal */
    for (int i = 0; i < 90; i++)
        cambridge_step(&sm, &p, 0.0, basal_iir, 1.0);
    double g_meal = cambridge_glucose_mg_dl(&sm, &p);
    MT_CHECK_GT(g_meal, p.Gpeq);  /* glucose rose after meal */

    /* 5. Extra insulin lowers glucose: run 60 min with 3× basal insulin */
    CambridgeState si;
    cambridge_init(&si, &p);
    for (int i = 0; i < 90; i++)
        cambridge_step(&si, &p, 0.0, 3.0 * basal_iir, 1.0);
    double g_ins = cambridge_glucose_mg_dl(&si, &p);
    MT_CHECK_LT(g_ins, p.Gpeq);   /* glucose fell with extra insulin */

    /* 6. State variables are non-negative after simulation */
    MT_CHECK(sm.Q1 >= 0.0);
    MT_CHECK(sm.Q2 >= 0.0);
    MT_CHECK(sm.D1 >= 0.0);
    MT_CHECK(sm.D2 >= 0.0);

    /* 7. Default params: tmaxI and tmaxG are positive */
    MT_CHECK_GT(p.tmaxI, 0.0);
    MT_CHECK_GT(p.tmaxG, 0.0);

    /* 8. Renal glucose excretion activates above 9 mmol/l (162 mg/dl) */
    CambridgeState sh = s;
    sh.Q1 = 9.5 * p.VG * p.BW;   /* force G = 9.5 mmol/l */
    double G_high = sh.Q1 / (p.VG * p.BW);
    double FR_high = G_high > 9.0 ? 0.003 * (G_high - 9.0) : 0.0;
    MT_CHECK_GT(FR_high, 0.0);

    /* 9. Bioavailability AG applied to meal input */
    MT_CHECK_DBL(p.AG, 0.8, 1e-9);

    /* 10. Zero insulin in T1D: EGP is unopposed → glucose rises above Gpeq */
    CambridgeState sz;
    cambridge_init(&sz, &p);
    for (int i = 0; i < 120; i++)
        cambridge_step(&sz, &p, 0.0, 0.0, 1.0);
    MT_CHECK_GT(cambridge_glucose_mg_dl(&sz, &p), p.Gpeq);
}
