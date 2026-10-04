#define GL_SILENCE_DEPRECATION 1
#include "eu4_submission_border.h"

#include "submission_counter_schema.h"

#include <OpenGL/gl.h>

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
static glMultiDrawElementsBaseVertex_fn real_multidraw __attribute__((unused)) = NULL;
static bool symbols_resolved = false;
static bool runtime_context_verified = false;

static _Thread_local uint64_t tls_unpublished_draws = 0;
static _Thread_local uint32_t tls_unpublished_site0 = 0;

bool eu4_border_gl_api_ready(void) {
    return symbols_resolved && runtime_context_verified;
}

static bool verify_active_gl_context_supports_multidraw(void) {
    const char *version = (const char *)glGetString(GL_VERSION);
    if (version == NULL) {
        return false;
    }
    return strstr(version, "2.") != NULL || strstr(version, "3.") != NULL || strstr(version, "4.") != NULL;
}

void eu4_border_init_gl_apis(void) {
    if (real_draw != NULL) {
        return;
    }
    real_draw = (glDrawElementsBaseVertex_fn)dlsym(RTLD_NEXT, "glDrawElementsBaseVertex");
    real_multidraw =
        (glMultiDrawElementsBaseVertex_fn)dlsym(RTLD_NEXT, "glMultiDrawElementsBaseVertex");
    symbols_resolved = real_draw != NULL && real_multidraw != NULL;
}

extern uint32_t g_eu4_border_control_flags;

bool eu4_border_minimal_hook(void) {
    return (g_eu4_border_control_flags & 1u) != 0;
}

bool eu4_border_mutate_enabled(void) {
    /* Mutation requires loop-head/run-head patch (PR B NO-GO for GL-tail defer). */
    (void)g_eu4_border_control_flags;
    return false;
}

void eu4_border_publish_tls_counters(void) {
    if (tls_unpublished_draws > 0) {
        eu4_submission_counter_add(EU4_COUNTER_BORDER_CANDIDATE_DRAWS, tls_unpublished_draws);
        tls_unpublished_draws = 0;
    }
    if (tls_unpublished_site0 > 0) {
        eu4_submission_counter_add(EU4_COUNTER_BORDER_SITE_0_DRAWS_COVERED, tls_unpublished_site0);
        tls_unpublished_site0 = 0;
    }
}

void eu4_border_reset_batch(void) {
    eu4_border_publish_tls_counters();
}

void eu4_submission_observation_arm_reset(void) {
    eu4_border_publish_tls_counters();
}

static void note_border_draw(uint32_t site_id) {
    tls_unpublished_draws++;
    if (site_id == 0) {
        tls_unpublished_site0++;
    }
    if ((g_eu4_border_control_flags & 2u) != 0) {
        eu4_submission_counter_add(EU4_COUNTER_BORDER_FALLBACK_MUTATE_DISABLED, 1);
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
    if (!runtime_context_verified) {
        runtime_context_verified = verify_active_gl_context_supports_multidraw();
    }
    if (eu4_border_minimal_hook()) {
        real_draw(mode, count, type, indices, basevertex);
        return;
    }

    if (site_id > 2) {
        real_draw(mode, count, type, indices, basevertex);
        return;
    }

    /* Observer only: always issue the original draw immediately (no deferred batching). */
    note_border_draw(site_id);
    real_draw(mode, count, type, indices, basevertex);
}

void eu4_border_flush_pending(void) {
    eu4_border_publish_tls_counters();
}
