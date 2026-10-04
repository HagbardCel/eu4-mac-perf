#include "eu4_submission_border.h"

#include "submission_counter_schema.h"

#include <dlfcn.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>

extern void eu4_submission_counter_add(eu4_submission_counter_slot_t slot, uint64_t delta);

typedef void (*glDrawElementsBaseVertex_fn)(
    GLenum mode,
    GLsizei count,
    GLenum type,
    const void *indices,
    GLint basevertex);

typedef void (*glMultiDrawElementsBaseVertex_fn)(
    GLenum mode,
    const GLsizei *count,
    GLenum type,
    const void *const *indices,
    const GLint *basevertex,
    GLsizei drawcount);

static glDrawElementsBaseVertex_fn real_draw = NULL;
static glMultiDrawElementsBaseVertex_fn real_multidraw = NULL;
static bool api_ready = false;

static _Thread_local GLsizei tls_counts[EU4_BORDER_MAX_BATCH];
static _Thread_local GLint tls_basevertex[EU4_BORDER_MAX_BATCH];
static _Thread_local const void *tls_indices[EU4_BORDER_MAX_BATCH];
static _Thread_local uint32_t tls_batch_len = 0;
static _Thread_local uint32_t tls_batch_max = 0;
static _Thread_local uint32_t tls_batch_site = UINT32_MAX;
static _Thread_local GLenum tls_batch_mode = 0;
static _Thread_local GLenum tls_batch_type = 0;

bool eu4_border_gl_api_ready(void) {
    return api_ready;
}

void eu4_border_init_gl_apis(void) {
    if (real_draw != NULL) {
        return;
    }
    real_draw = (glDrawElementsBaseVertex_fn)dlsym(RTLD_NEXT, "glDrawElementsBaseVertex");
    real_multidraw =
        (glMultiDrawElementsBaseVertex_fn)dlsym(RTLD_NEXT, "glMultiDrawElementsBaseVertex");
    api_ready = real_draw != NULL && real_multidraw != NULL;
}

extern uint32_t g_eu4_border_control_flags;

bool eu4_border_minimal_hook(void) {
    return (g_eu4_border_control_flags & 1u) != 0;
}

bool eu4_border_mutate_enabled(void) {
    if (!api_ready) {
        return false;
    }
    return (g_eu4_border_control_flags & 2u) != 0;
}

void eu4_border_reset_batch(void) {
    tls_batch_len = 0;
    tls_batch_site = UINT32_MAX;
    tls_batch_max = 0;
}

void eu4_submission_observation_arm_reset(void) {
    eu4_border_flush_pending();
    eu4_border_reset_batch();
}

static void record_batch(uint32_t site_id, uint32_t n) {
    if (n == 0) {
        return;
    }
    eu4_submission_counter_add(EU4_COUNTER_BORDER_MULTIDRAW_CALLS, 1);
    eu4_submission_counter_add(EU4_COUNTER_BORDER_DRAWS_COVERED_BY_MULTIDRAW, n);
    if (n > 1) {
        eu4_submission_counter_add(EU4_COUNTER_BORDER_DRAW_CALLS_ELIMINATED, n - 1);
    }
    eu4_submission_counter_add(EU4_COUNTER_BORDER_BATCH_SIZE_SUM, n);
    if (n > tls_batch_max) {
        tls_batch_max = n;
        eu4_submission_counter_add(EU4_COUNTER_BORDER_BATCH_SIZE_MAX, n);
    }
    if (site_id == 0) {
        eu4_submission_counter_add(EU4_COUNTER_BORDER_SITE_0_DRAWS_COVERED, n);
    } else if (site_id == 1) {
        eu4_submission_counter_add(EU4_COUNTER_BORDER_SITE_1_DRAWS_COVERED, n);
    } else if (site_id == 2) {
        eu4_submission_counter_add(EU4_COUNTER_BORDER_SITE_2_DRAWS_COVERED, n);
    }
}

static void flush_batch(void) {
    if (tls_batch_len == 0 || real_draw == NULL) {
        eu4_border_reset_batch();
        return;
    }
    if (tls_batch_len == 1) {
        real_draw(tls_batch_mode, tls_counts[0], tls_batch_type, tls_indices[0], tls_basevertex[0]);
        record_batch(tls_batch_site, 1);
    } else {
        real_multidraw(
            tls_batch_mode,
            tls_counts,
            tls_batch_type,
            tls_indices,
            tls_basevertex,
            (GLsizei)tls_batch_len);
        record_batch(tls_batch_site, tls_batch_len);
    }
    eu4_border_reset_batch();
}

static void fallback_single(
    GLenum mode,
    GLsizei count,
    GLenum type,
    const void *indices,
    GLint basevertex) {
    eu4_submission_counter_add(EU4_COUNTER_BORDER_ORIGINAL_DRAWS_FALLBACK, 1);
    if (real_draw != NULL) {
        real_draw(mode, count, type, indices, basevertex);
    }
}

void eu4_border_on_draw_elements_base_vertex(
    GLenum mode,
    GLsizei count,
    GLenum type,
    const void *indices,
    GLint basevertex,
    uint32_t site_id) {
    if (real_draw == NULL) {
        return;
    }
    if (eu4_border_minimal_hook()) {
        real_draw(mode, count, type, indices, basevertex);
        return;
    }

    eu4_submission_counter_add(EU4_COUNTER_BORDER_CANDIDATE_DRAWS, 1);

    if (site_id > 2) {
        fallback_single(mode, count, type, indices, basevertex);
        return;
    }

    const bool compatible = tls_batch_len > 0 && tls_batch_site == site_id && tls_batch_mode == mode &&
                            tls_batch_type == type;
    if (tls_batch_len > 0 && !compatible) {
        eu4_submission_counter_add(EU4_COUNTER_BORDER_FALLBACK_SITE_CHANGE, 1);
        eu4_border_reset_batch();
    }
    if (tls_batch_len == 0) {
        tls_batch_site = site_id;
        tls_batch_mode = mode;
        tls_batch_type = type;
        eu4_submission_counter_add(EU4_COUNTER_BORDER_CANDIDATE_RUNS, 1);
    }
    if (tls_batch_len < EU4_BORDER_MAX_BATCH) {
        const uint32_t i = tls_batch_len++;
        tls_counts[i] = count;
        tls_basevertex[i] = basevertex;
        tls_indices[i] = indices;
    } else {
        eu4_submission_counter_add(EU4_COUNTER_BORDER_FALLBACK_BATCH_FULL, 1);
    }

    if (!eu4_border_mutate_enabled()) {
        real_draw(mode, count, type, indices, basevertex);
        eu4_border_reset_batch();
        return;
    }

    if (tls_batch_len >= 2) {
        real_multidraw(
            tls_batch_mode,
            tls_counts,
            tls_batch_type,
            tls_indices,
            tls_basevertex,
            (GLsizei)tls_batch_len);
        record_batch(tls_batch_site, tls_batch_len);
        eu4_border_reset_batch();
    }
}

void eu4_border_flush_pending(void) {
    flush_batch();
}
