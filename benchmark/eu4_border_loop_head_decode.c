#include "eu4_border_loop_head_decode.h"

#include <stddef.h>
#include <stdint.h>

#define RBP_OFF_END_BOUND 0x198u
#define RBP_OFF_OUTER_BATCH 0xe4u
#define RBP_OFF_CACHED_COLOR 0x51u
#define RBP_OFF_CACHED_VBO 0x64u
#define RBP_OFF_DEFERRED_CTX 0x60u
#define R12_OFF_MODE 0x24u
#define R12_OFF_RECORD_BASE 0x78u
#define R12_OFF_IBO_TABLE 0x60u
#define RECORD_STRIDE 28u

static bool checked_mul(uintptr_t a, uintptr_t b, uintptr_t *out) {
    if (a != 0 && b > (uintptr_t)(-1) / a) {
        return false;
    }
    *out = a * b;
    return true;
}

bool eu4_border_decode_walk_geometry(uintptr_t cursor, uintptr_t end_bound, eu4_border_walk_geometry_t *out) {
    if (!out) {
        return false;
    }
    out->cursor = cursor;
    out->end_bound = end_bound;
    out->remaining = 0;
    out->walk_end = false;
    out->valid = false;
    if (end_bound < cursor) {
        return false;
    }
    const uintptr_t delta = end_bound - cursor;
    if (delta > (uintptr_t)(UINTPTR_MAX - 2u)) {
        return false;
    }
    if (((delta + 2u) & 3u) != 0u) {
        return false;
    }
    const uint32_t remaining = (uint32_t)((delta + 2u) / 4u);
    if (remaining == 0u) {
        return false;
    }
    out->remaining = remaining;
    out->walk_end = delta == 2u;
    out->valid = true;
    return true;
}

static bool read_pending_flags(void *rbp, bool *pending128, bool *pending170) {
    if (!rbp) {
        return false;
    }
    const uintptr_t frame = (uintptr_t)rbp;
    void *deferred = *(void **)(frame - RBP_OFF_DEFERRED_CTX);
    if (deferred == NULL) {
        return false;
    }
    void *inner = *(void **)deferred;
    if (inner == NULL) {
        return false;
    }
    const uintptr_t obj = (uintptr_t)inner;
    *pending128 = *(const uint8_t *)(obj + 0x128u) != 0u;
    *pending170 = *(const uint8_t *)(obj + 0x170u) != 0u;
    return true;
}

bool eu4_border_decode_loop_context(void *rbp, void *r12, uint8_t visibility_mask, eu4_border_loop_context_t *ctx) {
    if (!rbp || !r12 || !ctx) {
        return false;
    }
    const uintptr_t frame = (uintptr_t)rbp;
    const uintptr_t info = (uintptr_t)r12;
    bool p128 = false;
    bool p170 = false;
    if (!read_pending_flags(rbp, &p128, &p170)) {
        return false;
    }
    ctx->mode = *(const uint32_t *)(info + R12_OFF_MODE);
    ctx->cached_color = *(const uint8_t *)(frame - RBP_OFF_CACHED_COLOR);
    ctx->cached_vbo_index = (uint32_t)*(const int32_t *)(frame - RBP_OFF_CACHED_VBO);
    ctx->skip_or_visibility_mask = visibility_mask;
    ctx->outer_batch_index = *(const uint32_t *)(frame - RBP_OFF_OUTER_BATCH);
    ctx->bound_ibo_identity = 0;
    ctx->ibo_known = false;
    ctx->color_known = true;
    ctx->vbo_known = true;
    ctx->deferred_attrib_upload_pending = p128;
    ctx->secondary_upload_pending = p170;
    return true;
}

static bool visible(uint8_t mask, uint8_t visibility_byte) {
    return (mask & visibility_byte) != 0u;
}

bool eu4_border_resolve_step_at_cursor(
    void *rbp,
    void *r12,
    uintptr_t cursor,
    uint8_t visibility_mask,
    eu4_border_resolved_step_t *out) {
    if (!r12 || !out || cursor == 0) {
        return false;
    }
    (void)rbp;
    const uintptr_t info = (uintptr_t)r12;
    const uint8_t visibility_byte = *(const uint8_t *)(cursor + 1u);
    const uint8_t color_byte = *(const uint8_t *)cursor;
    const uint16_t record_index = *(const uint16_t *)(cursor - 2u);
    out->visibility_byte = visibility_byte;
    out->color_byte = color_byte;
    out->record_index = record_index;
    out->resolved_valid = false;
    out->triangle_count = 0;
    out->vbo_table_index = 0;
    out->ibo_identity = 0;
    out->ibo_argument = 0;
    if (!visible(visibility_mask, visibility_byte)) {
        out->resolved_valid = true;
        return true;
    }
    const uintptr_t record_base = *(const uintptr_t *)(info + R12_OFF_RECORD_BASE);
    const uintptr_t ibo_base_ptr = *(const uintptr_t *)(info + R12_OFF_IBO_TABLE);
    if (record_base == 0 || ibo_base_ptr == 0) {
        return false;
    }
    uintptr_t record_off = 0;
    if (!checked_mul((uintptr_t)record_index, RECORD_STRIDE, &record_off)) {
        return false;
    }
    const uintptr_t record_addr = record_base + record_off;
    const uintptr_t ibo_slot_addr = ibo_base_ptr + (uintptr_t)record_index * sizeof(uintptr_t);
    const uintptr_t ibo_val = *(const uintptr_t *)ibo_slot_addr;
    out->triangle_count = *(const uint16_t *)(record_addr + 6u);
    out->vbo_table_index = *(const uint16_t *)(record_addr + 0x18u);
    out->ibo_identity = ibo_val;
    out->ibo_argument = ibo_val;
    out->resolved_valid = true;
    return true;
}

bool eu4_border_resolve_remaining_steps(
    void *rbp,
    void *r12,
    uintptr_t cursor,
    uint8_t visibility_mask,
    uint32_t remaining,
    eu4_border_resolved_step_t *out_steps,
    uint32_t out_capacity,
    uint32_t *out_count) {
    if (!out_steps || !out_count || remaining > out_capacity) {
        return false;
    }
    uintptr_t walk_cursor = cursor;
    for (uint32_t i = 0; i < remaining; i++) {
        if (!eu4_border_resolve_step_at_cursor(rbp, r12, walk_cursor, visibility_mask, &out_steps[i])) {
            return false;
        }
        walk_cursor += 4u;
    }
    *out_count = remaining;
    return true;
}
