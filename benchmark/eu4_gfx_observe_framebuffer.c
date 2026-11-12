#define GL_SILENCE_DEPRECATION
#include "eu4_gfx_observe_framebuffer.h"

#include <CommonCrypto/CommonDigest.h>
#include <OpenGL/gl.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/time.h>
#include <time.h>
#include <unistd.h>

/* GL enums used by the observer. Spelling them out keeps this file independent
 * of a newer SDK's headers while staying GL 2.1/3.2 core compatible. */
enum {
    GLX_DRAW_FRAMEBUFFER_BINDING = 0x8CA6,
    GLX_READ_FRAMEBUFFER_BINDING = 0x8CAA,
    GLX_READ_BUFFER = 0x0C02,
    GLX_DRAW_BUFFER = 0x0C01,
    GLX_VIEWPORT = 0x0BA2,
    GLX_SCISSOR_BOX = 0x0C10,
    GLX_SCISSOR_TEST = 0x0C11,
    GLX_BLEND = 0x0BE2,
    GLX_BLEND_SRC_RGB = 0x80C9,
    GLX_BLEND_DST_RGB = 0x80C8,
    GLX_BLEND_SRC_ALPHA = 0x80CB,
    GLX_BLEND_DST_ALPHA = 0x80CA,
    GLX_BLEND_EQUATION_RGB = 0x8009,
    GLX_BLEND_EQUATION_ALPHA = 0x883D,
    GLX_DEPTH_TEST = 0x0B71,
    GLX_DEPTH_FUNC = 0x0B74,
    GLX_DEPTH_WRITEMASK = 0x0B72,
    GLX_STENCIL_TEST = 0x0B90,
    GLX_STENCIL_FUNC = 0x0B92,
    GLX_CULL_FACE = 0x0B44,
    GLX_FRONT_FACE = 0x0B46,
    GLX_COLOR_WRITEMASK = 0x0C23,
    GLX_CURRENT_PROGRAM = 0x8B8D,
    GLX_ACTIVE_TEXTURE = 0x84E0,
    GLX_TEXTURE_BINDING_2D = 0x8069,
    GLX_PACK_ALIGNMENT = 0x0D05,
    GLX_IMPLEMENTATION_COLOR_READ_FORMAT = 0x8B9B,
    GLX_IMPLEMENTATION_COLOR_READ_TYPE = 0x8B9A,
    GLX_PIXEL_PACK_BUFFER = 0x88EB,
    GLX_PIXEL_PACK_BUFFER_BINDING = 0x88ED,
    GLX_PACK_ROW_LENGTH = 0x0D02,
    GLX_PACK_SKIP_ROWS = 0x0D03,
    GLX_PACK_SKIP_PIXELS = 0x0D04,
    GLX_READ_BUFFER_BACK = 0x0405,
    GLX_ARRAY_BUFFER_BINDING = 0x8894,
    GLX_VERTEX_ATTRIB_ARRAY_BUFFER_BINDING = 0x889F,
    GLX_BUFFER_SIZE = 0x8764,
    GLX_SHADER_TYPE = 0x8B4F,
    GLX_VERTEX_SHADER = 0x8B31,
    GLX_FRAGMENT_SHADER = 0x8B30,
};

/* Saved CPU pixel-pack state. Non-default row length, skips or a bound PBO
 * would otherwise make glReadPixels write outside the tightly packed
 * destination. The observer restores every field it touches, on success and
 * failure. */
typedef struct {
    GLint alignment;
    GLint row_length;
    GLint skip_rows;
    GLint skip_pixels;
    GLint pack_buffer;
} pack_state;

static pack_state g_last_pack;
static int g_last_pack_restored;
static char g_last_reason[160];
static int g_test_fail_pack_config;

enum {
    MAX_PAYLOAD_BYTES = 256 * 1024 * 1024,
};

typedef struct {
    int enabled;
    int want_pre;
    int want_post;
    char run_dir[1024];
    int claimed;
    int have_pre;
    int pre_width;
    int pre_height;
    char pre_format[8];
    char pre_name[128];
    /* Selected draw's live vertex range, supplied by the draw entry hook. */
    int vertex_range_set;
    uint32_t vertex_stride;
    uint32_t vertex_offset;
    int32_t vertex_first;
    int32_t vertex_count;
} framebuffer_state;

static framebuffer_state g_fb;
static _Atomic uint32_t g_captures;

static void clear_gl_errors(void);
static int pack_begin(pack_state *state);
static int pack_end(const pack_state *state);

static uint64_t now_ns(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
}

static void timestamp_utc(char *out, size_t capacity) {
    struct timeval tv;
    gettimeofday(&tv, NULL);
    struct tm tm_utc;
    gmtime_r(&tv.tv_sec, &tm_utc);
    snprintf(out, capacity, "%04d-%02d-%02dT%02d:%02d:%02d.%06ldZ",
             tm_utc.tm_year + 1900, tm_utc.tm_mon + 1, tm_utc.tm_mday,
             tm_utc.tm_hour, tm_utc.tm_min, tm_utc.tm_sec, (long)tv.tv_usec);
}

static const char *env_text(const char *name) {
    const char *value = getenv(name);
    return value != NULL ? value : "";
}

static void sha256_hex(const void *bytes, size_t length, char out[65]) {
    uint8_t digest[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256(bytes, (CC_LONG)length, digest);
    static const char digits[] = "0123456789abcdef";
    for (int i = 0; i < CC_SHA256_DIGEST_LENGTH; i++) {
        out[i * 2] = digits[digest[i] >> 4];
        out[i * 2 + 1] = digits[digest[i] & 0xf];
    }
    out[64] = 0;
}

static int write_atomic(const char *path, const void *bytes, size_t length) {
    char partial[1200];
    if (snprintf(partial, sizeof partial, "%s.partial", path) >=
        (int)sizeof partial) {
        return -1;
    }
    FILE *file = fopen(partial, "wb");
    if (file == NULL) {
        return -1;
    }
    int failed = fwrite(bytes, 1, length, file) != length;
    if (!failed) {
        failed = fflush(file) != 0;
    }
    if (!failed) {
        failed = fsync(fileno(file)) != 0;
    }
    if (fclose(file) != 0) {
        failed = 1;
    }
    if (failed) {
        (void)unlink(partial);
        return -1;
    }
    if (rename(partial, path) != 0) {
        (void)unlink(partial);
        return -1;
    }
    return 0;
}

bool eu4_gfx_observe_framebuffer_configure_from_env(void) {
    memset(&g_fb, 0, sizeof g_fb);
    const char *mode = getenv("EU4_GFX_OPS_FRAMEBUFFER");
    const char *run_dir = getenv("EU4_GFX_STARTUP_DIR");
    if (mode == NULL || mode[0] == 0) {
        return true;
    }
    if (run_dir == NULL || run_dir[0] != '/' ||
        snprintf(g_fb.run_dir, sizeof g_fb.run_dir, "%s", run_dir) >=
            (int)sizeof g_fb.run_dir) {
        return false;
    }
    if (strcmp(mode, "post") == 0) {
        g_fb.want_post = 1;
    } else if (strcmp(mode, "pre+post") == 0) {
        g_fb.want_pre = 1;
        g_fb.want_post = 1;
    } else {
        return false;
    }
    g_fb.enabled = 1;
    atomic_store_explicit(&g_captures, 0, memory_order_relaxed);
    return true;
}

bool eu4_gfx_observe_framebuffer_enabled(void) { return g_fb.enabled != 0; }

uint32_t eu4_gfx_observe_framebuffer_captures(void) {
    return atomic_load_explicit(&g_captures, memory_order_relaxed);
}

int __attribute__((weak)) eu4_gfx_gl_drawable_extent(uint32_t *width,
                                                    uint32_t *height) {
    (void)width;
    (void)height;
    return 0;
}

typedef struct {
    GLint draw_framebuffer;
    GLint read_framebuffer;
    GLint read_buffer;
    GLint draw_buffer;
    GLint viewport[4];
    GLint scissor_box[4];
    GLint scissor_test;
    GLint blend;
    GLint blend_src_rgb;
    GLint blend_dst_rgb;
    GLint blend_src_alpha;
    GLint blend_dst_alpha;
    GLint blend_equation_rgb;
    GLint blend_equation_alpha;
    GLint depth_test;
    GLint depth_func;
    GLint depth_mask;
    GLint stencil_test;
    GLint stencil_func;
    GLint cull_face;
    GLint front_face;
    GLint color_mask[4];
    GLint program;
    GLint active_texture;
    GLint texture_2d;
    GLint pack_alignment;
    GLint read_format;
    GLint read_type;
    GLenum query_error;
} gl_state;

static void read_gl_state(gl_state *state) {
    memset(state, 0, sizeof *state);
    /* Discard any error left by engine calls so the readback error is ours. */
    for (int guard = 0; guard < 16 && glGetError() != GL_NO_ERROR; guard++) {
    }
    glGetIntegerv(GLX_DRAW_FRAMEBUFFER_BINDING, &state->draw_framebuffer);
    glGetIntegerv(GLX_READ_FRAMEBUFFER_BINDING, &state->read_framebuffer);
    glGetIntegerv(GLX_READ_BUFFER, &state->read_buffer);
    glGetIntegerv(GLX_DRAW_BUFFER, &state->draw_buffer);
    glGetIntegerv(GLX_VIEWPORT, state->viewport);
    glGetIntegerv(GLX_SCISSOR_BOX, state->scissor_box);
    glGetIntegerv(GLX_SCISSOR_TEST, &state->scissor_test);
    glGetIntegerv(GLX_BLEND, &state->blend);
    glGetIntegerv(GLX_BLEND_SRC_RGB, &state->blend_src_rgb);
    glGetIntegerv(GLX_BLEND_DST_RGB, &state->blend_dst_rgb);
    glGetIntegerv(GLX_BLEND_SRC_ALPHA, &state->blend_src_alpha);
    glGetIntegerv(GLX_BLEND_DST_ALPHA, &state->blend_dst_alpha);
    glGetIntegerv(GLX_BLEND_EQUATION_RGB, &state->blend_equation_rgb);
    glGetIntegerv(GLX_BLEND_EQUATION_ALPHA, &state->blend_equation_alpha);
    glGetIntegerv(GLX_DEPTH_TEST, &state->depth_test);
    glGetIntegerv(GLX_DEPTH_FUNC, &state->depth_func);
    glGetIntegerv(GLX_DEPTH_WRITEMASK, &state->depth_mask);
    glGetIntegerv(GLX_STENCIL_TEST, &state->stencil_test);
    glGetIntegerv(GLX_STENCIL_FUNC, &state->stencil_func);
    glGetIntegerv(GLX_CULL_FACE, &state->cull_face);
    glGetIntegerv(GLX_FRONT_FACE, &state->front_face);
    glGetIntegerv(GLX_COLOR_WRITEMASK, state->color_mask);
    glGetIntegerv(GLX_CURRENT_PROGRAM, &state->program);
    glGetIntegerv(GLX_ACTIVE_TEXTURE, &state->active_texture);
    glGetIntegerv(GLX_TEXTURE_BINDING_2D, &state->texture_2d);
    glGetIntegerv(GLX_PACK_ALIGNMENT, &state->pack_alignment);
    state->read_format = 0;
    state->read_type = 0;
    glGetIntegerv(GLX_IMPLEMENTATION_COLOR_READ_FORMAT, &state->read_format);
    glGetIntegerv(GLX_IMPLEMENTATION_COLOR_READ_TYPE, &state->read_type);
    for (int guard = 0; guard < 16 && glGetError() != GL_NO_ERROR; guard++) {
    }
    state->query_error = GL_NO_ERROR;
}

/* Digest of the sources attached to the program bound at the draw. This is a
 * shader identity that is independent of any reconstruction; a program whose
 * sources are not retrievable reports no digest rather than a fabricated one. */
static void read_glsl_identity(GLint program, char out[65], int *count) {
    out[0] = 0;
    *count = 0;
    if (program <= 0) {
        return;
    }
    GLint attached = 0;
    glGetProgramiv((GLuint)program, GL_ATTACHED_SHADERS, &attached);
    if (attached <= 0 || attached > 8) {
        return;
    }
    GLuint shaders[8];
    GLsizei got = 0;
    glGetAttachedShaders((GLuint)program, 8, &got, shaders);
    if (got <= 0) {
        return;
    }
    CC_SHA256_CTX context;
    CC_SHA256_Init(&context);
    int sources = 0;
    for (GLsizei i = 0; i < got; i++) {
        GLint length = 0;
        glGetShaderiv(shaders[i], GL_SHADER_SOURCE_LENGTH, &length);
        if (length <= 0 || length > 65536) {
            continue;
        }
        char *source = (char *)malloc((size_t)length + 1);
        if (source == NULL) {
            continue;
        }
        GLsizei written = 0;
        glGetShaderSource(shaders[i], length, &written, source);
        if (written > 0) {
            CC_SHA256_Update(&context, &written, (CC_LONG)sizeof written);
            CC_SHA256_Update(&context, source, (CC_LONG)written);
            sources += 1;
        }
        free(source);
    }
    if (sources > 0) {
        uint8_t digest[CC_SHA256_DIGEST_LENGTH];
        CC_SHA256_Final(digest, &context);
        static const char digits[] = "0123456789abcdef";
        for (int i = 0; i < CC_SHA256_DIGEST_LENGTH; i++) {
            out[i * 2] = digits[digest[i] >> 4];
            out[i * 2 + 1] = digits[digest[i] & 0xf];
        }
        out[64] = 0;
        *count = sources;
    }
}

/* Original GLSL attached to the program bound at the selected draw. The digest
 * in gl-reference.json is the identity; the dumped sources are the authentic
 * bytes a Metal translation must reproduce. Observation only: the GL program is
 * read, never modified. */
static int dump_glsl_sources(GLint program, char *out, size_t capacity) {
    if (out == NULL || capacity == 0) {
        return 0;
    }
    int used = snprintf(out, capacity, "[");
    if (used < 0 || (size_t)used >= capacity) {
        out[0] = 0;
        return 0;
    }
    if (program <= 0) {
        (void)snprintf(out, capacity, "[]");
        return 0;
    }
    GLint attached = 0;
    glGetProgramiv((GLuint)program, GL_ATTACHED_SHADERS, &attached);
    if (attached <= 0 || attached > 8) {
        (void)snprintf(out, capacity, "[]");
        return 0;
    }
    GLuint shaders[8];
    GLsizei got = 0;
    glGetAttachedShaders((GLuint)program, 8, &got, shaders);
    int dumped = 0;
    for (GLsizei i = 0; i < got && i < 8; i++) {
        GLint length = 0;
        GLint type = 0;
        glGetShaderiv(shaders[i], GLX_SHADER_TYPE, &type);
        glGetShaderiv(shaders[i], GL_SHADER_SOURCE_LENGTH, &length);
        if (length <= 0 || length > 65536) {
            continue;
        }
        char *source = (char *)malloc((size_t)length + 1);
        if (source == NULL) {
            continue;
        }
        GLsizei written = 0;
        glGetShaderSource(shaders[i], length, &written, source);
        if (written <= 0) {
            free(source);
            continue;
        }
        const char *stage = type == GLX_VERTEX_SHADER
                                ? "vertex"
                                : type == GLX_FRAGMENT_SHADER ? "pixel" : "other";
        char name[96];
        (void)snprintf(name, sizeof name, "gl-reference-shader-%d-%s.glsl", (int)i,
                       stage);
        char path[1200];
        char digest[65] = {0};
        sha256_hex(source, (size_t)written, digest);
        int write_ok = 0;
        if (snprintf(path, sizeof path, "%s/%s", g_fb.run_dir, name) <
            (int)sizeof path) {
            write_ok = write_atomic(path, source, (size_t)written) == 0;
        }
        free(source);
        if (!write_ok) {
            continue;
        }
        int wrote = snprintf(out + used, capacity - (size_t)used,
                             "%s{\"stage\":\"%s\",\"file\":\"%s\",\"bytes\":%d,"
                             "\"sha256\":\"%s\"}",
                             dumped == 0 ? "" : ",", stage, name, (int)written,
                             digest);
        if (wrote < 0 || (size_t)(used + wrote) >= capacity) {
            break;
        }
        used += wrote;
        dumped += 1;
    }
    if ((size_t)used + 1 < capacity) {
        out[used++] = ']';
        out[used] = 0;
    }
    return dumped;
}

void eu4_gfx_observe_framebuffer_set_vertex_range(uint32_t vertex_stride,
                                                  uint32_t vertex_offset,
                                                  int32_t first, int32_t count) {
    g_fb.vertex_range_set = 0;
    if (!g_fb.enabled || vertex_stride == 0 || vertex_stride > 4096 || first < 0 ||
        count <= 0) {
        return;
    }
    uint64_t bytes = (uint64_t)(uint32_t)count * (uint64_t)vertex_stride;
    if (bytes == 0 || bytes > 65536) {
        return;
    }
    g_fb.vertex_stride = vertex_stride;
    g_fb.vertex_offset = vertex_offset;
    g_fb.vertex_first = first;
    g_fb.vertex_count = count;
    g_fb.vertex_range_set = 1;
}

/* Read the exact VBO range the selected draw consumed. The engine's array
 * buffer binding is saved/restored, and every failure is reported in JSON
 * rather than silently substituting bytes. */
static void capture_live_vertex(const gl_state *state, char *out,
                                size_t capacity) {
    (void)state;
    if (out == NULL || capacity == 0) {
        return;
    }
    (void)snprintf(out, capacity, "null");
    if (!g_fb.vertex_range_set) {
        return;
    }
    GLint array_binding = 0;
    GLint attrib_binding = 0;
    glGetIntegerv(GLX_ARRAY_BUFFER_BINDING, &array_binding);
    glGetVertexAttribiv(0, GLX_VERTEX_ATTRIB_ARRAY_BUFFER_BINDING,
                        &attrib_binding);
    GLint buffer = attrib_binding != 0 ? attrib_binding : array_binding;
    if (buffer <= 0) {
        (void)snprintf(out, capacity,
                       "{\"schema\":\"gfx-gl-live-vertex/v1\",\"status\":"
                       "\"no_array_buffer\",\"stride\":%u,\"offset\":%u,"
                       "\"first\":%d,\"count\":%d}",
                       g_fb.vertex_stride, g_fb.vertex_offset, g_fb.vertex_first,
                       g_fb.vertex_count);
        clear_gl_errors();
        return;
    }
    glBindBuffer(GL_ARRAY_BUFFER, (GLuint)buffer);
    GLint buffer_size = 0;
    glGetBufferParameteriv(GL_ARRAY_BUFFER, GLX_BUFFER_SIZE, &buffer_size);
    uint64_t start = (uint64_t)g_fb.vertex_offset +
                     (uint64_t)(uint32_t)g_fb.vertex_first *
                         (uint64_t)g_fb.vertex_stride;
    uint64_t bytes =
        (uint64_t)(uint32_t)g_fb.vertex_count * (uint64_t)g_fb.vertex_stride;
    if (buffer_size <= 0 || start > (uint64_t)buffer_size ||
        bytes > (uint64_t)buffer_size - start || bytes > 65536) {
        glBindBuffer(GL_ARRAY_BUFFER, (GLuint)array_binding);
        clear_gl_errors();
        (void)snprintf(out, capacity,
                       "{\"schema\":\"gfx-gl-live-vertex/v1\",\"status\":"
                       "\"range_unavailable\",\"buffer\":%d,\"buffer_size\":%d,"
                       "\"stride\":%u,\"offset\":%u,\"first\":%d,\"count\":%d}",
                       (int)buffer, (int)buffer_size, g_fb.vertex_stride,
                       g_fb.vertex_offset, g_fb.vertex_first, g_fb.vertex_count);
        return;
    }
    uint8_t data[65536];
    glGetBufferSubData(GL_ARRAY_BUFFER, (GLintptr)start, (GLsizeiptr)bytes, data);
    GLenum error = glGetError();
    glBindBuffer(GL_ARRAY_BUFFER, (GLuint)array_binding);
    if (error != GL_NO_ERROR) {
        clear_gl_errors();
        (void)snprintf(out, capacity,
                       "{\"schema\":\"gfx-gl-live-vertex/v1\",\"status\":"
                       "\"read_error\",\"gl_error\":%u,\"buffer\":%d,"
                       "\"buffer_size\":%d,\"stride\":%u,\"offset\":%u,"
                       "\"first\":%d,\"count\":%d}",
                       (unsigned)error, (int)buffer, (int)buffer_size,
                       g_fb.vertex_stride, g_fb.vertex_offset, g_fb.vertex_first,
                       g_fb.vertex_count);
        return;
    }
    char sha[65] = {0};
    sha256_hex(data, (size_t)bytes, sha);
    char name[64];
    (void)snprintf(name, sizeof name, "gl-reference-vertex-live.bin");
    char path[1200];
    if (snprintf(path, sizeof path, "%s/%s", g_fb.run_dir, name) >=
            (int)sizeof path ||
        write_atomic(path, data, (size_t)bytes) != 0) {
        (void)snprintf(out, capacity,
                       "{\"schema\":\"gfx-gl-live-vertex/v1\",\"status\":"
                       "\"write_failed\"}");
        return;
    }
    (void)snprintf(
        out, capacity,
        "{\"schema\":\"gfx-gl-live-vertex/v1\",\"status\":\"ok\",\"raw_file\":"
        "\"%s\",\"bytes\":%llu,\"sha256\":\"%s\",\"stride\":%u,\"offset\":%u,"
        "\"first\":%d,\"count\":%d,\"buffer\":%d,\"buffer_size\":%d,"
        "\"array_buffer_binding\":%d,\"attrib0_buffer_binding\":%d}",
        name, (unsigned long long)bytes, sha, g_fb.vertex_stride,
        g_fb.vertex_offset, g_fb.vertex_first, g_fb.vertex_count, (int)buffer,
        (int)buffer_size, (int)array_binding, (int)attrib_binding);
}

/* Authentic image of the texture bound at the selected draw. This is the
 * source data the original fragment shader sampled; the Metal replay uses the
 * same bytes. Bounded and observation-only. */
static void capture_texture(const gl_state *state, char *out, size_t capacity) {
    if (out == NULL || capacity == 0) {
        return;
    }
    (void)snprintf(out, capacity, "null");
    if (state == NULL || state->texture_2d <= 0) {
        return;
    }
    GLint width = -1, height = -1, internal_format = 0;
    glGetTexLevelParameteriv(GL_TEXTURE_2D, 0, GL_TEXTURE_WIDTH, &width);
    glGetTexLevelParameteriv(GL_TEXTURE_2D, 0, GL_TEXTURE_HEIGHT, &height);
    glGetTexLevelParameteriv(GL_TEXTURE_2D, 0, GL_TEXTURE_INTERNAL_FORMAT,
                             &internal_format);
    GLenum error = glGetError();
    if (error != GL_NO_ERROR || width <= 0 || height <= 0 || width > 8192 ||
        height > 8192) {
        clear_gl_errors();
        (void)snprintf(out, capacity,
                       "{\"schema\":\"gfx-gl-texture/v1\",\"status\":"
                       "\"unavailable\",\"gl_error\":%u,\"width\":%d,"
                       "\"height\":%d,\"internal_format\":%d}",
                       (unsigned)error, (int)width, (int)height,
                       (int)internal_format);
        return;
    }
    size_t bytes = (size_t)width * (size_t)height * 4u;
    if (bytes == 0 || bytes > 64u * 1024u * 1024u) {
        (void)snprintf(out, capacity,
                       "{\"schema\":\"gfx-gl-texture/v1\",\"status\":"
                       "\"too_large\",\"width\":%d,\"height\":%d}",
                       (int)width, (int)height);
        return;
    }
    uint8_t *pixels = (uint8_t *)malloc(bytes);
    if (pixels == NULL) {
        return;
    }
    pack_state pack;
    if (!pack_begin(&pack)) {
        (void)pack_end(&pack);
        free(pixels);
        return;
    }
    glGetTexImage(GL_TEXTURE_2D, 0, GL_BGRA, GL_UNSIGNED_BYTE, pixels);
    error = glGetError();
    (void)pack_end(&pack);
    if (error != GL_NO_ERROR) {
        clear_gl_errors();
        free(pixels);
        (void)snprintf(out, capacity,
                       "{\"schema\":\"gfx-gl-texture/v1\",\"status\":"
                       "\"read_error\",\"gl_error\":%u,\"width\":%d,"
                       "\"height\":%d}",
                       (unsigned)error, (int)width, (int)height);
        return;
    }
    char sha[65] = {0};
    sha256_hex(pixels, bytes, sha);
    char name[64];
    (void)snprintf(name, sizeof name, "gl-reference-texture.bgra");
    char path[1200];
    int wrote = 0;
    if (snprintf(path, sizeof path, "%s/%s", g_fb.run_dir, name) <
        (int)sizeof path) {
        wrote = write_atomic(path, pixels, bytes) == 0;
    }
    free(pixels);
    if (!wrote) {
        (void)snprintf(out, capacity,
                       "{\"schema\":\"gfx-gl-texture/v1\",\"status\":"
                       "\"write_failed\"}");
        return;
    }
    (void)snprintf(out, capacity,
                   "{\"schema\":\"gfx-gl-texture/v1\",\"status\":\"ok\","
                   "\"raw_file\":\"%s\",\"width\":%d,\"height\":%d,"
                   "\"internal_format\":%d,\"bytes\":%llu,"
                   "\"sha256\":\"%s\"}",
                   name, (int)width, (int)height, (int)internal_format,
                   (unsigned long long)bytes, sha);
}

/* Original GL capability bytes, read from the live master context the draw
 * used. Byte 4 drives CSettings::TestPOW2TextureCompatibility in the engine. */
static void read_caps(const eu4_gfx_observe_call *call, char out[17],
                      int *byte4) {
    out[0] = 0;
    *byte4 = -1;
    if (call == NULL || call->input_size < sizeof(uint64_t)) {
        return;
    }
    uint64_t handle = 0;
    memcpy(&handle, call->input, sizeof handle);
    uint64_t master = 0;
    if (handle == 0 ||
        !eu4_gfx_observe_read((const void *)(uintptr_t)handle, &master,
                              sizeof master) ||
        master == 0) {
        return;
    }
    uint8_t caps[8];
    if (!eu4_gfx_observe_read_bytes((const void *)(uintptr_t)(master + 0x188),
                                    caps, sizeof caps)) {
        return;
    }
    static const char digits[] = "0123456789abcdef";
    for (int i = 0; i < 8; i++) {
        out[i * 2] = digits[caps[i] >> 4];
        out[i * 2 + 1] = digits[caps[i] & 0xf];
    }
    out[16] = 0;
    *byte4 = caps[4];
}

typedef struct {
    uint64_t shader_object;
    uint32_t constant_words;
    uint32_t constant_bytes;
    uint8_t constant[128];
    int constant_ok;
} draw_inputs;

static void read_draw_inputs(const eu4_gfx_observe_call *call,
                             draw_inputs *out) {
    memset(out, 0, sizeof *out);
    if (call == NULL || call->input_size < sizeof(uint64_t)) {
        return;
    }
    uint64_t handle = 0;
    memcpy(&handle, call->input, sizeof handle);
    uint64_t master = 0;
    if (handle == 0 ||
        !eu4_gfx_observe_read((const void *)(uintptr_t)handle, &master,
                              sizeof master) ||
        master == 0) {
        return;
    }
    (void)eu4_gfx_observe_read((const void *)(uintptr_t)(master + 0x70),
                               &out->shader_object, sizeof out->shader_object);
    uint64_t header = 0;
    uint32_t words = 0;
    if (!eu4_gfx_observe_read((const void *)(uintptr_t)(master + 0xc8), &words,
                              sizeof words) ||
        !eu4_gfx_observe_read((const void *)(uintptr_t)(master + 0xd0), &header,
                              sizeof header) ||
        header == 0 || words == 0 || words > 32) {
        return;
    }
    uint64_t data = 0;
    if (!eu4_gfx_observe_read((const void *)(uintptr_t)(header + 8), &data,
                              sizeof data) ||
        data == 0) {
        return;
    }
    uint32_t bytes = words * 16u;
    if (bytes > sizeof out->constant) {
        bytes = sizeof out->constant;
    }
    if (!eu4_gfx_observe_read_bytes((const void *)(uintptr_t)data, out->constant,
                                    bytes)) {
        return;
    }
    out->constant_words = words;
    out->constant_bytes = bytes;
    out->constant_ok = 1;
}

static void clear_gl_errors(void) {
    for (int guard = 0; guard < 32 && glGetError() != GL_NO_ERROR; guard++) {
    }
}

static int pack_begin(pack_state *state) {
    memset(state, 0, sizeof *state);
    glGetIntegerv(GLX_PACK_ALIGNMENT, &state->alignment);
    glGetIntegerv(GLX_PACK_ROW_LENGTH, &state->row_length);
    glGetIntegerv(GLX_PACK_SKIP_ROWS, &state->skip_rows);
    glGetIntegerv(GLX_PACK_SKIP_PIXELS, &state->skip_pixels);
    glGetIntegerv(GLX_PIXEL_PACK_BUFFER_BINDING, &state->pack_buffer);
    clear_gl_errors();
    glBindBuffer(GLX_PIXEL_PACK_BUFFER, 0);
    glPixelStorei(GLX_PACK_ALIGNMENT, 1);
    glPixelStorei(GLX_PACK_ROW_LENGTH, 0);
    glPixelStorei(GLX_PACK_SKIP_ROWS, 0);
    glPixelStorei(GLX_PACK_SKIP_PIXELS, 0);
    if (g_test_fail_pack_config) {
        /* Test-only: an invalid alignment makes the driver set GL_INVALID_VALUE
         * through the same path a real configuration failure would use. */
        glPixelStorei(GLX_PACK_ALIGNMENT, 3);
    }
    GLenum error = glGetError();
    if (error != GL_NO_ERROR) {
        snprintf(g_last_reason, sizeof g_last_reason,
                 "pixel-pack configuration failed (GL error 0x%x)", (unsigned)error);
        clear_gl_errors();
        return 0;
    }
    return 1;
}

static int pack_end(const pack_state *state) {
    glPixelStorei(GLX_PACK_ALIGNMENT, state->alignment);
    glPixelStorei(GLX_PACK_ROW_LENGTH, state->row_length);
    glPixelStorei(GLX_PACK_SKIP_ROWS, state->skip_rows);
    glPixelStorei(GLX_PACK_SKIP_PIXELS, state->skip_pixels);
    glBindBuffer(GLX_PIXEL_PACK_BUFFER, (GLuint)state->pack_buffer);
    GLenum error = glGetError();
    clear_gl_errors();
    return error == GL_NO_ERROR;
}

static int framebuffer_supported(const gl_state *state, const char **reason) {
    if (state->draw_framebuffer != state->read_framebuffer) {
        *reason = "draw and read framebuffer bindings differ";
        return 0;
    }
    if (state->read_framebuffer != 0) {
        *reason = "non-default framebuffer is not supported by this observer";
        return 0;
    }
    if (state->read_buffer != GLX_READ_BUFFER_BACK) {
        *reason = "read buffer is not GL_BACK";
        return 0;
    }
    if (state->draw_buffer != GLX_READ_BUFFER_BACK) {
        *reason = "draw buffer is not GL_BACK";
        return 0;
    }
    return 1;
}

/* Reads the viewport rectangle, which is exactly the region this draw writes.
 * glFinish is the explicit GPU-completion boundary; it is diagnostic-only and
 * never runs unless the observer is armed. */
static int read_region(gl_state *state, uint8_t **out, size_t *out_len,
                       int *out_width, int *out_height, const char **out_format,
                       uint64_t *out_finish_ns, uint64_t *out_read_ns,
                       GLenum *out_error) {
    int width = state->viewport[2];
    int height = state->viewport[3];
    if (width <= 0 || height <= 0) {
        return 0;
    }
    size_t need = (size_t)width * (size_t)height * 4u;
    if (need == 0 || need > (size_t)MAX_PAYLOAD_BYTES) {
        return 0;
    }
    uint8_t *pixels = (uint8_t *)malloc(need);
    if (pixels == NULL) {
        return 0;
    }
    if (!pack_begin(&g_last_pack)) {
        g_last_pack_restored = pack_end(&g_last_pack);
        *out_error = GL_INVALID_OPERATION;
        free(pixels);
        return 0;
    }
    uint64_t before_finish = now_ns();
    glFinish();
    *out_finish_ns = now_ns() - before_finish;
    uint64_t before_read = now_ns();
    glReadPixels(state->viewport[0], state->viewport[1], width, height, GL_BGRA,
                 GL_UNSIGNED_INT_8_8_8_8_REV, pixels);
    GLenum error = glGetError();
    const char *format = "BGRA8";
    if (error != GL_NO_ERROR) {
        /* Apple's native read format is BGRA; keep a portable fallback so a
         * driver that refuses it records an honest format instead of no data. */
        glReadPixels(state->viewport[0], state->viewport[1], width, height,
                     GL_RGBA, GL_UNSIGNED_BYTE, pixels);
        error = glGetError();
        format = "RGBA8";
    }
    *out_read_ns = now_ns() - before_read;
    g_last_pack_restored = pack_end(&g_last_pack);
    if (!g_last_pack_restored) {
        error = GL_INVALID_OPERATION;
        g_last_reason[0] = 0;
        snprintf(g_last_reason, sizeof g_last_reason,
                 "pixel-pack state restore failed");
    }
    *out_error = error;
    if (error != GL_NO_ERROR) {
        free(pixels);
        return 0;
    }
    *out = pixels;
    *out_len = need;
    *out_width = width;
    *out_height = height;
    *out_format = format;
    return 1;
}

static void write_failure(const char *name, GLenum error, const gl_state *state,
                          const char *reason) {
    char body[512];
    int length = snprintf(
        body, sizeof body,
        "{\"schema\":\"gfx-gl-reference-failure/v1\",\"gl_error\":%u,"
        "\"reason\":\"%s\",\"viewport\":[%d,%d,%d,%d]}\n",
        (unsigned)error, reason != NULL ? reason : "", state->viewport[0],
        state->viewport[1], state->viewport[2], state->viewport[3]);
    char path[1200];
    if (length > 0 && (size_t)length < sizeof body &&
        snprintf(path, sizeof path, "%s/%s", g_fb.run_dir, name) <
            (int)sizeof path) {
        (void)write_atomic(path, body, (size_t)length);
    }
}

eu4_gfx_framebuffer_status eu4_gfx_observe_framebuffer_before_draw(
    const eu4_gfx_observe_call *call, int32_t mode, int32_t effective_count,
    int32_t first) {
    (void)mode;
    (void)effective_count;
    (void)first;
    if (!g_fb.enabled || !g_fb.want_pre) {
        return EU4_GFX_FRAMEBUFFER_CAPTURE_DISABLED;
    }
    if (call == NULL || !call->target_selected) {
        return EU4_GFX_FRAMEBUFFER_CAPTURE_NOT_SELECTED;
    }
    if (g_fb.claimed) {
        return EU4_GFX_FRAMEBUFFER_CAPTURE_ALREADY;
    }
    g_fb.claimed = 1;

    gl_state state;
    read_gl_state(&state);
    const char *reason = NULL;
    if (!framebuffer_supported(&state, &reason)) {
        write_failure("gl-reference-pre-failure.json", GL_INVALID_OPERATION, &state, reason);
        return EU4_GFX_FRAMEBUFFER_CAPTURE_GL;
    }
    uint8_t *pixels = NULL;
    size_t length = 0;
    int width = 0, height = 0;
    const char *format = "BGRA8";
    uint64_t finish_ns = 0, read_ns = 0;
    GLenum error = GL_NO_ERROR;
    if (!read_region(&state, &pixels, &length, &width, &height, &format,
                     &finish_ns, &read_ns, &error)) {
        write_failure("gl-reference-pre-failure.json", error, &state,
                      g_last_reason[0] != 0 ? g_last_reason : "framebuffer readback failed");
        return EU4_GFX_FRAMEBUFFER_CAPTURE_GL;
    }
    const char *suffix = strcmp(format, "BGRA8") == 0 ? "bgra" : "rgba";
    char sha[65] = {0};
    sha256_hex(pixels, length, sha);
    char path[1300];
    if (snprintf(path, sizeof path, "%s/gl-reference-pre.%s", g_fb.run_dir,
                 suffix) >= (int)sizeof path ||
        write_atomic(path, pixels, length) != 0) {
        free(pixels);
        return EU4_GFX_FRAMEBUFFER_CAPTURE_IO;
    }
    free(pixels);
    snprintf(g_fb.pre_name, sizeof g_fb.pre_name, "gl-reference-pre.%s", suffix);
    g_fb.pre_width = width;
    g_fb.pre_height = height;
    snprintf(g_fb.pre_format, sizeof g_fb.pre_format, "%s", format);
    g_fb.have_pre = 1;
    char sidecar[1024];
    int wrote = snprintf(
        sidecar, sizeof sidecar,
        "{\"schema\":\"gfx-gl-reference-pre/v1\",\"raw_file\":\"%s\","
        "\"raw_bytes\":%zu,\"raw_sha256\":\"%s\",\"width\":%d,\"height\":%d,"
        "\"pixel_format\":\"%s\",\"viewport\":[%d,%d,%d,%d],"
        "\"framebuffer\":%d,\"read_buffer\":%d,\"finish_ns\":%llu,"
        "\"read_ns\":%llu}\n",
        g_fb.pre_name, length, sha, width, height, format, state.viewport[0],
        state.viewport[1], state.viewport[2], state.viewport[3],
        state.draw_framebuffer, state.read_buffer,
        (unsigned long long)finish_ns, (unsigned long long)read_ns);
    if (wrote <= 0 || (size_t)wrote >= sizeof sidecar ||
        snprintf(path, sizeof path, "%s/gl-reference-pre.json", g_fb.run_dir) >=
            (int)sizeof path ||
        write_atomic(path, sidecar, (size_t)wrote) != 0) {
        return EU4_GFX_FRAMEBUFFER_CAPTURE_IO;
    }
    return EU4_GFX_FRAMEBUFFER_CAPTURE_OK;
}

eu4_gfx_framebuffer_status eu4_gfx_observe_framebuffer_after_draw(
    const eu4_gfx_observe_call *call, int32_t mode, int32_t effective_count,
    int32_t first) {
    if (!g_fb.enabled || !g_fb.want_post) {
        return EU4_GFX_FRAMEBUFFER_CAPTURE_DISABLED;
    }
    if (call == NULL || !call->target_selected) {
        return EU4_GFX_FRAMEBUFFER_CAPTURE_NOT_SELECTED;
    }
    gl_state state;
    read_gl_state(&state);
    const char *reason = NULL;
    if (!framebuffer_supported(&state, &reason)) {
        write_failure("gl-reference-failure.json", GL_INVALID_OPERATION, &state, reason);
        return EU4_GFX_FRAMEBUFFER_CAPTURE_GL;
    }
    char glsl_sha[65];
    int shader_count = 0;
    read_glsl_identity(state.program, glsl_sha, &shader_count);
    char caps_hex[17];
    int caps_byte4 = -1;
    read_caps(call, caps_hex, &caps_byte4);
    draw_inputs inputs;
    read_draw_inputs(call, &inputs);
    char constant_json[900] = "null";
    if (inputs.constant_ok) {
        char sha[65] = {0};
        sha256_hex(inputs.constant, inputs.constant_bytes, sha);
        char path[1200];
        if (snprintf(path, sizeof path, "%s/gl-reference-constant.bin",
                     g_fb.run_dir) < (int)sizeof path &&
            write_atomic(path, inputs.constant, inputs.constant_bytes) == 0) {
            char hex[257];
            static const char digits[] = "0123456789abcdef";
            uint32_t shown = inputs.constant_bytes > 128 ? 128 : inputs.constant_bytes;
            for (uint32_t i = 0; i < shown; i++) {
                hex[i * 2] = digits[inputs.constant[i] >> 4];
                hex[i * 2 + 1] = digits[inputs.constant[i] & 0xf];
            }
            hex[shown * 2] = 0;
            (void)snprintf(
                constant_json, sizeof constant_json,
                "{\"schema\":\"gfx-gl-constant/v1\",\"raw_file\":"
                "\"gl-reference-constant.bin\",\"words\":%u,\"bytes\":%u,"
                "\"sha256\":\"%s\",\"hex\":\"%s\"}",
                inputs.constant_words, inputs.constant_bytes, sha, hex);
        }
    }
    GLint texture_width = -1;
    GLint texture_height = -1;
    GLint texture_format = 0;
    char texture_json[700] = "null";
    capture_texture(&state, texture_json, sizeof texture_json);
    char live_vertex_json[1200] = "null";
    char shader_sources_json[4096] = "[]";
    capture_live_vertex(&state, live_vertex_json, sizeof live_vertex_json);
    (void)dump_glsl_sources(state.program, shader_sources_json,
                            sizeof shader_sources_json);
    uint8_t *pixels = NULL;
    size_t length = 0;
    int width = 0, height = 0;
    const char *format = "BGRA8";
    uint64_t finish_ns = 0, read_ns = 0;
    GLenum error = GL_NO_ERROR;
    if (!read_region(&state, &pixels, &length, &width, &height, &format,
                     &finish_ns, &read_ns, &error)) {
        write_failure("gl-reference-failure.json", error, &state,
                      g_last_reason[0] != 0 ? g_last_reason : "framebuffer readback failed");
        return EU4_GFX_FRAMEBUFFER_CAPTURE_GL;
    }
    const char *suffix = strcmp(format, "BGRA8") == 0 ? "bgra" : "rgba";
    char sha[65] = {0};
    sha256_hex(pixels, length, sha);
    char raw_name[96];
    snprintf(raw_name, sizeof raw_name, "gl-reference.%s", suffix);
    char path[1300];
    if (snprintf(path, sizeof path, "%s/%s", g_fb.run_dir, raw_name) >=
            (int)sizeof path ||
        write_atomic(path, pixels, length) != 0) {
        free(pixels);
        return EU4_GFX_FRAMEBUFFER_CAPTURE_IO;
    }
    free(pixels);

    uint32_t drawable_width = 0, drawable_height = 0;
    int have_drawable =
        eu4_gfx_gl_drawable_extent(&drawable_width, &drawable_height);
    char drawable_width_text[16] = "null";
    char drawable_height_text[16] = "null";
    if (have_drawable) {
        snprintf(drawable_width_text, sizeof drawable_width_text, "%u",
                 drawable_width);
        snprintf(drawable_height_text, sizeof drawable_height_text, "%u",
                 drawable_height);
    }
    char pre_json[384] = "null";
    if (g_fb.have_pre) {
        snprintf(pre_json, sizeof pre_json,
                 "{\"raw_file\":\"%s\",\"width\":%d,\"height\":%d,"
                 "\"pixel_format\":\"%s\",\"origin\":\"lower_left\"}",
                 g_fb.pre_name, g_fb.pre_width, g_fb.pre_height,
                 g_fb.pre_format);
    }
    char captured[64];
    timestamp_utc(captured, sizeof captured);
    char body[8192];
    int length_json = snprintf(
        body, sizeof body,
        "{\"schema\":\"gfx-gl-reference/v1\",\"backend\":\"opengl\","
        "\"captured_utc\":\"%s\","
        "\"provenance\":{\"executable\":\"%s\",\"executable_sha256\":\"%s\","
        "\"library_sha256\":\"%s\",\"session\":\"%s\",\"pid\":%d},"
        "\"draw\":{\"symbol\":\"_GfxDraw\",\"call_id\":%llu,\"caller_rva\":"
        "\"0x%llx\",\"caller_known\":%s,\"thread_id\":%llu,\"mode\":%d,"
        "\"effective_count\":%d,\"first\":%d,\"shader_object\":\"0x%llx\"},"
        "\"target\":{\"framebuffer\":%d,\"read_framebuffer\":%d,"
        "\"read_buffer\":%d,\"draw_buffer\":%d,\"viewport\":[%d,%d,%d,%d],"
        "\"width\":%d,\"height\":%d,\"pixel_format\":\"%s\","
        "\"origin\":\"lower_left\",\"row_stride\":%d,"
        "\"drawable_width\":%s,\"drawable_height\":%s},"
        "\"gpu\":{\"finish\":\"glFinish\",\"finish_ns\":%llu,"
        "\"read_pixels\":true,\"read_ns\":%llu,\"gl_error\":%u},"
        "\"state\":{\"pack_alignment\":%d,\"scissor_test\":%d,"
        "\"scissor_box\":[%d,%d,%d,%d],\"blend\":%d,\"blend_src_rgb\":%d,"
        "\"blend_dst_rgb\":%d,\"blend_src_alpha\":%d,\"blend_dst_alpha\":%d,"
        "\"blend_equation_rgb\":%d,\"blend_equation_alpha\":%d,"
        "\"depth_test\":%d,\"depth_func\":%d,\"depth_mask\":%d,"
        "\"stencil_test\":%d,\"stencil_func\":%d,\"cull_face\":%d,"
        "\"front_face\":%d,\"color_mask\":[%d,%d,%d,%d],"
        "\"program\":%d,\"active_texture\":%d,\"texture_2d\":%d,"
        "\"implementation_read_format\":%d,\"implementation_read_type\":%d},"
        "\"shader\":{\"program\":%d,\"glsl_sha256\":\"%s\","
        "\"shader_count\":%d},"
        "\"caps\":{\"bytes_hex\":\"%s\",\"byte4\":%d},"
        "\"pack\":{\"saved_alignment\":%d,\"saved_row_length\":%d,"
        "\"saved_skip_rows\":%d,\"saved_skip_pixels\":%d,"
        "\"saved_pack_buffer\":%d,\"restored\":%s},"
        "\"settings\":{\"force_pow2_source\":\"engine TestPOW2TextureCompatibility\"},"
        "\"capture\":{\"raw_file\":\"%s\",\"raw_bytes\":%zu,"
        "\"raw_sha256\":\"%s\",\"pixel_format\":\"%s\"},"
        "\"pre_draw\":%s,"
        "\"texture\":{\"name\":%d,\"width\":%d,\"height\":%d,"
        "\"internal_format\":%d},"
        "\"texture_image\":%s,"
        "\"constants\":%s,\"shader_sources\":%s,\"live_vertex\":%s}\n",
        captured, env_text("EU4_GFX_EXPECTED_EXE"),
        env_text("EU4_GFX_EXECUTABLE_SHA256"),
        env_text("EU4_GFX_LIBRARY_SHA256"), env_text("EU4_GFX_STARTUP_ID"),
        (int)getpid(), (unsigned long long)call->call_id,
        (unsigned long long)call->caller_rva,
        call->caller_known ? "true" : "false",
        (unsigned long long)call->thread_id, mode, effective_count, first,
        (unsigned long long)inputs.shader_object,
        state.draw_framebuffer, state.read_framebuffer, state.read_buffer,
        state.draw_buffer, state.viewport[0], state.viewport[1], state.viewport[2],
        state.viewport[3], width, height, format, width * 4,
        drawable_width_text, drawable_height_text,
        (unsigned long long)finish_ns, (unsigned long long)read_ns,
        (unsigned)error, state.pack_alignment, state.scissor_test,
        state.scissor_box[0], state.scissor_box[1], state.scissor_box[2],
        state.scissor_box[3], state.blend, state.blend_src_rgb,
        state.blend_dst_rgb, state.blend_src_alpha, state.blend_dst_alpha,
        state.blend_equation_rgb, state.blend_equation_alpha, state.depth_test,
        state.depth_func, state.depth_mask, state.stencil_test,
        state.stencil_func, state.cull_face, state.front_face,
        state.color_mask[0], state.color_mask[1], state.color_mask[2],
        state.color_mask[3], state.program, state.active_texture,
        state.texture_2d, state.read_format, state.read_type, state.program,
        glsl_sha, shader_count, caps_hex, caps_byte4, g_last_pack.alignment,
        g_last_pack.row_length, g_last_pack.skip_rows, g_last_pack.skip_pixels,
        g_last_pack.pack_buffer, g_last_pack_restored ? "true" : "false",
        raw_name, length, sha, format, pre_json, state.texture_2d, texture_width,
        texture_height, texture_format, texture_json, constant_json,
        shader_sources_json, live_vertex_json);
    if (length_json <= 0 || (size_t)length_json >= sizeof body) {
        return EU4_GFX_FRAMEBUFFER_CAPTURE_IO;
    }
    if (snprintf(path, sizeof path, "%s/gl-reference.json", g_fb.run_dir) >=
            (int)sizeof path ||
        write_atomic(path, body, (size_t)length_json) != 0) {
        return EU4_GFX_FRAMEBUFFER_CAPTURE_IO;
    }
    if (!have_drawable) {
        const char *note =
            "{\"schema\":\"gfx-gl-drawable-unavailable/v1\","
            "\"reason\":\"no current NSOpenGL view\"}\n";
        if (snprintf(path, sizeof path,
                     "%s/gl-reference-drawable-unavailable.json",
                     g_fb.run_dir) < (int)sizeof path) {
            (void)write_atomic(path, note, strlen(note));
        }
    }
    atomic_fetch_add_explicit(&g_captures, 1, memory_order_relaxed);
    return EU4_GFX_FRAMEBUFFER_CAPTURE_OK;
}

/* --- Focused native test hooks (not used by the observer) --------------- */

void eu4_gfx_observe_framebuffer_test_fail_pack_config(int on) {
    g_test_fail_pack_config = on ? 1 : 0;
}

static int same_pack(const pack_state *left, const pack_state *right) {
    return left->alignment == right->alignment &&
           left->row_length == right->row_length &&
           left->skip_rows == right->skip_rows &&
           left->skip_pixels == right->skip_pixels &&
           left->pack_buffer == right->pack_buffer;
}

static void capture_pack(pack_state *state) {
    memset(state, 0, sizeof *state);
    glGetIntegerv(GLX_PACK_ALIGNMENT, &state->alignment);
    glGetIntegerv(GLX_PACK_ROW_LENGTH, &state->row_length);
    glGetIntegerv(GLX_PACK_SKIP_ROWS, &state->skip_rows);
    glGetIntegerv(GLX_PACK_SKIP_PIXELS, &state->skip_pixels);
    glGetIntegerv(GLX_PIXEL_PACK_BUFFER_BINDING, &state->pack_buffer);
}

int eu4_gfx_observe_framebuffer_test_pack_roundtrip(void) {
    pack_state before;
    capture_pack(&before);
    if (!pack_begin(&g_last_pack)) {
        return 1;
    }
    if (!pack_end(&g_last_pack)) {
        return 2;
    }
    pack_state after;
    capture_pack(&after);
    return same_pack(&before, &after) ? 0 : 3;
}

int eu4_gfx_observe_framebuffer_test_pack_failure(void) {
    pack_state before;
    capture_pack(&before);
    g_test_fail_pack_config = 1;
    int began = pack_begin(&g_last_pack);
    g_test_fail_pack_config = 0;
    int restored = pack_end(&g_last_pack);
    pack_state after;
    capture_pack(&after);
    if (began) {
        return 1;
    }
    if (!restored) {
        return 2;
    }
    return same_pack(&before, &after) ? 0 : 3;
}

int eu4_gfx_observe_framebuffer_test_framebuffer(int draw_framebuffer,
                                                 int read_framebuffer,
                                                 int read_buffer,
                                                 int draw_buffer) {
    gl_state state;
    memset(&state, 0, sizeof state);
    state.draw_framebuffer = draw_framebuffer;
    state.read_framebuffer = read_framebuffer;
    state.read_buffer = read_buffer;
    state.draw_buffer = draw_buffer;
    const char *reason = NULL;
    return framebuffer_supported(&state, &reason);
}
