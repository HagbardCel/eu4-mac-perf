#ifndef EU4_BORDER_LOOP_HEAD_DECODE_H
#define EU4_BORDER_LOOP_HEAD_DECODE_H

#include "border_loop_classifier.h"

#include <stdbool.h>
#include <stdint.h>

typedef struct {
    uintptr_t cursor;
    uintptr_t end_bound;
    uint32_t remaining;
    bool walk_end;
    bool valid;
} eu4_border_walk_geometry_t;

bool eu4_border_decode_walk_geometry(uintptr_t cursor, uintptr_t end_bound, eu4_border_walk_geometry_t *out);

bool eu4_border_decode_loop_context(void *rbp, void *r12, uint8_t visibility_mask, eu4_border_loop_context_t *ctx);

bool eu4_border_resolve_step_at_cursor(
    void *rbp,
    void *r12,
    uintptr_t cursor,
    uint8_t visibility_mask,
    eu4_border_resolved_step_t *out);

bool eu4_border_resolve_remaining_steps(
    void *rbp,
    void *r12,
    uintptr_t cursor,
    uint8_t visibility_mask,
    uint32_t remaining,
    eu4_border_resolved_step_t *out_steps,
    uint32_t out_capacity,
    uint32_t *out_count);

#endif
