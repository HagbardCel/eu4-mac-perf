#include "../benchmark/eu4_gfx_draw_indexed.h"

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int eu4_metal_draw_render(const void *vertices, int vertex_bytes, int stride, int color_offset,
                          int color_is_byte, const float *uniform, int vertex_count, int width, int height,
                          int as_strip, int cull_mode, int front_ccw, unsigned char *pixels_out);

enum { kStride = 24, kWidth = 64, kHeight = 64 };

static void fail(const char *message) {
    fprintf(stderr, "%s\n", message);
    exit(1);
}

static void expect(int actual, int wanted, const char *message) {
    if (actual != wanted) {
        fprintf(stderr, "%s (%d)\n", message, actual);
        exit(1);
    }
}

static void put_u32(uint8_t *image, int offset, uint32_t value) {
    memcpy(image + offset, &value, sizeof value);
}

static void put_ptr(uint8_t *image, int offset, const void *pointer) {
    memcpy(image + offset, &pointer, sizeof pointer);
}

static void prepare(uint8_t *image, int mode, int stored_count) {
    memset(image, 0x5a, 0x180);
    put_u32(image, 0x8, (uint32_t)mode);
    put_u32(image, 0x68, (uint32_t)stored_count);
    put_ptr(image, 0x70, (const void *)(uintptr_t)1);
    image[0x128] = 0;
    image[0x170] = 0;
}

static int gather(const uint8_t *image, int count, int edx, int base_vertex, const void *vertices,
                  int vertex_bytes, int stride, int color_offset, int color_is_byte, const void *indices,
                  int index_bytes, void *out, int out_bytes, int *vertex_count, int *written, int *as_strip) {
    uint8_t raw[0x172];
    uint8_t *unaligned = raw + 1;
    void *context = unaligned;
    memcpy(unaligned, image, EU4_GFX_DRAW_CONTEXT_BYTES);
    return eu4_gfx_draw_indexed_gather(&context, EU4_GFX_DRAW_CONTEXT_BYTES, count, edx, base_vertex, vertices,
                                       vertex_bytes, stride, color_offset, color_is_byte, indices, index_bytes, out,
                                       out_bytes, vertex_count, written, as_strip);
}

static const float kUniform[16] = {1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1};

static const float kTriangle[18] = {
    -0.5f, -0.5f, 1, 0, 0, 1, 0.5f, -0.5f, 1, 0, 0, 1, 0.0f, 0.5f, 1, 0, 0, 1,
};

/* Slot 0 is a decoy. base 1 and indices {2,0,1} resolve to slots 3,1,2. */
static const float kPermuted[24] = {
    2, 2, 0, 1, 0, 1, 0.5f, -0.5f, 1, 0, 0, 1, 0.0f, 0.5f, 1, 0, 0, 1, -0.5f, -0.5f, 1, 0, 0, 1,
};

static const float kStrip[24] = {
    -0.9f, -0.6f, 1, 0, 0, 1, -0.3f, -0.6f, 1, 0, 0, 1, -0.9f, 0.6f, 1, 0, 0, 1, 0.9f, 0.6f, 0, 1, 0, 1,
};

/* Slots 4,1,2,3 are the strip. base 1 and indices {3,0,1,2} select them in order. */
static const float kStripSource[30] = {
    3, 3, 0, 0, 1, 1, -0.3f, -0.6f, 1, 0, 0, 1, -0.9f, 0.6f, 1, 0, 0, 1,
    0.9f, 0.6f, 0, 1, 0, 1, -0.9f, -0.6f, 1, 0, 0, 1,
};

static void test_counts(void) {
    uint8_t image[0x180];
    uint8_t out[72];
    uint8_t again[72];
    int vertices = -1;
    int written = -1;
    int as_strip = -1;
    const uint16_t order[3] = {0, 1, 2};
    prepare(image, 4, 9);
    expect(gather(image, 3, 0, 0, kTriangle, (int)sizeof kTriangle, kStride, 8, 0, order, 6, out, 72, &vertices,
                  &written, &as_strip),
           EU4_GFX_DRAW_OK, "explicit count");
    expect(vertices, 3, "explicit vertex count");
    expect(written, 72, "explicit byte count");
    expect(as_strip, 0, "triangles are not a strip");
    if (memcmp(out, kTriangle, 72) != 0) {
        fail("explicit count copied the wrong records");
    }
    prepare(image, 4, 3);
    expect(gather(image, 0, 0, 0, kTriangle, 72, kStride, 8, 0, order, 6, out, 72, &vertices, &written, &as_strip),
           EU4_GFX_DRAW_OK, "zero count uses stored count");
    expect(vertices, 3, "stored vertex count");
    expect(gather(image, -4, 0x11111111, 0, kTriangle, 72, kStride, 8, 0, order, 6, again, 72, &vertices, &written,
                  &as_strip),
           EU4_GFX_DRAW_OK, "negative count uses stored count");
    if (memcmp(out, again, 72) != 0) {
        fail("edx changed the gather");
    }
    prepare(image, 4, 0);
    memset(out, 0xa5, sizeof out);
    expect(gather(image, 0, 0, 0, NULL, 0, kStride, 8, 0, NULL, 0, out, 72, &vertices, &written, &as_strip),
           EU4_GFX_DRAW_OK, "zero fallback");
    expect(vertices, 0, "zero vertex count");
    expect(written, 0, "zero byte count");
    if (out[0] != 0xa5) {
        fail("zero fallback wrote output");
    }
    prepare(image, 4, 2);
    expect(gather(image, -1, 0, 0, kTriangle, 72, kStride, 8, 0, order, 4, out, 48, &vertices, &written, &as_strip),
           EU4_GFX_DRAW_OK, "short fallback");
    expect(vertices, 2, "short vertex count");
    expect(written, 48, "short byte count");
    prepare(image, 4, -3);
    expect(gather(image, 0, 0, 0, kTriangle, 72, kStride, 8, 0, order, 6, out, 72, &vertices, &written, &as_strip),
           EU4_GFX_DRAW_RANGE, "negative stored count");
}

static void test_preconditions(void) {
    uint8_t image[0x180];
    uint8_t out[4];
    int vertices = 7;
    int written = 7;
    int as_strip = 7;
    void *context = image;
    const uint16_t order[3] = {0, 1, 2};
    memset(out, 0xa5, sizeof out);
    prepare(image, 4, 3);
    expect(eu4_gfx_draw_indexed_gather(NULL, EU4_GFX_DRAW_CONTEXT_BYTES, 3, 0, 0, kTriangle, 72, kStride, 8, 0, order,
                                       6, out, 4, &vertices, &written, &as_strip),
           EU4_GFX_DRAW_NULL_CONTEXT, "null slot");
    context = NULL;
    expect(eu4_gfx_draw_indexed_gather(&context, EU4_GFX_DRAW_CONTEXT_BYTES, 3, 0, 0, kTriangle, 72, kStride, 8, 0,
                                       order, 6, out, 4, &vertices, &written, &as_strip),
           EU4_GFX_DRAW_NULL_CONTEXT, "null context");
    context = image;
    expect(eu4_gfx_draw_indexed_gather(&context, EU4_GFX_DRAW_CONTEXT_BYTES - 1, 3, 0, 0, kTriangle, 72, kStride, 8, 0,
                                       order, 6, out, 4, &vertices, &written, &as_strip),
           EU4_GFX_DRAW_RANGE, "short context");
    put_ptr(image, 0x70, NULL);
    expect(eu4_gfx_draw_indexed_gather(&context, EU4_GFX_DRAW_CONTEXT_BYTES, 3, 0, 0, kTriangle, 72, kStride, 8, 0,
                                       order, 6, out, 4, &vertices, &written, &as_strip),
           EU4_GFX_DRAW_MISSING_PARENT, "missing parent");
    prepare(image, 4, 3);
    image[0x128] = 1;
    expect(eu4_gfx_draw_indexed_gather(&context, EU4_GFX_DRAW_CONTEXT_BYTES, 3, 0, 0, kTriangle, 72, kStride, 8, 0,
                                       order, 6, out, 4, &vertices, &written, &as_strip),
           EU4_GFX_DRAW_PENDING_UNIFORM, "pending uniform");
    prepare(image, 4, 3);
    image[0x170] = 1;
    expect(eu4_gfx_draw_indexed_gather(&context, EU4_GFX_DRAW_CONTEXT_BYTES, 3, 0, 0, kTriangle, 72, kStride, 8, 0,
                                       order, 6, out, 4, &vertices, &written, &as_strip),
           EU4_GFX_DRAW_PENDING_ATTRIB, "pending attrib");
    expect(vertices, 0, "failure clears the vertex count");
    expect(written, 0, "failure clears the byte count");
    if (out[0] != 0xa5) {
        fail("rejected gather wrote output");
    }
}

static int unchanged(const uint8_t *bytes, int size) {
    int i;
    for (i = 0; i < size; i++) {
        if (bytes[i] != 0xa5) {
            return 0;
        }
    }
    return 1;
}

static void test_geometry(void) {
    uint8_t image[0x180];
    uint8_t out[128];
    uint8_t sentinel[73];
    int vertices = 0;
    int written = 0;
    int as_strip = 1;
    const uint16_t permuted[3] = {2, 0, 1};
    const uint16_t from_negative[3] = {1, 2, 3};
    const uint16_t wrapped[1] = {65535};
    const uint16_t zero_index[1] = {0};
    const uint16_t strip_order[4] = {3, 0, 1, 2};
    uint8_t byte_vertex[12] = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11};
    prepare(image, 4, 0);
    expect(gather(image, 3, 99, 1, kPermuted, (int)sizeof kPermuted, kStride, 8, 0, permuted, 6, out, 72, &vertices,
                  &written, &as_strip),
           EU4_GFX_DRAW_OK, "permuted gather");
    expect(vertices, 3, "permuted vertex count");
    expect(written, 72, "permuted byte count");
    if (memcmp(out, kTriangle, 72) != 0) {
        fail("permuted records differ from the literal triangle");
    }
    expect(gather(image, 3, 0, -1, kPermuted, (int)sizeof kPermuted, kStride, 8, 0, from_negative, 6, out, 72,
                  &vertices, &written, &as_strip),
           EU4_GFX_DRAW_OK, "negative base in range");
    if (memcmp(out, kPermuted, 72) != 0) {
        fail("negative base resolved the wrong slots");
    }
    memset(sentinel, 0xa5, sizeof sentinel);
    expect(gather(image, 3, 0, 1, kPermuted, (int)sizeof kPermuted, kStride, 8, 0, permuted, 6, sentinel, 71,
                  &vertices, &written, &as_strip),
           EU4_GFX_DRAW_RANGE, "output one byte short");
    if (!unchanged(sentinel, (int)sizeof sentinel)) {
        fail("short output was written");
    }
    expect(gather(image, 1, 0, -1, kTriangle, 72, kStride, 8, 0, zero_index, 2, out, 24, &vertices, &written,
                  &as_strip),
           EU4_GFX_DRAW_RANGE, "negative resolved index");
    expect(gather(image, 1, 0, 1, kTriangle, 24, kStride, 8, 0, wrapped, 2, out, 24, &vertices, &written, &as_strip),
           EU4_GFX_DRAW_RANGE, "index wrap");
    expect(gather(image, 3, 0, 0, kTriangle, 72, 20, 8, 0, permuted, 6, out, 72, &vertices, &written, &as_strip),
           EU4_GFX_DRAW_LAYOUT, "color past stride");
    expect(gather(image, 3, 0, 0, kTriangle, 72, 4, 0, 0, permuted, 6, out, 72, &vertices, &written, &as_strip),
           EU4_GFX_DRAW_LAYOUT, "position past stride");
    expect(gather(image, 1, 0, 0, byte_vertex, 12, 12, 8, 1, zero_index, 2, out, 12, &vertices, &written, &as_strip),
           EU4_GFX_DRAW_OK, "byte color layout");
    if (memcmp(out, byte_vertex, 12) != 0) {
        fail("byte color record was repacked");
    }
    prepare(image, 5, 0);
    expect(gather(image, 4, 0, 1, kStripSource, (int)sizeof kStripSource, kStride, 8, 0, strip_order, 8, out, 96,
                  &vertices, &written, &as_strip),
           EU4_GFX_DRAW_OK, "strip gather");
    expect(as_strip, 1, "strip mode");
    expect(vertices, 4, "strip vertex count");
    expect(written, 96, "strip byte count");
    if (memcmp(out, kStrip, 96) != 0) {
        fail("strip records differ from the literal strip");
    }
    prepare(image, 1, 0);
    expect(gather(image, 3, 0, 0, kTriangle, 72, kStride, 8, 0, permuted, 6, out, 72, &vertices, &written, &as_strip),
           EU4_GFX_DRAW_BAD_MODE, "unsupported mode");
}

static void render_pair(const void *gathered, const void *literal, int bytes, int vertex_count, int as_strip,
                        const char *message) {
    unsigned char *left = malloc((size_t)kWidth * kHeight * 4);
    unsigned char *right = malloc((size_t)kWidth * kHeight * 4);
    int left_pixels;
    int right_pixels;
    if (left == NULL || right == NULL) {
        fail("pixel buffer");
    }
    left_pixels = eu4_metal_draw_render(gathered, bytes, kStride, 8, 0, kUniform, vertex_count, kWidth, kHeight,
                                        as_strip, 0, 1, left);
    right_pixels = eu4_metal_draw_render(literal, bytes, kStride, 8, 0, kUniform, vertex_count, kWidth, kHeight,
                                         as_strip, 0, 1, right);
    if (left_pixels <= 0 || right_pixels <= 0 || left_pixels != right_pixels ||
        memcmp(left, right, (size_t)kWidth * kHeight * 4) != 0) {
        fail(message);
    }
    free(left);
    free(right);
}

static void test_pixels(void) {
    uint8_t image[0x180];
    uint8_t triangle[72];
    uint8_t strip[96];
    uint8_t short_vertices[48];
    int vertices = 0;
    int written = 0;
    int as_strip = 0;
    int red = 0;
    int green = 0;
    int i;
    unsigned char *pixels;
    const uint16_t permuted[3] = {2, 0, 1};
    const uint16_t strip_order[4] = {3, 0, 1, 2};
    const uint16_t two[2] = {0, 1};
    prepare(image, 4, 0);
    expect(gather(image, 3, 0, 1, kPermuted, (int)sizeof kPermuted, kStride, 8, 0, permuted, 6, triangle, 72,
                  &vertices, &written, &as_strip),
           EU4_GFX_DRAW_OK, "triangle pixels gather");
    render_pair(triangle, kTriangle, 72, 3, 0, "triangle images differ");
    prepare(image, 5, 0);
    expect(gather(image, 4, 0, 1, kStripSource, (int)sizeof kStripSource, kStride, 8, 0, strip_order, 8, strip, 96,
                  &vertices, &written, &as_strip),
           EU4_GFX_DRAW_OK, "strip pixels gather");
    render_pair(strip, kStrip, 96, 4, 1, "strip images differ");
    /* cull_mode 0 is MTLCullModeNone, so both strip windings stay. */
    pixels = malloc((size_t)kWidth * kHeight * 4);
    if (pixels == NULL) {
        fail("strip pixel buffer");
    }
    if (eu4_metal_draw_render(kStrip, 96, kStride, 8, 0, kUniform, 4, kWidth, kHeight, 1, 0, 1, pixels) <= 0) {
        fail("strip produced no pixels");
    }
    for (i = 0; i < kWidth * kHeight * 4; i += 4) {
        if (pixels[i + 2] > 200 && pixels[i + 1] < 16 && pixels[i] < 16) {
            red++;
        }
        if (pixels[i + 1] > 32) {
            green++;
        }
    }
    free(pixels);
    if (red < 80 || green < 20) {
        fprintf(stderr, "strip coverage red=%d green=%d\n", red, green);
        fail("strip showed one triangle");
    }
    prepare(image, 4, 2);
    expect(gather(image, -1, 0, 0, kTriangle, 72, kStride, 8, 0, two, 4, short_vertices, 48, &vertices, &written,
                  &as_strip),
           EU4_GFX_DRAW_OK, "two vertex gather");
    if (eu4_metal_draw_render(short_vertices, 48, kStride, 8, 0, kUniform, 2, kWidth, kHeight, 0, 0, 1, NULL) != -1) {
        fail("short draw was rendered");
    }
}

static void test_overlap(void) {
    uint8_t image[0x180];
    uint8_t block[80];
    uint8_t saved[80];
    uint8_t verts[72];
    uint8_t saved_verts[72];
    int vertices = 7;
    int written = 7;
    int as_strip = 7;
    void *context;
    const uint16_t order[3] = {0, 1, 2};
    prepare(image, 4, 0);
    context = image;
    memset(block, 0x3c, sizeof block);
    memcpy(block, order, sizeof order);
    memcpy(saved, block, sizeof saved);
    expect(eu4_gfx_draw_indexed_gather(&context, EU4_GFX_DRAW_CONTEXT_BYTES, 3, 0, 0, kTriangle, 72, kStride, 8, 0,
                                       block, 6, block, 72, &vertices, &written, &as_strip),
           EU4_GFX_DRAW_RANGE, "output overlaps indices");
    if (memcmp(block, saved, sizeof block) != 0) {
        fail("index overlap wrote the input");
    }
    memcpy(verts, kTriangle, sizeof verts);
    memcpy(saved_verts, verts, sizeof verts);
    expect(eu4_gfx_draw_indexed_gather(&context, EU4_GFX_DRAW_CONTEXT_BYTES, 3, 0, 0, verts, 72, kStride, 8, 0, order,
                                       6, verts, 72, &vertices, &written, &as_strip),
           EU4_GFX_DRAW_RANGE, "output overlaps vertices");
    if (memcmp(verts, saved_verts, sizeof verts) != 0) {
        fail("vertex overlap wrote the input");
    }
}

int main(void) {
    test_counts();
    test_preconditions();
    test_geometry();
    test_overlap();
    test_pixels();
    return 0;
}
