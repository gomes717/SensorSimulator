#include <math.h>
#include "mintest.h"
#include "../inc/cgmsim_uva_padova.h"

void suite_uva_padova(void) {
    MT_SUITE("UVA/Padova T1DMS Model");

    UvaPadovaParams p = uva_padova_default_params();
    UvaPadovaState  s;
    uva_padova_init(&s, &p);

    /* 1. Initial plasma glucose is near target */
    double g0 = uva_padova_glucose_mg_dl(&s, &p);
    MT_CHECK_DBL(g0, p.Gpeq, 10.0);

    /* 2. Plasma glucose = Gp/VG */
    MT_CHECK_DBL(s.Gp / p.VG, g0, 1e-9);

    /* 3. Tissue glucose Gt > 0 after init */
    MT_CHECK_GT(s.Gt, 0.0);

    /* 4. Stomach compartments start empty */
    MT_CHECK_DBL(s.Qsto1, 0.0, 1e-9);
    MT_CHECK_DBL(s.Qsto2, 0.0, 1e-9);
    MT_CHECK_DBL(s.Qgut,  0.0, 1e-9);

    /* 5. A 75 g meal raises glucose */
    UvaPadovaState sm;
    uva_padova_init(&sm, &p);
    /* deliver 75 g over 1 min (= 75 g/min for 1 step) */
    uva_padova_step(&sm, &p, 75.0, 0.0, 1.0);
    /* advance 120 min with zero insulin */
    for (int i = 0; i < 120; i++)
        uva_padova_step(&sm, &p, 0.0, 0.0, 1.0);
    double g_meal = uva_padova_glucose_mg_dl(&sm, &p);
    MT_CHECK_GT(g_meal, p.Gpeq);

    /* 6. Stomach empties: Qsto1 increases immediately after meal */
    UvaPadovaState sm2;
    uva_padova_init(&sm2, &p);
    uva_padova_step(&sm2, &p, 50.0, 0.0, 1.0);
    MT_CHECK_GT(sm2.Qsto1, 0.0);

    /* 7. Insulin infusion raises subcutaneous insulin cpt 1 */
    UvaPadovaState si;
    uva_padova_init(&si, &p);
    uva_padova_step(&si, &p, 0.0, 0.01, 1.0); /* 0.01 U/min */
    MT_CHECK_GT(si.Isc1, 0.0);

    /* 8. Subcutaneous glucose Gs tracks plasma Gp with lag */
    MT_CHECK_DBL(s.Gs, s.Gp, 10.0);

    /* 9. Default kempt parameters are in valid range (0 ≤ kmin ≤ kmax) */
    MT_CHECK(p.kmin >= 0.0 && p.kmin <= p.kmax);

    /* 10. EGP > 0 at equilibrium glucose (basal liver glucose release) */
    double EGP0 = p.kp1 - p.kp2 * s.Gp - p.kp3 * s.XL;
    MT_CHECK_GT(EGP0, 0.0);
}
