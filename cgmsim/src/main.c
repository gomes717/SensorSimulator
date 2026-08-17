/*
 * cgmsim — command-line glucose simulator.
 *
 * Usage:
 *   cgmsim <scenario.json> [output.csv]
 *
 * Reads a scenario file describing which model to run, its parameters,
 * and a timeline of meal / insulin / exercise events, integrates the
 * model over the requested duration, and writes a CSV log that a Python
 * script (see plot.py) can turn into a graph. There is no interactive
 * UI here on purpose — everything is driven by the scenario file.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "../inc/cgmsim_cambridge.h"
#include "../inc/cgmsim_uva_padova.h"
#include "../inc/cgmsim_royparker.h"
#include "../inc/cgmsim_deichmann.h"
#include "../inc/cgmsim_sensors.h"
#include "../inc/cgmsim_params.h"
#include "../inc/cgmsim_json.h"

/* ── Model selection ─────────────────────────────────────────────────── */

typedef enum {
    MODEL_CAMBRIDGE,
    MODEL_UVA_PADOVA,
    MODEL_ROYPARKER,
    MODEL_DEICHMANN
} ModelKind;

static int model_kind_from_name(const char *name, ModelKind *out) {
    if (strcmp(name, "cambridge") == 0)   { *out = MODEL_CAMBRIDGE;  return 1; }
    if (strcmp(name, "uva_padova") == 0)  { *out = MODEL_UVA_PADOVA; return 1; }
    if (strcmp(name, "royparker") == 0)   { *out = MODEL_ROYPARKER;  return 1; }
    if (strcmp(name, "deichmann") == 0)   { *out = MODEL_DEICHMANN;  return 1; }
    return 0;
}

static const char *default_params_path(ModelKind k) {
    switch (k) {
        case MODEL_CAMBRIDGE:  return "params/cambridge.json";
        case MODEL_UVA_PADOVA: return "params/uva_padova.json";
        case MODEL_ROYPARKER:  return "params/royparker.json";
        case MODEL_DEICHMANN:  return "params/deichmann.json";
    }
    return "";
}

typedef struct {
    ModelKind kind;
    union {
        CambridgeState  cambridge;
        UvaPadovaState  uva_padova;
        RoyParkerState  royparker;
        DeichmannState  deichmann;
    } state;
    union {
        CambridgeParams cambridge;
        UvaPadovaParams uva_padova;
        RoyParkerParams royparker;
        DeichmannParams deichmann;
    } params;
} Model;

static void model_init(Model *m, const char *params_path) {
    switch (m->kind) {
        case MODEL_CAMBRIDGE:
            m->params.cambridge = cambridge_params_load(params_path);
            cambridge_init(&m->state.cambridge, &m->params.cambridge);
            break;
        case MODEL_UVA_PADOVA:
            m->params.uva_padova = uva_padova_params_load(params_path);
            uva_padova_init(&m->state.uva_padova, &m->params.uva_padova);
            break;
        case MODEL_ROYPARKER:
            m->params.royparker = royparker_params_load(params_path);
            royparker_init(&m->state.royparker, &m->params.royparker);
            break;
        case MODEL_DEICHMANN:
            m->params.deichmann = deichmann_params_load(params_path);
            deichmann_init(&m->state.deichmann, &m->params.deichmann);
            break;
    }
}

/* Baseline basal insulin infusion [U/h] used until a "basal" event fires.
 * royparker/deichmann carry a basal field in their own params; cambridge
 * and uva_padova instead expose a getter that reproduces the exact basal
 * rate their *_init() steady state assumes, so glucose doesn't drift away
 * from Gpeq with no meals/boluses in play. */
static double model_default_basal_u_per_h(const Model *m) {
    switch (m->kind) {
        case MODEL_CAMBRIDGE:  return cambridge_basal_iir_u_per_h(&m->params.cambridge);
        case MODEL_UVA_PADOVA: return uva_padova_basal_iir_u_per_h(&m->params.uva_padova);
        case MODEL_ROYPARKER:  return m->params.royparker.u1b;
        case MODEL_DEICHMANN:  return m->params.deichmann.IIRb;
    }
    return 1.0;
}

static double model_hr_basal(const Model *m) {
    return (m->kind == MODEL_DEICHMANN) ? m->params.deichmann.HRb : 0.0;
}

/* Advance the selected model by dt minutes and return plasma glucose. */
static double model_step(Model *m, double carbs_g_step, double carbs_rate_g_per_min,
                          double iir_u_per_h, double exercise_pct, double hr_bpm,
                          double t_sim, double dt) {
    switch (m->kind) {
        case MODEL_CAMBRIDGE:
            cambridge_step(&m->state.cambridge, &m->params.cambridge,
                            carbs_rate_g_per_min, iir_u_per_h, dt);
            return cambridge_glucose_mg_dl(&m->state.cambridge, &m->params.cambridge);
        case MODEL_UVA_PADOVA:
            uva_padova_step(&m->state.uva_padova, &m->params.uva_padova,
                             carbs_rate_g_per_min, iir_u_per_h / 60.0, dt);
            return uva_padova_glucose_mg_dl(&m->state.uva_padova, &m->params.uva_padova);
        case MODEL_ROYPARKER:
            royparker_step(&m->state.royparker, &m->params.royparker,
                            carbs_g_step, iir_u_per_h, exercise_pct, t_sim, dt);
            return royparker_glucose_mg_dl(&m->state.royparker);
        case MODEL_DEICHMANN:
            deichmann_step(&m->state.deichmann, &m->params.deichmann,
                            carbs_g_step, iir_u_per_h, hr_bpm, dt);
            return deichmann_glucose_mg_dl(&m->state.deichmann);
    }
    return 0.0;
}

/* ── Sensor selection ────────────────────────────────────────────────── */

typedef enum { SENSOR_IDEAL_CGM, SENSOR_BRETON, SENSOR_FACCHINETTI } SensorKind;

typedef struct {
    SensorKind kind;
    union {
        IdealCGMState     ideal;
        BretonState        breton;
        FacchinetttiState facchinetti;
    } state;
} Sensor;

static int sensor_kind_from_name(const char *name, SensorKind *out) {
    if (strcmp(name, "ideal_cgm") == 0)    { *out = SENSOR_IDEAL_CGM;   return 1; }
    if (strcmp(name, "breton") == 0)       { *out = SENSOR_BRETON;      return 1; }
    if (strcmp(name, "facchinetti") == 0)  { *out = SENSOR_FACCHINETTI; return 1; }
    return 0;
}

static void sensor_init(Sensor *sn, unsigned int seed) {
    switch (sn->kind) {
        case SENSOR_IDEAL_CGM:   ideal_cgm_init(&sn->state.ideal, 5.0); break;
        case SENSOR_BRETON:      breton_init(&sn->state.breton, seed); break;
        case SENSOR_FACCHINETTI: facchinetti_init(&sn->state.facchinetti, seed); break;
    }
}

static CGMReading sensor_update(Sensor *sn, double gp_mg_dl, double dt) {
    switch (sn->kind) {
        case SENSOR_IDEAL_CGM:   return ideal_cgm_update(&sn->state.ideal, gp_mg_dl, dt);
        case SENSOR_BRETON:      return breton_update(&sn->state.breton, gp_mg_dl, dt);
        case SENSOR_FACCHINETTI: return facchinetti_update(&sn->state.facchinetti, gp_mg_dl, dt);
    }
    CGMReading r = {0.0, 0};
    return r;
}

/* ── Scenario events ─────────────────────────────────────────────────── */

typedef enum { EVT_MEAL, EVT_BASAL, EVT_BOLUS, EVT_EXERCISE, EVT_HR } EventType;

typedef struct {
    EventType type;
    double time_min;
    double duration_min; /* meal/bolus/exercise/hr window; unused for basal */
    double value;        /* carbs_g | u_per_h | units | pct | bpm */
} Event;

static int event_type_from_name(const char *name, EventType *out) {
    if (strcmp(name, "meal") == 0)     { *out = EVT_MEAL;     return 1; }
    if (strcmp(name, "basal") == 0)    { *out = EVT_BASAL;    return 1; }
    if (strcmp(name, "bolus") == 0)    { *out = EVT_BOLUS;    return 1; }
    if (strcmp(name, "exercise") == 0) { *out = EVT_EXERCISE; return 1; }
    if (strcmp(name, "hr") == 0)       { *out = EVT_HR;       return 1; }
    return 0;
}

static int event_cmp(const void *a, const void *b) {
    double ta = ((const Event *)a)->time_min;
    double tb = ((const Event *)b)->time_min;
    return (ta > tb) - (ta < tb);
}

static Event *load_events(const JsonValue *scenario, size_t *out_count) {
    const JsonValue *arr = json_object_get(scenario, "events");
    size_t n = json_array_size(arr);
    Event *events = (Event *)calloc(n ? n : 1, sizeof(Event));
    size_t count = 0;

    for (size_t i = 0; i < n; i++) {
        const JsonValue *e = json_array_get(arr, i);
        const char *type_name = json_string(json_object_get(e, "type"), "");
        EventType type;
        if (!event_type_from_name(type_name, &type)) {
            fprintf(stderr, "warning: skipping event with unknown type '%s'\n", type_name);
            continue;
        }

        Event ev;
        ev.type         = type;
        ev.time_min     = json_number(json_object_get(e, "time_min"), 0.0);
        ev.duration_min  = json_number(json_object_get(e, "duration_min"),
                                       (type == EVT_BOLUS) ? 5.0 : 15.0);

        switch (type) {
            case EVT_MEAL:     ev.value = json_number(json_object_get(e, "carbs_g"), 0.0); break;
            case EVT_BASAL:    ev.value = json_number(json_object_get(e, "u_per_h"), 0.0); break;
            case EVT_BOLUS:    ev.value = json_number(json_object_get(e, "units"), 0.0);   break;
            case EVT_EXERCISE: ev.value = json_number(json_object_get(e, "pct"), 0.0);     break;
            case EVT_HR:       ev.value = json_number(json_object_get(e, "bpm"), 0.0);     break;
        }
        events[count++] = ev;
    }

    qsort(events, count, sizeof(Event), event_cmp);
    *out_count = count;
    return events;
}

/* Sum of active meal carb rate [g/min] at time t (windowed events). */
static double active_carb_rate(const Event *events, size_t n, double t) {
    double rate = 0.0;
    for (size_t i = 0; i < n; i++) {
        if (events[i].type != EVT_MEAL) continue;
        if (t >= events[i].time_min && t < events[i].time_min + events[i].duration_min)
            rate += events[i].value / events[i].duration_min;
    }
    return rate;
}

/* Total meal grams delivered exactly in the step starting at t (Roy/Parker
 * wants the whole meal at once and does its own trapezoidal absorption). */
static double meal_grams_at_step(const Event *events, size_t n, double t, double dt) {
    double grams = 0.0;
    for (size_t i = 0; i < n; i++) {
        if (events[i].type != EVT_MEAL) continue;
        if (t <= events[i].time_min && events[i].time_min < t + dt)
            grams += events[i].value;
    }
    return grams;
}

static double current_basal(const Event *events, size_t n, double t, double fallback) {
    double best_time = -1.0;
    double value = fallback;
    for (size_t i = 0; i < n; i++) {
        if (events[i].type != EVT_BASAL) continue;
        if (events[i].time_min <= t && events[i].time_min > best_time) {
            best_time = events[i].time_min;
            value = events[i].value;
        }
    }
    return value;
}

static double active_bolus_u_per_h(const Event *events, size_t n, double t) {
    double u_per_h = 0.0;
    for (size_t i = 0; i < n; i++) {
        if (events[i].type != EVT_BOLUS) continue;
        if (t >= events[i].time_min && t < events[i].time_min + events[i].duration_min)
            u_per_h += events[i].value * 60.0 / events[i].duration_min;
    }
    return u_per_h;
}

static double active_exercise_pct(const Event *events, size_t n, double t) {
    for (size_t i = 0; i < n; i++) {
        if (events[i].type != EVT_EXERCISE) continue;
        if (t >= events[i].time_min && t < events[i].time_min + events[i].duration_min)
            return events[i].value;
    }
    return 0.0;
}

static double active_hr_bpm(const Event *events, size_t n, double t, double baseline) {
    for (size_t i = 0; i < n; i++) {
        if (events[i].type != EVT_HR) continue;
        if (t >= events[i].time_min && t < events[i].time_min + events[i].duration_min)
            return events[i].value;
    }
    return baseline;
}

/* ── Main ─────────────────────────────────────────────────────────────── */

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s <scenario.json> [output.csv]\n", argv[0]);
        return 1;
    }

    JsonValue *scenario = json_parse_file(argv[1]);
    if (!scenario) {
        fprintf(stderr, "error: could not read/parse scenario file '%s'\n", argv[1]);
        return 1;
    }

    const char *model_name = json_string(json_object_get(scenario, "model"), "");
    Model model;
    if (!model_kind_from_name(model_name, &model.kind)) {
        fprintf(stderr, "error: unknown model '%s' (expected cambridge, uva_padova, "
                        "royparker or deichmann)\n", model_name);
        json_free(scenario);
        return 1;
    }

    const char *sensor_name = json_string(json_object_get(scenario, "sensor"), "ideal_cgm");
    Sensor sensor;
    if (!sensor_kind_from_name(sensor_name, &sensor.kind)) {
        fprintf(stderr, "error: unknown sensor '%s' (expected ideal_cgm, breton or "
                        "facchinetti)\n", sensor_name);
        json_free(scenario);
        return 1;
    }

    double duration_min = json_number(json_object_get(scenario, "duration_min"), 480.0);
    double dt_min       = json_number(json_object_get(scenario, "dt_min"), 1.0);
    unsigned int seed   = (unsigned int)json_number(json_object_get(scenario, "seed"), 42.0);

    const char *params_path = json_string(json_object_get(scenario, "params_file"),
                                          default_params_path(model.kind));

    const char *output_path = (argc >= 3) ? argv[2]
        : json_string(json_object_get(scenario, "output_file"), "output/glucose_log.csv");

    size_t n_events;
    Event *events = load_events(scenario, &n_events);

    model_init(&model, params_path);
    sensor_init(&sensor, seed);

    FILE *out = fopen(output_path, "w");
    if (!out) {
        fprintf(stderr, "error: could not open output file '%s' for writing\n", output_path);
        free(events);
        json_free(scenario);
        return 1;
    }

    fprintf(out, "t_min,glucose_true_mg_dl,sensor_mg_dl,sensor_valid,"
                 "carbs_g_step,iir_u_per_h,exercise_pct,hr_bpm\n");

    double basal_fallback = model_default_basal_u_per_h(&model);
    double hr_baseline    = model_hr_basal(&model);

    int n_steps = (int)(duration_min / dt_min + 0.5);
    for (int i = 0; i < n_steps; i++) {
        double t = i * dt_min;

        double carbs_rate   = active_carb_rate(events, n_events, t);
        double carbs_step_g = (model.kind == MODEL_ROYPARKER)
                               ? meal_grams_at_step(events, n_events, t, dt_min)
                               : carbs_rate * dt_min;
        double basal   = current_basal(events, n_events, t, basal_fallback);
        double bolus   = active_bolus_u_per_h(events, n_events, t);
        double iir     = basal + bolus;
        double exercise = active_exercise_pct(events, n_events, t);
        double hr       = active_hr_bpm(events, n_events, t, hr_baseline);

        double glucose = model_step(&model, carbs_step_g, carbs_rate, iir,
                                     exercise, hr, t, dt_min);
        CGMReading reading = sensor_update(&sensor, glucose, dt_min);

        fprintf(out, "%.4f,%.4f,%.4f,%d,%.4f,%.4f,%.4f,%.4f\n",
                t, glucose,
                reading.value_mg_dl, reading.valid,
                carbs_step_g, iir, exercise, hr);
    }

    fclose(out);
    free(events);

    printf("cgmsim: model=%s sensor=%s duration=%.0fmin dt=%.1fmin -> %s\n",
           model_name, sensor_name, duration_min, dt_min, output_path);

    json_free(scenario);
    return 0;
}
