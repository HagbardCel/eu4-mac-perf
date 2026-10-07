#include "eu4_submission_border_loop_head.h"

#include "eu4_border_loop_head_decode.h"
#include "eu4_submission_border.h"
#include "eu4_submission_observation.h"
#include "submission_counter_schema.h"

#include <OpenGL/OpenGL.h>
#include <dlfcn.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

extern void eu4_submission_counter_add(eu4_submission_counter_slot_t slot, uint64_t delta);

typedef void (*glMultiDrawElements_fn)(GLenum, const GLsizei *, GLenum, const void *const *, GLsizei);

static glMultiDrawElements_fn real_gl_multidraw = NULL;
static bool gate0_recorded = false;
static _Atomic uint64_t g_roi_loop_thread_id = 0;
static _Atomic bool g_roi_epoch_invalid = false;

static _Thread_local uint32_t tls_semantic_suppress = 0;
static _Thread_local uint32_t tls_implementable_suppress = 0;
static _Thread_local uint32_t tls_structural_draws_walk = 0;
static _Thread_local uint64_t tls_semantic_elim = 0;
static _Thread_local uint64_t tls_implementable_elim = 0;
static _Thread_local uint64_t tls_semantic_decisions = 0;
static _Thread_local uint64_t tls_implementable_decisions = 0;
static _Thread_local uint64_t tls_semantic_evaluations = 0;
static _Thread_local uint64_t tls_implementable_evaluations = 0;
static _Thread_local uint64_t tls_structural_draws = 0;
static _Thread_local uint64_t tls_structural_elim = 0;
static _Thread_local uint64_t tls_structural_walks = 0;
static _Thread_local uint64_t tls_loop_head_entries = 0;
static _Thread_local uint64_t tls_suppression_diag = 0;
static _Thread_local uint64_t tls_partial_walk_diag = 0;
static _Thread_local uint64_t tls_decode_failures = 0;
static _Thread_local uint64_t tls_geometry_failures = 0;
static _Thread_local uint64_t tls_resolution_failures = 0;
static _Thread_local uint64_t tls_thread_checks = 0;
static _Thread_local uint64_t tls_census_mode0_entries = 0;
static _Thread_local uint64_t tls_census_mode1_entries = 0;
static _Thread_local uint64_t tls_census_mode_other_entries = 0;
static _Thread_local uint64_t tls_census_mode0_visible = 0;
static _Thread_local uint64_t tls_census_mode1_visible = 0;
static _Thread_local uint64_t tls_census_mode_other_visible = 0;
static _Thread_local uint64_t tls_census_mode0_vnz = 0;
static _Thread_local uint64_t tls_census_mode1_vnz = 0;
static _Thread_local uint64_t tls_census_mode_other_vnz = 0;
static _Thread_local eu4_border_resolved_step_t tls_resolved_steps[EU4_BORDER_MAX_RESOLVE_STEPS];

static void record_domain_census(
    const eu4_border_loop_context_t *ctx,
    const eu4_border_resolved_step_t *current,
    uint8_t visibility_mask) {
    if (!ctx || !current) {
        return;
    }
    const bool vis = (visibility_mask & current->visibility_byte) != 0u;
    const bool vnz = vis && current->triangle_count > 0u;
    if (ctx->mode == 0u) {
        tls_census_mode0_entries++;
        if (vis) {
            tls_census_mode0_visible++;
        }
        if (vnz) {
            tls_census_mode0_vnz++;
        }
    } else if (ctx->mode == 1u) {
        tls_census_mode1_entries++;
        if (vis) {
            tls_census_mode1_visible++;
        }
        if (vnz) {
            tls_census_mode1_vnz++;
        }
    } else {
        tls_census_mode_other_entries++;
        if (vis) {
            tls_census_mode_other_visible++;
        }
        if (vnz) {
            tls_census_mode_other_vnz++;
        }
    }
}

static void flush_census_tls(void) {
    if (tls_census_mode0_entries > 0) {
        eu4_submission_counter_add(EU4_COUNTER_BORDER_MODE0_ENTRIES, tls_census_mode0_entries);
        tls_census_mode0_entries = 0;
    }
    if (tls_census_mode1_entries > 0) {
        eu4_submission_counter_add(EU4_COUNTER_BORDER_MODE1_ENTRIES, tls_census_mode1_entries);
        tls_census_mode1_entries = 0;
    }
    if (tls_census_mode_other_entries > 0) {
        eu4_submission_counter_add(EU4_COUNTER_BORDER_MODE_OTHER_ENTRIES, tls_census_mode_other_entries);
        tls_census_mode_other_entries = 0;
    }
    if (tls_census_mode0_visible > 0) {
        eu4_submission_counter_add(EU4_COUNTER_BORDER_MODE0_VISIBLE_ENTRIES, tls_census_mode0_visible);
        tls_census_mode0_visible = 0;
    }
    if (tls_census_mode1_visible > 0) {
        eu4_submission_counter_add(EU4_COUNTER_BORDER_MODE1_VISIBLE_ENTRIES, tls_census_mode1_visible);
        tls_census_mode1_visible = 0;
    }
    if (tls_census_mode_other_visible > 0) {
        eu4_submission_counter_add(EU4_COUNTER_BORDER_MODE_OTHER_VISIBLE_ENTRIES, tls_census_mode_other_visible);
        tls_census_mode_other_visible = 0;
    }
    if (tls_census_mode0_vnz > 0) {
        eu4_submission_counter_add(
            EU4_COUNTER_BORDER_MODE0_VISIBLE_NONZERO_TRIANGLE_ENTRIES, tls_census_mode0_vnz);
        tls_census_mode0_vnz = 0;
    }
    if (tls_census_mode1_vnz > 0) {
        eu4_submission_counter_add(
            EU4_COUNTER_BORDER_MODE1_VISIBLE_NONZERO_TRIANGLE_ENTRIES, tls_census_mode1_vnz);
        tls_census_mode1_vnz = 0;
    }
    if (tls_census_mode_other_vnz > 0) {
        eu4_submission_counter_add(
            EU4_COUNTER_BORDER_MODE_OTHER_VISIBLE_NONZERO_TRIANGLE_ENTRIES, tls_census_mode_other_vnz);
        tls_census_mode_other_vnz = 0;
    }
}

static uint64_t current_thread_id(void) {
    uint64_t tid = 0;
    (void)pthread_threadid_np(NULL, &tid);
    return tid;
}

static bool check_thread_affinity(void) {
    const uint64_t tid = current_thread_id();
    tls_thread_checks++;
    uint64_t expected = 0;
    if (atomic_compare_exchange_strong(&g_roi_loop_thread_id, &expected, tid)) {
        return true;
    }
    if (atomic_load_explicit(&g_roi_loop_thread_id, memory_order_relaxed) != tid) {
        eu4_submission_counter_add(EU4_COUNTER_BORDER_THREAD_MISMATCH_COUNT, 1);
        atomic_store_explicit(&g_roi_epoch_invalid, true, memory_order_relaxed);
        return false;
    }
    return true;
}

static void invalidate_epoch_clear_tls(void) {
    tls_semantic_suppress = 0;
    tls_implementable_suppress = 0;
    tls_structural_draws_walk = 0;
    atomic_store_explicit(&g_roi_epoch_invalid, true, memory_order_relaxed);
}

static void record_decode_failure(bool geometry, bool resolution) {
    tls_decode_failures++;
    if (geometry) {
        tls_geometry_failures++;
    }
    if (resolution) {
        tls_resolution_failures++;
    }
    invalidate_epoch_clear_tls();
}

static void record_gate0_once(void) {
    if (gate0_recorded) {
        return;
    }
    gate0_recorded = true;
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

static void record_run_length_histogram(bool semantic, uint32_t run_length) {
    eu4_submission_counter_slot_t slot;
    if (run_length < 2u) {
        return;
    }
    if (run_length == 2u) {
        slot = semantic ? EU4_COUNTER_BORDER_SEMANTIC_RUN_LEN_2 : EU4_COUNTER_BORDER_IMPLEMENTABLE_RUN_LEN_2;
    } else if (run_length <= 4u) {
        slot = semantic ? EU4_COUNTER_BORDER_SEMANTIC_RUN_LEN_3_4 : EU4_COUNTER_BORDER_IMPLEMENTABLE_RUN_LEN_3_4;
    } else if (run_length <= 8u) {
        slot = semantic ? EU4_COUNTER_BORDER_SEMANTIC_RUN_LEN_5_8 : EU4_COUNTER_BORDER_IMPLEMENTABLE_RUN_LEN_5_8;
    } else if (run_length <= 16u) {
        slot = semantic ? EU4_COUNTER_BORDER_SEMANTIC_RUN_LEN_9_16 : EU4_COUNTER_BORDER_IMPLEMENTABLE_RUN_LEN_9_16;
    } else if (run_length <= 32u) {
        slot = semantic ? EU4_COUNTER_BORDER_SEMANTIC_RUN_LEN_17_32 : EU4_COUNTER_BORDER_IMPLEMENTABLE_RUN_LEN_17_32;
    } else if (run_length <= 64u) {
        slot = semantic ? EU4_COUNTER_BORDER_SEMANTIC_RUN_LEN_33_64 : EU4_COUNTER_BORDER_IMPLEMENTABLE_RUN_LEN_33_64;
    } else if (run_length <= 128u) {
        slot = semantic ? EU4_COUNTER_BORDER_SEMANTIC_RUN_LEN_65_128 : EU4_COUNTER_BORDER_IMPLEMENTABLE_RUN_LEN_65_128;
    } else {
        slot = semantic ? EU4_COUNTER_BORDER_SEMANTIC_RUN_LEN_129_PLUS : EU4_COUNTER_BORDER_IMPLEMENTABLE_RUN_LEN_129_PLUS;
    }
    eu4_submission_counter_add(slot, 1);
}

static void apply_decision(
    const eu4_border_prefix_result_t *semantic,
    const eu4_border_prefix_result_t *implementable) {
    if (tls_semantic_suppress > 0) {
        tls_semantic_suppress--;
    } else {
        tls_semantic_evaluations++;
        if (semantic->batch_eligible) {
            record_run_length_histogram(true, semantic->run_length);
            tls_semantic_decisions++;
            tls_semantic_elim += semantic->eligible_draw_calls_eliminable;
            tls_semantic_suppress = semantic->run_length > 0 ? semantic->run_length - 1u : 0u;
        } else {
            tls_semantic_suppress = 0;
        }
    }
    if (tls_implementable_suppress > 0) {
        tls_implementable_suppress--;
    } else {
        tls_implementable_evaluations++;
        if (implementable->batch_eligible) {
            record_run_length_histogram(false, implementable->run_length);
            tls_implementable_decisions++;
            tls_implementable_elim += implementable->eligible_draw_calls_eliminable;
            tls_implementable_suppress = implementable->run_length > 0 ? implementable->run_length - 1u : 0u;
        } else {
            tls_implementable_suppress = 0;
        }
    }
}

static void close_structural_walk(void) {
    tls_structural_walks++;
    tls_structural_draws += tls_structural_draws_walk;
    if (tls_structural_draws_walk > 1) {
        tls_structural_elim += tls_structural_draws_walk - 1u;
    }
    tls_structural_draws_walk = 0;
}

void eu4_border_loop_head_epoch_reset(void) {
    atomic_store_explicit(&g_roi_loop_thread_id, 0, memory_order_relaxed);
    atomic_store_explicit(&g_roi_epoch_invalid, false, memory_order_relaxed);
    tls_semantic_suppress = 0;
    tls_implementable_suppress = 0;
    tls_structural_draws_walk = 0;
    tls_semantic_elim = 0;
    tls_implementable_elim = 0;
    tls_semantic_decisions = 0;
    tls_implementable_decisions = 0;
    tls_semantic_evaluations = 0;
    tls_implementable_evaluations = 0;
    tls_structural_draws = 0;
    tls_structural_elim = 0;
    tls_structural_walks = 0;
    tls_loop_head_entries = 0;
    tls_suppression_diag = 0;
    tls_partial_walk_diag = 0;
    tls_decode_failures = 0;
    tls_geometry_failures = 0;
    tls_resolution_failures = 0;
    tls_thread_checks = 0;
    tls_census_mode0_entries = 0;
    tls_census_mode1_entries = 0;
    tls_census_mode_other_entries = 0;
    tls_census_mode0_visible = 0;
    tls_census_mode1_visible = 0;
    tls_census_mode_other_visible = 0;
    tls_census_mode0_vnz = 0;
    tls_census_mode1_vnz = 0;
    tls_census_mode_other_vnz = 0;
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

bool eu4_border_loop_head_test_epoch_invalid(void) {
    return atomic_load_explicit(&g_roi_epoch_invalid, memory_order_relaxed);
}

uint64_t eu4_border_loop_head_test_tls_semantic_evaluations(void) {
    return tls_semantic_evaluations;
}

uint64_t eu4_border_loop_head_test_tls_implementable_evaluations(void) {
    return tls_implementable_evaluations;
}

uint64_t eu4_border_loop_head_test_tls_semantic_decisions(void) {
    return tls_semantic_decisions;
}

uint64_t eu4_border_loop_head_test_tls_implementable_decisions(void) {
    return tls_implementable_decisions;
}

void eu4_border_loop_head_test_on_armed_hit_suffix(
    const eu4_border_loop_context_t *ctx,
    const eu4_border_record_view_t *record_table,
    const uintptr_t *ibo_table,
    uint32_t record_count,
    const eu4_border_index_entry_t *side_entries,
    uint32_t side_count,
    bool walk_end) {
    eu4_border_loop_head_on_armed_hit(
        ctx, record_table, ibo_table, record_count, side_entries, side_count, walk_end);
}

void eu4_border_loop_head_on_armed_hit(
    const eu4_border_loop_context_t *ctx,
    const eu4_border_record_view_t *record_table,
    const uintptr_t *ibo_table,
    uint32_t record_count,
    const eu4_border_index_entry_t *side_entries,
    uint32_t side_count,
    bool walk_end) {
    if (!eu4_submission_observation_bank_active() || !check_thread_affinity()) {
        return;
    }
    record_gate0_once();
    tls_loop_head_entries++;
    eu4_border_prefix_result_t semantic;
    eu4_border_prefix_result_t implementable;
    eu4_border_resolved_step_t *steps = tls_resolved_steps;
    if (side_count > EU4_BORDER_MAX_RESOLVE_STEPS) {
        record_decode_failure(true, false);
        return;
    }
    for (uint32_t i = 0; i < side_count; i++) {
        const eu4_border_index_entry_t *ent = &side_entries[i];
        eu4_border_resolved_step_t *step = &steps[i];
        step->record_index = (uint16_t)ent->record_index;
        step->visibility_byte = ent->visibility_byte;
        step->color_byte = ent->color_byte;
        step->resolved_valid = false;
        if ((ctx->skip_or_visibility_mask & ent->visibility_byte) == 0u) {
            step->resolved_valid = true;
            continue;
        }
        if (ent->record_index < 0 || (uint32_t)ent->record_index >= record_count) {
            record_decode_failure(false, true);
            return;
        }
        step->triangle_count = (uint16_t)record_table[ent->record_index].triangle_count;
        step->vbo_table_index = (uint16_t)record_table[ent->record_index].vbo_table_index;
        step->ibo_identity = ibo_table[ent->record_index];
        step->ibo_argument = ibo_table[ent->record_index];
        step->resolved_valid = true;
    }
    if (side_count > 0) {
        record_domain_census(ctx, &steps[0], ctx->skip_or_visibility_mask);
    }
    if (side_count > 0 && ctx->mode == 0) {
        const eu4_border_resolved_step_t *cur = &steps[0];
        if ((ctx->skip_or_visibility_mask & cur->visibility_byte) != 0 && cur->resolved_valid &&
            cur->triangle_count > 0) {
            tls_structural_draws_walk++;
        }
    }
    eu4_border_classify_resolved_prefix(
        ctx, steps, side_count, EU4_BORDER_IBO_ONE_BIND, 0, true, true, &semantic);
    eu4_border_classify_resolved_prefix(
        ctx, steps, side_count, EU4_BORDER_IBO_ONE_BIND, EU4_BORDER_V1_SCAN_CAP, false, true, &implementable);
    apply_decision(&semantic, &implementable);
    if (walk_end) {
        close_structural_walk();
    }
}

void eu4_border_loop_head_observe_frame(void *rbp, void *r12, uint64_t r13_full, void *rcx) {
    const uint8_t visibility_mask = (uint8_t)(r13_full & 0xffu);
    if (!eu4_submission_observation_bank_active()) {
        return;
    }
    if (!check_thread_affinity()) {
        return;
    }
    const uintptr_t cursor = (uintptr_t)rcx;
    const uintptr_t end_bound = *(const uintptr_t *)((uintptr_t)rbp - 0x198u);
    eu4_border_walk_geometry_t geom;
    if (!eu4_border_decode_walk_geometry(cursor, end_bound, &geom)) {
        record_decode_failure(true, false);
        return;
    }
    if (geom.remaining > EU4_BORDER_MAX_RESOLVE_STEPS) {
        record_decode_failure(true, false);
        return;
    }
    eu4_border_loop_context_t ctx;
    if (!eu4_border_decode_loop_context(rbp, r12, visibility_mask, &ctx)) {
        record_decode_failure(false, true);
        return;
    }
    eu4_border_resolved_step_t current;
    if (!eu4_border_resolve_step_at_cursor(rbp, r12, cursor, visibility_mask, &current)) {
        record_decode_failure(false, true);
        return;
    }
    record_gate0_once();
    tls_loop_head_entries++;
    record_domain_census(&ctx, &current, visibility_mask);
    if (ctx.mode == 0 && (visibility_mask & current.visibility_byte) != 0 && current.resolved_valid &&
        current.triangle_count > 0) {
        tls_structural_draws_walk++;
    }
    if (tls_semantic_suppress > 0 && tls_implementable_suppress > 0) {
        tls_semantic_suppress--;
        tls_implementable_suppress--;
        if (geom.walk_end) {
            close_structural_walk();
        }
        return;
    }
    eu4_border_resolved_step_t *steps = tls_resolved_steps;
    uint32_t resolved = 0;
    if (!eu4_border_resolve_remaining_steps(
            rbp, r12, cursor, visibility_mask, geom.remaining, steps, EU4_BORDER_MAX_RESOLVE_STEPS, &resolved)) {
        record_decode_failure(false, true);
        return;
    }
    eu4_border_prefix_result_t semantic;
    eu4_border_prefix_result_t implementable;
    memset(&semantic, 0, sizeof(semantic));
    memset(&implementable, 0, sizeof(implementable));
    if (tls_semantic_suppress == 0) {
        eu4_border_classify_resolved_prefix(
            &ctx, steps, resolved, EU4_BORDER_IBO_ONE_BIND, 0, true, true, &semantic);
    }
    if (tls_implementable_suppress == 0) {
        eu4_border_classify_resolved_prefix(
            &ctx, steps, resolved, EU4_BORDER_IBO_ONE_BIND, EU4_BORDER_V1_SCAN_CAP, false, true, &implementable);
    }
    apply_decision(&semantic, &implementable);
    if (geom.walk_end) {
        close_structural_walk();
    }
}

void eu4_border_loop_head_capture_freeze_state(void) {
    if (atomic_load_explicit(&g_roi_epoch_invalid, memory_order_relaxed)) {
        eu4_submission_counter_add(EU4_COUNTER_BORDER_ROI_EPOCH_INVALID, 1);
    }
    eu4_submission_counter_add(EU4_COUNTER_BORDER_ROI_FREEZE_PARTIAL_WALK, tls_structural_draws_walk);
    eu4_submission_counter_add(EU4_COUNTER_BORDER_ROI_FREEZE_SEMANTIC_SUPPRESS, tls_semantic_suppress);
    eu4_submission_counter_add(EU4_COUNTER_BORDER_ROI_FREEZE_IMPLEMENTABLE_SUPPRESS, tls_implementable_suppress);
}

void eu4_border_loop_head_flush_pending(void) {
    if (!eu4_submission_observation_bank_active()) {
        return;
    }
    (void)check_thread_affinity();
    if (tls_structural_draws_walk > 0) {
        tls_partial_walk_diag++;
    }
    if (tls_semantic_suppress > 0 || tls_implementable_suppress > 0) {
        tls_suppression_diag++;
    }
    if (tls_loop_head_entries > 0) {
        eu4_submission_counter_add(EU4_COUNTER_BORDER_LOOP_HEAD_ENTRIES, tls_loop_head_entries);
        tls_loop_head_entries = 0;
    }
    if (tls_semantic_evaluations > 0) {
        eu4_submission_counter_add(EU4_COUNTER_BORDER_SEMANTIC_EVALUATIONS, tls_semantic_evaluations);
        tls_semantic_evaluations = 0;
    }
    if (tls_semantic_decisions > 0) {
        eu4_submission_counter_add(EU4_COUNTER_BORDER_SEMANTIC_DECISIONS, tls_semantic_decisions);
        tls_semantic_decisions = 0;
    }
    if (tls_semantic_elim > 0) {
        eu4_submission_counter_add(EU4_COUNTER_BORDER_SEMANTIC_ELIMINATIONS, tls_semantic_elim);
        tls_semantic_elim = 0;
    }
    if (tls_implementable_evaluations > 0) {
        eu4_submission_counter_add(EU4_COUNTER_BORDER_IMPLEMENTABLE_EVALUATIONS, tls_implementable_evaluations);
        tls_implementable_evaluations = 0;
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
    if (tls_decode_failures > 0) {
        eu4_submission_counter_add(EU4_COUNTER_BORDER_LOOP_DECODE_FAILURES, tls_decode_failures);
        tls_decode_failures = 0;
    }
    if (tls_geometry_failures > 0) {
        eu4_submission_counter_add(EU4_COUNTER_BORDER_LOOP_GEOMETRY_FAILURES, tls_geometry_failures);
        tls_geometry_failures = 0;
    }
    if (tls_resolution_failures > 0) {
        eu4_submission_counter_add(EU4_COUNTER_BORDER_LOOP_RESOLUTION_FAILURES, tls_resolution_failures);
        tls_resolution_failures = 0;
    }
    if (tls_partial_walk_diag > 0) {
        eu4_submission_counter_add(EU4_COUNTER_BORDER_ROI_PARTIAL_WALK_AT_FRAME_BOUNDARY, tls_partial_walk_diag);
        tls_partial_walk_diag = 0;
    }
    if (tls_suppression_diag > 0) {
        eu4_submission_counter_add(
            EU4_COUNTER_BORDER_ROI_NONZERO_SUPPRESSION_AT_FRAME_BOUNDARY, tls_suppression_diag);
        tls_suppression_diag = 0;
    }
    if (tls_thread_checks > 0) {
        eu4_submission_counter_add(EU4_COUNTER_BORDER_THREAD_CHECKS, tls_thread_checks);
        tls_thread_checks = 0;
    }
    flush_census_tls();
}

bool eu4_border_loop_head_self_test_decode_geometry(void) {
    eu4_border_walk_geometry_t geom;
    if (!eu4_border_decode_walk_geometry(100u, 102u, &geom) || !geom.walk_end || geom.remaining != 1u) {
        return false;
    }
    if (!eu4_border_decode_walk_geometry(100u, 106u, &geom) || geom.walk_end || geom.remaining != 2u) {
        return false;
    }
    if (eu4_border_decode_walk_geometry(200u, 100u, &geom)) {
        return false;
    }
    if (eu4_border_decode_walk_geometry(100u, 103u, &geom)) {
        return false;
    }
    if (eu4_border_decode_walk_geometry(0u, (uintptr_t)(1ull << 34), &geom)) {
        return false;
    }
    return true;
}

bool eu4_border_loop_head_self_test_thread_affinity(void) {
    eu4_border_loop_head_epoch_reset();
    atomic_store_explicit(&g_roi_loop_thread_id, 1u, memory_order_relaxed);
    if (check_thread_affinity()) {
        return false;
    }
    if (!eu4_border_loop_head_test_epoch_invalid()) {
        return false;
    }
    eu4_border_loop_head_epoch_reset();
    if (!check_thread_affinity()) {
        return false;
    }
    return !eu4_border_loop_head_test_epoch_invalid();
}

bool eu4_border_loop_head_self_test_freeze_capture(void) {
    eu4_border_loop_head_test_reset_tls();
    tls_structural_draws_walk = 2;
    tls_semantic_suppress = 1;
    tls_implementable_suppress = 3;
    eu4_border_loop_head_capture_freeze_state();
    return true;
}
