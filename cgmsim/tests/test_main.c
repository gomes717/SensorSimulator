#include <stdio.h>
#include "mintest.h"

/* Shared counters — declared extern in mintest.h */
int mt_total  = 0;
int mt_passed = 0;

/* Test suite prototypes */
void suite_cambridge(void);
void suite_uva_padova(void);
void suite_royparker(void);
void suite_deichmann(void);
void suite_sensors(void);

int main(void) {
    printf("==============================================\n");
    printf("  CGMSIM Unit Tests\n");
    printf("==============================================\n");

    suite_cambridge();
    suite_uva_padova();
    suite_royparker();
    suite_deichmann();
    suite_sensors();

    MT_SUMMARY();

    return (mt_passed == mt_total) ? 0 : 1;
}
