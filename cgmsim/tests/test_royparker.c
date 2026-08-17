#include <math.h>
#include "mintest.h"
#include "../inc/cgmsim_royparker.h"

void suite_royparker(void) {
    MT_SUITE("Roy/Parker 2007 Exercise Model");

    RoyParkerParams p = royparker_default_params();
    RoyParkerState  s;
    royparker_init(&s, &p);

    /* 1. Initial glucose equals Gpeq */
    MT_CHECK_DBL(royparker_glucose_mg_dl(&s), p.Gpeq, 1e-9);

    /* 2. Gut starts empty */
    MT_CHECK_DBL(s.NG, 0.0, 1e-9);

    /* 3. PVO2max starts at zero (no exercise) */
    MT_CHECK_DBL(s.PVO2max, 0.0, 1e-9);

    /* 4. A meal raises glucose: 60 g bolus, advance 90 min with basal IIR */
    RoyParkerState sm;
    royparker_init(&sm, &p);
    royparker_step(&sm, &p, 60.0, p.u1b, 0.0, 0.0, 1.0);
    for (int i = 1; i <= 90; i++)
        royparker_step(&sm, &p, 0.0, p.u1b, 0.0, (double)i, 1.0);
    MT_CHECK_GT(royparker_glucose_mg_dl(&sm), p.Gpeq);

    /* 5. Exercise activates PVO2max within 5 minutes */
    RoyParkerState se;
    royparker_init(&se, &p);
    for (int i = 0; i < 5; i++)
        royparker_step(&se, &p, 0.0, p.u1b, 50.0, (double)i, 1.0);
    MT_CHECK_GT(se.PVO2max, 0.0);

    /* 6. Exercise drives up Gprod (hepatic glucose release) */
    RoyParkerState se2;
    royparker_init(&se2, &p);
    for (int i = 0; i < 30; i++)
        royparker_step(&se2, &p, 0.0, p.u1b, 70.0, (double)i, 1.0);
    MT_CHECK_GT(se2.Gprod, 0.0);

    /* 7. Insulin action state X is positive at steady state */
    MT_CHECK_GT(s.I, 0.0);

    /* 8. Glycogenolysis (Ggly) remains zero when no exercise */
    MT_CHECK_DBL(s.Ggly, 0.0, 1e-9);

    /* 9. Meal start is recorded in state after meal step */
    RoyParkerState sm2;
    royparker_init(&sm2, &p);
    royparker_step(&sm2, &p, 45.0, p.u1b, 0.0, 10.0, 1.0);
    MT_CHECK_DBL(sm2.meal_start_min, 10.0, 1e-9);
    MT_CHECK_DBL(sm2.meal_carbs_g,   45.0, 1e-9);

    /* 10. Extra insulin for 60 min lowers glucose below Gpeq */
    RoyParkerState si;
    royparker_init(&si, &p);
    for (int i = 0; i < 90; i++)
        royparker_step(&si, &p, 0.0, 3.0 * p.u1b, 0.0, (double)i, 1.0);
    MT_CHECK_LT(royparker_glucose_mg_dl(&si), p.Gpeq);
}
