#ifndef CGMSIM_PARAMS_H
#define CGMSIM_PARAMS_H

#include "cgmsim_cambridge.h"
#include "cgmsim_uva_padova.h"
#include "cgmsim_royparker.h"
#include "cgmsim_deichmann.h"

/*
 * Loads a model's parameter struct starting from its literature defaults
 * (*_default_params) and overriding any field present in the given JSON
 * file. Unknown/missing fields keep the default. A missing or unreadable
 * file simply yields the defaults untouched.
 */

CambridgeParams  cambridge_params_load(const char *path);
UvaPadovaParams  uva_padova_params_load(const char *path);
RoyParkerParams  royparker_params_load(const char *path);
DeichmannParams  deichmann_params_load(const char *path);

#endif /* CGMSIM_PARAMS_H */
