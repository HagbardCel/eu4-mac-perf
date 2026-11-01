#include "eu4_gfx_draw_indexed.h"

#include <stddef.h>
#include <stdint.h>
#include <string.h>

enum { kTriangles = 4, kTriangleStrip = 5 };

static int finish(int code, int vertices, int written, int as_strip, int *vertex_count_out,
                  int *written_bytes_out, int *as_strip_out) {
    if (vertex_count_out != NULL) {
        *vertex_count_out = vertices;
    }
    if (written_bytes_out != NULL) {
        *written_bytes_out = written;
    }
    if (as_strip_out != NULL) {
        *as_strip_out = as_strip;
    }
    return code;
}

static int reject(int code, int *vertex_count_out, int *written_bytes_out, int *as_strip_out) {
    return finish(code, 0, 0, 0, vertex_count_out, written_bytes_out, as_strip_out);
}

static int ranges_overlap(const void *left, int64_t left_len, const void *right, int64_t right_len) {
    uintptr_t a = (uintptr_t)left;
    uintptr_t b = (uintptr_t)right;
    if (left_len <= 0 || right_len <= 0) {
        return 0;
    }
    if (a > UINTPTR_MAX - (uintptr_t)left_len || b > UINTPTR_MAX - (uintptr_t)right_len) {
        return 1;
    }
    return a < b + (uintptr_t)right_len && b < a + (uintptr_t)left_len;
}

static int layout_ok(int stride, int color_offset, int color_is_byte) {
    int color_bytes = color_is_byte ? 4 : 16;
    if ((color_is_byte != 0 && color_is_byte != 1) || stride < 8 || color_offset < 0) {
        return 0;
    }
    return (int64_t)color_offset + color_bytes <= stride;
}

int eu4_gfx_draw_indexed_gather(const void *context_slot, int context_bytes, int count, int unused_edx,
                                int base_vertex, const void *vertices, int vertex_bytes, int stride,
                                int color_offset, int color_is_byte, const void *indices, int index_bytes,
                                void *out, int out_bytes, int *vertex_count_out, int *written_bytes_out,
                                int *as_strip_out) {
    const uint8_t *image;
    void *context = NULL;
    void *parent = NULL;
    int32_t mode = 0;
    int32_t stored = 0;
    uint8_t uniform_pending = 0;
    uint8_t attrib_pending = 0;
    int selected;
    int as_strip;
    int64_t written;
    int i;
    (void)unused_edx;
    if (context_slot == NULL) {
        return reject(EU4_GFX_DRAW_NULL_CONTEXT, vertex_count_out, written_bytes_out, as_strip_out);
    }
    if (context_bytes < EU4_GFX_DRAW_CONTEXT_BYTES) {
        return reject(EU4_GFX_DRAW_RANGE, vertex_count_out, written_bytes_out, as_strip_out);
    }
    memcpy(&context, context_slot, sizeof context);
    if (context == NULL) {
        return reject(EU4_GFX_DRAW_NULL_CONTEXT, vertex_count_out, written_bytes_out, as_strip_out);
    }
    image = context;
    memcpy(&mode, image + 0x8, sizeof mode);
    memcpy(&stored, image + 0x68, sizeof stored);
    memcpy(&parent, image + 0x70, sizeof parent);
    memcpy(&uniform_pending, image + 0x128, 1);
    memcpy(&attrib_pending, image + 0x170, 1);
    if (parent == NULL) {
        return reject(EU4_GFX_DRAW_MISSING_PARENT, vertex_count_out, written_bytes_out, as_strip_out);
    }
    if (uniform_pending != 0) {
        return reject(EU4_GFX_DRAW_PENDING_UNIFORM, vertex_count_out, written_bytes_out, as_strip_out);
    }
    if (attrib_pending != 0) {
        return reject(EU4_GFX_DRAW_PENDING_ATTRIB, vertex_count_out, written_bytes_out, as_strip_out);
    }
    if (mode != kTriangles && mode != kTriangleStrip) {
        return reject(EU4_GFX_DRAW_BAD_MODE, vertex_count_out, written_bytes_out, as_strip_out);
    }
    as_strip = mode == kTriangleStrip;
    selected = count > 0 ? count : stored;
    if (selected < 0) {
        return reject(EU4_GFX_DRAW_RANGE, vertex_count_out, written_bytes_out, as_strip_out);
    }
    if (selected == 0) {
        return finish(EU4_GFX_DRAW_OK, 0, 0, as_strip, vertex_count_out, written_bytes_out, as_strip_out);
    }
    if (!layout_ok(stride, color_offset, color_is_byte)) {
        return reject(EU4_GFX_DRAW_LAYOUT, vertex_count_out, written_bytes_out, as_strip_out);
    }
    if (vertices == NULL || indices == NULL || out == NULL || vertex_bytes < 0 || index_bytes < 0 || out_bytes < 0) {
        return reject(EU4_GFX_DRAW_RANGE, vertex_count_out, written_bytes_out, as_strip_out);
    }
    written = (int64_t)selected * stride;
    if ((int64_t)selected * 2 > index_bytes || written > out_bytes ||
        ranges_overlap(out, written, indices, (int64_t)selected * 2) ||
        ranges_overlap(out, written, vertices, vertex_bytes)) {
        return reject(EU4_GFX_DRAW_RANGE, vertex_count_out, written_bytes_out, as_strip_out);
    }
    for (i = 0; i < selected; i++) {
        uint16_t index = 0;
        int64_t resolved;
        memcpy(&index, (const uint8_t *)indices + (size_t)i * 2, sizeof index);
        resolved = (int64_t)base_vertex + (int64_t)index;
        if (resolved < 0 || (resolved + 1) * stride > vertex_bytes) {
            return reject(EU4_GFX_DRAW_RANGE, vertex_count_out, written_bytes_out, as_strip_out);
        }
    }
    for (i = 0; i < selected; i++) {
        uint16_t index = 0;
        int64_t resolved;
        memcpy(&index, (const uint8_t *)indices + (size_t)i * 2, sizeof index);
        resolved = (int64_t)base_vertex + (int64_t)index;
        memcpy((uint8_t *)out + (size_t)i * (size_t)stride,
               (const uint8_t *)vertices + (size_t)resolved * (size_t)stride, (size_t)stride);
    }
    return finish(EU4_GFX_DRAW_OK, selected, (int)written, as_strip, vertex_count_out, written_bytes_out,
                  as_strip_out);
}
