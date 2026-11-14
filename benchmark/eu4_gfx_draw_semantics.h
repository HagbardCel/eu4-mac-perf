#ifndef EU4_GFX_DRAW_SEMANTICS_H
#define EU4_GFX_DRAW_SEMANTICS_H

#include <stdint.h>

/* Small, header-only contracts shared by the live generic encoder, the offline
   frame replay and their host tests. They encode the original OpenGL
   behaviour established from the Ghidra decompilation; they do not select by
   caller or scene. */

/* MTLPrimitiveType numeric values (Metal/Metal.h). -1 means unsupported. */
static inline int eu4_gfx_metal_topology(uint32_t gl_mode) {
    switch (gl_mode) {
        case 0: return 0; /* MTLPrimitiveTypePoint */
        case 1: return 1; /* MTLPrimitiveTypeLine */
        case 3: return 2; /* MTLPrimitiveTypeLineStrip */
        case 4: return 3; /* MTLPrimitiveTypeTriangle */
        case 5: return 4; /* MTLPrimitiveTypeTriangleStrip */
        /* GL_LINE_LOOP has no Metal equivalent. Refuse it rather than
           silently drawing a line strip (the merged #189 behaviour). */
        default: return -1;
    }
}

/* Effective vertex read by glDrawElementsBaseVertex: signed base is added. */
static inline int64_t eu4_gfx_effective_index(uint32_t index, int32_t base) {
    return (int64_t)index + (int64_t)base;
}

/* Computes the true effective-vertex range read by an indexed draw. Returns 0
   for a valid domain, -1 when any effective index is negative, and -2 when the
   index buffer is missing/empty. The range is not clamped. */
static inline int eu4_gfx_indexed_range(const uint16_t *indices, int count,
                                        int32_t base_vertex,
                                        int32_t *first, int32_t *last) {
    if (indices == NULL || count <= 0) {
        return -2;
    }
    int64_t minimum = INT64_MAX;
    int64_t maximum = INT64_MIN;
    for (int i = 0; i < count; i++) {
        int64_t effective = eu4_gfx_effective_index(indices[i], base_vertex);
        if (effective < 0 || effective > INT32_MAX) {
            return -1;
        }
        if (effective < minimum) minimum = effective;
        if (effective > maximum) maximum = effective;
    }
    if (first != NULL) *first = (int32_t)minimum;
    if (last != NULL) *last = (int32_t)maximum;
    return 0;
}

/* Selects the vertex/instance element range captured for one attribute.
   Instance-rate attributes always start at element 0; per-vertex indexed
   attributes use the actual sparse effective range; per-vertex nonindexed
   attributes use first..first+count-1 with checked arithmetic. Returns 0 on
   success and -1 for invalid/overflowing ranges. */
static inline int eu4_gfx_capture_vertex_range(
    int divisor, int indexed, int32_t instance_count, int32_t first,
    int32_t count, int32_t index_first, int32_t index_last,
    int32_t *out_first, int32_t *out_last) {
    if (out_first == NULL || out_last == NULL) {
        return -1;
    }
    if (divisor > 0) {
        *out_first = 0;
        *out_last = instance_count > 0 ? (instance_count - 1) / divisor : 0;
        return 0;
    }
    if (indexed) {
        if (index_first < 0 || index_last < index_first) {
            return -1;
        }
        *out_first = index_first;
        *out_last = index_last;
        return 0;
    }
    if (first < 0 || count < 0) {
        return -1;
    }
    if (count == 0) {
        *out_first = first;
        *out_last = first;
        return 0;
    }
    int64_t last = (int64_t)first + (int64_t)count - 1;
    if (last > INT32_MAX) {
        return -1;
    }
    *out_first = first;
    *out_last = (int32_t)last;
    return 0;
}

/* True when every active attribute fits the fixed capture representation. */
static inline int eu4_gfx_capture_attribute_count_ok(int active, int capacity) {
    return active >= 0 && capacity >= 0 && active <= capacity;
}

/* Checks that one complete attribute element fits in its bound buffer. The
   complete accessed range is offset + index*stride + width; a tightly sized
   final vertex does not need a full trailing stride. Returns 0 on overflow or
   when the range exceeds the buffer. */
static inline int eu4_gfx_attribute_end(uint64_t buffer_length, uint64_t offset,
                                         uint64_t stride, uint64_t index,
                                         uint64_t width, uint64_t *end_out) {
    if (width == 0) {
        return 0;
    }
    if (stride != 0 && index > (UINT64_MAX - offset) / stride) {
        return 0;
    }
    uint64_t start = offset + index * stride;
    if (width > UINT64_MAX - start) {
        return 0;
    }
    uint64_t end = start + width;
    if (end_out != NULL) {
        *end_out = end;
    }
    return end <= buffer_length;
}

/* Byte width of one OpenGL element type. Returns 0 for representations whose
   size is not a simple component multiple; callers must reject those. */
static inline int eu4_gfx_gl_type_size(uint32_t gl_type) {
    switch (gl_type) {
        case 0x1400: /* GL_BYTE */
        case 0x1401: /* GL_UNSIGNED_BYTE */
            return 1;
        case 0x1402: /* GL_SHORT */
        case 0x1403: /* GL_UNSIGNED_SHORT */
        case 0x140b: /* GL_HALF_FLOAT */
            return 2;
        case 0x1404: /* GL_INT */
        case 0x1405: /* GL_UNSIGNED_INT */
        case 0x1406: /* GL_FLOAT */
            return 4;
        case 0x140a: /* GL_DOUBLE */
            return 8;
        default:
            return 0;
    }
}

/* The four engine colour-mask bytes (GL order R,G,B,A) to MTLColorWriteMask
   bits. The engine stores 0/1 enables; any nonzero value enables the channel. */
static inline uint32_t eu4_gfx_color_write_mask(const uint8_t bytes[4]) {
    /* MTLColorWriteMask is BGRA-ordered: alpha=1, blue=2, green=4, red=8.
       The engine bytes are GL R,G,B,A. */
    return (bytes[0] ? 8u : 0u) | (bytes[1] ? 4u : 0u) |
           (bytes[2] ? 2u : 0u) | (bytes[3] ? 1u : 0u);
}

/* Captured attribute blobs are rebased to their first effective vertex. The
   offline replay must subtract that origin from the effective index; the live
   encoder uses the engine's original buffers and Metal's baseVertex instead. */
static inline int64_t eu4_gfx_replay_index(uint32_t index, int32_t base_vertex,
                                           int32_t vertex_origin) {
    return (int64_t)index + (int64_t)base_vertex - (int64_t)vertex_origin;
}

#endif
