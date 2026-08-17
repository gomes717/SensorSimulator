#ifndef MINTEST_H
#define MINTEST_H

#include <stdio.h>
#include <math.h>

/* Shared counters — defined in test_main.c */
extern int mt_total;
extern int mt_passed;

/* Check a boolean condition */
#define MT_CHECK(expr) do { \
    mt_total++; \
    if (expr) { \
        mt_passed++; \
    } else { \
        printf("  FAIL  %s:%d  (%s)\n", __FILE__, __LINE__, #expr); \
    } \
} while (0)

/* Check two doubles are within eps of each other */
#define MT_CHECK_DBL(a, b, eps) do { \
    mt_total++; \
    double _a = (double)(a), _b = (double)(b), _e = (double)(eps); \
    if (fabs(_a - _b) <= _e) { \
        mt_passed++; \
    } else { \
        printf("  FAIL  %s:%d  got %.8g  expected %.8g  (eps %.2g)\n", \
               __FILE__, __LINE__, _a, _b, _e); \
    } \
} while (0)

/* Check a > b */
#define MT_CHECK_GT(a, b) do { \
    mt_total++; \
    double _a = (double)(a), _b = (double)(b); \
    if (_a > _b) { \
        mt_passed++; \
    } else { \
        printf("  FAIL  %s:%d  %.8g not > %.8g\n", __FILE__, __LINE__, _a, _b); \
    } \
} while (0)

/* Check a < b */
#define MT_CHECK_LT(a, b) do { \
    mt_total++; \
    double _a = (double)(a), _b = (double)(b); \
    if (_a < _b) { \
        mt_passed++; \
    } else { \
        printf("  FAIL  %s:%d  %.8g not < %.8g\n", __FILE__, __LINE__, _a, _b); \
    } \
} while (0)

#define MT_SUITE(name) printf("\n=== %s ===\n", (name))

#define MT_SUMMARY() \
    printf("\n--- %d / %d tests passed ---\n", mt_passed, mt_total)

#endif /* MINTEST_H */
