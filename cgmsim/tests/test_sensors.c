#include <math.h>
#include "mintest.h"
#include "../inc/cgmsim_sensors.h"

void suite_sensors(void) {
    MT_SUITE("Sensors (IdealCGM, IdealSMBG, Breton2008, Facchinetti2014)");

    const double TRUE_G = 120.0; /* mg/dl test glucose value */

    /* ── Ideal CGM ─────────────────────────────────────────── */

    IdealCGMState cgm;
    ideal_cgm_init(&cgm, 5.0);

    /* 1. First call (timer pre-loaded) produces a valid reading */
    CGMReading r1 = ideal_cgm_update(&cgm, TRUE_G, 1.0);
    MT_CHECK(r1.valid == 1);
    MT_CHECK_DBL(r1.value_mg_dl, TRUE_G, 1e-9);

    /* 2. Subsequent 1-min calls do NOT produce readings (within 5-min interval) */
    CGMReading r2 = ideal_cgm_update(&cgm, TRUE_G, 1.0);
    MT_CHECK(r2.valid == 0);

    /* 3. After 4 more minutes (5 total) a reading is produced */
    CGMReading r_mid = {0, 0};
    for (int i = 0; i < 4; i++)
        r_mid = ideal_cgm_update(&cgm, TRUE_G + 5.0, 1.0);
    MT_CHECK(r_mid.valid == 1);
    MT_CHECK_DBL(r_mid.value_mg_dl, TRUE_G + 5.0, 1e-9);

    /* ── Ideal SMBG ────────────────────────────────────────── */

    /* 4. SMBG always returns valid exact reading */
    CGMReading rs = ideal_smbg_measure(85.3);
    MT_CHECK(rs.valid == 1);
    MT_CHECK_DBL(rs.value_mg_dl, 85.3, 1e-9);

    /* 5. SMBG at zero glucose returns 0 */
    CGMReading rs0 = ideal_smbg_measure(0.0);
    MT_CHECK_DBL(rs0.value_mg_dl, 0.0, 1e-9);

    /* ── Breton 2008 ───────────────────────────────────────── */

    BretonState b;
    breton_init(&b, 42u);

    /* 6. Reading is produced at the first step (timer pre-loaded) */
    CGMReading rb1 = breton_update(&b, TRUE_G, 1.0);
    MT_CHECK(rb1.valid == 1);

    /* 7. Reading is within a plausible range of the true value (±20 mg/dl) */
    MT_CHECK(fabs(rb1.value_mg_dl - TRUE_G) < 20.0);

    /* 8. Different seeds give different noise sequences */
    BretonState b2;
    breton_init(&b2, 999u);
    CGMReading rb_a = breton_update(&b,  TRUE_G, 1.0);
    CGMReading rb_b = breton_update(&b2, TRUE_G, 1.0);
    /* At least one of the two 1-min intermediate readings differs */
    int differ = (fabs(rb_a.value_mg_dl - rb_b.value_mg_dl) > 0.01)
                 || (rb_a.valid != rb_b.valid);
    MT_CHECK(differ);

    /* 9. Breton output is clamped to [0, 1000] */
    BretonState bclamp;
    breton_init(&bclamp, 1u);
    /* Run 5 minutes to trigger output, first pass with very high glucose */
    CGMReading rc1 = breton_update(&bclamp, 5000.0, 1.0);
    for (int i = 0; i < 4; i++) breton_update(&bclamp, 5000.0, 1.0);
    CGMReading rc2 = breton_update(&bclamp, 5000.0, 1.0);
    MT_CHECK(rc1.value_mg_dl <= 1000.0);
    MT_CHECK(rc2.value_mg_dl <= 1000.0);

    /* ── Facchinetti 2014 ──────────────────────────────────── */

    FacchinetttiState f;
    facchinetti_init(&f, 7u);

    /* 10. First step produces a valid reading */
    CGMReading rf1 = facchinetti_update(&f, TRUE_G, 1.0);
    MT_CHECK(rf1.valid == 1);

    /* 11. Reading is within ±30 mg/dl of true value (calibration + noise) */
    MT_CHECK(fabs(rf1.value_mg_dl - TRUE_G) < 30.0);

    /* 12. Recalibration resets the calibration timer */
    double t_before = f.t_calib_days;
    facchinetti_recalibrate(&f);
    MT_CHECK_DBL(f.t_calib_days, 0.0, 1e-9);
    (void)t_before;

    /* 13. After recalibration, bias b(0) = b0 = -14.8 */
    MT_CHECK_DBL(f.b0, -14.8, 1e-9);

    /* 14. Non-valid steps have value 0 */
    CGMReading rf2 = facchinetti_update(&f, TRUE_G, 1.0);
    /* This is 1-min after output, so valid=0 */
    MT_CHECK(rf2.valid == 0);
    MT_CHECK_DBL(rf2.value_mg_dl, 0.0, 1e-9);
}
