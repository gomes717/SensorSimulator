#ifndef CGMSIM_JSON_H
#define CGMSIM_JSON_H

#include <stddef.h>

/*
 * Minimal JSON reader: objects, arrays, strings, numbers, bool, null.
 * Enough to load model parameter files and simulation scenarios.
 * No external dependencies (pure C99).
 */

typedef enum {
    JSON_NULL,
    JSON_BOOL,
    JSON_NUMBER,
    JSON_STRING,
    JSON_ARRAY,
    JSON_OBJECT
} JsonType;

typedef struct JsonValue {
    JsonType type;
    union {
        int boolean;
        double number;
        char *string;
        struct { struct JsonValue **items; size_t count; } array;
        struct { char **keys; struct JsonValue **values; size_t count; } object;
    } as;
} JsonValue;

/* Parse JSON text/file. Returns NULL on error (malformed input, missing file). */
JsonValue *json_parse(const char *text);
JsonValue *json_parse_file(const char *path);

void json_free(JsonValue *v);

/* Accessors. All return a default/NULL when the value is absent or of the
 * wrong type, so callers can chain without NULL-checking every step. */
const JsonValue *json_object_get(const JsonValue *obj, const char *key);
double            json_number(const JsonValue *v, double default_value);
const char       *json_string(const JsonValue *v, const char *default_value);
size_t            json_array_size(const JsonValue *v);
const JsonValue  *json_array_get(const JsonValue *v, size_t index);

#endif /* CGMSIM_JSON_H */
