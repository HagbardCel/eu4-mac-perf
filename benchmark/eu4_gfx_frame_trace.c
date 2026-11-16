/* Complete-frame original-GL trace.
 *
 * See eu4_gfx_frame_trace.h for the contract. This module is observation-only:
 * every GL binding it must change for a readback is saved and restored, and it
 * never issues a draw, clear or state change of its own.
 */

#define GL_SILENCE_DEPRECATION 1
#define GL_GLEXT_PROTOTYPES 1

#include "eu4_gfx_frame_trace.h"
#include "eu4_gfx_draw_semantics.h"

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
#include <stdatomic.h>
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
#define FTRACE_MAX_BLOB_BYTES (512u * 1024u * 1024u)
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
    int finishing;
    int capturing_frame;
    int matching_frames;
    int matching_scenes;
    int event_count;
    int stream_validated;
    uint64_t timeline_seq;
    uint32_t scene_copy_count;
    uint64_t copy_count;
    int test_only;
    int test_fail_flush;
    int test_fail_reference;
    int test_fail_manifest_hash;
    int test_fail_event_digest;
    int have_seed;

    /* Authoritative same-process identity and one-shot handoff. */
    int seal_enabled;
    uint64_t capture_id;
    uint64_t next_capture_id;
    uint64_t process_start_ns;
    int32_t process_pid;
    char session_id[64];
    uint64_t timeout_ms;
    uint64_t armed_ms;
    uint64_t draw_events;
    uint64_t clear_events;
    uint64_t copy_events;
    int seal_written;
    uint64_t readback_ns;
    uint64_t finalize_ns;
    uint64_t seal_ns;
    char manifest_sha[65];
    uint64_t events_bytes;
    char events_sha[65];
    int events_verified;

    /* Caller set of the frame currently being observed (cheap, always on). */
    uint64_t frame_callers[FTRACE_MAX_CALLERS];
    int frame_caller_count;
    uint32_t frame_draws;

    FILE *events;
    char partial_path[FTRACE_PATH];
    char final_path[FTRACE_PATH];
    char timeline_path[FTRACE_PATH];
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
    int capture_failed;

    struct blob_entry *blobs;
    int blob_count;
    size_t blob_bytes;
    uint64_t frame_blob_bytes; /* continuous: reset per frame */

    /* Per-frame caches. Shader sources are static per program; a texture's
       contents are static for the frame unless it is a render target. Reading
       every texture for every draw is what makes a several-thousand-draw menu
       frame unaffordable. */
    struct shader_cache_entry *shader_cache;
    struct texture_cache_entry *texture_cache;
    char render_targets[64][64];
    int render_target_count;

    /* Bounded continuous capture.  The single-frame path leaves every field
       below zero/empty and keeps its one-shot gate unchanged. */
    int continuous;
    uint32_t continuous_max_frames;
    uint32_t continuous_max_pending;
    uint64_t continuous_max_bytes;
    uint32_t continuous_frames_completed;
    uint64_t continuous_bytes_written;
    uint64_t continuous_dedup_bytes;
    int continuous_stopped;
    char continuous_reason[64];
    char continuous_base[FTRACE_PATH];
    char previous_dir[FTRACE_PATH];
    char previous_manifest_sha[65];
    char previous_events_sha[65];
    uint64_t previous_capture_id;
    char previous_render_targets[64][64];
    int previous_render_target_count;
    /* Renderer-generated resources that remain valid across frame boundaries.
       Unlike render_targets (this frame's writes) this set is only mutated by
       a write or an evidenced lifecycle transition (delete/reset). */
    char carried_targets[64][64];
    int carried_target_count;
    char campaign_records[64][768];
    int campaign_record_count;
    char pending_deletes[64][64];
    int pending_delete_events[64];
    int pending_delete_count;
    char pending_mutations[64][64];
    char pending_mutation_ops[64][32];
    int pending_mutation_events[64];
    int pending_mutation_levels[64];
    int pending_mutation_count;
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

static void render_target_remove(const char *name) {
    if (name == NULL || name[0] == 0) {
        return;
    }
    for (int i = 0; i < g.render_target_count; i++) {
        if (strcmp(g.render_targets[i], name) == 0) {
            memmove(g.render_targets[i], g.render_targets[g.render_target_count - 1],
                    sizeof(g.render_targets[0]));
            g.render_target_count--;
            return;
        }
    }
}

static int carried_target_known(const char *name) {
    for (int i = 0; i < g.carried_target_count; i++) {
        if (strcmp(g.carried_targets[i], name) == 0) {
            return 1;
        }
    }
    return 0;
}

static void carried_target_add(const char *name) {
    if (name == NULL || name[0] == 0 || carried_target_known(name)) {
        return;
    }
    if (g.carried_target_count < 64) {
        snprintf(g.carried_targets[g.carried_target_count], 64, "%s", name);
        g.carried_target_count++;
    }
}

static void carried_target_remove(const char *name) {
    if (name == NULL || name[0] == 0) {
        return;
    }
    for (int i = 0; i < g.carried_target_count; i++) {
        if (strcmp(g.carried_targets[i], name) == 0) {
            memmove(g.carried_targets[i],
                    g.carried_targets[g.carried_target_count - 1],
                    sizeof(g.carried_targets[0]));
            g.carried_target_count--;
            return;
        }
    }
}

static void previous_target_remove(const char *name) {
    if (name == NULL || name[0] == 0) {
        return;
    }
    for (int i = 0; i < g.previous_render_target_count; i++) {
        if (strcmp(g.previous_render_targets[i], name) == 0) {
            memmove(g.previous_render_targets[i],
                    g.previous_render_targets[
                        g.previous_render_target_count - 1],
                    sizeof(g.previous_render_targets[0]));
            g.previous_render_target_count--;
            return;
        }
    }
}

static void blobs_reset(void) {
    for (blob_entry *entry = g.blobs; entry != NULL;) {
        blob_entry *next = entry->next;
        free(entry);
        entry = next;
    }
    g.blobs = NULL;
    g.blob_count = 0;
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

void eu4_gfx_frame_trace_texture_deleted(int count, const unsigned *names) {
    if (count <= 0 || names == NULL) {
        return;
    }
    pthread_mutex_lock(&g_lock);
    for (int i = 0; i < count; i++) {
        char name[64];
        snprintf(name, sizeof(name), "%u", names[i]);
        render_target_remove(name);
        previous_target_remove(name);
        carried_target_remove(name);
        int known = 0;
        for (int j = 0; j < g.pending_delete_count; j++) {
            if (strcmp(g.pending_deletes[j], name) == 0) {
                known = 1;
                break;
            }
        }
        if (!known && g.pending_delete_count < 64) {
            snprintf(g.pending_deletes[g.pending_delete_count], 64, "%s",
                     name);
            g.pending_delete_events[g.pending_delete_count] =
                g.armed ? g.event_count : -1;
            g.pending_delete_count++;
        }
    }
    pthread_mutex_unlock(&g_lock);
}

void eu4_gfx_frame_trace_test_register_target(const char *name) {
    if (!g.test_only || name == NULL || name[0] == 0) {
        return;
    }
    pthread_mutex_lock(&g_lock);
    render_target_add(name);
    pthread_mutex_unlock(&g_lock);
}

void eu4_gfx_frame_trace_texture_mutated(unsigned name, int level,
                                         int sub_image, int texture_target) {
    if (name == 0) {
        return;
    }
    pthread_mutex_lock(&g_lock);
    char text[64];
    snprintf(text, sizeof(text), "%u", name);
    /* Only renderer-owned resources are unsafe: an external CPU-supplied
       texture is captured at each sample, so its contents are authoritative
       even when they change. */
    int relevant = render_target_known(text) || carried_target_known(text);
    if (relevant) {
        int known = 0;
        for (int j = 0; j < g.pending_mutation_count; j++) {
            if (strcmp(g.pending_mutations[j], text) == 0 &&
                g.pending_mutation_levels[j] == level) {
                known = 1;
                break;
            }
        }
        if (!known && g.pending_mutation_count < 64) {
            snprintf(g.pending_mutations[g.pending_mutation_count], 64, "%s",
                     text);
            snprintf(g.pending_mutation_ops[g.pending_mutation_count], 32,
                     "%s", sub_image ? "glTexSubImage2D"
                                     : (texture_target ? "glTexImage2D"
                                                       : "glCopyTexImage2D"));
            g.pending_mutation_events[g.pending_mutation_count] =
                g.armed ? g.event_count : -1;
            g.pending_mutation_levels[g.pending_mutation_count] = level;
            g.pending_mutation_count++;
        }
    }
    pthread_mutex_unlock(&g_lock);
}

static const char *g_capture_failure_reason;

/* Sealed-frame consumer registry.  It is deliberately separate from g_lock:
 * the callback must run after the trace writer's mutex is released. */
static pthread_mutex_t g_seal_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t g_seal_cond = PTHREAD_COND_INITIALIZER;
static eu4_frame_sealed_callback g_seal_callback;
static void *g_seal_context;
static eu4_frame_capture_descriptor g_seal_pending_descriptor;
static int g_seal_pending;
static _Atomic int g_seal_pending_flag;
static int g_seal_dispatching;
static uint32_t g_seal_notifications;
static uint32_t g_seal_reentrant;
static int g_seal_last_status;
static int g_seal_last_written;
static _Thread_local int tls_seal_dispatch;
static int emit_event(const char *json);
static uint64_t now_ms(void);
static uint64_t now_ns(void);
static void timeline_write(const char *format, ...);
static const char *bool_text(int value);
static int write_reference_pixels(const uint8_t *pixels, uint32_t width,
                                  uint32_t height);
static int capture_reference(void);
static int read_drawable_bgra(uint32_t *out_width, uint32_t *out_height,
                              uint8_t **out_pixels);
static void sha256_hex(const void *data, size_t size, char out[65]);
static int write_atomic(const char *path, const void *data, size_t size);
static int write_frame_manifest(int complete, const char *reason);
static void finish_capture_ex(const char *reason, int allow_reference);
static int capture_timed_out_locked(void);
static void continuous_write_campaign(const char *status,
                                      const char *reason);

static void fail_capture_locked(const char *reason) {
    g.capture_failed = 1;
    if (g_capture_failure_reason == NULL) {
        g_capture_failure_reason = reason;
    }
}

/* Ordered copy/resolve event from the GL interposer.  Every copy is logged to
   the single monotonic timeline when enabled; only an armed capture records
   the event that replay consumes.  Unsupported variants fail the capture
   rather than accepting a source we cannot reconstruct. */
int eu4_gfx_frame_trace_copy_mode(void) {
    pthread_mutex_lock(&g_lock);
    g.copy_count++;
    g.scene_copy_count++;
    int record = g.armed ||
                 (g.timeline_path[0] != 0 && g.scene_copy_count == 1);
    pthread_mutex_unlock(&g_lock);
    return record;
}

int eu4_gfx_frame_trace_copy_event(const eu4_gfx_copy_event *event) {
    if (event == NULL) {
        return 0;
    }
    pthread_mutex_lock(&g_lock);
    int accepted = 0;
    if (g.timeline_path[0] != 0 && !g.capture_failed) {
        uint64_t sequence = ++g.timeline_seq;
        timeline_write(
            "{\"kind\":\"copy\",\"seq\":%llu,\"pid\":%d,\"monotonic_ns\":%llu,"
            "\"scene\":%d,\"present\":%d,\"armed\":%d,\"captured\":%d,"
            "\"source_framebuffer\":%d,\"read_buffer\":%d,\"draw_buffer\":%d,"
            "\"source_attachment_type\":%d,\"source_attachment_name\":%d,"
            "\"source_level\":%d,\"source_resolved\":%d,"
            "\"destination\":\"%d\",\"destination_level\":%d,"
            "\"source_rect\":[%d,%d,%d,%d],\"destination_offset\":[%d,%d]}",
            (unsigned long long)sequence, (int)getpid(),
            (unsigned long long)now_ns(), g.scene_index,
            g.present_index, g.armed, g.captured, event->source_framebuffer,
            event->read_buffer, event->draw_buffer, event->source_color_type,
            event->source_color_name, event->source_level,
            event->source_resolved, event->destination_texture,
            event->destination_level, event->x, event->y, event->width,
            event->height, event->dx, event->dy);
    }
    if (!g.armed || g.events == NULL) {
        pthread_mutex_unlock(&g_lock);
        return 0;
    }
    if (g.event_count >= (int)g.max_draws) {
        g.truncated = 1;
        pthread_mutex_unlock(&g_lock);
        return 0;
    }
    if (event->is_blit || event->source_level != 0 ||
        event->destination_level != 0 || !event->source_resolved ||
        !event->destination_resolved) {
        fail_capture_locked("unsupported_copy_variant");
        pthread_mutex_unlock(&g_lock);
        return 0;
    }
    if (event->source_framebuffer == 0 &&
        event->read_buffer != 0x0405 /* GL_BACK */) {
        /* The Metal default target represents the back buffer.  A front-buffer
           read is a pre-frame input we have not captured; refuse to substitute
           the back buffer for it. */
        fail_capture_locked("unsupported_default_read_buffer");
        pthread_mutex_unlock(&g_lock);
        return 0;
    }
    int destination_width = event->destination_width;
    int destination_height = event->destination_height;
    int internal_format = event->destination_internal_format;
    if ((destination_width <= 0 || destination_height <= 0 ||
         internal_format == 0) &&
        !g.test_only) {
        GLint queried_width = 0, queried_height = 0, queried_format = 0;
        glGetTexLevelParameteriv(GL_TEXTURE_2D, event->destination_level,
                                 GL_TEXTURE_WIDTH, &queried_width);
        glGetTexLevelParameteriv(GL_TEXTURE_2D, event->destination_level,
                                 GL_TEXTURE_HEIGHT, &queried_height);
        glGetTexLevelParameteriv(GL_TEXTURE_2D, event->destination_level,
                                 GL_TEXTURE_INTERNAL_FORMAT, &queried_format);
        destination_width = queried_width;
        destination_height = queried_height;
        internal_format = queried_format;
    }
    char source_image[512] = "";
    if (g.armed && event->source_framebuffer == 0) {
        const char *capture_source = getenv(
            "EU4_GFX_FRAME_TRACE_CAPTURE_COPY_SOURCE");
        if (capture_source != NULL && strcmp(capture_source, "1") == 0) {
            uint32_t source_width = 0, source_height = 0;
            uint8_t *source_pixels = NULL;
            if (read_drawable_bgra(&source_width, &source_height,
                                   &source_pixels) != 0 ||
                source_width != (uint32_t)event->source_width ||
                source_height != (uint32_t)event->source_height ||
                source_pixels == NULL) {
                free(source_pixels);
                fail_capture_locked("copy_source_read");
            } else {
                size_t source_bytes = (size_t)source_width * source_height * 4u;
                char source_path[FTRACE_PATH];
                char source_file[FTRACE_NAME];
                char source_sha[65];
                snprintf(source_file, sizeof(source_file),
                         "copy-source-%llu.bgra",
                         (unsigned long long)(g.copy_count + 1));
                snprintf(source_path, sizeof(source_path), "%s/blobs/%s",
                         g.dir, source_file);
                if (write_atomic(source_path, source_pixels, source_bytes) == 0) {
                    sha256_hex(source_pixels, source_bytes, source_sha);
                    snprintf(source_image, sizeof(source_image),
                             ",\"source_image\":{\"width\":%u,"
                             "\"height\":%u,\"bytes\":%zu,"
                             "\"file\":\"%s\",\"sha256\":\"%s\"}",
                             source_width, source_height, source_bytes,
                             source_file, source_sha);
                } else {
                    fail_capture_locked("copy_source_write");
                }
                free(source_pixels);
            }
        }
    }
    char body[4096];
    int length = snprintf(
        body, sizeof body,
        "{\"kind\":\"resolve\",\"operation\":\"%s\",\"is_blit\":%s,"
        "\"source_framebuffer\":%d,\"read_buffer\":%d,\"draw_buffer\":%d,"
        "\"double_buffered\":%d,\"source\":\"%d\","
        "\"source_attachment_type\":%d,\"source_attachment_name\":%d,"
        "\"source_attachment\":%d,"
        "\"source_level\":%d,\"source_resolved\":%s,"
        "\"source_width\":%d,\"source_height\":%d,"
        "\"source_internal_format\":%d,"
        "\"destination\":\"%d\",\"destination_level\":%d,"
        "\"destination_resolved\":%s,"
        "\"source_rect\":[%d,%d,%d,%d],"
        "\"destination_offset\":[%d,%d],\"width\":%d,\"height\":%d,"
        "\"destination_width\":%d,\"destination_height\":%d,"
        "\"internal_format\":%d,\"sequence\":%llu,\"pid\":%d,"
        "\"scene\":%d,\"present\":%d,\"captured_frame\":%d%s}",
        event->is_blit ? "glBlitFramebuffer" : "glCopyTexSubImage2D",
        bool_text(event->is_blit), event->source_framebuffer,
        event->read_buffer, event->draw_buffer, event->double_buffered,
        event->source_texture, event->source_color_type,
        event->source_color_name, event->source_attachment,
        event->source_level, bool_text(event->source_resolved),
        event->source_width,
        event->source_height, event->source_internal_format,
        event->destination_texture, event->destination_level,
        bool_text(event->destination_resolved), event->x, event->y,
        event->width, event->height, event->dx, event->dy,
        event->width, event->height, destination_width, destination_height,
        internal_format, (unsigned long long)++g.timeline_seq, (int)getpid(),
        g.scene_index, g.present_index, g.capturing_frame, source_image);
    if (length <= 0 || (size_t)length >= sizeof body) {
        fail_capture_locked("copy_event_oversized");
    } else if (emit_event(body)) {
        g.event_count++;
        g.copy_events++;
        accepted = 1;
    }
    pthread_mutex_unlock(&g_lock);
    return accepted;
}

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

/* ------------------------------------------------------------------------- */
/* Sealed-frame handoff                                                       */
/* ------------------------------------------------------------------------- */

int eu4_gfx_frame_sealed_subscribe(eu4_frame_sealed_callback callback,
                                   void *context) {
    if (callback == NULL) {
        return -1;
    }
    pthread_mutex_lock(&g_seal_lock);
    if (g_seal_callback != NULL) {
        pthread_mutex_unlock(&g_seal_lock);
        return -1;
    }
    g_seal_callback = callback;
    g_seal_context = context;
    if (g_seal_pending) {
        /* A frame was sealed while no consumer was registered. */
        atomic_store_explicit(&g_seal_pending_flag, 1, memory_order_release);
    }
    pthread_mutex_unlock(&g_seal_lock);
    return 0;
}

void eu4_gfx_frame_sealed_unsubscribe(void) {
    pthread_mutex_lock(&g_seal_lock);
    if (!tls_seal_dispatch) {
        /* Do not clear the context while a delivery is using it. */
        while (g_seal_dispatching) {
            pthread_cond_wait(&g_seal_cond, &g_seal_lock);
        }
    }
    g_seal_callback = NULL;
    g_seal_context = NULL;
    pthread_mutex_unlock(&g_seal_lock);
}

int eu4_gfx_frame_sealed_pending(void) {
    pthread_mutex_lock(&g_seal_lock);
    int pending = g_seal_pending;
    pthread_mutex_unlock(&g_seal_lock);
    return pending;
}

void eu4_gfx_frame_sealed_dispatch(void) {
    if (tls_seal_dispatch) {
        pthread_mutex_lock(&g_seal_lock);
        g_seal_reentrant++;
        pthread_mutex_unlock(&g_seal_lock);
        return;
    }
    if (!atomic_load_explicit(&g_seal_pending_flag, memory_order_acquire)) {
        return;
    }
    eu4_frame_capture_descriptor descriptor;
    eu4_frame_sealed_callback callback;
    void *context;
    pthread_mutex_lock(&g_seal_lock);
    while (g_seal_dispatching) {
        pthread_cond_wait(&g_seal_cond, &g_seal_lock);
    }
    if (!g_seal_pending) {
        pthread_mutex_unlock(&g_seal_lock);
        return;
    }
    if (g_seal_callback == NULL) {
        /* Keep the sealed frame pending for a consumer that registers later,
           but stop the per-hook acquire load until it does. */
        atomic_store_explicit(&g_seal_pending_flag, 0, memory_order_release);
        pthread_mutex_unlock(&g_seal_lock);
        return;
    }
    g_seal_dispatching = 1;
    g_seal_pending = 0;
    atomic_store_explicit(&g_seal_pending_flag, 0, memory_order_release);
    descriptor = g_seal_pending_descriptor;
    callback = g_seal_callback;
    context = g_seal_context;
    pthread_mutex_unlock(&g_seal_lock);

    tls_seal_dispatch = 1;
    int status = callback != NULL ? callback(&descriptor, context) : 0;
    tls_seal_dispatch = 0;

    pthread_mutex_lock(&g_seal_lock);
    /* A delivery happened even when the consumer reports failure; the status
     * distinguishes the two without re-notifying. */
    g_seal_notifications++;
    g_seal_last_status = status;
    g_seal_dispatching = 0;
    pthread_cond_broadcast(&g_seal_cond);
    pthread_mutex_unlock(&g_seal_lock);
}

uint32_t eu4_gfx_frame_sealed_notifications(void) {
    pthread_mutex_lock(&g_seal_lock);
    uint32_t count = g_seal_notifications;
    pthread_mutex_unlock(&g_seal_lock);
    return count;
}

uint32_t eu4_gfx_frame_sealed_reentrant_rejections(void) {
    pthread_mutex_lock(&g_seal_lock);
    uint32_t count = g_seal_reentrant;
    pthread_mutex_unlock(&g_seal_lock);
    return count;
}

int eu4_gfx_frame_sealed_last_callback_status(void) {
    pthread_mutex_lock(&g_seal_lock);
    int status = g_seal_last_status;
    pthread_mutex_unlock(&g_seal_lock);
    return status;
}

int eu4_gfx_frame_sealed_last_seal_written(void) {
    pthread_mutex_lock(&g_seal_lock);
    int written = g_seal_last_written;
    pthread_mutex_unlock(&g_seal_lock);
    return written;
}

int eu4_gfx_frame_trace_lock_probe(void) {
    if (pthread_mutex_trylock(&g_lock) != 0) {
        return 0;
    }
    pthread_mutex_unlock(&g_lock);
    return 1;
}

void eu4_gfx_frame_trace_test_reset_seal(void) {
    pthread_mutex_lock(&g_seal_lock);
    g_seal_callback = NULL;
    g_seal_context = NULL;
    g_seal_pending = 0;
    atomic_store_explicit(&g_seal_pending_flag, 0, memory_order_release);
    g_seal_dispatching = 0;
    g_seal_notifications = 0;
    g_seal_reentrant = 0;
    g_seal_last_status = 0;
    g_seal_last_written = 0;
    memset(&g_seal_pending_descriptor, 0, sizeof(g_seal_pending_descriptor));
    pthread_cond_broadcast(&g_seal_cond);
    pthread_mutex_unlock(&g_seal_lock);
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
    uint64_t blob_cap = g.continuous
        ? (4ull * 1024ull * 1024ull * 1024ull)
        : (uint64_t)FTRACE_MAX_BLOB_BYTES;
    if (size > blob_cap || g.blob_bytes > blob_cap - size) {
        fail_capture_locked("artifact_size_limit");
        return -1;
    }
    char name[FTRACE_NAME];
    snprintf(name, sizeof(name), "%s-%s.bin", prefix, sha);
    char path[FTRACE_PATH];
    snprintf(path, sizeof(path), "%s/blobs/%s", g.dir, name);
    int linked = 0;
    if (g.continuous && g.previous_dir[0] != 0) {
        /* Immutable payloads are content-addressed.  Reusing the previous
           frame's inode preserves provenance and portable replay while
           avoiding another full copy of an unchanged multi-megabyte blob. */
        char source[FTRACE_PATH];
        snprintf(source, sizeof(source), "%s/blobs/%s", g.previous_dir, name);
        if (link(source, path) == 0) {
            linked = 1;
            g.continuous_dedup_bytes += (uint64_t)size;
        }
    }
    if (!linked && write_atomic(path, data, size) != 0) {
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
    g.blob_bytes += size;
    if (!linked) {
        g.frame_blob_bytes += size;
    }
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

static uint64_t now_ns(void) {
    struct timespec spec;
    clock_gettime(CLOCK_MONOTONIC, &spec);
    return (uint64_t)spec.tv_sec * 1000000000ull + (uint64_t)spec.tv_nsec;
}

/* One ordered, process-identifiable timeline.  Callers hold g_lock, so the
   timeline sequence is the same total order the capture observes. */
static void timeline_write(const char *format, ...) {
    if (g.timeline_path[0] == 0) {
        return;
    }
    FILE *file = fopen(g.timeline_path, "a");
    if (file == NULL) {
        return;
    }
    va_list args;
    va_start(args, format);
    if (vfprintf(file, format, args) >= 0) {
        fputc('\n', file);
    }
    va_end(args);
    fclose(file);
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
    /* Process identity and the one-shot gate survive a reconfigure: the
       descriptor promises a process-monotonic id and at most one seal. */
    uint64_t preserved_next_capture_id = g.next_capture_id;
    uint64_t preserved_process_start_ns = g.process_start_ns;
    int32_t preserved_process_pid = g.process_pid;
    int preserved_captured = g.captured;
    char preserved_session_id[64];
    memcpy(preserved_session_id, g.session_id, sizeof(preserved_session_id));
    memset(&g, 0, sizeof(g));
    g.next_capture_id = preserved_next_capture_id;
    g.process_start_ns = preserved_process_start_ns;
    g.process_pid = preserved_process_pid;
    g.captured = preserved_captured;
    memcpy(g.session_id, preserved_session_id, sizeof(g.session_id));
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
    const char *timeline = getenv("EU4_GFX_FRAME_TRACE_TIMELINE");
    const char *timeout_ms = getenv("EU4_GFX_FRAME_TRACE_TIMEOUT_MS");
    const char *seal = getenv("EU4_GFX_FRAME_SEAL");
    const char *observe_dir = getenv("EU4_GFX_FRAME_OBSERVE_DIR");
    g.configured = 1;
    g.enabled = 0;
    g.process_pid = (int32_t)getpid();
    if (g.process_start_ns == 0) {
        g.process_start_ns = now_ns();
    }
    g.seal_enabled = seal != NULL && strcmp(seal, "1") == 0;
    const char *configured_session = getenv("EU4_GFX_FRAME_SESSION");
    if (configured_session == NULL || configured_session[0] == 0) {
        configured_session = getenv("EU4_GFX_STARTUP_ID");
    }
    if (configured_session != NULL && configured_session[0] != 0) {
        snprintf(g.session_id, sizeof(g.session_id), "%s", configured_session);
    } else if (g.session_id[0] == 0) {
        snprintf(g.session_id, sizeof(g.session_id), "pid-%d-%llx", (int)getpid(),
                 (unsigned long long)g.process_start_ns);
    }
    g.test_only = test_only != NULL && strcmp(test_only, "1") == 0;
    if (g.test_only) {
        const char *fail_flush =
            getenv("EU4_GFX_FRAME_TRACE_TEST_FAIL_FLUSH");
        g.test_fail_flush = fail_flush != NULL && strcmp(fail_flush, "1") == 0;
        const char *fail_reference =
            getenv("EU4_GFX_FRAME_TRACE_TEST_FAIL_REFERENCE");
        g.test_fail_reference =
            fail_reference != NULL && strcmp(fail_reference, "1") == 0;
        const char *fail_manifest =
            getenv("EU4_GFX_FRAME_TRACE_TEST_FAIL_MANIFEST_HASH");
        g.test_fail_manifest_hash =
            fail_manifest != NULL && strcmp(fail_manifest, "1") == 0;
        const char *fail_event_digest =
            getenv("EU4_GFX_FRAME_TRACE_TEST_FAIL_EVENT_DIGEST");
        g.test_fail_event_digest =
            fail_event_digest != NULL && strcmp(fail_event_digest, "1") == 0;
    }
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
    /* Absolute frame selection is defined for the present boundary.  A scene
       request that also names an absolute frame would otherwise silently
       select a different occurrence, so reject the combination. */
    if (g.boundary_scene && g.at_frame != UINT64_MAX) {
        return -1;
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
    if (timeout_ms != NULL) {
        uint64_t value = 0;
        if (parse_u64(timeout_ms, &value) != 0) {
            return -1;
        }
        g.timeout_ms = value; /* 0 means unbounded */
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
    const char *continuous = getenv("EU4_GFX_FRAME_TRACE_CONTINUOUS");
    if (continuous != NULL && continuous[0] != 0) {
        if (strcmp(continuous, "1") == 0) {
            g.continuous = 1;
            g.seal_enabled = 1;
            g.continuous_max_frames = 5;
            g.continuous_max_pending = 4;
            g.continuous_max_bytes = 512ull * 1024ull * 1024ull;
            const char *max_frames =
                getenv("EU4_GFX_FRAME_TRACE_MAX_FRAMES");
            const char *max_pending =
                getenv("EU4_GFX_FRAME_TRACE_MAX_PENDING");
            const char *max_bytes =
                getenv("EU4_GFX_FRAME_TRACE_MAX_BYTES");
            if (max_frames != NULL) {
                uint64_t value = 0;
                if (parse_u64(max_frames, &value) != 0 || value == 0 ||
                    value > 64) {
                    return -1;
                }
                g.continuous_max_frames = (uint32_t)value;
            }
            if (max_pending != NULL) {
                uint64_t value = 0;
                if (parse_u64(max_pending, &value) != 0 || value == 0 ||
                    value > 64) {
                    return -1;
                }
                g.continuous_max_pending = (uint32_t)value;
            }
            if (max_bytes != NULL) {
                uint64_t value = 0;
                if (parse_u64(max_bytes, &value) != 0 || value == 0) {
                    return -1;
                }
                g.continuous_max_bytes = value;
            }
            snprintf(g.continuous_base, sizeof(g.continuous_base), "%s",
                     g.dir);
        } else if (strcmp(continuous, "0") != 0) {
            return -1;
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
    if (timeline != NULL && strcmp(timeline, "1") == 0) {
        snprintf(g.timeline_path, sizeof(g.timeline_path), "%s/timeline.jsonl",
                 g.dir);
    }
    if (g.continuous) {
        continuous_write_campaign("running", "");
    }
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

static int emit_event(const char *json) {
    if (g.events == NULL) {
        fail_capture_locked("event_stream_unavailable");
        return 0;
    }
    if (fputs(json, g.events) == EOF || fputc('\n', g.events) == EOF) {
        fail_capture_locked("event_write_failed");
        fclose(g.events);
        g.events = NULL;
        return 0;
    }
    if (fflush(g.events) != 0 ||
        (g.test_only && g.test_fail_flush)) {
        fail_capture_locked("event_flush_failed");
        fclose(g.events);
        g.events = NULL;
        return 0;
    }
    return 1;
}

/* Finalizes and validates the event stream before a manifest may claim
   completion.  Every record must be one complete JSON object and the number of
   records on disk must equal the number the capture reported. */
static int finalize_event_stream(void) {
    int ok = 1;
    if (g.events != NULL) {
        if (ferror(g.events) != 0 || fflush(g.events) != 0) {
            fail_capture_locked("event_flush_failed");
            ok = 0;
        }
        if (fclose(g.events) != 0) {
            fail_capture_locked("event_close_failed");
            ok = 0;
        }
        g.events = NULL;
    }
    if (g.capture_failed) {
        ok = 0;
    }
    FILE *file = fopen(g.partial_path, "rb");
    if (file == NULL) {
        fail_capture_locked("event_stream_missing");
        return 0;
    }
    char *line = NULL;
    size_t capacity = 0;
    ssize_t length = 0;
    int records = 0;
    int malformed = 0;
    int newline_terminated = 1;
    while ((length = getline(&line, &capacity, file)) >= 0) {
        int start = 0;
        while (start < length && (line[start] == ' ' || line[start] == '\t')) {
            start++;
        }
        int end = (int)length;
        if (end > start && line[end - 1] == '\n') {
            end--;
        } else {
            newline_terminated = 0;
        }
        int has_kind = 0;
        for (int i = start; i + 6 <= end; i++) {
            if (memcmp(line + i, "\"kind\"", 6) == 0) {
                has_kind = 1;
                break;
            }
        }
        if (end <= start || line[start] != '{' || line[end - 1] != '}' ||
            !has_kind) {
            malformed = 1;
        }
        records++;
    }
    free(line);
    fclose(file);
    if (malformed) {
        fail_capture_locked("malformed_event");
        ok = 0;
    }
    if (!newline_terminated) {
        fail_capture_locked("unterminated_event");
        ok = 0;
    }
    if (records != g.event_count) {
        fail_capture_locked("event_count_mismatch");
        ok = 0;
    }
    if (records == 0) {
        fail_capture_locked("empty_event_stream");
        ok = 0;
    }
    g.stream_validated = ok && !g.capture_failed;
    return g.stream_validated;
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
    GLint previous_array = 0, previous_element = 0;
    glGetIntegerv(GL_ARRAY_BUFFER_BINDING, &previous_array);
    glGetIntegerv(GL_ELEMENT_ARRAY_BUFFER_BINDING, &previous_element);

    /* Indexed ranges are populated from the actual index data. Nonindexed
       ranges are selected per attribute with checked 64-bit arithmetic. */
    int32_t index_first = 0;
    int32_t index_last = 0;
    int range_valid = 1;
    int capture_error = 0;
    int index_read_error = 0;
    uint8_t *index_data = NULL;
    size_t index_bytes = 0;

    if (indexed) {
        if (index_buffer == 0) {
            index_buffer = (GLuint)previous_element;
        }
        if (index_buffer != 0 && count > 0) {
            GLint buffer_size = 0;
            glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, index_buffer);
            glGetBufferParameteriv(GL_ELEMENT_ARRAY_BUFFER, GL_BUFFER_SIZE,
                                   &buffer_size);
            index_bytes = (size_t)count * 2u;
            if (buffer_size <= 0 || index_bytes > (size_t)buffer_size) {
                index_bytes = buffer_size > 0 ? (size_t)buffer_size : 0;
                range_valid = 0;
            }
            if (index_bytes > 0) {
                index_data = malloc(index_bytes);
                if (index_data == NULL) {
                    index_read_error = 1;
                    capture_error = 1;
                } else {
                    /* Do not drain the application's pending GL error here:
                       that is observable state. A pre-existing error makes the
                       capture fail closed rather than being silently cleared. */
                    /* Do not consume the engine's pending GL error state.
                       The actual index values are validated by
                       eu4_gfx_indexed_range below; a short read is caught by
                       the range check. */
                    glGetBufferSubData(GL_ELEMENT_ARRAY_BUFFER, 0,
                                       (GLsizeiptr)index_bytes, index_data);
                }
            }
            if (index_bytes < (size_t)count * 2u || index_data == NULL) {
                range_valid = 0;
            } else if (eu4_gfx_indexed_range((const uint16_t *)index_data,
                                             count, base_vertex, &index_first,
                                             &index_last) != 0) {
                range_valid = 0;
                capture_error = 1;
            }
        } else {
            range_valid = 0;
            capture_error = 1;
        }
    }

    jprintf(out, "\"attributes\":[");
    int emitted = 0;
    GLint attribute_count = 0;
    glGetProgramiv(program, GL_ACTIVE_ATTRIBUTES, &attribute_count);
    if (attribute_count < 0) {
        attribute_count = 0;
    }
    GLint max_vertex_attribs = 0;
    glGetIntegerv(GL_MAX_VERTEX_ATTRIBS, &max_vertex_attribs);
    if (!eu4_gfx_capture_attribute_count_ok((int)attribute_count,
                                            FTRACE_MAX_ATTRS)) {
        capture_error = 1;
        range_valid = 0;
        g_capture_failure_reason = "attribute_capacity";
        (void)max_vertex_attribs;
    }
    const int element_bytes = 2;
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
        GLvoid *pointer = NULL;
        glGetVertexAttribPointerv((GLuint)location,
                                  GL_VERTEX_ATTRIB_ARRAY_POINTER, &pointer);
        offset = (GLint)(intptr_t)pointer;
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
        int element_size = eu4_gfx_gl_type_size((uint32_t)gl_type);
        int read_error = 0;
        int unsupported_type = 0;
        if (element_size == 0) {
            /* Packed or unknown representations cannot be sized safely. */
            unsupported_type = 1;
            read_error = 1;
            capture_error = 1;
            range_valid = 0;
            element_size = 1;
        }
        if (stride <= 0) {
            stride = components * element_size;
        }
        int32_t vertex_first = 0;
        int32_t vertex_last = 0;
        if (eu4_gfx_capture_vertex_range(divisor, indexed, instance_count,
                                         first, count, index_first, index_last,
                                         &vertex_first, &vertex_last) != 0) {
            range_valid = 0;
            capture_error = 1;
            g_capture_failure_reason = "vertex_range";
            vertex_first = 0;
            vertex_last = 0;
        }
        GLint buffer_size = 0;
        if (buffer != 0) {
            glBindBuffer(GL_ARRAY_BUFFER, (GLuint)buffer);
            glGetBufferParameteriv(GL_ARRAY_BUFFER, GL_BUFFER_SIZE, &buffer_size);
        }
        uint64_t attribute_width =
            (uint64_t)(components > 0 ? components : 0) *
            (uint64_t)element_size;
        uint64_t begin64 = 0;
        uint64_t end64 = 0;
        int range_truncated = 0;
        int range_ok = 0;
        if (!unsupported_type && attribute_width > 0 && stride > 0 &&
            (uint64_t)offset <= (uint64_t)INT64_MAX) {
            uint64_t offset64 = (uint64_t)(int64_t)offset;
            uint64_t stride64 = (uint64_t)stride;
            if (vertex_first >= 0 &&
                (uint64_t)vertex_first <=
                    (UINT64_MAX - offset64) / stride64) {
                begin64 = offset64 + (uint64_t)vertex_first * stride64;
                range_ok = eu4_gfx_attribute_end(
                    buffer_size > 0 ? (uint64_t)buffer_size : 0,
                    offset64, stride64, (uint64_t)vertex_last,
                    attribute_width, &end64);
                if (range_ok && begin64 <= end64 &&
                    end64 <= (uint64_t)buffer_size) {
                    range_ok = 1;
                } else {
                    range_ok = 0;
                }
            }
        }
        if (!range_ok) {
            range_truncated = buffer_size > 0 && !unsupported_type;
            range_valid = 0;
            capture_error = 1;
            if (buffer_size > 0 && begin64 > (uint64_t)buffer_size) {
                begin64 = (uint64_t)buffer_size;
            }
            end64 = begin64;
        }
        size_t begin = (size_t)begin64;
        size_t end = (size_t)end64;
        size_t bytes = end > begin ? end - begin : 0;
        char blob_name[FTRACE_NAME] = "";
        char blob_sha[65] = "";
        if (bytes > 0 && buffer != 0) {
            uint8_t *data = malloc(bytes);
            if (data == NULL) {
                read_error = 1;
                range_valid = 0;
                capture_error = 1;
            } else {
                glGetBufferSubData(GL_ARRAY_BUFFER, (GLintptr)begin,
                                   (GLsizeiptr)bytes, data);
                if (put_blob(data, bytes, "vb", blob_name, blob_sha) != 0) {
                    read_error = 1;
                    blob_name[0] = 0;
                    range_valid = 0;
                    capture_error = 1;
                }
                free(data);
            }
        } else if (bytes > 0) {
            read_error = 1;
            range_valid = 0;
            capture_error = 1;
        }
        jprintf(out,
                "%s{\"index\":%d,\"name\":\"%s\",\"location\":%d,"
                "\"enabled\":true,"
                "\"components\":%d,\"gl_type\":\"0x%x\",\"normalized\":%s,"
                "\"stride\":%d,\"offset\":%d,\"divisor\":%d,"
                "\"offset_source\":\"%s\","
                "\"buffer\":%d,\"buffer_bytes\":%d,\"range_begin\":%zu,"
                "\"range_bytes\":%zu,\"first_vertex\":%d,\"last_vertex\":%d,"
                "\"range_truncated\":%s,"
                "\"data\":\"%s\",\"sha256\":\"%s\"%s}",
                emitted > 0 ? "," : "", location, name, location, components,
                gl_type, bool_text(normalized != 0), stride, offset, divisor,
                offset_source, buffer, buffer_size, begin, bytes, vertex_first,
                vertex_last, bool_text(range_truncated), blob_name, blob_sha,
                read_error || index_read_error
                    ? (unsupported_type
                           ? ",\"read_error\":true,\"unsupported_type\":true"
                           : ",\"read_error\":true")
                    : "");
        emitted++;
    }
    jprintf(out, "]");
    if (indexed) {
        char blob_name[FTRACE_NAME] = "";
        char blob_sha[65] = "";
        if (index_data != NULL && index_bytes > 0) {
            if (put_blob(index_data, index_bytes, "ib", blob_name, blob_sha) != 0) {
                blob_name[0] = 0;
                index_read_error = 1;
                range_valid = 0;
                capture_error = 1;
            }
        }
        jprintf(out,
                ",\"indices\":{\"buffer\":%u,\"bytes\":%zu,\"first_index\":0,"
                "\"count\":%d,\"instance_count\":%d,\"base_vertex\":%d,"
                "\"vertex_origin\":%d,\"range_valid\":%s,\"read_error\":%s,"
                "\"file\":\"%s\",\"sha256\":\"%s\"}",
                index_buffer, index_bytes, count, instance_count, base_vertex,
                range_valid ? index_first : 0, bool_text(range_valid),
                bool_text(index_read_error), blob_name, blob_sha);
    }
    glBindBuffer(GL_ARRAY_BUFFER, (GLuint)previous_array);
    glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, (GLuint)previous_element);
    free(index_data);
    (void)element_bytes;
    if ((capture_error || !range_valid) && g_capture_failure_reason == NULL) {
        g_capture_failure_reason = "attribute_range";
    }
    return capture_error || !range_valid ? 1 : 0;
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
    if (capture_attributes((GLuint)program, indexed, first, count,
                           instance_count, base_vertex, master, 0, &attr) != 0) {
        attr.failed = 1;
    }
    if (attr.failed) {
        out->failed = 1;
    }
}

/* ------------------------------------------------------------------------- */
/* Frame manifest                                                             */
/* ------------------------------------------------------------------------- */

static int file_sha256(const char *path, char out[65]) {
    out[0] = 0;
    FILE *file = fopen(path, "rb");
    if (file == NULL) {
        return -1;
    }
    CC_SHA256_CTX context;
    CC_SHA256_Init(&context);
    uint8_t buffer[64 * 1024];
    size_t read_bytes = 0;
    while ((read_bytes = fread(buffer, 1, sizeof(buffer), file)) > 0) {
        CC_SHA256_Update(&context, buffer, (CC_LONG)read_bytes);
    }
    int failed = ferror(file) != 0;
    fclose(file);
    if (failed) {
        return -1;
    }
    unsigned char digest[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256_Final(digest, &context);
    static const char hex[] = "0123456789abcdef";
    for (int i = 0; i < CC_SHA256_DIGEST_LENGTH; i++) {
        out[i * 2] = hex[digest[i] >> 4];
        out[i * 2 + 1] = hex[digest[i] & 0xf];
    }
    out[64] = 0;
    return 0;
}

static int write_frame_manifest(int complete, const char *reason) {
    char path[FTRACE_PATH];
    snprintf(path, sizeof(path), "%s/frame-trace.json.partial", g.dir);
    FILE *file = fopen(path, "w");
    if (file == NULL) {
        return -1;
    }
    const char *vendor = g.gl_vendor[0] ? g.gl_vendor : "";
    const char *renderer = g.gl_renderer[0] ? g.gl_renderer : "";
    const char *version = g.gl_version[0] ? g.gl_version : "";
    fputs("{\"schema\":\"gfx-frame-trace/v1\",", file);
    fprintf(file, "\"complete\":%s,", bool_text(complete));
    fprintf(file, "\"reason\":\"%s\",", reason != NULL ? reason : "");
    fprintf(file, "\"truncated\":%s,", bool_text(g.truncated));
    fprintf(file, "\"stream_validated\":%s,", bool_text(g.stream_validated));
    fprintf(file, "\"pid\":%d,", (int)getpid());
    fprintf(file, "\"present_index\":%d,", g.capturing_frame);
    fprintf(file, "\"scene_index\":%d,",
            g.boundary_scene ? g.capturing_frame : g.scene_index);
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
    fprintf(file, "\"max_frame_draws\":%u,", g.max_frame_draws);
    if (g.at_frame == UINT64_MAX) {
        fputs("\"at_frame\":null,", file);
    } else {
        fprintf(file, "\"at_frame\":%llu,",
                (unsigned long long)g.at_frame);
    }
    fprintf(file, "\"boundary\":\"%s\",",
            g.boundary_scene ? "scene" : "present");
    fprintf(file, "\"required\":%s},", bool_text(g.caller_count > 0));
    fputs("\"seed\":{", file);
    fprintf(file, "\"width\":%d,", g.seed_width);
    fprintf(file, "\"height\":%d},", g.seed_height);
    fprintf(file, "\"blobs\":%d}\n", g.blob_count);
    if (ferror(file) != 0 || fflush(file) != 0 || fsync(fileno(file)) != 0) {
        fclose(file);
        unlink(path);
        return -1;
    }
    if (fclose(file) != 0) {
        unlink(path);
        return -1;
    }
    char final_path[FTRACE_PATH];
    snprintf(final_path, sizeof(final_path), "%s/frame-trace.json", g.dir);
    return rename(path, final_path) == 0 ? 0 : -1;
}

static void capture_dimensions_only(uint32_t *out_width,
                                    uint32_t *out_height) {
    uint32_t width = 0, height = 0;
    if (eu4_gfx_gl_drawable_extent(&width, &height) != 0 || width == 0 ||
        height == 0) {
        GLint viewport[4] = {0, 0, 0, 0};
        glGetIntegerv(GL_VIEWPORT, viewport);
        width = (uint32_t)(viewport[2] > 0 ? viewport[2] : 0);
        height = (uint32_t)(viewport[3] > 0 ? viewport[3] : 0);
    }
    if (width > 8192) {
        width = 0;
    }
    if (height > 8192) {
        height = 0;
    }
    *out_width = width;
    *out_height = height;
}

static void build_capture_descriptor(eu4_frame_capture_descriptor *out,
                                     int complete, int stream_validated,
                                     int readback_attempted, int readback_ok,
                                     const char *reason) {
    memset(out, 0, sizeof(*out));
    out->struct_size = (uint32_t)sizeof(*out);
    out->abi_version = EU4_FRAME_CAPTURE_ABI_VERSION;
    out->capture_frame_id = g.capture_id;
    snprintf(out->session_id, sizeof(out->session_id), "%s", g.session_id);
    out->pid = g.process_pid;
    out->process_start_ns = g.process_start_ns;
    snprintf(out->boundary, sizeof(out->boundary), "%s",
             g.boundary_scene ? "scene" : "present");
    out->present_index = (uint32_t)g.capturing_frame;
    out->scene_index =
        (uint32_t)(g.boundary_scene ? g.capturing_frame : g.scene_index);
    out->operation_count = (uint64_t)g.event_count;
    out->draw_count = g.draw_events;
    out->clear_count = g.clear_events;
    out->copy_count = g.copy_events;
    out->event_stream_bytes = g.events_bytes;
    out->event_stream_verified = g.events_verified ? 1u : 0u;
    snprintf(out->event_stream_file, sizeof(out->event_stream_file), "%s",
             "frame-events.jsonl");
    snprintf(out->event_stream_sha256, sizeof(out->event_stream_sha256), "%s",
             g.events_sha);
    out->width = g.reference_width;
    out->height = g.reference_height;
    snprintf(out->pixel_format, sizeof(out->pixel_format), "BGRA8");
    out->complete = complete ? 1u : 0u;
    out->trace_complete = complete ? 1u : 0u;
    out->trace_integrity = stream_validated ? 1u : 0u;
    out->readback_ok = readback_ok ? 1u : 0u;
    out->reference_bytes = (uint64_t)g.reference_bytes;
    snprintf(out->reference_file, sizeof(out->reference_file), "%s",
             g.reference_file);
    snprintf(out->reference_sha256, sizeof(out->reference_sha256), "%s",
             g.reference_sha);
    snprintf(out->manifest_file, sizeof(out->manifest_file), "%s",
             "frame-trace.json");
    snprintf(out->manifest_sha256, sizeof(out->manifest_sha256), "%s",
             g.manifest_sha);
    snprintf(out->capture_dir, sizeof(out->capture_dir), "%s", g.dir);
    snprintf(out->gpu_status, sizeof(out->gpu_status), "%s",
             !readback_attempted
                 ? "readback_not_attempted"
                 : (g.test_only
                        ? "test_synthetic"
                        : (readback_ok ? "gl_readpixels_synchronous"
                                       : "readback_failed")));
    if (!complete && reason != NULL &&
        strncmp(reason, "unsupported_", 12) == 0) {
        snprintf(out->unsupported_dependencies,
                 sizeof(out->unsupported_dependencies), "%.*s",
                 (int)sizeof(out->unsupported_dependencies) - 1, reason);
    }
    if (!complete) {
        snprintf(out->failure_reason, sizeof(out->failure_reason), "%s",
                 reason != NULL ? reason : "incomplete");
    }
    out->readback_ns = g.readback_ns;
    out->finalize_ns = g.finalize_ns;
    out->seal_ns = g.seal_ns;
}

/* Publishes the one immutable seal descriptor.  The consumer is notified only
 * for a complete frame, after this file is durable. */
static int write_seal_manifest(const eu4_frame_capture_descriptor *descriptor,
                               int sealed) {
    char path[FTRACE_PATH];
    snprintf(path, sizeof(path), "%s/frame-seal.json.partial", g.dir);
    FILE *file = fopen(path, "w");
    if (file == NULL) {
        return -1;
    }
    fprintf(file, "{\"schema\":\"%s\",", EU4_FRAME_SEAL_SCHEMA);
    fprintf(file, "\"sealed\":%s,", bool_text(sealed));
    fprintf(file, "\"complete\":%s,", bool_text(sealed));
    fprintf(file, "\"struct_size\":%u,", descriptor->struct_size);
    fprintf(file, "\"abi_version\":%u,", descriptor->abi_version);
    fprintf(file, "\"capture_frame_id\":%llu,",
            (unsigned long long)descriptor->capture_frame_id);
    fprintf(file, "\"session_id\":\"%s\",", descriptor->session_id);
    fprintf(file, "\"pid\":%d,", (int)descriptor->pid);
    fprintf(file, "\"process_start_ns\":%llu,",
            (unsigned long long)descriptor->process_start_ns);
    fprintf(file, "\"boundary\":\"%s\",", descriptor->boundary);
    fprintf(file, "\"present_index\":%u,", descriptor->present_index);
    fprintf(file, "\"scene_index\":%u,", descriptor->scene_index);
    fprintf(file, "\"operation_count\":%llu,",
            (unsigned long long)descriptor->operation_count);
    fprintf(file, "\"draw_count\":%llu,",
            (unsigned long long)descriptor->draw_count);
    fprintf(file, "\"clear_count\":%llu,",
            (unsigned long long)descriptor->clear_count);
    fprintf(file, "\"copy_count\":%llu,",
            (unsigned long long)descriptor->copy_count);
    fprintf(file, "\"event_stream_file\":\"%s\",",
            descriptor->event_stream_file);
    fprintf(file, "\"event_stream_bytes\":%llu,",
            (unsigned long long)descriptor->event_stream_bytes);
    fprintf(file, "\"event_stream_sha256\":\"%s\",",
            descriptor->event_stream_sha256);
    fprintf(file, "\"event_stream_verified\":%s,",
            bool_text(descriptor->event_stream_verified));
    fprintf(file, "\"width\":%u,", descriptor->width);
    fprintf(file, "\"height\":%u,", descriptor->height);
    fprintf(file, "\"pixel_format\":\"%s\",", descriptor->pixel_format);
    fprintf(file, "\"trace_complete\":%s,",
            bool_text(descriptor->trace_complete));
    fprintf(file, "\"trace_integrity\":%s,",
            bool_text(descriptor->trace_integrity));
    fprintf(file, "\"readback_ok\":%s,", bool_text(descriptor->readback_ok));
    fprintf(file, "\"reference_file\":\"%s\",", descriptor->reference_file);
    fprintf(file, "\"reference_bytes\":%llu,",
            (unsigned long long)descriptor->reference_bytes);
    fprintf(file, "\"reference_sha256\":\"%s\",",
            descriptor->reference_sha256);
    fprintf(file, "\"manifest_file\":\"%s\",", descriptor->manifest_file);
    fprintf(file, "\"manifest_sha256\":\"%s\",", descriptor->manifest_sha256);
    fprintf(file, "\"capture_dir\":\"%s\",", descriptor->capture_dir);
    fprintf(file, "\"gpu_status\":\"%s\",", descriptor->gpu_status);
    fprintf(file, "\"unsupported_dependencies\":\"%s\",",
            descriptor->unsupported_dependencies);
    fprintf(file, "\"failure_reason\":\"%s\",", descriptor->failure_reason);
    fprintf(file, "\"readback_ns\":%llu,",
            (unsigned long long)descriptor->readback_ns);
    fprintf(file, "\"finalize_ns\":%llu,",
            (unsigned long long)descriptor->finalize_ns);
    fprintf(file, "\"seal_ns\":%llu,",
            (unsigned long long)descriptor->seal_ns);
    fprintf(file, "\"sealed_monotonic_ns\":%llu}\n",
            (unsigned long long)now_ns());
    if (ferror(file) != 0 || fflush(file) != 0 || fsync(fileno(file)) != 0) {
        fclose(file);
        unlink(path);
        return -1;
    }
    if (fclose(file) != 0) {
        unlink(path);
        return -1;
    }
    char final_path[FTRACE_PATH];
    snprintf(final_path, sizeof(final_path), "%s/frame-seal.json", g.dir);
    return rename(path, final_path) == 0 ? 0 : -1;
}


/* ------------------------------------------------------------------------- */
/* Bounded continuous capture                                                */
/* ------------------------------------------------------------------------- */

static void continuous_set_stopped(const char *reason) {
    g.continuous_stopped = 1;
    if (reason != NULL) {
        snprintf(g.continuous_reason, sizeof(g.continuous_reason), "%s",
                 reason);
    }
}

static void continuous_snapshot_carried(void) {
    g.previous_render_target_count = 0;
    for (int i = 0; i < g.carried_target_count &&
                    i < (int)(sizeof(g.previous_render_targets) /
                              sizeof(g.previous_render_targets[0])); i++) {
        snprintf(g.previous_render_targets[g.previous_render_target_count],
                 sizeof(g.previous_render_targets[0]), "%s",
                 g.carried_targets[i]);
        g.previous_render_target_count++;
    }
}

/* Merge this frame's writes into the persistent carried set.  Entries that
   were not written are deliberately preserved: a renderer resource remains
   valid across any number of frames in which it is neither written nor
   sampled.  Deletion removes an entry immediately. */
static void continuous_copy_targets(void) {
    for (int i = 0; i < g.render_target_count; i++) {
        carried_target_add(g.render_targets[i]);
    }
}

static void continuous_write_campaign(const char *status,
                                      const char *reason) {
    if (!g.continuous || g.continuous_base[0] == 0) {
        return;
    }
    char path[FTRACE_PATH];
    if (snprintf(path, sizeof(path), "%s/sequence.json",
                 g.continuous_base) <= 0) {
        return;
    }
    char *buffer = NULL;
    size_t size = 0;
    FILE *stream = open_memstream(&buffer, &size);
    if (stream == NULL) {
        return;
    }
    fprintf(stream,
            "{\"schema\":\"gfx-frame-sequence-campaign/v1\","
            "\"session_id\":\"%s\",\"pid\":%d,"
            "\"status\":\"%s\",\"reason\":\"%s\","
            "\"max_frames\":%u,\"max_pending\":%u,"
            "\"max_bytes\":%llu,\"frames_completed\":%u,"
            "\"bytes_written\":%llu,\"deduplicated_bytes\":%llu,"
            "\"frames\":[",
            g.session_id, (int)getpid(), status, reason ? reason : "",
            g.continuous_max_frames, g.continuous_max_pending,
            (unsigned long long)g.continuous_max_bytes,
            g.continuous_frames_completed,
            (unsigned long long)g.continuous_bytes_written,
            (unsigned long long)g.continuous_dedup_bytes);
    for (int i = 0; i < g.campaign_record_count; i++) {
        fprintf(stream, "%s%s", i > 0 ? "," : "", g.campaign_records[i]);
    }
    fprintf(stream, "]}\n");
    fclose(stream);
    if (buffer != NULL) {
        (void)write_atomic(path, buffer, size);
        free(buffer);
    }
}

static int continuous_write_frame_sequence(
    const eu4_frame_capture_descriptor *descriptor, const char *reason,
    int complete) {
    if (!g.continuous || descriptor == NULL) {
        return 0;
    }
    char path[FTRACE_PATH];
    if (snprintf(path, sizeof(path), "%s/frame-sequence.json", g.dir) <= 0) {
        return -1;
    }
    char previous[1200];
    if (g.previous_capture_id != 0) {
        snprintf(previous, sizeof(previous),
                 "{\"capture_frame_id\":%llu,\"dir\":\"%s\","
                 "\"manifest_sha256\":\"%s\",\"event_stream_sha256\":\"%s\"}",
                 (unsigned long long)g.previous_capture_id, g.previous_dir,
                 g.previous_manifest_sha, g.previous_events_sha);
    } else {
        snprintf(previous, sizeof(previous), "null");
    }
    char targets[4096];
    size_t used = 0;
    targets[0] = 0;
    for (int i = 0; i < g.render_target_count; i++) {
        if (used + strlen(g.render_targets[i]) + 4 >= sizeof(targets)) {
            break;
        }
        used += (size_t)snprintf(targets + used, sizeof(targets) - used,
                                 "%s\"%s\"", i > 0 ? "," : "",
                                 g.render_targets[i]);
    }
    char carried[4096];
    used = 0;
    carried[0] = 0;
    for (int i = 0; i < g.previous_render_target_count; i++) {
        if (used + strlen(g.previous_render_targets[i]) + 4 >=
            sizeof(carried)) {
            break;
        }
        used += (size_t)snprintf(carried + used, sizeof(carried) - used,
                                 "%s\"%s\"", i > 0 ? "," : "",
                                 g.previous_render_targets[i]);
    }
    char lifecycle[4096];
    used = 0;
    lifecycle[0] = 0;
    for (int i = 0; i < g.pending_delete_count;) {
        int event_index = g.pending_delete_events[i];
        used += (size_t)snprintf(
            lifecycle + used, sizeof(lifecycle) - used,
            "%s{\"kind\":\"delete\",\"event\":%d,\"names\":[",
            lifecycle[0] != 0 ? "," : "", event_index);
        int first = 1;
        int j = i;
        while (j < g.pending_delete_count &&
               g.pending_delete_events[j] == event_index) {
            used += (size_t)snprintf(
                lifecycle + used, sizeof(lifecycle) - used, "%s\"%s\"",
                first ? "" : ",", g.pending_deletes[j]);
            first = 0;
            j++;
        }
        used += (size_t)snprintf(lifecycle + used,
                                 sizeof(lifecycle) - used, "]}");
        i = j;
    }
    for (int i = 0; i < g.pending_mutation_count; i++) {
        if (used + 160 >= sizeof(lifecycle)) {
            break;
        }
        used += (size_t)snprintf(
            lifecycle + used, sizeof(lifecycle) - used,
            "%s{\"kind\":\"mutation\",\"operation\":\"%s\","
            "\"event\":%d,\"level\":%d,\"names\":[\"%s\"]}",
            lifecycle[0] != 0 ? "," : "",
            g.pending_mutation_ops[i], g.pending_mutation_events[i],
            g.pending_mutation_levels[i], g.pending_mutations[i]);
    }
    char *buffer = NULL;
    size_t size = 0;
    FILE *stream = open_memstream(&buffer, &size);
    if (stream == NULL) {
        return -1;
    }
    fprintf(stream,
            "{\"schema\":\"gfx-frame-sequence/v1\","
            "\"session_id\":\"%s\",\"pid\":%d,"
            "\"sequence_index\":%u,\"campaign_frames\":%u,"
            "\"capture_frame_id\":%llu,\"complete\":%s,"
            "\"boundary\":\"%s\",\"reason\":\"%s\","
            "\"previous\":%s,"
            "\"render_targets\":[%s],"
            "\"carried_renderer_outputs\":[%s],"
            "\"lifecycle\":[%s],"
            "\"reference_sha256\":\"%s\","
            "\"manifest_sha256\":\"%s\","
            "\"event_stream_sha256\":\"%s\","
            "\"operations\":%llu,\"draws\":%llu,\"clears\":%llu,"
            "\"copies\":%llu}\n",
            g.session_id, (int)getpid(), g.continuous_frames_completed + 1,
            g.continuous_max_frames,
            (unsigned long long)descriptor->capture_frame_id,
            complete ? "true" : "false", descriptor->boundary,
            reason ? reason : "", previous, targets, carried, lifecycle,
            descriptor->reference_sha256, descriptor->manifest_sha256,
            descriptor->event_stream_sha256,
            (unsigned long long)descriptor->operation_count,
            (unsigned long long)descriptor->draw_count,
            (unsigned long long)descriptor->clear_count,
            (unsigned long long)descriptor->copy_count);
    fclose(stream);
    int result = -1;
    if (buffer != NULL) {
        result = write_atomic(path, buffer, size);
        free(buffer);
    }
    if (result == 0) {
        g.pending_delete_count = 0;
        g.pending_mutation_count = 0;
    }
    return result;
}

static void continuous_record_frame(
    const eu4_frame_capture_descriptor *descriptor) {
    if (!g.continuous || descriptor == NULL) {
        return;
    }
    if (g.campaign_record_count <
        (int)(sizeof(g.campaign_records) / sizeof(g.campaign_records[0]))) {
        snprintf(g.campaign_records[g.campaign_record_count],
                 sizeof(g.campaign_records[0]),
                 "{\"sequence\":%u,\"capture_frame_id\":%llu,"
                 "\"dir\":\"%s\",\"manifest_sha256\":\"%s\","
                 "\"event_stream_sha256\":\"%s\","
                 "\"reference_sha256\":\"%s\",\"operations\":%llu,"
                 "\"draws\":%llu,\"clears\":%llu,\"copies\":%llu}",
                 g.continuous_frames_completed + 1,
                 (unsigned long long)descriptor->capture_frame_id, g.dir,
                 descriptor->manifest_sha256,
                 descriptor->event_stream_sha256,
                 descriptor->reference_sha256,
                 (unsigned long long)descriptor->operation_count,
                 (unsigned long long)descriptor->draw_count,
                 (unsigned long long)descriptor->clear_count,
                 (unsigned long long)descriptor->copy_count);
        g.campaign_record_count++;
    }
}

static void publish_sealed_descriptor(
    const eu4_frame_capture_descriptor *descriptor) {
    pthread_mutex_lock(&g_seal_lock);
    g_seal_pending_descriptor = *descriptor;
    g_seal_pending = 1;
    atomic_store_explicit(&g_seal_pending_flag, 1, memory_order_release);
    g_seal_last_written = g.seal_written;
    pthread_mutex_unlock(&g_seal_lock);
}

/* ------------------------------------------------------------------------- */
/* Event pumps                                                                */
/* ------------------------------------------------------------------------- */

static void finish_capture_ex(const char *reason, int allow_reference) {
    if (!g.armed || g.finishing) {
        return;
    }
    g.finishing = 1;
    uint64_t readback_started = now_ns();
    int have_reference = 0;
    if (allow_reference) {
        if (g.test_only) {
            if (!g.test_fail_reference) {
                /* Offline writer host: exercise the real finalize/validation
                   path without an OpenGL context. */
                uint8_t pixels[4 * 4 * 4];
                for (size_t i = 0; i < sizeof(pixels); i++) {
                    pixels[i] = (uint8_t)(i * 37u);
                }
                have_reference = write_reference_pixels(pixels, 4, 4) == 0;
            }
        } else {
            /* One passive readback implementation for both the probe and the
               final reference; it never drains the engine's GL error queue. */
            have_reference = capture_reference() == 0;
        }
    }
    g.readback_ns = now_ns() - readback_started;
    int readback_attempted = allow_reference;
    if (!readback_attempted) {
        /* A timeout still reports the original render extent, but explicitly
           does not claim a reference image. */
        capture_dimensions_only(&g.reference_width, &g.reference_height);
    }
    /* The manifest is published only after the event stream has been closed
       and validated.  A write/flush failure, malformed record, missing record
       or count mismatch therefore cannot produce complete=true. */
    uint64_t finalize_started = now_ns();
    int stream_validated = finalize_event_stream();
    if (stream_validated &&
        strcmp(g.partial_path, g.final_path) != 0) {
        if (rename(g.partial_path, g.final_path) != 0) {
            fail_capture_locked("event_rename_failed");
            stream_validated = 0;
        }
    }
    g.events_verified = 0;
    if (stream_validated) {
        char events_path[FTRACE_PATH];
        snprintf(events_path, sizeof(events_path), "%s/frame-events.jsonl",
                 g.dir);
        if (g.test_only && g.test_fail_event_digest) {
            unlink(events_path);
        }
        struct stat events_stat;
        if (stat(events_path, &events_stat) == 0 && events_stat.st_size > 0 &&
            file_sha256(events_path, g.events_sha) == 0) {
            g.events_bytes = (uint64_t)events_stat.st_size;
            g.events_verified = 1;
        } else {
            fail_capture_locked("event_stream_digest");
        }
    }
    stream_validated = stream_validated && g.events_verified;
    g.stream_validated = stream_validated;
    const char *manifest_reason;
    if (g.capture_failed) {
        manifest_reason = g_capture_failure_reason != NULL
                              ? g_capture_failure_reason
                              : "capture_failed";
    } else if (g.truncated) {
        manifest_reason = "draw_cap";
    } else if (!have_reference) {
        manifest_reason = "reference_read";
    } else if (!stream_validated) {
        manifest_reason = "event_stream_invalid";
    } else {
        manifest_reason = reason;
    }
    int complete = have_reference && !g.truncated && !g.capture_failed &&
                   stream_validated;
    int manifest_ok = write_frame_manifest(complete, manifest_reason) == 0;
    if (manifest_ok) {
        char manifest_path[FTRACE_PATH];
        snprintf(manifest_path, sizeof(manifest_path), "%s/frame-trace.json",
                 g.dir);
        if (g.test_only && g.test_fail_manifest_hash) {
            unlink(manifest_path);
        }
        if (file_sha256(manifest_path, g.manifest_sha) != 0) {
            manifest_ok = 0;
            g.manifest_sha[0] = 0;
            /* Do not leave an on-disk manifest that claims completion when the
               sealed digest could not be produced. */
            (void)write_frame_manifest(0, "manifest_hash_failed");
        }
    }
    complete = complete && manifest_ok;
    if (!manifest_ok) {
        manifest_reason = g.manifest_sha[0] ? "manifest_write_failed"
                                            : "manifest_hash_failed";
    }
    g.finalize_ns = now_ns() - finalize_started;

    eu4_frame_capture_descriptor descriptor;
    build_capture_descriptor(&descriptor, complete, stream_validated,
                             readback_attempted, have_reference,
                             manifest_reason);
    g.seal_written = 0;
    if (g.seal_enabled) {
        /* Includes readback, finalize and seal preparation.  The file is
         * written after this measurement; the callback descriptor therefore
         * carries the cost that was actually observable before publication. */
        descriptor.seal_ns = now_ns() - readback_started;
        g.seal_ns = descriptor.seal_ns;
        if (write_seal_manifest(&descriptor, complete) == 0) {
            g.seal_written = 1;
        } else {
            /* An unwritable seal cannot notify a consumer. */
            complete = 0;
            descriptor.complete = 0;
            descriptor.failure_reason[0] = 0;
            snprintf(descriptor.failure_reason,
                     sizeof(descriptor.failure_reason), "%s",
                     "seal_write_failed");
            g.seal_written = 0;
        }
    }
    g.armed = 0;
    g.captured = 1;
    g.finishing = 0;
    if (complete && g.continuous) {
        if (continuous_write_frame_sequence(&descriptor, manifest_reason, 1) !=
            0) {
            continuous_set_stopped("sequence_write");
            continuous_write_campaign("incomplete", "sequence_write");
            complete = 0;
            descriptor.complete = 0;
            snprintf(descriptor.failure_reason,
                     sizeof(descriptor.failure_reason), "%s",
                     "sequence_write_failed");
            g.seal_written = 0;
        } else {
            continuous_record_frame(&descriptor);
            g.continuous_frames_completed++;
            g.continuous_bytes_written += g.reference_bytes + g.events_bytes +
                                           g.frame_blob_bytes;
            snprintf(g.previous_dir, sizeof(g.previous_dir), "%s", g.dir);
            snprintf(g.previous_manifest_sha,
                     sizeof(g.previous_manifest_sha), "%s",
                     g.manifest_sha);
            snprintf(g.previous_events_sha, sizeof(g.previous_events_sha),
                     "%s", g.events_sha);
            g.previous_capture_id = g.capture_id;
            continuous_copy_targets();
            if (g.continuous_frames_completed >= g.continuous_max_frames) {
                continuous_set_stopped("frame_limit");
                continuous_write_campaign("complete", "frame_limit");
            } else {
                continuous_write_campaign("running", "");
            }
        }
    }
    if (complete && g.seal_enabled && g.seal_written) {
        publish_sealed_descriptor(&descriptor);
    } else {
        pthread_mutex_lock(&g_seal_lock);
        g_seal_last_written = g.seal_written;
        pthread_mutex_unlock(&g_seal_lock);
    }
}

static void finish_capture(const char *reason) {
    finish_capture_ex(reason, 1);
}

/* A bounded capture that never reaches its boundary is finalized with an
 * explicit incomplete result; no reference pixels are invented. */
static int capture_timed_out_locked(void) {
    if (!g.armed || g.timeout_ms == 0) {
        return 0;
    }
    if (now_ms() - g.armed_ms < g.timeout_ms) {
        return 0;
    }
    fail_capture_locked("capture_timeout");
    finish_capture_ex("capture_timeout", 0);
    return 1;
}

static void open_events(void) {
    g.events = fopen(g.partial_path, "w");
    if (g.events == NULL) {
        g.armed = 0;
    }
}

static void start_capture_at_frame(int next_frame) {
    if (g.continuous) {
        if (g.continuous_stopped) {
            return;
        }
        if (g.continuous_frames_completed >= g.continuous_max_frames) {
            continuous_set_stopped("frame_limit");
            continuous_write_campaign("complete", "frame_limit");
            return;
        }
        if (g.continuous_max_bytes != 0 &&
            g.continuous_bytes_written >= g.continuous_max_bytes) {
            continuous_set_stopped("byte_budget");
            continuous_write_campaign("incomplete", "byte_budget");
            return;
        }
        snprintf(g.dir, sizeof(g.dir), "%s/frame-%06u", g.continuous_base,
                 g.continuous_frames_completed + 1);
        if (mkdir_p(g.dir) != 0) {
            continuous_set_stopped("frame_directory");
            continuous_write_campaign("incomplete", "frame_directory");
            return;
        }
        char frame_blobs[FTRACE_PATH];
        snprintf(frame_blobs, sizeof(frame_blobs), "%s/blobs", g.dir);
        if (mkdir_p(frame_blobs) != 0) {
            continuous_set_stopped("frame_directory");
            continuous_write_campaign("incomplete", "frame_directory");
            return;
        }
        continuous_snapshot_carried();
        snprintf(g.partial_path, sizeof(g.partial_path),
                 "%s/frame-events.jsonl.partial", g.dir);
        snprintf(g.final_path, sizeof(g.final_path),
                 "%s/frame-events.jsonl", g.dir);
        g.reference_file[0] = 0;
        g.reference_sha[0] = 0;
        g.reference_bytes = 0;
        g.reference_width = 0;
        g.reference_height = 0;
        g.frame_blob_bytes = 0;
        /* Blob names are content-addressed.  A blob written for the previous
           frame is not present in this frame's blobs/ directory, so the
           per-frame dedup table must start empty while the campaign byte
           counter stays cumulative. */
        blobs_reset();
    }
    frame_caches_reset();
    g.captured = 0;
    g.armed = 1;
    g.armed_ms = now_ms();
    g.capturing_frame = next_frame;
    g.capture_id = ++g.next_capture_id;
    g.event_count = 0;
    g.draw_events = 0;
    g.clear_events = 0;
    g.copy_events = 0;
    g.truncated = 0;
    g.capture_failed = 0;
    g.stream_validated = 0;
    g.seal_written = 0;
    g.manifest_sha[0] = 0;
    g.events_bytes = 0;
    g.events_sha[0] = 0;
    g.events_verified = 0;
    g.readback_ns = 0;
    g.finalize_ns = 0;
    g.seal_ns = 0;
    g_capture_failure_reason = NULL;
    g.have_seed = 0;
    g.seed_emitted = 0;
    g.seed_json[0] = 0;
    g.seed_width = 0;
    g.seed_height = 0;
    open_events();
}

static void continuous_rearm_after_frame(void) {
    if (!g.continuous || g.stream_only || g.continuous_stopped ||
        !g.captured || g.armed) {
        return;
    }
    int next = g.boundary_scene ? g.scene_index + 1 : g.present_index + 1;
    if (next > 0) {
        start_capture_at_frame(next);
    }
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
    /* Verify the intended default framebuffer/back buffer without touching the
       engine's error queue.  A mis-selected read target is a readback failure,
       never evidence. */
    GLint selected_read_framebuffer = -1, selected_draw_framebuffer = -1;
    GLint selected_read_buffer = 0;
    glGetIntegerv(GL_READ_FRAMEBUFFER_BINDING, &selected_read_framebuffer);
    glGetIntegerv(GL_DRAW_FRAMEBUFFER_BINDING, &selected_draw_framebuffer);
    glGetIntegerv(GL_READ_BUFFER, &selected_read_buffer);
    if (selected_read_framebuffer != 0 || selected_draw_framebuffer != 0 ||
        selected_read_buffer != GL_BACK) {
        glBindFramebuffer(GL_DRAW_FRAMEBUFFER, (GLuint)draw_framebuffer);
        glBindFramebuffer(GL_READ_FRAMEBUFFER, (GLuint)read_framebuffer);
        glReadBuffer((GLenum)read_buffer);
        free(pixels);
        return -1;
    }
    glPixelStorei(GL_PACK_ALIGNMENT, 1);
    glPixelStorei(GL_PACK_ROW_LENGTH, 0);
    glPixelStorei(GL_PACK_SKIP_ROWS, 0);
    glPixelStorei(GL_PACK_SKIP_PIXELS, 0);
    if (pack_buffer != 0) {
        glBindBuffer(GL_PIXEL_PACK_BUFFER, 0);
    }
    /* Deliberately do not drain the GL error queue here.  The engine owns that
       state and reading it in the observer would consume a pending renderer
       error.  OpenGL provides no non-destructive error peek; the consequence is
       that a failure of this readback cannot be distinguished from a pending
       engine error without consuming one.  The observer records the read as
       attempted and leaves the queue untouched. */
    /* glReadPixels is synchronous; do not issue glFinish, which the Apple
       driver implements as an error-queue flush. */
    glReadPixels(0, 0, (GLsizei)width, (GLsizei)height, GL_BGRA,
                 GL_UNSIGNED_INT_8_8_8_8_REV, pixels);
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
    *out_width = width;
    *out_height = height;
    *out_pixels = pixels;
    return 0;
}

/* Validates and publishes the one final reference image.  Every dimension,
   byte-count and digest check happens before the manifest can see it. */
static int write_reference_pixels(const uint8_t *pixels, uint32_t width,
                                  uint32_t height) {
    if (pixels == NULL || width == 0 || height == 0 || width > 8192 ||
        height > 8192) {
        return -1;
    }
    if ((uint64_t)width * (uint64_t)height > (uint64_t)(SIZE_MAX / 4u)) {
        return -1;
    }
    size_t bytes = (size_t)width * (size_t)height * 4u;
    if (bytes == 0 || bytes / 4u / width != height) {
        return -1;
    }
    char path[FTRACE_PATH];
    snprintf(path, sizeof(path), "%s/frame-reference.bgra", g.dir);
    if (write_atomic(path, pixels, bytes) != 0) {
        return -1;
    }
    char sha[65];
    sha256_hex(pixels, bytes, sha);
    if (strlen(sha) != 64) {
        unlink(path);
        return -1;
    }
    snprintf(g.reference_file, sizeof(g.reference_file),
             "frame-reference.bgra");
    snprintf(g.reference_sha, sizeof(g.reference_sha), "%s", sha);
    g.reference_bytes = bytes;
    g.reference_width = width;
    g.reference_height = height;
    if (g.reference_bytes != (size_t)g.reference_width * g.reference_height * 4u ||
        strlen(g.reference_sha) != 64) {
        g.reference_file[0] = 0;
        g.reference_bytes = 0;
        g.reference_width = 0;
        g.reference_height = 0;
        return -1;
    }
    return 0;
}

static int capture_reference(void) {
    uint32_t width = 0, height = 0;
    uint8_t *pixels = NULL;
    if (read_drawable_bgra(&width, &height, &pixels) != 0) {
        free(pixels);
        return -1;
    }
    int result = write_reference_pixels(pixels, width, height);
    free(pixels);
    return result;
}

/* Exported for the native state-restoration host: performs one readback using
   the same state-saving path without claiming an engine error. */
int eu4_gfx_frame_trace_readback_probe(void) {
    uint32_t width = 0, height = 0;
    uint8_t *pixels = NULL;
    int result = read_drawable_bgra(&width, &height, &pixels);
    free(pixels);
    return result;
}

/* Host-test only: exercise the production final-reference writer/validator
   without starting a capture. */
int eu4_gfx_frame_trace_test_reference(const char *directory) {
    if (directory == NULL || directory[0] != '/') {
        return -1;
    }
    snprintf(g.dir, sizeof(g.dir), "%s", directory);
    if (mkdir_p(g.dir) != 0) {
        return -1;
    }
    return capture_reference();
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
    g.scene_copy_count = 0;
    timeline_write(
        "{\"kind\":\"scene_entry\",\"seq\":%llu,\"pid\":%d,"
        "\"monotonic_ns\":%llu,\"scene\":%d,\"draws\":%u,"
        "\"copies_before\":%u,\"copies_total\":%llu,"
        "\"armed\":%d,\"captured\":%d}",
        (unsigned long long)++g.timeline_seq, (int)getpid(),
        (unsigned long long)now_ns(), g.scene_index, g.frame_draws,
        g.scene_copy_count, (unsigned long long)g.copy_count, g.armed,
        g.captured);
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
    if (g.armed && capture_timed_out_locked()) {
        /* The bounded capture ended before its normal boundary. */
    } else if (g.boundary_scene && g.armed &&
               g.capturing_frame == g.scene_index) {
        /* PresentScene entry is the frame-end boundary: the engine has issued
           every draw of the selected frame and has not yet called _GfxPresent
           (which may swap).  Read the reference here, never at exit. */
        finish_capture("scene_present");
    } else if (g.boundary_scene && g.armed &&
               g.capturing_frame < g.scene_index) {
        /* The selected frame's entry was missed, so the current back buffer may
           belong to a later frame.  Publish an explicit incomplete result
           instead of attaching the wrong pixels. */
        fail_capture_locked("scene_entry_missed");
        finish_capture_ex("scene_entry_missed", 0);
    }
    pthread_mutex_unlock(&g_lock);
    eu4_gfx_frame_sealed_dispatch();
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
    (void)capture_timed_out_locked();
    if (g.boundary_scene && !g.stream_only) {
        /* Normal finalization happens at the next PresentScene entry, before
           _GfxPresent.  Reaching this exit still armed means the entry hook was
           missed; finalizing here would read after a possible swap. */
        if (g.armed) {
            /* No next entry finalized this capture, so the exit may follow a
               swap.  Never read the back buffer here. */
            fail_capture_locked("scene_entry_missed");
            finish_capture_ex("scene_entry_missed", 0);
        }
        if (!g.captured && !g.armed) {
            int time_ready =
                g.after_ms == 0 || now_ms() - g.start_ms >= g.after_ms;
            int enough = time_ready && g.frame_draws > 0 &&
                         g.frame_draws >= g.min_draws &&
                         caller_set_matches() &&
                         (g.max_frame_draws == 0 ||
                          g.frame_draws <= g.max_frame_draws);
            int candidate = 0;
            if (enough) {
                g.matching_scenes++;
                candidate = (uint32_t)g.matching_scenes == g.occurrence;
            }
            timeline_write(
                "{\"kind\":\"scene_exit\",\"seq\":%llu,\"pid\":%d,"
                "\"monotonic_ns\":%llu,\"scene\":%d,\"draws\":%u,"
                "\"ready\":%d,\"enough\":%d,\"matching\":%d,"
                "\"selected\":%d,\"copies\":%u,\"copies_total\":%llu,"
                "\"armed\":%d,\"captured\":%d}",
                (unsigned long long)++g.timeline_seq, (int)getpid(),
                (unsigned long long)now_ns(), g.scene_index, g.frame_draws,
                time_ready, enough, g.matching_scenes, candidate,
                g.scene_copy_count, (unsigned long long)g.copy_count, g.armed,
                g.captured);
            const char *log_path = getenv("EU4_GFX_FRAME_TRACE_LOG");
            if (log_path != NULL && log_path[0] == '/' &&
                g.scene_index % 60 == 0) {
                FILE *log = fopen(log_path, "a");
                if (log != NULL) {
                    fprintf(log,
                            "{\"event\":\"exit\",\"scene\":%d,\"ms\":%llu,"
                            "\"draws\":%u,\"ready\":%d,\"enough\":%d,"
                            "\"matching\":%d,\"selected\":%d,"
                            "\"armed\":%d,\"captured\":%d}\n",
                            g.scene_index,
                            (unsigned long long)(now_ms() - g.start_ms),
                            g.frame_draws, time_ready, enough,
                            g.matching_scenes, candidate, g.armed,
                            g.captured);
                    fclose(log);
                }
            }
            if (candidate) {
                start_capture_at_frame(g.scene_index + 1);
            }
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
    continuous_rearm_after_frame();
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
    eu4_gfx_frame_sealed_dispatch();
}

void eu4_gfx_frame_trace_present_entry(eu4_gfx_capture_frame *frame,
                                       eu4_gfx_observe_call *call) {
    (void)frame;
    if (!g.enabled) {
        return;
    }
    pthread_mutex_lock(&g_lock);
    if (!capture_timed_out_locked() && g.armed && g.boundary_fb < 0 &&
        !g.boundary_scene) {
        finish_capture("present");
    }
    call->retain = 0;
    pthread_mutex_unlock(&g_lock);
    eu4_gfx_frame_sealed_dispatch();
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
    (void)capture_timed_out_locked();
    g.present_index++;
    int candidate = 0;
    if (g.boundary_scene) {
        /* The scene boundary owns frame tracking; _GfxPresent may be invoked
           inside PresentScene and must not reset the draw count. */
        pthread_mutex_unlock(&g_lock);
        eu4_gfx_frame_sealed_dispatch();
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
    continuous_rearm_after_frame();
    pthread_mutex_unlock(&g_lock);
    eu4_gfx_frame_sealed_dispatch();
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
    if (capture_timed_out_locked()) {
        pthread_mutex_unlock(&g_lock);
        eu4_gfx_frame_sealed_dispatch();
        return;
    }
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
    if (g.test_only && g.armed && g.events != NULL &&
        g.event_count < (int)g.max_draws) {
        /* The offline writer host has no GL context; keep the ordered stream
           shape without querying the driver. */
        char body[192];
        int length = snprintf(
            body, sizeof body,
            "{\"kind\":\"clear\",\"before_first_draw\":%s,\"flags\":%d,"
            "\"test\":true}",
            bool_text(g.event_count == 0), (int)frame->rsi);
        if (length > 0 && (size_t)length < sizeof body && emit_event(body)) {
            g.event_count++;
            g.clear_events++;
        }
        pthread_mutex_unlock(&g_lock);
        eu4_gfx_frame_sealed_dispatch();
        return;
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
                g.clear_events++;
            } else {
                g.capture_failed = 1;
            }
            free(buffer);
        }
    }
    pthread_mutex_unlock(&g_lock);
    eu4_gfx_frame_sealed_dispatch();
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
    if (capture_timed_out_locked()) {
        pthread_mutex_unlock(&g_lock);
        eu4_gfx_frame_sealed_dispatch();
        return;
    }
    if (g.test_only) {
        caller_add(call->caller_rva);
        g.frame_draws++;
        pthread_mutex_unlock(&g_lock);
        eu4_gfx_frame_sealed_dispatch();
        return;
    }
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
    eu4_gfx_frame_sealed_dispatch();
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
    if (capture_timed_out_locked()) {
        pthread_mutex_unlock(&g_lock);
        eu4_gfx_frame_sealed_dispatch();
        return;
    }
    if (g.test_only) {
        if (g.armed && g.events != NULL) {
            if (g.event_count >= (int)g.max_draws) {
                g.truncated = 1;
            } else {
                char body[256];
                int length = snprintf(
                    body, sizeof body,
                    "{\"kind\":\"draw\",\"index\":%d,\"test\":true,"
                    "\"draw\":{\"entry\":\"_GfxDraw\",\"caller_rva\":\"0x%llx\"}}",
                    g.event_count, (unsigned long long)call->caller_rva);
                if (length > 0 && (size_t)length < sizeof body &&
                    emit_event(body)) {
                    g.event_count++;
                    g.draw_events++;
                }
            }
        }
        pthread_mutex_unlock(&g_lock);
        eu4_gfx_frame_sealed_dispatch();
        return;
    }
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
                g.draw_events++;
            } else {
                fail_capture_locked(g_capture_failure_reason != NULL
                                        ? g_capture_failure_reason
                                        : "draw_event_capture");
            }
            free(buffer);
        }
    }
    pthread_mutex_unlock(&g_lock);
    eu4_gfx_frame_sealed_dispatch();
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
    if (write_frame_manifest(1, "test") != 0) {
        return -1;
    }
    char path[FTRACE_PATH];
    snprintf(path, sizeof(path), "%s/frame-trace.json", g.dir);
    FILE *probe = fopen(path, "r");
    if (probe == NULL) {
        return -1;
    }
    fclose(probe);
    return 0;
}
