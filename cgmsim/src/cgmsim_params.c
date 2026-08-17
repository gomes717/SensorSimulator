#include <stddef.h>
#include <string.h>
#include "../inc/cgmsim_params.h"
#include "../inc/cgmsim_json.h"

typedef struct {
    const char *name;
    size_t offset;
} ParamField;

#define PFIELD(struct_type, field) { #field, offsetof(struct_type, field) }

static void apply_json_fields(void *dst, const JsonValue *obj,
                               const ParamField *fields, size_t n_fields) {
    if (!obj || obj->type != JSON_OBJECT) return;
    for (size_t i = 0; i < n_fields; i++) {
        const JsonValue *v = json_object_get(obj, fields[i].name);
        if (v && v->type == JSON_NUMBER) {
            *(double *)((char *)dst + fields[i].offset) = v->as.number;
        }
    }
}

/* ── Cambridge ────────────────────────────────────────────────────────── */
static const ParamField cambridge_fields[] = {
    PFIELD(CambridgeParams, BW),  PFIELD(CambridgeParams, VG),
    PFIELD(CambridgeParams, VI),  PFIELD(CambridgeParams, k12),
    PFIELD(CambridgeParams, ka1), PFIELD(CambridgeParams, ka2),
    PFIELD(CambridgeParams, ka3), PFIELD(CambridgeParams, SIT),
    PFIELD(CambridgeParams, SID), PFIELD(CambridgeParams, SIE),
    PFIELD(CambridgeParams, ke),  PFIELD(CambridgeParams, tmaxI),
    PFIELD(CambridgeParams, tmaxG), PFIELD(CambridgeParams, AG),
    PFIELD(CambridgeParams, EGP0), PFIELD(CambridgeParams, F01),
    PFIELD(CambridgeParams, Gpeq),
};

CambridgeParams cambridge_params_load(const char *path) {
    CambridgeParams p = cambridge_default_params();
    JsonValue *root = json_parse_file(path);
    apply_json_fields(&p, root, cambridge_fields,
                       sizeof(cambridge_fields) / sizeof(cambridge_fields[0]));
    json_free(root);
    return p;
}

/* ── UVA/Padova ───────────────────────────────────────────────────────── */
static const ParamField uva_padova_fields[] = {
    PFIELD(UvaPadovaParams, BW),   PFIELD(UvaPadovaParams, VG),
    PFIELD(UvaPadovaParams, VI),   PFIELD(UvaPadovaParams, k1),
    PFIELD(UvaPadovaParams, k2),   PFIELD(UvaPadovaParams, m1),
    PFIELD(UvaPadovaParams, m2),   PFIELD(UvaPadovaParams, m4),
    PFIELD(UvaPadovaParams, kmin), PFIELD(UvaPadovaParams, kmax),
    PFIELD(UvaPadovaParams, kgri), PFIELD(UvaPadovaParams, kabs),
    PFIELD(UvaPadovaParams, ki),   PFIELD(UvaPadovaParams, Fcns),
    PFIELD(UvaPadovaParams, Vm0),  PFIELD(UvaPadovaParams, Vmx),
    PFIELD(UvaPadovaParams, Km0),  PFIELD(UvaPadovaParams, p2u),
    PFIELD(UvaPadovaParams, kp1),  PFIELD(UvaPadovaParams, kp2),
    PFIELD(UvaPadovaParams, kp3),  PFIELD(UvaPadovaParams, ke1),
    PFIELD(UvaPadovaParams, ke2),  PFIELD(UvaPadovaParams, ka1),
    PFIELD(UvaPadovaParams, ka2),  PFIELD(UvaPadovaParams, kd),
    PFIELD(UvaPadovaParams, Td),   PFIELD(UvaPadovaParams, bmeal),
    PFIELD(UvaPadovaParams, cmeal),PFIELD(UvaPadovaParams, f),
    PFIELD(UvaPadovaParams, HEeq), PFIELD(UvaPadovaParams, Gpeq),
    PFIELD(UvaPadovaParams, Ib),
};

UvaPadovaParams uva_padova_params_load(const char *path) {
    UvaPadovaParams p = uva_padova_default_params();
    JsonValue *root = json_parse_file(path);
    apply_json_fields(&p, root, uva_padova_fields,
                       sizeof(uva_padova_fields) / sizeof(uva_padova_fields[0]));
    json_free(root);
    return p;
}

/* ── Roy/Parker ───────────────────────────────────────────────────────── */
static const ParamField royparker_fields[] = {
    PFIELD(RoyParkerParams, Gpeq), PFIELD(RoyParkerParams, BW),
    PFIELD(RoyParkerParams, VolG), PFIELD(RoyParkerParams, Ib),
    PFIELD(RoyParkerParams, u1b),  PFIELD(RoyParkerParams, p1),
    PFIELD(RoyParkerParams, p2),   PFIELD(RoyParkerParams, p3),
    PFIELD(RoyParkerParams, p4),   PFIELD(RoyParkerParams, n),
    PFIELD(RoyParkerParams, a1),   PFIELD(RoyParkerParams, a2),
    PFIELD(RoyParkerParams, a3),   PFIELD(RoyParkerParams, a4),
    PFIELD(RoyParkerParams, a5),   PFIELD(RoyParkerParams, a6),
    PFIELD(RoyParkerParams, k),    PFIELD(RoyParkerParams, T1),
    PFIELD(RoyParkerParams, kG),   PFIELD(RoyParkerParams, Tasc),
    PFIELD(RoyParkerParams, Tmax), PFIELD(RoyParkerParams, Tdes),
};

RoyParkerParams royparker_params_load(const char *path) {
    RoyParkerParams p = royparker_default_params();
    JsonValue *root = json_parse_file(path);
    apply_json_fields(&p, root, royparker_fields,
                       sizeof(royparker_fields) / sizeof(royparker_fields[0]));
    json_free(root);
    return p;
}

/* ── Deichmann ────────────────────────────────────────────────────────── */
static const ParamField deichmann_fields[] = {
    PFIELD(DeichmannParams, Gpeq),  PFIELD(DeichmannParams, BW),
    PFIELD(DeichmannParams, Gb),    PFIELD(DeichmannParams, Ib),
    PFIELD(DeichmannParams, HRb),   PFIELD(DeichmannParams, p1),
    PFIELD(DeichmannParams, p2),    PFIELD(DeichmannParams, p3),
    PFIELD(DeichmannParams, alpha), PFIELD(DeichmannParams, beta),
    PFIELD(DeichmannParams, tauHR), PFIELD(DeichmannParams, tau),
    PFIELD(DeichmannParams, f),     PFIELD(DeichmannParams, AG),
    PFIELD(DeichmannParams, Vg),    PFIELD(DeichmannParams, tau_m),
    PFIELD(DeichmannParams, k21),   PFIELD(DeichmannParams, kd),
    PFIELD(DeichmannParams, ka),    PFIELD(DeichmannParams, ke),
    PFIELD(DeichmannParams, Vi),    PFIELD(DeichmannParams, IIRb),
};

DeichmannParams deichmann_params_load(const char *path) {
    DeichmannParams p = deichmann_default_params();
    JsonValue *root = json_parse_file(path);
    apply_json_fields(&p, root, deichmann_fields,
                       sizeof(deichmann_fields) / sizeof(deichmann_fields[0]));
    json_free(root);
    return p;
}
