/* Complete-frame original-GL trace.
 *
 * See eu4_gfx_frame_trace.h for the contract. This module is observation-only:
 * every GL binding it must change for a readback is saved and restored, and it
 * never issues a draw, clear or state change of its own.
 */

#define GL_SILENCE_DEPRECATION 1
#define GL_GLEXT_PROTOTYPES 1

#include "eu4_gfx_frame_trace.h"

#include "eu4_gfx_observe_framebuffer.h"
/* The state host and manifest host link this file without the observation
   stream.  Weak declarations keep those diagnostics builds valid. */
#if defined(__APPLE__)
#define EU4_OBSERVE_WEAK __attribute__((weak_import))
#else
#define EU4_OBSERVE_WEAK __attribute__((weak))
#endif
extern int eu4_gfx_frame_observe_init(const char *, const char *, const char *)
    EU4_OBSERVE_WEAK;
extern int eu4_gfx_frame_observe_enabled(void) EU4_OBSERVE_WEAK;
extern int eu4_gfx_frame_observe_lockstep_enabled(void) EU4_OBSERVE_WEAK;
extern int eu4_gfx_frame_observe_lockstep_wait(uint32_t, uint32_t)
    EU4_OBSERVE_WEAK;
extern int eu4_gfx_frame_observe_wants(uint32_t) EU4_OBSERVE_WEAK;
extern int eu4_gfx_frame_observe_emit_metadata(uint32_t, uint32_t, const char *,
                                               uint32_t, uint32_t, uint64_t,
                                               uint32_t, int, const char *)
    EU4_OBSERVE_WEAK;
extern int eu4_gfx_frame_observe_emit(const uint8_t *, uint32_t, uint32_t,
                                      const char *, uint32_t, uint32_t, uint64_t,
                                      uint32_t, int, int, const char *)
    EU4_OBSERVE_WEAK;

#include <CommonCrypto/CommonDigest.h>
#include <OpenGL/gl.h>
#include <OpenGL/glext.h>

#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>
#include <dlfcn.h>

typedef void (*SamplerParameterivFn)(GLuint, GLenum, GLint *);
typedef void (*SamplerParameterfvFn)(GLuint, GLenum, GLfloat *);
static SamplerParameterivFn g_glGetSamplerParameteriv;
static SamplerParameterfvFn g_glGetSamplerParameterfv;
static void load_sampler_queries(void) {
    if (g_glGetSamplerParameteriv == NULL) {
        g_glGetSamplerParameteriv = (SamplerParameterivFn)dlsym(
            RTLD_DEFAULT, "glGetSamplerParameteriv");
    }
    if (g_glGetSamplerParameterfv == NULL) {
        g_glGetSamplerParameterfv = (SamplerParameterfvFn)dlsym(
            RTLD_DEFAULT, "glGetSamplerParameterfv");
    }
}

#define FTRACE_PATH 1024u
#define FTRACE_MAX_ATTRS 16
#define FTRACE_MAX_UNITS 16
#define FTRACE_MAX_UNIFORMS 64
#define FTRACE_MAX_SHADERS 8
#define FTRACE_MAX_CALLERS 32
#define FTRACE_MAX_LEVELS 16
#define FTRACE_MAX_EVENTS 16384
#define FTRACE_MAX_TEX_BYTES (256u * 1024u * 1024u)
#define FTRACE_NAME 96

#define GL_TEXTURE_MAX_ANISOTROPY 0x84FE
#define GL_TEXTURE_BINDING_CUBE_MAP_VALUE 0x8514
#define GL_TEXTURE_BINDING_3D_VALUE 0x806A
#define GL_VERTEX_ATTRIB_ARRAY_DIVISOR 0x88FE
#define GL_COMPRESSED_DXT1 0x83F0
#define GL_COMPRESSED_DXT3 0x83F2
#define GL_COMPRESSED_DXT5 0x83F3
#define GL_RGBA32F_VALUE 0x8814
#define GL_RGB32F_VALUE 0x8815
#define GL_RGBA16F_VALUE 0x881A
#define GL_RGB16F_VALUE 0x881B
#define GL_RGBA16_VALUE 0x805B
#define GL_SRGB8_ALPHA8_VALUE 0x8C43
#define GL_DEPTH32F_STENCIL8_VALUE 0x8CAC
#define GL_SAMPLER_2D_RECT_VALUE 0x8B63
#define GL_SAMPLER_BINDING_VALUE 0x8919

static struct {
    int configured;
    int enabled;
    char dir[FTRACE_PATH];
    uint64_t callers[FTRACE_MAX_CALLERS];
    int caller_count;
    uint32_t min_draws;
    uint32_t max_frame_draws;
    uint32_t occurrence;
    uint64_t at_frame; /* UINT64_MAX when unset */
    uint64_t after_ms;
    uint64_t start_ms;
    int boundary_fb; /* -1: present boundary; >=0: clear of this framebuffer */
    int boundary_scene; /* PresentScene entry/exit boundary */
    int stream_only; /* per-frame observation without a frame-trace capture */
    int scene_index;
    uint64_t pre_post_callers[16];
    int pre_post_count;
    uint32_t max_draws;

    int present_index;
    int captured;
    int armed;
    int capturing_frame;
    int matching_frames;
    int event_count;
    int have_seed;

    /* Caller set of the frame currently being observed (cheap, always on). */
    uint64_t frame_callers[FTRACE_MAX_CALLERS];
    int frame_caller_count;
    uint32_t frame_draws;

    FILE *events;
    char partial_path[FTRACE_PATH];
    char final_path[FTRACE_PATH];
    char reference_file[FTRACE_NAME];
    char reference_sha[65];
    size_t reference_bytes;
    uint32_t reference_width;
    uint32_t reference_height;

    char gl_vendor[256];
    char gl_renderer[256];
    char gl_version[256];
    char pre_image_json[2048];
    int pre_image_valid;
    char seed_json[2048];
    int seed_width;
    int seed_height;
    int seed_emitted;
    int truncated;

    struct blob_entry *blobs;
    int blob_count;

    /* Per-frame caches. Shader sources are static per program; a texture's
       contents are static for the frame unless it is a render target. Reading
       every texture for every draw is what makes a several-thousand-draw menu
       frame unaffordable. */
    struct shader_cache_entry *shader_cache;
    struct texture_cache_entry *texture_cache;
    char render_targets[64][64];
    int render_target_count;
} g;

typedef struct blob_entry {
    char sha[65];
    char name[FTRACE_NAME];
    struct blob_entry *next;
} blob_entry;

typedef struct shader_cache_entry {
    uint64_t program;
    char *json;
    struct shader_cache_entry *next;
} shader_cache_entry;

typedef struct texture_cache_entry {
    char key[96];
    char *levels_json;
    struct texture_cache_entry *next;
} texture_cache_entry;

static int render_target_known(const char *name) {
    for (int i = 0; i < g.render_target_count; i++) {
        if (strcmp(g.render_targets[i], name) == 0) {
            return 1;
        }
    }
    return 0;
}

static void render_target_add(const char *name) {
    if (name == NULL || name[0] == 0 || render_target_known(name)) {
        return;
    }
    if (g.render_target_count < 64) {
        snprintf(g.render_targets[g.render_target_count], 64, "%s", name);
        g.render_target_count++;
    }
}

static void frame_caches_reset(void) {
    for (shader_cache_entry *entry = g.shader_cache; entry != NULL;) {
        shader_cache_entry *next = entry->next;
        free(entry->json);
        free(entry);
        entry = next;
    }
    g.shader_cache = NULL;
    for (texture_cache_entry *entry = g.texture_cache; entry != NULL;) {
        texture_cache_entry *next = entry->next;
        free(entry->levels_json);
        free(entry);
        entry = next;
    }
    g.texture_cache = NULL;
    g.render_target_count = 0;
}

static const char *shader_cache_get(uint64_t program) {
    for (shader_cache_entry *entry = g.shader_cache; entry != NULL;
         entry = entry->next) {
        if (entry->program == program) {
            return entry->json;
        }
    }
    return NULL;
}

static void shader_cache_put(uint64_t program, const char *json) {
    shader_cache_entry *entry = calloc(1, sizeof(*entry));
    if (entry == NULL) {
        return;
    }
    entry->program = program;
    entry->json = strdup(json);
    entry->next = g.shader_cache;
    g.shader_cache = entry;
}

static const char *texture_cache_get(const char *key) {
    for (texture_cache_entry *entry = g.texture_cache; entry != NULL;
         entry = entry->next) {
        if (strcmp(entry->key, key) == 0) {
            return entry->levels_json;
        }
    }
    return NULL;
}

static void texture_cache_put(const char *key, const char *levels) {
    texture_cache_entry *entry = calloc(1, sizeof(*entry));
    if (entry == NULL) {
        return;
    }
    snprintf(entry->key, sizeof(entry->key), "%s", key);
    entry->levels_json = strdup(levels);
    entry->next = g.texture_cache;
    g.texture_cache = entry;
}

static pthread_mutex_t g_lock = PTHREAD_MUTEX_INITIALIZER;

/* Entry-side draw arguments. The gateway's exit frame holds post-call
 * registers, so every engine argument the packet needs is captured here. */
typedef struct {
    uint64_t call_id;
    uintptr_t master;
    int valid;
    int indexed;
    int instanced;
    int mode;
    int first;
    int count;
    int instances;
    int base_vertex;
} pending_draw;

static _Thread_local pending_draw g_pending;

static void sha256_hex(const void *data, size_t size, char out[65]) {
    unsigned char digest[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256(data, (CC_LONG)size, digest);
    static const char hex[] = "0123456789abcdef";
    for (int i = 0; i < CC_SHA256_DIGEST_LENGTH; i++) {
        out[i * 2] = hex[digest[i] >> 4];
        out[i * 2 + 1] = hex[digest[i] & 0xf];
    }
    out[64] = 0;
}

static int mkdir_p(const char *path) {
    char buffer[FTRACE_PATH];
    size_t length = strlen(path);
    if (length == 0 || length >= sizeof(buffer)) {
        return -1;
    }
    memcpy(buffer, path, length + 1);
    for (size_t i = 1; i < length; i++) {
        if (buffer[i] != '/') {
            continue;
        }
        buffer[i] = 0;
        if (mkdir(buffer, 0755) != 0 && errno != EEXIST) {
            return -1;
        }
        buffer[i] = '/';
    }
    if (mkdir(buffer, 0755) != 0 && errno != EEXIST) {
        return -1;
    }
    return 0;
}

static int write_atomic(const char *path, const void *data, size_t size) {
    char partial[FTRACE_PATH];
    if (snprintf(partial, sizeof(partial), "%s.partial", path) < 0) {
        return -1;
    }
    int fd = open(partial, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd < 0) {
        return -1;
    }
    const uint8_t *bytes = data;
    size_t written = 0;
    while (written < size) {
        ssize_t step = write(fd, bytes + written, size - written);
        if (step <= 0) {
            close(fd);
            unlink(partial);
            return -1;
        }
        written += (size_t)step;
    }
    if (fsync(fd) != 0) {
        close(fd);
        unlink(partial);
        return -1;
    }
    close(fd);
    return rename(partial, path) == 0 ? 0 : -1;
}

/* Writes a payload blob once and returns its content-addressed file name. */
static int put_blob(const void *data, size_t size, const char *prefix,
                    char out_name[FTRACE_NAME], char out_sha[65]) {
    char sha[65];
    sha256_hex(data, size, sha);
    if (out_sha != NULL) {
        snprintf(out_sha, 65, "%s", sha);
    }
    for (blob_entry *entry = g.blobs; entry != NULL; entry = entry->next) {
        if (strcmp(entry->sha, sha) == 0) {
            snprintf(out_name, FTRACE_NAME, "%s", entry->name);
            return 0;
        }
    }
    char name[FTRACE_NAME];
    snprintf(name, sizeof(name), "%s-%s.bin", prefix, sha);
    char path[FTRACE_PATH];
    snprintf(path, sizeof(path), "%s/blobs/%s", g.dir, name);
    if (write_atomic(path, data, size) != 0) {
        return -1;
    }
    blob_entry *entry = calloc(1, sizeof(*entry));
    if (entry == NULL) {
        return -1;
    }
    snprintf(entry->sha, sizeof(entry->sha), "%s", sha);
    snprintf(entry->name, sizeof(entry->name), "%s", name);
    entry->next = g.blobs;
    g.blobs = entry;
    g.blob_count++;
    snprintf(out_name, FTRACE_NAME, "%s", name);
    return 0;
}

/* A small JSON string builder over a stream. */
typedef struct {
    FILE *file;
    int failed;
} json_out;

static void jprintf(json_out *out, const char *format, ...) {
    if (out->failed || out->file == NULL) {
        return;
    }
    va_list args;
    va_start(args, format);
    if (vfprintf(out->file, format, args) < 0) {
        out->failed = 1;
    }
    va_end(args);
}

static const char *bool_text(int value) { return value ? "true" : "false"; }

/* ------------------------------------------------------------------------- */
/* Configuration                                                              */
/* ------------------------------------------------------------------------- */

static uint64_t now_ms(void) {
    struct timespec spec;
    clock_gettime(CLOCK_MONOTONIC, &spec);
    return (uint64_t)spec.tv_sec * 1000ull + (uint64_t)spec.tv_nsec / 1000000ull;
}

static int parse_u64(const char *text, uint64_t *out) {
    if (text == NULL || *text == 0) {
        return -1;
    }
    char *end = NULL;
    errno = 0;
    unsigned long long value = strtoull(text, &end, 0);
    if (errno != 0 || end == text || *end != 0) {
        return -1;
    }
    *out = (uint64_t)value;
    return 0;
}

int eu4_gfx_frame_trace_configure_from_env(void) {
    memset(&g, 0, sizeof(g));
    g.at_frame = UINT64_MAX;
    g.boundary_fb = -1;
    const char *dir = getenv("EU4_GFX_FRAME_TRACE_DIR");
    const char *boundary_fb = getenv("EU4_GFX_FRAME_TRACE_BOUNDARY_FB");
    const char *boundary = getenv("EU4_GFX_FRAME_TRACE_BOUNDARY");
    const char *pre_post = getenv("EU4_GFX_FRAME_TRACE_PRE_POST_CALLERS");
    const char *test_only = getenv("EU4_GFX_FRAME_TRACE_TEST_ONLY");
    const char *callers = getenv("EU4_GFX_FRAME_TRACE_CALLERS");
    const char *min_draws = getenv("EU4_GFX_FRAME_TRACE_MIN_DRAWS");
    const char *occurrence = getenv("EU4_GFX_FRAME_TRACE_OCCURRENCE");
    const char *at_frame = getenv("EU4_GFX_FRAME_TRACE_AT_FRAME");
    const char *after_ms = getenv("EU4_GFX_FRAME_TRACE_AFTER_MS");
    const char *max_draws = getenv("EU4_GFX_FRAME_TRACE_MAX_DRAWS");
    const char *max_frame_draws = getenv("EU4_GFX_FRAME_TRACE_MAX_FRAME_DRAWS");
    const char *observe_dir = getenv("EU4_GFX_FRAME_OBSERVE_DIR");
    g.configured = 1;
    g.enabled = 0;
    if (observe_dir != NULL && observe_dir[0] == '/') {
        g.stream_only = (dir == NULL || dir[0] == 0);
        if (g.stream_only) {
            g.enabled = 1;
            g.boundary_scene = 1;
            snprintf(g.dir, sizeof(g.dir), "%s", observe_dir);
        }
        if (eu4_gfx_frame_observe_init != NULL) {
            const char *session = getenv("EU4_GFX_FRAME_SESSION");
            if (session == NULL || session[0] == 0) {
                session = getenv("EU4_GFX_STARTUP_ID");
            }
            (void)eu4_gfx_frame_observe_init("opengl", observe_dir, session);
        }
    }
    if (dir == NULL || dir[0] == 0) {
        if (test_only != NULL && strcmp(test_only, "1") == 0) {
            return 0;
        }
        return 0;
    }
    if (dir[0] != '/') {
        return -1;
    }
    snprintf(g.dir, sizeof(g.dir), "%s", dir);
    g.occurrence = 1;
    g.max_draws = FTRACE_MAX_EVENTS;
    if (min_draws != NULL) {
        uint64_t value = 0;
        if (parse_u64(min_draws, &value) != 0) {
            return -1;
        }
        g.min_draws = (uint32_t)value;
    }
    if (occurrence != NULL) {
        uint64_t value = 0;
        if (parse_u64(occurrence, &value) != 0 || value == 0) {
            return -1;
        }
        g.occurrence = (uint32_t)value;
    }
    if (at_frame != NULL && parse_u64(at_frame, &g.at_frame) != 0) {
        return -1;
    }
    if (boundary != NULL && strcmp(boundary, "scene") == 0) {
        g.boundary_scene = 1;
    }
    if (pre_post != NULL && pre_post[0] != 0) {
        char copy[512];
        snprintf(copy, sizeof(copy), "%s", pre_post);
        char *save = NULL;
        for (char *token = strtok_r(copy, ",", &save); token != NULL;
             token = strtok_r(NULL, ",", &save)) {
            if (g.pre_post_count >= 16) {
                return -1;
            }
            uint64_t value = 0;
            if (parse_u64(token, &value) != 0) {
                return -1;
            }
            g.pre_post_callers[g.pre_post_count++] = value;
        }
    }
    if (boundary_fb != NULL && boundary_fb[0] != 0 &&
        strcmp(boundary_fb, "-1") != 0) {
        uint64_t value = 0;
        if (parse_u64(boundary_fb, &value) != 0 || value > 1024) {
            return -1;
        }
        g.boundary_fb = (int)value;
    }
    if (after_ms != NULL) {
        uint64_t value = 0;
        if (parse_u64(after_ms, &value) != 0) {
            return -1;
        }
        g.after_ms = value;
    }
    g.start_ms = now_ms();
    if (max_draws != NULL) {
        uint64_t value = 0;
        if (parse_u64(max_draws, &value) != 0 || value == 0) {
            return -1;
        }
        g.max_draws = (uint32_t)value;
    }
    if (max_frame_draws != NULL) {
        uint64_t value = 0;
        if (parse_u64(max_frame_draws, &value) != 0) {
            return -1;
        }
        g.max_frame_draws = (uint32_t)value; /* 0 means unlimited */
    }
    if (callers != NULL && callers[0] != 0) {
        char copy[512];
        snprintf(copy, sizeof(copy), "%s", callers);
        char *save = NULL;
        for (char *token = strtok_r(copy, ",", &save); token != NULL;
             token = strtok_r(NULL, ",", &save)) {
            if (g.caller_count >= FTRACE_MAX_CALLERS) {
                return -1;
            }
            uint64_t value = 0;
            if (parse_u64(token, &value) != 0) {
                return -1;
            }
            g.callers[g.caller_count++] = value;
        }
    }
    if (mkdir_p(g.dir) != 0) {
        return -1;
    }
    char blobs[FTRACE_PATH];
    snprintf(blobs, sizeof(blobs), "%s/blobs", g.dir);
    if (mkdir_p(blobs) != 0) {
        return -1;
    }
    snprintf(g.partial_path, sizeof(g.partial_path), "%s/frame-events.jsonl",
             g.dir);
    snprintf(g.final_path, sizeof(g.final_path), "%s/frame-events.jsonl", g.dir);
    load_sampler_queries();
    g.enabled = 1;
    return 1;
}

int eu4_gfx_frame_trace_enabled(void) { return g.enabled; }

/* ------------------------------------------------------------------------- */
/* Frame selection                                                            */
/* ------------------------------------------------------------------------- */

static int caller_seen(uint64_t rva) {
    for (int i = 0; i < g.frame_caller_count; i++) {
        if (g.frame_callers[i] == rva) {
            return 1;
        }
    }
    return 0;
}

static void caller_add(uint64_t rva) {
    if (rva == 0 || caller_seen(rva)) {
        return;
    }
    if (g.frame_caller_count < FTRACE_MAX_CALLERS) {
        g.frame_callers[g.frame_caller_count++] = rva;
    }
}

static int caller_set_matches(void) {
    if (g.caller_count == 0) {
        return 1;
    }
    for (int i = 0; i < g.caller_count; i++) {
        if (!caller_seen(g.callers[i])) {
            return 0;
        }
    }
    return 1;
}

static void frame_reset(void) {
    g.frame_caller_count = 0;
    g.frame_draws = 0;
}

static void emit_event(const char *json) {
    if (g.events == NULL) {
        return;
    }
    fputs(json, g.events);
    fputc('\n', g.events);
    if (fflush(g.events) != 0) {
        g.events = NULL;
    }
}

/* ------------------------------------------------------------------------- */
/* GL helpers                                                                 */
/* ------------------------------------------------------------------------- */

static const char *texture_target_name(GLenum target) {
    switch (target) {
        case GL_TEXTURE_2D: return "2D";
        case GL_TEXTURE_CUBE_MAP: return "cube";
        case GL_TEXTURE_3D: return "3D";
        case GL_TEXTURE_1D: return "1D";
        default: return "unknown";
    }
}

static GLenum texture_binding_query(GLenum target) {
    switch (target) {
        case GL_TEXTURE_2D: return GL_TEXTURE_BINDING_2D;
        case GL_TEXTURE_CUBE_MAP: return GL_TEXTURE_BINDING_CUBE_MAP;
        case GL_TEXTURE_3D: return GL_TEXTURE_BINDING_3D;
        case GL_TEXTURE_1D: return GL_TEXTURE_BINDING_1D;
        default: return GL_TEXTURE_BINDING_2D;
    }
}

static const char *format_name(GLenum format) {
    switch (format) {
        case GL_RGBA: return "RGBA";
        case GL_BGRA: return "BGRA";
        case GL_RGBA8: return "RGBA8";
        case GL_RGB: return "RGB";
        case GL_RGB8: return "RGB8";
        case GL_COMPRESSED_DXT1: return "DXT1";
        case GL_COMPRESSED_DXT3: return "DXT3";
        case GL_COMPRESSED_DXT5: return "DXT5";
        case GL_DEPTH_COMPONENT: return "DEPTH";
        case GL_DEPTH_COMPONENT16: return "DEPTH16";
        case GL_DEPTH_COMPONENT24: return "DEPTH24";
        case GL_DEPTH_COMPONENT32: return "DEPTH32";
        case GL_DEPTH24_STENCIL8: return "DEPTH24_STENCIL8";
        case GL_DEPTH_STENCIL: return "DEPTH_STENCIL";
        case GL_RGBA32F_VALUE: return "RGBA32F";
        case GL_RGBA16F_VALUE: return "RGBA16F";
        case GL_RGB32F_VALUE: return "RGB32F";
        case GL_RGB16F_VALUE: return "RGB16F";
        case GL_RGBA16_VALUE: return "RGBA16";
        case GL_SRGB8_ALPHA8_VALUE: return "SRGB8_ALPHA8";
        case GL_DEPTH32F_STENCIL8_VALUE: return "DEPTH32F_STENCIL8";
        default: return "other";
    }
}

static int format_is_float(GLenum internal) {
    return internal == GL_RGBA32F_VALUE || internal == GL_RGBA16F_VALUE ||
           internal == GL_RGB32F_VALUE || internal == GL_RGB16F_VALUE ||
           internal == GL_RGBA16_VALUE;
}

static int format_is_depth(GLenum internal) {
    return internal == GL_DEPTH_COMPONENT || internal == GL_DEPTH_COMPONENT16 ||
           internal == GL_DEPTH_COMPONENT24 || internal == GL_DEPTH_COMPONENT32 ||
           internal == GL_DEPTH24_STENCIL8 ||
           internal == GL_DEPTH32F_STENCIL8_VALUE;
}

/* Reads every mip level of `target`/`name` as normalized RGBA. Emits a JSON
 * member `"levels":[...]` (without a leading comma). */
/* The texture readback must not disturb engine state. It operates on the
   currently active unit (saving that unit's binding for the inspected target)
   and restores it afterwards; the active unit itself is never changed. */
static void capture_texture_begin(GLenum target, GLuint name, GLint *binding,
                                  GLint *alignment, GLint *row_length,
                                  GLint *skip_rows, GLint *skip_pixels,
                                  GLint *pack_buffer) {
    glGetIntegerv(texture_binding_query(target), binding);
    glGetIntegerv(GL_PACK_ALIGNMENT, alignment);
    glGetIntegerv(GL_PACK_ROW_LENGTH, row_length);
    glGetIntegerv(GL_PACK_SKIP_ROWS, skip_rows);
    glGetIntegerv(GL_PACK_SKIP_PIXELS, skip_pixels);
    glGetIntegerv(GL_PIXEL_PACK_BUFFER_BINDING, pack_buffer);
    glPixelStorei(GL_PACK_ALIGNMENT, 1);
    glPixelStorei(GL_PACK_ROW_LENGTH, 0);
    glPixelStorei(GL_PACK_SKIP_ROWS, 0);
    glPixelStorei(GL_PACK_SKIP_PIXELS, 0);
    if (*pack_buffer != 0) {
        glBindBuffer(GL_PIXEL_PACK_BUFFER, 0);
    }
    glBindTexture(target, name);
}

static void capture_texture_end(GLenum target, GLint binding, GLint alignment,
                                GLint row_length, GLint skip_rows,
                                GLint skip_pixels, GLint pack_buffer) {
    glBindTexture(target, (GLuint)binding);
    glPixelStorei(GL_PACK_ALIGNMENT, alignment);
    glPixelStorei(GL_PACK_ROW_LENGTH, row_length);
    glPixelStorei(GL_PACK_SKIP_ROWS, skip_rows);
    glPixelStorei(GL_PACK_SKIP_PIXELS, skip_pixels);
    if (pack_buffer != 0) {
        glBindBuffer(GL_PIXEL_PACK_BUFFER, (GLuint)pack_buffer);
    }
    while (glGetError() != GL_NO_ERROR) {
    }
}

/* Exported for the state-restoration regression host: performs the same
   bind/restore round trip without reading pixels. */
int eu4_gfx_frame_trace_texture_probe(GLenum target, GLuint name) {
    GLint binding = 0, alignment = 0, row_length = 0;
    GLint skip_rows = 0, skip_pixels = 0, pack_buffer = 0;
    capture_texture_begin(target, name, &binding, &alignment, &row_length,
                          &skip_rows, &skip_pixels, &pack_buffer);
    GLint bound = 0;
    glGetIntegerv(texture_binding_query(target), &bound);
    capture_texture_end(target, binding, alignment, row_length, skip_rows,
                        skip_pixels, pack_buffer);
    return bound == (GLint)name ? 0 : -1;
}

static int read_texture(GLenum target, GLuint name, json_out *out) {
    GLint previous_alignment = 0;
    GLint previous_row = 0;
    GLint previous_binding = 0;
    GLint previous_skip_rows = 0, previous_skip_pixels = 0, previous_pack_buffer = 0;
    capture_texture_begin(target, name, &previous_binding, &previous_alignment,
                          &previous_row, &previous_skip_rows,
                          &previous_skip_pixels, &previous_pack_buffer);
    while (glGetError() != GL_NO_ERROR) {
    }

    int faces = target == GL_TEXTURE_CUBE_MAP ? 6 : 1;
    GLenum level_target = target == GL_TEXTURE_CUBE_MAP
                              ? GL_TEXTURE_CUBE_MAP_POSITIVE_X
                              : target;
    GLint max_level = 0;
    glGetTexParameteriv(target, GL_TEXTURE_MAX_LEVEL, &max_level);
    if (max_level <= 0 || max_level > FTRACE_MAX_LEVELS - 1) {
        max_level = FTRACE_MAX_LEVELS - 1;
    }
    int levels = 0;
    int failure = 0;
    jprintf(out, "\"levels\":[");
    for (int level = 0; level <= max_level; level++) {
        GLint width = 0, height = 0, internal = 0;
        glGetTexLevelParameteriv(level_target, level, GL_TEXTURE_WIDTH, &width);
        glGetTexLevelParameteriv(level_target, level, GL_TEXTURE_HEIGHT, &height);
        glGetTexLevelParameteriv(level_target, level, GL_TEXTURE_INTERNAL_FORMAT,
                                 &internal);
        if (width <= 0 || height <= 0) {
            break;
        }
        int is_depth = format_is_depth((GLenum)internal);
        int is_float = format_is_float((GLenum)internal);
        GLenum read_format = is_depth ? GL_DEPTH_COMPONENT : GL_RGBA;
        GLenum read_type = (is_depth || is_float) ? GL_FLOAT : GL_UNSIGNED_BYTE;
        size_t component_bytes = is_depth ? 4u : (is_float ? 16u : 4u);
        size_t row_stride = (size_t)width * component_bytes;
        size_t bytes = row_stride * (size_t)height;
        if (bytes == 0 || bytes > FTRACE_MAX_TEX_BYTES) {
            failure = 1;
            break;
        }
        jprintf(out,
                "%s{\"level\":%d,\"width\":%d,\"height\":%d,"
                "\"internal_format\":\"%s\",\"pixel_format\":\"%s\","
                "\"pixel_type\":\"%s\",\"row_stride\":%zu,",
                levels > 0 ? "," : "", level, width, height,
                format_name((GLenum)internal),
                is_depth ? "DEPTH" : (is_float ? "RGBA32F" : "RGBA8"),
                (is_depth || is_float) ? "float" : "unsigned_byte", row_stride);
        if (faces == 1) {
            uint8_t *pixels = malloc(bytes);
            if (pixels == NULL) {
                failure = 1;
                break;
            }
            memset(pixels, 0, bytes);
            glGetTexImage(target, level, read_format, read_type, pixels);
            if (glGetError() != GL_NO_ERROR) {
                free(pixels);
                failure = 1;
                break;
            }
            char blob_name[FTRACE_NAME] = "";
            char blob_sha[65] = "";
            if (put_blob(pixels, bytes, "tex", blob_name, blob_sha) != 0) {
                free(pixels);
                failure = 1;
                break;
            }
            free(pixels);
            jprintf(out,
                    "\"bytes\":%zu,\"file\":\"%s\",\"sha256\":\"%s\"}",
                    bytes, blob_name, blob_sha);
        } else {
            jprintf(out, "\"bytes\":%zu,\"faces\":[", bytes * (size_t)faces);
            for (int face = 0; face < faces; face++) {
                uint8_t *pixels = malloc(bytes);
                if (pixels == NULL) {
                    failure = 1;
                    break;
                }
                memset(pixels, 0, bytes);
                glGetTexImage((GLenum)(GL_TEXTURE_CUBE_MAP_POSITIVE_X + face),
                              level, read_format, read_type, pixels);
                if (glGetError() != GL_NO_ERROR) {
                    free(pixels);
                    failure = 1;
                    break;
                }
                char blob_name[FTRACE_NAME] = "";
                char blob_sha[65] = "";
                if (put_blob(pixels, bytes, "texc", blob_name, blob_sha) != 0) {
                    free(pixels);
                    failure = 1;
                    break;
                }
                free(pixels);
                jprintf(out,
                        "%s{\"face\":%d,\"row_stride\":%zu,\"bytes\":%zu,"
                        "\"file\":\"%s\",\"sha256\":\"%s\"}",
                        face > 0 ? "," : "", face, row_stride, bytes,
                        blob_name, blob_sha);
            }
            jprintf(out, "]}");
            if (failure) {
                break;
            }
        }
        levels++;
    }
    jprintf(out, "]");
    if (failure) {
        jprintf(out, ",\"capture_error\":\"texture_read\"");
    }
    capture_texture_end(target, previous_binding, previous_alignment,
                        previous_row, previous_skip_rows, previous_skip_pixels,
                        previous_pack_buffer);
    return failure ? -1 : 0;
}

static int capture_shaders(GLuint program, json_out *out) {
    GLint attached = 0;
    glGetProgramiv(program, GL_ATTACHED_SHADERS, &attached);
    GLuint shaders[FTRACE_MAX_SHADERS];
    GLsizei count = 0;
    if (attached < 0) {
        attached = 0;
    }
    if (attached > FTRACE_MAX_SHADERS) {
        attached = FTRACE_MAX_SHADERS;
    }
    if (attached > 0) {
        glGetAttachedShaders(program, (GLsizei)attached, &count, shaders);
    }
    int emitted = 0;
    jprintf(out, "\"shaders\":[");
    for (GLsizei i = 0; i < count; i++) {
        GLint stage = 0, compile = 0, source_length = 0;
        glGetShaderiv(shaders[i], GL_SHADER_TYPE, &stage);
        glGetShaderiv(shaders[i], GL_COMPILE_STATUS, &compile);
        glGetShaderiv(shaders[i], GL_SHADER_SOURCE_LENGTH, &source_length);
        if (source_length <= 0 || source_length > 16 * 1024 * 1024) {
            continue;
        }
        char *source = malloc((size_t)source_length);
        if (source == NULL) {
            continue;
        }
        GLsizei written = 0;
        glGetShaderSource(shaders[i], source_length, &written, source);
        char blob_name[FTRACE_NAME] = "";
        char blob_sha[65] = "";
        if (put_blob(source, (size_t)written, "shader", blob_name, blob_sha) != 0) {
            free(source);
            continue;
        }
        free(source);
        jprintf(out,
                "%s{\"stage\":\"%s\",\"compile\":%s,\"bytes\":%d,"
                "\"file\":\"%s\",\"sha256\":\"%s\"}",
                emitted > 0 ? "," : "",
                stage == GL_VERTEX_SHADER ? "vertex" : "pixel",
                bool_text(compile != 0), written, blob_name, blob_sha);
        emitted++;
    }
    jprintf(out, "]");
    return emitted > 0 ? 0 : -1;
}

static int capture_uniforms(GLuint program, json_out *out) {
    GLint count = 0;
    glGetProgramiv(program, GL_ACTIVE_UNIFORMS, &count);
    if (count < 0) {
        count = 0;
    }
    int emitted = 0;
    int sampler_index = 0;
    jprintf(out, "\"uniforms\":[");
    for (GLint i = 0; i < count && i < FTRACE_MAX_UNIFORMS; i++) {
        char name[256];
        GLsizei length = 0;
        GLint size = 0;
        GLenum type = 0;
        glGetActiveUniform(program, (GLuint)i, sizeof(name), &length, &size,
                           &type, name);
        GLint location = glGetUniformLocation(program, name);
        if (location < 0) {
            continue;
        }
        int is_sampler = type == GL_SAMPLER_2D || type == GL_SAMPLER_CUBE ||
                         type == GL_SAMPLER_3D || type == GL_SAMPLER_2D_SHADOW ||
                         type == GL_SAMPLER_1D ||
                         type == GL_SAMPLER_2D_RECT_VALUE;
        if (is_sampler) {
            GLint unit = 0;
            glGetUniformiv(program, location, &unit);
            jprintf(out,
                    "%s{\"name\":\"%s\",\"type\":\"sampler\",\"unit\":%d,"
                    "\"index\":%d}",
                    emitted > 0 ? "," : "", name, unit, sampler_index);
            sampler_index++;
            emitted++;
            continue;
        }
        int components = 0;
        int is_int = 0;
        switch (type) {
            case GL_FLOAT: components = 1; break;
            case GL_FLOAT_VEC2: components = 2; break;
            case GL_FLOAT_VEC3: components = 3; break;
            case GL_FLOAT_VEC4: components = 4; break;
            case GL_FLOAT_MAT2: components = 4; break;
            case GL_FLOAT_MAT3: components = 9; break;
            case GL_FLOAT_MAT4: components = 16; break;
            case GL_INT: case GL_BOOL: components = 1; is_int = 1; break;
            case GL_INT_VEC2: case GL_BOOL_VEC2: components = 2; is_int = 1; break;
            case GL_INT_VEC3: case GL_BOOL_VEC3: components = 3; is_int = 1; break;
            case GL_INT_VEC4: case GL_BOOL_VEC4: components = 4; is_int = 1; break;
            default: continue;
        }
        int elements = size > 0 ? size : 1;
        size_t values = (size_t)elements * (size_t)components;
        size_t bytes = values * (is_int ? sizeof(GLint) : sizeof(GLfloat));
        void *data = calloc(1, bytes);
        if (data == NULL) {
            jprintf(out, "]");
            return -1;
        }
        if (elements > 1) {
            /* Apple's legacy GL returns only the first element for an array
               base location, so each element is queried separately. */
            int location_stride = 1;
            if (type == GL_FLOAT_MAT4) {
                location_stride = 4;
            } else if (type == GL_FLOAT_MAT3) {
                location_stride = 3;
            } else if (type == GL_FLOAT_MAT2) {
                location_stride = 2;
            }
            for (int element = 0; element < elements; element++) {
                GLint element_location = location + element * location_stride;
                if (is_int) {
                    glGetUniformiv(program, element_location,
                                   (GLint *)data + element * components);
                } else {
                    glGetUniformfv(program, element_location,
                                   (GLfloat *)data + element * components);
                }
            }
        } else if (is_int) {
            glGetUniformiv(program, location, (GLint *)data);
        } else {
            glGetUniformfv(program, location, (GLfloat *)data);
        }
        char blob_name[FTRACE_NAME] = "";
        char blob_sha[65] = "";
        if (put_blob(data, bytes, "uniform", blob_name, blob_sha) != 0) {
            free(data);
            jprintf(out, "]");
            return -1;
        }
        free(data);
        const char *kind = is_int ? "int" : "float";
        if (!is_int) {
            switch (type) {
                case GL_FLOAT_MAT4: kind = "mat4"; break;
                case GL_FLOAT_MAT3: kind = "mat3"; break;
                case GL_FLOAT_MAT2: kind = "mat2"; break;
                default: break;
            }
        }
        jprintf(out,
                "%s{\"name\":\"%s\",\"type\":\"%s\",\"components\":%d,"
                "\"elements\":%d,\"bytes\":%zu,\"file\":\"%s\","
                "\"sha256\":\"%s\"}",
                emitted > 0 ? "," : "", name, kind, components, elements, bytes,
                blob_name, blob_sha);
        emitted++;
    }
    jprintf(out, "]");
    return 0;
}

static int engine_attribute_layout(uintptr_t master, int location,
                                   uint32_t *offset, int *divisor,
                                   int *normalized) {
    if (master == 0 || location < 0) {
        return -1;
    }
    uint64_t shader = 0;
    if (!eu4_gfx_observe_read((const void *)(master + 0x70), &shader,
                              sizeof(shader)) ||
        shader == 0) {
        return -1;
    }
    uint64_t elements = 0;
    int32_t count = 0;
    if (!eu4_gfx_observe_read((const void *)(uintptr_t)(shader + 0x10), &elements,
                              sizeof(elements)) ||
        elements == 0 ||
        !eu4_gfx_observe_read((const void *)(uintptr_t)(shader + 0x18), &count,
                              sizeof(count)) ||
        count <= 0 || count > 64) {
        return -1;
    }
    /* Element layout, as consumed by SShaderOpenGL::EnableVertexAttribArrays:
     *   +0x0c attribute location   +0x10 size   +0x14 GL type
     *   +0x18 byte offset          +0x1c vertex buffer slot
     *   +0x20 divisor (byte)       +0x21 normalized (byte)
     * Element stride is 0x28. */
    for (int i = 0; i < count; i++) {
        uint8_t element[0x28];
        if (!eu4_gfx_observe_read_bytes(
                (const void *)(uintptr_t)(elements + (uint64_t)i * 0x28), element,
                sizeof(element))) {
            return -1;
        }
        int32_t element_location = 0;
        memcpy(&element_location, element + 0xc, sizeof(element_location));
        if (element_location != location) {
            continue;
        }
        uint32_t element_offset = 0;
        int32_t buffer_slot = 0;
        memcpy(&element_offset, element + 0x18, sizeof(element_offset));
        memcpy(&buffer_slot, element + 0x1c, sizeof(buffer_slot));
        uint32_t slot_offset = 0;
        if (buffer_slot >= 0 && buffer_slot < 4) {
            (void)eu4_gfx_observe_read(
                (const void *)(master + 0x150 + (uint64_t)buffer_slot * 4),
                &slot_offset, sizeof(slot_offset));
        }
        if (offset != NULL) {
            *offset = element_offset + slot_offset;
        }
        if (divisor != NULL) {
            *divisor = element[0x20];
        }
        if (normalized != NULL) {
            *normalized = element[0x21];
        }
        return 0;
    }
    return -1;
}

/* Reads the byte offset of one attribute from the engine's vertex layout.
 * Apple's legacy GL returns 0 for GL_VERTEX_ATTRIB_ARRAY_POINTER on a
 * VBO-backed array, so the driver cannot supply it; the engine's element table
 * (SShaderOpenGL +0x10/+0x18, elements of 0x28 bytes) is the authoritative
 * source and is exactly what EnableVertexAttribArrays passes to
 * glVertexAttribPointer. */

static int capture_attributes(GLuint program, int indexed, int first, int count,
                              int instance_count, int base_vertex,
                              uintptr_t master, GLuint index_buffer,
                              json_out *out) {
    jprintf(out, "\"attributes\":[");
    int emitted = 0;
    GLint attribute_count = 0;
    glGetProgramiv(program, GL_ACTIVE_ATTRIBUTES, &attribute_count);
    if (attribute_count < 0) {
        attribute_count = 0;
    }
    int element_bytes = 2;
    if (indexed) {
        GLint bound = 0;
        glGetIntegerv(GL_ELEMENT_ARRAY_BUFFER_BINDING, &bound);
        if (bound != 0) {
            index_buffer = (GLuint)bound;
        }
        element_bytes = 2; /* the engine's indexed tails pass GL_UNSIGNED_SHORT */
    }
    for (int i = 0; i < attribute_count && i < FTRACE_MAX_ATTRS; i++) {
        char name[256];
        GLsizei length = 0;
        GLint size = 0;
        GLenum type = 0;
        glGetActiveAttrib(program, (GLuint)i, sizeof(name), &length, &size,
                          &type, name);
        GLint location = glGetAttribLocation(program, name);
        if (location < 0) {
            continue;
        }
        GLint enabled = 0;
        glGetVertexAttribiv((GLuint)location, GL_VERTEX_ATTRIB_ARRAY_ENABLED,
                            &enabled);
        if (!enabled) {
            GLfloat constant[4] = {0.0f, 0.0f, 0.0f, 1.0f};
            glGetVertexAttribfv((GLuint)location, GL_CURRENT_VERTEX_ATTRIB,
                                constant);
            jprintf(out,
                    "%s{\"index\":%d,\"name\":\"%s\",\"location\":%d,"
                    "\"enabled\":false,\"components\":%d,"
                    "\"constant_value\":[%.9g,%.9g,%.9g,%.9g]}",
                    emitted > 0 ? "," : "", location, name, location, size,
                    (double)constant[0], (double)constant[1],
                    (double)constant[2], (double)constant[3]);
            emitted++;
            continue;
        }
        GLint buffer = 0, components = 0, gl_type = 0, normalized = 0,
              stride = 0, offset = 0, divisor = 0;
        glGetVertexAttribiv((GLuint)location,
                            GL_VERTEX_ATTRIB_ARRAY_BUFFER_BINDING, &buffer);
        glGetVertexAttribiv((GLuint)location, GL_VERTEX_ATTRIB_ARRAY_SIZE,
                            &components);
        glGetVertexAttribiv((GLuint)location, GL_VERTEX_ATTRIB_ARRAY_TYPE,
                            &gl_type);
        glGetVertexAttribiv((GLuint)location, GL_VERTEX_ATTRIB_ARRAY_NORMALIZED,
                            &normalized);
        glGetVertexAttribiv((GLuint)location, GL_VERTEX_ATTRIB_ARRAY_STRIDE,
                            &stride);
        glGetVertexAttribiv((GLuint)location, GL_VERTEX_ATTRIB_ARRAY_POINTER,
                            &offset);
        glGetVertexAttribiv((GLuint)location, GL_VERTEX_ATTRIB_ARRAY_DIVISOR,
                            &divisor);
        uint32_t engine_offset = 0;
        int engine_divisor = 0;
        int engine_normalized = 0;
        const char *offset_source = "gl";
        if (engine_attribute_layout(master, location, &engine_offset,
                                    &engine_divisor, &engine_normalized) == 0) {
            offset = (GLint)engine_offset;
            divisor = engine_divisor;
            normalized = engine_normalized;
            offset_source = "engine";
        }
        int element_size = 4;
        switch (gl_type) {
            case GL_UNSIGNED_BYTE: case GL_BYTE: element_size = 1; break;
            case GL_UNSIGNED_SHORT: case GL_SHORT:
            case GL_HALF_FLOAT: element_size = 2; break;
            default: element_size = 4; break;
        }
        if (stride <= 0) {
            stride = components * element_size;
        }
        int vertex_first = indexed ? base_vertex : first;
        int vertex_last = indexed ? base_vertex + (count > 0 ? count - 1 : 0)
                                  : first + (count > 0 ? count - 1 : 0);
        if (vertex_first < 0) {
            vertex_first = 0;
        }
        size_t begin = (size_t)offset + (size_t)vertex_first * (size_t)stride;
        size_t end = (size_t)offset + (size_t)(vertex_last + 1) * (size_t)stride;
        GLint buffer_size = 0;
        if (buffer != 0) {
            glBindBuffer(GL_ARRAY_BUFFER, (GLuint)buffer);
            glGetBufferParameteriv(GL_ARRAY_BUFFER, GL_BUFFER_SIZE, &buffer_size);
        }
        if (end > (size_t)(buffer_size > 0 ? buffer_size : 0)) {
            end = (size_t)(buffer_size > 0 ? buffer_size : 0);
        }
        size_t bytes = begin < end ? end - begin : 0;
        char blob_name[FTRACE_NAME] = "";
        char blob_sha[65] = "";
        int read_error = 0;
        if (bytes > 0 && buffer != 0) {
            uint8_t *data = malloc(bytes);
            if (data == NULL) {
                read_error = 1;
            } else {
                while (glGetError() != GL_NO_ERROR) {
                }
                glGetBufferSubData(GL_ARRAY_BUFFER, (GLintptr)begin,
                                   (GLsizeiptr)bytes, data);
                if (glGetError() != GL_NO_ERROR ||
                    put_blob(data, bytes, "vb", blob_name, blob_sha) != 0) {
                    read_error = 1;
                    blob_name[0] = 0;
                }
                free(data);
            }
        }
        jprintf(out,
                "%s{\"index\":%d,\"name\":\"%s\",\"location\":%d,"
                "\"enabled\":true,"
                "\"components\":%d,\"gl_type\":\"0x%x\",\"normalized\":%s,"
                "\"stride\":%d,\"offset\":%d,\"divisor\":%d,"
                "\"offset_source\":\"%s\","
                "\"buffer\":%d,\"buffer_bytes\":%d,\"range_begin\":%zu,"
                "\"range_bytes\":%zu,\"first_vertex\":%d,\"last_vertex\":%d,"
                "\"data\":\"%s\",\"sha256\":\"%s\"%s}",
                emitted > 0 ? "," : "", location, name, location, components,
                gl_type, bool_text(normalized != 0), stride, offset, divisor,
                offset_source, buffer, buffer_size, begin, bytes, vertex_first,
                vertex_last, blob_name, blob_sha,
                read_error ? ",\"read_error\":true" : "");
        emitted++;
    }
    jprintf(out, "]");
    if (indexed) {
        GLint buffer_size = 0;
        if (index_buffer != 0) {
            glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, index_buffer);
            glGetBufferParameteriv(GL_ELEMENT_ARRAY_BUFFER, GL_BUFFER_SIZE,
                                   &buffer_size);
        }
        size_t bytes = (size_t)(count > 0 ? count : 0) * (size_t)element_bytes;
        if (bytes > (size_t)(buffer_size > 0 ? buffer_size : 0)) {
            bytes = (size_t)(buffer_size > 0 ? buffer_size : 0);
        }
        char blob_name[FTRACE_NAME] = "";
        char blob_sha[65] = "";
        if (bytes > 0) {
            uint8_t *data = malloc(bytes);
            if (data == NULL) {
                bytes = 0;
            } else {
                while (glGetError() != GL_NO_ERROR) {
                }
                glGetBufferSubData(GL_ELEMENT_ARRAY_BUFFER, 0, (GLsizeiptr)bytes,
                                   data);
                if (glGetError() != GL_NO_ERROR) {
                    free(data);
                    bytes = 0;
                } else if (put_blob(data, bytes, "ib", blob_name, blob_sha) != 0) {
                    blob_name[0] = 0;
                    free(data);
                    data = NULL;
                }
                if (data != NULL) {
                    free(data);
                }
            }
        }
        jprintf(out,
                ",\"indices\":{\"buffer\":%u,\"bytes\":%zu,\"first_index\":0,"
                "\"count\":%d,\"instance_count\":%d,\"file\":\"%s\","
                "\"sha256\":\"%s\"}",
                index_buffer, bytes, count, instance_count, blob_name, blob_sha);
    }
    return 0;
}

static int capture_target(json_out *out, GLint *out_width, GLint *out_height) {
    GLint framebuffer = 0;
    glGetIntegerv(GL_DRAW_FRAMEBUFFER_BINDING, &framebuffer);
    GLint draw_buffer = 0;
    glGetIntegerv(GL_DRAW_BUFFER0, &draw_buffer);
    GLint viewport[4] = {0, 0, 0, 0};
    glGetIntegerv(GL_VIEWPORT, viewport);
    GLint width = viewport[2];
    GLint height = viewport[3];
    jprintf(out,
            "\"target\":{\"framebuffer\":%d,\"draw_buffer\":\"0x%x\","
            "\"viewport\":[%d,%d,%d,%d]",
            framebuffer, draw_buffer, viewport[0], viewport[1], viewport[2],
            viewport[3]);
    if (framebuffer != 0) {
        GLint object_type = 0, object_name = 0, texture_level = 0;
        glGetFramebufferAttachmentParameteriv(
            GL_DRAW_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
            GL_FRAMEBUFFER_ATTACHMENT_OBJECT_TYPE, &object_type);
        glGetFramebufferAttachmentParameteriv(
            GL_DRAW_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
            GL_FRAMEBUFFER_ATTACHMENT_OBJECT_NAME, &object_name);
        if (object_type == GL_TEXTURE) {
            glGetFramebufferAttachmentParameteriv(
                GL_DRAW_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                GL_FRAMEBUFFER_ATTACHMENT_TEXTURE_LEVEL, &texture_level);
            GLint texture_width = 0, texture_height = 0, internal = 0;
            GLint previous = 0;
            glGetIntegerv(GL_TEXTURE_BINDING_2D, &previous);
            glBindTexture(GL_TEXTURE_2D, (GLuint)object_name);
            glGetTexLevelParameteriv(GL_TEXTURE_2D, texture_level,
                                     GL_TEXTURE_WIDTH, &texture_width);
            glGetTexLevelParameteriv(GL_TEXTURE_2D, texture_level,
                                     GL_TEXTURE_HEIGHT, &texture_height);
            glGetTexLevelParameteriv(GL_TEXTURE_2D, texture_level,
                                     GL_TEXTURE_INTERNAL_FORMAT, &internal);
            glBindTexture(GL_TEXTURE_2D, (GLuint)previous);
            width = texture_width;
            height = texture_height;
            {
                char target_name[64];
                snprintf(target_name, sizeof(target_name), "%d", object_name);
                render_target_add(target_name);
            }
            jprintf(out,
                    ",\"color_attachment\":{\"type\":\"texture\",\"name\":%d,"
                    "\"level\":%d,\"internal_format\":\"%s\",\"width\":%d,"
                    "\"height\":%d}",
                    object_name, texture_level, format_name((GLenum)internal),
                    width, height);
        } else if (object_type == GL_RENDERBUFFER) {
            GLint previous_renderbuffer = 0;
            glGetIntegerv(GL_RENDERBUFFER_BINDING, &previous_renderbuffer);
            glBindRenderbuffer(GL_RENDERBUFFER, (GLuint)object_name);
            GLint render_width = 0, render_height = 0, internal = 0;
            glGetRenderbufferParameteriv(GL_RENDERBUFFER, GL_RENDERBUFFER_WIDTH,
                                         &render_width);
            glGetRenderbufferParameteriv(GL_RENDERBUFFER,
                                         GL_RENDERBUFFER_HEIGHT, &render_height);
            glGetRenderbufferParameteriv(GL_RENDERBUFFER,
                                         GL_RENDERBUFFER_INTERNAL_FORMAT,
                                         &internal);
            width = render_width;
            height = render_height;
            jprintf(out,
                    ",\"color_attachment\":{\"type\":\"renderbuffer\","
                    "\"name\":%d,\"internal_format\":\"%s\",\"width\":%d,"
                    "\"height\":%d}",
                    object_name, format_name((GLenum)internal), width, height);
            glBindRenderbuffer(GL_RENDERBUFFER, (GLuint)previous_renderbuffer);
        }
        GLint depth_type = 0, depth_name = 0;
        glGetFramebufferAttachmentParameteriv(
            GL_DRAW_FRAMEBUFFER, GL_DEPTH_ATTACHMENT,
            GL_FRAMEBUFFER_ATTACHMENT_OBJECT_TYPE, &depth_type);
        glGetFramebufferAttachmentParameteriv(
            GL_DRAW_FRAMEBUFFER, GL_DEPTH_ATTACHMENT,
            GL_FRAMEBUFFER_ATTACHMENT_OBJECT_NAME, &depth_name);
        if (depth_type != 0) {
            if (depth_type == GL_TEXTURE) {
                char target_name[64];
                snprintf(target_name, sizeof(target_name), "%d", depth_name);
                render_target_add(target_name);
            }
            jprintf(out,
                    ",\"depth_attachment\":{\"type\":\"%s\",\"name\":%d}",
                    depth_type == GL_TEXTURE ? "texture" : "renderbuffer",
                    depth_name);
        }
    }
    jprintf(out, ",\"width\":%d,\"height\":%d}", width > 0 ? width : 0,
            height > 0 ? height : 0);
    if (out_width != NULL) {
        *out_width = width;
    }
    if (out_height != NULL) {
        *out_height = height;
    }
    return 0;
}

static int capture_target_pixels(const char *prefix, const char *member,
                                 json_out *out, int *out_width,
                                 int *out_height) {
    GLint framebuffer = 0;
    glGetIntegerv(GL_DRAW_FRAMEBUFFER_BINDING, &framebuffer);
    GLint width = 0, height = 0;
    if (framebuffer == 0) {
        GLint viewport[4] = {0, 0, 0, 0};
        glGetIntegerv(GL_VIEWPORT, viewport);
        uint32_t drawable_width = 0, drawable_height = 0;
        if (eu4_gfx_gl_drawable_extent(&drawable_width, &drawable_height) &&
            drawable_width > 0 && drawable_height > 0) {
            width = (GLint)drawable_width;
            height = (GLint)drawable_height;
        } else {
            width = viewport[2];
            height = viewport[3];
        }
    } else {
        GLint object_type = 0, object_name = 0, level = 0;
        glGetFramebufferAttachmentParameteriv(
            GL_DRAW_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
            GL_FRAMEBUFFER_ATTACHMENT_OBJECT_TYPE, &object_type);
        glGetFramebufferAttachmentParameteriv(
            GL_DRAW_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
            GL_FRAMEBUFFER_ATTACHMENT_OBJECT_NAME, &object_name);
        if (object_type == GL_TEXTURE) {
            glGetFramebufferAttachmentParameteriv(
                GL_DRAW_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                GL_FRAMEBUFFER_ATTACHMENT_TEXTURE_LEVEL, &level);
            GLint previous = 0;
            glGetIntegerv(GL_TEXTURE_BINDING_2D, &previous);
            glBindTexture(GL_TEXTURE_2D, (GLuint)object_name);
            glGetTexLevelParameteriv(GL_TEXTURE_2D, level, GL_TEXTURE_WIDTH,
                                     &width);
            glGetTexLevelParameteriv(GL_TEXTURE_2D, level, GL_TEXTURE_HEIGHT,
                                     &height);
            glBindTexture(GL_TEXTURE_2D, (GLuint)previous);
        } else {
            GLint previous_renderbuffer = 0;
            glGetIntegerv(GL_RENDERBUFFER_BINDING, &previous_renderbuffer);
            glBindRenderbuffer(GL_RENDERBUFFER, (GLuint)object_name);
            glGetRenderbufferParameteriv(GL_RENDERBUFFER, GL_RENDERBUFFER_WIDTH,
                                         &width);
            glGetRenderbufferParameteriv(GL_RENDERBUFFER,
                                         GL_RENDERBUFFER_HEIGHT, &height);
            glBindRenderbuffer(GL_RENDERBUFFER, (GLuint)previous_renderbuffer);
        }
    }
    if (width <= 0 || height <= 0 || width > 8192 || height > 8192) {
        return -1;
    }
    size_t bytes = (size_t)width * (size_t)height * 4;
    uint8_t *pixels = (uint8_t *)malloc(bytes);
    if (pixels == NULL) {
        return -1;
    }
    GLint read_framebuffer = 0, read_buffer = 0, pack_alignment = 0;
    GLint pack_row = 0, pack_skip_rows = 0, pack_skip_pixels = 0, pack_buffer = 0;
    glGetIntegerv(GL_READ_FRAMEBUFFER_BINDING, &read_framebuffer);
    glGetIntegerv(GL_READ_BUFFER, &read_buffer);
    glGetIntegerv(GL_PACK_ALIGNMENT, &pack_alignment);
    glGetIntegerv(GL_PACK_ROW_LENGTH, &pack_row);
    glGetIntegerv(GL_PACK_SKIP_ROWS, &pack_skip_rows);
    glGetIntegerv(GL_PACK_SKIP_PIXELS, &pack_skip_pixels);
    glGetIntegerv(GL_PIXEL_PACK_BUFFER_BINDING, &pack_buffer);
    glBindFramebuffer(GL_READ_FRAMEBUFFER, (GLuint)framebuffer);
    if (framebuffer != 0) {
        glReadBuffer(GL_COLOR_ATTACHMENT0);
    }
    glPixelStorei(GL_PACK_ALIGNMENT, 1);
    glPixelStorei(GL_PACK_ROW_LENGTH, 0);
    glPixelStorei(GL_PACK_SKIP_ROWS, 0);
    glPixelStorei(GL_PACK_SKIP_PIXELS, 0);
    if (pack_buffer != 0) {
        glBindBuffer(GL_PIXEL_PACK_BUFFER, 0);
    }
    while (glGetError() != GL_NO_ERROR) {
    }
    glFinish();
    glReadPixels(0, 0, width, height, GL_BGRA, GL_UNSIGNED_INT_8_8_8_8_REV,
                 pixels);
    GLenum gl_error = glGetError();
    glBindFramebuffer(GL_READ_FRAMEBUFFER, (GLuint)read_framebuffer);
    if (framebuffer != 0) {
        glReadBuffer((GLenum)read_buffer);
    }
    glPixelStorei(GL_PACK_ALIGNMENT, pack_alignment);
    glPixelStorei(GL_PACK_ROW_LENGTH, pack_row);
    glPixelStorei(GL_PACK_SKIP_ROWS, pack_skip_rows);
    glPixelStorei(GL_PACK_SKIP_PIXELS, pack_skip_pixels);
    if (pack_buffer != 0) {
        glBindBuffer(GL_PIXEL_PACK_BUFFER, (GLuint)pack_buffer);
    }
    if (gl_error != GL_NO_ERROR) {
        free(pixels);
        return -1;
    }
    char blob_name[FTRACE_NAME] = "";
    char blob_sha[65] = "";
    if (put_blob(pixels, bytes, prefix, blob_name, blob_sha) != 0) {
        free(pixels);
        return -1;
    }
    free(pixels);
    jprintf(out,
            "\"%s\":{\"width\":%d,\"height\":%d,\"file\":\"%s\","
            "\"sha256\":\"%s\"}",
            member, width, height, blob_name, blob_sha);
    if (out_width != NULL) {
        *out_width = width;
    }
    if (out_height != NULL) {
        *out_height = height;
    }
    return 0;
}

static int capture_seed(json_out *out, int *out_width, int *out_height) {
    return capture_target_pixels("seed", "seed", out, out_width, out_height);
}

static void capture_draw_state(json_out *out, int indexed, GLint mode, int first,
                               int count, int instance_count, int base_vertex,
                               uintptr_t master,
                               const eu4_gfx_observe_call *call) {
    char *target_json = NULL;
    size_t target_size = 0;
    FILE *target_stream = open_memstream(&target_json, &target_size);
    if (target_stream != NULL) {
        json_out target_out = {target_stream, 0};
        capture_target(&target_out, NULL, NULL);
        fclose(target_stream);
    }
    GLint program = 0;
    glGetIntegerv(GL_CURRENT_PROGRAM, &program);
    GLint blend = 0, blend_src_rgb = 0, blend_dst_rgb = 0, blend_src_a = 0,
          blend_dst_a = 0, blend_eq_rgb = 0, blend_eq_a = 0;
    glGetIntegerv(GL_BLEND, &blend);
    glGetIntegerv(GL_BLEND_SRC_RGB, &blend_src_rgb);
    glGetIntegerv(GL_BLEND_DST_RGB, &blend_dst_rgb);
    glGetIntegerv(GL_BLEND_SRC_ALPHA, &blend_src_a);
    glGetIntegerv(GL_BLEND_DST_ALPHA, &blend_dst_a);
    glGetIntegerv(GL_BLEND_EQUATION_RGB, &blend_eq_rgb);
    glGetIntegerv(GL_BLEND_EQUATION_ALPHA, &blend_eq_a);
    GLboolean color_mask[4] = {GL_TRUE, GL_TRUE, GL_TRUE, GL_TRUE};
    glGetBooleanv(GL_COLOR_WRITEMASK, color_mask);
    GLint depth_test = 0, depth_func = 0;
    GLboolean depth_mask = GL_TRUE;
    glGetIntegerv(GL_DEPTH_TEST, &depth_test);
    glGetIntegerv(GL_DEPTH_FUNC, &depth_func);
    glGetBooleanv(GL_DEPTH_WRITEMASK, &depth_mask);
    GLint stencil_test = 0, stencil_func = 0, stencil_ref = 0,
          stencil_value_mask = 0, stencil_fail = 0, stencil_zfail = 0,
          stencil_zpass = 0, stencil_write_mask = 0;
    glGetIntegerv(GL_STENCIL_TEST, &stencil_test);
    glGetIntegerv(GL_STENCIL_FUNC, &stencil_func);
    glGetIntegerv(GL_STENCIL_REF, &stencil_ref);
    glGetIntegerv(GL_STENCIL_VALUE_MASK, &stencil_value_mask);
    glGetIntegerv(GL_STENCIL_FAIL, &stencil_fail);
    glGetIntegerv(GL_STENCIL_PASS_DEPTH_FAIL, &stencil_zfail);
    glGetIntegerv(GL_STENCIL_PASS_DEPTH_PASS, &stencil_zpass);
    glGetIntegerv(GL_STENCIL_WRITEMASK, &stencil_write_mask);
    GLint cull = 0, cull_mode = 0, front_face = 0;
    glGetIntegerv(GL_CULL_FACE, &cull);
    glGetIntegerv(GL_CULL_FACE_MODE, &cull_mode);
    glGetIntegerv(GL_FRONT_FACE, &front_face);
    GLint polygon_mode[2] = {0, 0};
    glGetIntegerv(GL_POLYGON_MODE, polygon_mode);
    GLint polygon_offset_fill = 0;
    GLfloat polygon_offset_factor = 0.0f, polygon_offset_units = 0.0f;
    glGetIntegerv(GL_POLYGON_OFFSET_FILL, &polygon_offset_fill);
    glGetFloatv(GL_POLYGON_OFFSET_FACTOR, &polygon_offset_factor);
    glGetFloatv(GL_POLYGON_OFFSET_UNITS, &polygon_offset_units);
    GLint alpha_test = 0, alpha_func = 0;
    GLfloat alpha_ref = 0.0f;
    glGetIntegerv(GL_ALPHA_TEST, &alpha_test);
    glGetIntegerv(GL_ALPHA_TEST_FUNC, &alpha_func);
    glGetFloatv(GL_ALPHA_TEST_REF, &alpha_ref);
    GLint scissor_test = 0, scissor_box[4] = {0, 0, 0, 0};
    glGetIntegerv(GL_SCISSOR_TEST, &scissor_test);
    glGetIntegerv(GL_SCISSOR_BOX, scissor_box);
    GLint multisample = 0, dither = 0;
    glGetIntegerv(GL_MULTISAMPLE, &multisample);
    glGetIntegerv(GL_DITHER, &dither);
    GLint vp[4] = {0, 0, 0, 0};
    glGetIntegerv(GL_VIEWPORT, vp);

    jprintf(out,
            "\"state\":{\"blend\":{\"enabled\":%s,\"src_rgb\":\"0x%x\","
            "\"dst_rgb\":\"0x%x\",\"src_alpha\":\"0x%x\",\"dst_alpha\":\"0x%x\","
            "\"equation_rgb\":\"0x%x\",\"equation_alpha\":\"0x%x\"},"
            "\"color_mask\":[%d,%d,%d,%d],"
            "\"depth\":{\"test\":%s,\"func\":\"0x%x\",\"write\":%s},"
            "\"stencil\":{\"test\":%s,\"func\":\"0x%x\",\"ref\":%d,"
            "\"value_mask\":%d,\"fail\":\"0x%x\",\"zfail\":\"0x%x\",\"zpass\":\"0x%x\","
            "\"write_mask\":%d},"
            "\"cull\":{\"enabled\":%s,\"mode\":\"0x%x\",\"front\":\"0x%x\"},"
            "\"polygon\":{\"mode\":[\"0x%x\",\"0x%x\"],\"offset_fill\":%s,"
            "\"offset_factor\":%.9g,\"offset_units\":%.9g},"
            "\"alpha_test\":{\"enabled\":%s,\"func\":\"0x%x\",\"ref\":%.9g},"
            "\"scissor\":{\"enabled\":%s,\"box\":[%d,%d,%d,%d]},"
            "\"multisample\":%s,\"dither\":%s,\"viewport\":[%d,%d,%d,%d]}",
            bool_text(blend != 0), blend_src_rgb, blend_dst_rgb, blend_src_a,
            blend_dst_a, blend_eq_rgb, blend_eq_a, color_mask[0] ? 1 : 0,
            color_mask[1] ? 1 : 0, color_mask[2] ? 1 : 0, color_mask[3] ? 1 : 0,
            bool_text(depth_test != 0), depth_func, bool_text(depth_mask != 0),
            bool_text(stencil_test != 0), stencil_func, stencil_ref,
            stencil_value_mask, stencil_fail, stencil_zfail, stencil_zpass,
            stencil_write_mask, bool_text(cull != 0), cull_mode, front_face,
            polygon_mode[0], polygon_mode[1],
            bool_text(polygon_offset_fill != 0), (double)polygon_offset_factor,
            (double)polygon_offset_units, bool_text(alpha_test != 0), alpha_func,
            (double)alpha_ref, bool_text(scissor_test != 0), scissor_box[0],
            scissor_box[1], scissor_box[2], scissor_box[3],
            bool_text(multisample != 0), bool_text(dither != 0), vp[0], vp[1],
            vp[2], vp[3]);

    {
        GLint active_attributes = 0, active_uniforms = 0, program_link = 0;
        glGetProgramiv((GLuint)program, GL_ACTIVE_ATTRIBUTES, &active_attributes);
        glGetProgramiv((GLuint)program, GL_ACTIVE_UNIFORMS, &active_uniforms);
        glGetProgramiv((GLuint)program, GL_LINK_STATUS, &program_link);
        jprintf(out,
                ",\"program_diagnostics\":{\"active_attributes\":%d,"
                "\"active_uniforms\":%d,\"link\":%s,\"enabled_bits\":[",
                active_attributes, active_uniforms, bool_text(program_link != 0));
        for (int slot = 0; slot < FTRACE_MAX_ATTRS; slot++) {
            GLint enabled = 0;
            glGetVertexAttribiv((GLuint)slot, GL_VERTEX_ATTRIB_ARRAY_ENABLED,
                                &enabled);
            jprintf(out, "%s%d", slot > 0 ? "," : "", enabled ? 1 : 0);
        }
        jprintf(out, "]}");
    }
    {
        GLint active_unit = 0;
        glGetIntegerv(GL_ACTIVE_TEXTURE, &active_unit);
        jprintf(out, ",\"active_unit\":%d", (int)active_unit - (int)GL_TEXTURE0);
    }
    jprintf(out, ",\"program\":{\"name\":%d", program);
    json_out sub = *out;
    jprintf(&sub, ",");
    const char *cached_shaders = shader_cache_get((uint64_t)program);
    if (cached_shaders != NULL) {
        jprintf(&sub, "%s", cached_shaders);
    } else {
        char *shader_json = NULL;
        size_t shader_size = 0;
        FILE *shader_stream = open_memstream(&shader_json, &shader_size);
        if (shader_stream != NULL) {
            json_out shader_out = {shader_stream, 0};
            capture_shaders((GLuint)program, &shader_out);
            fclose(shader_stream);
            if (!shader_out.failed && shader_json != NULL) {
                shader_cache_put((uint64_t)program, shader_json);
                jprintf(&sub, "%s", shader_json);
            }
            free(shader_json);
        }
    }
    jprintf(&sub, ",");
    capture_uniforms((GLuint)program, &sub);
    jprintf(&sub, "}");
    if (sub.failed) {
        out->failed = 1;
    }

    GLint uniform_count = 0;
    glGetProgramiv((GLuint)program, GL_ACTIVE_UNIFORMS, &uniform_count);
    jprintf(out, ",\"textures\":[");
    int emitted = 0;
    int captured_units[FTRACE_MAX_UNITS];
    int captured_count = 0;
    for (GLint i = 0; i < uniform_count; i++) {
        char name[256];
        GLsizei length = 0;
        GLint size = 0;
        GLenum type = 0;
        glGetActiveUniform((GLuint)program, (GLuint)i, sizeof(name), &length,
                           &size, &type, name);
        GLenum target = 0;
        switch (type) {
            case GL_SAMPLER_2D:
            case GL_SAMPLER_2D_SHADOW:
            case GL_SAMPLER_2D_RECT_VALUE: target = GL_TEXTURE_2D; break;
            case GL_SAMPLER_CUBE: target = GL_TEXTURE_CUBE_MAP; break;
            case GL_SAMPLER_3D: target = GL_TEXTURE_3D; break;
            case GL_SAMPLER_1D: target = GL_TEXTURE_1D; break;
            default: continue;
        }
        GLint location = glGetUniformLocation((GLuint)program, name);
        if (location < 0) {
            continue;
        }
        GLint unit = 0;
        glGetUniformiv((GLuint)program, location, &unit);
        if (unit < 0 || unit >= FTRACE_MAX_UNITS) {
            continue;
        }
        int duplicate = 0;
        for (int k = 0; k < captured_count; k++) {
            if (captured_units[k] == unit) {
                duplicate = 1;
                break;
            }
        }
        if (duplicate) {
            continue;
        }
        if (captured_count < FTRACE_MAX_UNITS) {
            captured_units[captured_count++] = unit;
        }
        GLint previous_active = 0;
        glGetIntegerv(GL_ACTIVE_TEXTURE, &previous_active);
        glActiveTexture(GL_TEXTURE0 + (GLenum)unit);
        GLint binding = 0;
        glGetIntegerv(texture_binding_query(target), &binding);
        if (binding == 0) {
            glActiveTexture((GLenum)previous_active);
            continue;
        }
        GLint sampler = 0;
        glGetIntegerv(GL_SAMPLER_BINDING_VALUE, &sampler);
        GLint min_filter = 0, mag_filter = 0, wrap_s = 0, wrap_t = 0, wrap_r = 0,
              compare_mode = 0, compare_func = 0, base_level = 0,
              texture_max_level = 0;
        GLfloat min_lod = 0.0f, max_lod = 0.0f, lod_bias = 0.0f;
        if (sampler != 0 && g_glGetSamplerParameteriv != NULL &&
            g_glGetSamplerParameterfv != NULL) {
            g_glGetSamplerParameteriv((GLuint)sampler, GL_TEXTURE_MIN_FILTER,
                                      &min_filter);
            g_glGetSamplerParameteriv((GLuint)sampler, GL_TEXTURE_MAG_FILTER,
                                      &mag_filter);
            g_glGetSamplerParameteriv((GLuint)sampler, GL_TEXTURE_WRAP_S,
                                      &wrap_s);
            g_glGetSamplerParameteriv((GLuint)sampler, GL_TEXTURE_WRAP_T,
                                      &wrap_t);
            g_glGetSamplerParameteriv((GLuint)sampler, GL_TEXTURE_WRAP_R,
                                      &wrap_r);
            g_glGetSamplerParameteriv((GLuint)sampler, GL_TEXTURE_COMPARE_MODE,
                                      &compare_mode);
            g_glGetSamplerParameteriv((GLuint)sampler, GL_TEXTURE_COMPARE_FUNC,
                                      &compare_func);
            g_glGetSamplerParameterfv((GLuint)sampler, GL_TEXTURE_MIN_LOD,
                                      &min_lod);
            g_glGetSamplerParameterfv((GLuint)sampler, GL_TEXTURE_MAX_LOD,
                                      &max_lod);
            g_glGetSamplerParameterfv((GLuint)sampler, GL_TEXTURE_LOD_BIAS,
                                      &lod_bias);
        } else {
            glGetTexParameteriv(target, GL_TEXTURE_MIN_FILTER, &min_filter);
            glGetTexParameteriv(target, GL_TEXTURE_MAG_FILTER, &mag_filter);
            glGetTexParameteriv(target, GL_TEXTURE_WRAP_S, &wrap_s);
            glGetTexParameteriv(target, GL_TEXTURE_WRAP_T, &wrap_t);
            glGetTexParameteriv(target, GL_TEXTURE_WRAP_R, &wrap_r);
            glGetTexParameteriv(target, GL_TEXTURE_COMPARE_MODE, &compare_mode);
            glGetTexParameteriv(target, GL_TEXTURE_COMPARE_FUNC, &compare_func);
            glGetTexParameterfv(target, GL_TEXTURE_MIN_LOD, &min_lod);
            glGetTexParameterfv(target, GL_TEXTURE_MAX_LOD, &max_lod);
            glGetTexParameterfv(target, GL_TEXTURE_LOD_BIAS, &lod_bias);
        }
        glGetTexParameteriv(target, GL_TEXTURE_BASE_LEVEL, &base_level);
        glGetTexParameteriv(target, GL_TEXTURE_MAX_LEVEL, &texture_max_level);
        GLfloat anisotropy = 1.0f;
        glGetTexParameterfv(target, GL_TEXTURE_MAX_ANISOTROPY, &anisotropy);
        GLint width = 0, height = 0, internal = 0;
        GLenum level_target = target == GL_TEXTURE_CUBE_MAP
                                  ? GL_TEXTURE_CUBE_MAP_POSITIVE_X
                                  : target;
        glGetTexLevelParameteriv(level_target, 0, GL_TEXTURE_WIDTH, &width);
        glGetTexLevelParameteriv(level_target, 0, GL_TEXTURE_HEIGHT, &height);
        glGetTexLevelParameteriv(level_target, 0, GL_TEXTURE_INTERNAL_FORMAT,
                                 &internal);
        jprintf(out,
                "%s{\"unit\":%d,\"sampler_name\":\"%s\",\"target\":\"%s\","
                "\"name\":%d,\"width\":%d,\"height\":%d,"
                "\"internal_format\":\"%s\",\"sampler\":{\"min\":\"0x%x\","
                "\"mag\":\"0x%x\",\"wrap_s\":\"0x%x\",\"wrap_t\":\"0x%x\",\"wrap_r\":\"0x%x\","
                "\"min_lod\":%.9g,\"max_lod\":%.9g,\"lod_bias\":%.9g,"
                "\"base_level\":%d,\"max_level\":%d,\"compare_mode\":\"0x%x\","
                "\"compare_func\":\"0x%x\",\"anisotropy\":%.9g}",
                emitted > 0 ? "," : "", unit, name, texture_target_name(target),
                binding, width, height, format_name((GLenum)internal),
                min_filter, mag_filter, wrap_s, wrap_t, wrap_r,
                (double)min_lod, (double)max_lod, (double)lod_bias, base_level,
                texture_max_level, compare_mode, compare_func,
                (double)anisotropy);
        json_out tex = *out;
        jprintf(&tex, ",");
        char texture_key[96];
        char texture_name[64];
        snprintf(texture_name, sizeof(texture_name), "%d", binding);
        snprintf(texture_key, sizeof(texture_key), "%s-%s",
                 texture_target_name(target), texture_name);
        const char *cached_levels =
            render_target_known(texture_name) ? NULL
                                              : texture_cache_get(texture_key);
        if (cached_levels != NULL) {
            jprintf(&tex, "%s", cached_levels);
        } else {
            char *levels_json = NULL;
            size_t levels_size = 0;
            FILE *levels_stream = open_memstream(&levels_json, &levels_size);
            if (levels_stream != NULL) {
                json_out levels_out = {levels_stream, 0};
                (void)read_texture(target, (GLuint)binding, &levels_out);
                fclose(levels_stream);
                if (!levels_out.failed && levels_json != NULL) {
                    if (!render_target_known(texture_name)) {
                        texture_cache_put(texture_key, levels_json);
                    }
                    jprintf(&tex, "%s", levels_json);
                }
                free(levels_json);
            }
        }
        jprintf(&tex, "}");
        if (tex.failed) {
            out->failed = 1;
        }
        glActiveTexture((GLenum)previous_active);
        emitted++;
    }
    jprintf(out, "]");

    jprintf(out, ",%s", target_json != NULL ? target_json : "\"target\":{}");
    free(target_json);
    jprintf(out,
            ",\"draw\":{\"entry\":\"%s\",\"mode\":%d,\"first\":%d,"
            "\"count\":%d,\"instance_count\":%d,\"indexed\":%s,"
            "\"base_vertex\":%d,"
            "\"caller_rva\":\"0x%llx\",\"thread\":%llu,\"call_id\":%llu}",
            indexed ? "_GfxDrawIndexed" : "_GfxDraw", (int)mode, first, count,
            instance_count, bool_text(indexed), base_vertex,
            (unsigned long long)call->caller_rva,
            (unsigned long long)call->thread_id,
            (unsigned long long)call->call_id);

    json_out attr = *out;
    jprintf(&attr, ",");
    capture_attributes((GLuint)program, indexed, first, count, instance_count,
                       base_vertex, master, 0, &attr);
    if (attr.failed) {
        out->failed = 1;
    }
}

/* ------------------------------------------------------------------------- */
/* Frame manifest                                                             */
/* ------------------------------------------------------------------------- */

static void write_frame_manifest(int complete, const char *reason) {
    char path[FTRACE_PATH];
    snprintf(path, sizeof(path), "%s/frame-trace.json.partial", g.dir);
    FILE *file = fopen(path, "w");
    if (file == NULL) {
        return;
    }
    const char *vendor = g.gl_vendor[0] ? g.gl_vendor : "";
    const char *renderer = g.gl_renderer[0] ? g.gl_renderer : "";
    const char *version = g.gl_version[0] ? g.gl_version : "";
    fputs("{\"schema\":\"gfx-frame-trace/v1\",", file);
    fprintf(file, "\"complete\":%s,", bool_text(complete));
    fprintf(file, "\"reason\":\"%s\",", reason != NULL ? reason : "");
    fprintf(file, "\"truncated\":%s,", bool_text(g.truncated));
    fprintf(file, "\"pid\":%d,", (int)getpid());
    fprintf(file, "\"present_index\":%d,", g.capturing_frame);
    fprintf(file, "\"events\":%d,", g.event_count);
    fputs("\"gl\":{", file);
    fprintf(file, "\"vendor\":\"%s\",", vendor != NULL ? vendor : "");
    fprintf(file, "\"renderer\":\"%s\",", renderer != NULL ? renderer : "");
    fprintf(file, "\"version\":\"%s\"},", version != NULL ? version : "");
    fputs("\"frame\":{", file);
    fprintf(file, "\"width\":%u,", g.reference_width);
    fprintf(file, "\"height\":%u,", g.reference_height);
    fprintf(file, "\"origin\":\"lower_left\",\"pixel_format\":\"BGRA8\",");
    fprintf(file, "\"row_stride\":%u,", g.reference_width * 4);
    fprintf(file, "\"reference\":\"%s\",", g.reference_file);
    fprintf(file, "\"reference_bytes\":%zu,", g.reference_bytes);
    fprintf(file, "\"reference_sha256\":\"%s\"},", g.reference_sha);
    fputs("\"selection\":{\"callers\":[", file);
    for (int i = 0; i < g.caller_count; i++) {
        fprintf(file, "%s\"0x%llx\"", i > 0 ? "," : "",
                (unsigned long long)g.callers[i]);
    }
    fprintf(file, "],\"min_draws\":%u,", g.min_draws);
    fprintf(file, "\"occurrence\":%u,", g.occurrence);
    fprintf(file, "\"required\":%s},", bool_text(g.caller_count > 0));
    fputs("\"seed\":{", file);
    fprintf(file, "\"width\":%d,", g.seed_width);
    fprintf(file, "\"height\":%d},", g.seed_height);
    fprintf(file, "\"blobs\":%d}\n", g.blob_count);
    if (ferror(file) != 0 || fflush(file) != 0 || fsync(fileno(file)) != 0) {
        fclose(file);
        unlink(path);
        return;
    }
    fclose(file);
    char final_path[FTRACE_PATH];
    snprintf(final_path, sizeof(final_path), "%s/frame-trace.json", g.dir);
    rename(path, final_path);
}

/* ------------------------------------------------------------------------- */
/* Event pumps                                                                */
/* ------------------------------------------------------------------------- */

static void finish_capture(const char *reason) {
    if (!g.armed) {
        return;
    }
    GLint viewport[4] = {0, 0, 0, 0};
    glGetIntegerv(GL_VIEWPORT, viewport);
    uint32_t width = 0, height = 0;
    if (eu4_gfx_gl_drawable_extent(&width, &height) == 0 || width == 0 ||
        height == 0) {
        width = (uint32_t)(viewport[2] > 0 ? viewport[2] : 0);
        height = (uint32_t)(viewport[3] > 0 ? viewport[3] : 0);
    }
    int have_reference = 0;
    if (width > 0 && height > 0 && width <= 8192 && height <= 8192) {
        size_t bytes = (size_t)width * height * 4;
        uint8_t *pixels = malloc(bytes);
        if (pixels != NULL) {
            GLint pack_alignment = 0, pack_row = 0, pack_skip_rows = 0,
                  pack_skip_pixels = 0, pack_buffer = 0;
            glGetIntegerv(GL_PACK_ALIGNMENT, &pack_alignment);
            glGetIntegerv(GL_PACK_ROW_LENGTH, &pack_row);
            glGetIntegerv(GL_PACK_SKIP_ROWS, &pack_skip_rows);
            glGetIntegerv(GL_PACK_SKIP_PIXELS, &pack_skip_pixels);
            glGetIntegerv(GL_PIXEL_PACK_BUFFER_BINDING, &pack_buffer);
            glPixelStorei(GL_PACK_ALIGNMENT, 1);
            glPixelStorei(GL_PACK_ROW_LENGTH, 0);
            glPixelStorei(GL_PACK_SKIP_ROWS, 0);
            glPixelStorei(GL_PACK_SKIP_PIXELS, 0);
            if (pack_buffer != 0) {
                glBindBuffer(GL_PIXEL_PACK_BUFFER, 0);
            }
            while (glGetError() != GL_NO_ERROR) {
            }
            glFinish();
            glReadPixels(0, 0, (GLsizei)width, (GLsizei)height, GL_BGRA,
                         GL_UNSIGNED_INT_8_8_8_8_REV, pixels);
            if (glGetError() == GL_NO_ERROR) {
                char path[FTRACE_PATH];
                snprintf(path, sizeof(path), "%s/frame-reference.bgra", g.dir);
                if (write_atomic(path, pixels, bytes) == 0) {
                    snprintf(g.reference_file, sizeof(g.reference_file),
                             "frame-reference.bgra");
                    sha256_hex(pixels, bytes, g.reference_sha);
                    g.reference_bytes = bytes;
                    g.reference_width = width;
                    g.reference_height = height;
                    have_reference = 1;
                }
            }
            glPixelStorei(GL_PACK_ALIGNMENT, pack_alignment);
            glPixelStorei(GL_PACK_ROW_LENGTH, pack_row);
            glPixelStorei(GL_PACK_SKIP_ROWS, pack_skip_rows);
            glPixelStorei(GL_PACK_SKIP_PIXELS, pack_skip_pixels);
            if (pack_buffer != 0) {
                glBindBuffer(GL_PIXEL_PACK_BUFFER, (GLuint)pack_buffer);
            }
            free(pixels);
        }
    }
    write_frame_manifest(have_reference && !g.truncated,
                         g.truncated ? "draw_cap"
                                     : (have_reference ? reason : "reference_read"));
    if (g.events != NULL) {
        fclose(g.events);
        g.events = NULL;
    }
    g.armed = 0;
    g.captured = 1;
}

static void open_events(void) {
    g.events = fopen(g.partial_path, "w");
    if (g.events == NULL) {
        g.armed = 0;
    }
}

static void start_capture_at_frame(int next_frame) {
    frame_caches_reset();
    g.armed = 1;
    g.capturing_frame = next_frame;
    g.event_count = 0;
    g.truncated = 0;
    g.have_seed = 0;
    g.seed_emitted = 0;
    g.seed_json[0] = 0;
    g.seed_width = 0;
    g.seed_height = 0;
    open_events();
}

static uint64_t stream_caller_signature(void) {
    uint64_t signature = 1469598103934665603ULL;
    for (int i = 0; i < g.frame_caller_count; i++) {
        signature ^= g.frame_callers[i];
        signature *= 1099511628211ULL;
    }
    return signature;
}

/* Read the actual drawable (default framebuffer) into tightly packed BGRA8.
   Every binding the readback changes is restored, including pack skip state and
   a bound pixel-pack buffer.  Returns 0 on success. */
static int read_drawable_bgra(uint32_t *out_width, uint32_t *out_height,
                              uint8_t **out_pixels) {
    *out_width = 0;
    *out_height = 0;
    *out_pixels = NULL;
    uint32_t width = 0, height = 0;
    GLint viewport[4] = {0, 0, 0, 0};
    glGetIntegerv(GL_VIEWPORT, viewport);
    if (eu4_gfx_gl_drawable_extent(&width, &height) != 0 || width == 0 ||
        height == 0) {
        width = (uint32_t)(viewport[2] > 0 ? viewport[2] : 0);
        height = (uint32_t)(viewport[3] > 0 ? viewport[3] : 0);
    }
    if (width == 0 || height == 0 || width > 8192 || height > 8192) {
        return -1;
    }
    size_t bytes = (size_t)width * height * 4;
    uint8_t *pixels = malloc(bytes);
    if (pixels == NULL) {
        return -1;
    }
    GLint draw_framebuffer = 0, read_framebuffer = 0, read_buffer = 0;
    GLint pack_alignment = 0, pack_row = 0, pack_skip_rows = 0;
    GLint pack_skip_pixels = 0, pack_buffer = 0;
    glGetIntegerv(GL_DRAW_FRAMEBUFFER_BINDING, &draw_framebuffer);
    glGetIntegerv(GL_READ_FRAMEBUFFER_BINDING, &read_framebuffer);
    glGetIntegerv(GL_READ_BUFFER, &read_buffer);
    glGetIntegerv(GL_PACK_ALIGNMENT, &pack_alignment);
    glGetIntegerv(GL_PACK_ROW_LENGTH, &pack_row);
    glGetIntegerv(GL_PACK_SKIP_ROWS, &pack_skip_rows);
    glGetIntegerv(GL_PACK_SKIP_PIXELS, &pack_skip_pixels);
    glGetIntegerv(GL_PIXEL_PACK_BUFFER_BINDING, &pack_buffer);
    if (draw_framebuffer != 0) {
        glBindFramebuffer(GL_DRAW_FRAMEBUFFER, 0);
    }
    if (read_framebuffer != 0) {
        glBindFramebuffer(GL_READ_FRAMEBUFFER, 0);
    }
    glReadBuffer(GL_BACK);
    glPixelStorei(GL_PACK_ALIGNMENT, 1);
    glPixelStorei(GL_PACK_ROW_LENGTH, 0);
    glPixelStorei(GL_PACK_SKIP_ROWS, 0);
    glPixelStorei(GL_PACK_SKIP_PIXELS, 0);
    if (pack_buffer != 0) {
        glBindBuffer(GL_PIXEL_PACK_BUFFER, 0);
    }
    while (glGetError() != GL_NO_ERROR) {
    }
    glFinish();
    glReadPixels(0, 0, (GLsizei)width, (GLsizei)height, GL_BGRA,
                 GL_UNSIGNED_INT_8_8_8_8_REV, pixels);
    GLenum error = glGetError();
    glBindFramebuffer(GL_DRAW_FRAMEBUFFER, (GLuint)draw_framebuffer);
    glBindFramebuffer(GL_READ_FRAMEBUFFER, (GLuint)read_framebuffer);
    glReadBuffer((GLenum)read_buffer);
    glPixelStorei(GL_PACK_ALIGNMENT, pack_alignment);
    glPixelStorei(GL_PACK_ROW_LENGTH, pack_row);
    glPixelStorei(GL_PACK_SKIP_ROWS, pack_skip_rows);
    glPixelStorei(GL_PACK_SKIP_PIXELS, pack_skip_pixels);
    if (pack_buffer != 0) {
        glBindBuffer(GL_PIXEL_PACK_BUFFER, (GLuint)pack_buffer);
    }
    if (error != GL_NO_ERROR) {
        free(pixels);
        return -1;
    }
    *out_width = width;
    *out_height = height;
    *out_pixels = pixels;
    return 0;
}

void eu4_gfx_frame_trace_boundary_entry(eu4_gfx_capture_frame *frame,
                                        eu4_gfx_observe_call *call) {
    (void)frame;
    if (!g.enabled) {
        return;
    }
    pthread_mutex_lock(&g_lock);
    call->retain = 0;
    g.scene_index++;
    const char *log_path = getenv("EU4_GFX_FRAME_TRACE_LOG");
    if (log_path != NULL && log_path[0] == '/' && g.scene_index % 60 == 0) {
        FILE *log = fopen(log_path, "a");
        if (log != NULL) {
            fprintf(log,
                    "{\"event\":\"entry\",\"scene\":%d,\"ms\":%llu,"
                    "\"draws\":%u,\"armed\":%d,\"captured\":%d}\n",
                    g.scene_index, (unsigned long long)(now_ms() - g.start_ms),
                    g.frame_draws, g.armed, g.captured);
            fclose(log);
        }
    }
    if (g.boundary_scene && g.armed) {
        /* Every draw of the scene has been issued when the frontend presents. */
        finish_capture("scene_present");
    }
    pthread_mutex_unlock(&g_lock);
}

void eu4_gfx_frame_trace_boundary_exit(eu4_gfx_capture_frame *frame,
                                       eu4_gfx_observe_call *call,
                                       eu4_gfx_observe_record *record) {
    (void)frame;
    (void)record;
    if (!g.enabled) {
        return;
    }
    pthread_mutex_lock(&g_lock);
    call->retain = 0;
    if (g.boundary_scene && !g.captured && !g.armed && !g.stream_only) {
        int time_ready = g.after_ms == 0 || now_ms() - g.start_ms >= g.after_ms;
        int enough = time_ready && g.frame_draws >= g.min_draws &&
                     caller_set_matches();
        const char *log_path = getenv("EU4_GFX_FRAME_TRACE_LOG");
        if (log_path != NULL && log_path[0] == '/' && g.scene_index % 60 == 0) {
            FILE *log = fopen(log_path, "a");
            if (log != NULL) {
                fprintf(log,
                        "{\"event\":\"exit\",\"scene\":%d,\"ms\":%llu,"
                        "\"draws\":%u,\"ready\":%d,\"enough\":%d,"
                        "\"armed\":%d,\"captured\":%d}\n",
                        g.scene_index,
                        (unsigned long long)(now_ms() - g.start_ms),
                        g.frame_draws, time_ready, enough, g.armed, g.captured);
                fclose(log);
            }
        }
        if (enough) {
            start_capture_at_frame(g.scene_index + 1);
        }
    }
    uint32_t stream_width = 0, stream_height = 0;
    uint8_t *stream_pixels = NULL;
    uint32_t stream_draws = 0, stream_distinct = 0;
    uint64_t stream_signature = 0;
    int stream_ok = 0;
    int metadata_only = 0;
    if (g.stream_only) {
        stream_draws = g.frame_draws;
        stream_distinct = (uint32_t)g.frame_caller_count;
        stream_signature = stream_caller_signature();
        const char *metadata = getenv("EU4_GFX_FRAME_METADATA");
        metadata_only = metadata != NULL && strcmp(metadata, "1") == 0;
        if (metadata_only) {
            uint32_t width = 0, height = 0;
            if (eu4_gfx_gl_drawable_extent(&width, &height) != 0 ||
                width == 0 || height == 0) {
                GLint viewport[4] = {0, 0, 0, 0};
                glGetIntegerv(GL_VIEWPORT, viewport);
                width = (uint32_t)(viewport[2] > 0 ? viewport[2] : 0);
                height = (uint32_t)(viewport[3] > 0 ? viewport[3] : 0);
            }
            stream_width = width;
            stream_height = height;
            int wanted = eu4_gfx_frame_observe_wants != NULL &&
                         eu4_gfx_frame_observe_wants((uint32_t)g.scene_index);
            stream_ok = 1;
            if (wanted) {
                stream_ok = read_drawable_bgra(&stream_width, &stream_height,
                                               &stream_pixels) == 0;
                metadata_only = 0;
            }
        } else {
            stream_ok = read_drawable_bgra(&stream_width, &stream_height,
                                           &stream_pixels) == 0;
        }
    }
    frame_reset();
    pthread_mutex_unlock(&g_lock);
    if (g.stream_only && metadata_only &&
        eu4_gfx_frame_observe_emit_metadata != NULL) {
        (void)eu4_gfx_frame_observe_emit_metadata(
            stream_width, stream_height, "lower_left", (uint32_t)g.scene_index,
            stream_draws, stream_signature, stream_distinct, 1, "");
    } else if (g.stream_only && eu4_gfx_frame_observe_emit != NULL) {
        (void)eu4_gfx_frame_observe_emit(
            stream_pixels, stream_width, stream_height, "lower_left",
            (uint32_t)g.scene_index, stream_draws, stream_signature,
            stream_distinct, stream_ok, stream_ok,
            stream_ok ? "" : "drawable_readback_failed");
    }
    if (g.stream_only && eu4_gfx_frame_observe_lockstep_enabled != NULL &&
        eu4_gfx_frame_observe_lockstep_enabled() &&
        eu4_gfx_frame_observe_lockstep_wait != NULL) {
        (void)eu4_gfx_frame_observe_lockstep_wait((uint32_t)g.scene_index, 0);
    }
    free(stream_pixels);
}

void eu4_gfx_frame_trace_present_entry(eu4_gfx_capture_frame *frame,
                                       eu4_gfx_observe_call *call) {
    (void)frame;
    if (!g.enabled) {
        return;
    }
    pthread_mutex_lock(&g_lock);
    if (g.armed && g.boundary_fb < 0 && !g.boundary_scene) {
        finish_capture("present");
    }
    call->retain = 0;
    pthread_mutex_unlock(&g_lock);
}

void eu4_gfx_frame_trace_present_exit(eu4_gfx_capture_frame *frame,
                                      eu4_gfx_observe_call *call,
                                      eu4_gfx_observe_record *record) {
    (void)frame;
    (void)record;
    if (!g.enabled) {
        return;
    }
    pthread_mutex_lock(&g_lock);
    call->retain = 0;
    g.present_index++;
    int candidate = 0;
    if (g.boundary_scene) {
        /* The scene boundary owns frame tracking; _GfxPresent may be invoked
           inside PresentScene and must not reset the draw count. */
        pthread_mutex_unlock(&g_lock);
        return;
    }
    if (!g.captured && !g.armed) {
        if (g.at_frame != UINT64_MAX) {
            candidate = (uint64_t)(g.present_index + 1) == g.at_frame;
        } else {
            int time_ready =
                g.after_ms == 0 ||
                now_ms() - g.start_ms >= g.after_ms;
            int enough = time_ready && g.frame_draws >= g.min_draws &&
                         g.frame_draws > 0 && caller_set_matches() &&
                         (g.max_frame_draws == 0 ||
                          g.frame_draws <= g.max_frame_draws);
            if (enough) {
                g.matching_frames++;
                candidate = (uint32_t)g.matching_frames == g.occurrence;
            }
        }
    }
    frame_reset();
    if (candidate) {
        start_capture_at_frame(g.present_index + 1);
    }
    pthread_mutex_unlock(&g_lock);
}

/* _GfxResolve at 0x15ece13 (glCopyTexSubImage2D into the header's texture).
   Records the framebuffers bound at the copy so the reconstructed backend can
   use the same source. */
void eu4_gfx_frame_trace_resolve_entry(eu4_gfx_capture_frame *frame,
                                       eu4_gfx_observe_call *call) {
    if (call != NULL) {
        call->output_ok = 0;
    }
    if (frame == NULL || g.dir[0] == 0) {
        return;
    }
    /* Resolves happen before the first draw of the captured frame, so this
       observation is not gated on arming; the directory only exists once the
       capture has been configured. */
    GLint read_fbo = 0, draw_fbo = 0, fbo = 0;
    GLint type = 0, name = 0, level = 0;
    glGetIntegerv(0x8CAA, &read_fbo); /* GL_READ_FRAMEBUFFER_BINDING */
    glGetIntegerv(0x8CA6, &draw_fbo); /* GL_DRAW_FRAMEBUFFER_BINDING */
    glGetIntegerv(0x8D40, &fbo);      /* GL_FRAMEBUFFER_BINDING */
    if (read_fbo != 0) {
        GLint previous = 0;
        glGetIntegerv(0x8CAA, &previous);
        glBindFramebuffer(0x8CA8, (GLuint)read_fbo); /* GL_READ_FRAMEBUFFER */
        glGetFramebufferAttachmentParameteriv(0x8CA8, 0x8CE0 /* COLOR_ATTACHMENT0 */,
                                              0x8CD0 /* OBJECT_TYPE */, &type);
        glGetFramebufferAttachmentParameteriv(0x8CA8, 0x8CE0,
                                              0x8CD1 /* OBJECT_NAME */, &name);
        if (type == 0x1702 /* GL_TEXTURE */) {
            glGetFramebufferAttachmentParameteriv(0x8CA8, 0x8CE0,
                                                  0x8CD2 /* TEXTURE_LEVEL */,
                                                  &level);
        }
        glBindFramebuffer(0x8CA8, (GLuint)previous);
    }
    char *body = NULL;
    size_t size = 0;
    FILE *stream = open_memstream(&body, &size);
    if (stream == NULL) {
        return;
    }
    fprintf(stream,
            "{\"event\":\"resolve\",\"header\":\"0x%llx\","
            "\"read_fbo\":%d,\"draw_fbo\":%d,\"fbo\":%d,"
            "\"color_type\":%d,\"color_name\":%d,\"color_level\":%d}\n",
            (unsigned long long)(uintptr_t)frame->rsi, read_fbo, draw_fbo, fbo,
            type, name, level);
    fclose(stream);
    if (body != NULL) {
        char path[FTRACE_PATH];
        snprintf(path, sizeof path, "%s/frame-resolve.jsonl", g.dir);
        FILE *file = fopen(path, "ab");
        if (file != NULL) {
            fwrite(body, 1, strlen(body), file);
            fclose(file);
        }
        free(body);
    }
}

void eu4_gfx_frame_trace_clear_entry(eu4_gfx_capture_frame *frame,
                                     eu4_gfx_observe_call *call) {
    (void)frame;
    if (!g.enabled) {
        return;
    }
    pthread_mutex_lock(&g_lock);
    call->retain = 0;
    if (g.boundary_fb >= 0) {
        GLint draw_framebuffer = 0;
        glGetIntegerv(GL_DRAW_FRAMEBUFFER_BINDING, &draw_framebuffer);
        if (draw_framebuffer == g.boundary_fb) {
            if (g.armed) {
                finish_capture("boundary_clear");
            }
            if (!g.captured) {
                int time_ready = g.after_ms == 0 ||
                                 now_ms() - g.start_ms >= g.after_ms;
                int enough = time_ready && g.frame_draws >= g.min_draws &&
                             caller_set_matches();
                if (enough) {
                    frame_reset();
                    start_capture_at_frame(g.present_index + 1);
                }
            }
            frame_reset();
        }
    }
    if (g.armed && g.events != NULL && g.event_count >= (int)g.max_draws) {
        g.truncated = 1;
    }
    if (g.armed && g.events != NULL && g.event_count < (int)g.max_draws) {
        GLfloat color[4] = {0.0f, 0.0f, 0.0f, 1.0f};
        GLfloat depth = 1.0f;
        GLint stencil = 0;
        if (frame->rdx != 0) {
            GLfloat captured[4] = {0.0f, 0.0f, 0.0f, 1.0f};
            if (eu4_gfx_observe_read_bytes((const void *)(uintptr_t)frame->rdx,
                                           captured, sizeof(captured))) {
                memcpy(color, captured, sizeof(color));
            }
        }
        char *buffer = NULL;
        size_t size = 0;
        FILE *memory = open_memstream(&buffer, &size);
        if (memory != NULL) {
            json_out out = {memory, 0};
            jprintf(&out,
                    "{\"kind\":\"clear\",\"before_first_draw\":%s,"
                    "\"flags\":%d,\"color\":[%.9g,%.9g,%.9g,%.9g],"
                    "\"depth\":%.9g,\"stencil\":%d,",
                    bool_text(g.event_count == 0), (int)frame->rsi,
                    (double)color[0], (double)color[1], (double)color[2],
                    (double)color[3], (double)depth, stencil);
            capture_target(&out, NULL, NULL);
            jprintf(&out, "}");
            fclose(memory);
            if (!out.failed && buffer != NULL) {
                emit_event(buffer);
                g.event_count++;
            }
            free(buffer);
        }
    }
    pthread_mutex_unlock(&g_lock);
}

void eu4_gfx_frame_trace_clear_exit(eu4_gfx_capture_frame *frame,
                                    eu4_gfx_observe_call *call,
                                    eu4_gfx_observe_record *record) {
    (void)frame;
    (void)record;
    call->retain = 0;
}

static void draw_entry_common(eu4_gfx_capture_frame *frame,
                              eu4_gfx_observe_call *call, int indexed,
                              int instanced) {
    (void)frame;
    (void)indexed;
    if (!g.enabled) {
        return;
    }
    pthread_mutex_lock(&g_lock);
    if (g.gl_vendor[0] == 0) {
        const char *vendor = (const char *)glGetString(GL_VENDOR);
        const char *renderer = (const char *)glGetString(GL_RENDERER);
        const char *version = (const char *)glGetString(GL_VERSION);
        snprintf(g.gl_vendor, sizeof(g.gl_vendor), "%s", vendor ? vendor : "");
        snprintf(g.gl_renderer, sizeof(g.gl_renderer), "%s",
                 renderer ? renderer : "");
        snprintf(g.gl_version, sizeof(g.gl_version), "%s", version ? version : "");
    }
    caller_add(call->caller_rva);
    g.frame_draws++;
    {
        uint64_t master = 0;
        int mode = 0;
        int count = 0;
        int first = 0;
        int instances = 1;
        int base_vertex = 0;
        int32_t raw = (int32_t)frame->rsi;
        if (eu4_gfx_observe_read((const void *)(uintptr_t)frame->rdi, &master,
                                 sizeof(master)) &&
            master != 0) {
            uint8_t snapshot[0x171];
            if (eu4_gfx_observe_read_bytes((const void *)(uintptr_t)master,
                                           snapshot, sizeof(snapshot))) {
                memcpy(&mode, snapshot + 0x08, sizeof(mode));
                if (indexed) {
                    memcpy(&count, snapshot + 0x68, sizeof(count));
                    first = 0;
                    base_vertex = instanced ? 0 : (int32_t)frame->rcx;
                    instances = instanced ? (int32_t)frame->rdx : 1;
                } else {
                    memcpy(&count, snapshot + 0x64, sizeof(count));
                    first = (int32_t)frame->rdx;
                }
                if (raw > 0) {
                    count = raw;
                }
            }
        }
        g_pending.call_id = call->call_id;
        g_pending.master = (uintptr_t)master;
        g_pending.valid = 1;
        g_pending.indexed = indexed;
        g_pending.instanced = instanced;
        g_pending.mode = mode;
        g_pending.first = first;
        g_pending.count = count;
        g_pending.instances = instances;
        g_pending.base_vertex = base_vertex;
    }
    if (g.armed && g.events != NULL) {
        for (int i = 0; i < g.pre_post_count; i++) {
            if (g.pre_post_callers[i] != call->caller_rva) {
                continue;
            }
            char *pre_json = NULL;
            size_t pre_size = 0;
            FILE *pre_stream = open_memstream(&pre_json, &pre_size);
            if (pre_stream != NULL) {
                json_out pre_out = {pre_stream, 0};
                (void)capture_target_pixels("pre", "pre_image", &pre_out, NULL,
                                            NULL);
                fclose(pre_stream);
                if (!pre_out.failed && pre_json != NULL) {
                    snprintf(g.pre_image_json, sizeof(g.pre_image_json), "%s",
                             pre_json);
                    g.pre_image_valid = 1;
                }
                free(pre_json);
            }
            break;
        }
    }
    if (g.armed && g.events != NULL && !g.have_seed) {
        char *buffer = NULL;
        size_t size = 0;
        FILE *memory = open_memstream(&buffer, &size);
        if (memory != NULL) {
            json_out out = {memory, 0};
            jprintf(&out, "{");
            int width = 0, height = 0;
            if (capture_seed(&out, &width, &height) == 0) {
                jprintf(&out, "}");
                g.seed_width = width;
                g.seed_height = height;
            } else {
                jprintf(&out, "\"seed_error\":true}");
            }
            fclose(memory);
            if (!out.failed && buffer != NULL) {
                snprintf(g.seed_json, sizeof(g.seed_json), "%s", buffer);
                g.have_seed = 1;
            }
            free(buffer);
        }
    }
    pthread_mutex_unlock(&g_lock);
}

static void draw_exit_common(eu4_gfx_capture_frame *frame,
                             eu4_gfx_observe_call *call, int indexed,
                             GLint mode, int first, int count,
                             int instance_count, int base_vertex,
                             uintptr_t master) {
    (void)frame;
    if (!g.enabled) {
        return;
    }
    pthread_mutex_lock(&g_lock);
    call->retain = 0;
    if (g.armed && g.events != NULL && g.event_count >= (int)g.max_draws) {
        g.truncated = 1;
    }
    if (g.armed && g.events != NULL && g.event_count < (int)g.max_draws) {
        char *buffer = NULL;
        size_t size = 0;
        FILE *memory = open_memstream(&buffer, &size);
        if (memory != NULL) {
            json_out out = {memory, 0};
            jprintf(&out, "{\"kind\":\"draw\",\"index\":%d", g.event_count);
            if (!g.seed_emitted && g.seed_json[0] != 0) {
                const char *seed = g.seed_json;
                size_t length = strlen(seed);
                if (length >= 2 && seed[0] == '{' && seed[length - 1] == '}') {
                    jprintf(&out, ",%.*s", (int)(length - 2), seed + 1);
                }
                g.seed_emitted = 1;
            }
            if (g.pre_image_valid) {
                jprintf(&out, ",%s", g.pre_image_json);
                char *post_json = NULL;
                size_t post_size = 0;
                FILE *post_stream = open_memstream(&post_json, &post_size);
                if (post_stream != NULL) {
                    json_out post_out = {post_stream, 0};
                    (void)capture_target_pixels("post", "post_image", &post_out,
                                                NULL, NULL);
                    fclose(post_stream);
                    if (!post_out.failed && post_json != NULL) {
                        jprintf(&out, ",%s", post_json);
                    }
                    free(post_json);
                }
                g.pre_image_valid = 0;
                g.pre_image_json[0] = 0;
            }
            jprintf(&out, ",");
            capture_draw_state(&out, indexed, mode, first, count,
                               instance_count, base_vertex, master, call);
            jprintf(&out, "}");
            fclose(memory);
            if (!out.failed && buffer != NULL) {
                emit_event(buffer);
                g.event_count++;
            }
            free(buffer);
        }
    }
    pthread_mutex_unlock(&g_lock);
}

void eu4_gfx_frame_trace_draw_entry(eu4_gfx_capture_frame *frame,
                                    eu4_gfx_observe_call *call) {
    draw_entry_common(frame, call, 0, 0);
}

void eu4_gfx_frame_trace_draw_exit(eu4_gfx_capture_frame *frame,
                                   eu4_gfx_observe_call *call) {
    (void)frame;
    if (!g.enabled) {
        return;
    }
    pending_draw pending = g_pending;
    if (!pending.valid || pending.call_id != call->call_id) {
        pending.mode = 0;
        pending.count = 0;
        pending.first = 0;
        pending.instances = 1;
    }
    draw_exit_common(frame, call, pending.indexed, pending.mode, pending.first,
                     pending.count, pending.instances, pending.base_vertex,
                     pending.master);
}

void eu4_gfx_frame_trace_draw_indexed_entry(eu4_gfx_capture_frame *frame,
                                            eu4_gfx_observe_call *call) {
    eu4_gfx_observe_set_kind(call, EU4_GFX_OBSERVE_KIND_OPERATION);
    draw_entry_common(frame, call, 1, 0);
}

void eu4_gfx_frame_trace_draw_indexed_exit(eu4_gfx_capture_frame *frame,
                                           eu4_gfx_observe_call *call,
                                           eu4_gfx_observe_record *record) {
    (void)record;
    if (!g.enabled) {
        return;
    }
    pending_draw pending = g_pending;
    if (!pending.valid || pending.call_id != call->call_id) {
        pending.mode = 0;
        pending.count = 0;
        pending.first = 0;
        pending.instances = 1;
    }
    draw_exit_common(frame, call, 1, pending.mode, pending.first,
                     pending.count, pending.instances, pending.base_vertex,
                     pending.master);
}

void eu4_gfx_frame_trace_draw_instanced_entry(eu4_gfx_capture_frame *frame,
                                              eu4_gfx_observe_call *call) {
    eu4_gfx_observe_set_kind(call, EU4_GFX_OBSERVE_KIND_OPERATION);
    draw_entry_common(frame, call, 1, 1);
}

void eu4_gfx_frame_trace_draw_instanced_exit(eu4_gfx_capture_frame *frame,
                                             eu4_gfx_observe_call *call,
                                             eu4_gfx_observe_record *record) {
    (void)record;
    if (!g.enabled) {
        return;
    }
    pending_draw pending = g_pending;
    int instances = pending.valid ? (int32_t)frame->rdx : 1;
    if (!pending.valid || pending.call_id != call->call_id) {
        pending.mode = 0;
        pending.count = 0;
        pending.first = 0;
    }
    (void)instances;
    draw_exit_common(frame, call, 1, pending.mode, pending.first,
                     pending.count, pending.instances, pending.base_vertex,
                     pending.master);
}

int eu4_gfx_frame_trace_test_manifest(const char *directory) {
    if (directory == NULL || directory[0] != '/') {
        return -1;
    }
    snprintf(g.dir, sizeof(g.dir), "%s", directory);
    if (mkdir_p(g.dir) != 0) {
        return -1;
    }
    snprintf(g.reference_file, sizeof(g.reference_file), "%s",
             "frame-reference.bgra");
    snprintf(g.reference_sha, sizeof(g.reference_sha), "%s",
             "0000000000000000000000000000000000000000000000000000000000000000");
    g.reference_bytes = 0;
    g.reference_width = 0;
    g.reference_height = 0;
    g.capturing_frame = 1;
    g.event_count = 0;
    g.caller_count = 0;
    g.seed_width = 0;
    g.seed_height = 0;
    g.blob_count = 0;
    g.truncated = 0;
    write_frame_manifest(1, "test");
    char path[FTRACE_PATH];
    snprintf(path, sizeof(path), "%s/frame-trace.json", g.dir);
    FILE *probe = fopen(path, "r");
    if (probe == NULL) {
        return -1;
    }
    fclose(probe);
    return 0;
}
