#ifndef CGMSIM_TYPES_H
#define CGMSIM_TYPES_H

/* Result returned by all CGM/SMBG sensors each simulation step. */
typedef struct {
    double value_mg_dl; /* glucose reading */
    int    valid;       /* 1 if a measurement was produced this step */
} CGMReading;

#endif /* CGMSIM_TYPES_H */
