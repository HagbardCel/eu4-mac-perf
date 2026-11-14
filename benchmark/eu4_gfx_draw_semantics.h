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
