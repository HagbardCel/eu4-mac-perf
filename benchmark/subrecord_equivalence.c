#include "subrecord_equivalence.h"

#include <string.h>

#include "subrecord_equivalence_ranges.inc"

static const eu4_layout_range_t eu4_mesh_buffer_bind_sub_ranges[] = {
    {0x30, 8, EU4_CMP_EQUAL},
    {0x38, 8, EU4_CMP_EQUAL},
    {0x40, 8, EU4_CMP_EQUAL},
    {0x7d, 1, EU4_CMP_EQUAL},
};
static const size_t eu4_mesh_buffer_bind_sub_range_count =
    sizeof(eu4_mesh_buffer_bind_sub_ranges) / sizeof(eu4_mesh_buffer_bind_sub_ranges[0]);

bool eu4_records_equal_fields_only(
    const uint8_t *left,
    const uint8_t *right,
    size_t record_size,
    const eu4_layout_range_t *ranges,
    size_t range_count
) {
    if (!left || !right) {
        return false;
    }
    for (size_t index = 0; index < range_count; index++) {
        const eu4_layout_range_t *range = &ranges[index];
        if (range->offset + range->size > record_size) {
            return false;
        }
        if (range->policy != EU4_CMP_EQUAL) {
            continue;
        }
        if (memcmp(left + range->offset, right + range->offset, range->size) != 0) {
            return false;
        }
    }
    return true;
}

bool eu4_records_equal_on_ranges(
    const uint8_t *left,
    const uint8_t *right,
    size_t record_size,
    const eu4_layout_range_t *ranges,
    size_t range_count
) {
    if (!left || !right) {
        return false;
    }
    for (size_t index = 0; index < range_count; index++) {
        const eu4_layout_range_t *range = &ranges[index];
        if (range->offset + range->size > record_size) {
            return false;
        }
        if (memcmp(left + range->offset, right + range->offset, range->size) != 0) {
            return false;
        }
    }
    return true;
}

static bool contexts_comparable(
    const eu4_subrecord_context_t *prev_ctx,
    const eu4_subrecord_context_t *curr_ctx,
    eu4_predicate_reason_t *reason
) {
    if (!prev_ctx || !curr_ctx) {
        if (reason) {
            *reason = EU4_PRED_RECORD_MISMATCH;
        }
        return false;
    }
    if (prev_ctx->chain_broken || curr_ctx->chain_broken) {
        if (reason) {
            *reason = EU4_PRED_CHAIN_BROKEN;
        }
        return false;
    }
    if (!curr_ctx->has_immediate_predecessor) {
        if (reason) {
            *reason = EU4_PRED_CHAIN_BROKEN;
        }
        return false;
    }
    if (!prev_ctx->same_parent_boundary || !curr_ctx->same_parent_boundary) {
        if (reason) {
            *reason = EU4_PRED_PARENT_BOUNDARY;
        }
        return false;
    }
    if (prev_ctx->parent_sflushdata_id != curr_ctx->parent_sflushdata_id) {
        if (reason) {
            *reason = EU4_PRED_PARENT_BOUNDARY;
        }
        return false;
    }
    if (prev_ctx->layer_index != curr_ctx->layer_index) {
        if (reason) {
            *reason = EU4_PRED_LAYER_MISMATCH;
        }
        return false;
    }
    if (prev_ctx->flush_array_kind != curr_ctx->flush_array_kind) {
        if (reason) {
            *reason = EU4_PRED_FLUSH_ARRAY_MISMATCH;
        }
        return false;
    }
    return true;
}

bool eu4_submission_state_equivalent(
    const uint8_t *prev_parent,
    const uint8_t *curr_parent,
    const uint8_t *prev_sub,
    const uint8_t *curr_sub,
    const eu4_subrecord_context_t *prev_ctx,
    const eu4_subrecord_context_t *curr_ctx,
    eu4_predicate_reason_t *reason
) {
    if (!contexts_comparable(prev_ctx, curr_ctx, reason)) {
        return false;
    }
    if (!eu4_records_equal_fields_only(
            prev_parent,
            curr_parent,
            EU4_SFLUSHDATA_SIZE,
            eu4_sflushdata_ranges,
            eu4_sflushdata_range_count)) {
        if (reason) {
            *reason = EU4_PRED_RECORD_MISMATCH;
        }
        return false;
    }
    if (!eu4_records_equal_fields_only(
            prev_sub,
            curr_sub,
            EU4_MESH_DRAW_SUBRECORD_SIZE,
            eu4_mesh_draw_ranges,
            eu4_mesh_draw_range_count)) {
        if (reason) {
            *reason = EU4_PRED_RECORD_MISMATCH;
        }
        return false;
    }
    if (reason) {
        *reason = EU4_PRED_OK;
    }
    return true;
}

bool eu4_buffer_bind_elision_eligible(
    const uint8_t *prev_parent,
    const uint8_t *curr_parent,
    const uint8_t *prev_sub,
    const uint8_t *curr_sub,
    const eu4_subrecord_context_t *prev_ctx,
    const eu4_subrecord_context_t *curr_ctx,
    eu4_predicate_reason_t *reason
) {
    if (!contexts_comparable(prev_ctx, curr_ctx, reason)) {
        return false;
    }
    if (!eu4_records_equal_fields_only(
            prev_parent,
            curr_parent,
            EU4_SFLUSHDATA_SIZE,
            eu4_sflushdata_ranges,
            eu4_sflushdata_range_count)) {
        if (reason) {
            *reason = EU4_PRED_RECORD_MISMATCH;
        }
        return false;
    }
    if (!eu4_records_equal_on_ranges(
            prev_sub,
            curr_sub,
            EU4_MESH_DRAW_SUBRECORD_SIZE,
            eu4_mesh_buffer_bind_sub_ranges,
            eu4_mesh_buffer_bind_sub_range_count)) {
        if (reason) {
            *reason = EU4_PRED_RECORD_MISMATCH;
        }
        return false;
    }
    if (reason) {
        *reason = EU4_PRED_OK;
    }
    return true;
}

bool eu4_texture_setup_elision_eligible(
    const uint8_t *prev_parent,
    const uint8_t *curr_parent,
    const uint8_t *prev_sub,
    const uint8_t *curr_sub,
    const eu4_subrecord_context_t *prev_ctx,
    const eu4_subrecord_context_t *curr_ctx,
    eu4_predicate_reason_t *reason
) {
    (void)prev_parent;
    (void)curr_parent;
    (void)prev_sub;
    (void)curr_sub;
    (void)prev_ctx;
    (void)curr_ctx;
    if (reason) {
        *reason = EU4_PRED_BARRIER_UNRESOLVED;
    }
    return false;
}

bool eu4_object_constants_elision_eligible(
    const uint8_t *prev_parent,
    const uint8_t *curr_parent,
    const uint8_t *prev_sub,
    const uint8_t *curr_sub,
    const eu4_subrecord_context_t *prev_ctx,
    const eu4_subrecord_context_t *curr_ctx,
    eu4_predicate_reason_t *reason
) {
    (void)prev_parent;
    (void)curr_parent;
    (void)prev_sub;
    (void)curr_sub;
    (void)prev_ctx;
    (void)curr_ctx;
    if (reason) {
        *reason = EU4_PRED_BARRIER_UNRESOLVED;
    }
    return false;
}

bool eu4_setup_elision_eligible(
    const uint8_t *prev_parent,
    const uint8_t *curr_parent,
    const uint8_t *prev_sub,
    const uint8_t *curr_sub,
    const eu4_subrecord_context_t *prev_ctx,
    const eu4_subrecord_context_t *curr_ctx,
    eu4_predicate_reason_t *reason
) {
    if (!eu4_buffer_bind_elision_eligible(
            prev_parent, curr_parent, prev_sub, curr_sub, prev_ctx, curr_ctx, reason)) {
        return false;
    }
    if (!eu4_texture_setup_elision_eligible(
            prev_parent, curr_parent, prev_sub, curr_sub, prev_ctx, curr_ctx, reason)) {
        return false;
    }
    if (!eu4_object_constants_elision_eligible(
            prev_parent, curr_parent, prev_sub, curr_sub, prev_ctx, curr_ctx, reason)) {
        return false;
    }
    if (reason) {
        *reason = EU4_PRED_OK;
    }
    return true;
}

bool eu4_draw_batch_eligible(
    const uint8_t *prev_parent,
    const uint8_t *curr_parent,
    const uint8_t *prev_sub,
    const uint8_t *curr_sub,
    const eu4_subrecord_context_t *prev_ctx,
    const eu4_subrecord_context_t *curr_ctx,
    eu4_predicate_reason_t *reason
) {
    (void)prev_parent;
    (void)curr_parent;
    (void)prev_sub;
    (void)curr_sub;
    (void)prev_ctx;
    (void)curr_ctx;
    if (reason) {
        *reason = EU4_PRED_GEOMETRY_UNPROVEN;
    }
    return false;
}
