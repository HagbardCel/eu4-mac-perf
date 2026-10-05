#include "eu4_submission_border_loop_head.h"

#include "eu4_submission_border.h"
#include "eu4_submission_observation.h"
#include "submission_counter_schema.h"

#include <OpenGL/OpenGL.h>
#include <dlfcn.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

extern void eu4_submission_counter_add(eu4_submission_counter_slot_t slot, uint64_t delta);

typedef void (*glMultiDrawElements_fn)(GLenum, const GLsizei *, GLenum, const void *const *, GLsizei);

static glMultiDrawElements_fn real_gl_multidraw = NULL;
static bool gate0_recorded = false;

static _Thread_local uint32_t tls_semantic_suppress = 0;
static _Thread_local uint32_t tls_implementable_suppress = 0;
static _Thread_local uint32_t tls_structural_draws_walk = 0;
static _Thread_local uint64_t tls_semantic_elim = 0;
static _Thread_local uint64_t tls_implementable_elim = 0;
static _Thread_local uint64_t tls_semantic_decisions = 0;
static _Thread_local uint64_t tls_implementable_decisions = 0;
static _Thread_local uint64_t tls_structural_draws = 0;
static _Thread_local uint64_t tls_structural_elim = 0;
static _Thread_local uint64_t tls_structural_walks = 0;
static _Thread_local uint64_t tls_loop_head_entries = 0;
static _Thread_local uint64_t tls_suppression_diag = 0;

static void record_gate0_once(void) {
    if (gate0_recorded) {
        return;
    }
    gate0_recorded = true;
    eu4_border_init_gl_apis();
    if (real_gl_multidraw == NULL) {
        real_gl_multidraw = (glMultiDrawElements_fn)dlsym(RTLD_NEXT, "glMultiDrawElements");
    }
    const bool context_ok = CGLGetCurrentContext() != NULL;
    const bool symbol_ok = real_gl_multidraw != NULL;
    eu4_submission_counter_add(EU4_COUNTER_BORDER_RUNTIME_CONTEXT_CHECKED, 1);
    if (context_ok && symbol_ok) {
        eu4_submission_counter_add(EU4_COUNTER_BORDER_RUNTIME_CONTEXT_MULTIDRAW_SUPPORTED, 1);
    }
}

static void apply_decision(
    const eu4_border_prefix_result_t *semantic,
    const eu4_border_prefix_result_t *implementable) {
    if (tls_semantic_suppress > 0) {
        tls_semantic_suppress--;
    } else if (semantic->batch_eligible) {
        tls_semantic_decisions++;
        tls_semantic_elim += semantic->eligible_draw_calls_eliminable;
        tls_semantic_suppress = semantic->run_length > 0 ? semantic->run_length - 1u : 0u;
    } else {
        tls_semantic_suppress = 0;
    }

    if (tls_implementable_suppress > 0) {
        tls_implementable_suppress--;
    } else if (implementable->batch_eligible) {
        tls_implementable_decisions++;
        tls_implementable_elim += implementable->eligible_draw_calls_eliminable;
        tls_implementable_suppress = implementable->run_length > 0 ? implementable->run_length - 1u : 0u;
    } else {
        tls_implementable_suppress = 0;
    }
}

static void flush_structural_walk(void) {
    if (tls_structural_draws_walk > 0) {
        tls_structural_walks++;
        tls_structural_draws += tls_structural_draws_walk;
        if (tls_structural_draws_walk > 1) {
            tls_structural_elim += tls_structural_draws_walk - 1u;
        }
        tls_structural_draws_walk = 0;
    }
}

void eu4_border_loop_head_epoch_reset(void) {
    tls_semantic_suppress = 0;
    tls_implementable_suppress = 0;
    tls_structural_draws_walk = 0;
    tls_semantic_elim = 0;
    tls_implementable_elim = 0;
    tls_semantic_decisions = 0;
    tls_implementable_decisions = 0;
    tls_structural_draws = 0;
    tls_structural_elim = 0;
    tls_structural_walks = 0;
    tls_loop_head_entries = 0;
    tls_suppression_diag = 0;
    gate0_recorded = false;
}

void eu4_border_loop_head_test_reset_tls(void) {
    eu4_border_loop_head_epoch_reset();
}

uint64_t eu4_border_loop_head_test_tls_semantic_eliminations(void) {
    return tls_semantic_elim;
}

uint64_t eu4_border_loop_head_test_tls_implementable_eliminations(void) {
    return tls_implementable_elim;
}

void eu4_border_loop_head_record_structural_submission(bool mode0_positive_count) {
    if (!eu4_submission_observation_bank_active()) {
        return;
    }
    if (mode0_positive_count) {
        tls_structural_draws_walk++;
    }
}

void eu4_border_loop_head_on_armed_hit(
    const eu4_border_loop_context_t *ctx,
    const eu4_border_record_view_t *record_table,
    const uint32_t *ibo_table,
    uint32_t record_count,
    const eu4_border_index_entry_t *side_entries,
    uint32_t side_count,
    bool walk_end) {
    if (!eu4_submission_observation_bank_active()) {
        return;
    }
    record_gate0_once();
    tls_loop_head_entries++;

    eu4_border_prefix_result_t semantic;
    eu4_border_prefix_result_t implementable;
    eu4_border_classify_batchable_prefix(
        ctx,
        record_table,
        ibo_table,
        record_count,
        side_entries,
        side_count,
        EU4_BORDER_IBO_ONE_BIND,
        EU4_BORDER_V1_SCAN_CAP,
        true,
        true,
        &semantic);
    eu4_border_classify_batchable_prefix(
        ctx,
        record_table,
        ibo_table,
        record_count,
        side_entries,
        side_count,
        EU4_BORDER_IBO_ONE_BIND,
        EU4_BORDER_V1_SCAN_CAP,
        false,
        true,
        &implementable);

    apply_decision(&semantic, &implementable);

    if (walk_end) {
        flush_structural_walk();
    }
}

void eu4_border_loop_head_flush_pending(void) {
    if (!eu4_submission_observation_bank_active()) {
        return;
    }
    if (tls_semantic_suppress > 0 || tls_implementable_suppress > 0) {
        tls_suppression_diag++;
    }
    flush_structural_walk();

    if (tls_loop_head_entries > 0) {
        eu4_submission_counter_add(EU4_COUNTER_BORDER_LOOP_HEAD_ENTRIES, tls_loop_head_entries);
        tls_loop_head_entries = 0;
    }
    if (tls_semantic_decisions > 0) {
        eu4_submission_counter_add(EU4_COUNTER_BORDER_SEMANTIC_DECISIONS, tls_semantic_decisions);
        tls_semantic_decisions = 0;
    }
    if (tls_semantic_elim > 0) {
        eu4_submission_counter_add(EU4_COUNTER_BORDER_SEMANTIC_ELIMINATIONS, tls_semantic_elim);
        tls_semantic_elim = 0;
    }
    if (tls_implementable_decisions > 0) {
        eu4_submission_counter_add(EU4_COUNTER_BORDER_IMPLEMENTABLE_DECISIONS, tls_implementable_decisions);
        tls_implementable_decisions = 0;
    }
    if (tls_implementable_elim > 0) {
        eu4_submission_counter_add(EU4_COUNTER_BORDER_IMPLEMENTABLE_ELIMINATIONS, tls_implementable_elim);
        tls_implementable_elim = 0;
    }
    if (tls_structural_draws > 0) {
        eu4_submission_counter_add(EU4_COUNTER_BORDER_STRUCTURAL_DRAWS, tls_structural_draws);
        tls_structural_draws = 0;
    }
    if (tls_structural_elim > 0) {
        eu4_submission_counter_add(EU4_COUNTER_BORDER_STRUCTURAL_ELIMINATIONS, tls_structural_elim);
        tls_structural_elim = 0;
    }
    if (tls_structural_walks > 0) {
        eu4_submission_counter_add(EU4_COUNTER_BORDER_STRUCTURAL_WALKS, tls_structural_walks);
        tls_structural_walks = 0;
    }
    if (tls_suppression_diag > 0) {
        eu4_submission_counter_add(
            EU4_COUNTER_BORDER_ROI_NONZERO_SUPPRESSION_AT_FRAME_BOUNDARY, tls_suppression_diag);
        tls_suppression_diag = 0;
    }
}
