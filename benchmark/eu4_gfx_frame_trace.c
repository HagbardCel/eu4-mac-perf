/* Complete-frame original-GL trace.
 *
 * See eu4_gfx_frame_trace.h for the contract. This module is observation-only:
 * every GL binding it must change for a readback is saved and restored, and it
 * never issues a draw, clear or state change of its own.
 */

#define GL_SILENCE_DEPRECATION 1
#define GL_GLEXT_PROTOTYPES 1

#include "eu4_gfx_frame_trace.h"
#include "eu4_gfx_uv2_sparse.h"
#include "../renderer/metal/eu4_gfx_draw_semantics.h"

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
#include <OpenGL/OpenGL.h>

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
#include <sys/sysctl.h>
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
#define FTRACE_MAX_RESOURCES 64
#define FTRACE_MAX_LIFECYCLE 64
#define FTRACE_MAX_TEX_BYTES (256u * 1024u * 1024u)
#define FTRACE_MAX_BLOB_BYTES (512u * 1024u * 1024u)
/* A bounded collection of selected pre-operation framebuffer checkpoints.
   Each individual image is limited to the maximum drawable extent the
   readback itself enforces (8192x8192 BGRA8); the whole set additionally
   shares one aggregate readback-byte budget and one deadline per capture. */
#define FTRACE_MAX_CHECKPOINTS 8
#define FTRACE_DEFAULT_MAX_CHECKPOINTS 4
#define FTRACE_MAX_CHECKPOINT_BYTES (256u * 1024u * 1024u)
#define FTRACE_DEFAULT_CHECKPOINT_TOTAL_BYTES (128ull * 1024ull * 1024ull)
#define FTRACE_CHECKPOINT_MAX_NS (10ull * 1000ull * 1000ull * 1000ull)
/* Opt-in exact-prefix coverage: one post-event observation per executed
   operation from event 0 through the selected end event, plus the initial
   pre-event-0 framebuffer state.  Every observation is a full BGRA readback
   with its own target/resource identity and an aggregate byte/time budget. */
#define FTRACE_MAX_PREFIX_EVENTS 4096
#define FTRACE_PREFIX_HARD_MAX_BYTES (768ull * 1024ull * 1024ull)
#define FTRACE_PREFIX_DEFAULT_MAX_BYTES (384ull * 1024ull * 1024ull)
#define FTRACE_PREFIX_MAX_NS (10ull * 1000ull * 1000ull * 1000ull)
#define FTRACE_PREFIX_LINE_MAX 4096
#define FTRACE_NAME 96

#define GL_TEXTURE_MAX_ANISOTROPY 0x84FE
#define GL_TEXTURE_BINDING_CUBE_MAP_VALUE 0x8514
#define GL_TEXTURE_BINDING_3D_VALUE 0x806A
#define GL_VERTEX_ATTRIB_ARRAY_DIVISOR 0x88FE
#define GL_VERTEX_ATTRIB_ARRAY_INTEGER_VALUE 0x88FD
#define GL_COMPRESSED_DXT1 0x83F0
#ifndef GL_FRAMEBUFFER_SRGB
#define GL_FRAMEBUFFER_SRGB 0x8DB9
#endif
#define GL_COMPRESSED_DXT1_ALPHA 0x83F1
#define GL_COMPRESSED_DXT3 0x83F2
#define GL_COMPRESSED_DXT5 0x83F3
#define GL_RGBA32F_VALUE 0x8814
#define GL_RGB32F_VALUE 0x8815
#define GL_R32F_VALUE 0x822E
#ifndef GL_RED
#define GL_RED 0x1903
#endif
#define GL_RGBA16F_VALUE 0x881A
#define GL_RGB16F_VALUE 0x881B
#define GL_RGBA16_VALUE 0x805B
#define GL_SRGB8_ALPHA8_VALUE 0x8C43
#define GL_DEPTH32F_STENCIL8_VALUE 0x8CAD
#define GL_DEPTH24_STENCIL8_VALUE 0x88F0
#define GL_DEPTH_COMPONENT16_VALUE 0x81A5
#define GL_DEPTH_COMPONENT24_VALUE 0x81A6
#define GL_DEPTH_COMPONENT32F_VALUE 0x8DAB
#define GL_STENCIL_INDEX8_VALUE 0x8D48
#define GL_SAMPLER_2D_RECT_VALUE 0x8B63
#define GL_SAMPLER_BINDING_VALUE 0x8919
#ifndef GL_SIGNED_NORMALIZED
#define GL_SIGNED_NORMALIZED 0x8F9D
#endif
#ifndef GL_UNSIGNED_NORMALIZED
#define GL_UNSIGNED_NORMALIZED 0x8C17
#endif
#ifndef GL_FRAMEBUFFER_ATTACHMENT_DEPTH_SIZE
#define GL_FRAMEBUFFER_ATTACHMENT_DEPTH_SIZE 0x8216
#endif
#ifndef GL_FRAMEBUFFER_ATTACHMENT_STENCIL_SIZE
#define GL_FRAMEBUFFER_ATTACHMENT_STENCIL_SIZE 0x8217
#endif
#ifndef GL_FRAMEBUFFER_ATTACHMENT_TEXTURE_LAYER
#define GL_FRAMEBUFFER_ATTACHMENT_TEXTURE_LAYER 0x8CD4
#endif
#ifndef GL_FRAMEBUFFER_ATTACHMENT_TEXTURE_CUBE_MAP_FACE
#define GL_FRAMEBUFFER_ATTACHMENT_TEXTURE_CUBE_MAP_FACE 0x8CD3
#endif

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

    /* Bounded opt-in default-framebuffer checkpoints.  Each requested event is
       captured immediately before that operation executes.  An empty set
       disables the feature entirely and performs no additional GPU readback. */
    int checkpoint_events[FTRACE_MAX_CHECKPOINTS];
    int checkpoint_count;
    int checkpoint_max_count;
    uint64_t checkpoint_max_bytes;
    int checkpoint_captured[FTRACE_MAX_CHECKPOINTS];
    uint64_t checkpoint_ns[FTRACE_MAX_CHECKPOINTS];
    size_t checkpoint_bytes[FTRACE_MAX_CHECKPOINTS];
    uint64_t checkpoint_total_bytes;
    char checkpoint_file[FTRACE_MAX_CHECKPOINTS][FTRACE_NAME];
    char checkpoint_depth_file[FTRACE_MAX_CHECKPOINTS][FTRACE_NAME];
    char checkpoint_stencil_file[FTRACE_MAX_CHECKPOINTS][FTRACE_NAME];
    size_t checkpoint_depth_bytes[FTRACE_MAX_CHECKPOINTS];
    size_t checkpoint_stencil_bytes[FTRACE_MAX_CHECKPOINTS];
    char checkpoint_json[FTRACE_MAX_CHECKPOINTS][3072];
    /* Slot captured at the current event and not yet attached to its record.
       -1 means nothing is pending. */
    int checkpoint_pending;

    char gl_vendor[256];
    char gl_renderer[256];
    char gl_version[256];
    int last_capture_target_fb;
    int test_target_fb_override;
    int test_target_internal_override;
    int gl_drawable_valid;
    int gl_drawable_binding;
    int gl_samples;
    int gl_sample_buffers;
    int gl_depth_bits;
    int gl_stencil_bits;
    int gl_red_bits;
    int gl_green_bits;
    int gl_blue_bits;
    int gl_alpha_bits;
    int gl_double_buffer;
    int gl_stereo;
    int gl_framebuffer_srgb;
    int gl_dither;
    int gl_multisample;
    int gl_draw_buffer;
    int gl_read_buffer;
    char pre_image_json[2048];
    uint64_t pre_image_call_id;
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
    char render_targets[FTRACE_MAX_RESOURCES][64];
    int render_target_count;
    uint64_t texture_cache_reads;
    uint64_t texture_cache_hits;
    uint64_t texture_cache_invalidations;
    int capacity_exceeded;
    char capacity_limit[32];
    char capacity_reason[64];

    /* Opt-in exact-prefix coverage.  Disabled by default; an empty set
       performs no additional GPU readback and writes no prefix file. */
    int prefix_enabled;
    int prefix_end;
    int prefix_sparse;
    int prefix_event_count;
    uint64_t prefix_caller;
    int prefix_events[FTRACE_MAX_PREFIX_EVENTS];
    int prefix_depth_stencil;
    int prefix_count;
    int prefix_initial;
    int prefix_next_event;
    uint64_t prefix_bytes;
    uint64_t prefix_max_bytes;
    uint64_t prefix_readback_ns;
    uint64_t prefix_max_ns;
    int prefix_failed;
    char prefix_file[FTRACE_NAME];
    char prefix_sha[65];
    uint64_t prefix_file_bytes;
    FILE *prefix_observations;
    int prefix_copy_pending_event;
    int prefix_copy_pending_texture;
    int prefix_clear_pending_event;
    int prefix_clear_flags;

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
    char previous_render_targets[FTRACE_MAX_RESOURCES][64];
    int previous_render_target_count;
    /* Renderer-generated resources that remain valid across frame boundaries.
       Unlike render_targets (this frame's writes) this set is only mutated by
       a write or an evidenced lifecycle transition (delete/reset). */
    char carried_targets[FTRACE_MAX_RESOURCES][64];
    int carried_target_count;
    char campaign_records[FTRACE_MAX_RESOURCES][768];
    int campaign_record_count;
    char pending_deletes[FTRACE_MAX_LIFECYCLE][64];
    int pending_delete_events[FTRACE_MAX_LIFECYCLE];
    unsigned long long pending_delete_sequences[FTRACE_MAX_LIFECYCLE];
    int pending_delete_count;
    char pending_mutations[FTRACE_MAX_LIFECYCLE][64];
    char pending_mutation_ops[FTRACE_MAX_LIFECYCLE][32];
    int pending_mutation_events[FTRACE_MAX_LIFECYCLE];
    unsigned long long pending_mutation_sequences[FTRACE_MAX_LIFECYCLE];
    int pending_mutation_levels[FTRACE_MAX_LIFECYCLE];
    unsigned pending_mutation_targets[FTRACE_MAX_LIFECYCLE];
    int pending_mutation_count;
    /* Monotonic intra-frame order of lifecycle transitions.  Several distinct
       GL calls can share one captured event index; the sequence keeps them
       distinguishable and ordered. */
    unsigned long long lifecycle_sequence;
} g;

/* Opt-in event-1 uv2 diagnostic probe.  Disabled unless EU4_GFX_UV2_PROBE=1;
 * the expected draw identity is the authenticated event-1 draw from the
 * sealed reference (program 310, framebuffer 6, colour texture 433).  The
 * probe never feeds the sealed reference and never fails the capture. */
typedef struct {
    int enabled;
    int event;
    int program;
    int framebuffer;
    int texture;
    int require_identity;
    char dir[FTRACE_PATH];
    char varying[64];
} uv2_probe_config;

static uv2_probe_config g_uv2_probe = {
    0, 1, 310, 6, 433, 1, "", "v_TEXCOORD1"};
static int g_uv2_probe_done;
static eu4_uv2_selection g_uv2_pixels;
static char g_uv2_sparse_sha[65];
static void uv2_probe_write_failure(const char *status, const char *reason);

/* The probe executes at the draw's exit hook, where the deferred uniform and
 * attribute uploads inside _GfxDrawIndexed have been applied; the engine's
 * pre-draw colour and depth must be captured at entry, before the draw writes
 * them.  This one-shot record carries that state across the call. */
typedef struct {
    int attempted;
    int ready;
    int event;
    int indexed;
    unsigned mode;
    int count;
    int first;
    int instances;
    int base_vertex;
    uintptr_t master;
    uint64_t caller_rva;
    int program;
    int framebuffer;
    int texture;
    int width;
    int height;
    int depth_bits;
    uint8_t *color;
    uint8_t *depth;
} uv2_probe_pending;

static uv2_probe_pending g_uv2_probe_pending;
static int g_uv2_probe_require_pre;

static void uv2_probe_clear_pending(void) {
    free(g_uv2_probe_pending.color);
    free(g_uv2_probe_pending.depth);
    memset(&g_uv2_probe_pending, 0, sizeof(g_uv2_probe_pending));
}

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
    unsigned target;
    unsigned name;
    char *levels_json;
    struct texture_cache_entry *next;
} texture_cache_entry;

static void fail_capture_locked(const char *reason);

/* A bounded collection that would silently omit a resource or lifecycle
   transition is a hard capture failure.  The affected frame is published as
   incomplete with an explicit resource_limit reason and can never qualify a
   renderer-equivalence result. */
static void capacity_exceeded(const char *what) {
    if (!g.capacity_exceeded) {
        g.capacity_exceeded = 1;
        snprintf(g.capacity_limit, sizeof(g.capacity_limit), "%s", what);
    }
    fail_capture_locked("resource_limit");
}

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
    if (g.render_target_count >= FTRACE_MAX_RESOURCES) {
        snprintf(g.capacity_reason, sizeof(g.capacity_reason),
                 "render_targets(%d)", g.render_target_count);
        capacity_exceeded(g.capacity_reason);
        return;
    }
    snprintf(g.render_targets[g.render_target_count], 64, "%s", name);
    g.render_target_count++;
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
    if (g.carried_target_count >= FTRACE_MAX_RESOURCES) {
        snprintf(g.capacity_reason, sizeof(g.capacity_reason),
                 "carried_targets(%d)", g.carried_target_count);
        capacity_exceeded(g.capacity_reason);
        return;
    }
    snprintf(g.carried_targets[g.carried_target_count], 64, "%s", name);
    g.carried_target_count++;
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

/* The cache is keyed by the actual texture identity (target and name), not by
   a formatted string.  A mutation or deletion invalidates exactly that entry,
   so an unchanged resource keeps its single readback while a changed version
   is captured again. */
static const char *texture_cache_get(GLenum target, unsigned name) {
    g.texture_cache_reads++;
    for (texture_cache_entry *entry = g.texture_cache; entry != NULL;
         entry = entry->next) {
        if (entry->target == (unsigned)target && entry->name == name) {
            g.texture_cache_hits++;
            return entry->levels_json;
        }
    }
    return NULL;
}

static void texture_cache_put(GLenum target, unsigned name,
                              const char *levels) {
    texture_cache_entry *entry = calloc(1, sizeof(*entry));
    if (entry == NULL) {
        return;
    }
    entry->target = (unsigned)target;
    entry->name = name;
    entry->levels_json = strdup(levels);
    entry->next = g.texture_cache;
    g.texture_cache = entry;
}

static void texture_cache_invalidate(GLenum target, unsigned name) {
    for (texture_cache_entry **link = &g.texture_cache; *link != NULL;) {
        texture_cache_entry *entry = *link;
        if (entry->target == (unsigned)target && entry->name == name) {
            *link = entry->next;
            free(entry->levels_json);
            free(entry);
            g.texture_cache_invalidations++;
            continue;
        }
        link = &entry->next;
    }
}

static void texture_cache_invalidate_name(unsigned name) {
    for (texture_cache_entry **link = &g.texture_cache; *link != NULL;) {
        texture_cache_entry *entry = *link;
        if (entry->name == name) {
            *link = entry->next;
            free(entry->levels_json);
            free(entry);
            g.texture_cache_invalidations++;
            continue;
        }
        link = &entry->next;
    }
}

static pthread_mutex_t g_lock = PTHREAD_MUTEX_INITIALIZER;

/* A cube face is the same texture object as the cube map.  The cache and the
   lifecycle identity must use the target that sampling sees. */
static unsigned texture_identity_target(unsigned target) {
    if (target >= 0x8515u && target <= 0x851Au) { /* GL_TEXTURE_CUBE_MAP_* */
        return 0x8513u; /* GL_TEXTURE_CUBE_MAP */
    }
    return target != 0 ? target : 0x0DE1u; /* GL_TEXTURE_2D */
}

void eu4_gfx_frame_trace_texture_deleted(int count, const unsigned *names) {
    if (count <= 0 || names == NULL) {
        return;
    }
    pthread_mutex_lock(&g_lock);
    unsigned long long sequence = ++g.lifecycle_sequence;
    for (int i = 0; i < count; i++) {
        char name[64];
        snprintf(name, sizeof(name), "%u", names[i]);
        render_target_remove(name);
        /* The frame-start snapshot must keep a name that is deleted later in
           the same frame: the lifecycle record carries the delete event, so a
           sample before that event may still use the carried image.  Only the
           persistent set is removed immediately (for the next frame). */
        carried_target_remove(name);
        texture_cache_invalidate_name(names[i]);
        /* A numeric GL name can be deleted, recreated and deleted again in one
           frame.  Those are distinct transitions and each deletion must keep
           its own event index; only the duplicate names inside one
           glDeleteTextures call are the same transition. */
        int duplicate_in_call = 0;
        for (int j = 0; j < i; j++) {
            if (names[j] == names[i]) {
                duplicate_in_call = 1;
                break;
            }
        }
        if (duplicate_in_call) {
            continue;
        }
        if (g.pending_delete_count >= FTRACE_MAX_LIFECYCLE) {
            capacity_exceeded("delete_records");
            continue;
        }
        snprintf(g.pending_deletes[g.pending_delete_count], 64, "%s", name);
        g.pending_delete_events[g.pending_delete_count] =
            g.armed ? g.event_count : -1;
        g.pending_delete_sequences[g.pending_delete_count] = sequence;
        g.pending_delete_count++;
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

/* A name deleted earlier in this frame can be reallocated by a later
   glTexImage2D.  That recreation is part of the lifecycle timeline even though
   the name is no longer a live render target when the write is observed. */
static int texture_deleted_this_frame(const char *name) {
    for (int i = 0; i < g.pending_delete_count; i++) {
        if (strcmp(g.pending_deletes[i], name) == 0) {
            return 1;
        }
    }
    return 0;
}

void eu4_gfx_frame_trace_texture_mutated(unsigned name, int level,
                                         int sub_image, unsigned target_enum) {
    if (name == 0) {
        return;
    }
    unsigned target = texture_identity_target(target_enum);
    pthread_mutex_lock(&g_lock);
    char text[64];
    snprintf(text, sizeof(text), "%u", name);
    /* Any observed CPU-originated write invalidates the cached contents of
       exactly this (target, name), including external textures that were
       already sampled earlier in the frame.  Unchanged textures keep their
       cached readback. */
    texture_cache_invalidate((GLenum)target, name);
    /* Only renderer-owned resources have persistent Metal history.  An
       external CPU-supplied texture is captured at each sample, so its new
       contents are authoritative without a lifecycle record. */
    int relevant = render_target_known(text) || carried_target_known(text) ||
                   texture_deleted_this_frame(text);
    if (relevant) {
        if (g.pending_mutation_count >= FTRACE_MAX_LIFECYCLE) {
            capacity_exceeded("mutation_records");
        } else {
            snprintf(g.pending_mutations[g.pending_mutation_count], 64, "%s",
                     text);
            /* Only glTexImage2D and glTexSubImage2D are interposed; a
               sub_image == 0 observation is always a reallocation, including
               a cube face. */
            snprintf(g.pending_mutation_ops[g.pending_mutation_count], 32,
                     "%s", sub_image ? "glTexSubImage2D" : "glTexImage2D");
            g.pending_mutation_events[g.pending_mutation_count] =
                g.armed ? g.event_count : -1;
            g.pending_mutation_sequences[g.pending_mutation_count] =
                ++g.lifecycle_sequence;
            g.pending_mutation_levels[g.pending_mutation_count] = level;
            g.pending_mutation_targets[g.pending_mutation_count] = target;
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
/* The default drawable's queried properties.  This is the only source of the
   precision/sample claims the replay control is allowed to rely on: an
   offscreen FBO's attachments are never substituted for the live window. */
typedef struct {
    int valid;
    int binding;
    int samples;
    int sample_buffers;
    int depth_bits;
    int stencil_bits;
    int red_bits;
    int green_bits;
    int blue_bits;
    int alpha_bits;
    int double_buffer;
    int stereo;
    int draw_buffer;
    int read_buffer;
    int framebuffer_srgb;
    int framebuffer_srgb_known;
    int dither;
    int multisample;
} ftrace_drawable_state;

static int srgb_extension_supported(void);
static int emit_event(const char *json);
static uint64_t now_ms(void);
static uint64_t now_ns(void);
static void timeline_write(const char *format, ...);
static const char *bool_text(int value);
static int write_reference_pixels(const uint8_t *pixels, uint32_t width,
                                  uint32_t height);
static int capture_reference(void);
static int read_drawable_bgra(uint32_t *out_width, uint32_t *out_height,
                              uint8_t **out_pixels,
                              ftrace_drawable_state *out_state);
static int read_observation_pixels(uint32_t framebuffer, GLenum read_buffer,
                                   uint32_t texture, uint32_t width,
                                   uint32_t height, GLenum format,
                                   GLenum type, size_t bytes_per_pixel,
                                   uint8_t **out_pixels);
static int framebuffer_attachment_identity_supported(void);
static void sha256_hex(const void *data, size_t size, char out[65]);
static int write_atomic(const char *path, const void *data, size_t size);
static int write_frame_manifest(int complete, const char *reason);
static void finish_capture_ex(const char *reason, int allow_reference);
static int capture_timed_out_locked(void);
static void continuous_write_campaign(const char *status,
                                      const char *reason);
static int maybe_capture_default_checkpoint_locked(void);
static void prefix_reset_locked(void);
static int prefix_emit_locked(int event, const char *timing, const char *kind);
static int prefix_emit_texture_locked(int event, const char *timing,
                                      const char *kind, int texture_name);
static int capture_default_checkpoint_pixels_locked(
    int slot, const uint8_t *pixels, size_t pixels_bytes,
    const uint8_t *depth, size_t depth_bytes,
    const uint8_t *stencil, size_t stencil_bytes,
    uint32_t width, uint32_t height,
    const ftrace_drawable_state *drawable,
    int depth_used, int stencil_used);
static int checkpoint_pending_slot_locked(void);
static void checkpoint_clear_pending_locked(void);

static void fail_capture_locked(const char *reason) {
    g.capture_failed = 1;
    if (g_capture_failure_reason == NULL) {
        g_capture_failure_reason = reason;
    }
}

/* The pending checkpoint may only attach to the event it was selected for.
   A record emitted for any other index means the pre-execution observation
   was attached to the wrong operation, so the capture is failed rather than
   silently re-bound. */
static int checkpoint_pending_slot_locked(void) {
    if (g.checkpoint_pending < 0) {
        return -1;
    }
    if (g.checkpoint_pending >= g.checkpoint_count) {
        fail_capture_locked("default_checkpoint_unattached");
        return -1;
    }
    if (g.checkpoint_events[g.checkpoint_pending] != g.event_count) {
        fail_capture_locked("default_checkpoint_unattached");
        return -1;
    }
    return g.checkpoint_pending;
}

static void checkpoint_clear_pending_locked(void) {
    g.checkpoint_pending = -1;
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
    g.prefix_copy_pending_event = -1;
    g.prefix_copy_pending_texture = -1;
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
    /* The copy interposer calls this function before the real GL copy, so this
       is the selected event's pre-execution observation boundary. */
    if (maybe_capture_default_checkpoint_locked() < 0) {
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
    char checkpoint_image[sizeof(g.checkpoint_json[0]) + 64] = "";
    int checkpoint_slot = checkpoint_pending_slot_locked();
    if (checkpoint_slot >= 0) {
        int written = snprintf(checkpoint_image, sizeof(checkpoint_image),
                               ",\"default_checkpoint\":%s",
                               g.checkpoint_json[checkpoint_slot]);
        if (written <= 0 || (size_t)written >= sizeof(checkpoint_image)) {
            /* The checkpoint record must attach verbatim; a truncated object
               would be sealed as an unparseable event stream. */
            checkpoint_image[0] = 0;
            fail_capture_locked("default_checkpoint_record");
        }
    }
    if (g.armed && event->source_framebuffer == 0) {
        const char *capture_source = getenv(
            "EU4_GFX_FRAME_TRACE_CAPTURE_COPY_SOURCE");
        if (capture_source != NULL && strcmp(capture_source, "1") == 0) {
            uint32_t source_width = 0, source_height = 0;
            uint8_t *source_pixels = NULL;
            if (read_drawable_bgra(&source_width, &source_height,
                                   &source_pixels, NULL) != 0 ||
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
        "\"scene\":%d,\"present\":%d,\"captured_frame\":%d%s%s}",
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
        g.scene_index, g.present_index, g.capturing_frame, source_image,
        checkpoint_image);
    if (length <= 0 || (size_t)length >= sizeof body) {
        fail_capture_locked("copy_event_oversized");
    } else if (emit_event(body)) {
        g.event_count++;
        g.copy_events++;
        accepted = 1;
        checkpoint_clear_pending_locked();
        g.prefix_copy_pending_event =
            g.prefix_enabled && g.event_count - 1 <= g.prefix_end
                ? g.event_count - 1 : -1;
        g.prefix_copy_pending_texture =
            g.prefix_copy_pending_event >= 0 ? event->destination_texture : -1;
    }
    pthread_mutex_unlock(&g_lock);
    return accepted;
}

/* Checked at engine entry, the effective submission, and engine exit.
 * Readback may temporarily bind a different framebuffer, so observe first. */
typedef struct {
    GLint framebuffer, read_framebuffer, draw_buffer, read_buffer;
    GLint attachment, level, samples, depth, stencil, attachment_type, other_buffer;
    GLenum framebuffer_status, error;
    int error_checked;
} selected_draw_target;

static int selected_target(selected_draw_target *target, int check_error) {
    GLint type = 0, buffers = 0;
    glGetIntegerv(GL_DRAW_FRAMEBUFFER_BINDING, &target->framebuffer);
    glGetIntegerv(GL_READ_FRAMEBUFFER_BINDING, &target->read_framebuffer);
    glGetIntegerv(GL_DRAW_BUFFER, &target->draw_buffer);
    glGetIntegerv(GL_READ_BUFFER, &target->read_buffer);
    if (target->framebuffer <= 0 || target->read_framebuffer != target->framebuffer ||
        target->draw_buffer != GL_COLOR_ATTACHMENT0 || target->read_buffer != GL_COLOR_ATTACHMENT0)
        return 0;
    target->framebuffer_status = glCheckFramebufferStatus(GL_DRAW_FRAMEBUFFER);
    if (target->framebuffer_status != GL_FRAMEBUFFER_COMPLETE) return 0;
#define ATTACHMENT(slot, field, value) glGetFramebufferAttachmentParameteriv( \
    GL_DRAW_FRAMEBUFFER, (slot), GL_FRAMEBUFFER_ATTACHMENT_##field, &(value))
    ATTACHMENT(GL_COLOR_ATTACHMENT0, OBJECT_TYPE, type);
    target->attachment_type = type;
    if (type != GL_TEXTURE) return 0;
    ATTACHMENT(GL_COLOR_ATTACHMENT0, OBJECT_NAME, target->attachment);
    ATTACHMENT(GL_COLOR_ATTACHMENT0, TEXTURE_LEVEL, target->level);
    ATTACHMENT(GL_DEPTH_ATTACHMENT, OBJECT_TYPE, target->depth);
    ATTACHMENT(GL_STENCIL_ATTACHMENT, OBJECT_TYPE, target->stencil);
#undef ATTACHMENT
    glGetIntegerv(GL_SAMPLES, &target->samples);
    glGetIntegerv(GL_MAX_DRAW_BUFFERS, &buffers);
    for (GLint i = 1; i < buffers; i++) {
        GLint other = 0;
        glGetIntegerv(GL_DRAW_BUFFER0 + i, &other);
        if (other != GL_NONE) {
            target->other_buffer = other;
            return 0;
        }
    }
    if (target->attachment <= 0 || target->level != 0 ||
        target->samples || target->depth || target->stencil) return 0;
    /* This established query consumes an error. Inspect it for a potentially
       valid observation only, never to enrich an already-refused result. */
    if (check_error) {
        target->error_checked = 1;
        target->error = glGetError();
    }
    return target->error == GL_NO_ERROR;
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
static _Thread_local struct {
    selected_draw_target entry, submission;
    CGLContextObj context;
    uint64_t call_id;
    unsigned count;
    const char *failure;
    int mode, first, vertices;
    int observed_mode, observed_first, observed_vertices;
    int submission_context_matches;
} g_selected;

static void selected_failure(const char *reason) {
    if (g_selected.failure == NULL) g_selected.failure = reason;
}

static void begin_selected(uint64_t call_id, int supported, int mode, int first, int count) {
    memset(&g_selected, 0, sizeof(g_selected));
    g_selected.call_id = call_id;
    g_selected.context = CGLGetCurrentContext();
    g_selected.mode = mode;
    g_selected.first = first;
    g_selected.vertices = count;
    if (!supported) selected_failure("unsupported_draw");
    else if (g_selected.context == NULL) selected_failure("entry_context");
    else if (!selected_target(&g_selected.entry, 1)) selected_failure("entry_target");
}

void eu4_gfx_frame_trace_submit_arrays(unsigned mode, int first, int count) {
    if (!g_selected.call_id) return;
    if (++g_selected.count != 1) {
        selected_failure("multiple_submissions");
        return;
    }
    g_selected.observed_mode = (int)mode;
    g_selected.observed_first = first;
    g_selected.observed_vertices = count;
    g_selected.submission_context_matches = g_selected.context == CGLGetCurrentContext();
    if (!g_selected.submission_context_matches) {
        selected_failure("submission_context");
        return;
    }
    if (mode != (unsigned)g_selected.mode || first != g_selected.first ||
        count != g_selected.vertices) selected_failure("submission_arguments");
    if (!selected_target(&g_selected.submission, g_selected.failure == NULL)) selected_failure("submission_target");
    else if (memcmp(&g_selected.entry, &g_selected.submission, sizeof(g_selected.entry)))
        selected_failure("submission_target_changed");
}

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
    if (g.prefix_enabled) {
        /* The exact-prefix payload is an explicit, separately budgeted set of
           full-frame readbacks; it must not be silently truncated by the
           ordinary per-frame artifact cap.  The prefix budget and the ordinary
           frame artifacts are both still bounded. */
        blob_cap += g.prefix_max_bytes;
    }
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

/* Reads the opt-in uv2 diagnostic configuration.  Returns 1 enabled, 0
 * disabled, -1 malformed (the frame capture then refuses to arm). */
static int uv2_probe_configure_from_env(const char *trace_dir) {
    g_uv2_probe.enabled = 0;
    g_uv2_probe_done = 0;
    g_uv2_sparse_sha[0] = 0;
    if (eu4_uv2_parse_pixels(getenv("EU4_GFX_UV2_PROBE_PIXELS"),
                             &g_uv2_pixels) != 0) {
        return -1;
    }
    const char *enabled = getenv("EU4_GFX_UV2_PROBE");
    if (enabled == NULL || enabled[0] == 0 || strcmp(enabled, "0") == 0) {
        return 0;
    }
    if (strcmp(enabled, "1") != 0) {
        return -1;
    }
    uint64_t value = 0;
    const char *event = getenv("EU4_GFX_UV2_PROBE_EVENT");
    if (event != NULL && event[0] != 0) {
        if (parse_u64(event, &value) != 0 || value > (uint64_t)INT_MAX) {
            return -1;
        }
        g_uv2_probe.event = (int)value;
    }
    const char *program = getenv("EU4_GFX_UV2_PROBE_PROGRAM");
    if (program != NULL && program[0] != 0) {
        if (parse_u64(program, &value) != 0 || value > (uint64_t)INT_MAX) {
            return -1;
        }
        g_uv2_probe.program = (int)value;
    }
    const char *framebuffer = getenv("EU4_GFX_UV2_PROBE_FRAMEBUFFER");
    if (framebuffer != NULL && framebuffer[0] != 0) {
        if (parse_u64(framebuffer, &value) != 0 ||
            value > (uint64_t)INT_MAX) {
            return -1;
        }
        g_uv2_probe.framebuffer = (int)value;
    }
    const char *texture = getenv("EU4_GFX_UV2_PROBE_TEXTURE");
    if (texture != NULL && texture[0] != 0) {
        if (parse_u64(texture, &value) != 0 || value > (uint64_t)INT_MAX) {
            return -1;
        }
        g_uv2_probe.texture = (int)value;
    }
    const char *varying = getenv("EU4_GFX_UV2_PROBE_VARYING");
    if (varying != NULL && varying[0] != 0) {
        if (strlen(varying) >= sizeof(g_uv2_probe.varying)) {
            return -1;
        }
        for (const char *cursor = varying; *cursor != 0; cursor++) {
            char character = *cursor;
            if (!((character >= 'a' && character <= 'z') ||
                  (character >= 'A' && character <= 'Z') ||
                  (character >= '0' && character <= '9') ||
                  character == '_')) {
                return -1;
            }
        }
        snprintf(g_uv2_probe.varying, sizeof(g_uv2_probe.varying), "%s",
                 varying);
    }
    const char *dir = getenv("EU4_GFX_UV2_PROBE_DIR");
    if (dir != NULL && dir[0] != 0) {
        if (dir[0] != '/') {
            return -1;
        }
        snprintf(g_uv2_probe.dir, sizeof(g_uv2_probe.dir), "%s", dir);
    } else if (trace_dir != NULL && trace_dir[0] == '/') {
        snprintf(g_uv2_probe.dir, sizeof(g_uv2_probe.dir), "%s/uv2-probe",
                 trace_dir);
    } else {
        return -1;
    }
    g_uv2_probe.enabled = 1;
    return 1;
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
    g.checkpoint_pending = -1;
    g.checkpoint_max_count = FTRACE_DEFAULT_MAX_CHECKPOINTS;
    g.checkpoint_max_bytes = FTRACE_DEFAULT_CHECKPOINT_TOTAL_BYTES;
    g.prefix_max_bytes = FTRACE_PREFIX_DEFAULT_MAX_BYTES;
    g.prefix_max_ns = FTRACE_PREFIX_MAX_NS;
    g.prefix_copy_pending_event = -1;
    g.prefix_copy_pending_texture = -1;
    g.prefix_clear_pending_event = -1;
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
    const char *default_checkpoint_event =
        getenv("EU4_GFX_FRAME_TRACE_DEFAULT_CHECKPOINT_EVENT");
    const char *default_checkpoint_events =
        getenv("EU4_GFX_FRAME_TRACE_DEFAULT_CHECKPOINT_EVENTS");
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
    const char *max_checkpoints =
        getenv("EU4_GFX_FRAME_TRACE_MAX_CHECKPOINTS");
    if (max_checkpoints != NULL && max_checkpoints[0] != 0) {
        uint64_t value = 0;
        if (parse_u64(max_checkpoints, &value) != 0 || value == 0 ||
            value > (uint64_t)FTRACE_MAX_CHECKPOINTS) {
            return -1;
        }
        g.checkpoint_max_count = (int)value;
    }
    const char *checkpoint_max_bytes =
        getenv("EU4_GFX_FRAME_TRACE_CHECKPOINT_MAX_BYTES");
    if (checkpoint_max_bytes != NULL && checkpoint_max_bytes[0] != 0) {
        uint64_t value = 0;
        if (parse_u64(checkpoint_max_bytes, &value) != 0 || value == 0) {
            return -1;
        }
        g.checkpoint_max_bytes = value;
    }
    /* One selection source only.  The ordered list is the multi-checkpoint
       form; the single-event variable is retained for existing callers. */
    const char *checkpoint_selection = NULL;
    if (default_checkpoint_events != NULL &&
        default_checkpoint_events[0] != 0) {
        if (default_checkpoint_event != NULL &&
            default_checkpoint_event[0] != 0) {
            return -1;
        }
        checkpoint_selection = default_checkpoint_events;
    } else if (default_checkpoint_event != NULL &&
               default_checkpoint_event[0] != 0) {
        checkpoint_selection = default_checkpoint_event;
    }
    if (checkpoint_selection != NULL) {
        size_t selection_length = strlen(checkpoint_selection);
        /* Reject empty elements explicitly: strtok_r would silently collapse
           them and a requested checkpoint would disappear. */
        if (selection_length >= 512 || checkpoint_selection[0] == ',' ||
            checkpoint_selection[selection_length - 1] == ',' ||
            strstr(checkpoint_selection, ",,") != NULL) {
            return -1;
        }
        char selection_copy[512];
        snprintf(selection_copy, sizeof(selection_copy), "%s",
                 checkpoint_selection);
        char *save = NULL;
        for (char *token = strtok_r(selection_copy, ",", &save); token != NULL;
             token = strtok_r(NULL, ",", &save)) {
            uint64_t value = 0;
            if (parse_u64(token, &value) != 0 ||
                value > (uint64_t)INT_MAX) {
                return -1;
            }
            if (value >= (uint64_t)g.max_draws) {
                /* A selected event at or beyond the event budget can never be
                   recorded and can never be observed pre-execution. */
                return -1;
            }
            if (g.checkpoint_count >= g.checkpoint_max_count ||
                g.checkpoint_count >= FTRACE_MAX_CHECKPOINTS) {
                return -1;
            }
            if (g.checkpoint_count > 0 &&
                value <= (uint64_t)
                    g.checkpoint_events[g.checkpoint_count - 1]) {
                /* Strictly increasing: duplicates and reordering are
                   rejected rather than normalised. */
                return -1;
            }
            g.checkpoint_events[g.checkpoint_count++] = (int)value;
        }
        if (g.checkpoint_count == 0) {
            return -1;
        }
    }
    const char *prefix_end = getenv("EU4_GFX_FRAME_TRACE_PREFIX_END");
    const char *observe_events =
        getenv("EU4_GFX_FRAME_TRACE_OBSERVE_EVENTS");
    const char *observe_caller =
        getenv("EU4_GFX_FRAME_TRACE_OBSERVE_CALLER");
    int select_prefix = prefix_end != NULL && prefix_end[0] != 0;
    int select_events = observe_events != NULL && observe_events[0] != 0;
    int select_caller = observe_caller != NULL && observe_caller[0] != 0;
    if (select_prefix + select_events + select_caller > 1) {
        return -1;
    }
    if (select_caller) {
        if (parse_u64(observe_caller, &g.prefix_caller) != 0 ||
            g.prefix_caller == 0) {
            return -1;
        }
        g.prefix_enabled = 1;
        g.prefix_sparse = 1;
        g.prefix_event_count = 1;
        g.prefix_events[0] = -1;
        g.prefix_end = FTRACE_MAX_PREFIX_EVENTS - 1;
    } else if (prefix_end != NULL && prefix_end[0] != 0) {
        uint64_t value = 0;
        if (parse_u64(prefix_end, &value) != 0 ||
            value >= (uint64_t)FTRACE_MAX_PREFIX_EVENTS) {
            return -1;
        }
        if (g.max_draws == 0 || value >= (uint64_t)g.max_draws) {
            /* Prefix events are operation indices; the frame event budget
               must cover the complete prefix. */
            return -1;
        }
        g.prefix_enabled = 1;
        g.prefix_end = (int)value;
    } else if (observe_events != NULL && observe_events[0] != 0) {
        size_t selection_length = strlen(observe_events);
        if (selection_length >= 4096 || observe_events[0] == ',' ||
            observe_events[selection_length - 1] == ',' ||
            strstr(observe_events, ",,") != NULL) {
            return -1;
        }
        char selection_copy[4096];
        snprintf(selection_copy, sizeof(selection_copy), "%s",
                 observe_events);
        char *save = NULL;
        for (char *token = strtok_r(selection_copy, ",", &save); token != NULL;
             token = strtok_r(NULL, ",", &save)) {
            uint64_t value = 0;
            if (parse_u64(token, &value) != 0 || value > (uint64_t)INT_MAX) {
                return -1;
            }
            if (value >= (uint64_t)FTRACE_MAX_PREFIX_EVENTS ||
                value >= (uint64_t)g.max_draws) {
                return -1;
            }
            if (g.prefix_event_count >= FTRACE_MAX_PREFIX_EVENTS) {
                return -1;
            }
            if (g.prefix_event_count > 0 &&
                value <= (uint64_t)
                    g.prefix_events[g.prefix_event_count - 1]) {
                return -1;
            }
            g.prefix_events[g.prefix_event_count++] = (int)value;
        }
        if (g.prefix_event_count == 0) {
            return -1;
        }
        g.prefix_enabled = 1;
        g.prefix_sparse = 1;
        g.prefix_end = g.prefix_events[g.prefix_event_count - 1];
    }
    if (g.prefix_enabled) {
        g.prefix_max_bytes = FTRACE_PREFIX_DEFAULT_MAX_BYTES;
        g.prefix_max_ns = FTRACE_PREFIX_MAX_NS;
        const char *prefix_bytes =
            getenv("EU4_GFX_FRAME_TRACE_PREFIX_MAX_BYTES");
        if (prefix_bytes != NULL && prefix_bytes[0] != 0) {
            uint64_t bytes = 0;
            if (parse_u64(prefix_bytes, &bytes) != 0 || bytes == 0 ||
                bytes > FTRACE_PREFIX_HARD_MAX_BYTES) {
                return -1;
            }
            g.prefix_max_bytes = bytes;
        }
        const char *prefix_ns = getenv("EU4_GFX_FRAME_TRACE_PREFIX_MAX_NS");
        if (prefix_ns != NULL && prefix_ns[0] != 0) {
            uint64_t ns = 0;
            if (parse_u64(prefix_ns, &ns) != 0 || ns == 0 ||
                ns > FTRACE_PREFIX_MAX_NS) {
                return -1;
            }
            g.prefix_max_ns = ns;
        }
        const char *depth_stencil =
            getenv("EU4_GFX_FRAME_TRACE_OBSERVE_DEPTH_STENCIL");
        g.prefix_depth_stencil =
            depth_stencil != NULL && strcmp(depth_stencil, "1") == 0;
    }
    if (uv2_probe_configure_from_env(g.dir) < 0) {
        return -1;
    }
    if (g_uv2_probe.enabled &&
        (g_uv2_probe.event < 0 ||
         g_uv2_probe.event >= (int)g.max_draws)) {
        return -1;
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
static int json_line_balanced(const char *line, int start, int end) {
    int curly = 0, square = 0, in_string = 0, escape = 0;
    for (int index = start; index < end; index++) {
        char character = line[index];
        if (in_string) {
            if (escape) escape = 0;
            else if (character == '\\') escape = 1;
            else if (character == '"') in_string = 0;
            continue;
        }
        if (character == '"') in_string = 1;
        else if (character == '{') curly++;
        else if (character == '}') {
            if (--curly < 0) return 0;
        } else if (character == '[') square++;
        else if (character == ']') {
            if (--square < 0) return 0;
        }
    }
    return !in_string && curly == 0 && square == 0;
}

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
            !has_kind || !json_line_balanced(line, start, end)) {
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
        case GL_COMPRESSED_DXT1_ALPHA: return "DXT1_ALPHA";
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
        case GL_R32F_VALUE: return "R32F";
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

/* S3TC textures are captured twice: the existing decoded RGBA8 levels keep
 * every consumer working, and the original compressed blocks preserve the
 * representation the live sampler actually decodes.  The Metal replay prefers
 * the compressed blocks when every level provides them. */
static int compressed_block_bytes(GLenum internal) {
    switch (internal) {
        case GL_COMPRESSED_DXT1:
        case GL_COMPRESSED_DXT1_ALPHA: return 8;
        case GL_COMPRESSED_DXT3: return 16;
        case GL_COMPRESSED_DXT5: return 16;
        default: return 0;
    }
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
                    "\"bytes\":%zu,\"file\":\"%s\",\"sha256\":\"%s\"",
                    bytes, blob_name, blob_sha);
            int cblock = compressed_block_bytes((GLenum)internal);
            if (cblock > 0) {
                /* Declare that this level was expected to carry original
                 * blocks before attempting the read.  A capture that loses
                 * every block record must not look like a legacy decoded
                 * texture. */
                jprintf(out, ",\"compressed_expected\":true");
                size_t cw = ((size_t)width + 3u) / 4u;
                size_t ch = ((size_t)height + 3u) / 4u;
                size_t cbytes = cw * ch * (size_t)cblock;
                uint8_t *compressed_pixels = malloc(cbytes);
                if (compressed_pixels != NULL) {
                    memset(compressed_pixels, 0, cbytes);
                    while (glGetError() != GL_NO_ERROR) {
                    }
                    glGetCompressedTexImage(target, level, compressed_pixels);
                    if (glGetError() == GL_NO_ERROR) {
                        char compressed_name[FTRACE_NAME] = "";
                        char compressed_sha[65] = "";
                        if (put_blob(compressed_pixels, cbytes, "texc",
                                     compressed_name, compressed_sha) == 0) {
                            jprintf(out,
                                    ",\"compressed\":{\"format\":\"%s\","
                                    "\"internal_format\":%u,"
                                    "\"bytes\":%zu,\"row_stride\":%zu,"
                                    "\"file\":\"%s\",\"sha256\":\"%s\"}",
                                    format_name((GLenum)internal),
                                    (unsigned)internal, cbytes,
                                    cw * (size_t)cblock, compressed_name,
                                    compressed_sha);
                        }
                    }
                    free(compressed_pixels);
                }
            }
            jprintf(out, "}");
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
    if (g.test_only && g.test_target_fb_override >= 0) {
        framebuffer = (GLint)g.test_target_fb_override;
    } else {
        glGetIntegerv(GL_DRAW_FRAMEBUFFER_BINDING, &framebuffer);
    }
    g.last_capture_target_fb = (int)framebuffer;
    GLint draw_buffer = 0, read_buffer = 0;
    glGetIntegerv(GL_DRAW_BUFFER, &draw_buffer);
    glGetIntegerv(GL_READ_BUFFER, &read_buffer);
    GLint viewport[4] = {0, 0, 0, 0};
    glGetIntegerv(GL_VIEWPORT, viewport);
    GLint width = viewport[2];
    GLint height = viewport[3];
    jprintf(out,
            "\"target\":{\"framebuffer\":%d,\"draw_buffer\":\"0x%x\","
            "\"read_buffer\":\"0x%x\","
            "\"viewport\":[%d,%d,%d,%d]",
            framebuffer, draw_buffer, read_buffer, viewport[0], viewport[1],
            viewport[2], viewport[3]);
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
    GLint object_type = 0, object_name = 0, level = 0, internal_format = 0;
    int selected_pre = g.prefix_caller != 0 && strcmp(member, "pre_image") == 0;
    if (selected_pre && (framebuffer == 0 ||
            glCheckFramebufferStatus(GL_DRAW_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE)) {
        return -1;
    }
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
            if (selected_pre) {
                glGetTexLevelParameteriv(GL_TEXTURE_2D, level, GL_TEXTURE_INTERNAL_FORMAT,
                                         &internal_format);
            }
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
    if (selected_pre && (object_type != GL_TEXTURE || object_name <= 0 || level != 0 ||
                         internal_format != GL_RGBA8)) {
        return -1;
    }
    size_t bytes = (size_t)width * (size_t)height * 4;
    uint8_t *pixels = NULL;
    if (read_observation_pixels((uint32_t)framebuffer,
                                framebuffer == 0 ? GL_BACK
                                                 : GL_COLOR_ATTACHMENT0,
                                0, (uint32_t)width, (uint32_t)height, GL_BGRA,
                                GL_UNSIGNED_INT_8_8_8_8_REV, 4, &pixels) != 0) {
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
            "\"sha256\":\"%s\"",
            member, width, height, blob_name, blob_sha);
    if (selected_pre) {
        jprintf(out, ",\"framebuffer\":%d,\"attachment\":%d,\"level\":%d,"
                "\"internal_format\":%d,\"pixel_format\":\"BGRA8\",\"origin\":\"lower_left\"",
                framebuffer, object_name, level, internal_format);
    }
    jprintf(out, "}");
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


/* ------------------------------------------------------------------------- */
/* Opt-in exact-prefix coverage                                               */
/* ------------------------------------------------------------------------- */

typedef struct {
    int framebuffer;
    int width;
    int height;
    int color_type;
    int color_name;
    int color_level;
    int internal_format;
    int depth_present;
    int stencil_present;
    int depth_bits;
    int stencil_bits;
    int color_component_type;
    int color_red_bits;
    int color_green_bits;
    int color_blue_bits;
    int color_alpha_bits;
    int unsupported_format;
    int alpha_applicable;
    GLenum read_type;
    int colour_bpp;
    int direct_texture;
    GLenum read_format;
    const char *pixel_format;
} prefix_target_state;

static int prefix_complete_locked(void) {
    if (g.prefix_failed) {
        return 0;
    }
    if (g.prefix_sparse) {
        return g.prefix_event_count > 0 &&
               g.prefix_count == g.prefix_event_count;
    }
    return g.prefix_initial && g.prefix_next_event == g.prefix_end + 1;
}

static void prefix_reset_locked(void) {
    if (g.prefix_caller != 0) {
        g.prefix_events[0] = -1;
        g.prefix_end = FTRACE_MAX_PREFIX_EVENTS - 1;
    }
    if (g.prefix_observations != NULL) {
        fclose(g.prefix_observations);
        g.prefix_observations = NULL;
    }
    g.prefix_count = 0;
    g.prefix_initial = 0;
    g.prefix_next_event = 0;
    g.prefix_bytes = 0;
    g.prefix_readback_ns = 0;
    g.prefix_failed = 0;
    g.prefix_file[0] = 0;
    g.prefix_sha[0] = 0;
    g.prefix_file_bytes = 0;
    g.prefix_copy_pending_event = -1;
    g.prefix_copy_pending_texture = -1;
    g.prefix_clear_pending_event = -1;
}

typedef struct {
    int internal_format;
    GLenum read_format;
    GLenum read_type;
    int colour_bpp;
    const char *pixel_format;
    int alpha_applicable;
    int red_bits;
    int green_bits;
    int blue_bits;
    int alpha_bits;
    int component_type;
    int supported;
} observation_format_identity;

/* The one mapping from an attachment's internal format to its exact readback
   byte layout and comparison scope.  Unknown formats are unsupported rather
   than normalized into a nearby one. */
static int observation_format_identity_for(int internal_format,
                                           observation_format_identity *out) {
    memset(out, 0, sizeof(*out));
    out->internal_format = internal_format;
    out->component_type = GL_UNSIGNED_NORMALIZED;
    switch (internal_format) {
        case GL_RGB8:
            out->read_format = GL_BGRA;
            out->read_type = GL_UNSIGNED_BYTE;
            out->colour_bpp = 4;
            out->pixel_format = "BGRA8";
            out->alpha_applicable = 0;
            out->red_bits = 8;
            out->green_bits = 8;
            out->blue_bits = 8;
            out->alpha_bits = 0;
            out->supported = 1;
            return 1;
        case GL_RGBA8:
            out->read_format = GL_RGBA;
            out->read_type = GL_UNSIGNED_BYTE;
            out->colour_bpp = 4;
            out->pixel_format = "RGBA8";
            out->alpha_applicable = 1;
            out->red_bits = 8;
            out->green_bits = 8;
            out->blue_bits = 8;
            out->alpha_bits = 8;
            out->supported = 1;
            return 1;
        case GL_SRGB8_ALPHA8_VALUE:
            /* The Metal target is materialized as BGRA8Unorm, so the byte
               layout is BGRA8; the stored alpha channel still applies. */
            out->read_format = GL_BGRA;
            out->read_type = GL_UNSIGNED_BYTE;
            out->colour_bpp = 4;
            out->pixel_format = "BGRA8";
            out->alpha_applicable = 1;
            out->red_bits = 8;
            out->green_bits = 8;
            out->blue_bits = 8;
            out->alpha_bits = 8;
            out->supported = 1;
            return 1;
        case GL_R32F_VALUE:
            out->read_format = GL_RED;
            out->read_type = GL_FLOAT;
            out->colour_bpp = 4;
            out->pixel_format = "R32F";
            out->alpha_applicable = 0;
            out->red_bits = 32;
            out->component_type = GL_FLOAT;
            out->supported = 1;
            return 1;
        default:
            return 0;
    }
}

static int depth_stencil_bits_for(int internal_format, int *depth_bits,
                                  int *stencil_bits) {
    *depth_bits = 0;
    *stencil_bits = 0;
    switch (internal_format) {
        case GL_DEPTH_COMPONENT16_VALUE:
            *depth_bits = 16;
            return 1;
        case GL_DEPTH_COMPONENT24_VALUE:
            *depth_bits = 24;
            return 1;
        case GL_DEPTH_COMPONENT32F_VALUE:
            *depth_bits = 32;
            return 1;
        case GL_DEPTH24_STENCIL8_VALUE:
            *depth_bits = 24;
            *stencil_bits = 8;
            return 1;
        case GL_DEPTH32F_STENCIL8_VALUE:
            *depth_bits = 32;
            *stencil_bits = 8;
            return 1;
        case GL_STENCIL_INDEX8_VALUE:
            *stencil_bits = 8;
            return 1;
        default:
            return 0;
    }
}

typedef struct {
    int present;
    int type;
    int name;
    int level;
    int internal_format;
    int width;
    int height;
    int sizes_known;
    int red_bits;
    int green_bits;
    int blue_bits;
    int alpha_bits;
    int renderbuffer_depth_bits;
    GLint depth_component_type;
} observation_attachment;

/* Query one framebuffer attachment's effective identity. Only the sparse
   depth refusal requests the independent renderbuffer storage size.
   Bindings are restored before returning; no GL error is read. */
static void query_attachment_identity(GLenum attachment,
                                      observation_attachment *out,
                                      bool measure_storage_depth) {
    memset(out, 0, sizeof(*out));
    GLint type = 0, name = 0, level = 0;
    glGetFramebufferAttachmentParameteriv(
        GL_DRAW_FRAMEBUFFER, attachment,
        GL_FRAMEBUFFER_ATTACHMENT_OBJECT_TYPE, &type);
    if (type == 0 || type == GL_NONE) {
        return;
    }
    out->present = 1;
    out->type = (int)type;
    glGetFramebufferAttachmentParameteriv(
        GL_DRAW_FRAMEBUFFER, attachment,
        GL_FRAMEBUFFER_ATTACHMENT_OBJECT_NAME, &name);
    out->name = (int)name;
    if (type == GL_TEXTURE) {
        glGetFramebufferAttachmentParameteriv(
            GL_DRAW_FRAMEBUFFER, attachment,
            GL_FRAMEBUFFER_ATTACHMENT_TEXTURE_LEVEL, &level);
        out->level = (int)level;
        GLint previous = 0;
        glGetIntegerv(GL_TEXTURE_BINDING_2D, &previous);
        glBindTexture(GL_TEXTURE_2D, (GLuint)name);
        GLint width = 0, height = 0, internal = 0, red = 0, green = 0,
              blue = 0, alpha = 0;
        glGetTexLevelParameteriv(GL_TEXTURE_2D, level, GL_TEXTURE_WIDTH,
                                 &width);
        glGetTexLevelParameteriv(GL_TEXTURE_2D, level, GL_TEXTURE_HEIGHT,
                                 &height);
        glGetTexLevelParameteriv(GL_TEXTURE_2D, level,
                                 GL_TEXTURE_INTERNAL_FORMAT, &internal);
        glGetTexLevelParameteriv(GL_TEXTURE_2D, level, GL_TEXTURE_RED_SIZE,
                                 &red);
        glGetTexLevelParameteriv(GL_TEXTURE_2D, level, GL_TEXTURE_GREEN_SIZE,
                                 &green);
        glGetTexLevelParameteriv(GL_TEXTURE_2D, level, GL_TEXTURE_BLUE_SIZE,
                                 &blue);
        glGetTexLevelParameteriv(GL_TEXTURE_2D, level, GL_TEXTURE_ALPHA_SIZE,
                                 &alpha);
        glBindTexture(GL_TEXTURE_2D, (GLuint)previous);
        out->width = (int)width;
        out->height = (int)height;
        out->internal_format = (int)internal;
        out->sizes_known = 1;
        out->red_bits = (int)red;
        out->green_bits = (int)green;
        out->blue_bits = (int)blue;
        out->alpha_bits = (int)alpha;
    } else if (type == GL_RENDERBUFFER) {
        GLint previous = 0;
        glGetIntegerv(GL_RENDERBUFFER_BINDING, &previous);
        glBindRenderbuffer(GL_RENDERBUFFER, (GLuint)name);
        GLint width = 0, height = 0, internal = 0;
        glGetRenderbufferParameteriv(GL_RENDERBUFFER, GL_RENDERBUFFER_WIDTH,
                                     &width);
        glGetRenderbufferParameteriv(GL_RENDERBUFFER, GL_RENDERBUFFER_HEIGHT,
                                     &height);
        glGetRenderbufferParameteriv(GL_RENDERBUFFER,
                                     GL_RENDERBUFFER_INTERNAL_FORMAT,
                                     &internal);
        if (measure_storage_depth) {
            GLint bits = -1;
            glGetRenderbufferParameteriv(GL_RENDERBUFFER,
                                         GL_RENDERBUFFER_DEPTH_SIZE, &bits);
            out->renderbuffer_depth_bits = bits;
        }
        glBindRenderbuffer(GL_RENDERBUFFER, (GLuint)previous);
        out->width = (int)width;
        out->height = (int)height;
        out->internal_format = (int)internal;
        observation_format_identity identity;
        if (observation_format_identity_for(out->internal_format, &identity)) {
            out->sizes_known = 1;
            out->red_bits = identity.red_bits;
            out->green_bits = identity.green_bits;
            out->blue_bits = identity.blue_bits;
            out->alpha_bits = identity.alpha_bits;
        }
    }
}

static int prefix_target_locked(prefix_target_state *out, int direct_texture) {
    memset(out, 0, sizeof(*out));
    out->framebuffer = -1;
    observation_format_identity identity;
    int red = 0, green = 0, blue = 0, alpha = 0;
    int sizes_known = 0;
    int internal_format = 0;
    if (direct_texture >= 0) {
        out->direct_texture = 1;
        out->color_type = GL_TEXTURE;
        out->color_name = direct_texture;
        out->color_level = 0;
        out->width = 4;
        out->height = 4;
        if (g.test_only) {
            internal_format = g.test_target_internal_override >= 0
                                  ? g.test_target_internal_override : GL_RGBA8;
            if (observation_format_identity_for(internal_format, &identity)) {
                sizes_known = 1;
                red = identity.red_bits;
                green = identity.green_bits;
                blue = identity.blue_bits;
                alpha = identity.alpha_bits;
            }
        } else {
            GLint previous = 0;
            glGetIntegerv(GL_TEXTURE_BINDING_2D, &previous);
            glBindTexture(GL_TEXTURE_2D, (GLuint)direct_texture);
            GLint width = 0, height = 0, iformat = 0, r = 0, g = 0, b = 0,
                  a = 0;
            glGetTexLevelParameteriv(GL_TEXTURE_2D, 0, GL_TEXTURE_WIDTH,
                                     &width);
            glGetTexLevelParameteriv(GL_TEXTURE_2D, 0, GL_TEXTURE_HEIGHT,
                                     &height);
            glGetTexLevelParameteriv(GL_TEXTURE_2D, 0,
                                     GL_TEXTURE_INTERNAL_FORMAT, &iformat);
            glGetTexLevelParameteriv(GL_TEXTURE_2D, 0, GL_TEXTURE_RED_SIZE,
                                     &r);
            glGetTexLevelParameteriv(GL_TEXTURE_2D, 0, GL_TEXTURE_GREEN_SIZE,
                                     &g);
            glGetTexLevelParameteriv(GL_TEXTURE_2D, 0, GL_TEXTURE_BLUE_SIZE,
                                     &b);
            glGetTexLevelParameteriv(GL_TEXTURE_2D, 0, GL_TEXTURE_ALPHA_SIZE,
                                     &a);
            glBindTexture(GL_TEXTURE_2D, (GLuint)previous);
            out->width = (int)width;
            out->height = (int)height;
            internal_format = (int)iformat;
            sizes_known = 1;
            red = (int)r;
            green = (int)g;
            blue = (int)b;
            alpha = (int)a;
        }
    } else if (g.test_only) {
        out->framebuffer = g.test_target_fb_override >= 0
                               ? g.test_target_fb_override : 0;
        out->width = 4;
        out->height = 4;
        out->color_type = out->framebuffer == 0 ? 0 : GL_TEXTURE;
        out->color_name = out->framebuffer;
        out->color_level = 0;
        if (out->framebuffer == 0) {
            internal_format = 0;
            red = green = blue = alpha = 8;
            sizes_known = 1;
        } else {
            internal_format = g.test_target_internal_override >= 0
                                  ? g.test_target_internal_override : GL_RGBA8;
            if (observation_format_identity_for(internal_format, &identity)) {
                sizes_known = 1;
                red = identity.red_bits;
                green = identity.green_bits;
                blue = identity.blue_bits;
                alpha = identity.alpha_bits;
            }
        }
        out->depth_present = 1;
        out->stencil_present = 1;
        out->depth_bits = 24;
        out->stencil_bits = 8;
    } else {
        GLint framebuffer = 0;
        glGetIntegerv(GL_DRAW_FRAMEBUFFER_BINDING, &framebuffer);
        out->framebuffer = (int)framebuffer;
        if (framebuffer == 0) {
            GLint draw_buffer = 0, samples = 0;
            glGetIntegerv(GL_DRAW_BUFFER, &draw_buffer);
            glGetIntegerv(GL_SAMPLES, &samples);
            if (draw_buffer != GL_BACK || samples > 0) {
                /* Only the single-sample back buffer has a defined readback
                   contract here. */
                fail_capture_locked("prefix_unsupported_target");
                return -1;
            }
            uint32_t drawable_width = 0, drawable_height = 0;
            if (eu4_gfx_gl_drawable_extent(&drawable_width, &drawable_height) == 0 &&
                drawable_width > 0 && drawable_height > 0) {
                out->width = (int)drawable_width;
                out->height = (int)drawable_height;
            } else {
                GLint viewport[4] = {0, 0, 0, 0};
                glGetIntegerv(GL_VIEWPORT, viewport);
                out->width = viewport[2];
                out->height = viewport[3];
            }
            GLint bits = 0;
            glGetIntegerv(GL_RED_BITS, &red);
            glGetIntegerv(GL_GREEN_BITS, &green);
            glGetIntegerv(GL_BLUE_BITS, &blue);
            glGetIntegerv(GL_ALPHA_BITS, &alpha);
            sizes_known = 1;
            internal_format = 0;
            glGetIntegerv(GL_DEPTH_BITS, &bits);
            out->depth_bits = (int)bits;
            glGetIntegerv(GL_STENCIL_BITS, &bits);
            out->stencil_bits = (int)bits;
            out->depth_present = out->depth_bits > 0;
            out->stencil_present = out->stencil_bits > 0;
        } else {
            GLint draw_buffer = 0, samples = 0;
            glGetIntegerv(GL_DRAW_BUFFER, &draw_buffer);
            glGetIntegerv(GL_SAMPLES, &samples);
            if (draw_buffer != GL_COLOR_ATTACHMENT0 || samples > 0 ||
                glCheckFramebufferStatus(GL_DRAW_FRAMEBUFFER) !=
                    GL_FRAMEBUFFER_COMPLETE) {
                fail_capture_locked("prefix_unsupported_target");
                return -1;
            }
            observation_attachment colour;
            query_attachment_identity(GL_COLOR_ATTACHMENT0, &colour, false);
            if (!colour.present ||
                (colour.type != GL_TEXTURE && colour.type != GL_RENDERBUFFER)) {
                fail_capture_locked("prefix_unsupported_target");
                return -1;
            }
            if (colour.type == GL_RENDERBUFFER) {
                /* The replay target registry is texture-keyed; a renderbuffer
                   colour attachment cannot be given a distinct Metal target
                   identity here.  Record it as unsupported rather than alias a
                   texture that happens to share its numeric GL name. */
                memset(out, 0, sizeof(*out));
                out->framebuffer = (int)framebuffer;
                out->color_type = GL_RENDERBUFFER;
                out->color_name = colour.name;
                out->width = colour.width;
                out->height = colour.height;
                out->unsupported_format = 1;
                out->pixel_format = NULL;
                if (out->width <= 0 || out->height <= 0) {
                    fail_capture_locked("prefix_dimensions");
                    return -1;
                }
                return 0;
            }
            if (!framebuffer_attachment_identity_supported()) {
                fail_capture_locked("prefix_unsupported_target");
                return -1;
            }
            GLint cube_face = 0, layer = 0;
            glGetFramebufferAttachmentParameteriv(
                GL_DRAW_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                GL_FRAMEBUFFER_ATTACHMENT_TEXTURE_CUBE_MAP_FACE, &cube_face);
            glGetFramebufferAttachmentParameteriv(
                GL_DRAW_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                GL_FRAMEBUFFER_ATTACHMENT_TEXTURE_LAYER, &layer);
            if (cube_face != 0 || layer != 0) {
                /* Only a plain 2D texture attachment has the recorded byte
                   layout; cube/array/3D faces need a different readback. */
                fail_capture_locked("prefix_unsupported_target");
                return -1;
            }
            out->color_type = colour.type;
            out->color_name = colour.name;
            out->color_level = colour.level;
            out->width = colour.width;
            out->height = colour.height;
            internal_format = colour.internal_format;
            sizes_known = colour.sizes_known;
            red = colour.red_bits;
            green = colour.green_bits;
            blue = colour.blue_bits;
            alpha = colour.alpha_bits;
            observation_attachment depth_attachment;
            observation_attachment stencil_attachment;
            query_attachment_identity(GL_DEPTH_ATTACHMENT, &depth_attachment,
                                      false);
            query_attachment_identity(GL_STENCIL_ATTACHMENT,
                                      &stencil_attachment, false);
            int depth_bits = 0, stencil_bits = 0;
            if (depth_attachment.present &&
                depth_stencil_bits_for(depth_attachment.internal_format,
                                       &depth_bits, &stencil_bits)) {
                out->depth_bits = depth_bits;
                if (stencil_bits > 0) {
                    out->stencil_bits = stencil_bits;
                }
            }
            if (stencil_attachment.present) {
                int extra_depth = 0, extra_stencil = 0;
                if (depth_stencil_bits_for(stencil_attachment.internal_format,
                                           &extra_depth, &extra_stencil) &&
                    extra_stencil > 0) {
                    out->stencil_bits = extra_stencil;
                } else if (depth_attachment.present &&
                           stencil_attachment.name == depth_attachment.name) {
                    out->stencil_bits = 8;
                }
            }
            out->depth_present =
                depth_attachment.present || out->depth_bits > 0;
            out->stencil_present =
                stencil_attachment.present || out->stencil_bits > 0;
        }
    }
    if (out->framebuffer == 0 && direct_texture < 0) {
        if (red != 8 || green != 8 || blue != 8 ||
            (alpha != 0 && alpha != 8)) {
            out->unsupported_format = 1;
            out->pixel_format = NULL;
        } else {
            out->internal_format = 0;
            out->read_format = GL_BGRA;
            out->read_type = GL_UNSIGNED_BYTE;
            out->colour_bpp = 4;
            out->pixel_format = "BGRA8";
            out->alpha_applicable = alpha == 8;
            out->color_component_type = GL_UNSIGNED_NORMALIZED;
            out->color_red_bits = red;
            out->color_green_bits = green;
            out->color_blue_bits = blue;
            out->color_alpha_bits = alpha;
        }
    } else if (!observation_format_identity_for(internal_format, &identity) ||
               (sizes_known &&
                (red != identity.red_bits || green != identity.green_bits ||
                 blue != identity.blue_bits || alpha != identity.alpha_bits))) {
        out->unsupported_format = 1;
        out->pixel_format = NULL;
    } else {
        out->internal_format = identity.internal_format;
        out->read_format = identity.read_format;
        out->read_type = identity.read_type;
        out->colour_bpp = identity.colour_bpp;
        out->pixel_format = identity.pixel_format;
        out->alpha_applicable = identity.alpha_applicable;
        out->color_component_type = identity.component_type;
        out->color_red_bits = identity.red_bits;
        out->color_green_bits = identity.green_bits;
        out->color_blue_bits = identity.blue_bits;
        out->color_alpha_bits = identity.alpha_bits;
    }
    if (out->width <= 0 || out->height <= 0 || out->width > 8192 ||
        out->height > 8192) {
        fail_capture_locked("prefix_dimensions");
        return -1;
    }
    return 0;
}

static int prefix_read_colour_locked(const prefix_target_state *target,
                                     int event, uint8_t **out_pixels,
                                     size_t *out_bytes) {
    *out_pixels = NULL;
    *out_bytes = 0;
    size_t bytes = (size_t)target->width * (size_t)target->height *
                   (size_t)target->colour_bpp;
    if (g.test_only) {
        uint8_t *pixels = (uint8_t *)malloc(bytes ? bytes : 1);
        if (pixels == NULL) {
            fail_capture_locked("prefix_read");
            return -1;
        }
        for (size_t i = 0; i < bytes; i++) {
            pixels[i] = (uint8_t)(i * 37u + 11u + (size_t)event * 7u);
        }
        *out_pixels = pixels;
        *out_bytes = bytes;
        return 0;
    }
    if (target->unsupported_format || target->pixel_format == NULL) {
        fail_capture_locked("prefix_unsupported_format");
        return -1;
    }
    if (!target->direct_texture && target->framebuffer != 0) {
        GLint previous = 0;
        glGetIntegerv(GL_READ_FRAMEBUFFER_BINDING, &previous);
        glBindFramebuffer(GL_READ_FRAMEBUFFER, (GLuint)target->framebuffer);
        GLenum status = glCheckFramebufferStatus(GL_READ_FRAMEBUFFER);
        glBindFramebuffer(GL_READ_FRAMEBUFFER, (GLuint)previous);
        if (status != GL_FRAMEBUFFER_COMPLETE) {
            fail_capture_locked("prefix_unsupported_target");
            return -1;
        }
    }
    uint32_t texture = target->direct_texture
                           ? (uint32_t)target->color_name : 0;
    uint32_t framebuffer = target->direct_texture
                               ? 0 : (uint32_t)target->framebuffer;
    GLenum read_buffer = target->framebuffer == 0
                             ? GL_BACK : GL_COLOR_ATTACHMENT0;
    uint8_t *pixels = NULL;
    if (read_observation_pixels(framebuffer, read_buffer, texture,
                                (uint32_t)target->width,
                                (uint32_t)target->height, target->read_format,
                                target->read_type, (size_t)target->colour_bpp,
                                &pixels) != 0) {
        free(pixels);
        fail_capture_locked("prefix_read");
        return -1;
    }
    *out_pixels = pixels;
    *out_bytes = bytes;
    return 0;
}

static int prefix_read_attachment_locked(const prefix_target_state *target,
                                         GLenum format, GLenum type,
                                         size_t bytes_per_pixel,
                                         uint8_t **out_pixels,
                                         size_t *out_bytes, size_t *out_capacity) {
    *out_pixels = NULL;
    *out_bytes = 0;
    if (out_capacity != NULL) {
        *out_capacity = 0;
    }
    if (target->direct_texture || target->framebuffer < 0) {
        return -1;
    }
    size_t bytes = (size_t)target->width * (size_t)target->height *
                   bytes_per_pixel;
    if (g.test_only) {
        uint8_t *pixels = (uint8_t *)calloc(bytes ? bytes : 1, 1);
        if (pixels == NULL) {
            fail_capture_locked("prefix_read");
            return -1;
        }
        if (format == GL_DEPTH_COMPONENT && type == GL_FLOAT) {
            float *values = (float *)pixels;
            for (size_t i = 0; i < (size_t)target->width * target->height; i++) {
                values[i] = 0.5f;
            }
        } else {
            memset(pixels, 0x5A, bytes);
        }
        *out_pixels = pixels;
        *out_bytes = bytes;
        if (out_capacity != NULL) {
            *out_capacity = bytes;
        }
        return 0;
    }
    uint8_t *pixels = NULL;
    GLenum read_buffer = target->framebuffer == 0
                             ? GL_BACK : GL_COLOR_ATTACHMENT0;
    if (read_observation_pixels((uint32_t)target->framebuffer, read_buffer, 0,
                                (uint32_t)target->width,
                                (uint32_t)target->height, format, type,
                                bytes_per_pixel, &pixels) != 0) {
        free(pixels);
        fail_capture_locked("prefix_read");
        return -1;
    }
    *out_pixels = pixels;
    *out_bytes = bytes;
    if (out_capacity != NULL) {
        *out_capacity = bytes;
    }
    return 0;
}

static int prefix_emit_target_locked(int event, const char *timing,
                                     const char *kind, int direct_texture) {
    if (!g.prefix_enabled) {
        return 0;
    }
    if (g.prefix_failed) {
        return -1;
    }
    if (g.prefix_observations == NULL) {
        fail_capture_locked("prefix_not_open");
        return -1;
    }
    if (event < 0 || event > g.prefix_end) {
        return 0;
    }
    int post = strcmp(timing, "post_event") == 0;
    if (g.prefix_sparse) {
        if (!post) {
            /* A selected range has no proven initial-state anchor. */
            fail_capture_locked("prefix_initial_invalid");
            return -1;
        }
        int selected = 0;
        for (int index = 0; index < g.prefix_event_count; index++) {
            if (g.prefix_events[index] == event) {
                selected = 1;
                break;
            }
        }
        if (!selected) {
            return 0;
        }
        if (g.prefix_count >= g.prefix_event_count ||
            g.prefix_events[g.prefix_count] != event) {
            /* Selected events execute in order; a repeated or skipped entry
               would make the selection ambiguous. */
            fail_capture_locked("prefix_gap");
            return -1;
        }
    } else if (post) {
        if (event != g.prefix_next_event) {
            fail_capture_locked("prefix_gap");
            return -1;
        }
    } else if (strcmp(timing, "pre_event") == 0) {
        if (event != 0 || g.prefix_initial) {
            fail_capture_locked("prefix_initial_invalid");
            return -1;
        }
    } else {
        fail_capture_locked("prefix_timing");
        return -1;
    }
    prefix_target_state target;
    if (prefix_target_locked(&target, direct_texture) != 0) {
        return -1;
    }
    int unsupported_colour = target.unsupported_format;
    if (unsupported_colour &&
        (direct_texture >= 0 || strcmp(kind, "clear") != 0)) {
        /* A draw or copy into an unsupported colour format cannot be
           accounted for; only an isolated auxiliary clear may be recorded as
           an explicit exclusion. */
        fail_capture_locked("prefix_unsupported_format");
        return -1;
    }
    int depth_required = 0;
    int stencil_required = 0;
    if (!g.prefix_depth_stencil) {
        /* Colour equality is the asserted verdict; depth/stencil snapshots are
           an explicitly enabled diagnostic and are not captured by default. */
    } else if (strcmp(kind, "draw") == 0) {
        if (!g.test_only) {
            GLboolean depth_mask = GL_TRUE;
            GLint depth_test = 0, stencil_test = 0;
            glGetBooleanv(GL_DEPTH_WRITEMASK, &depth_mask);
            glGetIntegerv(GL_DEPTH_TEST, &depth_test);
            glGetIntegerv(GL_STENCIL_TEST, &stencil_test);
            depth_required = depth_mask != 0 || depth_test != 0;
            stencil_required = stencil_test != 0;
        }
    } else if (strcmp(kind, "clear") == 0) {
        depth_required = (g.prefix_clear_flags & 2) != 0;
        stencil_required = (g.prefix_clear_flags & 4) != 0;
        if (g.test_only) {
            depth_required = 0;
            stencil_required = 0;
        }
    }
    /* A target without a depth (or stencil) attachment cannot read or write
       that buffer, so the absence is a recorded fact rather than a missing
       observation.  Every other unsupported state still fails closed. */
    int depth_attachment_present = target.depth_present;
    int stencil_attachment_present = target.stencil_present;
    if (depth_required && !depth_attachment_present) {
        depth_required = 0;
    }
    if (stencil_required && !stencil_attachment_present) {
        stencil_required = 0;
    }
    uint64_t colour_bytes = unsupported_colour
                                ? 0
                                : (uint64_t)target.width *
                                      (uint64_t)target.height * 4ull;
    uint64_t depth_bytes = depth_required
                               ? (uint64_t)target.width *
                                     (uint64_t)target.height * 4ull
                               : 0;
    uint64_t stencil_bytes = stencil_required
                                 ? (uint64_t)target.width * (uint64_t)target.height
                                 : 0;
    uint64_t projected = colour_bytes + depth_bytes + stencil_bytes;
    if ((projected == 0 && !unsupported_colour) ||
        g.prefix_bytes > g.prefix_max_bytes ||
        projected > g.prefix_max_bytes - g.prefix_bytes) {
        fail_capture_locked("prefix_budget");
        return -1;
    }
    uint64_t started = now_ns();
    uint8_t *colour = NULL;
    size_t actual_colour = 0;
    uint8_t *depth = NULL;
    size_t actual_depth = 0;
    uint8_t *stencil = NULL;
    size_t actual_stencil = 0;
    if (!unsupported_colour) {
        if (prefix_read_colour_locked(&target, event, &colour,
                                      &actual_colour) != 0 ||
            actual_colour != (size_t)colour_bytes) {
            free(colour);
            return -1;
        }
    }
    if (depth_required) {
        if (prefix_read_attachment_locked(&target, GL_DEPTH_COMPONENT, GL_FLOAT,
                                          sizeof(float), &depth, &actual_depth,
                                          NULL) != 0 ||
            actual_depth != (size_t)depth_bytes) {
            free(colour);
            free(depth);
            return -1;
        }
    }
    if (stencil_required) {
        if (prefix_read_attachment_locked(&target, GL_STENCIL_INDEX,
                                          GL_UNSIGNED_BYTE, sizeof(uint8_t),
                                          &stencil, &actual_stencil,
                                          NULL) != 0 ||
            actual_stencil != (size_t)stencil_bytes) {
            free(colour);
            free(depth);
            free(stencil);
            return -1;
        }
    }
    char colour_file[FTRACE_NAME] = "";
    char colour_sha[65] = "";
    if (!unsupported_colour &&
        put_blob(colour, actual_colour, "prefix-color", colour_file,
                 colour_sha) != 0) {
        free(colour);
        free(depth);
        free(stencil);
        return -1;
    }
    char depth_file[FTRACE_NAME] = "";
    char depth_sha[65] = "";
    char stencil_file[FTRACE_NAME] = "";
    char stencil_sha[65] = "";
    if (depth != NULL && actual_depth > 0) {
        if (put_blob(depth, actual_depth, "prefix-depth", depth_file,
                     depth_sha) != 0) {
            free(colour);
            free(depth);
            free(stencil);
            return -1;
        }
    }
    if (stencil != NULL && actual_stencil > 0) {
        if (put_blob(stencil, actual_stencil, "prefix-stencil", stencil_file,
                     stencil_sha) != 0) {
            free(colour);
            free(depth);
            free(stencil);
            return -1;
        }
    }
    char *target_json = NULL;
    if (direct_texture >= 0) {
        target_json = (char *)malloc(512);
        if (target_json != NULL) {
            snprintf(target_json, 512,
                     "\"target\":{\"framebuffer\":-1,"
                     "\"draw_buffer\":\"0x0\",\"read_buffer\":\"0x0\","
                     "\"viewport\":[0,0,%d,%d],"
                     "\"color_attachment\":{\"type\":\"texture\","
                     "\"name\":%d,\"level\":0,"
                     "\"internal_format\":\"%s\","
                     "\"width\":%d,\"height\":%d}}",
                     target.width, target.height, direct_texture,
                     target.pixel_format, target.width, target.height);
        }
    } else if (g.test_only) {
        /* The offline writer host has no GL context.  Emit the same target
           shape without querying the driver. */
        target_json = (char *)malloc(512);
        if (target_json != NULL) {
            if (target.framebuffer == 0) {
                snprintf(target_json, 512,
                         "\"target\":{\"framebuffer\":0,"
                         "\"draw_buffer\":\"0x405\","
                         "\"read_buffer\":\"0x405\","
                         "\"viewport\":[0,0,%d,%d]}",
                         target.width, target.height);
            } else {
                snprintf(target_json, 512,
                         "\"target\":{\"framebuffer\":%d,"
                         "\"draw_buffer\":\"0x8ce0\","
                         "\"read_buffer\":\"0x8ce0\","
                         "\"viewport\":[0,0,%d,%d],"
                         "\"color_attachment\":{\"type\":\"texture\","
                         "\"name\":%d,\"level\":0,"
                         "\"internal_format\":\"RGBA8\","
                         "\"width\":%d,\"height\":%d}}",
                         target.framebuffer, target.width, target.height,
                         target.color_name, target.width, target.height);
            }
        }
    } else {
        size_t target_size = 0;
        FILE *target_stream = open_memstream(&target_json, &target_size);
        if (target_stream != NULL) {
            json_out target_out = {target_stream, 0};
            capture_target(&target_out, NULL, NULL);
            fclose(target_stream);
            if (target_out.failed) {
                free(target_json);
                target_json = NULL;
            }
        }
    }
    if (target_json == NULL) {
        free(colour);
        free(depth);
        free(stencil);
        fail_capture_locked("prefix_record");
        return -1;
    }
    char resource[64];
    char target_name[64];
    if (direct_texture >= 0) {
        snprintf(resource, sizeof(resource), "tex:%d", direct_texture);
        snprintf(target_name, sizeof(target_name), "%d", direct_texture);
    } else if (target.framebuffer == 0) {
        snprintf(resource, sizeof(resource), "fb:0");
        snprintf(target_name, sizeof(target_name), "0");
    } else if (target.color_type == GL_RENDERBUFFER) {
        /* Renderbuffers and textures occupy different name spaces.  A
           renderbuffer attachment is identified by its framebuffer, never by
           a texture name that happens to share the numeric value. */
        snprintf(resource, sizeof(resource), "fb:%d", target.framebuffer);
        snprintf(target_name, sizeof(target_name), "%d", target.framebuffer);
    } else {
        snprintf(resource, sizeof(resource), "tex:%d", target.color_name);
        snprintf(target_name, sizeof(target_name), "%d", target.color_name);
    }
    char depth_json[512];
    if (depth != NULL) {
        snprintf(depth_json, sizeof(depth_json),
                 "\"depth\":{\"status\":\"captured\",\"file\":\"%s\","
                 "\"sha256\":\"%s\",\"bytes\":%zu,\"format\":\"f32\","
                 "\"width\":%d,\"height\":%d},",
                 depth_file, depth_sha, actual_depth, target.width,
                 target.height);
    } else if (!depth_attachment_present) {
        snprintf(depth_json, sizeof(depth_json),
                 "\"depth\":{\"status\":\"not_present\"},");
    } else {
        snprintf(depth_json, sizeof(depth_json),
                 "\"depth\":{\"status\":\"not_used\"},");
    }
    char stencil_json[512];
    if (stencil != NULL) {
        snprintf(stencil_json, sizeof(stencil_json),
                 "\"stencil\":{\"status\":\"captured\",\"file\":\"%s\","
                 "\"sha256\":\"%s\",\"bytes\":%zu,\"format\":\"u8\","
                 "\"width\":%d,\"height\":%d}",
                 stencil_file, stencil_sha, actual_stencil, target.width,
                 target.height);
    } else if (!stencil_attachment_present) {
        snprintf(stencil_json, sizeof(stencil_json),
                 "\"stencil\":{\"status\":\"not_present\"}");
    } else {
        snprintf(stencil_json, sizeof(stencil_json),
                 "\"stencil\":{\"status\":\"not_used\"}");
    }
    char colour_json[512];
    if (unsupported_colour) {
        snprintf(colour_json, sizeof(colour_json),
                 "\"color\":{\"status\":\"unsupported_format\","
                 "\"reason\":\"not a comparable BGRA colour format\","
                 "\"internal_format\":%d}",
                 target.internal_format);
    } else {
        snprintf(colour_json, sizeof(colour_json),
                 "\"color\":{\"file\":\"%s\",\"sha256\":\"%s\","
                 "\"bytes\":%zu,\"pixel_format\":\"%s\"}",
                 colour_file, colour_sha, actual_colour, target.pixel_format);
    }
    char pixel_format_json[64];
    if (unsupported_colour || target.pixel_format == NULL) {
        snprintf(pixel_format_json, sizeof(pixel_format_json), "null");
    } else {
        snprintf(pixel_format_json, sizeof(pixel_format_json), "\"%s\"",
                 target.pixel_format);
    }
    char line[FTRACE_PREFIX_LINE_MAX];
    int length = snprintf(
        line, sizeof(line),
        "{\"schema\":\"gfx-observation/v1\",\"event\":%d,"
        "\"timing\":\"%s\",\"kind\":\"%s\",%s,"
        "\"resource\":\"%s\",\"target_name\":\"%s\","
        "\"pixel_format\":%s,\"alpha_applicable\":%s,"
        "\"origin\":\"lower_left\","
        "\"width\":%d,\"height\":%d,\"row_stride\":%d,"
        "\"target_identity\":{\"internal_format\":%d,"
        "\"component_type\":%d,\"red_bits\":%d,\"green_bits\":%d,"
        "\"blue_bits\":%d,\"alpha_bits\":%d,"
        "\"depth_present\":%s,\"stencil_present\":%s},"
        "%s,%s%s,"
        "\"readback_ns\":%llu,\"pid\":%d,"
        "\"session_id\":\"%s\",\"capture_frame_id\":%llu,"
        "\"budget\":{\"used_bytes\":%llu,\"max_bytes\":%llu,"
        "\"used_ns\":%llu,\"max_ns\":%llu}}",
        event, timing, kind, target_json, resource, target_name,
        pixel_format_json,
        unsupported_colour ? "false"
                           : (target.alpha_applicable ? "true" : "false"),
        target.width, target.height, target.width * target.colour_bpp,
        target.internal_format, target.color_component_type,
        target.color_red_bits, target.color_green_bits,
        target.color_blue_bits, target.color_alpha_bits,
        target.depth_present ? "true" : "false",
        target.stencil_present ? "true" : "false",
        colour_json, depth_json, stencil_json,
        (unsigned long long)(now_ns() - started),
        (int)getpid(), g.session_id,
        (unsigned long long)g.capture_id,
        (unsigned long long)(g.prefix_bytes + projected),
        (unsigned long long)g.prefix_max_bytes,
        (unsigned long long)(g.prefix_readback_ns + (now_ns() - started)),
        (unsigned long long)g.prefix_max_ns);
    free(target_json);
    free(colour);
    free(depth);
    free(stencil);
    if (length <= 0 || (size_t)length >= sizeof(line)) {
        fail_capture_locked("prefix_record");
        return -1;
    }
    if (fprintf(g.prefix_observations, "%s\n", line) < 0) {
        fail_capture_locked("prefix_write");
        return -1;
    }
    g.prefix_bytes += projected;
    g.prefix_readback_ns += now_ns() - started;
    if (g.prefix_readback_ns > g.prefix_max_ns) {
        fail_capture_locked("prefix_time");
        return -1;
    }
    if (post) {
        g.prefix_count++;
        if (!g.prefix_sparse) {
            g.prefix_next_event = event + 1;
        }
    } else {
        g.prefix_initial = 1;
    }
    return 0;
}

static int prefix_emit_locked(int event, const char *timing,
                              const char *kind) {
    return prefix_emit_target_locked(event, timing, kind, -1);
}

static int prefix_emit_texture_locked(int event, const char *timing,
                                      const char *kind, int texture_name) {
    return prefix_emit_target_locked(event, timing, kind, texture_name);
}

static void capture_draw_state(json_out *out, int indexed, GLint mode, int first,
                               int count, int instance_count, int base_vertex,
                               uintptr_t master,
                               const eu4_gfx_observe_call *call) {
    const int selected = g.prefix_caller != 0 && g.prefix_caller == call->caller_rva;
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
    int framebuffer_srgb_known = srgb_extension_supported();
    GLint framebuffer_srgb = framebuffer_srgb_known
        ? (glIsEnabled(GL_FRAMEBUFFER_SRGB) ? 1 : 0) : -1;
    GLfloat depth_range[2] = {0.0f, 1.0f};
    glGetFloatv(GL_DEPTH_RANGE, depth_range);
    GLint logic_op = 0, color_logic_op = 0;
    glGetIntegerv(GL_LOGIC_OP_MODE, &logic_op);
    glGetIntegerv(GL_COLOR_LOGIC_OP, &color_logic_op);
    GLint vp[4] = {0, 0, 0, 0};
    glGetIntegerv(GL_VIEWPORT, vp);

    jprintf(out,
            "\"state\":{\"blend\":{\"enabled\":%s,\"src_rgb\":\"0x%x\","
            "\"dst_rgb\":\"0x%x\",\"src_alpha\":\"0x%x\",\"dst_alpha\":\"0x%x\","
            "\"equation_rgb\":\"0x%x\",\"equation_alpha\":\"0x%x\"},"
            "\"color_mask\":[%d,%d,%d,%d],"
            "\"depth\":{\"test\":%s,\"func\":\"0x%x\",\"write\":%s,"
            "\"range\":[%.9g,%.9g]},"
            "\"stencil\":{\"test\":%s,\"func\":\"0x%x\",\"ref\":%d,"
            "\"value_mask\":%d,\"fail\":\"0x%x\",\"zfail\":\"0x%x\",\"zpass\":\"0x%x\","
            "\"write_mask\":%d},"
            "\"cull\":{\"enabled\":%s,\"mode\":\"0x%x\",\"front\":\"0x%x\"},"
            "\"polygon\":{\"mode\":[\"0x%x\",\"0x%x\"],\"offset_fill\":%s,"
            "\"offset_factor\":%.9g,\"offset_units\":%.9g},"
            "\"alpha_test\":{\"enabled\":%s,\"func\":\"0x%x\",\"ref\":%.9g},"
            "\"scissor\":{\"enabled\":%s,\"box\":[%d,%d,%d,%d]},"
            "\"multisample\":%s,\"dither\":%s,"
            "\"framebuffer_srgb\":%s,"
            "\"framebuffer_srgb_known\":%s,"
            "\"logic_op\":{\"enabled\":%s,\"mode\":\"0x%x\"},"
            "\"viewport\":[%d,%d,%d,%d]}",
            bool_text(blend != 0), blend_src_rgb, blend_dst_rgb, blend_src_a,
            blend_dst_a, blend_eq_rgb, blend_eq_a, color_mask[0] ? 1 : 0,
            color_mask[1] ? 1 : 0, color_mask[2] ? 1 : 0, color_mask[3] ? 1 : 0,
            bool_text(depth_test != 0), depth_func, bool_text(depth_mask != 0),
            (double)depth_range[0], (double)depth_range[1],
            bool_text(stencil_test != 0), stencil_func, stencil_ref,
            stencil_value_mask, stencil_fail, stencil_zfail, stencil_zpass,
            stencil_write_mask, bool_text(cull != 0), cull_mode, front_face,
            polygon_mode[0], polygon_mode[1],
            bool_text(polygon_offset_fill != 0), (double)polygon_offset_factor,
            (double)polygon_offset_units, bool_text(alpha_test != 0), alpha_func,
            (double)alpha_ref, bool_text(scissor_test != 0), scissor_box[0],
            scissor_box[1], scissor_box[2], scissor_box[3],
            bool_text(multisample != 0), bool_text(dither != 0),
            framebuffer_srgb < 0 ? "null"
                                 : bool_text(framebuffer_srgb != 0),
            bool_text(framebuffer_srgb_known != 0),
            bool_text(color_logic_op != 0), logic_op, vp[0], vp[1], vp[2],
            vp[3]);

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
    const char *cached_shaders = selected ? NULL : shader_cache_get((uint64_t)program);
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
                if (!selected) shader_cache_put((uint64_t)program, shader_json);
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
        char texture_name[64];
        snprintf(texture_name, sizeof(texture_name), "%d", binding);
        const char *cached_levels =
            selected || render_target_known(texture_name)
                ? NULL
                : texture_cache_get(target, (unsigned)binding);
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
                    if (!selected && !render_target_known(texture_name)) {
                        texture_cache_put(target, (unsigned)binding,
                                          levels_json);
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
                           instance_count, base_vertex, selected ? 0 : master, 0, &attr) != 0) {
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

/* Records the live default drawable's effective pixel format (issue #278).
   A value the driver cannot expose is emitted as "unavailable:<reason>"; the
   requested CGL attribute is never substituted for the effective one. */
static void write_cgl_drawable_state(FILE *file) {
    CGLContextObj context = CGLGetCurrentContext();
    if (context == NULL) {
        fputs("\"cgl\":{\"status\":\"unavailable:no_current_context\"},"
              "\"default_drawable\":{\"status\":\"unavailable:no_context\"},",
              file);
        return;
    }
    CGLPixelFormatObj format = CGLGetPixelFormat(context);
    if (format == NULL) {
        fputs("\"cgl\":{\"status\":\"unavailable:no_pixel_format\"},"
              "\"default_drawable\":{\"status\":\"unavailable:no_context\"},",
              file);
        return;
    }
    fputs("\"cgl\":{\"status\":\"ok\",", file);
    const struct {
        CGLPixelFormatAttribute attribute;
        const char *name;
    } values[] = {
        {kCGLPFAColorSize, "color_size"},
        {kCGLPFAAlphaSize, "alpha_size"},
        {kCGLPFADepthSize, "depth_size"},
        {kCGLPFAStencilSize, "stencil_size"},
        {kCGLPFASampleBuffers, "sample_buffers"},
        {kCGLPFASamples, "samples"},
        {kCGLPFAAccelerated, "accelerated"},
        {kCGLPFADoubleBuffer, "double_buffer"},
        {kCGLPFAStereo, "stereo"},
        {kCGLPFARendererID, "renderer_id"},
    };
    for (size_t index = 0; index < sizeof(values) / sizeof(values[0]); index++) {
        GLint value = 0;
        CGLError error = CGLDescribePixelFormat(format, 0, values[index].attribute,
                                                &value);
        if (error == kCGLNoError) {
            fprintf(file, "\"%s\":%d,", values[index].name, (int)value);
        } else {
            fprintf(file, "\"%s\":\"unavailable:cgl_error_%d\",",
                    values[index].name, (int)error);
        }
    }
    GLint profile = 0;
    CGLError profile_error = CGLDescribePixelFormat(
        format, 0, kCGLPFAOpenGLProfile, &profile);
    if (profile_error != kCGLNoError) {
        fputs("\"profile\":\"unavailable:cgl_error\"", file);
    } else if (profile == kCGLOGLPVersion_3_2_Core) {
        fputs("\"profile\":\"3.2_core\"", file);
    } else if (profile == kCGLOGLPVersion_Legacy) {
        fputs("\"profile\":\"legacy\"", file);
    } else {
        fprintf(file, "\"profile\":\"0x%x\"", (unsigned)profile);
    }
    /* The default drawable's attachments are only valid while framebuffer
       zero is selected.  A timeout or incomplete-capture path can leave a
       user FBO bound, so record the current bindings, query the default
       framebuffer explicitly, and restore both targets afterwards. */
    GLint binding = 0, previous_read_binding = 0;
    glGetIntegerv(GL_DRAW_FRAMEBUFFER_BINDING, &binding);
    glGetIntegerv(GL_READ_FRAMEBUFFER_BINDING, &previous_read_binding);
    glBindFramebuffer(GL_FRAMEBUFFER, 0);
    GLenum default_status = glCheckFramebufferStatus(GL_FRAMEBUFFER);
    if (default_status != GL_FRAMEBUFFER_COMPLETE) {
        fprintf(file,
                "},\"default_drawable\":{\"status\":"
                "\"unavailable:incomplete_0x%x\"},",
                (unsigned)default_status);
        glBindFramebuffer(GL_DRAW_FRAMEBUFFER, (GLuint)binding);
        glBindFramebuffer(GL_READ_FRAMEBUFFER, (GLuint)previous_read_binding);
        return;
    }
    fputs("},\"default_drawable\":{\"status\":\"ok\",", file);
    GLint samples = 0, sample_buffers = 0;
    GLint draw_buffer = 0, read_buffer = 0;
    glGetIntegerv(GL_SAMPLES, &samples);
    glGetIntegerv(GL_SAMPLE_BUFFERS, &sample_buffers);
    glGetIntegerv(GL_DRAW_BUFFER, &draw_buffer);
    glGetIntegerv(GL_READ_BUFFER, &read_buffer);
    fprintf(file,
            "\"draw_framebuffer_binding\":%d,\"samples\":%d,"
            "\"sample_buffers\":%d,\"draw_buffer\":\"0x%x\","
            "\"read_buffer\":\"0x%x\",\"render_target_path\":\"%s\",",
            (int)binding, (int)samples, (int)sample_buffers,
            (unsigned)draw_buffer, (unsigned)read_buffer,
            binding == 0 ? "default-window" : "fbo");
    const struct {
        GLenum attachment;
        const char *name;
    } attachments[] = {
        {GL_BACK_LEFT, "color_back_left"},
        {GL_DEPTH, "depth"},
        {GL_STENCIL, "stencil"},
    };
    int identity_supported = framebuffer_attachment_identity_supported();
    for (size_t index = 0;
         index < sizeof(attachments) / sizeof(attachments[0]); index++) {
        GLint type = 0;
        glGetFramebufferAttachmentParameteriv(
            GL_FRAMEBUFFER, attachments[index].attachment,
            GL_FRAMEBUFFER_ATTACHMENT_OBJECT_TYPE, &type);
        fprintf(file, "\"%s\":{\"object_type\":", attachments[index].name);
        if (type == GL_NONE) {
            fputs("\"absent\"", file);
        } else if (type == GL_FRAMEBUFFER_DEFAULT) {
            fputs("\"framebuffer_default\"", file);
        } else if (type == GL_TEXTURE) {
            fputs("\"texture\"", file);
        } else if (type == GL_RENDERBUFFER) {
            fputs("\"renderbuffer\"", file);
        } else {
            fprintf(file, "\"0x%x\"", (unsigned)type);
        }
        if (!identity_supported) {
            fputs(",\"component_type\":\"unavailable:unsupported\"", file);
            fputs("}", file);
        } else {
            GLint component = 0;
            glGetFramebufferAttachmentParameteriv(
                GL_FRAMEBUFFER, attachments[index].attachment,
                GL_FRAMEBUFFER_ATTACHMENT_COMPONENT_TYPE, &component);
            fputs(",\"component_type\":", file);
            switch ((GLenum)component) {
                case GL_FLOAT: fputs("\"float\"", file); break;
                case GL_INT: fputs("\"int\"", file); break;
                case GL_UNSIGNED_INT: fputs("\"unsigned_int\"", file); break;
                case GL_SIGNED_NORMALIZED:
                    fputs("\"signed_normalized\"", file); break;
                case GL_UNSIGNED_NORMALIZED:
                    fputs("\"unsigned_normalized\"", file); break;
                default:
                    fprintf(file, "\"0x%x\"", (unsigned)component); break;
            }
            const struct {
                GLenum parameter;
                const char *name;
            } sizes[] = {
                {GL_FRAMEBUFFER_ATTACHMENT_RED_SIZE, "red_size"},
                {GL_FRAMEBUFFER_ATTACHMENT_GREEN_SIZE, "green_size"},
                {GL_FRAMEBUFFER_ATTACHMENT_BLUE_SIZE, "blue_size"},
                {GL_FRAMEBUFFER_ATTACHMENT_ALPHA_SIZE, "alpha_size"},
                {GL_FRAMEBUFFER_ATTACHMENT_DEPTH_SIZE, "depth_size"},
                {GL_FRAMEBUFFER_ATTACHMENT_STENCIL_SIZE, "stencil_size"},
            };
            for (size_t size_index = 0;
                 size_index < sizeof(sizes) / sizeof(sizes[0]); size_index++) {
                GLint value = 0;
                glGetFramebufferAttachmentParameteriv(
                    GL_FRAMEBUFFER, attachments[index].attachment,
                    sizes[size_index].parameter, &value);
                fprintf(file, ",\"%s\":%d", sizes[size_index].name,
                        (int)value);
            }
            fputs("}", file);
        }
        if (index + 1 < sizeof(attachments) / sizeof(attachments[0])) {
            fputs(",", file);
        }
    }
    fputs("},", file);
    glBindFramebuffer(GL_DRAW_FRAMEBUFFER, (GLuint)binding);
    glBindFramebuffer(GL_READ_FRAMEBUFFER, (GLuint)previous_read_binding);
}

/* Emits the observation coverage block.  The observation file is the one
   authoritative record; the manifest references it by digest, byte count and
   record count instead of duplicating every record. */
static void write_prefix_manifest(FILE *file) {
    int complete = prefix_complete_locked();
    char end_text[32];
    if (g.prefix_sparse) {
        snprintf(end_text, sizeof(end_text), "null");
    } else {
        snprintf(end_text, sizeof(end_text), "%d", g.prefix_end);
    }
    fprintf(file,
            ",\"prefix\":{\"schema\":\"gfx-observations/v1\","
            "\"mode\":\"%s\",\"authority\":\"%s\","
            "\"end\":%s,\"status\":\"%s\",\"count\":%d,"
            "\"post_event_count\":%d,\"initial\":%s,"
            "\"depth_stencil\":%s,"
            "\"observation_file\":\"%s\","
            "\"observation_sha256\":\"%s\","
            "\"observation_bytes\":%llu,",
            g.prefix_sparse ? "selected" : "prefix",
            g.test_only ? "synthetic" : "authentic",
            end_text, complete ? "complete" : "incomplete", g.prefix_count,
            g.prefix_count,
            (!g.prefix_sparse && g.prefix_initial) ? "true" : "false",
            g.prefix_depth_stencil ? "true" : "false",
            g.prefix_file, g.prefix_sha,
            (unsigned long long)g.prefix_file_bytes);
    if (g.prefix_sparse) {
        fputs("\"events\":[", file);
        for (int index = 0; index < g.prefix_event_count; index++) {
            fprintf(file, "%s%d", index == 0 ? "" : ",",
                    g.prefix_events[index]);
        }
        fputs("],", file);
    }
    fprintf(file,
            "\"budget\":{\"max_bytes\":%llu,\"used_bytes\":%llu,"
            "\"max_ns\":%llu,\"used_ns\":%llu}}",
            (unsigned long long)g.prefix_max_bytes,
            (unsigned long long)g.prefix_bytes,
            (unsigned long long)g.prefix_max_ns,
            (unsigned long long)g.prefix_readback_ns);
}

static int write_frame_manifest(int complete, const char *reason) {
    if (g_uv2_pixels.count != 0 && g_uv2_sparse_sha[0] == 0)
        uv2_probe_write_failure("event_not_observed", "sparse event or readback was unavailable");
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
#if defined(__x86_64__)
    fputs("\"process_architecture\":\"x86_64\",", file);
#elif defined(__aarch64__)
    fputs("\"process_architecture\":\"arm64\",", file);
#endif
    const char *executable_sha = getenv("EU4_GFX_EXECUTABLE_SHA256");
    if (executable_sha && strlen(executable_sha) == 64 &&
        strspn(executable_sha, "0123456789abcdef") == 64)
        fprintf(file, "\"executable_sha256\":\"%s\",", executable_sha);
    char macos_build[64] = {0};
    size_t build_size = sizeof(macos_build);
    if (sysctlbyname("kern.osversion", macos_build, &build_size, NULL, 0) == 0 &&
        build_size > 1 && build_size <= sizeof(macos_build) &&
        strspn(macos_build, "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz") == strlen(macos_build))
        fprintf(file, "\"macos_build\":\"%s\",", macos_build);
    fprintf(file, "\"complete\":%s,", bool_text(complete));
    fprintf(file, "\"reason\":\"%s\",", reason != NULL ? reason : "");
    fprintf(file, "\"truncated\":%s,", bool_text(g.truncated));
    fprintf(file, "\"test_only\":%s,", bool_text(g.test_only));
    fprintf(file, "\"stream_validated\":%s,", bool_text(g.stream_validated));
    fprintf(file, "\"pid\":%d,", (int)getpid());
    fprintf(file, "\"present_index\":%d,", g.capturing_frame);
    fprintf(file, "\"scene_index\":%d,",
            g.boundary_scene ? g.capturing_frame : g.scene_index);
    fprintf(file, "\"events\":%d,", g.event_count);
    fputs("\"default_checkpoint_events\":[", file);
    for (int index = 0; index < g.checkpoint_count; index++) {
        fprintf(file, "%s%d", index == 0 ? "" : ",",
                g.checkpoint_events[index]);
    }
    fputs("],", file);
    fprintf(file, "\"default_checkpoint_count\":%d,", g.checkpoint_count);
    if (g.checkpoint_count == 1) {
        fprintf(file, "\"default_checkpoint_event\":%d,",
                g.checkpoint_events[0]);
    } else {
        /* The legacy singular field is only meaningful for one selection. */
        fputs("\"default_checkpoint_event\":null,", file);
    }
    fprintf(file, "\"capacity_exceeded\":%s,", bool_text(g.capacity_exceeded));
    fprintf(file, "\"capacity_limit\":\"%s\",", g.capacity_limit);
    fprintf(file, "\"texture_cache\":{\"reads\":%llu,\"hits\":%llu,"
                  "\"invalidations\":%llu},",
            (unsigned long long)g.texture_cache_reads,
            (unsigned long long)g.texture_cache_hits,
            (unsigned long long)g.texture_cache_invalidations);
    fputs("\"gl\":{", file);
    fprintf(file, "\"vendor\":\"%s\",", vendor != NULL ? vendor : "");
    fprintf(file, "\"renderer\":\"%s\",", renderer != NULL ? renderer : "");
    fprintf(file, "\"version\":\"%s\",", version != NULL ? version : "");
    if (g.gl_drawable_valid) {
        fprintf(file,
                "\"drawable\":{\"samples\":%d,\"sample_buffers\":%d,"
                "\"depth_bits\":%d,\"stencil_bits\":%d,"
                "\"red_bits\":%d,\"green_bits\":%d,\"blue_bits\":%d,"
                "\"alpha_bits\":%d,\"double_buffer\":%s,\"stereo\":%s,"
                "\"drawn_framebuffer_binding\":%d,"
                "\"framebuffer_srgb\":%s,\"framebuffer_srgb_known\":%s,"
                "\"dither\":%s,\"multisample\":%s,"
                "\"draw_buffer\":\"0x%x\",\"read_buffer\":\"0x%x\"},",
                g.gl_samples, g.gl_sample_buffers, g.gl_depth_bits,
                g.gl_stencil_bits, g.gl_red_bits, g.gl_green_bits,
                g.gl_blue_bits, g.gl_alpha_bits,
                bool_text(g.gl_double_buffer), bool_text(g.gl_stereo),
                g.gl_drawable_binding,
                g.gl_framebuffer_srgb < 0 ? "null"
                    : bool_text(g.gl_framebuffer_srgb),
                bool_text(g.gl_framebuffer_srgb >= 0),
                bool_text(g.gl_dither), bool_text(g.gl_multisample),
                g.gl_draw_buffer, g.gl_read_buffer);
    }
    write_cgl_drawable_state(file);
    fprintf(file, "\"drawable_observed\":%s},", bool_text(g.gl_drawable_valid));
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
    fprintf(file, "\"blobs\":%d", g.blob_count);
    if (g.prefix_enabled) {
        write_prefix_manifest(file);
    }
    if (g_uv2_pixels.count != 0) {
        fprintf(file, ",\"uv2_fragments\":{\"file\":\""
                EU4_UV2_SPARSE_FILE "\",\"sha256\":\"%s\"}", g_uv2_sparse_sha);
    }
    fputs("}\n", file);
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

static void write_name_array(FILE *stream, char names[][64], int count) {
    for (int i = 0; i < count; i++) {
        fprintf(stream, "%s\"%s\"", i > 0 ? "," : "", names[i]);
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
    char *previous = NULL;
    size_t previous_size = 0;
    FILE *previous_stream = open_memstream(&previous, &previous_size);
    if (previous_stream == NULL) {
        return -1;
    }
    if (g.previous_capture_id != 0) {
        fprintf(previous_stream,
                "{\"capture_frame_id\":%llu,\"dir\":\"%s\","
                "\"manifest_sha256\":\"%s\",\"event_stream_sha256\":\"%s\"}",
                (unsigned long long)g.previous_capture_id, g.previous_dir,
                g.previous_manifest_sha, g.previous_events_sha);
    } else {
        fputs("null", previous_stream);
    }
    fclose(previous_stream);
    if (previous == NULL) {
        return -1;
    }
    char *targets = NULL;
    size_t targets_size = 0;
    FILE *targets_stream = open_memstream(&targets, &targets_size);
    if (targets_stream == NULL) {
        free(previous);
        return -1;
    }
    write_name_array(targets_stream, g.render_targets,
                     g.render_target_count);
    fclose(targets_stream);
    char *carried = NULL;
    size_t carried_size = 0;
    FILE *carried_stream = open_memstream(&carried, &carried_size);
    if (carried_stream == NULL) {
        free(previous);
        free(targets);
        return -1;
    }
    write_name_array(carried_stream, g.previous_render_targets,
                     g.previous_render_target_count);
    fclose(carried_stream);
    char *lifecycle = NULL;
    size_t lifecycle_size = 0;
    FILE *lifecycle_stream = open_memstream(&lifecycle, &lifecycle_size);
    if (lifecycle_stream == NULL) {
        free(previous);
        free(targets);
        free(carried);
        return -1;
    }
    /* Deletes and mutations share one monotonic sequence, so the recorded
       call order survives even when several transitions land on the same
       captured event index.  Emitting all deletes before all mutations (the
       old behaviour) folded a distinct delete/recreate/delete sequence into a
       single transition and let replay reuse the wrong resource generation. */
    enum { kLifecycleDelete = 0, kLifecycleMutation = 1 };
    typedef struct {
        int kind;
        int event;
        unsigned long long sequence;
        int start;
        int count;
    } lifecycle_record;
    lifecycle_record records[FTRACE_MAX_LIFECYCLE * 2];
    int record_count = 0;
    for (int i = 0; i < g.pending_delete_count; i++) {
        int event_index = g.pending_delete_events[i];
        unsigned long long sequence = g.pending_delete_sequences[i];
        if (i > 0 &&
            g.pending_delete_events[i - 1] == event_index &&
            g.pending_delete_sequences[i - 1] == sequence) {
            /* Duplicate names inside one glDeleteTextures call are the same
               transition; a second independent call always has a new
               sequence even at the same event index. */
            records[record_count - 1].count++;
            continue;
        }
        if (record_count >= (int)(sizeof(records) / sizeof(records[0]))) {
            capacity_exceeded("lifecycle_records");
            break;
        }
        records[record_count].kind = kLifecycleDelete;
        records[record_count].event = event_index;
        records[record_count].sequence = sequence;
        records[record_count].start = i;
        records[record_count].count = 1;
        record_count++;
    }
    for (int i = 0; i < g.pending_mutation_count; i++) {
        if (record_count >= (int)(sizeof(records) / sizeof(records[0]))) {
            capacity_exceeded("lifecycle_records");
            break;
        }
        records[record_count].kind = kLifecycleMutation;
        records[record_count].event = g.pending_mutation_events[i];
        records[record_count].sequence = g.pending_mutation_sequences[i];
        records[record_count].start = i;
        records[record_count].count = 1;
        record_count++;
    }
    for (int i = 1; i < record_count; i++) {
        lifecycle_record value = records[i];
        int j = i - 1;
        while (j >= 0 &&
               (records[j].event > value.event ||
                (records[j].event == value.event &&
                 records[j].sequence > value.sequence))) {
            records[j + 1] = records[j];
            j--;
        }
        records[j + 1] = value;
    }
    int lifecycle_first = 1;
    for (int i = 0; i < record_count; i++) {
        lifecycle_record *record = &records[i];
        if (record->kind == kLifecycleDelete) {
            fprintf(lifecycle_stream,
                    "%s{\"kind\":\"delete\",\"event\":%d,"
                    "\"lifecycle_sequence\":%llu,\"names\":[",
                    lifecycle_first ? "" : ",", record->event,
                    record->sequence);
            lifecycle_first = 0;
            int first = 1;
            for (int j = record->start;
                 j < record->start + record->count; j++) {
                fprintf(lifecycle_stream, "%s\"%s\"", first ? "" : ",",
                        g.pending_deletes[j]);
                first = 0;
            }
            fputs("]}", lifecycle_stream);
            continue;
        }
        int index = record->start;
        fprintf(lifecycle_stream,
                "%s{\"kind\":\"mutation\",\"operation\":\"%s\","
                "\"target\":\"0x%x\",\"event\":%d,"
                "\"lifecycle_sequence\":%llu,\"level\":%d,"
                "\"names\":[\"%s\"]}",
                lifecycle_first ? "" : ",", g.pending_mutation_ops[index],
                g.pending_mutation_targets[index],
                g.pending_mutation_events[index],
                g.pending_mutation_sequences[index],
                g.pending_mutation_levels[index],
                g.pending_mutations[index]);
        lifecycle_first = 0;
    }
    fclose(lifecycle_stream);
    if (targets == NULL || carried == NULL || lifecycle == NULL) {
        free(previous);
        free(targets);
        free(carried);
        free(lifecycle);
        return -1;
    }
    char *buffer = NULL;
    size_t size = 0;
    FILE *stream = open_memstream(&buffer, &size);
    if (stream == NULL) {
        free(previous);
        free(targets);
        free(carried);
        free(lifecycle);
        return -1;
    }
    fprintf(stream,
            "{\"schema\":\"gfx-frame-sequence/v1\","
            "\"session_id\":\"%s\",\"pid\":%d,"
            "\"sequence_index\":%u,\"campaign_frames\":%u,"
            "\"capture_frame_id\":%llu,"
            "\"present_index\":%u,\"scene_index\":%u,"
            "\"complete\":%s,"
            "\"boundary\":\"%s\",\"reason\":\"%s\","
            "\"previous\":%s,"
            "\"render_targets\":[%s],"
            "\"carried_renderer_outputs\":[%s],"
            "\"lifecycle\":[%s],"
            "\"texture_cache\":{\"reads\":%llu,\"hits\":%llu,"
            "\"invalidations\":%llu},"
            "\"reference_sha256\":\"%s\","
            "\"manifest_sha256\":\"%s\","
            "\"event_stream_sha256\":\"%s\","
            "\"operations\":%llu,\"draws\":%llu,\"clears\":%llu,"
            "\"copies\":%llu}\n",
            g.session_id, (int)getpid(), g.continuous_frames_completed + 1,
            g.continuous_max_frames,
            (unsigned long long)descriptor->capture_frame_id,
            descriptor->present_index, descriptor->scene_index,
            complete ? "true" : "false", descriptor->boundary,
            reason ? reason : "", previous, targets, carried, lifecycle,
            (unsigned long long)g.texture_cache_reads,
            (unsigned long long)g.texture_cache_hits,
            (unsigned long long)g.texture_cache_invalidations,
            descriptor->reference_sha256, descriptor->manifest_sha256,
            descriptor->event_stream_sha256,
            (unsigned long long)descriptor->operation_count,
            (unsigned long long)descriptor->draw_count,
            (unsigned long long)descriptor->clear_count,
            (unsigned long long)descriptor->copy_count);
    fclose(stream);
    free(previous);
    free(targets);
    free(carried);
    free(lifecycle);
    int result = -1;
    if (buffer != NULL) {
        result = write_atomic(path, buffer, size);
        free(buffer);
    }
    if (result == 0) {
        g.pending_delete_count = 0;
        g.pending_mutation_count = 0;
        g.lifecycle_sequence = 0;
    }
    return result;
}

static void continuous_record_frame(
    const eu4_frame_capture_descriptor *descriptor) {
    if (!g.continuous || descriptor == NULL) {
        return;
    }
    if (g.campaign_record_count >=
        (int)(sizeof(g.campaign_records) / sizeof(g.campaign_records[0]))) {
        capacity_exceeded("campaign_records");
        return;
    }
    {
        snprintf(g.campaign_records[g.campaign_record_count],
                 sizeof(g.campaign_records[0]),
                 "{\"sequence\":%u,\"capture_frame_id\":%llu,"
                 "\"present_index\":%u,\"scene_index\":%u,"
                 "\"dir\":\"%s\",\"manifest_sha256\":\"%s\","
                 "\"event_stream_sha256\":\"%s\","
                 "\"reference_sha256\":\"%s\",\"operations\":%llu,"
                 "\"draws\":%llu,\"clears\":%llu,\"copies\":%llu}",
                 g.continuous_frames_completed + 1,
                 (unsigned long long)descriptor->capture_frame_id,
                 descriptor->present_index, descriptor->scene_index, g.dir,
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
    if (g.prefix_enabled) {
        if (g.prefix_observations != NULL) {
            int close_failed = ferror(g.prefix_observations) != 0 ||
                               fflush(g.prefix_observations) != 0;
            int descriptor = fileno(g.prefix_observations);
            if (!close_failed && descriptor >= 0) {
                close_failed = fsync(descriptor) != 0;
            }
            if (fclose(g.prefix_observations) != 0) {
                close_failed = 1;
            }
            g.prefix_observations = NULL;
            if (close_failed) {
                fail_capture_locked("prefix_close");
            }
        }
        char prefix_path[FTRACE_PATH];
        struct stat prefix_stat;
        if (snprintf(prefix_path, sizeof(prefix_path), "%s/%s", g.dir,
                     g.prefix_file) >= (int)sizeof(prefix_path) ||
            stat(prefix_path, &prefix_stat) != 0 ||
            prefix_stat.st_size <= 0 ||
            file_sha256(prefix_path, g.prefix_sha) != 0) {
            fail_capture_locked("prefix_file");
        } else {
            g.prefix_file_bytes = (uint64_t)prefix_stat.st_size;
        }
        if (!prefix_complete_locked()) {
            fail_capture_locked("prefix_incomplete");
        }
    }
    /* Every requested checkpoint must have reached a supported
       pre-operation boundary exactly once; a missed one is a capture
       failure, not a frame that can be qualified. */
    for (int index = 0; index < g.checkpoint_count; index++) {
        if (!g.checkpoint_captured[index]) {
            fail_capture_locked("default_checkpoint_missed");
            break;
        }
    }
    /* A checkpoint captured at a boundary that finalizes before the selected
       event record is emitted has no event to attach to.  Never publish an
       unreferenced blob as if it belonged to the selected event. */
    if (g.checkpoint_pending >= 0) {
        fail_capture_locked("default_checkpoint_unattached");
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
    if (g.continuous && !complete) {
        /* A frame that cannot be sealed must stop the campaign explicitly.
           Re-arming the same frame index would rewrite the same directory
           until an external timeout and hide the real reason. */
        const char *stop = g.capacity_exceeded ? "resource_limit"
                                               : "frame_incomplete";
        continuous_set_stopped(stop);
        continuous_write_campaign("incomplete", stop);
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
            if (g.continuous_frames_completed >=
                       g.continuous_max_frames) {
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
        const char *stop_file = getenv("EU4_GFX_FRAME_TRACE_STOP_FILE");
        if (stop_file != NULL && stop_file[0] != 0 &&
            access(stop_file, F_OK) == 0) {
            /* An explicit external stop must stop capture at the next frame
               boundary instead of relying only on the frame/byte budget. */
            continuous_set_stopped("external_stop");
            continuous_write_campaign("complete", "external_stop");
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
    int carry_overflow = 0;
    char carry_limit[32] = "";
    if (g.continuous) {
        /* Merge the preceding frame's writes into the persistent carried set
           before this frame's per-frame target list is discarded.  An
           overflow here fails this frame closed instead of dropping a
           resource from its carried declaration. */
        g.capacity_exceeded = 0;
        g.capacity_limit[0] = 0;
        continuous_copy_targets();
        carry_overflow = g.capacity_exceeded;
        snprintf(carry_limit, sizeof(carry_limit), "%s", g.capacity_limit);
    }
    frame_caches_reset();
    g.texture_cache_reads = 0;
    g.texture_cache_hits = 0;
    g.texture_cache_invalidations = 0;
    g.capacity_exceeded = 0;
    g.capacity_limit[0] = 0;
    g.captured = 0;
    g.armed = 1;
    g.armed_ms = now_ms();
    g.capturing_frame = next_frame;
    g.capture_id = ++g.next_capture_id;
    g.event_count = 0;
    g.draw_events = 0;
    g.clear_events = 0;
    g.copy_events = 0;
    for (int index = 0; index < FTRACE_MAX_CHECKPOINTS; index++) {
        g.checkpoint_captured[index] = 0;
        g.checkpoint_ns[index] = 0;
        g.checkpoint_bytes[index] = 0;
        g.checkpoint_file[index][0] = 0;
        g.checkpoint_depth_file[index][0] = 0;
        g.checkpoint_stencil_file[index][0] = 0;
        g.checkpoint_depth_bytes[index] = 0;
        g.checkpoint_stencil_bytes[index] = 0;
        g.checkpoint_json[index][0] = 0;
    }
    g.checkpoint_total_bytes = 0;
    g.checkpoint_pending = -1;
    g.last_capture_target_fb = 0;
    g.test_target_fb_override = -1;
    g.test_target_internal_override = -1;
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
    if (carry_overflow) {
        g.capacity_exceeded = 1;
        snprintf(g.capacity_limit, sizeof(g.capacity_limit), "%s",
                 carry_limit);
        fail_capture_locked("resource_limit");
    }
    if (g.continuous) {
        continuous_snapshot_carried();
    }
    g.have_seed = 0;
    g.seed_emitted = 0;
    g.seed_json[0] = 0;
    g.seed_width = 0;
    g.seed_height = 0;
    open_events();
    if (g.prefix_enabled) {
        prefix_reset_locked();
        snprintf(g.prefix_file, sizeof(g.prefix_file), "%s",
                 "prefix-observations.jsonl");
        char prefix_path[FTRACE_PATH];
        if (snprintf(prefix_path, sizeof(prefix_path), "%s/%s", g.dir,
                     g.prefix_file) >= (int)sizeof(prefix_path)) {
            fail_capture_locked("prefix_open");
        } else {
            g.prefix_observations = fopen(prefix_path, "w");
            if (g.prefix_observations == NULL) {
                fail_capture_locked("prefix_open");
            } else if (!g.prefix_sparse &&
                       prefix_emit_locked(0, "pre_event", "initial") != 0) {
                g.prefix_failed = 1;
            }
        }
    }
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

/* glIsEnabled with an unsupported enum plants a GL error in the engine's
   queue, which this observer must never do.  The sRGB enable is therefore
   queried only when the driver advertises the extension. */
/* ARB_framebuffer_object adds attachment component/size queries.  On a driver
   that lacks it those enums raise a GL error, so they are never queried: the
   observer must not alter the engine's error queue. */
static int framebuffer_attachment_identity_supported(void) {
    static int cached = -1;
    if (cached < 0) {
        const char *version = (const char *)glGetString(GL_VERSION);
        const char *extensions = (const char *)glGetString(GL_EXTENSIONS);
        cached = 0;
        if (version != NULL && atoi(version) >= 3) {
            cached = 1;
        } else if (extensions != NULL &&
                   strstr(extensions, "GL_ARB_framebuffer_object") != NULL) {
            cached = 1;
        }
    }
    return cached;
}

static int srgb_extension_supported(void) {
    static int cached = -1;
    if (cached < 0) {
        const char *extensions = (const char *)glGetString(GL_EXTENSIONS);
        cached = 0;
        if (extensions != NULL &&
            (strstr(extensions, "GL_EXT_framebuffer_sRGB") != NULL ||
             strstr(extensions, "GL_ARB_framebuffer_sRGB") != NULL)) {
            cached = 1;
        }
    }
    return cached;
}

static void query_drawable_state(ftrace_drawable_state *out) {
    memset(out, 0, sizeof(*out));
    GLint value = 0;
    glGetIntegerv(GL_DRAW_FRAMEBUFFER_BINDING, &value);
    out->binding = (int)value;
    glGetIntegerv(GL_SAMPLES, &value);
    out->samples = (int)value;
    glGetIntegerv(GL_SAMPLE_BUFFERS, &value);
    out->sample_buffers = (int)value;
    glGetIntegerv(GL_DEPTH_BITS, &value);
    out->depth_bits = (int)value;
    glGetIntegerv(GL_STENCIL_BITS, &value);
    out->stencil_bits = (int)value;
    glGetIntegerv(GL_RED_BITS, &value);
    out->red_bits = (int)value;
    glGetIntegerv(GL_GREEN_BITS, &value);
    out->green_bits = (int)value;
    glGetIntegerv(GL_BLUE_BITS, &value);
    out->blue_bits = (int)value;
    glGetIntegerv(GL_ALPHA_BITS, &value);
    out->alpha_bits = (int)value;
    glGetIntegerv(GL_DOUBLEBUFFER, &value);
    out->double_buffer = (int)value;
    glGetIntegerv(GL_STEREO, &value);
    out->stereo = (int)value;
    glGetIntegerv(GL_DRAW_BUFFER, &value);
    out->draw_buffer = (int)value;
    glGetIntegerv(GL_READ_BUFFER, &value);
    out->read_buffer = (int)value;
    out->framebuffer_srgb_known = srgb_extension_supported();
    out->framebuffer_srgb = out->framebuffer_srgb_known
        ? (glIsEnabled(GL_FRAMEBUFFER_SRGB) ? 1 : 0) : -1;
    out->dither = glIsEnabled(GL_DITHER) ? 1 : 0;
    out->multisample = glIsEnabled(GL_MULTISAMPLE) ? 1 : 0;
    out->valid = out->binding == 0;
}

typedef struct {
    GLint draw_framebuffer;
    GLint read_framebuffer;
    GLint read_buffer;
    GLint texture_binding;
    GLint pack_alignment;
    GLint pack_row;
    GLint pack_skip_rows;
    GLint pack_skip_pixels;
    GLint pack_buffer;
    GLboolean pack_swap_bytes;
    GLboolean pack_lsb_first;
    int texture_bound;
    int draw_bound;
} observation_readback_guard;

static void end_observation_readback(const observation_readback_guard *guard) {
    if (guard->texture_bound) {
        glBindTexture(GL_TEXTURE_2D, (GLuint)guard->texture_binding);
    } else {
        glBindFramebuffer(GL_READ_FRAMEBUFFER, (GLuint)guard->read_framebuffer);
        if (guard->draw_bound) {
            glBindFramebuffer(GL_DRAW_FRAMEBUFFER,
                              (GLuint)guard->draw_framebuffer);
        }
        glReadBuffer((GLenum)guard->read_buffer);
    }
    glPixelStorei(GL_PACK_ALIGNMENT, guard->pack_alignment);
    glPixelStorei(GL_PACK_ROW_LENGTH, guard->pack_row);
    glPixelStorei(GL_PACK_SKIP_ROWS, guard->pack_skip_rows);
    glPixelStorei(GL_PACK_SKIP_PIXELS, guard->pack_skip_pixels);
    glPixelStorei(GL_PACK_SWAP_BYTES, guard->pack_swap_bytes);
    glPixelStorei(GL_PACK_LSB_FIRST, guard->pack_lsb_first);
    if (guard->pack_buffer != 0) {
        glBindBuffer(GL_PIXEL_PACK_BUFFER, (GLuint)guard->pack_buffer);
    }
}

/* Select one observation source and a tightly packed pack state.  Every
   mutation is restored by end_observation_readback.  The engine's GL error
   queue is deliberately never queried: OpenGL has no non-destructive peek and
   the observer must not consume a pending renderer error. */
static void begin_observation_readback(observation_readback_guard *guard,
                                       GLuint framebuffer, GLenum read_buffer,
                                       GLuint texture) {
    memset(guard, 0, sizeof(*guard));
    glGetIntegerv(GL_PACK_ALIGNMENT, &guard->pack_alignment);
    glGetIntegerv(GL_PACK_ROW_LENGTH, &guard->pack_row);
    glGetIntegerv(GL_PACK_SKIP_ROWS, &guard->pack_skip_rows);
    glGetIntegerv(GL_PACK_SKIP_PIXELS, &guard->pack_skip_pixels);
    glGetIntegerv(GL_PIXEL_PACK_BUFFER_BINDING, &guard->pack_buffer);
    glGetBooleanv(GL_PACK_SWAP_BYTES, &guard->pack_swap_bytes);
    glGetBooleanv(GL_PACK_LSB_FIRST, &guard->pack_lsb_first);
    if (texture != 0) {
        guard->texture_bound = 1;
        glGetIntegerv(GL_TEXTURE_BINDING_2D, &guard->texture_binding);
        glBindTexture(GL_TEXTURE_2D, texture);
    } else {
        glGetIntegerv(GL_DRAW_FRAMEBUFFER_BINDING, &guard->draw_framebuffer);
        glGetIntegerv(GL_READ_FRAMEBUFFER_BINDING, &guard->read_framebuffer);
        glGetIntegerv(GL_READ_BUFFER, &guard->read_buffer);
        /* Reading the default drawable requires it to be the draw target as
           well, so the driver reports its samples/integer state. */
        guard->draw_bound = framebuffer == 0 && guard->draw_framebuffer != 0;
        if (guard->draw_bound) {
            glBindFramebuffer(GL_DRAW_FRAMEBUFFER, 0);
        }
        glBindFramebuffer(GL_READ_FRAMEBUFFER, framebuffer);
        glReadBuffer(read_buffer);
    }
    glPixelStorei(GL_PACK_ALIGNMENT, 1);
    glPixelStorei(GL_PACK_ROW_LENGTH, 0);
    glPixelStorei(GL_PACK_SKIP_ROWS, 0);
    glPixelStorei(GL_PACK_SKIP_PIXELS, 0);
    glPixelStorei(GL_PACK_SWAP_BYTES, GL_FALSE);
    glPixelStorei(GL_PACK_LSB_FIRST, GL_FALSE);
    if (guard->pack_buffer != 0) {
        glBindBuffer(GL_PIXEL_PACK_BUFFER, 0);
    }
}

/* One shared readback primitive for every observation source: the default
   drawable, an offscreen framebuffer attachment, or a standalone texture.
   glReadPixels/glGetTexImage are synchronous; glFinish is not issued because
   the Apple driver implements it as an error-queue flush. */
static int read_observation_pixels(uint32_t framebuffer, GLenum read_buffer,
                                   uint32_t texture, uint32_t width,
                                   uint32_t height, GLenum format,
                                   GLenum type, size_t bytes_per_pixel,
                                   uint8_t **out_pixels) {
    *out_pixels = NULL;
    if (width == 0 || height == 0 || width > 8192 || height > 8192 ||
        bytes_per_pixel == 0 ||
        (uint64_t)width * (uint64_t)height >
            (uint64_t)(SIZE_MAX / bytes_per_pixel)) {
        return -1;
    }
    size_t bytes = (size_t)width * (size_t)height * bytes_per_pixel;
    uint8_t *pixels = (uint8_t *)calloc(bytes ? bytes : 1, 1);
    if (pixels == NULL) {
        return -1;
    }
    if (g.prefix_caller != 0 && glGetError() != GL_NO_ERROR) {
        free(pixels);
        return -1;
    }
    observation_readback_guard guard;
    begin_observation_readback(&guard, framebuffer, read_buffer, texture);
    if (texture != 0) {
        glGetTexImage(GL_TEXTURE_2D, 0, format, type, pixels);
    } else {
        glReadPixels(0, 0, (GLsizei)width, (GLsizei)height, format, type,
                     pixels);
    }
    end_observation_readback(&guard);
    if (g.prefix_caller != 0 && glGetError() != GL_NO_ERROR) {
        free(pixels);
        return -1;
    }
    *out_pixels = pixels;
    return 0;
}

/* Read one default-drawable buffer into tightly packed pixels.  The extent is
   the actual drawable extent when the driver reports one, never an offscreen
   attachment's size and never the viewport alone unless no extent exists. */
static int read_drawable_pixels(GLenum format, GLenum type,
                                size_t bytes_per_pixel,
                                uint32_t *out_width, uint32_t *out_height,
                                uint8_t **out_pixels,
                                ftrace_drawable_state *out_state) {
    *out_width = 0;
    *out_height = 0;
    *out_pixels = NULL;
    if (bytes_per_pixel == 0) {
        return -1;
    }
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
    if ((uint64_t)width * (uint64_t)height >
        (uint64_t)(SIZE_MAX / bytes_per_pixel)) {
        return -1;
    }
    size_t bytes = (size_t)width * (size_t)height * bytes_per_pixel;
    uint8_t *pixels = (uint8_t *)calloc(bytes ? bytes : 1, 1);
    if (pixels == NULL) {
        return -1;
    }
    observation_readback_guard guard;
    begin_observation_readback(&guard, 0, GL_BACK, 0);
    if (out_state != NULL) {
        query_drawable_state(out_state);
    }
    GLint selected_draw = -1, selected_read = -1, selected_buffer = 0;
    glGetIntegerv(GL_DRAW_FRAMEBUFFER_BINDING, &selected_draw);
    glGetIntegerv(GL_READ_FRAMEBUFFER_BINDING, &selected_read);
    glGetIntegerv(GL_READ_BUFFER, &selected_buffer);
    if (selected_draw != 0 || selected_read != 0 ||
        selected_buffer != GL_BACK) {
        end_observation_readback(&guard);
        free(pixels);
        return -1;
    }
    glReadPixels(0, 0, (GLsizei)width, (GLsizei)height, format, type, pixels);
    end_observation_readback(&guard);
    *out_width = width;
    *out_height = height;
    *out_pixels = pixels;
    return 0;
}

/* Read the actual drawable (default framebuffer) into tightly packed BGRA8.
   Every binding the readback changes is restored, including pack skip state and
   a bound pixel-pack buffer.  Returns 0 on success. */
static int read_drawable_bgra(uint32_t *out_width, uint32_t *out_height,
                              uint8_t **out_pixels,
                              ftrace_drawable_state *out_state) {
    return read_drawable_pixels(GL_BGRA, GL_UNSIGNED_INT_8_8_8_8_REV, 4,
                                out_width, out_height, out_pixels, out_state);
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
    if (read_drawable_bgra(&width, &height, &pixels, NULL) != 0) {
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
    int result = read_drawable_bgra(&width, &height, &pixels, NULL);
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

/* Publish one already-read pre-event observation as the additive default-
   framebuffer checkpoint of the selected event in ``slot``.  The colour image,
   the depth contents and the stencil contents are content-addressed blobs and
   join the ordinary blob registry, so normal trace verification and the
   portable case inventory see them exactly like every other captured payload.

   The drawable record is queried from the live default framebuffer while it is
   current; an offscreen FBO's attachment precision is never substituted. */
static int capture_default_checkpoint_pixels_locked(
    int slot, const uint8_t *pixels, size_t pixels_bytes,
    const uint8_t *depth, size_t depth_bytes,
    const uint8_t *stencil, size_t stencil_bytes,
    uint32_t width, uint32_t height,
    const ftrace_drawable_state *drawable,
    int depth_used, int stencil_used) {
    if (slot < 0 || slot >= g.checkpoint_count) {
        fail_capture_locked("default_checkpoint_unattached");
        return -1;
    }
    if (pixels == NULL || width == 0 || height == 0 || width > 8192 ||
        height > 8192) {
        fail_capture_locked("default_checkpoint_dimensions");
        return -1;
    }
    if ((uint64_t)width * (uint64_t)height > (uint64_t)(SIZE_MAX / 4u)) {
        fail_capture_locked("default_checkpoint_size");
        return -1;
    }
    size_t expected_colour = (size_t)width * (size_t)height * 4u;
    if (pixels_bytes != expected_colour ||
        expected_colour / 4u / width != height ||
        expected_colour > (size_t)FTRACE_MAX_CHECKPOINT_BYTES) {
        fail_capture_locked("default_checkpoint_size");
        return -1;
    }
    size_t expected_depth = (size_t)width * (size_t)height * sizeof(float);
    size_t expected_stencil = (size_t)width * (size_t)height;
    // A buffer is required only when the selected event actually tests it.
    // The drawable almost always advertises depth/stencil bits, so requiring
    // an unused buffer would make a normal 2D checkpoint impossible.
    if (depth_used && depth == NULL) {
        fail_capture_locked("default_checkpoint_depth_size");
        return -1;
    }
    if (stencil_used && stencil == NULL) {
        fail_capture_locked("default_checkpoint_stencil_size");
        return -1;
    }
    if (depth != NULL && depth_bytes != expected_depth) {
        fail_capture_locked("default_checkpoint_depth_size");
        return -1;
    }
    if (stencil != NULL && stencil_bytes != expected_stencil) {
        fail_capture_locked("default_checkpoint_stencil_size");
        return -1;
    }
    if (expected_depth > (size_t)FTRACE_MAX_CHECKPOINT_BYTES ||
        expected_stencil > (size_t)FTRACE_MAX_CHECKPOINT_BYTES) {
        fail_capture_locked("default_checkpoint_size");
        return -1;
    }
    /* Every byte this checkpoint will write counts against the aggregate
       readback budget before the first blob is written. */
    uint64_t projected = (uint64_t)expected_colour;
    if (depth != NULL) {
        projected += (uint64_t)depth_bytes;
    }
    if (stencil != NULL) {
        projected += (uint64_t)stencil_bytes;
    }
    if (g.checkpoint_total_bytes > g.checkpoint_max_bytes ||
        projected > g.checkpoint_max_bytes - g.checkpoint_total_bytes) {
        fail_capture_locked("default_checkpoint_budget");
        return -1;
    }
    char file[FTRACE_NAME];
    char sha[65];
    if (put_blob(pixels, pixels_bytes, "default-checkpoint", file, sha) != 0) {
        return -1;
    }
    char depth_file[FTRACE_NAME] = "";
    char depth_sha[65] = "";
    char stencil_file[FTRACE_NAME] = "";
    char stencil_sha[65] = "";
    if (depth != NULL && depth_bytes > 0) {
        if (put_blob(depth, depth_bytes, "default-checkpoint-depth", depth_file,
                     depth_sha) != 0) {
            return -1;
        }
    }
    if (stencil != NULL && stencil_bytes > 0) {
        if (put_blob(stencil, stencil_bytes, "default-checkpoint-stencil",
                     stencil_file, stencil_sha) != 0) {
            return -1;
        }
    }
    char depth_json[512];
    if (depth_file[0] != 0) {
        snprintf(depth_json, sizeof(depth_json),
                 "\"depth\":{\"file\":\"%s\",\"sha256\":\"%s\","
                 "\"bytes\":%zu,\"format\":\"f32\",\"width\":%u,"
                 "\"height\":%u},",
                 depth_file, depth_sha, depth_bytes, width, height);
    } else {
        snprintf(depth_json, sizeof(depth_json),
                 "\"depth\":{\"status\":\"absent\",\"bits\":%d},",
                 drawable != NULL ? drawable->depth_bits : 0);
    }
    char stencil_json[512];
    if (stencil_file[0] != 0) {
        snprintf(stencil_json, sizeof(stencil_json),
                 "\"stencil\":{\"file\":\"%s\",\"sha256\":\"%s\","
                 "\"bytes\":%zu,\"format\":\"u8\",\"width\":%u,"
                 "\"height\":%u},",
                 stencil_file, stencil_sha, stencil_bytes, width, height);
    } else {
        snprintf(stencil_json, sizeof(stencil_json),
                 "\"stencil\":{\"status\":\"absent\",\"bits\":%d},",
                 drawable != NULL ? drawable->stencil_bits : 0);
    }
    /* A standalone host call has no configured session; the identity fields
       are then omitted rather than published empty. */
    char identity[192] = "";
    if (g.session_id[0] != 0 && g.capture_id != 0) {
        snprintf(identity, sizeof(identity),
                 ",\"session_id\":\"%s\",\"capture_frame_id\":%llu",
                 g.session_id, (unsigned long long)g.capture_id);
    }
    snprintf(g.checkpoint_file[slot], sizeof(g.checkpoint_file[slot]), "%s",
             file);
    snprintf(g.checkpoint_depth_file[slot],
             sizeof(g.checkpoint_depth_file[slot]), "%s", depth_file);
    snprintf(g.checkpoint_stencil_file[slot],
             sizeof(g.checkpoint_stencil_file[slot]), "%s", stencil_file);
    g.checkpoint_depth_bytes[slot] = depth_bytes;
    g.checkpoint_stencil_bytes[slot] = stencil_bytes;
    int written = snprintf(
        g.checkpoint_json[slot], sizeof(g.checkpoint_json[slot]),
        "{\"schema\":\"gfx-default-framebuffer-checkpoint/v2\","
        "\"event\":%d,\"target\":\"0\",\"resource\":\"fb:0\","
        "\"width\":%u,\"height\":%u,\"bytes\":%zu,"
        "\"file\":\"%s\",\"sha256\":\"%s\","
        "\"pixel_format\":\"BGRA8\",\"origin\":\"lower_left\","
        "\"timing\":\"pre_event\",\"status\":\"captured\","
        "\"drawable\":{\"samples\":%d,\"sample_buffers\":%d,"
        "\"depth_bits\":%d,\"stencil_bits\":%d,"
        "\"red_bits\":%d,\"green_bits\":%d,\"blue_bits\":%d,"
        "\"alpha_bits\":%d,\"double_buffer\":%s,\"stereo\":%s,"
        "\"drawn_framebuffer_binding\":%d},"
        "\"draw_buffer\":\"0x%x\",\"read_buffer\":\"0x%x\","
        "\"framebuffer_srgb\":%s,\"framebuffer_srgb_known\":%s,"
        "\"dither\":%s,\"multisample\":%s,"
        "%s%s"
        "\"readback_ns\":%llu%s}",
        g.checkpoint_events[slot], width, height, pixels_bytes, file, sha,
        drawable != NULL ? drawable->samples : 0,
        drawable != NULL ? drawable->sample_buffers : 0,
        drawable != NULL ? drawable->depth_bits : 0,
        drawable != NULL ? drawable->stencil_bits : 0,
        drawable != NULL ? drawable->red_bits : 0,
        drawable != NULL ? drawable->green_bits : 0,
        drawable != NULL ? drawable->blue_bits : 0,
        drawable != NULL ? drawable->alpha_bits : 0,
        bool_text(drawable != NULL && drawable->double_buffer),
        bool_text(drawable != NULL && drawable->stereo),
        drawable != NULL ? drawable->binding : -1,
        drawable != NULL ? drawable->draw_buffer : 0,
        drawable != NULL ? drawable->read_buffer : 0,
        (drawable != NULL && drawable->framebuffer_srgb >= 0)
            ? bool_text(drawable->framebuffer_srgb) : "null",
        bool_text(drawable != NULL && drawable->framebuffer_srgb_known),
        bool_text(drawable != NULL && drawable->dither),
        bool_text(drawable != NULL && drawable->multisample),
        depth_json, stencil_json,
        (unsigned long long)g.checkpoint_ns[slot], identity);
    if (written <= 0 || (size_t)written >= sizeof(g.checkpoint_json[slot])) {
        fail_capture_locked("default_checkpoint_record");
        return -1;
    }
    g.checkpoint_bytes[slot] = pixels_bytes;
    g.checkpoint_total_bytes += projected;
    g.checkpoint_captured[slot] = 1;
    return 0;
}

/* Observe the default framebuffer immediately before the selected event.  This
   runs from the draw/clear entry hooks and from the pre-execution copy event,
   never from an exit hook, so the captured image matches Metal's pre-event
   checkpoint semantics.  Returns 1 after a capture, 0 when this is not a
   selected event, and -1 when the requested checkpoint could not be
   produced. */
static int maybe_capture_default_checkpoint_locked(void) {
    if (g.checkpoint_count == 0 || !g.armed || g.events == NULL) {
        return 0;
    }
    int slot = -1;
    for (int index = 0; index < g.checkpoint_count; index++) {
        if (g.checkpoint_events[index] == g.event_count) {
            slot = index;
            break;
        }
    }
    if (slot < 0) {
        return 0;
    }
    if (g.event_count >= (int)g.max_draws) {
        fail_capture_locked("default_checkpoint_event_not_recorded");
        return -1;
    }
    if (g.checkpoint_captured[slot]) {
        fail_capture_locked("default_checkpoint_duplicate");
        return -1;
    }
    if (g.checkpoint_pending >= 0) {
        /* An earlier selected checkpoint was captured but its record was never
           emitted; the pre-execution observation cannot be attached. */
        fail_capture_locked("default_checkpoint_unattached");
        return -1;
    }
    uint32_t width = 0;
    uint32_t height = 0;
    ftrace_drawable_state drawable;
    memset(&drawable, 0, sizeof(drawable));
    if (g.test_only) {
        /* The offline writer host has no GL context.  Its synthetic image is
           bounded exactly like the production readback below. */
        width = 4;
        height = 4;
        drawable.valid = 1;
        drawable.binding = 0;
        drawable.samples = 0;
        drawable.sample_buffers = 0;
        drawable.depth_bits = 24;
        drawable.stencil_bits = 8;
        drawable.red_bits = 8;
        drawable.green_bits = 8;
        drawable.blue_bits = 8;
        drawable.alpha_bits = 8;
        drawable.double_buffer = 1;
        drawable.draw_buffer = GL_BACK;
        drawable.read_buffer = GL_BACK;
    } else {
        /* The event's own target is not necessarily bound yet at the entry
           hook (the draw body binds it).  Read fb:0 for the prestate and let
           the event writer reject a checkpoint whose recorded target is not
           the default framebuffer; the readback fills the drawable state
           while framebuffer zero is actually current. */
        capture_dimensions_only(&width, &height);
    }
    if (width == 0 || height == 0) {
        fail_capture_locked("default_checkpoint_dimensions");
        return -1;
    }
    uint64_t colour_bytes = (uint64_t)width * (uint64_t)height * 4ull;
    uint64_t depth_bytes = (uint64_t)width * (uint64_t)height * sizeof(float);
    uint64_t stencil_bytes = (uint64_t)width * (uint64_t)height;
    uint64_t projected = colour_bytes;
    if (drawable.depth_bits > 0) {
        projected += depth_bytes;
    }
    if (drawable.stencil_bits > 0) {
        projected += stencil_bytes;
    }
    if (projected == 0 ||
        g.checkpoint_total_bytes > g.checkpoint_max_bytes ||
        projected > g.checkpoint_max_bytes - g.checkpoint_total_bytes) {
        fail_capture_locked("default_checkpoint_budget");
        return -1;
    }
    uint64_t started = now_ns();
    uint8_t *pixels = NULL;
    uint8_t *depth = NULL;
    uint8_t *stencil = NULL;
    int depth_used = 1;
    int stencil_used = 1;
    if (g.test_only) {
        /* It still exercises the production blob/metadata/finalize path with a
           bounded synthetic image; the real readback below is shared with the
           final reference. */
        pixels = malloc((size_t)colour_bytes);
        if (pixels != NULL) {
            for (size_t i = 0; i < (size_t)colour_bytes; i++) {
                pixels[i] = (uint8_t)(i * 37u);
            }
        }
        if (drawable.depth_bits > 0) {
            depth = malloc((size_t)depth_bytes);
            if (depth != NULL) {
                float *values = (float *)depth;
                for (size_t i = 0; i < (size_t)(width * height); i++) {
                    values[i] = 0.5f;
                }
            }
        }
        if (drawable.stencil_bits > 0) {
            stencil = malloc((size_t)stencil_bytes);
            if (stencil != NULL) {
                memset(stencil, 0x5A, (size_t)stencil_bytes);
            }
        }
    } else {
        if (read_drawable_bgra(&width, &height, &pixels, &drawable) != 0) {
            free(pixels);
            fail_capture_locked("default_checkpoint_read");
            return -1;
        }
        depth_used = glIsEnabled(GL_DEPTH_TEST) ? 1 : 0;
        stencil_used = glIsEnabled(GL_STENCIL_TEST) ? 1 : 0;
        if (drawable.depth_bits > 0 && depth_used) {
            uint32_t got_width = 0, got_height = 0;
            if (read_drawable_pixels(GL_DEPTH_COMPONENT, GL_FLOAT,
                                     sizeof(float), &got_width, &got_height,
                                     &depth, NULL) != 0 ||
                got_width != width || got_height != height) {
                free(pixels);
                free(depth);
                fail_capture_locked("default_checkpoint_depth_read");
                return -1;
            }
        }
        if (drawable.stencil_bits > 0 && stencil_used) {
            uint32_t got_width = 0, got_height = 0;
            if (read_drawable_pixels(GL_STENCIL_INDEX, GL_UNSIGNED_BYTE,
                                     sizeof(uint8_t), &got_width, &got_height,
                                     &stencil, NULL) != 0 ||
                got_width != width || got_height != height) {
                free(pixels);
                free(depth);
                free(stencil);
                fail_capture_locked("default_checkpoint_stencil_read");
                return -1;
            }
        }
    }
    if (pixels == NULL ||
        (depth_used && drawable.depth_bits > 0 && depth == NULL) ||
        (stencil_used && drawable.stencil_bits > 0 && stencil == NULL)) {
        free(pixels);
        free(depth);
        free(stencil);
        fail_capture_locked("default_checkpoint_read");
        return -1;
    }
    g.checkpoint_ns[slot] = now_ns() - started;
    int result = capture_default_checkpoint_pixels_locked(
        slot, pixels, (size_t)colour_bytes, depth,
        depth != NULL ? (size_t)depth_bytes : 0,
        stencil, stencil != NULL ? (size_t)stencil_bytes : 0,
        width, height, &drawable, depth_used, stencil_used);
    free(pixels);
    free(depth);
    free(stencil);
    if (result != 0) {
        return -1;
    }
    if (g.checkpoint_ns[slot] > FTRACE_CHECKPOINT_MAX_NS) {
        fail_capture_locked("default_checkpoint_time");
        return -1;
    }
    g.checkpoint_pending = slot;
    return 1;
}

/* Host-test only: run the production readback/writer for one selected event,
   without an event stream, so the GL state-restoration host can assert that
   the real readback left the driver state untouched. */
int eu4_gfx_frame_trace_test_checkpoint(const char *directory, int event,
                                        char *out_file, size_t out_size) {
    if (directory == NULL || directory[0] != '/' || event < 0) {
        return -1;
    }
    snprintf(g.dir, sizeof(g.dir), "%s", directory);
    if (mkdir_p(g.dir) != 0) {
        return -1;
    }
    char blobs[FTRACE_PATH];
    snprintf(blobs, sizeof(blobs), "%s/blobs", g.dir);
    if (mkdir_p(blobs) != 0) {
        return -1;
    }
    g.checkpoint_events[0] = event;
    g.checkpoint_count = 1;
    if (g.checkpoint_max_count == 0) {
        g.checkpoint_max_count = FTRACE_DEFAULT_MAX_CHECKPOINTS;
    }
    if (g.checkpoint_max_bytes == 0) {
        g.checkpoint_max_bytes = FTRACE_DEFAULT_CHECKPOINT_TOTAL_BYTES;
    }
    g.checkpoint_pending = -1;
    g.checkpoint_captured[0] = 0;
    g.checkpoint_bytes[0] = 0;
    g.checkpoint_total_bytes = 0;
    g.checkpoint_file[0][0] = 0;
    g.checkpoint_depth_file[0][0] = 0;
    g.checkpoint_stencil_file[0][0] = 0;
    g.checkpoint_depth_bytes[0] = 0;
    g.checkpoint_stencil_bytes[0] = 0;
    g.checkpoint_json[0][0] = 0;
    uint64_t started = now_ns();
    uint32_t width = 0;
    uint32_t height = 0;
    uint8_t *pixels = NULL;
    ftrace_drawable_state drawable;
    memset(&drawable, 0, sizeof(drawable));
    if (read_drawable_bgra(&width, &height, &pixels, &drawable) != 0) {
        free(pixels);
        return -1;
    }
    uint8_t *depth = NULL;
    uint8_t *stencil = NULL;
    size_t depth_bytes = 0;
    size_t stencil_bytes = 0;
    if (drawable.depth_bits > 0) {
        uint32_t got_width = 0, got_height = 0;
        if (read_drawable_pixels(GL_DEPTH_COMPONENT, GL_FLOAT, sizeof(float),
                                 &got_width, &got_height, &depth,
                                 NULL) != 0 ||
            got_width != width || got_height != height) {
            free(pixels);
            free(depth);
            return -1;
        }
        depth_bytes = (size_t)width * (size_t)height * sizeof(float);
    }
    if (drawable.stencil_bits > 0) {
        uint32_t got_width = 0, got_height = 0;
        if (read_drawable_pixels(GL_STENCIL_INDEX, GL_UNSIGNED_BYTE,
                                 sizeof(uint8_t), &got_width, &got_height,
                                 &stencil, NULL) != 0 ||
            got_width != width || got_height != height) {
            free(pixels);
            free(depth);
            free(stencil);
            return -1;
        }
        stencil_bytes = (size_t)width * (size_t)height;
    }
    g.checkpoint_ns[0] = now_ns() - started;
    int result = capture_default_checkpoint_pixels_locked(
        0, pixels, (size_t)width * (size_t)height * 4u, depth, depth_bytes,
        stencil, stencil_bytes, width, height, &drawable, 1, 1);
    free(pixels);
    free(depth);
    free(stencil);
    if (result != 0) {
        return -1;
    }
    if (out_file != NULL && out_size > 0) {
        snprintf(out_file, out_size, "%s", g.checkpoint_file[0]);
    }
    return 0;
}

/* Host-test only: run the real production selected-event path
   (maybe_capture_default_checkpoint_locked), including its default-framebuffer
   guard and test-state gating, without a full frame stream.  This is the path
   the live recorder uses; the shortcut above bypasses it. */
int eu4_gfx_frame_trace_test_checkpoint_production(const char *directory,
                                                   int event,
                                                   char *out_file,
                                                   size_t out_size) {
    if (directory == NULL || directory[0] != '/' || event < 0) {
        return -1;
    }
    snprintf(g.dir, sizeof(g.dir), "%s", directory);
    if (mkdir_p(g.dir) != 0) {
        return -1;
    }
    char blobs[FTRACE_PATH];
    snprintf(blobs, sizeof(blobs), "%s/blobs", g.dir);
    if (mkdir_p(blobs) != 0) {
        return -1;
    }
    g.checkpoint_events[0] = event;
    g.checkpoint_count = 1;
    g.checkpoint_pending = -1;
    g.checkpoint_captured[0] = 0;
    g.checkpoint_bytes[0] = 0;
    g.checkpoint_depth_bytes[0] = 0;
    g.checkpoint_stencil_bytes[0] = 0;
    g.checkpoint_total_bytes = 0;
    g.checkpoint_file[0][0] = 0;
    g.checkpoint_depth_file[0][0] = 0;
    g.checkpoint_stencil_file[0][0] = 0;
    g.checkpoint_json[0][0] = 0;
    g.armed = 1;
    g.events = (void *)1;
    g.event_count = event;
    g.max_draws = event + 1;
    int result = maybe_capture_default_checkpoint_locked();
    g.armed = 0;
    g.events = NULL;
    g.checkpoint_pending = -1;
    if (result != 1) {
        return -1;
    }
    if (out_file != NULL && out_size > 0) {
        snprintf(out_file, out_size, "%s", g.checkpoint_file[0]);
    }
    return 0;
}

static void write_selected_target(json_out *out, const char *key,
                                  const selected_draw_target *target) {
    jprintf(out, ",\"%s\":{\"framebuffer\":%d,\"read_framebuffer\":%d,"
            "\"draw_buffer\":%d,\"read_buffer\":%d,\"attachment\":%d,"
            "\"level\":%d,\"samples\":%d,\"depth_type\":%d,\"stencil_type\":%d,"
            "\"attachment_type\":%d,\"other_draw_buffer\":%d,"
            "\"framebuffer_status\":%u,\"gl_error\":",
            key, target->framebuffer, target->read_framebuffer, target->draw_buffer,
            target->read_buffer, target->attachment, target->level, target->samples,
            target->depth, target->stencil, target->attachment_type, target->other_buffer,
            target->framebuffer_status);
    if (target->error_checked) jprintf(out, "%u}", target->error);
    else jprintf(out, "null}");
}

static int capture_selected_submission(json_out *out, uint64_t call_id) {
    selected_draw_target exit_target = {0};
    int exit_context_matches = g_selected.context != NULL &&
                               g_selected.context == CGLGetCurrentContext();
    if (!call_id || g_selected.call_id != call_id) selected_failure("call_identity");
    if (g_selected.count == 0) selected_failure("missing_submission");
    if (!exit_context_matches) selected_failure("exit_context");
    else if (!selected_target(&exit_target, g_selected.failure == NULL)) selected_failure("exit_target");
    else if (memcmp(&g_selected.entry, &exit_target, sizeof(exit_target)))
        selected_failure("exit_target_changed");
    if (g_selected.failure != NULL) {
        jprintf(out, ",\"submission_failure\":{\"reason\":\"%s\",\"count\":%u,"
                "\"expected_arguments\":[%d,%d,%d],\"submission_context_matches\":%s,"
                "\"exit_context_matches\":%s", g_selected.failure, g_selected.count,
                g_selected.mode, g_selected.first, g_selected.vertices,
                g_selected.submission_context_matches ? "true" : "false",
                exit_context_matches ? "true" : "false");
        if (g_selected.count) {
            jprintf(out, ",\"actual_arguments\":[%d,%d,%d]", g_selected.observed_mode,
                    g_selected.observed_first, g_selected.observed_vertices);
            if (g_selected.submission_context_matches)
                write_selected_target(out, "submission_target", &g_selected.submission);
        }
        write_selected_target(out, "entry_target", &g_selected.entry);
        if (exit_context_matches) write_selected_target(out, "exit_target", &exit_target);
        jprintf(out, "}");
        return 0;
    }
    jprintf(out, ",\"submission\":{\"api\":\"glDrawArrays\",\"mode\":%d,"
            "\"first\":%d,\"count\":%d,\"call_id\":%llu,\"framebuffer\":%d,"
            "\"read_framebuffer\":%d,\"attachment\":%d,\"level\":%d,"
            "\"draw_buffer\":%d,\"read_buffer\":%d,\"samples\":%d,"
            "\"depth_present\":false,\"stencil_present\":false,"
            "\"target_continuity\":\"entry_submission_exit\"}",
            g_selected.observed_mode, g_selected.observed_first, g_selected.observed_vertices,
            (unsigned long long)call_id, exit_target.framebuffer,
            exit_target.read_framebuffer, exit_target.attachment, exit_target.level,
            exit_target.draw_buffer, exit_target.read_buffer, exit_target.samples);
    return !out->failed;
}

void eu4_gfx_frame_trace_test_begin_arrays(unsigned mode, int first, int count) {
    begin_selected(1, 1, (int)mode, first, count);
}

int eu4_gfx_frame_trace_test_write_arrays(FILE *output) {
    json_out out = {output, 0};
    int ok = capture_selected_submission(&out, 1);
    if (ok) {
        const eu4_gfx_observe_call call = {.call_id = 1, .caller_rva = 1};
        uint64_t caller = g.prefix_caller;
        g.prefix_caller = call.caller_rva;
        jprintf(&out, ",");
        capture_draw_state(&out, 0, g_selected.mode, g_selected.first,
                           g_selected.vertices, 1, 0, 0, &call);
        g.prefix_caller = caller;
        ok = !out.failed;
    }
    g_selected.call_id = 0;
    return ok;
}

/* Host-test only: run one real observation (shared readback and writer) and
   return its record.  Used by the synthetic GL-context state host. */
int eu4_gfx_frame_trace_test_observe(const char *directory, int event,
                                     const char *timing, const char *kind,
                                     int texture_name, char *out_record,
                                     size_t out_size) {
    if (directory == NULL || directory[0] != '/' || timing == NULL ||
        kind == NULL) {
        return -1;
    }
    snprintf(g.dir, sizeof(g.dir), "%s", directory);
    if (mkdir_p(g.dir) != 0) {
        return -1;
    }
    char blobs[FTRACE_PATH];
    snprintf(blobs, sizeof(blobs), "%s/blobs", g.dir);
    if (mkdir_p(blobs) != 0) {
        return -1;
    }
    if (strcmp(timing, "selected_pre_draw") == 0) {
        char *json = NULL;
        size_t size = 0;
        FILE *memory = open_memstream(&json, &size);
        if (memory == NULL) {
            return -1;
        }
        uint64_t caller = g.prefix_caller;
        g.prefix_caller = 1;
        json_out out = {memory, 0};
        jprintf(&out, "{");
        int status = capture_target_pixels("pre", "pre_image", &out, NULL, NULL);
        jprintf(&out, "}");
        fclose(memory);
        g.prefix_caller = caller;
        int ok = status == 0 && !out.failed && out_record != NULL && size < out_size;
        if (ok) {
            memcpy(out_record, json, size + 1);
        }
        free(json);
        return ok ? 0 : -1;
    }
    if (g.prefix_observations != NULL) {
        fclose(g.prefix_observations);
        g.prefix_observations = NULL;
    }
    snprintf(g.prefix_file, sizeof(g.prefix_file), "%s",
             "prefix-observations.jsonl");
    char path[FTRACE_PATH];
    if (snprintf(path, sizeof(path), "%s/%s", g.dir, g.prefix_file) >=
        (int)sizeof(path)) {
        return -1;
    }
    g.prefix_observations = fopen(path, "w");
    if (g.prefix_observations == NULL) {
        return -1;
    }
    g.prefix_enabled = 1;
    g.prefix_end = event;
    g.prefix_initial = 0;
    g.prefix_next_event = 0;
    g.prefix_count = 0;
    g.prefix_bytes = 0;
    g.prefix_readback_ns = 0;
    g.prefix_failed = 0;
    g.prefix_max_bytes = FTRACE_PREFIX_HARD_MAX_BYTES;
    g.prefix_max_ns = FTRACE_PREFIX_MAX_NS;
    int result = prefix_emit_target_locked(event, timing, kind, texture_name);
    int write_failed = ferror(g.prefix_observations) != 0 ||
                       fflush(g.prefix_observations) != 0;
    fclose(g.prefix_observations);
    g.prefix_observations = NULL;
    g.prefix_enabled = 0;
    if (write_failed) {
        return -1;
    }
    if (out_record != NULL && out_size > 0) {
        out_record[0] = 0;
        FILE *observations = fopen(path, "r");
        if (observations != NULL) {
            if (fgets(out_record, (int)out_size, observations) == NULL) {
                out_record[0] = 0;
            }
            size_t length = strlen(out_record);
            while (length > 0 && (out_record[length - 1] == '\n' ||
                                  out_record[length - 1] == '\r')) {
                out_record[--length] = 0;
            }
            fclose(observations);
        }
    }
    return result;
}

/* Host-test only: force the recorded target framebuffer for a scenario that
   has no GL context.  Value -1 restores the live query. */
void eu4_gfx_frame_trace_test_set_target(int framebuffer) {
    g.test_target_fb_override = framebuffer;
}

/* Host-test only: force the recorded colour attachment's internal format for a
   synthetic target so the format-aware observation path can be exercised. */
void eu4_gfx_frame_trace_test_set_target_format(int internal_format) {
    g.test_target_internal_override = internal_format;
}

/* Host-test only: the last checkpoint's record and attachment blob names, so a
   synthetic GL context can verify the captured provenance without parsing the
   event stream. */
int eu4_gfx_frame_trace_test_checkpoint_records(
    char *json, size_t json_size, char *depth_file, size_t depth_size,
    char *stencil_file, size_t stencil_size) {
    if (json == NULL || json_size == 0) {
        return -1;
    }
    snprintf(json, json_size, "%s", g.checkpoint_json[0]);
    if (depth_file != NULL && depth_size > 0) {
        snprintf(depth_file, depth_size, "%s", g.checkpoint_depth_file[0]);
    }
    if (stencil_file != NULL && stencil_size > 0) {
        snprintf(stencil_file, stencil_size, "%s",
                 g.checkpoint_stencil_file[0]);
    }
    return 0;
}

/* Host-test only: invoke the selected-event hook directly so the duplicate
   guard can be exercised before the draw/clear/copy boundary runs it. */
int eu4_gfx_frame_trace_test_checkpoint_hook(void) {
    return maybe_capture_default_checkpoint_locked();
}

int eu4_gfx_frame_trace_test_checkpoint_captured(void) {
    for (int index = 0; index < g.checkpoint_count; index++) {
        if (g.checkpoint_captured[index]) {
            return 1;
        }
    }
    return 0;
}

int eu4_gfx_frame_trace_test_checkpoint_count(void) {
    return g.checkpoint_count;
}

int eu4_gfx_frame_trace_test_checkpoint_captured_at(int slot) {
    if (slot < 0 || slot >= g.checkpoint_count) {
        return 0;
    }
    return g.checkpoint_captured[slot];
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
                                               &stream_pixels, NULL) == 0;
                metadata_only = 0;
            }
        } else {
            stream_ok = read_drawable_bgra(&stream_width, &stream_height,
                                           &stream_pixels, NULL) == 0;
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

static void capture_clear_state(json_out *out) {
    GLint scissor_test = 0, scissor_box[4] = {0, 0, 0, 0};
    GLboolean color_mask[4] = {GL_TRUE, GL_TRUE, GL_TRUE, GL_TRUE};
    GLboolean depth_mask = GL_TRUE;
    GLint stencil_mask = -1;
    if (!g.test_only) {
        glGetIntegerv(GL_SCISSOR_TEST, &scissor_test);
        glGetIntegerv(GL_SCISSOR_BOX, scissor_box);
        glGetBooleanv(GL_COLOR_WRITEMASK, color_mask);
        glGetBooleanv(GL_DEPTH_WRITEMASK, &depth_mask);
        glGetIntegerv(GL_STENCIL_WRITEMASK, &stencil_mask);
    }
    /* The write masks and the scissor are part of a clear's coverage.  A clear
       with a disabled channel, a partial stencil mask or a clipped scissor
       does not initialise the whole buffer, so the graph must not claim it
       does. */
    jprintf(out,
            "\"state\":{\"scissor\":{\"enabled\":%s,"
            "\"box\":[%d,%d,%d,%d]},"
            "\"color_mask\":[%d,%d,%d,%d],\"depth_mask\":%s,"
            "\"stencil_mask\":%d},",
            bool_text(scissor_test != 0), scissor_box[0], scissor_box[1],
            scissor_box[2], scissor_box[3], color_mask[0] ? 1 : 0,
            color_mask[1] ? 1 : 0, color_mask[2] ? 1 : 0,
            color_mask[3] ? 1 : 0, bool_text(depth_mask != 0), stencil_mask);
}

void eu4_gfx_frame_trace_clear_entry(eu4_gfx_capture_frame *frame,
                                     eu4_gfx_observe_call *call) {
    (void)frame;
    if (!g.enabled) {
        return;
    }
    pthread_mutex_lock(&g_lock);
    call->retain = 0;
    g.prefix_clear_pending_event = -1;
    g.prefix_clear_flags = (int)frame->rsi;
    if (capture_timed_out_locked()) {
        pthread_mutex_unlock(&g_lock);
        eu4_gfx_frame_sealed_dispatch();
        return;
    }
    if (g.boundary_fb >= 0) {
        GLint draw_framebuffer = 0;
        if (!g.test_only) {
            glGetIntegerv(GL_DRAW_FRAMEBUFFER_BINDING, &draw_framebuffer);
        }
        /* The offline writer host has no GL context.  Its boundary model is
           the default framebuffer; production keeps the real query above. */
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
    /* Observe the selected clear only after boundary handling has decided
       whether this clear opens, continues or closes the capture.  An opening
       boundary clear arms the frame above, so the selected event is then event
       0 of the new frame.  A closing clear has already finalized that frame
       and is not recorded, so it must not trigger a readback.  The clear
       itself still executes before the event record is emitted, matching the
       Metal pre-event checkpoint semantics. */
    if (maybe_capture_default_checkpoint_locked() < 0) {
        pthread_mutex_unlock(&g_lock);
        eu4_gfx_frame_sealed_dispatch();
        return;
    }
    if (g.armed && g.events != NULL && g.event_count >= (int)g.max_draws) {
        g.truncated = 1;
    }
    if (g.test_only && g.armed && g.events != NULL &&
        g.event_count < (int)g.max_draws) {
        /* The offline writer host has no GL context; keep the ordered stream
           shape without querying the driver. */
        int checkpoint_slot = checkpoint_pending_slot_locked();
        int target_fb = g.test_target_fb_override >= 0
            ? g.test_target_fb_override : g.last_capture_target_fb;
        const char *checkpoint_prefix = "";
        const char *checkpoint_body = "";
        if (checkpoint_slot >= 0) {
            if (target_fb != 0) {
                fail_capture_locked("default_checkpoint_not_default");
            } else {
                checkpoint_prefix = ",\"default_checkpoint\":";
                checkpoint_body = g.checkpoint_json[checkpoint_slot];
            }
        }
        char body[2048];
        int length = snprintf(
            body, sizeof body,
            "{\"kind\":\"clear\",\"before_first_draw\":%s,\"flags\":%d,"
            "\"state\":{\"scissor\":{\"enabled\":false,"
            "\"box\":[0,0,0,0]},\"color_mask\":[1,1,1,1],"
            "\"depth_mask\":true,\"stencil_mask\":-1},"
            "\"test\":true%s%s}",
            bool_text(g.event_count == 0), (int)frame->rsi,
            checkpoint_prefix, checkpoint_body);
        if (length > 0 && (size_t)length < sizeof body && emit_event(body)) {
            g.event_count++;
            g.clear_events++;
            checkpoint_clear_pending_locked();
            g.prefix_clear_pending_event =
                g.prefix_enabled && g.event_count - 1 <= g.prefix_end
                    ? g.event_count - 1 : -1;
        }
        pthread_mutex_unlock(&g_lock);
        eu4_gfx_frame_sealed_dispatch();
        return;
    }
    if (g.armed && g.events != NULL && g.event_count < (int)g.max_draws) {
        GLfloat color[4] = {0.0f, 0.0f, 0.0f, 1.0f};
        GLfloat depth = 1.0f;
        GLint stencil = 0;
        /* The clear values are live GL state set by the engine immediately
           before glClear; observing them keeps the clear-history fallback
           faithful to a non-default clear value. */
        glGetFloatv(GL_DEPTH_CLEAR_VALUE, &depth);
        glGetIntegerv(GL_STENCIL_CLEAR_VALUE, &stencil);
        if (frame->rdx != 0) {
            GLfloat captured[4] = {0.0f, 0.0f, 0.0f, 1.0f};
            if (eu4_gfx_observe_read_bytes((const void *)(uintptr_t)frame->rdx,
                                           captured, sizeof(captured))) {
                memcpy(color, captured, sizeof(color));
            }
        }
        char *buffer = NULL;
        size_t size = 0;
        int checkpoint_slot = checkpoint_pending_slot_locked();
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
            capture_clear_state(&out);
            capture_target(&out, NULL, NULL);
            if (checkpoint_slot >= 0 &&
                g.last_capture_target_fb != 0) {
                fail_capture_locked("default_checkpoint_not_default");
            } else if (checkpoint_slot >= 0) {
                jprintf(&out, ",\"default_checkpoint\":%s",
                        g.checkpoint_json[checkpoint_slot]);
            }
            jprintf(&out, "}");
            fclose(memory);
            if (!out.failed && buffer != NULL) {
                emit_event(buffer);
                g.event_count++;
                g.clear_events++;
                checkpoint_clear_pending_locked();
                g.prefix_clear_pending_event =
                    g.prefix_enabled && g.event_count - 1 <= g.prefix_end
                        ? g.event_count - 1 : -1;
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
    if (!g.enabled || !g.prefix_enabled || !g.armed) {
        return;
    }
    pthread_mutex_lock(&g_lock);
    int event = g.prefix_clear_pending_event;
    g.prefix_clear_pending_event = -1;
    if (event >= 0) {
        (void)prefix_emit_locked(event, "post_event", "clear");
    }
    pthread_mutex_unlock(&g_lock);
    eu4_gfx_frame_sealed_dispatch();
}

/* ------------------------------------------------------------------------- */
/* Opt-in event-1 uv2 diagnostic probe                                       */
/* ------------------------------------------------------------------------- */
/* Output contract (one directory, written once):
 *
 *   uv2-probe.json   gfx-uv2-probe/v1 descriptor.  Declares side, event,
 *                    draw/target identity, source and uniform digests, the
 *                    vertex range digests actually consumed, the probe output
 *                    layout (width, height, lower-left origin, RGBA32F,
 *                    row-major little-endian float32 rows of width*16 bytes,
 *                    components uv2.x, uv2.y, 0, 1), the original-shader
 *                    control layout (BGRA8, RGB scope) and every failure.
 *   uv2-probe.rgba32f  interpolated uv2.xy written by the probe fragment
 *                    shader, plus constant z=0.0 and w=1.0.
 *   uv2-control.bgra   the same draw with the original fragment shader, used
 *                    to test that this diagnostic execution reproduces the
 *                    authentic event output.
 *
 * The probe runs after the engine's own draw, never writes the engine's
 * render target and restores every GL binding it changes.  It is diagnostic
 * evidence only and never feeds the sealed reference. */

#define UV2_PROBE_MAX_ATTRS 16
#define UV2_PROBE_SOURCE_MAX (4u * 1024u * 1024u)
#define UV2_PROBE_FRAGMENT_MAX 1024

typedef struct {
    int active;
    int location;
    int enabled;
    int offset_known;
    int integer;
    int client_array;
    uintptr_t client_pointer;
    GLint buffer;
    GLint size;
    GLint type;
    GLint normalized;
    GLint stride;
    GLint divisor;
    GLint offset;
    GLfloat current[4];
    char name[256];
} uv2_attribute;

typedef struct {
    GLint draw_framebuffer;
    GLint read_framebuffer;
    GLint read_buffer;
    GLint program;
    GLint active_texture;
    GLint texture_2d;
    GLint array_buffer;
    GLint element_buffer;
    GLint viewport[4];
    GLint scissor_test;
    GLint scissor_box[4];
    GLint cull_face;
    GLint cull_mode;
    GLint front_face;
    GLint blend;
    GLint blend_src_rgb;
    GLint blend_dst_rgb;
    GLint blend_src_alpha;
    GLint blend_dst_alpha;
    GLint blend_equation_rgb;
    GLint blend_equation_alpha;
    GLint depth_test;
    GLint depth_func;
    GLboolean depth_mask;
    GLfloat depth_range[2];
    GLint stencil_test;
    GLboolean color_mask[4];
    GLint polygon_mode[2];
    GLint polygon_offset_fill;
    GLfloat polygon_offset_factor;
    GLfloat polygon_offset_units;
    GLint alpha_test;
    GLint alpha_func;
    GLfloat alpha_ref;
    GLint logic_op;
    GLint color_logic_op;
    GLfloat clear_color[4];
    int attribute_count;
    uv2_attribute attributes[UV2_PROBE_MAX_ATTRS];
} uv2_state_guard;


/* Reads the original vertex and fragment sources from the live program.  The
 * engine compiled these bytes from GLSL; glGetShaderSource returns them
 * unchanged, so the diagnostic recompiles the very same vertex-stage logic. */
static int uv2_probe_program_sources(GLuint program, char **vertex_source,
                                     char **fragment_source, char *vertex_sha,
                                     char *fragment_sha, char *reason,
                                     size_t reason_size) {
    *vertex_source = NULL;
    *fragment_source = NULL;
    vertex_sha[0] = 0;
    fragment_sha[0] = 0;
    GLint shader_count = 0;
    glGetProgramiv(program, GL_ATTACHED_SHADERS, &shader_count);
    if (shader_count <= 0 || shader_count > 16 ||
        (g_uv2_pixels.count != 0 && shader_count != 2)) {
        snprintf(reason, reason_size, "attached_shader_count");
        return -1;
    }
    GLuint shaders[16];
    GLsizei written = 0;
    glGetAttachedShaders(program, shader_count, &written, shaders);
    for (GLsizei i = 0; i < written; i++) {
        GLint shader_type = 0;
        glGetShaderiv(shaders[i], GL_SHADER_TYPE, &shader_type);
        GLint source_length = 0;
        glGetShaderiv(shaders[i], GL_SHADER_SOURCE_LENGTH, &source_length);
        if (source_length <= 0 ||
            (uint32_t)source_length > UV2_PROBE_SOURCE_MAX) {
            snprintf(reason, reason_size, "shader_source_length");
            return -1;
        }
        char *source = calloc((size_t)source_length + 1u, 1);
        if (source == NULL) {
            snprintf(reason, reason_size, "shader_source_alloc");
            return -1;
        }
        GLsizei source_written = 0;
        glGetShaderSource(shaders[i], source_length, &source_written, source);
        source[source_length] = 0;
        if (source_written <= 0 || strlen(source) == 0) {
            free(source);
            snprintf(reason, reason_size, "shader_source_empty");
            return -1;
        }
        char digest[65];
        sha256_hex(source, strlen(source), digest);
        if (shader_type == GL_VERTEX_SHADER && *vertex_source == NULL) {
            *vertex_source = source;
            memcpy(vertex_sha, digest, sizeof(digest));
        } else if (shader_type == GL_FRAGMENT_SHADER &&
                   *fragment_source == NULL) {
            *fragment_source = source;
            memcpy(fragment_sha, digest, sizeof(digest));
        } else {
            free(source);
        }
    }
    if (*vertex_source == NULL || *fragment_source == NULL) {
        snprintf(reason, reason_size, "shader_stages_missing");
        return -1;
    }
    return 0;
}

static GLuint uv2_probe_compile_shader(GLenum type, const char *source,
                                       char *log, size_t log_size) {
    GLuint shader = glCreateShader(type);
    if (shader == 0) {
        snprintf(log, log_size, "glCreateShader failed");
        return 0;
    }
    glShaderSource(shader, 1, &source, NULL);
    glCompileShader(shader);
    GLint compiled = 0;
    glGetShaderiv(shader, GL_COMPILE_STATUS, &compiled);
    if (!compiled) {
        GLint length = 0;
        glGetShaderiv(shader, GL_INFO_LOG_LENGTH, &length);
        if (length > 0) {
            size_t take = (size_t)length;
            if (take >= log_size) {
                take = log_size - 1;
            }
            glGetShaderInfoLog(shader, (GLsizei)take, NULL, log);
            log[take] = 0;
        } else {
            snprintf(log, log_size, "shader compile failed");
        }
        glDeleteShader(shader);
        return 0;
    }
    return shader;
}

/* Links the diagnostic program with the original attribute locations bound
 * explicitly, so the live vertex arrays feed the same shader inputs. */
static GLuint uv2_probe_link_program(GLuint vertex_shader,
                                     GLuint fragment_shader,
                                     GLuint original_program, char *log,
                                     size_t log_size) {
    GLuint program = glCreateProgram();
    if (program == 0) {
        snprintf(log, log_size, "glCreateProgram failed");
        return 0;
    }
    glAttachShader(program, vertex_shader);
    glAttachShader(program, fragment_shader);
    GLint attribute_count = 0;
    glGetProgramiv(original_program, GL_ACTIVE_ATTRIBUTES, &attribute_count);
    for (GLint i = 0; i < attribute_count; i++) {
        char name[256];
        GLsizei length = 0;
        GLint size = 0;
        GLenum type = 0;
        glGetActiveAttrib(original_program, (GLuint)i, sizeof(name), &length,
                          &size, &type, name);
        GLint location = glGetAttribLocation(original_program, name);
        if (location >= 0) {
            glBindAttribLocation(program, (GLuint)location, name);
        }
    }
    glLinkProgram(program);
    GLint linked = 0;
    glGetProgramiv(program, GL_LINK_STATUS, &linked);
    if (!linked) {
        GLint length = 0;
        glGetProgramiv(program, GL_INFO_LOG_LENGTH, &length);
        if (length > 0) {
            size_t take = (size_t)length;
            if (take >= log_size) {
                take = log_size - 1;
            }
            glGetProgramInfoLog(program, (GLsizei)take, NULL, log);
            log[take] = 0;
        } else {
            snprintf(log, log_size, "program link failed");
        }
        glDeleteProgram(program);
        return 0;
    }
    return program;
}

typedef struct {
    int components;
    int is_int;
    int is_matrix;
    int is_sampler;
    int matrix_dimension;
} uv2_uniform_shape;

static int uv2_probe_uniform_shape(GLenum type, uv2_uniform_shape *shape) {
    memset(shape, 0, sizeof(*shape));
    switch (type) {
        case GL_FLOAT: shape->components = 1; return 0;
        case GL_FLOAT_VEC2: shape->components = 2; return 0;
        case GL_FLOAT_VEC3: shape->components = 3; return 0;
        case GL_FLOAT_VEC4: shape->components = 4; return 0;
        case GL_FLOAT_MAT2:
            shape->components = 4;
            shape->is_matrix = 1;
            shape->matrix_dimension = 2;
            return 0;
        case GL_FLOAT_MAT3:
            shape->components = 9;
            shape->is_matrix = 1;
            shape->matrix_dimension = 3;
            return 0;
        case GL_FLOAT_MAT4:
            shape->components = 16;
            shape->is_matrix = 1;
            shape->matrix_dimension = 4;
            return 0;
        case GL_INT:
        case GL_BOOL: shape->components = 1; shape->is_int = 1; return 0;
        case GL_INT_VEC2:
        case GL_BOOL_VEC2: shape->components = 2; shape->is_int = 1; return 0;
        case GL_INT_VEC3:
        case GL_BOOL_VEC3: shape->components = 3; shape->is_int = 1; return 0;
        case GL_INT_VEC4:
        case GL_BOOL_VEC4: shape->components = 4; shape->is_int = 1; return 0;
        case GL_SAMPLER_1D:
        case GL_SAMPLER_2D:
        case GL_SAMPLER_3D:
        case GL_SAMPLER_CUBE:
        case GL_SAMPLER_2D_SHADOW:
        case GL_SAMPLER_2D_RECT_VALUE:
            shape->components = 1;
            shape->is_int = 1;
            shape->is_sampler = 1;
            return 0;
        default: return -1;
    }
}

/* Applies one uniform element and appends its exact source bytes to ``bytes``
 * so the descriptor can prove the diagnostic used the same uniform values the
 * capture recorded. */
static int uv2_probe_copy_uniform_element(GLuint source, GLuint destination,
                                          GLint source_location,
                                          GLint destination_location,
                                          const uv2_uniform_shape *shape,
                                          void *value, size_t value_bytes) {
    (void)destination;
    memset(value, 0, value_bytes);
    if (shape->is_int) {
        glGetUniformiv(source, source_location, (GLint *)value);
    } else {
        glGetUniformfv(source, source_location, (GLfloat *)value);
    }
    if (destination_location < 0) {
        return 0;
    }
    if (shape->is_matrix) {
        if (shape->matrix_dimension == 2) {
            glUniformMatrix2fv(destination_location, 1, GL_FALSE,
                               (const GLfloat *)value);
        } else if (shape->matrix_dimension == 3) {
            glUniformMatrix3fv(destination_location, 1, GL_FALSE,
                               (const GLfloat *)value);
        } else {
            glUniformMatrix4fv(destination_location, 1, GL_FALSE,
                               (const GLfloat *)value);
        }
        return 1;
    }
    if (shape->is_int) {
        if (shape->components == 1) {
            glUniform1iv(destination_location, 1, (const GLint *)value);
        } else if (shape->components == 2) {
            glUniform2iv(destination_location, 1, (const GLint *)value);
        } else if (shape->components == 3) {
            glUniform3iv(destination_location, 1, (const GLint *)value);
        } else {
            glUniform4iv(destination_location, 1, (const GLint *)value);
        }
        return 1;
    }
    if (shape->components == 1) {
        glUniform1fv(destination_location, 1, (const GLfloat *)value);
    } else if (shape->components == 2) {
        glUniform2fv(destination_location, 1, (const GLfloat *)value);
    } else if (shape->components == 3) {
        glUniform3fv(destination_location, 1, (const GLfloat *)value);
    } else {
        glUniform4fv(destination_location, 1, (const GLfloat *)value);
    }
    return 1;
}

static void uv2_probe_apply_uniforms(GLuint source, GLuint destination) {
    GLint uniform_count = 0;
    glGetProgramiv(source, GL_ACTIVE_UNIFORMS, &uniform_count);
    for (GLint i = 0; i < uniform_count; i++) {
        char name[256];
        GLsizei length = 0;
        GLint size = 0;
        GLenum type = 0;
        glGetActiveUniform(source, (GLuint)i, sizeof(name), &length, &size,
                           &type, name);
        char base[256];
        snprintf(base, sizeof(base), "%s", name);
        char *bracket = strstr(base, "[0]");
        if (bracket != NULL) {
            *bracket = 0;
        }
        uv2_uniform_shape shape;
        if (uv2_probe_uniform_shape(type, &shape) != 0) {
            continue;
        }
        int elements = size > 0 ? size : 1;
        for (int element = 0; element < elements; element++) {
            char element_name[300];
            if (elements == 1) {
                snprintf(element_name, sizeof(element_name), "%s", base);
            } else {
                snprintf(element_name, sizeof(element_name), "%s[%d]", base,
                         element);
            }
            GLint source_location = glGetUniformLocation(source, element_name);
            if (source_location < 0) {
                continue;
            }
            GLint destination_location =
                glGetUniformLocation(destination, element_name);
            float value[16] = {0};
            (void)uv2_probe_copy_uniform_element(
                source, destination, source_location, destination_location,
                &shape, value, sizeof(value));
        }
    }
}

static int uv2_probe_attribute_count_ok(GLint count) {
    return count >= 0 && count <= UV2_PROBE_MAX_ATTRS;
}

static void uv2_probe_save_state(uv2_state_guard *guard, GLuint program,
                                 uintptr_t master) {
    memset(guard, 0, sizeof(*guard));
    glGetIntegerv(GL_DRAW_FRAMEBUFFER_BINDING, &guard->draw_framebuffer);
    glGetIntegerv(GL_READ_FRAMEBUFFER_BINDING, &guard->read_framebuffer);
    glGetIntegerv(GL_READ_BUFFER, &guard->read_buffer);
    glGetIntegerv(GL_CURRENT_PROGRAM, &guard->program);
    glGetIntegerv(GL_ACTIVE_TEXTURE, &guard->active_texture);
    glGetIntegerv(GL_TEXTURE_BINDING_2D, &guard->texture_2d);
    glGetIntegerv(GL_ARRAY_BUFFER_BINDING, &guard->array_buffer);
    glGetIntegerv(GL_ELEMENT_ARRAY_BUFFER_BINDING, &guard->element_buffer);
    glGetIntegerv(GL_VIEWPORT, guard->viewport);
    glGetIntegerv(GL_SCISSOR_TEST, &guard->scissor_test);
    glGetIntegerv(GL_SCISSOR_BOX, guard->scissor_box);
    glGetIntegerv(GL_CULL_FACE, &guard->cull_face);
    glGetIntegerv(GL_CULL_FACE_MODE, &guard->cull_mode);
    glGetIntegerv(GL_FRONT_FACE, &guard->front_face);
    glGetIntegerv(GL_BLEND, &guard->blend);
    glGetIntegerv(GL_BLEND_SRC_RGB, &guard->blend_src_rgb);
    glGetIntegerv(GL_BLEND_DST_RGB, &guard->blend_dst_rgb);
    glGetIntegerv(GL_BLEND_SRC_ALPHA, &guard->blend_src_alpha);
    glGetIntegerv(GL_BLEND_DST_ALPHA, &guard->blend_dst_alpha);
    glGetIntegerv(GL_BLEND_EQUATION_RGB, &guard->blend_equation_rgb);
    glGetIntegerv(GL_BLEND_EQUATION_ALPHA, &guard->blend_equation_alpha);
    glGetIntegerv(GL_DEPTH_TEST, &guard->depth_test);
    glGetIntegerv(GL_DEPTH_FUNC, &guard->depth_func);
    glGetBooleanv(GL_DEPTH_WRITEMASK, &guard->depth_mask);
    glGetFloatv(GL_DEPTH_RANGE, guard->depth_range);
    glGetIntegerv(GL_STENCIL_TEST, &guard->stencil_test);
    glGetBooleanv(GL_COLOR_WRITEMASK, guard->color_mask);
    glGetIntegerv(GL_POLYGON_MODE, guard->polygon_mode);
    glGetIntegerv(GL_POLYGON_OFFSET_FILL, &guard->polygon_offset_fill);
    glGetFloatv(GL_POLYGON_OFFSET_FACTOR, &guard->polygon_offset_factor);
    glGetFloatv(GL_POLYGON_OFFSET_UNITS, &guard->polygon_offset_units);
    glGetIntegerv(GL_ALPHA_TEST, &guard->alpha_test);
    glGetIntegerv(GL_ALPHA_TEST_FUNC, &guard->alpha_func);
    glGetFloatv(GL_ALPHA_TEST_REF, &guard->alpha_ref);
    glGetIntegerv(GL_COLOR_LOGIC_OP, &guard->color_logic_op);
    glGetIntegerv(GL_LOGIC_OP_MODE, &guard->logic_op);
    glGetFloatv(GL_COLOR_CLEAR_VALUE, guard->clear_color);
    GLint attribute_count = 0;
    glGetProgramiv(program, GL_ACTIVE_ATTRIBUTES, &attribute_count);
    if (attribute_count < 0) {
        attribute_count = 0;
    }
    for (GLint i = 0; i < attribute_count; i++) {
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
        uv2_attribute *attribute = &guard->attributes[guard->attribute_count];
        attribute->active = 1;
        attribute->location = location;
        snprintf(attribute->name, sizeof(attribute->name), "%s", name);
        glGetVertexAttribiv((GLuint)location, GL_VERTEX_ATTRIB_ARRAY_ENABLED,
                            &attribute->enabled);
        if (!attribute->enabled) {
            glGetVertexAttribfv((GLuint)location, GL_CURRENT_VERTEX_ATTRIB,
                                attribute->current);
            guard->attribute_count++;
            continue;
        }
        glGetVertexAttribiv((GLuint)location,
                            GL_VERTEX_ATTRIB_ARRAY_BUFFER_BINDING,
                            &attribute->buffer);
        glGetVertexAttribiv((GLuint)location, GL_VERTEX_ATTRIB_ARRAY_SIZE,
                            &attribute->size);
        glGetVertexAttribiv((GLuint)location, GL_VERTEX_ATTRIB_ARRAY_TYPE,
                            &attribute->type);
        glGetVertexAttribiv((GLuint)location,
                            GL_VERTEX_ATTRIB_ARRAY_NORMALIZED,
                            &attribute->normalized);
        glGetVertexAttribiv((GLuint)location, GL_VERTEX_ATTRIB_ARRAY_STRIDE,
                            &attribute->stride);
        GLvoid *pointer = NULL;
        glGetVertexAttribPointerv((GLuint)location,
                                  GL_VERTEX_ATTRIB_ARRAY_POINTER, &pointer);
        attribute->client_array = attribute->buffer == 0;
        attribute->client_pointer = (uintptr_t)pointer;
        attribute->offset = attribute->client_array
            ? 0 : (GLint)(intptr_t)pointer;
        /* A client array carries its base address in the attribute state; a
           VBO-backed array needs the engine element table because Apple's
           legacy GL reports a zero pointer for it. */
        attribute->offset_known =
            attribute->client_array ? attribute->client_pointer != 0 : 0;
        uint32_t engine_offset = 0;
        int engine_divisor = 0;
        int engine_normalized = 0;
        if (!attribute->client_array &&
            engine_attribute_layout(master, location, &engine_offset,
                                    &engine_divisor,
                                    &engine_normalized) == 0) {
            attribute->offset = (GLint)engine_offset;
            attribute->divisor = engine_divisor;
            attribute->normalized = engine_normalized;
            attribute->offset_known = 1;
        }
        GLint integer_attribute = 0;
        glGetVertexAttribiv((GLuint)location,
                            GL_VERTEX_ATTRIB_ARRAY_INTEGER_VALUE,
                            &integer_attribute);
        attribute->integer = integer_attribute != 0;
        guard->attribute_count++;
    }
}

static void uv2_probe_restore_state(const uv2_state_guard *guard) {
    glBindFramebuffer(GL_DRAW_FRAMEBUFFER, (GLuint)guard->draw_framebuffer);
    glBindFramebuffer(GL_READ_FRAMEBUFFER, (GLuint)guard->read_framebuffer);
    glReadBuffer((GLenum)guard->read_buffer);
    glActiveTexture((GLenum)guard->active_texture);
    glBindTexture(GL_TEXTURE_2D, (GLuint)guard->texture_2d);
    glUseProgram((GLuint)guard->program);
    glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, (GLuint)guard->element_buffer);
    glViewport(guard->viewport[0], guard->viewport[1], guard->viewport[2],
               guard->viewport[3]);
    if (guard->scissor_test) {
        glEnable(GL_SCISSOR_TEST);
    } else {
        glDisable(GL_SCISSOR_TEST);
    }
    glScissor(guard->scissor_box[0], guard->scissor_box[1],
              guard->scissor_box[2], guard->scissor_box[3]);
    if (guard->cull_face) {
        glEnable(GL_CULL_FACE);
    } else {
        glDisable(GL_CULL_FACE);
    }
    glCullFace((GLenum)guard->cull_mode);
    glFrontFace((GLenum)guard->front_face);
    if (guard->blend) {
        glEnable(GL_BLEND);
    } else {
        glDisable(GL_BLEND);
    }
    glBlendFuncSeparate((GLenum)guard->blend_src_rgb,
                        (GLenum)guard->blend_dst_rgb,
                        (GLenum)guard->blend_src_alpha,
                        (GLenum)guard->blend_dst_alpha);
    glBlendEquationSeparate((GLenum)guard->blend_equation_rgb,
                            (GLenum)guard->blend_equation_alpha);
    if (guard->depth_test) {
        glEnable(GL_DEPTH_TEST);
    } else {
        glDisable(GL_DEPTH_TEST);
    }
    glDepthFunc((GLenum)guard->depth_func);
    glDepthMask(guard->depth_mask);
    glDepthRange((GLdouble)guard->depth_range[0],
                 (GLdouble)guard->depth_range[1]);
    if (guard->stencil_test) {
        glEnable(GL_STENCIL_TEST);
    } else {
        glDisable(GL_STENCIL_TEST);
    }
    glColorMask(guard->color_mask[0], guard->color_mask[1],
                guard->color_mask[2], guard->color_mask[3]);
    glPolygonMode(GL_FRONT, (GLenum)guard->polygon_mode[0]);
    glPolygonMode(GL_BACK, (GLenum)guard->polygon_mode[1]);
    if (guard->polygon_offset_fill) {
        glEnable(GL_POLYGON_OFFSET_FILL);
    } else {
        glDisable(GL_POLYGON_OFFSET_FILL);
    }
    glPolygonOffset(guard->polygon_offset_factor,
                    guard->polygon_offset_units);
    if (guard->alpha_test) {
        glEnable(GL_ALPHA_TEST);
    } else {
        glDisable(GL_ALPHA_TEST);
    }
    glAlphaFunc((GLenum)guard->alpha_func, guard->alpha_ref);
    if (guard->color_logic_op) {
        glEnable(GL_COLOR_LOGIC_OP);
    } else {
        glDisable(GL_COLOR_LOGIC_OP);
    }
    glLogicOp((GLenum)guard->logic_op);
    glClearColor(guard->clear_color[0], guard->clear_color[1],
                 guard->clear_color[2], guard->clear_color[3]);
    for (int i = 0; i < guard->attribute_count; i++) {
        const uv2_attribute *attribute = &guard->attributes[i];
        if (!attribute->enabled) {
            glDisableVertexAttribArray((GLuint)attribute->location);
            glVertexAttrib4fv((GLuint)attribute->location, attribute->current);
            continue;
        }
        glBindBuffer(GL_ARRAY_BUFFER, (GLuint)attribute->buffer);
        glVertexAttribPointer((GLuint)attribute->location,
                              attribute->size, (GLenum)attribute->type,
                              attribute->normalized, attribute->stride,
                              attribute->client_array
                                  ? (const GLvoid *)attribute->client_pointer
                                  : (const GLvoid *)(intptr_t)attribute->offset);
        glEnableVertexAttribArray((GLuint)attribute->location);
    }
    glBindBuffer(GL_ARRAY_BUFFER, (GLuint)guard->array_buffer);
}

typedef struct {
    GLuint framebuffer;
    GLuint color;
    GLuint depth;
} uv2_target;

static void uv2_probe_delete_target(uv2_target *target) {
    if (target->framebuffer != 0) {
        glDeleteFramebuffers(1, &target->framebuffer);
    }
    if (target->color != 0) {
        glDeleteTextures(1, &target->color);
    }
    if (target->depth != 0) {
        glDeleteTextures(1, &target->depth);
    }
    memset(target, 0, sizeof(*target));
}

/* Creates one isolated diagnostic target and seeds it with the exact pre-draw
 * colour (control only) and depth (both passes) read from the engine target. */
typedef struct {
    GLint alignment;
    GLint row_length;
    GLint skip_rows;
    GLint skip_pixels;
    GLint unpack_buffer;
    GLboolean swap_bytes;
    GLboolean lsb_first;
} uv2_unpack_guard;

static void uv2_unpack_guard_begin(uv2_unpack_guard *guard) {
    memset(guard, 0, sizeof(*guard));
    glGetIntegerv(GL_UNPACK_ALIGNMENT, &guard->alignment);
    glGetIntegerv(GL_UNPACK_ROW_LENGTH, &guard->row_length);
    glGetIntegerv(GL_UNPACK_SKIP_ROWS, &guard->skip_rows);
    glGetIntegerv(GL_UNPACK_SKIP_PIXELS, &guard->skip_pixels);
    glGetIntegerv(GL_PIXEL_UNPACK_BUFFER_BINDING, &guard->unpack_buffer);
    glGetBooleanv(GL_UNPACK_SWAP_BYTES, &guard->swap_bytes);
    glGetBooleanv(GL_UNPACK_LSB_FIRST, &guard->lsb_first);
    glPixelStorei(GL_UNPACK_ALIGNMENT, 1);
    glPixelStorei(GL_UNPACK_ROW_LENGTH, 0);
    glPixelStorei(GL_UNPACK_SKIP_ROWS, 0);
    glPixelStorei(GL_UNPACK_SKIP_PIXELS, 0);
    glPixelStorei(GL_UNPACK_SWAP_BYTES, GL_FALSE);
    glPixelStorei(GL_UNPACK_LSB_FIRST, GL_FALSE);
    if (guard->unpack_buffer != 0) {
        glBindBuffer(GL_PIXEL_UNPACK_BUFFER, 0);
    }
}

static void uv2_unpack_guard_end(const uv2_unpack_guard *guard) {
    glPixelStorei(GL_UNPACK_ALIGNMENT, guard->alignment);
    glPixelStorei(GL_UNPACK_ROW_LENGTH, guard->row_length);
    glPixelStorei(GL_UNPACK_SKIP_ROWS, guard->skip_rows);
    glPixelStorei(GL_UNPACK_SKIP_PIXELS, guard->skip_pixels);
    glPixelStorei(GL_UNPACK_SWAP_BYTES, guard->swap_bytes);
    glPixelStorei(GL_UNPACK_LSB_FIRST, guard->lsb_first);
    if (guard->unpack_buffer != 0) {
        glBindBuffer(GL_PIXEL_UNPACK_BUFFER, (GLuint)guard->unpack_buffer);
    }
}

/* Creates one isolated diagnostic target and seeds it with the exact pre-draw
 * colour (control only) and depth (both passes) read from the engine target.
 * Uploads use a tight pixel-unpack state so a bound unpack buffer or a
 * non-default row/skip state cannot silently change the seed. */
static int uv2_probe_make_target(uv2_target *target, GLint color_internal,
                                 GLenum color_format, GLenum color_type,
                                 uint32_t width, uint32_t height,
                                 GLint depth_bits,
                                 const uint8_t *color_seed,
                                 const float *depth_seed, char *reason,
                                 size_t reason_size) {
    memset(target, 0, sizeof(*target));
    GLint previous_texture = 0;
    glGetIntegerv(GL_TEXTURE_BINDING_2D, &previous_texture);
    uv2_unpack_guard unpack;
    uv2_unpack_guard_begin(&unpack);
    int result = -1;
    glGenTextures(1, &target->color);
    if (target->color == 0) {
        snprintf(reason, reason_size, "color_texture");
        goto done;
    }
    glBindTexture(GL_TEXTURE_2D, target->color);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    glTexImage2D(GL_TEXTURE_2D, 0, color_internal, (GLsizei)width,
                 (GLsizei)height, 0, color_format, color_type, NULL);
    if (color_seed != NULL) {
        /* RGB8 seeds are the capture's BGRA8 pre-image; GL converts the source
           layout exactly because every channel is 8-bit. */
        glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, (GLsizei)width,
                        (GLsizei)height, GL_BGRA, GL_UNSIGNED_BYTE, color_seed);
    }
    if (depth_bits > 0) {
        GLint depth_internal = GL_DEPTH_COMPONENT24;
        if (depth_bits == 32) {
            depth_internal = GL_DEPTH_COMPONENT32F;
        } else if (depth_bits == 16) {
            depth_internal = GL_DEPTH_COMPONENT16;
        }
        glGenTextures(1, &target->depth);
        if (target->depth == 0) {
            snprintf(reason, reason_size, "depth_texture");
            goto done;
        }
        glBindTexture(GL_TEXTURE_2D, target->depth);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
        glTexImage2D(GL_TEXTURE_2D, 0, depth_internal, (GLsizei)width,
                     (GLsizei)height, 0, GL_DEPTH_COMPONENT, GL_FLOAT, NULL);
        if (depth_seed != NULL) {
            glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, (GLsizei)width,
                            (GLsizei)height, GL_DEPTH_COMPONENT, GL_FLOAT,
                            depth_seed);
        }
    }
    glBindTexture(GL_TEXTURE_2D, (GLuint)previous_texture);
    glGenFramebuffers(1, &target->framebuffer);
    if (target->framebuffer == 0) {
        snprintf(reason, reason_size, "framebuffer");
        goto done;
    }
    glBindFramebuffer(GL_DRAW_FRAMEBUFFER, target->framebuffer);
    glFramebufferTexture2D(GL_DRAW_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                           GL_TEXTURE_2D, target->color, 0);
    if (target->depth != 0) {
        glFramebufferTexture2D(GL_DRAW_FRAMEBUFFER, GL_DEPTH_ATTACHMENT,
                               GL_TEXTURE_2D, target->depth, 0);
    }
    glDrawBuffer(GL_COLOR_ATTACHMENT0);
    GLenum status = glCheckFramebufferStatus(GL_DRAW_FRAMEBUFFER);
    if (status != GL_FRAMEBUFFER_COMPLETE) {
        snprintf(reason, reason_size, "framebuffer_incomplete_0x%x",
                 (unsigned)status);
        goto done;
    }
    result = 0;
done:
    glBindTexture(GL_TEXTURE_2D, (GLuint)previous_texture);
    uv2_unpack_guard_end(&unpack);
    return result;
}

/* Reads and hashes one live vertex attribute range exactly as the capture
 * defines it, proving the diagnostic draw consumed the same bytes. */
static int uv2_probe_attribute_digest(const uv2_attribute *attribute,
                                      int indexed, int count,
                                      int instance_count, int first,
                                      int32_t index_first, int32_t index_last,
                                      char *sha) {
    sha[0] = 0;
    if (!attribute->active || !attribute->enabled) {
        return 0;
    }
    int element_size = eu4_gfx_gl_type_size((uint32_t)attribute->type);
    if (element_size == 0) {
        return -1;
    }
    int stride = attribute->stride;
    if (stride <= 0) {
        stride = attribute->size * element_size;
    }
    int32_t vertex_first = 0;
    int32_t vertex_last = 0;
    if (eu4_gfx_capture_vertex_range(attribute->divisor, indexed,
                                     instance_count, first, count, index_first,
                                     index_last, &vertex_first,
                                     &vertex_last) != 0) {
        return -1;
    }
    GLint buffer_size = 0;
    if (attribute->buffer != 0) {
        glBindBuffer(GL_ARRAY_BUFFER, (GLuint)attribute->buffer);
        glGetBufferParameteriv(GL_ARRAY_BUFFER, GL_BUFFER_SIZE, &buffer_size);
    }
    uint64_t attribute_width =
        (uint64_t)(attribute->size > 0 ? attribute->size : 0) *
        (uint64_t)element_size;
    uint64_t stride64 = (uint64_t)stride;
    if (vertex_first < 0 || vertex_last < vertex_first) {
        return -1;
    }
    uint64_t span = (uint64_t)((int64_t)vertex_last - vertex_first) *
                        stride64 + attribute_width;
    if (attribute->client_array) {
        const uint8_t *base =
            (const uint8_t *)attribute->client_pointer;
        uint64_t begin = (uint64_t)vertex_first * stride64;
        if (base == NULL || span == 0) {
            return -1;
        }
        sha256_hex(base + begin, (size_t)span, sha);
        return 0;
    }
    uint64_t offset = (uint64_t)(int64_t)attribute->offset;
    uint64_t begin = 0;
    uint64_t end = 0;
    if (attribute->buffer == 0 ||
        (uint64_t)vertex_first > (UINT64_MAX - offset) / stride64) {
        return -1;
    }
    begin = offset + (uint64_t)vertex_first * stride64;
    if (eu4_gfx_attribute_end(buffer_size > 0 ? (uint64_t)buffer_size : 0,
                              offset, stride64, (uint64_t)vertex_last,
                              attribute_width, &end) == 0 ||
        begin > end || end > (uint64_t)buffer_size) {
        return -1;
    }
    uint64_t bytes = end - begin;
    if (bytes == 0) {
        return -1;
    }
    uint8_t *data = malloc((size_t)bytes);
    if (data == NULL) {
        return -1;
    }
    glBindBuffer(GL_ARRAY_BUFFER, (GLuint)attribute->buffer);
    glGetBufferSubData(GL_ARRAY_BUFFER, (GLintptr)begin, (GLsizeiptr)bytes,
                       data);
    sha256_hex(data, (size_t)bytes, sha);
    free(data);
    return 0;
}

/* Add an observation to the reviewed shader's final store. Its original
 * discard, arithmetic and color output still execute with depth enabled. */
static const char *uv2_sparse_fragment(const char *source, char **result) {
    const char *store = "gl_FragColor = glsl_main( glsl_vertex );";
    const char *where = strstr(source, store);
    if (where == NULL)
        return "instrumentation_marker";
    char *instrumented = NULL;
    if (asprintf(&instrumented, "%.*sgl_FragData[0] = glsl_main( glsl_vertex );"
                 "\n gl_FragData[1] = vec4(glsl_vertex.uv2, 0.0, 1.0);%s",
                 (int)(where - source), source, where + strlen(store)) < 0)
        return "instrumentation_allocation";
    *result = instrumented;
    return NULL;
}

static void uv2_sparse_publish(const char *body, size_t size) {
    char path[FTRACE_PATH];
    snprintf(path, sizeof(path), "%s/" EU4_UV2_SPARSE_FILE, g.dir);
    if (write_atomic(path, body, size) == 0)
        sha256_hex(body, size, g_uv2_sparse_sha);
}

static void uv2_sparse_header(json_out *out, const char *status) {
    jprintf(out, "{\"schema\":\"" EU4_UV2_SPARSE_SCHEMA "\","
            "\"side\":\"opengl\",\"status\":\"%s\",\"origin\":\"lower_left\","
            "\"capture\":{\"session_id\":\"%s\",\"capture_frame_id\":%llu,\"pid\":%d},"
            "\"event\":%d,\"program\":%d,\"framebuffer\":%d,\"texture\":%d,\"level\":0",
            status, g.session_id, (unsigned long long)g.capture_id, (int)getpid(),
            g_uv2_probe.event, g_uv2_probe.program, g_uv2_probe.framebuffer,
            g_uv2_probe.texture);
    jprintf(out, ",\"selection\":[");
    for (unsigned i = 0; i < g_uv2_pixels.count; i++) {
        eu4_uv2_pixel pixel = g_uv2_pixels.pixels[i];
        jprintf(out, "%s[%u,%u]", i ? "," : "", pixel.x, pixel.y);
    }
    jprintf(out, "]");
}

static void uv2_sparse_read(GLuint framebuffer, GLenum attachment,
                            GLenum format, GLenum type, void *values) {
    observation_readback_guard guard;
    begin_observation_readback(&guard, framebuffer, attachment, 0);
    for (unsigned i = 0; i < g_uv2_pixels.count; i++) {
        eu4_uv2_pixel pixel = g_uv2_pixels.pixels[i];
        size_t stride = format == GL_RGBA && type == GL_FLOAT ? 16 : 4;
        glReadPixels(pixel.x, pixel.y, 1, 1, format, type,
                     (uint8_t *)values + i * stride);
    }
    end_observation_readback(&guard);
}

static void uv2_sparse_draw(int first, int triangles) {
    glDrawElements(GL_TRIANGLES, triangles * 3, GL_UNSIGNED_SHORT,
                   (const GLvoid *)(uintptr_t)(first * 3 * sizeof(uint16_t)));
}

static GLuint uv2_sparse_samples(GLuint query, int first, int triangles) {
    GLuint samples = 0;
    glBeginQuery(GL_SAMPLES_PASSED, query);
    uv2_sparse_draw(first, triangles);
    glEndQuery(GL_SAMPLES_PASSED);
    glGetQueryObjectuiv(query, GL_QUERY_RESULT, &samples);
    return samples;
}

/* Query the original linked program with the captured pre-depth held fixed.
 * Every actual writer also passes against the initial depth because LEQUAL
 * writes can only tighten it. The actual RGB8 change proves a writer exists.
 * The pinned shader's discard is independent of derivatives and destinations,
 * and the linked program has no fragment depth export or draw-relative IDs. */
static int uv2_sparse_observe(const uv2_state_guard *guard, uv2_target control,
                              uv2_target probe, GLuint instrumented,
                              int count, int width, int height, const char *index_sha,
                              const uint8_t *pre_color, const uint8_t *pre_depth) {
    GLint max_draw_buffers = 0, active_query = 0, counter_bits = 0;
    GLint depth_type = 0, sample_buffers = -1;
    glGetFramebufferAttachmentParameteriv(GL_DRAW_FRAMEBUFFER, GL_DEPTH_ATTACHMENT,
                                           GL_FRAMEBUFFER_ATTACHMENT_COMPONENT_TYPE,
                                           &depth_type);
    glGetIntegerv(GL_SAMPLE_BUFFERS, &sample_buffers);
    glGetIntegerv(GL_MAX_DRAW_BUFFERS, &max_draw_buffers);
    glGetQueryiv(GL_SAMPLES_PASSED, GL_CURRENT_QUERY, &active_query);
    glGetQueryiv(GL_SAMPLES_PASSED, GL_QUERY_COUNTER_BITS, &counter_bits);
    if (max_draw_buffers < 2 || active_query || counter_bits < 16 ||
        depth_type != GL_UNSIGNED_NORMALIZED || sample_buffers != 0 ||
        pre_color == NULL || pre_depth == NULL)
        return -1;
    for (int i = 0; i < max_draw_buffers; i++) {
        GLint buffer = 0;
        glGetIntegerv(GL_DRAW_BUFFER0 + i, &buffer);
        if (buffer != (i == 0 ? GL_COLOR_ATTACHMENT0 : GL_NONE))
            return -1;
    }
    for (unsigned i = 0; i < g_uv2_pixels.count; i++) {
        if (g_uv2_pixels.pixels[i].x >= (unsigned)width ||
            g_uv2_pixels.pixels[i].y >= (unsigned)height)
            return -1;
    }
    if (guard->blend || guard->color_logic_op || guard->scissor_test ||
        !guard->depth_test || !guard->depth_mask || guard->depth_func != GL_LEQUAL ||
        guard->depth_range[0] != 0.0f || guard->depth_range[1] != 1.0f ||
        !guard->color_mask[0] || !guard->color_mask[1] || !guard->color_mask[2] ||
        !guard->color_mask[3] || guard->polygon_mode[0] != GL_FILL ||
        guard->polygon_mode[1] != GL_FILL || guard->polygon_offset_fill ||
        glIsEnabled(GL_POLYGON_SMOOTH) ||
        glIsEnabled(GL_POLYGON_STIPPLE) || glIsEnabled(GL_SAMPLE_ALPHA_TO_COVERAGE) ||
        glIsEnabled(GL_SAMPLE_ALPHA_TO_ONE) || glIsEnabled(GL_SAMPLE_COVERAGE))
        return -1;

    // User clip planes are undefined when this vertex shader omits gl_ClipVertex.
    GLint clip_planes = 0;
    glGetIntegerv(GL_MAX_CLIP_PLANES, &clip_planes);
    for (int i = 0; i < clip_planes; i++)
        if (glIsEnabled(GL_CLIP_PLANE0 + i))
            return -1;
    const char *extensions = (const char *)glGetString(GL_EXTENSIONS);
    if (extensions != NULL && strstr(extensions, "GL_NV_primitive_restart") &&
        glIsEnabled(0x8558 /* GL_PRIMITIVE_RESTART_NV */))
        return -1;

    glBindFramebuffer(GL_DRAW_FRAMEBUFFER, control.framebuffer);
    glUseProgram((GLuint)guard->program);
    glColorMask(GL_FALSE, GL_FALSE, GL_FALSE, GL_FALSE);
    glDepthMask(GL_FALSE);
    glEnable(GL_SCISSOR_TEST);
    GLuint query = 0;
    glGenQueries(1, &query);
    if (query == 0)
        return -1;
    GLuint hits[EU4_UV2_MAX_PIXELS] = {0};
    GLuint hits_without_depth[EU4_UV2_MAX_PIXELS] = {0};
    int primitives[EU4_UV2_MAX_PIXELS];
    for (unsigned i = 0; i < g_uv2_pixels.count; i++) {
        eu4_uv2_pixel pixel = g_uv2_pixels.pixels[i];
        glScissor(pixel.x, pixel.y, 1, 1);
        hits[i] = uv2_sparse_samples(query, 0, count / 3);
        primitives[i] = -1;
        if (hits[i] == 0) {
            glDisable(GL_DEPTH_TEST);
            hits_without_depth[i] = uv2_sparse_samples(query, 0, count / 3);
            glEnable(GL_DEPTH_TEST);
        }
        if (hits[i] != 1)
            continue;
        int first = 0, end = count / 3;
        while (end - first > 1) {
            int middle = first + (end - first) / 2;
            GLuint lower_hits = uv2_sparse_samples(query, first, middle - first);
            if (lower_hits > 1)
                break;
            if (lower_hits == 1)
                end = middle;
            else
                first = middle;
        }
        if (end - first == 1 && uv2_sparse_samples(query, first, 1) == 1)
            primitives[i] = first;
    }
    glDeleteQueries(1, &query);

    glFramebufferTexture2D(GL_DRAW_FRAMEBUFFER, GL_COLOR_ATTACHMENT1,
                           GL_TEXTURE_2D, probe.color, 0);
    GLenum attachments[] = {GL_COLOR_ATTACHMENT0, GL_COLOR_ATTACHMENT1};
    glDrawBuffers(2, attachments);
    if (glCheckFramebufferStatus(GL_DRAW_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE)
        return -1;
    glColorMask(GL_TRUE, GL_TRUE, GL_TRUE, GL_TRUE);
    glDepthMask(GL_TRUE);
    glDrawBuffer(GL_COLOR_ATTACHMENT1);
    glDisable(GL_SCISSOR_TEST);
    glClearColor(0, 0, 0, 0);
    glClear(GL_COLOR_BUFFER_BIT);
    glDrawBuffers(2, attachments);
    glEnable(GL_SCISSOR_TEST);
    glUseProgram(instrumented);
    for (unsigned i = 0; i < g_uv2_pixels.count; i++) {
        int primitive = primitives[i];
        if (primitive < 0)
            continue;
        eu4_uv2_pixel pixel = g_uv2_pixels.pixels[i];
        glScissor(pixel.x, pixel.y, 1, 1);
        uv2_sparse_draw(primitive, 1);
    }

    uint8_t control_rgb[EU4_UV2_MAX_PIXELS][4] = {{0}};
    uint32_t uv[EU4_UV2_MAX_PIXELS][4] = {{0}};
    uint32_t depth[EU4_UV2_MAX_PIXELS] = {0};
    uv2_sparse_read(control.framebuffer, GL_COLOR_ATTACHMENT0,
                    GL_RGBA, GL_UNSIGNED_BYTE, control_rgb);
    uv2_sparse_read(control.framebuffer, GL_COLOR_ATTACHMENT1, GL_RGBA, GL_FLOAT, uv);
    uv2_sparse_read(control.framebuffer, GL_COLOR_ATTACHMENT0,
                    GL_DEPTH_COMPONENT, GL_FLOAT, depth);

    char *body = NULL;
    size_t size = 0;
    FILE *memory = open_memstream(&body, &size);
    if (memory == NULL)
        return -1;
    json_out out = {memory, 0};
    uv2_sparse_header(&out, "observed");
    jprintf(&out, ",\"method\":\"original_program_query_pre_depth\","
            "\"width\":%d,\"height\":%d,\"samples\":1,"
            "\"index_sha256\":\"%s\",\"pixels\":[", width, height, index_sha);
    for (unsigned i = 0; i < g_uv2_pixels.count; i++) {
        eu4_uv2_pixel pixel = g_uv2_pixels.pixels[i];
        size_t offset = ((size_t)pixel.y * width + pixel.x) * 4;
        uint32_t before_depth = 0;
        memcpy(&before_depth, pre_depth + offset, sizeof(before_depth));
        jprintf(&out, "%s{\"x\":%u,\"y\":%u,\"sample\":0,\"hits\":%u,"
                "\"hits_without_depth\":%u,\"primitive\":%d,\"survived\":%s,\"uv2_bits\":[%u,%u],"
                "\"control_rgb\":[%u,%u,%u],\"pre_rgb\":[%u,%u,%u],"
                "\"pre_depth_bits\":%u,\"control_depth_bits\":%u}",
                i ? "," : "", pixel.x, pixel.y, hits[i], hits_without_depth[i],
                primitives[i], uv[i][3] == 0x3f800000 ? "true" : "false",
                uv[i][0], uv[i][1],
                control_rgb[i][0], control_rgb[i][1], control_rgb[i][2],
                pre_color[offset + 2], pre_color[offset + 1], pre_color[offset],
                before_depth, depth[i]);
    }
    jprintf(&out, "]}\n");
    fclose(memory);
    if (!out.failed && body != NULL)
        uv2_sparse_publish(body, size);
    free(body);
    return g_uv2_sparse_sha[0] != 0 ? 0 : -1;
}

typedef struct {
    int observed;
    int required;
    bool unavailable;
} uv2_numeric_mismatch;

/* Writes one descriptor-only failure so the operator and the offline
 * comparator see exactly why the probe did not produce numbers. */
static void uv2_probe_write_failure_detail(
    const char *status, const char *reason, const uv2_numeric_mismatch *mismatch,
    const observation_attachment *depth) {
    int sparse = g_uv2_pixels.count != 0;
    if (sparse)
        fprintf(stderr, "[frame trace] sparse UV2: %s (%s)\n", status, reason);
    char *body = NULL;
    size_t size = 0;
    FILE *memory = open_memstream(&body, &size);
    if (memory == NULL)
        return;
    json_out out = {memory, 0};
    if (sparse) {
        uv2_sparse_header(&out, status);
        const char *code = reason[0] != 0 && strlen(reason) < 64 &&
            strspn(reason, "abcdefghijklmnopqrstuvwxyz0123456789_") == strlen(reason)
            ? reason : status;
        jprintf(&out, ",\"reason\":\"%s\"", code);
        if (mismatch != NULL) {
            jprintf(&out, ",\"required\":%d,\"observed\":", mismatch->required);
            if (mismatch->unavailable)
                jprintf(&out, "null");
            else
                jprintf(&out, "%d", mismatch->observed);
        }
        if (depth != NULL && depth->type == GL_RENDERBUFFER) {
            jprintf(&out, ",\"depth_attachment\":{\"type\":\"renderbuffer\","
                    "\"name\":%d,\"internal_format\":%d,\"depth_bits\":%d",
                    depth->name, depth->internal_format, depth->renderbuffer_depth_bits);
            if (depth->depth_component_type != 0)
                jprintf(&out, ",\"component_type\":%d", depth->depth_component_type);
            jprintf(&out, "}");
        }
        jprintf(&out, ",\"pixels\":[]}\n");
    } else {
        jprintf(&out,
                "{\"schema\":\"gfx-uv2-probe/v1\",\"side\":\"opengl\","
                "\"status\":\"%s\",\"reason\":\"%s\","
                "\"session_id\":\"%s\",\"capture_frame_id\":%llu,\"pid\":%d,"
                "\"event\":%d,\"program\":%d}",
                status, reason, g.session_id,
                (unsigned long long)g.capture_id, (int)getpid(),
                g_uv2_probe.event, g_uv2_probe.program);
    }
    fclose(memory);
    if (!out.failed && body != NULL) {
        if (sparse) {
            uv2_sparse_publish(body, size);
        } else {
            char path[FTRACE_PATH];
            snprintf(path, sizeof(path), "%s/uv2-probe.json", g_uv2_probe.dir);
            (void)write_atomic(path, body, size);
        }
    }
    free(body);
}

static void uv2_probe_write_failure(const char *status, const char *reason) {
    uv2_probe_write_failure_detail(status, reason, NULL, NULL);
}

static bool uv2_probe_reject_value(const char *status, const char *reason,
                                   int observed, int required) {
    if (observed == required)
        return false;
    const uv2_numeric_mismatch mismatch = {observed, required, false};
    uv2_probe_write_failure_detail(status, reason, &mismatch, NULL);
    return true;
}

/* ARB_framebuffer_object (or GL 3+) defines the component-size query.
 * For an absent attachment only object type/name can be queried. A -1
 * sentinel distinguishes a query with no result from a measured size. */
static GLint uv2_probe_depth_size(GLint *type) {
    *type = -1;
    glGetFramebufferAttachmentParameteriv(
        GL_DRAW_FRAMEBUFFER, GL_DEPTH_ATTACHMENT,
        GL_FRAMEBUFFER_ATTACHMENT_OBJECT_TYPE, type);
    if (*type == GL_NONE)
        return 0;
    if ((*type != GL_RENDERBUFFER && *type != GL_TEXTURE) ||
        !framebuffer_attachment_identity_supported())
        return -1;
    GLint bits = -1;
    glGetFramebufferAttachmentParameteriv(
        GL_DRAW_FRAMEBUFFER, GL_DEPTH_ATTACHMENT,
        GL_FRAMEBUFFER_ATTACHMENT_DEPTH_SIZE, &bits);
    return bits;
}

/* Run once from the draw exit, using the original program and bindings,
 * and the pre-draw attachments saved at entry. */
static int uv2_probe_execute(int event, int indexed, unsigned mode, int count,
                             int first, int instances, int base_vertex,
                             uintptr_t master) {
    if (!g_uv2_probe.enabled || g_uv2_probe_done) {
        return 0;
    }
    if (event != g_uv2_probe.event) {
        return 0;
    }
    if (!indexed || count <= 0 || mode == 0 || instances != 1 ||
        base_vertex != 0 || first != 0) {
        g_uv2_probe_done = 1;
        uv2_probe_write_failure("selection_unsupported",
                                "the probe supports single-instance indexed "
                                "draws with base_vertex and first zero only");
        return 0;
    }
    if (mkdir_p(g_uv2_probe.dir) != 0) {
        uv2_probe_write_failure("error", "probe_directory");
        g_uv2_probe_done = 1;
        return -1;
    }
    GLint program = 0;
    glGetIntegerv(GL_CURRENT_PROGRAM, &program);
    GLint draw_framebuffer = 0;
    glGetIntegerv(GL_DRAW_FRAMEBUFFER_BINDING, &draw_framebuffer);
    GLint object_type = 0;
    GLint texture_name = 0;
    glGetFramebufferAttachmentParameteriv(
        GL_DRAW_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
        GL_FRAMEBUFFER_ATTACHMENT_OBJECT_TYPE, &object_type);
    glGetFramebufferAttachmentParameteriv(
        GL_DRAW_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
        GL_FRAMEBUFFER_ATTACHMENT_OBJECT_NAME, &texture_name);
    GLint draw_buffer = 0;
    glGetIntegerv(GL_DRAW_BUFFER, &draw_buffer);
    if (draw_buffer != GL_COLOR_ATTACHMENT0) {
        uv2_probe_write_failure("unsupported_draw_buffer",
                                "the colour draw buffer is not attachment 0");
        g_uv2_probe_done = 1;
        return 0;
    }
    if (program <= 0 || object_type != GL_TEXTURE) {
        uv2_probe_write_failure("identity_mismatch",
                                "no colour texture draw target");
        g_uv2_probe_done = 1;
        return 0;
    }
    bool sparse = g_uv2_pixels.count != 0;
    if (g_uv2_probe.require_identity &&
        (uv2_probe_reject_value("identity_mismatch",
                               sparse ? "program" : "event_draw_identity",
                               program, g_uv2_probe.program) ||
         uv2_probe_reject_value("identity_mismatch",
                               sparse ? "framebuffer" : "event_draw_identity",
                               draw_framebuffer, g_uv2_probe.framebuffer) ||
         uv2_probe_reject_value("identity_mismatch",
                               sparse ? "texture" : "event_draw_identity",
                               texture_name, g_uv2_probe.texture))) {
        g_uv2_probe_done = 1;
        return 0;
    }
    GLint level = 0;
    glGetFramebufferAttachmentParameteriv(
        GL_DRAW_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
        GL_FRAMEBUFFER_ATTACHMENT_TEXTURE_LEVEL, &level);
    GLint previous_texture = 0;
    glGetIntegerv(GL_TEXTURE_BINDING_2D, &previous_texture);
    glBindTexture(GL_TEXTURE_2D, (GLuint)texture_name);
    GLint width = 0, height = 0, internal_format = 0;
    glGetTexLevelParameteriv(GL_TEXTURE_2D, level, GL_TEXTURE_WIDTH, &width);
    glGetTexLevelParameteriv(GL_TEXTURE_2D, level, GL_TEXTURE_HEIGHT, &height);
    glGetTexLevelParameteriv(GL_TEXTURE_2D, level,
                             GL_TEXTURE_INTERNAL_FORMAT, &internal_format);
    glBindTexture(GL_TEXTURE_2D, (GLuint)previous_texture);
    if (width <= 0 || height <= 0 || width > 8192 || height > 8192) {
        uv2_probe_write_failure("error", "target_extent");
        g_uv2_probe_done = 1;
        return -1;
    }
    if (internal_format != GL_RGB8) {
        /* The control must be compared with the authentic RGB8 output.  An
           unknown target format is refused rather than normalized. */
        uv2_probe_write_failure("unsupported_target_format",
                                "control target is not RGB8");
        g_uv2_probe_done = 1;
        return 0;
    }
    GLint samples = 0;
    glGetIntegerv(GL_SAMPLES, &samples);
    if (samples > 0) {
        uv2_probe_write_failure("unsupported_multisample",
                                "multisampled targets are not reproducible");
        g_uv2_probe_done = 1;
        return 0;
    }
    GLint depth_type = 0;
    GLint depth_bits = uv2_probe_depth_size(&depth_type);
    const char *unsupported_draw = "unsupported_sparse_draw";
    if (sparse && depth_bits != 24) {
        const uv2_numeric_mismatch mismatch = {depth_bits, 24, depth_bits < 0};
        observation_attachment depth = {0};
        if (depth_type == GL_RENDERBUFFER) {
            query_attachment_identity(GL_DEPTH_ATTACHMENT, &depth, true);
            if (depth_bits >= 0)
                glGetFramebufferAttachmentParameteriv(
                    GL_DRAW_FRAMEBUFFER, GL_DEPTH_ATTACHMENT,
                    GL_FRAMEBUFFER_ATTACHMENT_COMPONENT_TYPE, &depth.depth_component_type);
        }
        uv2_probe_write_failure_detail(
            unsupported_draw, mismatch.unavailable ? "depth_bits_unavailable" : "depth_bits",
            &mismatch, &depth);
        g_uv2_probe_done = 1;
        return 0;
    }
    if (depth_bits < 0) {
        uv2_probe_write_failure("unsupported_depth", "depth_bits_unavailable");
        g_uv2_probe_done = 1;
        return 0;
    }
    if (sparse &&
        (uv2_probe_reject_value(unsupported_draw, "event", event, 1) ||
         uv2_probe_reject_value(unsupported_draw, "program", program, 310) ||
         uv2_probe_reject_value(unsupported_draw, "framebuffer", draw_framebuffer, 6) ||
         uv2_probe_reject_value(unsupported_draw, "texture", texture_name, 433) ||
         uv2_probe_reject_value(unsupported_draw, "level", level, 0) ||
         uv2_probe_reject_value(unsupported_draw, "primitive_mode", (int)mode, GL_TRIANGLES) ||
         uv2_probe_reject_value(unsupported_draw, "index_count_remainder", count % 3, 0) ||
         (count / 3 > EU4_UV2_MAX_TRIANGLES &&
          uv2_probe_reject_value(unsupported_draw, "triangle_limit",
                                 count / 3, EU4_UV2_MAX_TRIANGLES)))) {
        g_uv2_probe_done = 1;
        return 0;
    }
    GLint stencil_test = 0;
    glGetIntegerv(GL_STENCIL_TEST, &stencil_test);
    if (stencil_test) {
        uv2_probe_write_failure("unsupported_stencil",
                                "an enabled stencil test needs its exact "
                                "contents");
        g_uv2_probe_done = 1;
        return 0;
    }
    GLint alpha_test = 0;
    glGetIntegerv(GL_ALPHA_TEST, &alpha_test);
    if (alpha_test) {
        uv2_probe_write_failure("unsupported_alpha_test",
                                "the probe fragment has no authentic alpha");
        g_uv2_probe_done = 1;
        return 0;
    }
    GLint active_attributes = 0;
    glGetProgramiv((GLuint)program, GL_ACTIVE_ATTRIBUTES, &active_attributes);
    if (!uv2_probe_attribute_count_ok(active_attributes)) {
        uv2_probe_write_failure(
            "unsupported_attribute_count",
            "the original program declares more active attributes than the "
            "diagnostic state guard supports");
        g_uv2_probe_done = 1;
        return 0;
    }
    uv2_state_guard guard;
    uv2_probe_save_state(&guard, (GLuint)program, master);
    if (guard.attribute_count == 0) {
        uv2_probe_write_failure("error", "attribute_state_unavailable");
        uv2_probe_restore_state(&guard);
        g_uv2_probe_done = 1;
        return 0;
    }
    if (guard.element_buffer == 0) {
        uv2_probe_write_failure("unsupported_index_buffer",
                                "no element array buffer is bound");
        g_uv2_probe_done = 1;
        return 0;
    }
    {
        GLint index_bytes = 0;
        glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, (GLuint)guard.element_buffer);
        glGetBufferParameteriv(GL_ELEMENT_ARRAY_BUFFER, GL_BUFFER_SIZE,
                               &index_bytes);
        if (index_bytes <= 0 || (int64_t)count * 2 > (int64_t)index_bytes) {
            uv2_probe_write_failure("unsupported_index_buffer",
                                    "the index range exceeds the bound "
                                    "unsigned-short buffer");
            g_uv2_probe_done = 1;
            return 0;
        }
    }
    for (int i = 0; i < guard.attribute_count; i++) {
        const uv2_attribute *attribute = &guard.attributes[i];
        if (!attribute->active || !attribute->enabled) {
            continue;
        }
        const char *reason_name = NULL;
        if (attribute->divisor != 0) {
            reason_name = "instanced attributes are not supported";
        } else if (!attribute->offset_known) {
            reason_name = "the attribute offset is not established";
        } else if (attribute->integer) {
            reason_name = "integer vertex attributes are not supported";
        } else if (attribute->size <= 0 || attribute->size > 4 ||
                   attribute->stride < 0 ||
                   eu4_gfx_gl_type_size((uint32_t)attribute->type) == 0) {
            reason_name = "the attribute layout is unsupported";
        }
        if (reason_name != NULL) {
            uv2_probe_write_failure("unsupported_attribute_state", reason_name);
            g_uv2_probe_done = 1;
            return 0;
        }
    }
    char reason[256] = "";
    char log[2048] = "";
    char vertex_sha[65];
    char fragment_sha[65];
    char *vertex_source = NULL;
    char *fragment_source = NULL;
    char *instrumented_fragment = NULL;
    int result = -1;
    uv2_target control = {0, 0, 0};
    uv2_target probe = {0, 0, 0};
    GLuint control_program = 0;
    GLuint probe_program = 0;
    uint8_t *pre_color = NULL;
    uint8_t *pre_depth_raw = NULL;
    const float *pre_depth = NULL;
    int owns_pre = 0;
    uint16_t *index_data = NULL;
    uint8_t *control_pixels = NULL;
    uint8_t *probe_pixels = NULL;
    char probe_fragment[UV2_PROBE_FRAGMENT_MAX];
    snprintf(probe_fragment, sizeof(probe_fragment),
             "#version 120\n"
             "varying vec2 %s;\n"
             "void main() {\n"
             "    gl_FragColor = vec4(%s, 0.0, 1.0);\n"
             "}\n",
             g_uv2_probe.varying, g_uv2_probe.varying);
    if (uv2_probe_program_sources((GLuint)program, &vertex_source,
                                  &fragment_source, vertex_sha, fragment_sha,
                                  reason, sizeof(reason)) != 0) {
        uv2_probe_write_failure("error", reason);
        goto done;
    }
    if (strstr(vertex_source, g_uv2_probe.varying) == NULL) {
        uv2_probe_write_failure("varying_missing",
                                "the original vertex stage does not declare "
                                "the uv2 varying");
        goto done;
    }
    if (sparse) {
        const char *failure = NULL;
        if (strcmp(vertex_sha, EU4_UV2_VERTEX_SHA) != 0)
            failure = "vertex_shader_digest";
        else if (strcmp(fragment_sha, EU4_UV2_FRAGMENT_SHA) != 0)
            failure = "fragment_shader_digest";
        else if (strcmp(g_uv2_probe.varying, "v_TEXCOORD1") != 0)
            failure = "varying";
        else
            failure = uv2_sparse_fragment(fragment_source, &instrumented_fragment);
        if (failure != NULL) {
            uv2_probe_write_failure(unsupported_draw, failure);
            goto done;
        }
    }
    GLuint control_vertex_shader = uv2_probe_compile_shader(
        GL_VERTEX_SHADER, vertex_source, log, sizeof(log));
    if (control_vertex_shader == 0) {
        uv2_probe_write_failure("shader_compile", log);
        goto done;
    }
    GLuint control_fragment_shader = uv2_probe_compile_shader(
        GL_FRAGMENT_SHADER, instrumented_fragment != NULL ? instrumented_fragment
                                                           : fragment_source,
        log, sizeof(log));
    if (control_fragment_shader == 0) {
        glDeleteShader(control_vertex_shader);
        uv2_probe_write_failure("shader_compile", log);
        goto done;
    }
    control_program = uv2_probe_link_program(
        control_vertex_shader, control_fragment_shader, (GLuint)program, log,
        sizeof(log));
    glDeleteShader(control_vertex_shader);
    glDeleteShader(control_fragment_shader);
    if (g_uv2_pixels.count == 0) {
        GLuint probe_vertex_shader = uv2_probe_compile_shader(
            GL_VERTEX_SHADER, vertex_source, log, sizeof(log));
        GLuint probe_fragment_shader =
            probe_vertex_shader == 0
                ? 0
                : uv2_probe_compile_shader(GL_FRAGMENT_SHADER, probe_fragment, log,
                                           sizeof(log));
        if (probe_fragment_shader != 0) {
            probe_program = uv2_probe_link_program(
                probe_vertex_shader, probe_fragment_shader, (GLuint)program, log,
                sizeof(log));
        }
        if (probe_vertex_shader != 0) {
            glDeleteShader(probe_vertex_shader);
        }
        if (probe_fragment_shader != 0) {
            glDeleteShader(probe_fragment_shader);
        }
    }
    if (control_program == 0 || (g_uv2_pixels.count == 0 && probe_program == 0)) {
        uv2_probe_write_failure("shader_link", log);
        goto done;
    }
    /* The exact pre-draw colour and depth are captured at the draw's entry
       hook; the control is seeded with both and the float probe target with
       the depth only.  A direct test invocation may fall back to reading the
       current state when no pending entry capture exists. */
    if (!g_uv2_probe_pending.ready && g_uv2_probe_require_pre) {
        snprintf(reason, sizeof(reason), "pre_state_unavailable");
        uv2_probe_write_failure("error", reason);
        goto done;
    }
    if (g_uv2_probe_pending.ready) {
        if (g_uv2_probe_pending.width != width ||
            g_uv2_probe_pending.height != height ||
            g_uv2_probe_pending.event != event ||
            g_uv2_probe_pending.program != (int)program ||
            g_uv2_probe_pending.framebuffer != (int)draw_framebuffer ||
            g_uv2_probe_pending.texture != (int)texture_name ||
            g_uv2_probe_pending.color == NULL) {
            snprintf(reason, sizeof(reason), "pending_state_mismatch");
            uv2_probe_write_failure("error", reason);
            goto done;
        }
        if (sparse && uv2_probe_reject_value(unsupported_draw, "pre_depth_bits",
                                             g_uv2_probe_pending.depth_bits, depth_bits)) {
            goto done;
        }
        pre_color = g_uv2_probe_pending.color;
        pre_depth_raw = g_uv2_probe_pending.depth;
        depth_bits = g_uv2_probe_pending.depth_bits;
        if (pre_depth_raw != NULL) {
            pre_depth = (const float *)pre_depth_raw;
        }
    } else {
        owns_pre = 1;
        if (read_observation_pixels((uint32_t)draw_framebuffer,
                                    GL_COLOR_ATTACHMENT0, 0, (uint32_t)width,
                                    (uint32_t)height, GL_BGRA,
                                    GL_UNSIGNED_BYTE, 4, &pre_color) != 0 ||
            pre_color == NULL) {
            snprintf(reason, sizeof(reason), "pre_color_readback");
            uv2_probe_write_failure("error", reason);
            goto done;
        }
        if (depth_bits > 0) {
            if (read_observation_pixels((uint32_t)draw_framebuffer,
                                        GL_COLOR_ATTACHMENT0, 0,
                                        (uint32_t)width, (uint32_t)height,
                                        GL_DEPTH_COMPONENT, GL_FLOAT, 4,
                                        &pre_depth_raw) != 0 ||
                pre_depth_raw == NULL) {
                snprintf(reason, sizeof(reason), "pre_depth_readback");
                uv2_probe_write_failure("error", reason);
                goto done;
            }
            pre_depth = (const float *)pre_depth_raw;
        }
    }
    if (uv2_probe_make_target(&control, GL_RGB8, GL_RGB, GL_UNSIGNED_BYTE,
                              (uint32_t)width, (uint32_t)height, depth_bits,
                              pre_color, pre_depth, reason,
                              sizeof(reason)) != 0 ||
        uv2_probe_make_target(&probe, GL_RGBA32F_VALUE, GL_RGBA, GL_FLOAT,
                              (uint32_t)width, (uint32_t)height,
                              g_uv2_pixels.count != 0 ? 0 : depth_bits,
                              NULL, pre_depth, reason, sizeof(reason)) != 0) {
        uv2_probe_write_failure("error", reason);
        goto done;
    }
    /* Index range identity and the per-attribute digests the engine draw will
       consume. */
    GLint element_buffer = guard.element_buffer;
    GLint index_buffer_size = 0;
    int32_t index_first = 0;
    int32_t index_last = 0;
    char index_sha[65] = "";
    if (element_buffer != 0) {
        glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, (GLuint)element_buffer);
        glGetBufferParameteriv(GL_ELEMENT_ARRAY_BUFFER, GL_BUFFER_SIZE,
                               &index_buffer_size);
        size_t index_bytes = (size_t)count * 2u;
        if (index_buffer_size > 0 && index_bytes <= (size_t)index_buffer_size) {
            index_data = malloc(index_bytes);
            if (index_data != NULL) {
                glGetBufferSubData(GL_ELEMENT_ARRAY_BUFFER, 0,
                                   (GLsizeiptr)index_bytes, index_data);
                sha256_hex(index_data, index_bytes, index_sha);
                if (eu4_gfx_indexed_range(index_data, count, base_vertex,
                                          &index_first, &index_last) != 0) {
                    index_first = 0;
                    index_last = -1;
                }
            }
        }
    }
    /* ---- control pass with the original shader pair ---- */
    glUseProgram(control_program);
    uv2_probe_apply_uniforms((GLuint)program, control_program);
    {
        uv2_attribute *attributes = guard.attributes;
        for (int i = 0; i < guard.attribute_count; i++) {
            uv2_attribute *attribute = &attributes[i];
            if (!attribute->active) {
                continue;
            }
            if (!attribute->enabled) {
                glDisableVertexAttribArray((GLuint)attribute->location);
                glVertexAttrib4fv((GLuint)attribute->location,
                                  attribute->current);
                continue;
            }
            glBindBuffer(GL_ARRAY_BUFFER, (GLuint)attribute->buffer);
            glVertexAttribPointer(
                (GLuint)attribute->location, attribute->size,
                (GLenum)attribute->type, attribute->normalized,
                attribute->stride,
                attribute->client_array
                    ? (const GLvoid *)attribute->client_pointer
                    : (const GLvoid *)(intptr_t)attribute->offset);
            glEnableVertexAttribArray((GLuint)attribute->location);
        }
    }
    if (g_uv2_pixels.count != 0) {
        glBindFramebuffer(GL_DRAW_FRAMEBUFFER, (GLuint)draw_framebuffer);
        if (index_data != NULL && index_last >= 0 && index_last < UINT16_MAX &&
            uv2_sparse_observe(&guard, control, probe,
                control_program, count, width, height, index_sha,
                pre_color, pre_depth_raw) == 0) {
            result = 1;
        } else {
            uv2_probe_write_failure("unsupported_sparse_state", "coverage_or_mrt");
        }
        goto done;
    }
    {
        GLint viewport[4];
        glGetIntegerv(GL_VIEWPORT, viewport);
        glBindFramebuffer(GL_DRAW_FRAMEBUFFER, control.framebuffer);
        glDrawBuffer(GL_COLOR_ATTACHMENT0);
        glViewport(viewport[0], viewport[1], viewport[2], viewport[3]);
        glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, (GLuint)element_buffer);
        glDrawElements((GLenum)mode, count, GL_UNSIGNED_SHORT,
                       (const GLvoid *)0);
    }
    if (read_observation_pixels(control.framebuffer, GL_COLOR_ATTACHMENT0, 0,
                                (uint32_t)width, (uint32_t)height, GL_BGRA,
                                GL_UNSIGNED_BYTE, 4,
                                &control_pixels) != 0 ||
        control_pixels == NULL) {
        uv2_probe_write_failure("error", "control_readback");
        goto done;
    }
    /* ---- uv2 probe pass with the original vertex stage ---- */
    glUseProgram(probe_program);
    uv2_probe_apply_uniforms((GLuint)program, probe_program);
    glDisable(GL_BLEND);
    glDisable(GL_DEPTH_TEST);
    glDisable(GL_ALPHA_TEST);
    glDisable(GL_COLOR_LOGIC_OP);
    glColorMask(GL_TRUE, GL_TRUE, GL_TRUE, GL_TRUE);
    {
        GLint viewport[4];
        glGetIntegerv(GL_VIEWPORT, viewport);
        glBindFramebuffer(GL_DRAW_FRAMEBUFFER, probe.framebuffer);
        glDrawBuffer(GL_COLOR_ATTACHMENT0);
        glViewport(viewport[0], viewport[1], viewport[2], viewport[3]);
        glClearColor(0.0f, 0.0f, 0.0f, 0.0f);
        glClear(GL_COLOR_BUFFER_BIT);
        glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, (GLuint)element_buffer);
        glDrawElements((GLenum)mode, count, GL_UNSIGNED_SHORT,
                       (const GLvoid *)0);
    }
    if (read_observation_pixels(0, 0, probe.color, (uint32_t)width,
                                (uint32_t)height, GL_RGBA, GL_FLOAT, 16,
                                &probe_pixels) != 0 ||
        probe_pixels == NULL) {
        uv2_probe_write_failure("error", "probe_readback");
        goto done;
    }
    result = 1;
done:
    /* Every path restores the engine state at draw exit. */
    if (g_uv2_probe_done == 0) {
        uv2_probe_restore_state(&guard);
    }
    /* Publish the descriptor and the two raw outputs after restoration, so a
       write failure cannot leave the engine in diagnostic state. */
    {
        size_t control_bytes = (size_t)width * (size_t)height * 4u;
        size_t probe_bytes = (size_t)width * (size_t)height * 16u;
        char control_file[FTRACE_NAME] = "uv2-control.bgra";
        char probe_file[FTRACE_NAME] = "uv2-probe.rgba32f";
        char control_sha[65] = "";
        char probe_sha[65] = "";
        int writes_ok = 0;
        if (result == 1 && control_pixels != NULL && probe_pixels != NULL) {
            char path[FTRACE_PATH];
            snprintf(path, sizeof(path), "%s/%s", g_uv2_probe.dir,
                     control_file);
            if (write_atomic(path, control_pixels, control_bytes) == 0) {
                sha256_hex(control_pixels, control_bytes, control_sha);
                snprintf(path, sizeof(path), "%s/%s", g_uv2_probe.dir,
                         probe_file);
                if (write_atomic(path, probe_pixels, probe_bytes) == 0) {
                    sha256_hex(probe_pixels, probe_bytes, probe_sha);
                    writes_ok = 1;
                }
            }
            if (!writes_ok) {
                uv2_probe_write_failure("error", "diagnostic_write");
            }
        }
        if (writes_ok) {
            /* Per-attribute and per-uniform records are computed here so the
               descriptor proves the input bytes.  The exact digests are also
               what the offline comparator checks against the captured event. */
            char *buffer = NULL;
            size_t size = 0;
            FILE *memory = open_memstream(&buffer, &size);
            if (memory != NULL) {
                json_out out = {memory, 0};
                jprintf(&out,
                        "{\"schema\":\"gfx-uv2-probe/v1\","
                        "\"side\":\"opengl\",\"status\":\"ok\","
                        "\"sealed\":%s,"
                        "\"session_id\":\"%s\",\"capture_frame_id\":%llu,"
                        "\"pid\":%d,\"event\":%d,\"program\":%d,"
                        "\"target\":{\"framebuffer\":%d,\"texture\":%d},"
                        "\"draw\":{\"entry\":\"%s\",\"mode\":%u,"
                        "\"count\":%d,\"first\":%d,\"instances\":%d,"
                        "\"base_vertex\":%d,\"indexed\":%s,"
                        "\"caller_rva\":\"0x%llx\"},"
                        "\"origin\":\"lower_left\","
                        "\"pixel_format\":\"RGBA32F\","
                        "\"width\":%d,\"height\":%d,\"row_stride\":%d,"
                        "\"raw_file\":\"%s\",\"raw_bytes\":%zu,"
                        "\"raw_sha256\":\"%s\","
                        "\"control\":{\"file\":\"%s\",\"sha256\":\"%s\"},"
                        "\"sources\":{\"vertex_sha256\":\"%s\","
                        "\"fragment_sha256\":\"%s\"},"
                        "\"indices\":{\"count\":%d,\"sha256\":\"%s\"}",
                        bool_text(g.seal_enabled != 0), g.session_id,
                        (unsigned long long)g.capture_id,
                        (int)getpid(), event, (int)program,
                        (int)draw_framebuffer, (int)texture_name,
                        indexed ? "_GfxDrawIndexed" : "_GfxDraw", mode, count,
                        first, instances, base_vertex,
                        bool_text(indexed),
                        (unsigned long long)g_uv2_probe_pending.caller_rva,
                        width, height,
                        width * 16, probe_file, probe_bytes, probe_sha,
                        control_file, control_sha, vertex_sha, fragment_sha,
                        count, index_sha);
                /* Attribute and uniform input digests, computed from the live
                   driver state and compared offline with the sealed capture. */
                jprintf(&out, ",\"attributes\":[");
                int emitted = 0;
                for (int i = 0; i < guard.attribute_count; i++) {
                    uv2_attribute *attribute = &guard.attributes[i];
                    if (!attribute->active) {
                        continue;
                    }
                    char digest[65] = "";
                    int digest_ok = attribute->enabled
                        ? uv2_probe_attribute_digest(
                              attribute, indexed, count, instances, first,
                              index_first, index_last, digest)
                        : 0;
                    jprintf(&out,
                            "%s{\"name\":\"%s\",\"location\":%d,"
                            "\"enabled\":%s,\"sha256\":\"%s\","
                            "\"digest_ok\":%s}",
                            emitted > 0 ? "," : "", attribute->name,
                            attribute->location,
                            bool_text(attribute->enabled), digest,
                            bool_text(digest_ok == 0 && attribute->enabled));
                    emitted++;
                }
                jprintf(&out, "],\"uniforms\":[");
                emitted = 0;
                GLint uniform_count = 0;
                glGetProgramiv((GLuint)program, GL_ACTIVE_UNIFORMS,
                               &uniform_count);
                for (GLint i = 0; i < uniform_count; i++) {
                    char name[256];
                    GLsizei length = 0;
                    GLint uniform_size = 0;
                    GLenum type = 0;
                    glGetActiveUniform((GLuint)program, (GLuint)i, sizeof(name),
                                       &length, &uniform_size, &type, name);
                    char base[256];
                    snprintf(base, sizeof(base), "%s", name);
                    char *bracket = strstr(base, "[0]");
                    if (bracket != NULL) {
                        *bracket = 0;
                    }
                    uv2_uniform_shape shape;
                    if (uv2_probe_uniform_shape(type, &shape) != 0) {
                        continue;
                    }
                    if (shape.is_sampler) {
                        GLint location =
                            glGetUniformLocation((GLuint)program, base);
                        GLint unit = -1;
                        if (location >= 0) {
                            glGetUniformiv((GLuint)program, location, &unit);
                        }
                        jprintf(&out,
                                "%s{\"name\":\"%s\",\"kind\":\"sampler\","
                                "\"unit\":%d}",
                                emitted > 0 ? "," : "", base, (int)unit);
                        emitted++;
                        continue;
                    }
                    int elements = uniform_size > 0 ? uniform_size : 1;
                    uint8_t *raw = malloc((size_t)elements *
                                          (size_t)shape.components *
                                          sizeof(GLfloat));
                    if (raw == NULL) {
                        continue;
                    }
                    size_t used = 0;
                    for (int element = 0; element < elements; element++) {
                        char element_name[300];
                        if (elements == 1) {
                            snprintf(element_name, sizeof(element_name), "%s",
                                     base);
                        } else {
                            snprintf(element_name, sizeof(element_name),
                                     "%s[%d]", base, element);
                        }
                        GLint location =
                            glGetUniformLocation((GLuint)program, element_name);
                        if (location < 0) {
                            continue;
                        }
                        if (shape.is_int) {
                            glGetUniformiv((GLuint)program, location,
                                           (GLint *)(raw + used));
                        } else {
                            glGetUniformfv((GLuint)program, location,
                                           (GLfloat *)(raw + used));
                        }
                        used += (size_t)shape.components * sizeof(GLfloat);
                    }
                    char digest[65] = "";
                    if (used > 0) {
                        sha256_hex(raw, used, digest);
                    }
                    jprintf(&out,
                            "%s{\"name\":\"%s\",\"sha256\":\"%s\"}",
                            emitted > 0 ? "," : "", name, digest);
                    emitted++;
                    free(raw);
                }
                jprintf(&out, "]}");
                fclose(memory);
                if (!out.failed && buffer != NULL) {
                    char path[FTRACE_PATH];
                    snprintf(path, sizeof(path), "%s/uv2-probe.json",
                             g_uv2_probe.dir);
                    (void)write_atomic(path, buffer, size);
                }
            }
            free(buffer);
        }
    }
    /* Descriptor digests read live buffers and can change their bindings. */
    uv2_probe_restore_state(&guard);
    if (owns_pre) {
        free(pre_color);
        free(pre_depth_raw);
    }
    free(control_pixels);
    free(probe_pixels);
    free(vertex_source);
    free(fragment_source);
    free(instrumented_fragment);
    free(index_data);
    if (control_program != 0) {
        glDeleteProgram(control_program);
    }
    if (probe_program != 0) {
        glDeleteProgram(probe_program);
    }
    uv2_probe_delete_target(&control);
    uv2_probe_delete_target(&probe);
    g_uv2_probe_done = 1;
    return result;
}

/* Entry half: captures the engine target's pre-draw colour and depth for the
 * selected event.  Nothing engine-visible changes. */
static void uv2_probe_prepare(int event, int indexed, unsigned mode, int count,
                              int first, int instances, int base_vertex,
                              uintptr_t master, uint64_t caller_rva) {
    uv2_probe_clear_pending();
    if (!g_uv2_probe.enabled || g_uv2_probe_done) {
        return;
    }
    if (event != g_uv2_probe.event) {
        return;
    }
    g_uv2_probe_pending.attempted = 1;
    g_uv2_probe_pending.event = event;
    g_uv2_probe_pending.indexed = indexed;
    g_uv2_probe_pending.mode = mode;
    g_uv2_probe_pending.count = count;
    g_uv2_probe_pending.first = first;
    g_uv2_probe_pending.instances = instances;
    g_uv2_probe_pending.base_vertex = base_vertex;
    g_uv2_probe_pending.master = master;
    g_uv2_probe_pending.caller_rva = caller_rva;
    if (!indexed || count <= 0 || mode == 0 || instances != 1 ||
        base_vertex != 0 || first != 0) {
        return;
    }
    GLint program = 0;
    glGetIntegerv(GL_CURRENT_PROGRAM, &program);
    GLint draw_framebuffer = 0;
    glGetIntegerv(GL_DRAW_FRAMEBUFFER_BINDING, &draw_framebuffer);
    GLint object_type = 0;
    GLint texture_name = 0;
    glGetFramebufferAttachmentParameteriv(
        GL_DRAW_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
        GL_FRAMEBUFFER_ATTACHMENT_OBJECT_TYPE, &object_type);
    glGetFramebufferAttachmentParameteriv(
        GL_DRAW_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
        GL_FRAMEBUFFER_ATTACHMENT_OBJECT_NAME, &texture_name);
    if (program <= 0 || object_type != GL_TEXTURE) {
        return;
    }
    GLint draw_buffer = 0;
    glGetIntegerv(GL_DRAW_BUFFER, &draw_buffer);
    if (draw_buffer != GL_COLOR_ATTACHMENT0) {
        return;
    }
    GLint level = 0;
    glGetFramebufferAttachmentParameteriv(
        GL_DRAW_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
        GL_FRAMEBUFFER_ATTACHMENT_TEXTURE_LEVEL, &level);
    GLint previous_texture = 0;
    glGetIntegerv(GL_TEXTURE_BINDING_2D, &previous_texture);
    glBindTexture(GL_TEXTURE_2D, (GLuint)texture_name);
    GLint width = 0, height = 0, internal_format = 0;
    glGetTexLevelParameteriv(GL_TEXTURE_2D, level, GL_TEXTURE_WIDTH, &width);
    glGetTexLevelParameteriv(GL_TEXTURE_2D, level, GL_TEXTURE_HEIGHT, &height);
    glGetTexLevelParameteriv(GL_TEXTURE_2D, level,
                             GL_TEXTURE_INTERNAL_FORMAT, &internal_format);
    glBindTexture(GL_TEXTURE_2D, (GLuint)previous_texture);
    if (width <= 0 || height <= 0 || width > 8192 || height > 8192 ||
        internal_format != GL_RGB8) {
        return;
    }
    GLint samples = 0;
    glGetIntegerv(GL_SAMPLES, &samples);
    GLint stencil_test = 0;
    glGetIntegerv(GL_STENCIL_TEST, &stencil_test);
    GLint alpha_test = 0;
    glGetIntegerv(GL_ALPHA_TEST, &alpha_test);
    if (samples > 0 || stencil_test || alpha_test) {
        return;
    }
    GLint depth_type = 0;
    GLint depth_bits = uv2_probe_depth_size(&depth_type);
    if (depth_bits < 0) {
        return;
    }
    uint8_t *color = NULL;
    if (read_observation_pixels((uint32_t)draw_framebuffer,
                                GL_COLOR_ATTACHMENT0, 0, (uint32_t)width,
                                (uint32_t)height, GL_BGRA, GL_UNSIGNED_BYTE, 4,
                                &color) != 0 ||
        color == NULL) {
        free(color);
        return;
    }
    uint8_t *depth = NULL;
    if (depth_bits > 0) {
        if (read_observation_pixels((uint32_t)draw_framebuffer,
                                    GL_COLOR_ATTACHMENT0, 0, (uint32_t)width,
                                    (uint32_t)height, GL_DEPTH_COMPONENT,
                                    GL_FLOAT, 4, &depth) != 0 ||
            depth == NULL) {
            free(color);
            free(depth);
            return;
        }
    }
    g_uv2_probe_pending.program = (int)program;
    g_uv2_probe_pending.framebuffer = (int)draw_framebuffer;
    g_uv2_probe_pending.texture = (int)texture_name;
    g_uv2_probe_pending.width = width;
    g_uv2_probe_pending.height = height;
    g_uv2_probe_pending.depth_bits = depth_bits;
    g_uv2_probe_pending.color = color;
    g_uv2_probe_pending.depth = depth;
    g_uv2_probe_pending.ready = 1;
}

/* Exit half: runs the diagnostic against the applied draw state. */
static void uv2_probe_finish(void) {
    if (!g_uv2_probe_pending.attempted || g_uv2_probe_done) {
        uv2_probe_clear_pending();
        return;
    }
    /* The core classifies identity/format/state failures first; a missing
       entry capture is then refused instead of falling back to the post-draw
       target contents. */
    g_uv2_probe_require_pre = 1;
    (void)uv2_probe_execute(
        g_uv2_probe_pending.event, g_uv2_probe_pending.indexed,
        g_uv2_probe_pending.mode, g_uv2_probe_pending.count,
        g_uv2_probe_pending.first, g_uv2_probe_pending.instances,
        g_uv2_probe_pending.base_vertex, g_uv2_probe_pending.master);
    g_uv2_probe_require_pre = 0;
    uv2_probe_clear_pending();
}

int eu4_gfx_uv2_probe_test_attribute_count_ok(int count) {
    return uv2_probe_attribute_count_ok((GLint)count);
}

int eu4_gfx_uv2_probe_test_configure(const char *directory, int event,
                                     int program, int framebuffer, int texture,
                                     const char *pixels) {
    if (directory == NULL || directory[0] != '/' ||
        eu4_uv2_parse_pixels(pixels, &g_uv2_pixels) != 0) {
        return -1;
    }
    memset(&g_uv2_probe, 0, sizeof(g_uv2_probe));
    snprintf(g.dir, sizeof(g.dir), "%s", directory);
    g_uv2_sparse_sha[0] = 0;
    g_uv2_probe.enabled = 1;
    g_uv2_probe.event = event;
    g_uv2_probe.program = program;
    g_uv2_probe.framebuffer = framebuffer;
    g_uv2_probe.texture = texture;
    g_uv2_probe.require_identity =
        program >= 0 && framebuffer >= 0 && texture >= 0;
    snprintf(g_uv2_probe.dir, sizeof(g_uv2_probe.dir), "%s", directory);
    snprintf(g_uv2_probe.varying, sizeof(g_uv2_probe.varying), "%s",
             "v_TEXCOORD1");
    g_uv2_probe_done = 0;
    g_uv2_probe_require_pre = 0;
    uv2_probe_clear_pending();
    return 0;
}

int eu4_gfx_uv2_probe_test_run(int event, int indexed, unsigned mode, int count,
                               int first, int instances, int base_vertex,
                               uintptr_t master) {
    uv2_probe_prepare(event, indexed, mode, count, first, instances,
                      base_vertex, master, 0);
    if (!g_uv2_probe_pending.attempted) {
        return 0;
    }
    g_uv2_probe_require_pre = 1;
    int result = uv2_probe_execute(
        event, indexed, mode, count, first, instances, base_vertex, master);
    g_uv2_probe_require_pre = 0;
    uv2_probe_clear_pending();
    return result;
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
    /* Draw records are emitted on exit; the checkpoint must observe the
       framebuffer before the selected draw executes, so it belongs here. */
    if (maybe_capture_default_checkpoint_locked() < 0) {
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
    if (!g.gl_drawable_valid) {
        /* GL_DRAW_FRAMEBUFFER_BINDING is part of the default drawable state.
         * A draw bound to an offscreen FBO describes that FBO, not the
         * window drawable; retry on later draws until the default is bound.
         * The sample count and bit depths are queried from the driver while
         * framebuffer zero is current; an FBO attachment is never a source. */
        ftrace_drawable_state drawable;
        query_drawable_state(&drawable);
        g.gl_drawable_binding = drawable.binding;
        if (drawable.valid) {
            g.gl_samples = drawable.samples;
            g.gl_sample_buffers = drawable.sample_buffers;
            g.gl_depth_bits = drawable.depth_bits;
            g.gl_stencil_bits = drawable.stencil_bits;
            g.gl_red_bits = drawable.red_bits;
            g.gl_green_bits = drawable.green_bits;
            g.gl_blue_bits = drawable.blue_bits;
            g.gl_alpha_bits = drawable.alpha_bits;
            g.gl_double_buffer = drawable.double_buffer;
            g.gl_stereo = drawable.stereo;
            g.gl_framebuffer_srgb = drawable.framebuffer_srgb;
            g.gl_dither = drawable.dither;
            g.gl_multisample = drawable.multisample;
            g.gl_draw_buffer = drawable.draw_buffer;
            g.gl_read_buffer = drawable.read_buffer;
            g.gl_drawable_valid = 1;
        }
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
        int selected_pre = !g.test_only && g.prefix_caller != 0 &&
                           g.prefix_caller == call->caller_rva;
        if (selected_pre) {
            begin_selected(call->call_id, !indexed && !instanced,
                           g_pending.mode, g_pending.first, g_pending.count);
        }
        int observe_pre = selected_pre;
        for (int i = 0; i < g.pre_post_count && !observe_pre; i++) {
            observe_pre = g.pre_post_callers[i] == call->caller_rva;
        }
        if (observe_pre) {
            char *pre_json = NULL;
            size_t pre_size = 0;
            FILE *pre_stream = open_memstream(&pre_json, &pre_size);
            if (pre_stream != NULL) {
                json_out pre_out = {pre_stream, 0};
                int captured = capture_target_pixels("pre", "pre_image", &pre_out, NULL, NULL);
                fclose(pre_stream);
                if (captured == 0 && !pre_out.failed && pre_json != NULL &&
                    pre_size < sizeof(g.pre_image_json)) {
                    snprintf(g.pre_image_json, sizeof(g.pre_image_json), "%s", pre_json);
                    g.pre_image_valid = 1;
                    g.pre_image_call_id = call->call_id;
                } else if (selected_pre) {
                    fail_capture_locked("selected_pre_image_unavailable");
                }
                free(pre_json);
            } else if (selected_pre) {
                fail_capture_locked("selected_pre_image_unavailable");
            }
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
    /* The diagnostic probe runs before the engine's own draw, against the
       exact pre-draw state that draw is about to consume.  It restores every
       binding it changes and never touches the engine's render target. */
    if (g.armed && g.events != NULL && !g.test_only && g_uv2_probe.enabled) {
        uv2_probe_prepare(g.event_count, g_pending.indexed,
                          (unsigned)g_pending.mode, g_pending.count,
                          g_pending.first, g_pending.instances,
                          g_pending.base_vertex, g_pending.master,
                          call->caller_rva);
    }
    pthread_mutex_unlock(&g_lock);
    eu4_gfx_frame_sealed_dispatch();
}

static void observe_draw_exit_locked(uint64_t caller_rva) {
    if (!g.prefix_enabled) {
        return;
    }
    int event = g.event_count - 1;
    if (g.prefix_caller != 0 && caller_rva == g.prefix_caller) {
        if (event >= FTRACE_MAX_PREFIX_EVENTS || g.prefix_events[0] >= 0) {
            fail_capture_locked("observation_caller_outside_unique_scope");
            return;
        }
        g.prefix_events[0] = event;
        g.prefix_end = event;
    }
    if (event <= g.prefix_end) {
        (void)prefix_emit_locked(event, "post_event", "draw");
    }
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
                int checkpoint_slot = checkpoint_pending_slot_locked();
                int target_fb = g.test_target_fb_override >= 0
                    ? g.test_target_fb_override : g.last_capture_target_fb;
                const char *checkpoint_prefix = "";
                const char *checkpoint_body = "";
                if (checkpoint_slot >= 0) {
                    if (target_fb != 0) {
                        fail_capture_locked("default_checkpoint_not_default");
                    } else {
                        checkpoint_prefix = ",\"default_checkpoint\":";
                        checkpoint_body = g.checkpoint_json[checkpoint_slot];
                    }
                }
                char body[2048];
                int length = snprintf(
                    body, sizeof body,
                    "{\"kind\":\"draw\",\"index\":%d,\"test\":true,"
                    "\"draw\":{\"entry\":\"_GfxDraw\",\"caller_rva\":\"0x%llx\"}%s%s}",
                    g.event_count, (unsigned long long)call->caller_rva,
                    checkpoint_prefix, checkpoint_body);
                if (length > 0 && (size_t)length < sizeof body &&
                    emit_event(body)) {
                    g.event_count++;
                    g.draw_events++;
                    checkpoint_clear_pending_locked();
                    observe_draw_exit_locked(call->caller_rva);
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
        int checkpoint_slot = checkpoint_pending_slot_locked();
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
            int selected_pre = g.prefix_caller != 0 && g.prefix_caller == call->caller_rva;
            if (selected_pre) {
                if (!capture_selected_submission(&out, call->call_id)) {
                    fail_capture_locked("selected_submission_or_target_unverified");
                }
                g_selected.call_id = 0;
            }
            if (selected_pre && (!g.pre_image_valid || g.pre_image_call_id != call->call_id)) {
                fail_capture_locked("selected_pre_image_unattached");
            }
            if (g.pre_image_valid && g.pre_image_call_id == call->call_id) {
                jprintf(&out, ",%s", g.pre_image_json);
                char *post_json = NULL;
                size_t post_size = 0;
                FILE *post_stream = selected_pre ? NULL : open_memstream(&post_json, &post_size);
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
            /* capture_draw_state -> capture_target has now recorded the
               event's authoritative target.  Decide admissibility from that
               exact value, never from a value captured before the draw body. */
            if (checkpoint_slot >= 0) {
                if (g.last_capture_target_fb != 0) {
                    fail_capture_locked("default_checkpoint_not_default");
                } else {
                    jprintf(&out, ",\"default_checkpoint\":%s",
                            g.checkpoint_json[checkpoint_slot]);
                }
            }
            jprintf(&out, "}");
            fclose(memory);
            if (!out.failed && buffer != NULL) {
                emit_event(buffer);
                g.event_count++;
                g.draw_events++;
                checkpoint_clear_pending_locked();
                observe_draw_exit_locked(call->caller_rva);
            } else {
                fail_capture_locked(g_capture_failure_reason != NULL
                                        ? g_capture_failure_reason
                                        : "draw_event_capture");
            }
            free(buffer);
        }
    }
    if (g_uv2_probe.enabled && !g.test_only) {
        uv2_probe_finish();
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

void eu4_gfx_frame_trace_copy_exit(void) {
    if (!g.enabled || !g.prefix_enabled || !g.armed) {
        return;
    }
    pthread_mutex_lock(&g_lock);
    int event = g.prefix_copy_pending_event;
    int texture = g.prefix_copy_pending_texture;
    g.prefix_copy_pending_event = -1;
    g.prefix_copy_pending_texture = -1;
    if (event >= 0 && texture > 0) {
        (void)prefix_emit_texture_locked(event, "post_event", "resolve",
                                         texture);
    }
    pthread_mutex_unlock(&g_lock);
    eu4_gfx_frame_sealed_dispatch();
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
