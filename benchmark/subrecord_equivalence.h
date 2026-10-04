#ifndef EU4_SUBRECORD_EQUIVALENCE_H
#define EU4_SUBRECORD_EQUIVALENCE_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define EU4_SFLUSHDATA_SIZE 80u
#define EU4_MESH_DRAW_SUBRECORD_SIZE 232u

typedef enum {
    EU4_CMP_EQUAL = 0,
    EU4_CMP_IGNORE = 1,
    EU4_CMP_BARRIER = 2,
} eu4_comparison_policy_t;

typedef struct {
    uint32_t offset;
    uint32_t size;
    eu4_comparison_policy_t policy;
} eu4_layout_range_t;

typedef struct {
    uint64_t parent_sflushdata_id;
    uint32_t layer_index;
    uint32_t flush_array_kind;
    bool same_parent_boundary;
    bool chain_broken;
    bool has_immediate_predecessor;
} eu4_subrecord_context_t;

typedef enum {
    EU4_PRED_OK = 0,
    EU4_PRED_CHAIN_BROKEN = 1,
    EU4_PRED_PARENT_BOUNDARY = 2,
    EU4_PRED_LAYER_MISMATCH = 3,
    EU4_PRED_FLUSH_ARRAY_MISMATCH = 4,
    EU4_PRED_RECORD_MISMATCH = 5,
    EU4_PRED_GEOMETRY_UNPROVEN = 6,
    EU4_PRED_BARRIER_UNRESOLVED = 7,
} eu4_predicate_reason_t;

extern const eu4_layout_range_t eu4_sflushdata_ranges[];
extern const size_t eu4_sflushdata_range_count;
extern const eu4_layout_range_t eu4_mesh_draw_ranges[];
extern const size_t eu4_mesh_draw_range_count;

bool eu4_layout_has_barrier_ranges(const eu4_layout_range_t *ranges, size_t range_count);

bool eu4_records_barrier_bytes_match(
    const uint8_t *left,
    const uint8_t *right,
    size_t record_size,
    const eu4_layout_range_t *ranges,
    size_t range_count
);

bool eu4_records_equal_fields_only(
    const uint8_t *left,
    const uint8_t *right,
    size_t record_size,
    const eu4_layout_range_t *ranges,
    size_t range_count
);

bool eu4_records_equal_on_ranges(
    const uint8_t *left,
    const uint8_t *right,
    size_t record_size,
    const eu4_layout_range_t *ranges,
    size_t range_count
);

bool eu4_submission_state_equivalent(
    const uint8_t *prev_parent,
    const uint8_t *curr_parent,
    const uint8_t *prev_sub,
    const uint8_t *curr_sub,
    const eu4_subrecord_context_t *prev_ctx,
    const eu4_subrecord_context_t *curr_ctx,
    eu4_predicate_reason_t *reason
);

bool eu4_buffer_bind_elision_eligible(
    const uint8_t *prev_parent,
    const uint8_t *curr_parent,
    const uint8_t *prev_sub,
    const uint8_t *curr_sub,
    const eu4_subrecord_context_t *prev_ctx,
    const eu4_subrecord_context_t *curr_ctx,
    eu4_predicate_reason_t *reason
);

bool eu4_texture_setup_elision_eligible(
    const uint8_t *prev_parent,
    const uint8_t *curr_parent,
    const uint8_t *prev_sub,
    const uint8_t *curr_sub,
    const eu4_subrecord_context_t *prev_ctx,
    const eu4_subrecord_context_t *curr_ctx,
    eu4_predicate_reason_t *reason
);

bool eu4_object_constants_elision_eligible(
    const uint8_t *prev_parent,
    const uint8_t *curr_parent,
    const uint8_t *prev_sub,
    const uint8_t *curr_sub,
    const eu4_subrecord_context_t *prev_ctx,
    const eu4_subrecord_context_t *curr_ctx,
    eu4_predicate_reason_t *reason
);

bool eu4_setup_elision_eligible(
    const uint8_t *prev_parent,
    const uint8_t *curr_parent,
    const uint8_t *prev_sub,
    const uint8_t *curr_sub,
    const eu4_subrecord_context_t *prev_ctx,
    const eu4_subrecord_context_t *curr_ctx,
    eu4_predicate_reason_t *reason
);

bool eu4_draw_batch_eligible(
    const uint8_t *prev_parent,
    const uint8_t *curr_parent,
    const uint8_t *prev_sub,
    const uint8_t *curr_sub,
    const eu4_subrecord_context_t *prev_ctx,
    const eu4_subrecord_context_t *curr_ctx,
    eu4_predicate_reason_t *reason
);

#endif
