#ifndef BORDER_LOOP_CLASSIFIER_H
#define BORDER_LOOP_CLASSIFIER_H

#include <stdbool.h>
#include <stdint.h>

#define EU4_BORDER_V1_SCAN_CAP 128u
#define EU4_BORDER_MAX_RESOLVE_STEPS 8192u

typedef enum {
    EU4_BORDER_TERM_BARRIER = 0,
    EU4_BORDER_TERM_WALK_END = 1,
    EU4_BORDER_TERM_SCAN_CAP = 2,
    EU4_BORDER_TERM_INVALID = 3,
} eu4_border_termination_kind_t;

typedef enum {
    EU4_BORDER_IBO_ONE_BIND = 0,
    EU4_BORDER_IBO_ZERO_BIND = 1,
} eu4_border_ibo_topology_t;

typedef struct {
    uint32_t mode;
    uint32_t cached_color;
    uint32_t cached_vbo_index;
    uint32_t skip_or_visibility_mask;
    uint32_t outer_batch_index;
    uintptr_t bound_ibo_identity;
    bool ibo_known;
    bool color_known;
    bool vbo_known;
    bool deferred_attrib_upload_pending;
    bool secondary_upload_pending;
} eu4_border_loop_context_t;

typedef struct {
    int32_t record_index;
    uint8_t visibility_byte;
    uint8_t color_byte;
} eu4_border_index_entry_t;

typedef struct {
    uint16_t arg3_u16_at_plus_04;
    uint32_t triangle_count;
    uint32_t vbo_table_index;
} eu4_border_record_view_t;

typedef struct {
    uint16_t record_index;
    uint8_t visibility_byte;
    uint8_t color_byte;
    bool resolved_valid;
    uint16_t triangle_count;
    uint16_t vbo_table_index;
    uintptr_t ibo_identity;
    uintptr_t ibo_argument;
} eu4_border_resolved_step_t;

typedef struct {
    uint32_t run_length;
    uint32_t stop_reason_mask;
    uint32_t boundary_reason_mask;
    uint32_t eligible_draw_calls_eliminable;
    bool batch_eligible;
    bool v1_fall_through_recommended;
    eu4_border_termination_kind_t termination_kind;
} eu4_border_prefix_result_t;

bool eu4_border_special_precolor_outer_batch(uint32_t outer_batch_index);

void eu4_border_classify_resolved_prefix(
    const eu4_border_loop_context_t *ctx,
    const eu4_border_resolved_step_t *steps,
    uint32_t step_count,
    eu4_border_ibo_topology_t ibo_topology,
    uint32_t max_scan_steps,
    bool unbounded_scan,
    bool walk_exhausted,
    eu4_border_prefix_result_t *out);

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
    eu4_border_prefix_result_t *out);

#endif
