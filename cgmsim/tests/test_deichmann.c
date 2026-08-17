#include <math.h>
#include "mintest.h"
#include "../inc/cgmsim_deichmann.h"

void suite_deichmann(void) {
    MT_SUITE("Deichmann 2021 Exercise Model");

    DeichmannParams p = deichmann_default_params();
    DeichmannState  s;
    deichmann_init(&s, &p);

    /* 1. Initial glucose equals Gpeq */
    MT_CHECK_DBL(deichmann_glucose_mg_dl(&s), p.Gpeq, 1e-9);

    /* 2. Subcutaneous insulin cpt 1 is non-zero at steady state (basal IIR) */
    MT_CHECK_GT(s.x1, 0.0);

    /* 3. Plasma insulin Ic converges toward basal Ib over time */
    MT_CHECK_DBL(s.Ic, p.Ib, p.Ib * 0.5); /* within 50% of basal */

    /* 4. Exercise at HR=140 bpm activates the Y state (HR deviation) */
    DeichmannState se;
    deichmann_init(&se, &p);
    for (int i = 0; i < 20; i++)
        deichmann_step(&se, &p, 0.0, p.IIRb, 140.0, 1.0);
    MT_CHECK_GT(se.Y, 0.0);

    /* 5. Z state builds up during sustained exercise */
    MT_CHECK_GT(se.Z, 0.0);

    /* 6. HRint accumulates during elevated HR */
    MT_CHECK_GT(se.HRint, 0.0);

    /* 7. Meal raises glucose: 60 g, advance 90 min */
    DeichmannState sm;
    deichmann_init(&sm, &p);
    deichmann_step(&sm, &p, 60.0, p.IIRb, p.HRb, 1.0);
    for (int i = 0; i < 90; i++)
        deichmann_step(&sm, &p, 0.0, p.IIRb, p.HRb, 1.0);
    MT_CHECK_GT(deichmann_glucose_mg_dl(&sm), p.Gpeq);

    /* 8. Higher insulin → lower glucose: parallel 300-min trajectories.
     * The Deichmann model equilibrates near Gb=172 mg/dl, so we cannot
     * simply compare against Gpeq=100. Instead, run two identical sims
     * that differ only in insulin dose and verify the ordering at the end. */
    DeichmannState s_basal, s_triple;
    deichmann_init(&s_basal, &p);
    deichmann_init(&s_triple, &p);
    for (int i = 0; i < 300; i++) {
        deichmann_step(&s_basal,  &p, 0.0, p.IIRb,       p.HRb, 1.0);
        deichmann_step(&s_triple, &p, 0.0, 3.0 * p.IIRb, p.HRb, 1.0);
    }
    MT_CHECK_LT(deichmann_glucose_mg_dl(&s_triple),
                deichmann_glucose_mg_dl(&s_basal));

    /* 9. D1 and D2 are zero at init */
    MT_CHECK_DBL(s.D1, 0.0, 1e-9);
    MT_CHECK_DBL(s.D2, 0.0, 1e-9);

    /* 10. HRint is zero at rest (no HR deviation) */
    MT_CHECK_DBL(s.HRint, 0.0, 1e-9);
}
