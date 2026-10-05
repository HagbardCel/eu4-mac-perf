#include "border_loop_classifier.h"

#include <string.h>

enum {
    BARRIER_SKIP = 1u << 0,
    BARRIER_UNSUPPORTED_MODE = 1u << 1,
    BARRIER_COLOR = 1u << 2,
    BARRIER_VBO = 1u << 3,
    BARRIER_IBO = 1u << 4,
    BARRIER_OTHER = 1u << 5,
};

typedef struct {
    uint32_t mode;
    uint32_t color_state;
    uint32_t vbo_table_index;
    uintptr_t ibo_identity;
    uintptr_t ibo_argument;
} batch_key_t;

typedef enum { TS_UNKNOWN = 0, TS_MATCH = 1, TS_MISMATCH = 2 } tristate_t;

typedef struct {
    tristate_t color;
    tristate_t vbo;
    tristate_t ibo;
} entry_match_t;

static bool entry_match_all(const entry_match_t *m) {
    return m->color == TS_MATCH && m->vbo == TS_MATCH && m->ibo == TS_MATCH;
}

static bool entry_match_any_unknown(const entry_match_t *m) {
    return m->color == TS_UNKNOWN || m->vbo == TS_UNKNOWN || m->ibo == TS_UNKNOWN;
}

bool eu4_border_special_precolor_outer_batch(uint32_t outer_batch_index) {
    const uint32_t idx = outer_batch_index & 0xFFFFFFFFu;
    return idx <= 7u && ((0xB0u >> idx) & 1u) != 0u;
}

static bool step_visible(const eu4_border_loop_context_t *ctx, const eu4_border_resolved_step_t *step) {
    return (ctx->skip_or_visibility_mask & step->visibility_byte) != 0u;
}

static bool null_ibo(uintptr_t arg) {
    return arg == 0u;
}

static tristate_t cmp_field(bool known, uint32_t current, uint32_t required) {
    if (!known) {
        return TS_UNKNOWN;
    }
    return current == required ? TS_MATCH : TS_MISMATCH;
}

static entry_match_t entry_matches_key(
    const eu4_border_loop_context_t *ctx,
    const batch_key_t *key,
    eu4_border_ibo_topology_t topology) {
    entry_match_t m = {TS_UNKNOWN, TS_UNKNOWN, TS_UNKNOWN};
    m.color = cmp_field(ctx->color_known, ctx->cached_color, key->color_state);
    m.vbo = cmp_field(ctx->vbo_known, ctx->cached_vbo_index, key->vbo_table_index);
    if (topology == EU4_BORDER_IBO_ONE_BIND) {
        m.ibo = null_ibo(key->ibo_argument) ? TS_MISMATCH : TS_MATCH;
    } else {
        m.ibo = cmp_field(ctx->ibo_known, (uint32_t)ctx->bound_ibo_identity, (uint32_t)key->ibo_identity);
    }
    return m;
}

static uint32_t homogeneity_mask_step(const eu4_border_resolved_step_t *step, const batch_key_t *key) {
    uint32_t boundary = 0;
    if (step->color_byte != key->color_state) {
        boundary |= BARRIER_COLOR;
    }
    if (step->vbo_table_index != key->vbo_table_index) {
        boundary |= BARRIER_VBO;
    }
    if (step->ibo_identity != key->ibo_identity) {
        boundary |= BARRIER_IBO;
    }
    if (null_ibo(step->ibo_argument)) {
        boundary |= BARRIER_OTHER;
    }
    if (step->triangle_count == 0u) {
        boundary |= BARRIER_OTHER;
    }
    return boundary;
}

static void fill_result(
    eu4_border_prefix_result_t *out,
    uint32_t run_len,
    uint32_t stop,
    uint32_t boundary,
    const entry_match_t *entry_match,
    eu4_border_termination_kind_t kind) {
    const bool eligible = run_len >= 2u && entry_match_all(entry_match) && kind != EU4_BORDER_TERM_INVALID;
    const uint32_t elim = eligible ? (run_len >= 1u ? run_len - 1u : 0u) : 0u;
    const bool fall_through = run_len < 2u || kind == EU4_BORDER_TERM_INVALID;
    out->run_length = run_len;
    out->stop_reason_mask = stop;
    out->boundary_reason_mask = boundary;
    out->eligible_draw_calls_eliminable = elim;
    out->batch_eligible = eligible;
    out->v1_fall_through_recommended = fall_through;
    out->termination_kind = kind;
}

void eu4_border_classify_resolved_prefix(
    const eu4_border_loop_context_t *ctx,
    const eu4_border_resolved_step_t *steps,
    uint32_t step_count,
    eu4_border_ibo_topology_t ibo_topology,
    uint32_t max_scan_steps,
    bool unbounded_scan,
    bool walk_exhausted,
    eu4_border_prefix_result_t *out) {
    entry_match_t empty = {TS_UNKNOWN, TS_UNKNOWN, TS_UNKNOWN};
    if (!ctx || !out) {
        return;
    }
    memset(out, 0, sizeof(*out));
    if (ctx->deferred_attrib_upload_pending || ctx->secondary_upload_pending) {
        fill_result(out, 0, BARRIER_OTHER, 0, &empty, EU4_BORDER_TERM_BARRIER);
        return;
    }
    if (eu4_border_special_precolor_outer_batch(ctx->outer_batch_index)) {
        fill_result(out, 0, BARRIER_OTHER, 0, &empty, EU4_BORDER_TERM_BARRIER);
        return;
    }
    if (step_count == 0) {
        fill_result(out, 0, 0, 0, &empty, walk_exhausted ? EU4_BORDER_TERM_WALK_END : EU4_BORDER_TERM_SCAN_CAP);
        return;
    }
    if (ctx->mode != 0u) {
        fill_result(out, 0, BARRIER_UNSUPPORTED_MODE, BARRIER_UNSUPPORTED_MODE, &empty, EU4_BORDER_TERM_BARRIER);
        return;
    }
    const eu4_border_resolved_step_t *first = &steps[0];
    if (!step_visible(ctx, first)) {
        fill_result(out, 0, BARRIER_SKIP, BARRIER_SKIP, &empty, EU4_BORDER_TERM_BARRIER);
        return;
    }
    if (!first->resolved_valid) {
        fill_result(out, 0, BARRIER_OTHER, BARRIER_OTHER, &empty, EU4_BORDER_TERM_INVALID);
        return;
    }
    batch_key_t key = {
        ctx->mode,
        first->color_byte,
        first->vbo_table_index,
        first->ibo_identity,
        first->ibo_argument,
    };
    entry_match_t entry_match = entry_matches_key(ctx, &key, ibo_topology);
    if (entry_match_any_unknown(&entry_match) || !entry_match_all(&entry_match)) {
        fill_result(out, 0, BARRIER_OTHER, 0, &entry_match, EU4_BORDER_TERM_BARRIER);
        return;
    }
    if (homogeneity_mask_step(first, &key) != 0u) {
        fill_result(out, 0, BARRIER_OTHER, BARRIER_OTHER, &entry_match, EU4_BORDER_TERM_BARRIER);
        return;
    }
    uint32_t run_len = 0;
    uint32_t boundary = 0;
    eu4_border_termination_kind_t termination = EU4_BORDER_TERM_WALK_END;
    bool loop_broken = false;
    for (uint32_t i = 0; i < step_count; i++) {
        if (!unbounded_scan && run_len >= max_scan_steps) {
            termination = EU4_BORDER_TERM_SCAN_CAP;
            loop_broken = true;
            break;
        }
        const eu4_border_resolved_step_t *step = &steps[i];
        if (!step_visible(ctx, step)) {
            boundary = BARRIER_SKIP;
            termination = EU4_BORDER_TERM_BARRIER;
            loop_broken = true;
            break;
        }
        if (!step->resolved_valid) {
            boundary = BARRIER_OTHER;
            termination = EU4_BORDER_TERM_INVALID;
            loop_broken = true;
            break;
        }
        const uint32_t mask = homogeneity_mask_step(step, &key);
        if (mask != 0u) {
            boundary = mask;
            termination = EU4_BORDER_TERM_BARRIER;
            loop_broken = true;
            break;
        }
        run_len++;
    }
    if (!loop_broken) {
        termination = walk_exhausted ? EU4_BORDER_TERM_WALK_END : EU4_BORDER_TERM_SCAN_CAP;
    }
    fill_result(out, run_len, boundary, boundary, &entry_match, termination);
}

void eu4_border_classify_batchable_prefix(
    const eu4_border_loop_context_t *ctx,
    const eu4_border_record_view_t *record_table,
    const uintptr_t *ibo_table,
    uint32_t record_count,
    const eu4_border_index_entry_t *side_entries,
    uint32_t side_count,
    eu4_border_ibo_topology_t ibo_topology,
    uint32_t max_scan_steps,
    bool unbounded_scan,
    bool walk_exhausted,
    eu4_border_prefix_result_t *out) {
    if (!ctx || !out || !side_entries) {
        return;
    }
    eu4_border_resolved_step_t steps[EU4_BORDER_MAX_RESOLVE_STEPS];
    if (side_count > EU4_BORDER_MAX_RESOLVE_STEPS) {
        entry_match_t empty = {TS_UNKNOWN, TS_UNKNOWN, TS_UNKNOWN};
        fill_result(out, 0, BARRIER_OTHER, BARRIER_OTHER, &empty, EU4_BORDER_TERM_INVALID);
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
            continue;
        }
        const eu4_border_record_view_t *rec = &record_table[ent->record_index];
        const uintptr_t ibo = ibo_table[ent->record_index];
        step->triangle_count = (uint16_t)rec->triangle_count;
        step->vbo_table_index = (uint16_t)rec->vbo_table_index;
        step->ibo_identity = ibo;
        step->ibo_argument = ibo;
        step->resolved_valid = true;
    }
    eu4_border_classify_resolved_prefix(
        ctx, steps, side_count, ibo_topology, max_scan_steps, unbounded_scan, walk_exhausted, out);
}
