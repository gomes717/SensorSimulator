#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include "../inc/cgmsim_json.h"

typedef struct {
    const char *p;
} Parser;

static JsonValue *parse_value(Parser *ps);

static void skip_ws(Parser *ps) {
    while (*ps->p && isspace((unsigned char)*ps->p)) ps->p++;
}

static JsonValue *json_new(JsonType type) {
    JsonValue *v = (JsonValue *)calloc(1, sizeof(JsonValue));
    v->type = type;
    return v;
}

static char *parse_string_raw(Parser *ps) {
    /* assumes *ps->p == '"' */
    ps->p++;
    size_t cap = 32, len = 0;
    char *buf = (char *)malloc(cap);
    while (*ps->p && *ps->p != '"') {
        char c = *ps->p++;
        if (c == '\\' && *ps->p) {
            char esc = *ps->p++;
            switch (esc) {
                case 'n': c = '\n'; break;
                case 't': c = '\t'; break;
                case 'r': c = '\r'; break;
                case '"': c = '"';  break;
                case '\\': c = '\\'; break;
                case '/': c = '/';  break;
                default:  c = esc;  break;
            }
        }
        if (len + 1 >= cap) { cap *= 2; buf = (char *)realloc(buf, cap); }
        buf[len++] = c;
    }
    if (*ps->p == '"') ps->p++;
    buf[len] = '\0';
    return buf;
}

static JsonValue *parse_object(Parser *ps) {
    JsonValue *v = json_new(JSON_OBJECT);
    ps->p++; /* '{' */
    skip_ws(ps);
    size_t cap = 8;
    v->as.object.keys   = (char **)malloc(cap * sizeof(char *));
    v->as.object.values = (JsonValue **)malloc(cap * sizeof(JsonValue *));
    v->as.object.count  = 0;

    if (*ps->p == '}') { ps->p++; return v; }

    while (*ps->p) {
        skip_ws(ps);
        if (*ps->p != '"') break; /* malformed */
        char *key = parse_string_raw(ps);
        skip_ws(ps);
        if (*ps->p != ':') { free(key); break; }
        ps->p++;
        skip_ws(ps);
        JsonValue *val = parse_value(ps);
        if (val == NULL) { free(key); break; }

        if (v->as.object.count >= cap) {
            cap *= 2;
            v->as.object.keys   = (char **)realloc(v->as.object.keys, cap * sizeof(char *));
            v->as.object.values = (JsonValue **)realloc(v->as.object.values, cap * sizeof(JsonValue *));
        }
        v->as.object.keys[v->as.object.count]   = key;
        v->as.object.values[v->as.object.count] = val;
        v->as.object.count++;

        skip_ws(ps);
        if (*ps->p == ',') { ps->p++; continue; }
        if (*ps->p == '}') { ps->p++; break; }
        break; /* malformed */
    }
    return v;
}

static JsonValue *parse_array(Parser *ps) {
    JsonValue *v = json_new(JSON_ARRAY);
    ps->p++; /* '[' */
    skip_ws(ps);
    size_t cap = 8;
    v->as.array.items = (JsonValue **)malloc(cap * sizeof(JsonValue *));
    v->as.array.count = 0;

    if (*ps->p == ']') { ps->p++; return v; }

    while (*ps->p) {
        skip_ws(ps);
        JsonValue *item = parse_value(ps);
        if (item == NULL) break; /* malformed */

        if (v->as.array.count >= cap) {
            cap *= 2;
            v->as.array.items = (JsonValue **)realloc(v->as.array.items, cap * sizeof(JsonValue *));
        }
        v->as.array.items[v->as.array.count++] = item;

        skip_ws(ps);
        if (*ps->p == ',') { ps->p++; continue; }
        if (*ps->p == ']') { ps->p++; break; }
        break; /* malformed */
    }
    return v;
}

static JsonValue *parse_value(Parser *ps) {
    skip_ws(ps);
    char c = *ps->p;
    if (c == '{') return parse_object(ps);
    if (c == '[') return parse_array(ps);
    if (c == '"') {
        JsonValue *v = json_new(JSON_STRING);
        v->as.string = parse_string_raw(ps);
        return v;
    }
    if (c == 't' && strncmp(ps->p, "true", 4) == 0) {
        ps->p += 4;
        JsonValue *v = json_new(JSON_BOOL);
        v->as.boolean = 1;
        return v;
    }
    if (c == 'f' && strncmp(ps->p, "false", 5) == 0) {
        ps->p += 5;
        JsonValue *v = json_new(JSON_BOOL);
        v->as.boolean = 0;
        return v;
    }
    if (c == 'n' && strncmp(ps->p, "null", 4) == 0) {
        ps->p += 4;
        return json_new(JSON_NULL);
    }
    if (c == '-' || isdigit((unsigned char)c)) {
        char *end;
        double num = strtod(ps->p, &end);
        if (end == ps->p) return NULL; /* malformed */
        ps->p = end;
        JsonValue *v = json_new(JSON_NUMBER);
        v->as.number = num;
        return v;
    }
    return NULL; /* malformed / unexpected char */
}

JsonValue *json_parse(const char *text) {
    if (!text) return NULL;
    Parser ps;
    ps.p = text;
    JsonValue *v = parse_value(&ps);
    return v;
}

JsonValue *json_parse_file(const char *path) {
    FILE *f = fopen(path, "rb");
    if (!f) return NULL;
    fseek(f, 0, SEEK_END);
    long size = ftell(f);
    if (size < 0) { fclose(f); return NULL; }
    fseek(f, 0, SEEK_SET);
    char *buf = (char *)malloc((size_t)size + 1);
    size_t nread = fread(buf, 1, (size_t)size, f);
    buf[nread] = '\0';
    fclose(f);
    JsonValue *v = json_parse(buf);
    free(buf);
    return v;
}

void json_free(JsonValue *v) {
    if (!v) return;
    switch (v->type) {
        case JSON_STRING:
            free(v->as.string);
            break;
        case JSON_ARRAY:
            for (size_t i = 0; i < v->as.array.count; i++)
                json_free(v->as.array.items[i]);
            free(v->as.array.items);
            break;
        case JSON_OBJECT:
            for (size_t i = 0; i < v->as.object.count; i++) {
                free(v->as.object.keys[i]);
                json_free(v->as.object.values[i]);
            }
            free(v->as.object.keys);
            free(v->as.object.values);
            break;
        default:
            break;
    }
    free(v);
}

const JsonValue *json_object_get(const JsonValue *obj, const char *key) {
    if (!obj || obj->type != JSON_OBJECT) return NULL;
    for (size_t i = 0; i < obj->as.object.count; i++) {
        if (strcmp(obj->as.object.keys[i], key) == 0)
            return obj->as.object.values[i];
    }
    return NULL;
}

double json_number(const JsonValue *v, double default_value) {
    if (!v || v->type != JSON_NUMBER) return default_value;
    return v->as.number;
}

const char *json_string(const JsonValue *v, const char *default_value) {
    if (!v || v->type != JSON_STRING) return default_value;
    return v->as.string;
}

size_t json_array_size(const JsonValue *v) {
    if (!v || v->type != JSON_ARRAY) return 0;
    return v->as.array.count;
}

const JsonValue *json_array_get(const JsonValue *v, size_t index) {
    if (!v || v->type != JSON_ARRAY || index >= v->as.array.count) return NULL;
    return v->as.array.items[index];
}
