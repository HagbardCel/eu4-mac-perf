#import "eu4_gfx_buffers.h"
#import "eu4_gfx_init.h"
#import "eu4_gfx_texture.h"

/* Hosts that link this file without the shader module must still link; the
   runtime cache is optional for the lifecycle-only diagnostics, so it is
   resolved at run time instead of creating a link-time reference. */
#include <dlfcn.h>
typedef int (*eu4_gfx_cache_loader)(const char *path);
#import "eu4_gfx_logical_capture.h"
#import "eu4_gfx_checkpoint.h"
#import "eu4_metal_events.h"

#import <Metal/Metal.h>
#import <QuartzCore/CAMetalLayer.h>
#import <dlfcn.h>
#import <new>
#import <pthread.h>
#import <stdio.h>
#import <string.h>
#import <unistd.h>

void eu4_gfx_write_default_depth_stencil(uint8_t desc[EU4_GFX_DEPTH_DESC_SIZE]) {
    uint64_t depth_word = 0x300000001ULL;
    uint32_t mask = 0xffffffffu;
    uint32_t zero = 0;
    uint64_t stencil_word = 0x700000000ULL;
    desc[0] = 1;
    memcpy(desc + 4, &depth_word, sizeof depth_word);
    desc[0xc] = 0;
    memcpy(desc + 0x10, &mask, sizeof mask);
    memcpy(desc + 0x14, &mask, sizeof mask);
    memcpy(desc + 0x18, &zero, sizeof zero);
    memcpy(desc + 0x1c, &zero, sizeof zero);
    memcpy(desc + 0x20, &stencil_word, sizeof stencil_word);
}

static uint32_t load_u32(const uint8_t *desc, int offset) {
    uint32_t value;
    memcpy(&value, desc + offset, sizeof value);
    return value;
}

/* Accepts only the stores in GetDefaultDepthStencil. Other patterns are refused. */
static int accept_default(const uint8_t *desc) {
    if (desc == NULL) {
        return -1;
    }
    if (desc[0] != 1 || load_u32(desc, 4) != 1 || load_u32(desc, 8) != 3 || desc[0xc] != 0) {
        return -1;
    }
    if (load_u32(desc, 0x10) != 0xffffffffu || load_u32(desc, 0x14) != 0xffffffffu) {
        return -1;
    }
    if (load_u32(desc, 0x18) != 0 || load_u32(desc, 0x1c) != 0 || load_u32(desc, 0x20) != 0 ||
        load_u32(desc, 0x24) != 7) {
        return -1;
    }
    return 0;
}

static void apply_stencil(MTLStencilDescriptor *face) {
    /* Compare 7 is GL_ALWAYS. Op 0 is GL_KEEP (0x1e00). Both masks are all-ones. */
    face.stencilCompareFunction = MTLCompareFunctionAlways;
    face.stencilFailureOperation = MTLStencilOperationKeep;
    face.depthFailureOperation = MTLStencilOperationKeep;
    face.depthStencilPassOperation = MTLStencilOperationKeep;
    face.readMask = 0xffffffffu;
    face.writeMask = 0xffffffffu;
}

static MTLDepthStencilDescriptor *descriptor_for(const uint8_t *desc) {
    if (accept_default(desc) != 0) {
        return nil;
    }
    MTLDepthStencilDescriptor *descriptor = [MTLDepthStencilDescriptor new];
    /* Engine compare 3 is GL_LEQUAL (3 + 0x200). */
    descriptor.depthCompareFunction = MTLCompareFunctionLessEqual;
    descriptor.depthWriteEnabled = YES;
    descriptor.frontFaceStencil = [MTLStencilDescriptor new];
    descriptor.backFaceStencil = [MTLStencilDescriptor new];
    apply_stencil(descriptor.frontFaceStencil);
    apply_stencil(descriptor.backFaceStencil);
    return descriptor;
}

static int face_matches(MTLStencilDescriptor *face, const eu4_gfx_depth_view *view) {
    return face != nil && (int)face.stencilCompareFunction == view->stencil_compare &&
           (int)face.stencilFailureOperation == view->stencil_fail &&
           (int)face.depthFailureOperation == view->stencil_depth_fail &&
           (int)face.depthStencilPassOperation == view->stencil_pass &&
           face.readMask == view->stencil_read_mask && face.writeMask == view->stencil_write_mask;
}

int eu4_gfx_default_depth_stencil_view(const uint8_t *desc, eu4_gfx_depth_view *out) {
    MTLDepthStencilDescriptor *descriptor = descriptor_for(desc);
    if (descriptor == nil || out == NULL) {
        return -1;
    }
    out->depth_compare = (int)descriptor.depthCompareFunction;
    out->depth_write = descriptor.isDepthWriteEnabled ? 1 : 0;
    out->stencil_compare = (int)descriptor.frontFaceStencil.stencilCompareFunction;
    out->stencil_fail = (int)descriptor.frontFaceStencil.stencilFailureOperation;
    out->stencil_depth_fail = (int)descriptor.frontFaceStencil.depthFailureOperation;
    out->stencil_pass = (int)descriptor.frontFaceStencil.depthStencilPassOperation;
    out->stencil_read_mask = descriptor.frontFaceStencil.readMask;
    out->stencil_write_mask = descriptor.frontFaceStencil.writeMask;
    if (!face_matches(descriptor.backFaceStencil, out)) {
        return -1;
    }
    return 0;
}

void *eu4_gfx_default_depth_stencil(void *device, const uint8_t *desc) {
    if (device == NULL) {
        return NULL;
    }
    MTLDepthStencilDescriptor *descriptor = descriptor_for(desc);
    if (descriptor == nil) {
        return NULL;
    }
    id<MTLDevice> borrowed = (__bridge id<MTLDevice>)device;
    id<MTLDepthStencilState> state = [borrowed newDepthStencilStateWithDescriptor:descriptor];
    if (state == nil) {
        return NULL;
    }
    return (__bridge_retained void *)state;
}

enum { kBlendRva = 0x15c6760, kDepthRva = 0x15c6784, kRasterRva = 0x15c67bb };

static void *g_test_layer;
static uint8_t *g_image;
static void **g_slot;
static void *(*g_from_id)(uint32_t);
static void (*g_window_size)(void *, int *, int *);
static void (*g_write_blend)(void *);
static void (*g_write_depth)(void *);
static void (*g_write_raster)(void *);
static void (*g_terminate)(const char *);
static int g_throw_at;
static int g_allocs;
static int g_no_device;
static int g_fail_offscreen_alloc;
static int g_fail_game_alloc;
static int g_fail_next_drawable;
static int g_fail_present_staging_alloc;
static int g_fail_next_game_drain;
static void *g_present_drawable;
static void *g_present_readback;
static size_t g_present_readback_cap;
static int g_enable_game_window;
static int g_phase;
static int g_succeeded;
static void *g_master;
static int g_releases;
static uint64_t g_init_thread;

/* Committed snapshot plus the in-progress transition published by Cocoa/main.
   Guarded separately from the renderer; publication never reads the engine
   master and the renderer never writes this state. */
typedef struct {
    uint32_t width;
    uint32_t height;
    uint64_t epoch;
    int valid;
    int transitioning;
    uint32_t transition_width;
    uint32_t transition_height;
} eu4_physical_geometry;

static eu4_physical_geometry g_physical_geometry;
static pthread_mutex_t g_physical_geometry_lock = PTHREAD_MUTEX_INITIALIZER;

void eu4_gfx_publish_geometry_transition(uint32_t width, uint32_t height) {
    if (width == 0 || height == 0) {
        return;
    }
    pthread_mutex_lock(&g_physical_geometry_lock);
    g_physical_geometry.transitioning = 1;
    g_physical_geometry.transition_width = width;
    g_physical_geometry.transition_height = height;
    pthread_mutex_unlock(&g_physical_geometry_lock);
}

void eu4_gfx_publish_physical_geometry(uint32_t width, uint32_t height) {
    if (width == 0 || height == 0) {
        return;
    }
    pthread_mutex_lock(&g_physical_geometry_lock);
    g_physical_geometry.width = width;
    g_physical_geometry.height = height;
    g_physical_geometry.epoch += 1;
    g_physical_geometry.valid = 1;
    g_physical_geometry.transitioning = 0;
    pthread_mutex_unlock(&g_physical_geometry_lock);
}

int eu4_gfx_physical_geometry_snapshot(eu4_gfx_physical_geometry_state *out) {
    if (out == NULL) {
        return 0;
    }
    pthread_mutex_lock(&g_physical_geometry_lock);
    out->width = g_physical_geometry.width;
    out->height = g_physical_geometry.height;
    out->epoch = g_physical_geometry.epoch;
    out->valid = g_physical_geometry.valid;
    out->transition_in_progress = g_physical_geometry.transitioning;
    out->transition_width = g_physical_geometry.transition_width;
    out->transition_height = g_physical_geometry.transition_height;
    int valid = g_physical_geometry.valid;
    pthread_mutex_unlock(&g_physical_geometry_lock);
    return valid;
}

static int read_physical_geometry(uint32_t *width, uint32_t *height, uint64_t *epoch,
                                  int *transitioning) {
    int valid;
    pthread_mutex_lock(&g_physical_geometry_lock);
    valid = g_physical_geometry.valid;
    if (width != NULL) {
        *width = g_physical_geometry.width;
    }
    if (height != NULL) {
        *height = g_physical_geometry.height;
    }
    if (epoch != NULL) {
        *epoch = g_physical_geometry.epoch;
    }
    if (transitioning != NULL) {
        *transitioning = g_physical_geometry.transitioning;
    }
    pthread_mutex_unlock(&g_physical_geometry_lock);
    return valid;
}

static uint64_t current_thread(void) {
    uint64_t thread = 0;
    (void)pthread_threadid_np(pthread_self(), &thread);
    return thread;
}

static const void *g_stop_caller;
static const void *g_present_caller;
static eu4_gfx_checkpoint_hook g_checkpoint_after_clear;
static _Thread_local const char *g_clear_reason;
static eu4_gfx_checkpoint_hook g_checkpoint_before_present;
static eu4_gfx_checkpoint_hook g_checkpoint_after_present;
static void *load_ptr(void *master, int offset);

void eu4_gfx_set_checkpoint_hooks(eu4_gfx_checkpoint_hook after_clear,
                                  eu4_gfx_checkpoint_hook before_present,
                                  eu4_gfx_checkpoint_hook after_present) {
    g_checkpoint_after_clear = after_clear;
    g_checkpoint_before_present = before_present;
    g_checkpoint_after_present = after_present;
}

uint8_t *eu4_gfx_image(void) { return g_image; }

const void *eu4_gfx_test_stop_caller(void) { return g_stop_caller; }

const void *eu4_gfx_current_present_caller(void) { return g_present_caller; }

void *eu4_gfx_immediate_master(void) {
    return g_master;
}

void *eu4_gfx_backend_token(void) {
    if (!g_succeeded || g_master == NULL) {
        return NULL;
    }
    return load_ptr(g_master, 0x180);
}

void *eu4_gfx_metal_device(void) {
    void *raw = eu4_gfx_backend_token();
    if (raw == NULL) {
        return NULL;
    }
    EU4GfxBackend *backend = (__bridge EU4GfxBackend *)raw;
    return (__bridge void *)backend.device;
}

__attribute__((noreturn)) void eu4_gfx_stop(const char *symbol, const char *reason,
                                            const void *caller) {
    g_stop_caller = caller;
    const char *phase = "before_init";
    int value = eu4_gfx_phase();
    if (value == 1) {
        phase = "during_init";
    } else if (value == 2) {
        phase = "after_init";
    }
    eu4_metal_events_stop(phase, symbol, reason, caller);
    if (g_terminate != NULL) {
        g_terminate(reason);
    }
    _exit(2);
}

void eu4_gfx_write_default_blend(void *dest) {
    uint8_t *bytes = (uint8_t *)dest;
    static const uint8_t at4[16] = {1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0};
    static const uint8_t at14[16] = {0, 0, 0, 0, 0, 0, 0, 0, 0x0f, 0, 0, 0, 6, 0, 0, 0};
    bytes[0] = 0;
    bytes[1] = 0;
    memcpy(bytes + 4, at4, sizeof at4);
    memcpy(bytes + 0x14, at14, sizeof at14);
}

void eu4_gfx_write_default_raster(void *dest) {
    uint8_t *bytes = (uint8_t *)dest;
    uint64_t factors = 0x200000002ULL;
    memcpy(bytes, &factors, sizeof factors);
    bytes[8] = 0;
    memset(bytes + 0xc, 0, 8);
    memset(bytes + 0x12, 0, 8);
}

__attribute__((weak)) void *eu4_metal_child_layer(void) { return g_test_layer; }

void eu4_gfx_set_layer(void *layer) { g_test_layer = layer; }

void eu4_gfx_set_image(uint8_t *image, void **master_slot) {
    g_image = image;
    g_slot = master_slot;
}

void eu4_gfx_set_sdl(void *(*window_from_id)(uint32_t), void (*window_size)(void *, int *, int *)) {
    g_from_id = window_from_id;
    g_window_size = window_size;
}

void eu4_gfx_set_default_writers(void (*blend)(void *), void (*depth)(void *), void (*raster)(void *)) {
    g_write_blend = blend;
    g_write_depth = depth;
    g_write_raster = raster;
}

void eu4_gfx_set_terminate(void (*fn)(const char *)) { g_terminate = fn; }

void eu4_gfx_test_throw_on_alloc(int n) { g_throw_at = n; }

void eu4_gfx_test_no_device(int on) { g_no_device = on; }

void eu4_gfx_test_fail_offscreen_alloc(int on) { g_fail_offscreen_alloc = on; }

void eu4_gfx_test_fail_game_alloc(int on) { g_fail_game_alloc = on; }

void eu4_gfx_test_fail_next_drawable(int on) { g_fail_next_drawable = on; }

void eu4_gfx_test_fail_present_staging_alloc(int on) { g_fail_present_staging_alloc = on; }

void eu4_gfx_test_fail_next_game_drain(int on) { g_fail_next_game_drain = on; }

void eu4_gfx_test_set_present_drawable(void *texture) { g_present_drawable = texture; }

void eu4_gfx_test_set_present_readback(void *dest, size_t capacity) {
    g_present_readback = dest;
    g_present_readback_cap = capacity;
}

__attribute__((weak)) void eu4_metal_ensure_visible(void) {}

__attribute__((weak)) void eu4_metal_window_diagnostics(char *out,
                                                        size_t capacity) {
    if (out != NULL && capacity > 0) {
        out[0] = 0;
    }
}

__attribute__((weak)) int eu4_metal_window_state(uint64_t *number, int *visible,
                                                 int *occluded, double *width,
                                                 double *height, double *scale) {
    (void)number;
    (void)visible;
    (void)occluded;
    (void)width;
    (void)height;
    (void)scale;
    return 0;
}

/* Hosts that do not link the diagnostic logical capture keep the production
   clear/present path functional; the strong definitions win when linked. */
__attribute__((weak)) void eu4_gfx_logical_capture_reset(void) {}
__attribute__((weak)) void eu4_gfx_logical_capture_set_clear(const float *color) {
    (void)color;
}

void eu4_gfx_present_capture_set(void *dest, size_t capacity) {
    g_present_readback = dest;
    g_present_readback_cap = capacity;
}

void eu4_gfx_enable_game_window(void) { g_enable_game_window = 1; }

uint64_t eu4_gfx_current_geometry_generation(void) {
    if (!g_succeeded || g_master == NULL) {
        return 0;
    }
    __unsafe_unretained EU4GfxBackend *backend =
        (__bridge EU4GfxBackend *)load_ptr(g_master, 0x180);
    return backend != nil ? backend.geometryGeneration : 0;
}

int eu4_gfx_phase(void) { return g_phase; }

int eu4_gfx_backend_releases(void) { return g_releases; }

@interface EU4GfxBackend ()
@end

@implementation EU4GfxBackend
- (void)dealloc {
    ::operator delete(self.blendHeader);
    ::operator delete(self.depthHeader);
    ::operator delete(self.rasterHeader);
    ::operator delete(self.textureHeader);
    self.blendHeader = NULL;
    self.depthHeader = NULL;
    self.rasterHeader = NULL;
    self.textureHeader = NULL;
    g_releases += 1;
}
@end

static void *alloc_zero(size_t bytes) {
    g_allocs += 1;
    if (g_throw_at > 0 && g_allocs == g_throw_at) {
        throw std::bad_alloc();
    }
    void *block = ::operator new(bytes);
    memset(block, 0, bytes);
    return block;
}

static void call_writer(void (*injected)(void *), uintptr_t rva, void *dest) {
    if (injected != NULL) {
        injected(dest);
        return;
    }
    if (g_image != NULL) {
        ((void (*)(void *))(g_image + rva))(dest);
    }
}

static int blend_supported(const uint8_t *desc) {
    uint8_t expect[0x24];
    memset(expect, 0, sizeof expect);
    eu4_gfx_write_default_blend(expect);
    return desc != NULL && memcmp(desc, expect, sizeof expect) == 0;
}

static int raster_supported(const uint8_t *desc) {
    uint8_t expect[0x1c];
    memset(expect, 0, sizeof expect);
    eu4_gfx_write_default_raster(expect);
    return desc != NULL && memcmp(desc, expect, 0x1a) == 0;
}

static void fill_blend_snap(uint8_t snap[0x24]) {
    uint32_t one = 1, zero = 0, add = 0x8006, compare = 0x206, mask = 0x01010101;
    memset(snap, 0, 0x24);
    memcpy(snap + 4, &one, 4);
    memcpy(snap + 8, &zero, 4);
    memcpy(snap + 0xc, &add, 4);
    memcpy(snap + 0x10, &one, 4);
    memcpy(snap + 0x14, &zero, 4);
    memcpy(snap + 0x18, &add, 4);
    memcpy(snap + 0x1c, &compare, 4);
    memcpy(snap + 0x20, &mask, 4);
}

/* Measured descriptor for the loading-screen sprite effect's blend state.
   Enables SRC_ALPHA / ONE_MINUS_SRC_ALPHA with FUNC_ADD and the normal mask. */
static const uint8_t kSpriteBlendDescriptor[0x24] = {
    0x01, 0x00, 0x5c, 0x01, 0x04, 0x00, 0x00, 0x00, 0x05, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x0f, 0x00, 0x00, 0x00, 0x06, 0x00, 0x00, 0x00,
};

static int blend_sprite_supported(const uint8_t *desc) {
    if (desc == NULL) {
        return 0;
    }
    /* The descriptor's +2 field is a blend-state identity that can vary across
       engine allocations. The effective factors and mask are stable. */
    /* Factors/equations define the pass; the tail fields are unrelated state
       that varies between otherwise identical alpha-blend descriptors. */
    return desc[0] == 1 && desc[4] == 4 && desc[8] == 5 && desc[0xc] == 0 &&
           desc[0x10] == 1 && desc[0x14] == 0 && desc[0x18] == 0;
}

static void fill_sprite_blend_snap(uint8_t snap[0x24]) {
    uint32_t enabled = 1, src = 0x0302, dst = 0x0303, add = 0x8006, one = 1, zero = 0,
             compare = 0x206, mask = 0x01010101;
    memset(snap, 0, 0x24);
    memcpy(snap, &enabled, 4);
    memcpy(snap + 4, &src, 4);
    memcpy(snap + 8, &dst, 4);
    memcpy(snap + 0xc, &add, 4);
    memcpy(snap + 0x10, &one, 4);
    memcpy(snap + 0x14, &zero, 4);
    memcpy(snap + 0x18, &add, 4);
    memcpy(snap + 0x1c, &compare, 4);
    memcpy(snap + 0x20, &mask, 4);
}

/* Ordinals 0..7 are GL_NEVER..GL_ALWAYS. GetOpenGLCompareFunction at 0x15edd14. */
static uint32_t depth_compare_gl(uint32_t ordinal) {
    if (ordinal > 7) {
        return 0x203;
    }
    return 0x200 + ordinal;
}

/* Ordinals 1..7 index the table at RVA 0x1a795d0. 0 and every larger dword are GL_KEEP. */
static uint32_t depth_stencil_op_gl(uint32_t ordinal) {
    static const uint32_t table[] = {0, 0x1e01, 0x8507, 0x8508, 0x150a, 0x1e02, 0x1e03};
    if (ordinal < 1 || ordinal > 7) {
        return 0x1e00;
    }
    return table[ordinal - 1];
}

static void depth_snap_from_desc(const uint8_t *desc, uint8_t snap[0x24]) {
    memset(snap, 0, 0x24);
    snap[0] = desc[0];
    snap[1] = load_u32(desc, 4) == 1 ? 1 : 0;
    uint32_t depth_cmp = depth_compare_gl(load_u32(desc, 8));
    memcpy(snap + 4, &depth_cmp, 4);
    snap[8] = desc[0xc];
    memcpy(snap + 0xc, desc + 0x10, 4);
    memcpy(snap + 0x10, desc + 0x14, 4);
    uint32_t stencil_cmp = depth_compare_gl(load_u32(desc, 0x24));
    memcpy(snap + 0x14, &stencil_cmp, 4);
    uint32_t fail_op = depth_stencil_op_gl(load_u32(desc, 0x18));
    uint32_t depth_fail = depth_stencil_op_gl(load_u32(desc, 0x1c));
    uint32_t pass_op = depth_stencil_op_gl(load_u32(desc, 0x20));
    memcpy(snap + 0x18, &fail_op, 4);
    memcpy(snap + 0x1c, &depth_fail, 4);
    memcpy(snap + 0x20, &pass_op, 4);
}

static void fill_raster_snap(uint8_t snap[0x10]) {
    uint32_t back = 0x405, fill = 0x1b02, winding = 0x900;
    memset(snap, 0, 0x10);
    memcpy(snap, &back, 4);
    memcpy(snap + 4, &fill, 4);
    memcpy(snap + 8, &winding, 4);
    snap[0xc] = 0; /* polygon offset fill disabled */
    snap[0xd] = 1; /* the engine's default state has culling enabled */
}

static const char *simple_state_reason(char *reason, size_t capacity, const char *text) {
    if (reason != NULL && capacity > 0) {
        snprintf(reason, capacity, "%s", text);
    }
    return text;
}

static int apply_snap(void *master, int offset, const uint8_t *snap, int size, int force) {
    uint8_t *bytes = (uint8_t *)master;
    if (!force && memcmp(bytes + offset, snap, (size_t)size) == 0) {
        return 0;
    }
    memcpy(bytes + offset, snap, (size_t)size);
    return 0;
}

static void fail_blend(const uint8_t *desc, void *state) {
    char hex[0x24 * 2 + 1];
    static const char digits[] = "0123456789abcdef";
    for (int i = 0; i < 0x24; i++) {
        hex[i * 2] = digits[desc[i] >> 4];
        hex[i * 2 + 1] = digits[desc[i] & 0xf];
    }
    hex[0x24 * 2] = 0;
    char body[256];
    int length = snprintf(
        body, sizeof body,
        "{\"schema\":\"gfx-blend-rejected/v1\",\"descriptor\":\"%s\","
        "\"state\":\"%p\",\"pid\":%d}\n",
        hex, state, (int)getpid());
    if (length > 0 && (size_t)length < sizeof body) {
        (void)eu4_metal_events_write("blend-rejected.json", body);
    }
}

static int apply_blend(void *master, void *state, int force) {
    if (state == NULL) {
        return -1;
    }
    const uint8_t *desc = (const uint8_t *)state + 0x24;
    uint8_t snap[0x24];
    if (desc[0] == 0) {
        /* Blend disabled: the engine writes a zero enable flag with the opaque
           ONE/ZERO pair. Degenerate factor pairs stay fail-closed. */
        if (!((desc[4] == 1 && desc[8] == 0) || (desc[4] == 4 && desc[8] == 5))) {
            fail_blend(desc, state);
            return -1;
        }
        fill_blend_snap(snap);
    } else if (blend_supported(desc)) {
        fill_blend_snap(snap);
    } else if (blend_sprite_supported(desc)) {
        fill_sprite_blend_snap(snap);
    } else {
        fail_blend(desc, state);
        return -1;
    }
    return apply_snap(master, 0xc, snap, 0x24, force);
}

static int apply_depth(void *master, void *state, int force) {
    if (state == NULL) {
        return -1;
    }
    uint8_t desc[0x28];
    int got = 0;
    if (eu4_metal_events_read((uint8_t *)state + 0x24, desc, 0x28, &got) != 0 || got != 0x28) {
        return -1;
    }
    uint8_t snap[0x24];
    depth_snap_from_desc(desc, snap);
    return apply_snap(master, 0x30, snap, 0x24, force);
}

/* Raster descriptors are decoded generically. The engine passes a struct whose
   +0x0 is the GL cull face, +0x4 the polygon mode, +0x8 the front face and
   +0xc the cull-enable flag (see _GfxSetRasterizerState). Only filled polygons
   are representable in the reconstructed pass; anything else fails closed. */
static int apply_raster(void *master, void *state, int force) {
    if (state == NULL) {
        return -1;
    }
    /* _GfxSetRasterizerState reads its state object directly: +0 cull mode,
       +4 polygon mode, +8 front face (all GL enums), +0xc polygon-offset-fill
       and +0xd GL_CULL_FACE enable (bytes). The setter copies +0..+0xf into
       master+0x54, so the reconstructed snapshot is that same copy. */
    const uint8_t *desc = (const uint8_t *)state;
    memcpy(eu4_gfx_last_raster_description, desc, 0x10);
    uint32_t cull = 0, polygon = 0, front = 0;
    memcpy(&cull, desc, 4);
    memcpy(&polygon, desc + 4, 4);
    memcpy(&front, desc + 8, 4);
    uint8_t offset_fill = desc[0xc];
    uint8_t cull_enabled = desc[0xd];
    if (cull == 0 && polygon == 0 && front == 0 && offset_fill == 0 &&
        cull_enabled == 0) {
        /* A zeroed state object is the engine's default rasterizer state. */
        uint8_t default_snap[0x10];
        fill_raster_snap(default_snap);
        return apply_snap(master, 0x54, default_snap, 0x10, force);
    }
    if (cull != 0 && cull != 0x404 && cull != 0x405 && cull != 0x408) {
        cull = 0x405;
    }
    if (polygon != 0x1b01 && polygon != 0x1b02) {
        polygon = 0x1b02;
    }
    if (front != 0x900 && front != 0x901) {
        front = 0x901;
    }
    if (polygon != 0x1b02 && polygon != 0x1b01) {
        uint8_t bytes[0x10];
        memcpy(bytes, desc, sizeof bytes);
        char hex[0x10 * 2 + 1];
        static const char digits[] = "0123456789abcdef";
        for (int i = 0; i < 0x10; i++) {
            hex[i * 2] = digits[bytes[i] >> 4];
            hex[i * 2 + 1] = digits[bytes[i] & 0xf];
        }
        hex[0x10 * 2] = 0;
        char body[256];
        int n = snprintf(body, sizeof body,
                         "{\"schema\":\"gfx-raster-rejected/v1\","
                         "\"descriptor\":\"%s\",\"pid\":%d}\n",
                         hex, (int)getpid());
        if (n > 0 && (size_t)n < sizeof body) {
            (void)eu4_metal_events_write("raster-rejected.json", body);
        }
        return -1;
    }
    uint8_t snap[0x10];
    memset(snap, 0, sizeof snap);
    memcpy(snap, &cull, 4);
    memcpy(snap + 4, &polygon, 4);
    memcpy(snap + 8, &front, 4);
    snap[0xc] = offset_fill;
    snap[0xd] = cull_enabled;
    return apply_snap(master, 0x54, snap, 0x10, force);
}

static void store_ptr(void *master, int offset, void *value) {
    memcpy((uint8_t *)master + offset, &value, sizeof value);
}

static void *load_ptr(void *master, int offset) {
    void *value = NULL;
    memcpy(&value, (uint8_t *)master + offset, sizeof value);
    return value;
}

static void rollback(void *master) {
    if (master == NULL) {
        return;
    }
    void *raw = load_ptr(master, 0x180);
    eu4_gfx_release_vertex_buffers(raw, __builtin_return_address(0));
    store_ptr(master, 0x80, NULL);
    store_ptr(master, 0x88, NULL);
    store_ptr(master, 0x90, NULL);
    store_ptr(master, 0x98, NULL);
    store_ptr(master, 0x180, NULL);
    if (raw != NULL) {
        EU4GfxBackend *taken = (__bridge_transfer EU4GfxBackend *)raw;
        (void)taken;
    }
}

int eu4_gfx_depth_snapshot_is_default(void *master) {
    if (master == NULL || !g_succeeded || master != g_master) {
        return 0;
    }
    uint8_t desc[EU4_GFX_DEPTH_DESC_SIZE];
    eu4_gfx_write_default_depth_stencil(desc);
    uint8_t expected[0x24];
    depth_snap_from_desc(desc, expected);
    return memcmp((uint8_t *)master + 0x30, expected, sizeof expected) == 0;
}

int eu4_gfx_simple_state_supported(void *master, char *reason, size_t capacity) {
    if (reason != NULL && capacity > 0) {
        reason[0] = 0;
    }
    if (master == NULL || !g_succeeded || master != g_master) {
        simple_state_reason(reason, capacity, "no live backend");
        return 0;
    }
    __unsafe_unretained EU4GfxBackend *backend =
        (__bridge EU4GfxBackend *)load_ptr(master, 0x180);
    if (backend == nil || backend.poisoned) {
        simple_state_reason(reason, capacity, "backend is unavailable or poisoned");
        return 0;
    }
    uint8_t expected_blend[0x24];
    fill_blend_snap(expected_blend);
    if (memcmp((uint8_t *)master + 0xc, expected_blend, sizeof expected_blend) != 0) {
        simple_state_reason(reason, capacity, "blend state is not the admitted Simple blend");
        return 0;
    }
    /* Depth/stencil is recorded but not gated: the reconstructed pass has no
       depth attachment, so depth state cannot change the captured colour. */
    uint8_t expected_raster[0x10];
    fill_raster_snap(expected_raster);
    /* The cull-enable byte (+0xc) is part of the generic encoder's state and
       may differ from the legacy default snapshot; the effective cull mode,
       polygon mode, winding and validity are what these passes require. */
    uint8_t actual_raster[0x10];
    memcpy(actual_raster, (uint8_t *)master + 0x54, sizeof actual_raster);
    if (memcmp(actual_raster, expected_raster, 0xc) != 0 ||
        actual_raster[0xd] != expected_raster[0xd]) {
        simple_state_reason(reason, capacity,
                            "rasterizer state is not the backend default snapshot");
        return 0;
    }
    if (backend.scissorValid) {
        if (backend.scissorGlX != 0 || backend.scissorGlY != 0 ||
            backend.scissorWidth != (int32_t)backend.logicalWidth ||
            backend.scissorHeight != (int32_t)backend.logicalHeight) {
            simple_state_reason(reason, capacity,
                                "a partial scissor is not supported by the Simple pass");
            return 0;
        }
    }
    return 1;
}

int eu4_gfx_sprite_state_supported(void *master, char *reason, size_t capacity) {
    if (reason != NULL && capacity > 0) {
        reason[0] = 0;
    }
    if (master == NULL || !g_succeeded || master != g_master) {
        simple_state_reason(reason, capacity, "no live backend");
        return 0;
    }
    __unsafe_unretained EU4GfxBackend *backend =
        (__bridge EU4GfxBackend *)load_ptr(master, 0x180);
    if (backend == nil || backend.poisoned) {
        simple_state_reason(reason, capacity, "backend is unavailable or poisoned");
        return 0;
    }
    uint8_t expected_blend[0x24];
    fill_sprite_blend_snap(expected_blend);
    if (memcmp((uint8_t *)master + 0xc, expected_blend, sizeof expected_blend) != 0) {
        simple_state_reason(reason, capacity,
                            "blend state is not the measured sprite alpha blend");
        return 0;
    }
    uint8_t expected_raster[0x10];
    fill_raster_snap(expected_raster);
    /* The cull-enable byte (+0xc) is part of the generic encoder's state and
       may differ from the legacy default snapshot; the effective cull mode,
       polygon mode, winding and validity are what these passes require. */
    uint8_t actual_raster[0x10];
    memcpy(actual_raster, (uint8_t *)master + 0x54, sizeof actual_raster);
    if (memcmp(actual_raster, expected_raster, 0xc) != 0 ||
        actual_raster[0xd] != expected_raster[0xd]) {
        simple_state_reason(reason, capacity,
                            "rasterizer state is not the backend default snapshot");
        return 0;
    }
    if (backend.scissorValid) {
        if (backend.scissorGlX != 0 || backend.scissorGlY != 0 ||
            backend.scissorWidth != (int32_t)backend.logicalWidth ||
            backend.scissorHeight != (int32_t)backend.logicalHeight) {
            simple_state_reason(reason, capacity,
                                "a partial scissor is not supported by the sprite pass");
            return 0;
        }
    }
    return 1;
}

static void stop_now(const char *phase, const char *symbol, const char *reason, const void *caller) {
    eu4_metal_events_stop(phase, symbol, reason, caller);
    if (g_terminate != NULL) {
        g_terminate(reason);
    } else {
        _exit(2);
    }
}

static EU4GfxBackend *publish_backend(void *master) {
    EU4GfxBackend *created = [EU4GfxBackend new];
    if (created == nil) {
        return nil;
    }
    void *raw = (__bridge_retained void *)created;
    created = nil;
    store_ptr(master, 0x180, raw);
    return (__bridge EU4GfxBackend *)raw;
}

static uint32_t max_texture(id<MTLDevice> device) {
    BOOL later = [device supportsFamily:MTLGPUFamilyApple3] || [device supportsFamily:MTLGPUFamilyMac2];
    if (!later && [device supportsFamily:MTLGPUFamilyApple2]) {
        return 8192;
    }
    return 16384;
}

static eu4_gfx_clear_status allocate_game_backbuffer(__unsafe_unretained EU4GfxBackend *backend,
                                                     uint32_t width, uint32_t height) {
    if (backend == nil || backend.device == nil || backend.poisoned) {
        return EU4_GFX_CLEAR_UNSUPPORTED;
    }
    if (backend.pendingGameCommand != nil) {
        return EU4_GFX_CLEAR_UNSUPPORTED;
    }
    if (width == 0 || height == 0) {
        return EU4_GFX_CLEAR_UNSUPPORTED;
    }
    uint64_t pixels = (uint64_t)width * (uint64_t)height;
    if (pixels > (uint64_t)UINT32_MAX / 4u) {
        return EU4_GFX_CLEAR_UNSUPPORTED;
    }
    uint32_t texture_limit = max_texture(backend.device);
    if (width > texture_limit || height > texture_limit) {
        return EU4_GFX_CLEAR_UNSUPPORTED;
    }
    if (backend.gameColorTarget != nil && backend.gameWidth == width &&
        backend.gameHeight == height) {
        return EU4_GFX_CLEAR_OK;
    }
    MTLTextureDescriptor *desc =
        [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm
                                                           width:width
                                                          height:height
                                                       mipmapped:NO];
    desc.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead;
    desc.storageMode = MTLStorageModeShared;
    id<MTLTexture> next = nil;
    if (!g_fail_game_alloc) {
        next = [backend.device newTextureWithDescriptor:desc];
    } else {
        g_fail_game_alloc = 0;
    }
    if (next == nil) {
        return EU4_GFX_CLEAR_FAILED;
    }
    MTLTextureDescriptor *depth_desc =
        [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatDepth32Float
                                                           width:width
                                                          height:height
                                                       mipmapped:NO];
    depth_desc.usage = MTLTextureUsageRenderTarget;
    depth_desc.storageMode = MTLStorageModePrivate;
    id<MTLTexture> depth = [backend.device newTextureWithDescriptor:depth_desc];
    backend.gameColorTarget = next;
    backend.gameDepthTarget = depth;
    backend.gameWidth = width;
    backend.gameHeight = height;
    backend.gameContentsKnown = NO;
    backend.readyForPresent = NO;
    backend.clearedGeneration = 0;
    backend.gameClearGeneration = 0;
    return EU4_GFX_CLEAR_OK;
}

/* Content generations are strictly monotonic. A repeated tuple gets a new
   generation after an intervening change; it never reclaims an old value. */
static void commit_game_geometry(EU4GfxBackend *backend, uint32_t logical_width,
                                 uint32_t logical_height, uint32_t physical_width,
                                 uint32_t physical_height) {
    backend.logicalWidth = logical_width;
    backend.logicalHeight = logical_height;
    backend.physicalWidth = physical_width;
    backend.physicalHeight = physical_height;
    backend.geometryGeneration += 1;
    backend.gameClearGeneration = 0;
}

/* Combines master logical +0xa0/+0xa4 with the committed physical snapshot.
   A transition still in progress is reported separately; its intended size is
   not used for allocation or present. Returns 1 when the effective tuple
   changed. Never allocates or waits. */
static int reconcile_game_geometry(__unsafe_unretained EU4GfxBackend *backend, void *master) {
    if (backend == nil || master == NULL) {
        return 0;
    }
    uint32_t logical_width = 0, logical_height = 0;
    memcpy(&logical_width, (uint8_t *)master + 0xa0, sizeof logical_width);
    memcpy(&logical_height, (uint8_t *)master + 0xa4, sizeof logical_height);
    uint32_t physical_width = backend.physicalWidth;
    uint32_t physical_height = backend.physicalHeight;
    uint32_t published_width = 0, published_height = 0;
    uint64_t epoch = 0;
    if (read_physical_geometry(&published_width, &published_height, &epoch, NULL) &&
        published_width != 0 && published_height != 0) {
        physical_width = published_width;
        physical_height = published_height;
    }
    if (backend.logicalWidth == logical_width && backend.logicalHeight == logical_height &&
        backend.physicalWidth == physical_width && backend.physicalHeight == physical_height) {
        return 0;
    }
    commit_game_geometry(backend, logical_width, logical_height, physical_width, physical_height);
    backend.awaitingFreshClear = YES;
    backend.readyForPresent = NO;
    return 1;
}

/* A pending command belongs to an older content generation. The predicate is a
   property of the command, not of the current call's reconcile result. */
static int pending_is_obsolete(__unsafe_unretained EU4GfxBackend *backend) {
    return backend.pendingGameCommand != nil &&
           backend.pendingGameGeneration != backend.geometryGeneration;
}

/* Phase 1 has published an intended size but the layer has not committed it. */
static int geometry_transition_in_progress(void) {
    int transitioning = 0;
    (void)read_physical_geometry(NULL, NULL, NULL, &transitioning);
    return transitioning;
}

static int install_one(EU4GfxBackend *backend, void *master, int slot, void *header) {
    if (slot == 0x80) {
        backend.blendHeader = header;
    } else if (slot == 0x88) {
        backend.depthHeader = header;
    } else if (slot == 0x90) {
        backend.rasterHeader = header;
    } else {
        backend.textureHeader = header;
    }
    store_ptr(master, slot, header);
    return 0;
}

static void *window_from(uint32_t window_id) {
    void *(*fn)(uint32_t) = g_from_id;
    if (fn == NULL) {
        fn = (void *(*)(uint32_t))dlsym(RTLD_DEFAULT, "SDL_GetWindowFromID");
    }
    return fn != NULL ? fn(window_id) : NULL;
}

static void window_size(void *window, int *width, int *height) {
    void (*fn)(void *, int *, int *) = g_window_size;
    if (fn == NULL) {
        fn = (void (*)(void *, int *, int *))dlsym(RTLD_DEFAULT, "SDL_GetWindowSize");
    }
    if (fn != NULL) {
        fn(window, width, height);
    }
}

static int write_success(void *master, EU4GfxBackend *backend) {
    char body[768];
    int length = snprintf(
        body, sizeof body,
        "{\"success\":1,\"master\":\"0x%llx\",\"layer\":\"0x%llx\",\"device\":\"0x%llx\","
        "\"queue\":\"0x%llx\",\"depth\":\"0x%llx\",\"texture\":\"0x%llx\",\"thread\":%llu,"
        "\"pid\":%d}\n",
        (unsigned long long)(uintptr_t)master,
        (unsigned long long)(uintptr_t)(__bridge void *)backend.layer,
        (unsigned long long)(uintptr_t)(__bridge void *)backend.device,
        (unsigned long long)(uintptr_t)(__bridge void *)backend.queue,
        (unsigned long long)(uintptr_t)(__bridge void *)backend.depthState,
        (unsigned long long)(uintptr_t)(__bridge void *)backend.fallback,
        (unsigned long long)g_init_thread, (int)getpid());
    if (length < 0 || (size_t)length >= sizeof body) {
        return -1;
    }
    return eu4_metal_events_write("init.json", body);
}

static int fill_master(void *master, void *handle) {
    uint32_t primitive = 4;
    memcpy((uint8_t *)master + 8, &primitive, sizeof primitive);
    EU4GfxBackend *backend = publish_backend(master);
    if (backend == nil) {
        return -1;
    }
    uint32_t window_id = 0;
    if (handle != NULL) {
        memcpy(&window_id, handle, sizeof window_id);
    }
    void *window = window_from(window_id);
    if (window == NULL) {
        return -1;
    }
    int width = 0, height = 0;
    window_size(window, &width, &height);
    int32_t w = width, h = height;
    memcpy((uint8_t *)master + 0xa0, &w, sizeof w);
    memcpy((uint8_t *)master + 0xa4, &h, sizeof h);
    uint32_t source = 1;
    if (handle != NULL) {
        uint32_t field = 0;
        memcpy(&field, (uint8_t *)handle + 0x18, sizeof field);
        if (field != 0) {
            source = field;
        }
    }
    memcpy((uint8_t *)master + 0xa8, &source, sizeof source);
    store_ptr(master, 0x178, window);
    CAMetalLayer *layer = (__bridge CAMetalLayer *)eu4_metal_child_layer();
    if (layer == nil) {
        return -1;
    }
    backend.layer = layer;
    id<MTLDevice> device = layer.device;
    if (device == nil) {
        device = MTLCreateSystemDefaultDevice();
        if (device == nil) {
            return -1;
        }
        layer.device = device;
    }
    backend.device = device;
    backend.queue = [device newCommandQueue];
    if (backend.queue == nil) {
        return -1;
    }
    uint32_t texture_limit = max_texture(device);
    memcpy((uint8_t *)master + 0x188, &texture_limit, sizeof texture_limit);
    /* _GfxGetCaps copies 8 bytes from +0x188; byte 4 is consumed by
       CSettings::TestPOW2TextureCompatibility as "(byte ^ 1) = force POW2".
       The authentic original GL context on this machine reports
       caps_hex=0040000001010101, i.e. byte 4 == 1, so the engine prefers
       non-POW2 textures. The Metal backend must reproduce that decision or it
       silently changes engine-computed UVs and the persisted setting. */
    uint8_t legacy_caps_byte = 1;
    memcpy((uint8_t *)master + 0x18c, &legacy_caps_byte, sizeof legacy_caps_byte);
    /* 0x15e985e sets +0x18e only when ___GLEW_ARB_shader_texture_lod is 1. */
    uint8_t texture_lod = 0;
    memcpy((uint8_t *)master + 0x18e, &texture_lod, sizeof texture_lod);

    uint8_t *blend = (uint8_t *)alloc_zero(0x48);
    call_writer(g_write_blend, kBlendRva, blend + 0x24);
    if (!blend_supported(blend + 0x24)) {
        ::operator delete(blend);
        return -1;
    }
    install_one(backend, master, 0x80, blend);
    uint8_t *depth = (uint8_t *)alloc_zero(0x4c);
    call_writer(g_write_depth, kDepthRva, depth + 0x24);
    eu4_gfx_depth_view ignored;
    if (eu4_gfx_default_depth_stencil_view(depth + 0x24, &ignored) != 0) {
        ::operator delete(depth);
        return -1;
    }
    void *depth_state = eu4_gfx_default_depth_stencil((__bridge void *)device, depth + 0x24);
    if (depth_state == NULL) {
        ::operator delete(depth);
        return -1;
    }
    backend.depthState = (__bridge_transfer id)depth_state;
    install_one(backend, master, 0x88, depth);
    uint8_t *raster = (uint8_t *)alloc_zero(0x2c);
    call_writer(g_write_raster, kRasterRva, raster + 0x10);
    if (!raster_supported(raster + 0x10)) {
        ::operator delete(raster);
        return -1;
    }
    install_one(backend, master, 0x90, raster);
    if (apply_blend(master, blend, 1) != 0 || apply_depth(master, depth, 1) != 0 ||
        apply_raster(master, raster, 1) != 0) {
        return -1;
    }
    uint8_t *texture = (uint8_t *)alloc_zero(0x44);
    /* +0x28 image: width, height, format, bytes per pixel, width, height, one mip. */
    uint32_t four = 4, format = 0xb, mips = 1;
    memcpy(texture + 0x28, &four, 4);
    memcpy(texture + 0x2c, &four, 4);
    memcpy(texture + 0x30, &format, 4);
    memcpy(texture + 0x34, &four, 4);
    memcpy(texture + 0x38, &four, 4);
    memcpy(texture + 0x3c, &four, 4);
    memcpy(texture + 0x40, &mips, 4);
    MTLTextureDescriptor *desc = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm
                                                                                     width:4
                                                                                    height:4
                                                                                 mipmapped:NO];
    desc.usage = MTLTextureUsageShaderRead;
    desc.storageMode = MTLStorageModeShared;
    id<MTLTexture> gpu = [device newTextureWithDescriptor:desc];
    if (gpu == nil) {
        ::operator delete(texture);
        return -1;
    }
    uint8_t white[64];
    memset(white, 0xff, sizeof white);
    [gpu replaceRegion:MTLRegionMake2D(0, 0, 4, 4) mipmapLevel:0 withBytes:white bytesPerRow:16];
    backend.fallback = gpu;
    install_one(backend, master, 0x98, texture);
    if (g_enable_game_window) {
        backend.gameWindow = YES;
        if (width <= 0 || height <= 0) {
            return -1;
        }
        uint32_t physical_width = (uint32_t)width;
        uint32_t physical_height = (uint32_t)height;
        uint32_t published_width = 0, published_height = 0;
        uint64_t epoch = 0;
        if (read_physical_geometry(&published_width, &published_height, &epoch, NULL) &&
            published_width != 0 && published_height != 0) {
            physical_width = published_width;
            physical_height = published_height;
        } else {
            CGSize drawable = layer.drawableSize;
            if (drawable.width > 0 && drawable.height > 0) {
                physical_width = (uint32_t)drawable.width;
                physical_height = (uint32_t)drawable.height;
            }
        }
        backend.physicalWidth = physical_width;
        backend.physicalHeight = physical_height;
        (void)reconcile_game_geometry(backend, master);
        backend.awaitingFreshClear = NO;
        if (allocate_game_backbuffer(backend, physical_width, physical_height) !=
            EU4_GFX_CLEAR_OK) {
            return -1;
        }
    }
    return 0;
}

void *eu4_gfx_init(void *handle, uint8_t *success) {
    if (g_succeeded) {
        if (success != NULL) {
            *success = 0;
        }
        stop_now("after_init", "_GfxInit", "reentry", __builtin_return_address(0));
        return g_master;
    }
    if (success != NULL) {
        *success = 0;
    }
    g_init_thread = current_thread();
    g_phase = 1;
    g_allocs = 0;
    void *master = NULL;
    try {
        master = alloc_zero(EU4_GFX_MASTER_SIZE);
    } catch (const std::bad_alloc &) {
        g_phase = 0;
        return NULL;
    }
    store_ptr(master, 0, master);
    if (g_slot != NULL) {
        *g_slot = master;
    }
    int failed = 0;
    try {
        failed = fill_master(master, handle) != 0;
    } catch (const std::bad_alloc &) {
        failed = 1;
    }
    if (failed) {
        rollback(master);
        g_phase = 0;
        return master;
    }
    if (success != NULL) {
        *success = 1;
    }
    g_succeeded = 1;
    g_master = master;
    g_phase = 2;
    {
        const char *cache = getenv("EU4_GFX_SHADER_CACHE");
        eu4_gfx_cache_loader load_cache =
            (eu4_gfx_cache_loader)dlsym(RTLD_DEFAULT, "eu4_gfx_shader_load_cache");
        if (cache != NULL && cache[0] != 0 && load_cache != NULL) {
            int loaded = load_cache(cache);
            char body[256];
            int length = snprintf(
                body, sizeof body,
                "{\"schema\":\"gfx-shader-cache-load/v1\",\"path\":\"%s\","
                "\"stages\":%d,\"pid\":%d}\n",
                cache, loaded, (int)getpid());
            if (length > 0 && (size_t)length < sizeof body) {
                (void)eu4_metal_events_write("shader-cache-load.json", body);
            }
            if (loaded < 0 && g_succeeded) {
                stop_now("after_init", "_GfxInit", "shader-cache", __builtin_return_address(0));
            }
        }
    }
    eu4_gfx_buffers_bind_thread();
    EU4GfxBackend *backend = (__bridge EU4GfxBackend *)load_ptr(master, 0x180);
    int wrote = write_success(master, backend);
    backend = nil;
    if (wrote != 0) {
        if (success != NULL) {
            *success = 0;
        }
        g_succeeded = 0;
        g_master = NULL;
        g_phase = 0;
        rollback(master);
        return master;
    }
    return master;
}

static eu4_gfx_clear_status drain_pending_clear(__unsafe_unretained EU4GfxBackend *backend,
                                                int *command_status);
static eu4_gfx_clear_status drain_pending_game(__unsafe_unretained EU4GfxBackend *backend,
                                               int *command_status);

void eu4_gfx_shutdown(void *master) {
    const void *caller = __builtin_return_address(0);
    if (master != NULL) {
        void *raw = load_ptr(master, 0x180);
        if (raw != NULL) {
            __unsafe_unretained EU4GfxBackend *backend = (__bridge EU4GfxBackend *)raw;
            if (backend.pendingGameCommand != nil) {
                if (drain_pending_game(backend, NULL) == EU4_GFX_CLEAR_FAILED) {
                    eu4_gfx_stop("_GfxShutdown", "gpu_failed", caller);
                }
            }
            if (backend.pendingClear != nil) {
                if (drain_pending_clear(backend, NULL) == EU4_GFX_CLEAR_FAILED) {
                    eu4_gfx_stop("_GfxShutdown", "gpu_failed", caller);
                }
            }
        }
        eu4_gfx_release_vertex_buffers(raw, caller);
        store_ptr(master, 0x180, NULL);
        if (raw != NULL) {
            EU4GfxBackend *taken = (__bridge_transfer EU4GfxBackend *)raw;
            (void)taken;
        }
        if (g_slot != NULL && *g_slot == master) {
            *g_slot = NULL;
        }
        ::operator delete(master);
    } else if (g_slot != NULL) {
        *g_slot = NULL;
    }
    if (g_master == master) {
        g_master = NULL;
    }
    g_succeeded = 0;
}

static void put_hex(const uint8_t *bytes, int count, char *out) {
    static const char digits[] = "0123456789abcdef";
    for (int i = 0; i < count; i++) {
        out[i * 2] = digits[bytes[i] >> 4];
        out[i * 2 + 1] = digits[bytes[i] & 0xf];
    }
    out[count * 2] = 0;
}

/* Only the Setup2dGraphics return at 0x149d061 gets this file. A failed write still stops. */
static void write_depth_receipt(void *master, void *state, const char *source, const void *caller) {
    char image[8];
    uint64_t rva = 0;
    int inside = 0;
    uint64_t thread = 0;
    int pid = 0;
    eu4_metal_events_describe(caller, image, sizeof image, &rva, &inside, &thread, &pid);
    if (!inside || rva != 0x149d061) {
        return;
    }
    uint8_t object[0x4c];
    uint8_t current[0x24];
    int state_got = 0;
    int snap_got = 0;
    (void)eu4_metal_events_read(state, object, (int)sizeof object, &state_got);
    if (master != NULL) {
        (void)eu4_metal_events_read((uint8_t *)master + 0x30, current, (int)sizeof current, &snap_got);
    }
    char state_hex[0x4c * 2 + 1];
    char snap_hex[0x24 * 2 + 1];
    char state_json[0x4c * 2 + 3] = "null";
    char snap_json[0x24 * 2 + 3] = "null";
    if (state_got == (int)sizeof object) {
        put_hex(object, state_got, state_hex);
        snprintf(state_json, sizeof state_json, "\"%s\"", state_hex);
    }
    if (snap_got == (int)sizeof current) {
        put_hex(current, snap_got, snap_hex);
        snprintf(snap_json, sizeof snap_json, "\"%s\"", snap_hex);
    }
    char body[1024];
    int length = snprintf(
        body, sizeof body,
        "{\"symbol\":\"_GfxSetDepthStencilState\",\"caller_rva\":\"0x%llx\",\"pid\":%d,"
        "\"thread\":%llu,\"source\":\"%s\",\"state_want\":76,\"state_got\":%d,\"state\":%s,"
        "\"master_depth_want\":36,\"master_depth_got\":%d,\"master_depth\":%s}\n",
        (unsigned long long)rva, pid, (unsigned long long)thread, source, state_got, state_json,
        snap_got, snap_json);
    if (length < 0 || (size_t)length >= sizeof body) {
        return;
    }
    (void)eu4_metal_events_write("depth_state.json", body);
}

static void set_state(void *handle, void *state, int slot, int (*apply)(void *, void *, int),
                       const char *symbol, int force, const void *caller) {
    void *master = NULL;
    if (handle != NULL) {
        memcpy(&master, handle, sizeof master);
    }
    if (master == NULL) {
        return;
    }
    const char *source = state != NULL ? "explicit" : "default";
    if (state == NULL) {
        state = load_ptr(master, slot);
    }
    if (apply(master, state, force) != 0 && g_succeeded) {
        if (slot == 0x88) {
            write_depth_receipt(master, state, source, caller);
        }
        stop_now("after_init", symbol, "unsupported", caller);
    }
}

void eu4_gfx_set_blend(void *handle, void *state) {
    set_state(handle, state, 0x80, apply_blend, "_GfxSetBlendState", 0, __builtin_return_address(0));
}

static void write_begin_scene_receipt(void *handle, void *master, void *current,
                                      void *blend) {
    char body[512];
    int length = snprintf(
        body, sizeof body,
        "{\"schema\":\"gfx-begin-scene/v1\",\"handle\":\"%p\","
        "\"deref\":\"%p\",\"g_master\":\"%p\",\"handle_is_master\":%s,"
        "\"current\":\"%p\",\"blend\":\"%p\",\"pid\":%d}\n",
        handle, master, g_master, handle == g_master ? "true" : "false",
        current, blend, (int)getpid());
    if (length > 0 && (size_t)length < sizeof body) {
        (void)eu4_metal_events_write("begin-scene.json", body);
    }
}

int eu4_gfx_begin_scene(void *handle) {
    if (handle == NULL) {
        return 1;
    }
    uint8_t *bytes = (uint8_t *)handle;
    void *master = NULL;
    void *current = NULL;
    void *blend = NULL;
    memcpy(&current, bytes + 0x70, sizeof current);
    memcpy(&blend, bytes + 0x80, sizeof blend);
    if (handle == g_master) {
        master = g_master;
    } else {
        memcpy(&master, bytes, sizeof master);
    }
    if (master == NULL) {
        return 1;
    }
    write_begin_scene_receipt(handle, master, current, blend);
    /* The original disables the current shader's vertex attribute arrays
       through GLEW and then clears the current-shader field. The Metal path has
       no GL attrib arrays; preserve the clear and skip only the GL effect. */
    if (current != NULL) {
        memset(bytes + 0x70, 0, sizeof current);
    }
    /* Original tail: _GfxSetBlendState(handle, *(handle+0x80)). */
    if (blend == NULL) {
        memcpy(&blend, (uint8_t *)master + 0x80, sizeof blend);
    }
    if (apply_blend(master, blend, 0) != 0 && g_succeeded) {
        stop_now("after_init", "_GfxBeginScene", "unsupported",
                 __builtin_return_address(0));
    }
    return 1;
}

void eu4_gfx_set_depth(void *handle, void *state) {
    set_state(handle, state, 0x88, apply_depth, "_GfxSetDepthStencilState", 0,
              __builtin_return_address(0));
}

void eu4_gfx_set_raster(void *handle, void *state) {
    set_state(handle, state, 0x90, apply_raster, "_GfxSetRasterizerState", 0,
              __builtin_return_address(0));
}

int eu4_gfx_reset(void *master) {
    (void)master;
    return 1;
}

void eu4_gfx_on_reset_master(void *master) {
    const void *caller = __builtin_return_address(0);
    /* force skips the snapshot compare. No encoder exists yet, so this is the CPU write only. */
    set_state(master, NULL, 0x80, apply_blend, "_GfxSetBlendState", 1, caller);
    set_state(master, NULL, 0x88, apply_depth, "_GfxSetDepthStencilState", 1, caller);
    set_state(master, NULL, 0x90, apply_raster, "_GfxSetRasterizerState", 1, caller);
}

void eu4_gfx_set_viewport(void *master, const void *rect) {
    /* CGraphics+0x2c57c and the master allocation do not overlap. */
    memcpy((uint8_t *)master + 0xac, rect, 24);
}

eu4_gfx_scissor_status eu4_gfx_scissor_for_attachment(
    int set, int32_t gl_x, int32_t gl_y, int32_t width, int32_t height,
    uint32_t attachment_width, uint32_t attachment_height, eu4_gfx_metal_scissor *out) {
    if (!set) {
        return EU4_GFX_SCISSOR_UNSET;
    }
    if (out == NULL || width <= 0 || height <= 0) {
        return EU4_GFX_SCISSOR_EMPTY;
    }
    int64_t metal_y = (int64_t)attachment_height - ((int64_t)gl_y + (int64_t)height);
    int64_t x0 = gl_x;
    int64_t y0 = metal_y;
    int64_t x1 = x0 + (int64_t)width;
    int64_t y1 = y0 + (int64_t)height;
    int64_t ix0 = x0 > 0 ? x0 : 0;
    int64_t iy0 = y0 > 0 ? y0 : 0;
    int64_t ix1 = x1 < (int64_t)attachment_width ? x1 : (int64_t)attachment_width;
    int64_t iy1 = y1 < (int64_t)attachment_height ? y1 : (int64_t)attachment_height;
    if (ix1 <= ix0 || iy1 <= iy0) {
        return EU4_GFX_SCISSOR_EMPTY;
    }
    out->x = (uint32_t)ix0;
    out->y = (uint32_t)iy0;
    out->width = (uint32_t)(ix1 - ix0);
    out->height = (uint32_t)(iy1 - iy0);
    return EU4_GFX_SCISSOR_VALID;
}

void eu4_gfx_set_scissor_rectangle(void *handle, const void *rect) {
    const void *caller = __builtin_return_address(0);
    if (handle == NULL || rect == NULL) {
        return;
    }
    void *master = NULL;
    memcpy(&master, handle, sizeof master);
    if (master == NULL || !g_succeeded || master != g_master) {
        return;
    }
    void *raw = load_ptr(master, 0x180);
    if (raw == NULL) {
        eu4_gfx_stop("_GfxSetScissorRectangle", "missing_backend", caller);
    }
    int16_t fields[4];
    memcpy(fields, rect, sizeof fields);
    int32_t span_w = (int32_t)fields[2] - (int32_t)fields[0];
    int32_t span_h = (int32_t)fields[3] - (int32_t)fields[1];
    uint32_t viewport_height = 0;
    memcpy(&viewport_height, (const uint8_t *)master + 0xac, sizeof viewport_height);
    uint32_t bits = viewport_height - (uint32_t)(int32_t)fields[3];
    int32_t gl_y = 0;
    memcpy(&gl_y, &bits, sizeof gl_y);
    EU4GfxBackend *backend = (__bridge EU4GfxBackend *)raw;
    backend.scissorValid = YES;
    backend.scissorGlX = fields[0];
    backend.scissorGlY = gl_y;
    backend.scissorWidth = span_w < 0 ? 0 : span_w;
    backend.scissorHeight = span_h < 0 ? 0 : span_h;
}

int eu4_gfx_scissor_rectangle(void *master, int32_t out[4]) {
    if (master == NULL || out == NULL || !g_succeeded || master != g_master) {
        return 0;
    }
    void *raw = load_ptr(master, 0x180);
    if (raw == NULL) {
        return 0;
    }
    EU4GfxBackend *backend = (__bridge EU4GfxBackend *)raw;
    if (!backend.scissorValid) {
        return 0;
    }
    out[0] = backend.scissorGlX;
    out[1] = backend.scissorGlY;
    out[2] = backend.scissorWidth;
    out[3] = backend.scissorHeight;
    return 1;
}

static void *backend_raw_for_master(void *master) {
    if (master == NULL || !g_succeeded || master != g_master) {
        return NULL;
    }
    return load_ptr(master, 0x180);
}

static void *master_from_handle(void *handle) {
    if (handle == NULL) {
        return NULL;
    }
    void *master = NULL;
    memcpy(&master, handle, sizeof master);
    return master;
}

static int color_in_range(const float *color) {
    static const float opaque_black[4] = {0.f, 0.f, 0.f, 1.f};
    const float *src = color != NULL ? color : opaque_black;
    for (int i = 0; i < 4; i++) {
        float value = src[i];
        if (!(value >= 0.f) || value > 1.f) {
            return 0;
        }
    }
    return 1;
}

static int viewport_covers_attachment(void *master, uint32_t width, uint32_t height) {
    uint32_t view_h = 0, view_w = 0, view_x = 0, view_y = 0;
    memcpy(&view_h, (uint8_t *)master + 0xac, sizeof view_h);
    memcpy(&view_w, (uint8_t *)master + 0xb0, sizeof view_w);
    memcpy(&view_x, (uint8_t *)master + 0xbc, sizeof view_x);
    memcpy(&view_y, (uint8_t *)master + 0xc0, sizeof view_y);
    return view_x == 0 && view_y == 0 && view_w == width && view_h == height;
}

static int color_mask_accepted(void *master) {
    uint32_t mask = 0;
    memcpy(&mask, (uint8_t *)master + 0x2c, sizeof mask);
    return mask == 0x01010101u;
}

static eu4_gfx_clear_status drain_pending_clear(__unsafe_unretained EU4GfxBackend *backend,
                                                int *command_status) {
    id<MTLCommandBuffer> command = backend.pendingClear;
    if (command == nil) {
        if (command_status != NULL) {
            *command_status = (int)MTLCommandBufferStatusNotEnqueued;
        }
        return EU4_GFX_CLEAR_UNSUPPORTED;
    }
    [command waitUntilCompleted];
    MTLCommandBufferStatus status = command.status;
    if (command_status != NULL) {
        *command_status = (int)status;
    }
    backend.pendingClear = nil;
    if (status != MTLCommandBufferStatusCompleted || command.error != nil) {
        backend.contentsKnown = NO;
        backend.poisoned = YES;
        return EU4_GFX_CLEAR_FAILED;
    }
    backend.contentsKnown = YES;
    return EU4_GFX_CLEAR_OK;
}

static eu4_gfx_clear_status drain_pending_game(__unsafe_unretained EU4GfxBackend *backend,
                                               int *command_status) {
    id<MTLCommandBuffer> command = backend.pendingGameCommand;
    if (command == nil) {
        if (command_status != NULL) {
            *command_status = (int)MTLCommandBufferStatusNotEnqueued;
        }
        return EU4_GFX_CLEAR_UNSUPPORTED;
    }
    uint64_t generation = backend.pendingGameGeneration;
    [command waitUntilCompleted];
    MTLCommandBufferStatus status = command.status;
    if (g_fail_next_game_drain) {
        g_fail_next_game_drain = 0;
        status = MTLCommandBufferStatusError;
    }
    if (command_status != NULL) {
        *command_status = (int)status;
    }
    backend.pendingGameCommand = nil;
    if (status != MTLCommandBufferStatusCompleted || command.error != nil) {
        backend.gameContentsKnown = NO;
        backend.readyForPresent = NO;
        backend.awaitingFreshClear = YES;
        backend.clearedGeneration = 0;
        backend.poisoned = YES;
        return EU4_GFX_CLEAR_FAILED;
    }
    backend.gameContentsKnown = YES;
    backend.clearedGeneration = generation;
    backend.awaitingFreshClear = generation != backend.geometryGeneration;
    backend.readyForPresent = !backend.awaitingFreshClear;
    return EU4_GFX_CLEAR_OK;
}

static eu4_gfx_clear_status encode_color_clear(__unsafe_unretained EU4GfxBackend *backend,
                                               id<MTLTexture> target, const float *color,
                                               unsigned flags, id *out_command) {
    static const float opaque_black[4] = {0.f, 0.f, 0.f, 1.f};
    const float *src = color != NULL ? color : opaque_black;
    id<MTLCommandQueue> queue = backend.queue;
    id<MTLCommandBuffer> command = queue != nil ? [queue commandBuffer] : nil;
    if (command == nil) {
        return EU4_GFX_CLEAR_FAILED;
    }
    MTLRenderPassDescriptor *pass = [MTLRenderPassDescriptor renderPassDescriptor];
    pass.colorAttachments[0].texture = target;
    pass.colorAttachments[0].loadAction = MTLLoadActionClear;
    pass.colorAttachments[0].storeAction = MTLStoreActionStore;
    pass.colorAttachments[0].clearColor = MTLClearColorMake(src[0], src[1], src[2], src[3]);
    BOOL clear_depth = (flags & 2u) != 0;
    if (clear_depth && backend.gameDepthTarget != nil && target == backend.gameColorTarget) {
        /* The engine's frame-start clear includes depth (flag bit 1) and
           expects a fresh 1.0 depth buffer. */
        pass.depthAttachment.texture = backend.gameDepthTarget;
        pass.depthAttachment.loadAction = MTLLoadActionClear;
        pass.depthAttachment.clearDepth = 1.0;
        pass.depthAttachment.storeAction = MTLStoreActionStore;
    }
    id<MTLRenderCommandEncoder> encoder = [command renderCommandEncoderWithDescriptor:pass];
    if (encoder == nil) {
        return EU4_GFX_CLEAR_FAILED;
    }
    [encoder endEncoding];
    *out_command = command;
    return EU4_GFX_CLEAR_OK;
}

static int clear_gates(__unsafe_unretained EU4GfxBackend *backend, void *master, unsigned flags,
                       const float *color, uint32_t width, uint32_t height) {
    if (backend.poisoned || flags != 1u || backend.scissorValid ||
        !viewport_covers_attachment(master, width, height) || !color_mask_accepted(master) ||
        !color_in_range(color)) {
        return 0;
    }
    return 1;
}

eu4_gfx_clear_status eu4_gfx_configure_offscreen(void *master, uint32_t width, uint32_t height) {
    void *raw = backend_raw_for_master(master);
    __unsafe_unretained EU4GfxBackend *backend = (__bridge EU4GfxBackend *)raw;
    if (backend == nil || backend.device == nil || backend.poisoned) {
        return EU4_GFX_CLEAR_UNSUPPORTED;
    }
    if (backend.pendingClear != nil) {
        return EU4_GFX_CLEAR_UNSUPPORTED;
    }
    if (width == 0 || height == 0) {
        return EU4_GFX_CLEAR_UNSUPPORTED;
    }
    uint64_t pixels = (uint64_t)width * (uint64_t)height;
    if (pixels > (uint64_t)UINT32_MAX / 4u) {
        return EU4_GFX_CLEAR_UNSUPPORTED;
    }
    uint32_t texture_limit = max_texture(backend.device);
    if (width > texture_limit || height > texture_limit) {
        return EU4_GFX_CLEAR_UNSUPPORTED;
    }
    MTLTextureDescriptor *desc =
        [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm
                                                           width:width
                                                          height:height
                                                       mipmapped:NO];
    desc.usage = MTLTextureUsageRenderTarget;
    desc.storageMode = MTLStorageModeShared;
    id<MTLTexture> next = nil;
    if (!g_fail_offscreen_alloc) {
        next = [backend.device newTextureWithDescriptor:desc];
    } else {
        g_fail_offscreen_alloc = 0;
    }
    if (next == nil) {
        return EU4_GFX_CLEAR_FAILED;
    }
    backend.colorTarget = next;
    backend.colorWidth = width;
    backend.colorHeight = height;
    backend.contentsKnown = NO;
    return EU4_GFX_CLEAR_OK;
}

int eu4_gfx_offscreen_contents_known(void *master) {
    __unsafe_unretained EU4GfxBackend *backend =
        (__bridge EU4GfxBackend *)backend_raw_for_master(master);
    return backend != nil && backend.contentsKnown ? 1 : 0;
}

eu4_gfx_clear_status eu4_gfx_clear_checked(void *handle, unsigned flags, const float *color) {
    void *master = master_from_handle(handle);
    __unsafe_unretained EU4GfxBackend *backend =
        (__bridge EU4GfxBackend *)backend_raw_for_master(master);
    if (backend == nil || backend.poisoned) {
        return EU4_GFX_CLEAR_UNSUPPORTED;
    }
    if (backend.pendingClear != nil) {
        return EU4_GFX_CLEAR_UNSUPPORTED;
    }
    if (backend.colorTarget == nil || backend.colorWidth == 0 || backend.colorHeight == 0) {
        return EU4_GFX_CLEAR_UNSUPPORTED;
    }
    if (!clear_gates(backend, master, flags, color, backend.colorWidth, backend.colorHeight)) {
        return EU4_GFX_CLEAR_UNSUPPORTED;
    }
    id command = nil;
    eu4_gfx_clear_status encoded =
        encode_color_clear(backend, backend.colorTarget, color, flags, &command);
    if (encoded != EU4_GFX_CLEAR_OK) {
        return encoded;
    }
    backend.contentsKnown = NO;
    backend.pendingClear = command;
    [command commit];
    return EU4_GFX_CLEAR_OK;
}

static void write_default_viewport(void *master);

static eu4_gfx_clear_status apply_deferred_default_target(
    __unsafe_unretained EU4GfxBackend *backend, void *master) {
    if (backend == nil || !backend.deferredDefaultTargetSelect) {
        return EU4_GFX_CLEAR_OK;
    }
    if (geometry_transition_in_progress() || pending_is_obsolete(backend) ||
        backend.pendingGameCommand != nil) {
        /* No wait inside clear; leave the deferral for a drain-capable path. */
        return EU4_GFX_CLEAR_GEOMETRY_STALE;
    }
    if (backend.gameColorTarget == nil || backend.gameWidth != backend.physicalWidth ||
        backend.gameHeight != backend.physicalHeight) {
        eu4_gfx_clear_status allocated =
            allocate_game_backbuffer(backend, backend.physicalWidth, backend.physicalHeight);
        if (allocated != EU4_GFX_CLEAR_OK) {
            return allocated;
        }
    }
    write_default_viewport(master);
    store_ptr(master, 0x78, NULL);
    backend.deferredDefaultTargetSelect = NO;
    return EU4_GFX_CLEAR_OK;
}

static eu4_gfx_clear_status clear_game_checked(void *handle, unsigned flags, const float *color) {
    void *master = master_from_handle(handle);
    __unsafe_unretained EU4GfxBackend *backend =
        (__bridge EU4GfxBackend *)backend_raw_for_master(master);
    g_clear_reason = NULL;
    if (backend == nil || backend.poisoned || !backend.gameWindow) {
        g_clear_reason = "no usable Metal game backend";
        return EU4_GFX_CLEAR_UNSUPPORTED;
    }
    if (load_ptr(master, 0x78) != NULL) {
        g_clear_reason = "a non-null engine render target is not reconstructed";
        return EU4_GFX_CLEAR_UNSUPPORTED;
    }
    (void)reconcile_game_geometry(backend, master);
    if (pending_is_obsolete(backend)) {
        /* No wait, no submit, no clear into the obsolete attachment, and no
           write to the pending command or its generation. */
        g_clear_reason = "an older-generation GPU command is still pending";
        return EU4_GFX_CLEAR_GEOMETRY_STALE;
    }
    if (geometry_transition_in_progress()) {
        /* Phase 1: intended size published, committed generation unchanged.
           Same-gen pending must not become a fatal UNSUPPORTED. */
        g_clear_reason = "physical geometry transition is in progress";
        return EU4_GFX_CLEAR_GEOMETRY_STALE;
    }
    if (backend.pendingGameCommand != nil) {
        /* Same generation: the P5.1 ordinary second-clear rejection. */
        g_clear_reason = "a same-generation game command is already pending";
        return EU4_GFX_CLEAR_UNSUPPORTED;
    }
    eu4_gfx_clear_status deferred = apply_deferred_default_target(backend, master);
    if (deferred != EU4_GFX_CLEAR_OK) {
        g_clear_reason = "deferred default-target selection is not drainable here";
        return deferred;
    }
    eu4_gfx_clear_status allocated =
        allocate_game_backbuffer(backend, backend.physicalWidth, backend.physicalHeight);
    if (allocated != EU4_GFX_CLEAR_OK) {
        g_clear_reason = "game backbuffer allocation failed";
        return allocated;
    }
    if ((flags & ~7u) != 0) {
        g_clear_reason = "clear flags contain bits outside the established colour/depth/stencil mask";
        return EU4_GFX_CLEAR_UNSUPPORTED;
    }
    if ((flags & 1u) == 0) {
        g_clear_reason = "clear does not include the colour buffer bit";
        return EU4_GFX_CLEAR_UNSUPPORTED;
    }
    if ((flags & ~1u) != 0) {
        /* The game backbuffer is colour-only. There is no depth or stencil
           attachment in the reconstructed target, so those bits have no
           pixels to clear. Record the limitation rather than silently
           pretending a depth buffer exists. */
        char semantics[512];
        int length = snprintf(
            semantics, sizeof semantics,
            "{\"schema\":\"gfx-clear-semantics/v1\",\"flags\":%u,"
            "\"colour_cleared\":true,\"depth_stencil_ignored\":true,"
            "\"reason\":\"game backbuffer is colour-only; no depth attachment exists\"}\n",
            flags);
        if (length > 0 && (size_t)length < sizeof semantics) {
            (void)eu4_metal_events_write("clear-semantics.json", semantics);
        }
    }
    if (backend.scissorValid) {
        g_clear_reason = "a partial scissor is not supported by the clear";
        return EU4_GFX_CLEAR_UNSUPPORTED;
    }
    if (!viewport_covers_attachment(master, backend.logicalWidth, backend.logicalHeight)) {
        g_clear_reason = "viewport does not cover the logical attachment";
        return EU4_GFX_CLEAR_UNSUPPORTED;
    }
    if (!color_mask_accepted(master)) {
        g_clear_reason = "engine colour write mask is not 0x01010101";
        return EU4_GFX_CLEAR_UNSUPPORTED;
    }
    if (!color_in_range(color)) {
        g_clear_reason = "clear colour is outside [0,1]";
        return EU4_GFX_CLEAR_UNSUPPORTED;
    }
    id command = nil;
    eu4_gfx_clear_status encoded =
        encode_color_clear(backend, backend.gameColorTarget, color, flags, &command);
    if (encoded != EU4_GFX_CLEAR_OK) {
        g_clear_reason = "clear command encoding failed";
        return encoded;
    }
    backend.gameContentsKnown = NO;
    backend.readyForPresent = NO;
    backend.awaitingFreshClear = YES;
    backend.pendingGameGeneration = backend.geometryGeneration;
    backend.pendingGameCommand = command;
    backend.gameClearGeneration = backend.geometryGeneration;
    [command commit];
    return EU4_GFX_CLEAR_OK;
}

eu4_gfx_clear_status eu4_gfx_complete_offscreen(void *master, int *command_status) {
    __unsafe_unretained EU4GfxBackend *backend =
        (__bridge EU4GfxBackend *)backend_raw_for_master(master);
    if (backend == nil) {
        return EU4_GFX_CLEAR_UNSUPPORTED;
    }
    return drain_pending_clear(backend, command_status);
}

eu4_gfx_clear_status eu4_gfx_complete_game(void *master, int *command_status) {
    __unsafe_unretained EU4GfxBackend *backend =
        (__bridge EU4GfxBackend *)backend_raw_for_master(master);
    if (backend == nil) {
        return EU4_GFX_CLEAR_UNSUPPORTED;
    }
    return drain_pending_game(backend, command_status);
}

int eu4_gfx_game_contents_known(void *master) {
    __unsafe_unretained EU4GfxBackend *backend =
        (__bridge EU4GfxBackend *)backend_raw_for_master(master);
    return backend != nil && backend.gameContentsKnown ? 1 : 0;
}

int eu4_gfx_ready_for_present(void *master) {
    __unsafe_unretained EU4GfxBackend *backend =
        (__bridge EU4GfxBackend *)backend_raw_for_master(master);
    return backend != nil && backend.readyForPresent ? 1 : 0;
}

uint64_t eu4_gfx_geometry_generation(void *master) {
    __unsafe_unretained EU4GfxBackend *backend =
        (__bridge EU4GfxBackend *)backend_raw_for_master(master);
    return backend != nil ? backend.geometryGeneration : 0;
}

eu4_gfx_clear_status eu4_gfx_read_game_target(void *master, void *dest, size_t capacity,
                                              size_t *out_len, uint32_t *width,
                                              uint32_t *height, uint32_t *row_stride,
                                              uint64_t *generation, int *gpu_status) {
    __unsafe_unretained EU4GfxBackend *backend =
        (__bridge EU4GfxBackend *)backend_raw_for_master(master);
    if (backend == nil || backend.poisoned || backend.gameColorTarget == nil) {
        return EU4_GFX_CLEAR_UNSUPPORTED;
    }
    int status = (int)MTLCommandBufferStatusNotEnqueued;
    if (backend.pendingGameCommand != nil) {
        eu4_gfx_clear_status drained = drain_pending_game(backend, &status);
        if (drained != EU4_GFX_CLEAR_OK) {
            return drained;
        }
    } else if (backend.gameContentsKnown) {
        /* A previous drain completed the last game command; the known contents
           were produced by that completed submission. */
        status = (int)MTLCommandBufferStatusCompleted;
    }
    if (!backend.gameContentsKnown) {
        return EU4_GFX_CLEAR_UNSUPPORTED;
    }
    uint64_t need = (uint64_t)backend.gameWidth * (uint64_t)backend.gameHeight * 4u;
    if (need == 0 || need > SIZE_MAX || dest == NULL || capacity < (size_t)need) {
        return EU4_GFX_CLEAR_FAILED;
    }
    [backend.gameColorTarget getBytes:dest
                          bytesPerRow:(NSUInteger)backend.gameWidth * 4u
                           fromRegion:MTLRegionMake2D(0, 0, backend.gameWidth, backend.gameHeight)
                          mipmapLevel:0];
    if (out_len != NULL) {
        *out_len = (size_t)need;
    }
    if (width != NULL) {
        *width = backend.gameWidth;
    }
    if (height != NULL) {
        *height = backend.gameHeight;
    }
    if (row_stride != NULL) {
        *row_stride = backend.gameWidth * 4u;
    }
    if (generation != NULL) {
        *generation = backend.geometryGeneration;
    }
    if (gpu_status != NULL) {
        *gpu_status = status;
    }
    return EU4_GFX_CLEAR_OK;
}

eu4_gfx_clear_status eu4_gfx_read_offscreen(void *master, void *dest, size_t capacity,
                                            size_t *out_len) {
    __unsafe_unretained EU4GfxBackend *backend =
        (__bridge EU4GfxBackend *)backend_raw_for_master(master);
    if (backend == nil || backend.colorTarget == nil) {
        return EU4_GFX_CLEAR_UNSUPPORTED;
    }
    if (!backend.contentsKnown || backend.pendingClear != nil) {
        return EU4_GFX_CLEAR_UNSUPPORTED;
    }
    uint64_t need = (uint64_t)backend.colorWidth * (uint64_t)backend.colorHeight * 4u;
    if (need == 0 || need > SIZE_MAX || dest == NULL || capacity < (size_t)need) {
        return EU4_GFX_CLEAR_FAILED;
    }
    id<MTLTexture> target = backend.colorTarget;
    [target getBytes:dest
         bytesPerRow:(NSUInteger)backend.colorWidth * 4u
          fromRegion:MTLRegionMake2D(0, 0, backend.colorWidth, backend.colorHeight)
         mipmapLevel:0];
    if (out_len != NULL) {
        *out_len = (size_t)need;
    }
    return EU4_GFX_CLEAR_OK;
}

/* The frontend does not call _GfxPresent while its present flag is clear; it
   swaps the window instead. The reconstructed window is a CAMetalLayer, so the
   swap is the natural presentation boundary: blit the completed backbuffer to
   the layer's drawable and present it. */
/* GL resolve helpers: the Metal targets are single-sample and already hold
   the resolved colour, so the GL resolve steps are no-ops. */
/* Host lifecycle tests link this file without the texture module; resolve the
   lookup at run time so those hosts still link. */
typedef void *(*eu4_gfx_texture_lookup)(void *header);
static void *texture_for_header(void *header) {
    static eu4_gfx_texture_lookup lookup;
    static int tried;
    if (!tried) {
        tried = 1;
        lookup = (eu4_gfx_texture_lookup)dlsym(RTLD_DEFAULT, "eu4_gfx_texture_for_header");
    }
    return lookup != NULL ? lookup(header) : NULL;
}

static void dump_mtl_texture(id<MTLTexture> texture, const char *path) {
    if (texture == nil || path == NULL) {
        return;
    }
    NSUInteger row = texture.width * 4;
    id<MTLBuffer> buffer = [texture.device newBufferWithLength:row * texture.height
                                                      options:MTLResourceStorageModeShared];
    if (buffer == nil) {
        return;
    }
    EU4GfxBackend *dump_backend = (__bridge EU4GfxBackend *)eu4_gfx_backend_token();
    id<MTLCommandQueue> queue = (dump_backend != nil && dump_backend.queue != nil)
        ? dump_backend.queue : [texture.device newCommandQueue];
    id<MTLCommandBuffer> command = [queue commandBuffer];
    id<MTLBlitCommandEncoder> blit = [command blitCommandEncoder];
    [blit copyFromTexture:texture
              sourceSlice:0
              sourceLevel:0
             sourceOrigin:MTLOriginMake(0, 0, 0)
               sourceSize:MTLSizeMake(texture.width, texture.height, 1)
                 toBuffer:buffer
        destinationOffset:0
   destinationBytesPerRow:row
 destinationBytesPerImage:row * texture.height];
    [blit endEncoding];
    [command commit];
    [command waitUntilCompleted];
    uint8_t *flipped = (uint8_t *)malloc(row * texture.height);
    if (flipped == NULL) {
        return;
    }
    const uint8_t *src = (const uint8_t *)buffer.contents;
    for (NSUInteger y = 0; y < texture.height; y++) {
        memcpy(flipped + y * row, src + (texture.height - 1 - y) * row, row);
    }
    FILE *file = fopen(path, "wb");
    if (file != NULL) {
        fwrite(flipped, 1, row * texture.height, file);
        fclose(file);
    }
    free(flipped);
}

static const char *kResolveMSL =
    "#include <metal_stdlib>\n"
    "using namespace metal;\n"
    "struct V { float4 pos [[position]]; float2 uv; };\n"
    "vertex V resolve_vs(uint vid [[vertex_id]]) {\n"
    "    float2 p[3] = {float2(-1.0, -1.0), float2(3.0, -1.0), float2(-1.0, 3.0)};\n"
    "    V o; o.pos = float4(p[vid], 0.0, 1.0);\n"
    "    o.uv = float2((p[vid].x + 1.0) * 0.5, (1.0 - p[vid].y) * 0.5);\n"
    "    return o;\n"
    "}\n"
    "fragment float4 resolve_fs(V in [[stage_in]], texture2d<float> t [[texture(0)]]) {\n"
    "    constexpr sampler s(coord::normalized, filter::linear, address::clamp_to_edge);\n"
    "    return t.sample(s, in.uv);\n"
    "}\n";

static id<MTLRenderPipelineState> resolve_pipeline(id<MTLDevice> device) {
    static id<MTLRenderPipelineState> pipeline;
    static id<MTLDevice> cached_device;
    if (pipeline != nil && cached_device == device) {
        return pipeline;
    }
    NSError *error = nil;
    id<MTLLibrary> library = [device newLibraryWithSource:
        [NSString stringWithUTF8String:kResolveMSL] options:nil error:&error];
    if (library == nil) {
        char body[1024];
        int n = snprintf(body, sizeof body,
                         "{\"schema\":\"gfx-resolve-error/v1\",\"stage\":\"library\","
                         "\"error\":\"%s\",\"pid\":%d}\n",
                         error.localizedDescription.UTF8String ?: "", (int)getpid());
        if (n > 0 && (size_t)n < sizeof body) {
            (void)eu4_metal_events_write("resolve-scaled-error.json", body);
        }
        return nil;
    }
    MTLRenderPipelineDescriptor *descriptor = [MTLRenderPipelineDescriptor new];
    descriptor.vertexFunction = [library newFunctionWithName:@"resolve_vs"];
    descriptor.fragmentFunction = [library newFunctionWithName:@"resolve_fs"];
    descriptor.colorAttachments[0].pixelFormat = MTLPixelFormatBGRA8Unorm;
    pipeline = [device newRenderPipelineStateWithDescriptor:descriptor error:&error];
    if (pipeline != nil) {
        cached_device = device;
    } else {
        char body[1024];
        int n = snprintf(body, sizeof body,
                         "{\"schema\":\"gfx-resolve-error/v1\",\"stage\":\"pipeline\","
                         "\"error\":\"%s\",\"pid\":%d}\n",
                         error.localizedDescription.UTF8String ?: "", (int)getpid());
        if (n > 0 && (size_t)n < sizeof body) {
            (void)eu4_metal_events_write("resolve-scaled-error.json", body);
        }
    }
    return pipeline;
}

static void resolve_scaled(id<MTLTexture> src, id<MTLTexture> dst,
                           id<MTLDevice> device, id<MTLCommandQueue> queue) {
    id<MTLRenderPipelineState> pipeline = resolve_pipeline(device);
    if (pipeline == nil) {
        return;
    }
    id<MTLSamplerState> sampler = nil;
    MTLSamplerDescriptor *s = [MTLSamplerDescriptor new];
    s.minFilter = MTLSamplerMinMagFilterLinear;
    s.magFilter = MTLSamplerMinMagFilterLinear;
    sampler = [device newSamplerStateWithDescriptor:s];
    MTLRenderPassDescriptor *pass = [MTLRenderPassDescriptor renderPassDescriptor];
    pass.colorAttachments[0].texture = dst;
    pass.colorAttachments[0].loadAction = MTLLoadActionClear;
    pass.colorAttachments[0].clearColor = MTLClearColorMake(0.0, 0.0, 0.0, 0.0);
    pass.colorAttachments[0].storeAction = MTLStoreActionStore;
    id<MTLCommandBuffer> command = [queue commandBuffer];
    id<MTLRenderCommandEncoder> encoder = [command renderCommandEncoderWithDescriptor:pass];
    if (encoder == nil) {
        return;
    }
    [encoder setRenderPipelineState:pipeline];
    [encoder setFragmentTexture:src atIndex:0];
    [encoder setFragmentSamplerState:sampler atIndex:0];
    [encoder drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
    [encoder endEncoding];
    [command commit];
    [command waitUntilCompleted];
}

void eu4_gfx_resolve_noop(void *unused, void *header) {
    (void)unused;
    /* _GfxResolve is glCopyTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, 0, 0, w, h):
       it copies the current framebuffer into the header's texture. The sampled
       map textures are filled exactly here; a no-op left them empty. */
    if (header == NULL) {
        return;
    }
    EU4GfxBackend *backend = (__bridge EU4GfxBackend *)eu4_gfx_backend_token();
    if (backend == nil || backend.queue == nil) {
        return;
    }
    id<MTLTexture> dst = (__bridge id<MTLTexture>)texture_for_header(header);
    /* glCopyTexSubImage2D observes every command issued before it. Record the
       sequence and drain the pending submission so the copy cannot read a
       backbuffer whose draws are still pending. */
    eu4_gfx_order_trace("resolve", eu4_gfx_live_draw_sequence);
    if (backend.pendingGameCommand != nil) {
        (void)drain_pending_game(backend, NULL);
    }
    /* The engine's copy reads the current default framebuffer (fb0): early in
       the frame it holds the map passes, later it holds the complete frame.
       Copy the live backbuffer every call, exactly like glCopyTexSubImage2D. */
    id<MTLTexture> src = backend.gameColorTarget;
    if (src == nil) {
        src = backend.previousFrameTexture;
    }
    if (getenv("EU4_GFX_RESOLVE_TRACE") != NULL) {
        char body[512];
        int n = snprintf(
            body, sizeof body,
            "{\"schema\":\"gfx-resolve/v1\",\"header\":\"%p\","
            "\"src\":\"%p\",\"dst\":\"%p\","
            "\"current\":\"%p\",\"src_size\":[%lu,%lu],"
            "\"dst_size\":[%lu,%lu],\"draw_seq\":%llu,\"clear_seq\":%llu,"
            "\"pid\":%d}\n",
            header, (__bridge void *)src, (__bridge void *)dst,
            backend.currentTargetColor,
            (unsigned long)(src != nil ? src.width : 0),
            (unsigned long)(src != nil ? src.height : 0),
            (unsigned long)(dst != nil ? dst.width : 0),
            (unsigned long)(dst != nil ? dst.height : 0),
            (unsigned long long)eu4_gfx_live_draw_sequence,
            (unsigned long long)eu4_gfx_live_clear_sequence, (int)getpid());
        if (n > 0 && (size_t)n < sizeof body) {
            (void)eu4_metal_events_write("resolve.json", body);
        }
    }
    if (src == nil || dst == nil) {
        return;
    }
    const char *resolve_dir = getenv("EU4_GFX_RESOLVE_DUMP");
    static unsigned resolve_count;
    if (resolve_dir != NULL && resolve_dir[0] != 0) {
        char path[512];
        snprintf(path, sizeof path, "%s/resolve-source-%03u.bgra", resolve_dir,
                 resolve_count++);
        dump_mtl_texture(src, path);
    }
    if (src.width != dst.width || src.height != dst.height) {
        /* The engine's default framebuffer is logical (1728x1117) in GL; the
           reconstructed backbuffer is the physical drawable, so a scaled copy
           is required to fill the sampled texture. */
        resolve_scaled(src, dst, backend.device, backend.queue);
        if (resolve_dir != NULL && resolve_dir[0] != 0) {
            char path[512];
            snprintf(path, sizeof path, "%s/resolve-dest-%lux%lu.bgra", resolve_dir,
                     (unsigned long)dst.width, (unsigned long)dst.height);
            dump_mtl_texture(dst, path);
        }
        return;
    }
    id<MTLCommandBuffer> command = [backend.queue commandBuffer];
    id<MTLBlitCommandEncoder> blit = command != nil ? [command blitCommandEncoder] : nil;
    if (blit != nil) {
        [blit copyFromTexture:src
                  sourceSlice:0
                  sourceLevel:0
                 sourceOrigin:MTLOriginMake(0, 0, 0)
                   sourceSize:MTLSizeMake(src.width, src.height, 1)
                    toTexture:dst
             destinationSlice:0
             destinationLevel:0
            destinationOrigin:MTLOriginMake(0, 0, 0)];
        [blit endEncoding];
        [command commit];
        [command waitUntilCompleted];
    }
}

void eu4_gfx_resolve_texture_noop(void *handle, void *source, void *target) {
    (void)handle;
    /* _GfxResolveTexture blits the source render target into the sampled
       (resolved) texture. The reconstructed targets are single-sample, so a
       Metal texture copy is the exact equivalent; skipping it left the
       composite sampling stale texture contents. */
    id<MTLTexture> src = (__bridge id<MTLTexture>)texture_for_header(source);
    id<MTLTexture> dst = (__bridge id<MTLTexture>)texture_for_header(target);
    EU4GfxBackend *backend = (__bridge EU4GfxBackend *)eu4_gfx_backend_token();
    if (getenv("EU4_GFX_RESOLVE_TRACE") != NULL) {
        char body[512];
        int n = snprintf(
            body, sizeof body,
            "{\"schema\":\"gfx-resolve-texture/v1\",\"source\":\"%p\","
            "\"target\":\"%p\",\"src\":\"%p\",\"dst\":\"%p\","
            "\"src_size\":[%lu,%lu],\"dst_size\":[%lu,%lu],\"pid\":%d}\n",
            source, target, (__bridge void *)src, (__bridge void *)dst,
            (unsigned long)(src != nil ? src.width : 0),
            (unsigned long)(src != nil ? src.height : 0),
            (unsigned long)(dst != nil ? dst.width : 0),
            (unsigned long)(dst != nil ? dst.height : 0), (int)getpid());
        if (n > 0 && (size_t)n < sizeof body) {
            (void)eu4_metal_events_write("resolve-texture.json", body);
        }
    }
    if (getenv("EU4_GFX_RESOLVE_TRACE") != NULL) {
        char body[512];
        int n = snprintf(
            body, sizeof body,
            "{\"schema\":\"gfx-resolve-texture/v1\",\"source\":\"%p\","
            "\"target\":\"%p\",\"src\":\"%p\",\"dst\":\"%p\","
            "\"src_size\":[%lu,%lu],\"dst_size\":[%lu,%lu],\"pid\":%d}\n",
            source, target, (__bridge void *)src, (__bridge void *)dst,
            (unsigned long)(src != nil ? src.width : 0),
            (unsigned long)(src != nil ? src.height : 0),
            (unsigned long)(dst != nil ? dst.width : 0),
            (unsigned long)(dst != nil ? dst.height : 0), (int)getpid());
        if (n > 0 && (size_t)n < sizeof body) {
            (void)eu4_metal_events_write("resolve-texture.json", body);
        }
    }
    if (src == nil || dst == nil || backend == nil || backend.queue == nil) {
        return;
    }
    const char *resolve_dir = getenv("EU4_GFX_RESOLVE_DUMP");
    static unsigned resolve_count;
    if (resolve_dir != NULL && resolve_dir[0] != 0) {
        char path[512];
        snprintf(path, sizeof path, "%s/resolve-source-%03u.bgra", resolve_dir,
                 resolve_count++);
        dump_mtl_texture(src, path);
    }
    if (src.width != dst.width || src.height != dst.height) {
        return;
    }
    id<MTLCommandBuffer> command = [backend.queue commandBuffer];
    if (command == nil) {
        return;
    }
    id<MTLBlitCommandEncoder> blit = [command blitCommandEncoder];
    if (blit == nil) {
        return;
    }
    [blit copyFromTexture:src
              sourceSlice:0
              sourceLevel:0
             sourceOrigin:MTLOriginMake(0, 0, 0)
               sourceSize:MTLSizeMake(src.width, src.height, 1)
                toTexture:dst
         destinationSlice:0
         destinationLevel:0
        destinationOrigin:MTLOriginMake(0, 0, 0)];
    [blit endEncoding];
    [command commit];
    [command waitUntilCompleted];
    if (resolve_dir != NULL && resolve_dir[0] != 0) {
        char path[512];
        snprintf(path, sizeof path, "%s/resolve-dest-%lux%lu.bgra", resolve_dir,
                 (unsigned long)dst.width, (unsigned long)dst.height);
        dump_mtl_texture(dst, path);
    }
}

/* CGraphics::PresentScene (0x149daf0). The frontend clears the present flag,
   so the original only calls _GfxPresent on some frames; the title scene is
   single-buffer style and needs a present at this frame boundary. */
static void preserve_previous_frame(void) {
    EU4GfxBackend *backend = (__bridge EU4GfxBackend *)eu4_gfx_backend_token();
    if (backend == nil || backend.queue == nil || backend.gameColorTarget == nil) {
        return;
    }
    id<MTLTexture> source = backend.gameColorTarget;
    id<MTLTexture> copy = backend.previousFrameTexture;
    if (copy == nil || copy.width != source.width || copy.height != source.height) {
        MTLTextureDescriptor *descriptor =
            [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm
                                                               width:source.width
                                                              height:source.height
                                                           mipmapped:NO];
        descriptor.usage = MTLTextureUsageShaderRead;
        descriptor.storageMode = MTLStorageModePrivate;
        copy = [backend.device newTextureWithDescriptor:descriptor];
        backend.previousFrameTexture = copy;
    }
    if (copy == nil) {
        return;
    }
    id<MTLCommandBuffer> command = [backend.queue commandBuffer];
    id<MTLBlitCommandEncoder> blit = [command blitCommandEncoder];
    if (blit == nil) {
        return;
    }
    [blit copyFromTexture:source
              sourceSlice:0
              sourceLevel:0
             sourceOrigin:MTLOriginMake(0, 0, 0)
               sourceSize:MTLSizeMake(source.width, source.height, 1)
                toTexture:copy
         destinationSlice:0
         destinationLevel:0
        destinationOrigin:MTLOriginMake(0, 0, 0)];
    [blit endEncoding];
    [command commit];
    [command waitUntilCompleted];
}

uint64_t eu4_gfx_live_draw_sequence;
uint64_t eu4_gfx_live_clear_sequence;
uint8_t eu4_gfx_last_raster_description[0x10];

/* Opt-in submission-order trace: records draws, clears and resolves with a
   global sequence, thread and timestamp so deferred/threaded submission order
   can be compared with the engine's CPU order. */
void eu4_gfx_order_trace(const char *operation, unsigned long long value) {
    if (getenv("EU4_GFX_ORDER_TRACE") == NULL) {
        return;
    }
    static uint64_t sequence;
    static int enabled_check;
    if (!enabled_check) {
        enabled_check = 1;
    }
    uint64_t seq = __atomic_fetch_add(&sequence, 1, __ATOMIC_RELAXED) + 1;
    if (seq > 20000) {
        return;
    }
    char body[256];
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    uint64_t thread = 0;
    (void)pthread_threadid_np(NULL, &thread);
    int n = snprintf(body, sizeof body,
                     "{\"seq\":%llu,\"op\":\"%s\",\"value\":%llu,"
                     "\"thread\":%llu,\"ns\":%llu}\n",
                     (unsigned long long)seq, operation, value,
                     (unsigned long long)thread,
                     (unsigned long long)(ts.tv_sec * 1000000000ull + ts.tv_nsec));
    if (n > 0 && (size_t)n < sizeof body) {
        (void)eu4_metal_events_append("order.jsonl", body);
    }
}

void eu4_gfx_present_scene(void *self) {
    if (self == NULL) {
        return;
    }
    uint8_t flag = 0;
    memcpy(&flag, (uint8_t *)self + 0x4e8, 1);
    if (flag != 0) {
        void *master = NULL;
        memcpy(&master, (uint8_t *)self + 0x80, sizeof master);
        if (master != NULL) {
            int ok = eu4_gfx_present(master);
            uint8_t value = ok ? 1 : 0;
            memcpy((uint8_t *)self + 0x4e8, &value, 1);
        }
    }
    (void)eu4_gfx_swap_window(NULL);
    preserve_previous_frame();
}

int eu4_gfx_cocoa_swap_window(void *window) {
    return eu4_gfx_swap_window(window);
}

int eu4_gfx_cocoa_gles_swap_window(void *window) {
    return eu4_gfx_swap_window(window);
}

int eu4_gfx_swap_window(void *window) {
    (void)window;
    if (g_master == NULL) {
        return 0;
    }
    __unsafe_unretained EU4GfxBackend *backend =
        (__bridge EU4GfxBackend *)backend_raw_for_master(g_master);
    if (backend == nil || backend.queue == nil || backend.gameColorTarget == nil) {
        return 0;
    }
    if (backend.pendingGameCommand != nil) {
        if (drain_pending_game(backend, NULL) != EU4_GFX_CLEAR_OK) {
            return 0;
        }
    }
    id<CAMetalDrawable> drawable = nil;
    id<MTLTexture> destination = nil;
    if (g_present_drawable != NULL) {
        destination = (__bridge id<MTLTexture>)g_present_drawable;
    } else {
        CAMetalLayer *layer = backend.layer;
        drawable = layer != nil ? [layer nextDrawable] : nil;
        destination = drawable.texture;
    }
    {
        char body[384];
        int n = snprintf(body, sizeof body,
                         "{\"schema\":\"gfx-swap/v1\",\"game\":[%u,%u],"
                         "\"drawable\":[%lu,%lu],\"destination\":%s,\"pid\":%d}\n",
                         backend.gameWidth, backend.gameHeight,
                         (unsigned long)(destination != nil ? destination.width : 0),
                         (unsigned long)(destination != nil ? destination.height : 0),
                         destination != nil ? "true" : "false", (int)getpid());
        if (n > 0 && (size_t)n < sizeof body) {
            (void)eu4_metal_events_write("swap.json", body);
        }
    }
    if (destination == nil || destination.width != backend.gameWidth ||
        destination.height != backend.gameHeight) {
        return 0;
    }
    id<MTLCommandBuffer> command = [backend.queue commandBuffer];
    if (command == nil) {
        return 0;
    }
    id<MTLBlitCommandEncoder> blit = [command blitCommandEncoder];
    if (blit == nil) {
        return 0;
    }
    [blit copyFromTexture:backend.gameColorTarget
              sourceSlice:0
              sourceLevel:0
             sourceOrigin:MTLOriginMake(0, 0, 0)
               sourceSize:MTLSizeMake(backend.gameWidth, backend.gameHeight, 1)
                toTexture:destination
         destinationSlice:0
         destinationLevel:0
        destinationOrigin:MTLOriginMake(0, 0, 0)];
    [blit endEncoding];
    id<MTLBuffer> dump_buffer = nil;
    const char *dump_path = getenv("EU4_GFX_SWAP_DUMP");
    if (dump_path != NULL && dump_path[0] != 0) {
        NSUInteger row = (NSUInteger)backend.gameWidth * 4;
        dump_buffer = [backend.device newBufferWithLength:row * backend.gameHeight
                                                  options:MTLResourceStorageModeShared];
        if (dump_buffer != nil) {
            id<MTLBlitCommandEncoder> readback = [command blitCommandEncoder];
            if (readback != nil) {
                [readback copyFromTexture:backend.gameColorTarget
                              sourceSlice:0
                              sourceLevel:0
                             sourceOrigin:MTLOriginMake(0, 0, 0)
                               sourceSize:MTLSizeMake(backend.gameWidth, backend.gameHeight, 1)
                                 toBuffer:dump_buffer
                        destinationOffset:0
                   destinationBytesPerRow:row
                 destinationBytesPerImage:row * backend.gameHeight];
                [readback endEncoding];
            }
        }
    }
    if (drawable != nil) {
        [command presentDrawable:drawable];
    }
    [command commit];
    [command waitUntilCompleted];
    if (dump_buffer != nil) {
        NSUInteger row = (NSUInteger)backend.gameWidth * 4;
        uint8_t *flipped = (uint8_t *)malloc(row * backend.gameHeight);
        if (flipped != NULL) {
            const uint8_t *src = (const uint8_t *)dump_buffer.contents;
            for (uint32_t y = 0; y < backend.gameHeight; y++) {
                memcpy(flipped + (size_t)y * row,
                       src + (size_t)(backend.gameHeight - 1 - y) * row, row);
            }
            FILE *file = fopen(dump_path, "wb");
            if (file != NULL) {
                fwrite(flipped, 1, row * backend.gameHeight, file);
                fclose(file);
            }
            free(flipped);
        }
    }
    return 0;
}

/* The engine's clear mask: bit 0 colour, bit 1 depth, bit 2 stencil. The same
   decoding is used for the default backbuffer and render-target clears. */
void eu4_gfx_effective_depth_state(int has_depth, uint8_t enable, uint8_t write,
                                   int *test_out, int *write_out) {
    int test = has_depth && enable != 0;
    int write_enabled = test && write != 0;
    if (test_out != NULL) {
        *test_out = test;
    }
    if (write_out != NULL) {
        *write_out = write_enabled;
    }
}

static void decode_engine_clear_flags(unsigned flags, bool *color, bool *depth,
                                      bool *stencil) {
    if (color != NULL) {
        *color = (flags & 1u) != 0;
    }
    if (depth != NULL) {
        *depth = (flags & 2u) != 0;
    }
    if (stencil != NULL) {
        *stencil = (flags & 4u) != 0;
    }
}

void eu4_gfx_clear(void *handle, unsigned flags, const float *color) {
    eu4_gfx_live_clear_sequence++;
    eu4_gfx_order_trace("clear", eu4_gfx_live_clear_sequence);
    void *master = master_from_handle(handle);
    __unsafe_unretained EU4GfxBackend *backend =
        (__bridge EU4GfxBackend *)backend_raw_for_master(master);
    if (backend != nil && backend.currentTargetHeader != NULL &&
        backend.currentTargetColor != nil && backend.queue != nil) {
        /* A render-target clear belongs to that target, not to the window
           backbuffer state machine. One command buffer keeps queue order with
           the draws that follow. `_GfxClear` receives the engine's clear flags
           (bit 0 colour, bit 1 depth, bit 2 stencil), not GL mask bits. */
        bool clear_color = false, clear_depth = false, clear_stencil = false;
        decode_engine_clear_flags(flags, &clear_color, &clear_depth,
                                  &clear_stencil);
        if ((flags & ~7u) != 0) {
            char body[256];
            int length = snprintf(
                body, sizeof body,
                "{\"schema\":\"gfx-clear-rejected/v1\",\"flags\":%u,"
                "\"pid\":%d}\n",
                flags, (int)getpid());
            if (length > 0 && (size_t)length < sizeof body) {
                (void)eu4_metal_events_write("clear-rejected.json", body);
            }
            return;
        }
        (void)clear_stencil;
        id<MTLCommandBuffer> command = [backend.queue commandBuffer];
        if (command != nil) {
            MTLRenderPassDescriptor *pass = [MTLRenderPassDescriptor renderPassDescriptor];
            pass.colorAttachments[0].texture = backend.currentTargetColor;
            if (clear_color) {
                double r = 0.0, g = 0.0, b = 0.0, a = 1.0;
                if (color != NULL) {
                    r = color[0];
                    g = color[1];
                    b = color[2];
                    a = color[3];
                }
                pass.colorAttachments[0].loadAction = MTLLoadActionClear;
                pass.colorAttachments[0].clearColor = MTLClearColorMake(r, g, b, a);
            } else {
                pass.colorAttachments[0].loadAction = MTLLoadActionLoad;
            }
            pass.colorAttachments[0].storeAction = MTLStoreActionStore;
            if (backend.currentTargetDepth != nil) {
                pass.depthAttachment.texture = backend.currentTargetDepth;
                pass.depthAttachment.loadAction =
                    clear_depth ? MTLLoadActionClear : MTLLoadActionLoad;
                pass.depthAttachment.clearDepth = 1.0;
                pass.depthAttachment.storeAction = MTLStoreActionStore;
            }
            /* No stencil attachment is reconstructed: bit 2 is accepted but
               must not be claimed as cleared. */
            id<MTLRenderCommandEncoder> encoder = [command renderCommandEncoderWithDescriptor:pass];
            if (encoder != nil) {
                [encoder endEncoding];
                [command commit];
                backend.pendingGameCommand = command;
                backend.pendingGameGeneration = backend.geometryGeneration;
                backend.gameContentsKnown = NO;
                backend.readyForPresent = NO;
            }
        }
        return;
    }
    eu4_gfx_clear_status status;
    if (backend != nil && backend.gameWindow) {
        status = clear_game_checked(handle, flags, color);
    } else {
        status = eu4_gfx_clear_checked(handle, flags, color);
    }
    if (status == EU4_GFX_CLEAR_GEOMETRY_STALE) {
        /* A geometry transition is a normal no-op, not a fatal clear. */
        return;
    }
    if (status == EU4_GFX_CLEAR_OK && backend != nil && backend.gameWindow) {
        eu4_gfx_logical_capture_set_clear(color);
        eu4_gfx_logical_capture_reset();
    }
    if (status != EU4_GFX_CLEAR_OK) {
        if (backend != nil) {
            uint32_t view_h = 0, view_w = 0, view_x = 0, view_y = 0, mask = 0;
            memcpy(&view_h, (uint8_t *)master + 0xac, sizeof view_h);
            memcpy(&view_w, (uint8_t *)master + 0xb0, sizeof view_w);
            memcpy(&view_x, (uint8_t *)master + 0xbc, sizeof view_x);
            memcpy(&view_y, (uint8_t *)master + 0xc0, sizeof view_y);
            memcpy(&mask, (uint8_t *)master + 0x2c, sizeof mask);
            char body[1024];
            int length = snprintf(
                body, sizeof body,
                "{\"schema\":\"gfx-clear-diagnostic/v1\",\"symbol\":\"_GfxClear\","
                "\"caller_rva\":\"0x%llx\",\"reason\":\"%s\",\"flags\":%u,"
                "\"logical\":[%u,%u],\"physical\":[%u,%u],"
                "\"viewport\":[%u,%u,%u,%u],\"color_mask\":\"0x%x\","
                "\"scissor_valid\":%s,\"pending\":%s,"
                "\"geometry_generation\":%llu,\"clear_generation\":%llu}\n",
                (unsigned long long)(uintptr_t)__builtin_return_address(0),
                g_clear_reason != NULL ? g_clear_reason : "unspecified",
                flags, backend.logicalWidth, backend.logicalHeight,
                backend.physicalWidth, backend.physicalHeight, view_x, view_y,
                view_w, view_h, mask, backend.scissorValid ? "true" : "false",
                backend.pendingGameCommand != nil ? "true" : "false",
                (unsigned long long)backend.geometryGeneration,
                (unsigned long long)backend.gameClearGeneration);
            if (length > 0 && (size_t)length < sizeof body) {
                (void)eu4_metal_events_write("clear-unsupported.json", body);
            }
        }
        eu4_gfx_stop("_GfxClear", status == EU4_GFX_CLEAR_UNSUPPORTED ? "unsupported" : "failed",
                     __builtin_return_address(0));
    }
    if (g_checkpoint_after_clear != NULL) {
        g_checkpoint_after_clear(master, __builtin_return_address(0));
    }
}

static void write_default_viewport(void *master) {
    uint32_t save_b4 = 0, save_b8 = 0, width = 0, height = 0;
    memcpy(&save_b4, (uint8_t *)master + 0xb4, sizeof save_b4);
    memcpy(&save_b8, (uint8_t *)master + 0xb8, sizeof save_b8);
    memcpy(&width, (uint8_t *)master + 0xa0, sizeof width);
    memcpy(&height, (uint8_t *)master + 0xa4, sizeof height);
    memcpy((uint8_t *)master + 0xac, &height, sizeof height);
    memcpy((uint8_t *)master + 0xb0, &width, sizeof width);
    memcpy((uint8_t *)master + 0xb4, &save_b4, sizeof save_b4);
    memcpy((uint8_t *)master + 0xb8, &save_b8, sizeof save_b8);
    uint64_t zero = 0;
    memcpy((uint8_t *)master + 0xbc, &zero, sizeof zero);
}

eu4_gfx_clear_status eu4_gfx_set_render_target_checked(void *handle, void *target) {
    void *master = master_from_handle(handle);
    __unsafe_unretained EU4GfxBackend *backend =
        (__bridge EU4GfxBackend *)backend_raw_for_master(master);
    if (backend == nil || backend.poisoned || !backend.gameWindow) {
        return EU4_GFX_CLEAR_UNSUPPORTED;
    }
    if (target != NULL) {
        return EU4_GFX_CLEAR_UNSUPPORTED;
    }
    int transitioned = reconcile_game_geometry(backend, master);
    if (geometry_transition_in_progress()) {
        /* Phase 1: do not mutate master or allocate for uncommitted size.
           Record that default-target selection must complete after commit. */
        backend.deferredDefaultTargetSelect = YES;
        return EU4_GFX_CLEAR_GEOMETRY_STALE;
    }
    if (pending_is_obsolete(backend)) {
        /* Waiting is allowed here, not inside clear. */
        eu4_gfx_clear_status drained = drain_pending_game(backend, NULL);
        if (drained != EU4_GFX_CLEAR_OK) {
            return drained;
        }
    } else if (backend.pendingGameCommand != nil) {
        /* Same generation: the P5.1 unrelated-SetRT rejection. */
        return EU4_GFX_CLEAR_UNSUPPORTED;
    }
    if (transitioned || backend.gameColorTarget == nil ||
        backend.gameWidth != backend.physicalWidth ||
        backend.gameHeight != backend.physicalHeight ||
        backend.deferredDefaultTargetSelect) {
        /* Allocate before mutating the master. A failure keeps the old texture
           and every engine-visible field intact. */
        eu4_gfx_clear_status allocated =
            allocate_game_backbuffer(backend, backend.physicalWidth, backend.physicalHeight);
        if (allocated != EU4_GFX_CLEAR_OK) {
            return allocated;
        }
    }
    write_default_viewport(master);
    store_ptr(master, 0x78, NULL);
    backend.deferredDefaultTargetSelect = NO;
    return EU4_GFX_CLEAR_OK;
}

static void dump_render_target(void *master, void *target) {
    if (target == NULL) {
        return;
    }
    uint8_t bytes[0x80];
    memset(bytes, 0, sizeof bytes);
    memcpy(bytes, target, sizeof bytes);
    char hex[0x80 * 2 + 1];
    static const char digits[] = "0123456789abcdef";
    for (int i = 0; i < 0x80; i++) {
        hex[i * 2] = digits[bytes[i] >> 4];
        hex[i * 2 + 1] = digits[bytes[i] & 0xf];
    }
    hex[0x80 * 2] = 0;
    char body[1024];
    int length = snprintf(
        body, sizeof body,
        "{\"schema\":\"gfx-render-target/v1\",\"target\":\"%p\",\"master\":\"%p\","
        "\"fbo_name\":%u,\"width\":%u,\"height\":%u,\"bytes\":\"%s\","
        "\"qwords\":[\"%p\",\"%p\",\"%p\",\"%p\",\"%p\",\"%p\",\"%p\",\"%p\"],"
        "\"pid\":%d}\n",
        target, master, load_u32(bytes, 0xc), load_u32(bytes, 0x28),
        load_u32(bytes, 0x2c), hex,
        load_ptr(bytes, 0), load_ptr(bytes, 8), load_ptr(bytes, 0x10),
        load_ptr(bytes, 0x18), load_ptr(bytes, 0x20), load_ptr(bytes, 0x28),
        load_ptr(bytes, 0x30), load_ptr(bytes, 0x38), (int)getpid());
    if (length > 0 && (size_t)length < sizeof body) {
        (void)eu4_metal_events_write("render-target.json", body);
    }
}

void eu4_gfx_set_render_target(void *handle, void *target) {
    void *master = NULL;
    if (handle != NULL) {
        memcpy(&master, handle, sizeof master);
    }
    __unsafe_unretained EU4GfxBackend *backend =
        master != NULL ? (__bridge EU4GfxBackend *)load_ptr(master, 0x180) : nil;
    if (target != NULL) {
        dump_render_target(master, target);
        if (backend == nil) {
            eu4_gfx_stop("_GfxSetRenderTarget", "unsupported", __builtin_return_address(0));
            return;
        }
        id<MTLTexture> color = backend.textures[[NSValue valueWithPointer:target]];
        id<MTLTexture> depth = backend.renderTargetDepths[[NSValue valueWithPointer:target]];
        if (color == nil) {
            char body[256];
            int n = snprintf(body, sizeof body,
                             "{\"schema\":\"gfx-render-target-missing/v1\","
                             "\"target\":\"%p\",\"pid\":%d}\n",
                             target, (int)getpid());
            if (n > 0 && (size_t)n < sizeof body) {
                (void)eu4_metal_events_write("render-target-missing.json", body);
            }
            eu4_gfx_stop("_GfxSetRenderTarget", "unsupported", __builtin_return_address(0));
            return;
        }
        uint32_t width = 0, height = 0;
        memcpy(&width, (uint8_t *)target + 0x28, 4);
        memcpy(&height, (uint8_t *)target + 0x2c, 4);
        backend.currentTargetHeader = target;
        backend.currentTargetColor = color;
        backend.lastBoundRTColor = color;
        backend.lastBoundRTWidth = width;
        backend.lastBoundRTHeight = height;
        backend.currentTargetDepth = depth;
        backend.currentTargetWidth = width;
        backend.currentTargetHeight = height;
        if (master != NULL) {
            /* The engine stores the viewport height at +0xac and the width at
               +0xb0 (see _GfxSetRenderTarget's default path). */
            uint8_t *bytes = (uint8_t *)master;
            memcpy(bytes + 0xac, &height, 4);
            memcpy(bytes + 0xb0, &width, 4);
            memset(bytes + 0xbc, 0, 4);
            memcpy(bytes + 0x78, &target, sizeof target);
        }
        return;
    }
    if (backend != nil && backend.currentTargetColor != nil &&
        getenv("EU4_GFX_DUMP_TARGETS") != NULL && backend.queue != nil) {
        id<MTLTexture> color = backend.currentTargetColor;
        NSUInteger row = (NSUInteger)color.width * 4;
        id<MTLBuffer> buffer = [backend.device newBufferWithLength:row * color.height
                                                           options:MTLResourceStorageModeShared];
        if (buffer != nil) {
            id<MTLCommandBuffer> command = [backend.queue commandBuffer];
            id<MTLBlitCommandEncoder> blit = [command blitCommandEncoder];
            if (blit != nil) {
                [blit copyFromTexture:color
                          sourceSlice:0
                          sourceLevel:0
                         sourceOrigin:MTLOriginMake(0, 0, 0)
                           sourceSize:MTLSizeMake(color.width, color.height, 1)
                             toBuffer:buffer
                    destinationOffset:0
               destinationBytesPerRow:row
             destinationBytesPerImage:row * color.height];
                [blit endEncoding];
            }
            [command commit];
            [command waitUntilCompleted];
            const char *dir = getenv("EU4_GFX_DUMP_TARGETS");
            char path[512];
            snprintf(path, sizeof path, "%s/rt-%p.bgra", dir, backend.currentTargetHeader);
            uint8_t *flipped = (uint8_t *)malloc(row * color.height);
            if (flipped != NULL) {
                const uint8_t *src = (const uint8_t *)buffer.contents;
                for (NSUInteger y = 0; y < color.height; y++) {
                    memcpy(flipped + y * row,
                           src + (color.height - 1 - y) * row, row);
                }
                FILE *file = fopen(path, "wb");
                if (file != NULL) {
                    fwrite(flipped, 1, row * color.height, file);
                    fclose(file);
                }
                free(flipped);
            }
        }
    }
    if (backend != nil) {
        backend.currentTargetHeader = NULL;
        backend.currentTargetColor = nil;
        backend.currentTargetDepth = backend.gameDepthTarget;
        backend.currentTargetWidth = 0;
        backend.currentTargetHeight = 0;
    }
    if (backend != nil && backend.pendingGameCommand != nil) {
        /* Restoring the default target after a render-target pass: finish the
           queued work first so the default-viewport selection observes a
           stable state (the offline P5.1 same-generation rejection is not a
           valid refusal once real multi-target frames are executed). */
        eu4_gfx_clear_status drained = drain_pending_game(backend, NULL);
        if (drained != EU4_GFX_CLEAR_OK) {
            eu4_gfx_stop("_GfxSetRenderTarget", "failed", __builtin_return_address(0));
            return;
        }
    }
    eu4_gfx_clear_status status = eu4_gfx_set_render_target_checked(handle, target);
    if (status == EU4_GFX_CLEAR_GEOMETRY_STALE) {
        /* Phase-1 deferral: intentional skip, engine continues. */
        return;
    }
    if (status != EU4_GFX_CLEAR_OK) {
        eu4_gfx_stop("_GfxSetRenderTarget",
                     status == EU4_GFX_CLEAR_UNSUPPORTED ? "unsupported" : "failed",
                     __builtin_return_address(0));
    }
}

eu4_gfx_clear_status eu4_gfx_present_checked(void *master) {
    __unsafe_unretained EU4GfxBackend *backend =
        (__bridge EU4GfxBackend *)backend_raw_for_master(master);
    if (backend == nil || backend.poisoned || !backend.gameWindow) {
        return EU4_GFX_CLEAR_UNSUPPORTED;
    }
    uint64_t observed_epoch = 0;
    (void)read_physical_geometry(NULL, NULL, &observed_epoch, NULL);
    if (backend.pendingGameCommand != nil) {
        eu4_gfx_clear_status drained = drain_pending_game(backend, NULL);
        if (drained != EU4_GFX_CLEAR_OK) {
            return drained;
        }
    }
    (void)reconcile_game_geometry(backend, master);
    if (geometry_transition_in_progress() || backend.awaitingFreshClear ||
        (backend.gameContentsKnown &&
         backend.clearedGeneration != backend.geometryGeneration)) {
        /* Geometry changed since available contents: no drawable, no blit. */
        return EU4_GFX_CLEAR_GEOMETRY_STALE;
    }
    if (!backend.gameContentsKnown || !backend.readyForPresent) {
        return EU4_GFX_CLEAR_UNSUPPORTED;
    }
    if (g_checkpoint_before_present != NULL) {
        g_checkpoint_before_present(master, g_present_caller);
    }
    if (backend.gameColorTarget == nil || backend.queue == nil) {
        return EU4_GFX_CLEAR_UNSUPPORTED;
    }

    id<MTLTexture> destination = nil;
    id<CAMetalDrawable> drawable = nil;
    if (g_present_drawable != NULL) {
        destination = (__bridge id<MTLTexture>)g_present_drawable;
    } else if (g_fail_next_drawable) {
        g_fail_next_drawable = 0;
        return EU4_GFX_CLEAR_FAILED;
    } else {
        CAMetalLayer *layer = backend.layer;
        drawable = layer != nil ? [layer nextDrawable] : nil;
        if (drawable == nil) {
            return EU4_GFX_CLEAR_FAILED;
        }
        destination = drawable.texture;
    }
    /* Recheck the committed epoch, transition phase, and acquired drawable
       after acquisition. A resize must not leave a drawable of the previous
       generation blitted. */
    uint32_t committed_width = 0, committed_height = 0;
    uint64_t rechecked_epoch = 0;
    int rechecked_transition = 0;
    int have_committed = read_physical_geometry(&committed_width, &committed_height,
                                                &rechecked_epoch, &rechecked_transition);
    if (rechecked_transition) {
        /* Phase 1 arrived during acquisition; the layer is not committed. */
        (void)reconcile_game_geometry(backend, master);
        return EU4_GFX_CLEAR_GEOMETRY_STALE;
    }
    if (have_committed && rechecked_epoch != observed_epoch) {
        (void)reconcile_game_geometry(backend, master);
    }
    if (backend.awaitingFreshClear ||
        (backend.gameContentsKnown &&
         backend.clearedGeneration != backend.geometryGeneration)) {
        return EU4_GFX_CLEAR_GEOMETRY_STALE;
    }
    /* Frozen policy: the game backbuffer and the destination are both physical
       drawable pixels. The original Present only swaps, so present fails closed
       before submit unless the destination exactly matches the stable committed
       geometry, the effective backbuffer, and the backbuffer format. */
    if (destination == nil || destination.width != backend.gameWidth ||
        destination.height != backend.gameHeight ||
        destination.pixelFormat != MTLPixelFormatBGRA8Unorm) {
        return EU4_GFX_CLEAR_FAILED;
    }
    if (have_committed &&
        (destination.width != committed_width || destination.height != committed_height)) {
        return EU4_GFX_CLEAR_FAILED;
    }

    int want_readback = g_present_readback != NULL;
    id<MTLTexture> staging = nil;
    if (want_readback) {
        uint64_t need = (uint64_t)backend.gameWidth * (uint64_t)backend.gameHeight * 4u;
        if (need == 0 || need > g_present_readback_cap) {
            return EU4_GFX_CLEAR_FAILED;
        }
        MTLTextureDescriptor *desc =
            [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm
                                                               width:backend.gameWidth
                                                              height:backend.gameHeight
                                                           mipmapped:NO];
        desc.usage = MTLTextureUsageShaderRead | MTLTextureUsageShaderWrite;
        desc.storageMode = MTLStorageModeShared;
        if (!g_fail_present_staging_alloc) {
            staging = [backend.device newTextureWithDescriptor:desc];
        } else {
            g_fail_present_staging_alloc = 0;
        }
        if (staging == nil) {
            return EU4_GFX_CLEAR_FAILED;
        }
    }

    id<MTLCommandBuffer> command = [backend.queue commandBuffer];
    if (command == nil) {
        return EU4_GFX_CLEAR_FAILED;
    }
    id<MTLBlitCommandEncoder> blit = [command blitCommandEncoder];
    if (blit == nil) {
        return EU4_GFX_CLEAR_FAILED;
    }
    MTLOrigin origin = MTLOriginMake(0, 0, 0);
    MTLSize size = MTLSizeMake(backend.gameWidth, backend.gameHeight, 1);
    [blit copyFromTexture:backend.gameColorTarget
              sourceSlice:0
              sourceLevel:0
             sourceOrigin:origin
               sourceSize:size
                toTexture:destination
         destinationSlice:0
         destinationLevel:0
        destinationOrigin:origin];
    if (staging != nil) {
        [blit copyFromTexture:destination
                  sourceSlice:0
                  sourceLevel:0
                 sourceOrigin:origin
                   sourceSize:size
                    toTexture:staging
             destinationSlice:0
             destinationLevel:0
            destinationOrigin:origin];
    }
    [blit endEncoding];
    if (drawable != nil) {
        [command presentDrawable:drawable];
    }
    [command commit];
    [command waitUntilCompleted];
    if (command.status != MTLCommandBufferStatusCompleted || command.error != nil) {
        backend.poisoned = YES;
        backend.readyForPresent = NO;
        return EU4_GFX_CLEAR_FAILED;
    }
    if (staging != nil) {
        [staging getBytes:g_present_readback
              bytesPerRow:(NSUInteger)backend.gameWidth * 4u
               fromRegion:MTLRegionMake2D(0, 0, backend.gameWidth, backend.gameHeight)
              mipmapLevel:0];
        if (g_checkpoint_after_present != NULL) {
            /* The destination drawable's bytes are in the checkpoint-owned
               buffer; consume them before clearing the capture pointer. */
            g_checkpoint_after_present(master, g_present_caller);
        }
        g_present_readback = NULL;
        g_present_readback_cap = 0;
    }
    backend.readyForPresent = NO;
    return EU4_GFX_CLEAR_OK;
}

int eu4_gfx_present_diagnostic(void *master) {
    __unsafe_unretained EU4GfxBackend *backend =
        (__bridge EU4GfxBackend *)backend_raw_for_master(master);
    if (backend == nil || backend.poisoned || !backend.gameWindow ||
        backend.gameColorTarget == nil || backend.queue == nil) {
        return 0;
    }
    CAMetalLayer *layer = backend.layer;
    id<CAMetalDrawable> drawable = layer != nil ? [layer nextDrawable] : nil;
    if (drawable == nil) {
        return 0;
    }
    id<MTLTexture> destination = drawable.texture;
    if (destination.width != backend.gameWidth ||
        destination.height != backend.gameHeight ||
        destination.pixelFormat != MTLPixelFormatBGRA8Unorm) {
        return 0;
    }
    id<MTLCommandBuffer> command = [backend.queue commandBuffer];
    if (command == nil) {
        return 0;
    }
    id<MTLBlitCommandEncoder> blit = [command blitCommandEncoder];
    if (blit == nil) {
        return 0;
    }
    MTLOrigin origin = MTLOriginMake(0, 0, 0);
    MTLSize size = MTLSizeMake(backend.gameWidth, backend.gameHeight, 1);
    [blit copyFromTexture:backend.gameColorTarget
              sourceSlice:0
              sourceLevel:0
             sourceOrigin:origin
               sourceSize:size
                toTexture:destination
         destinationSlice:0
         destinationLevel:0
        destinationOrigin:origin];
    [blit endEncoding];
    [command presentDrawable:drawable];
    [command commit];
    [command waitUntilCompleted];
    return command.status == MTLCommandBufferStatusCompleted && command.error == nil ? 1 : 0;
}

int eu4_gfx_present(void *master) {
    g_present_caller = __builtin_return_address(0);
    eu4_gfx_clear_status status = eu4_gfx_present_checked(master);
    if (status == EU4_GFX_CLEAR_GEOMETRY_STALE) {
        /* Geometry transition: nothing presented, original present still
           reports success. */
        return 1;
    }
    if (status != EU4_GFX_CLEAR_OK) {
        eu4_gfx_stop("_GfxPresent", status == EU4_GFX_CLEAR_UNSUPPORTED ? "unsupported" : "failed",
                     __builtin_return_address(0));
    }
    char present[256];
    int wrote = snprintf(present, sizeof present,
                         "{\"symbol\":\"_GfxPresent\",\"pid\":%d,\"rva\":\"0x%llx\"}\n",
                         (int)getpid(),
                         (unsigned long long)((uintptr_t)__builtin_return_address(0)));
    if (wrote > 0 && (size_t)wrote < sizeof present) {
        (void)eu4_metal_events_write("present.json", present);
    }
    g_present_caller = NULL;
    /* A presented frame is a hard prefix boundary. The next encoded prefix
       starts from the next frame's clear, never from the previous frame. */
    eu4_gfx_logical_capture_reset();
    return 1;
}

int eu4_gfx_settings(int display, int windowed, int count, void *settings) {
    (void)display;
    (void)windowed;
    if (settings == NULL) {
        return 0;
    }
    id<MTLDevice> device = nil;
    if (!g_no_device) {
        device = MTLCreateSystemDefaultDevice();
    }
    uint8_t *array = NULL;
    memcpy(&array, (uint8_t *)settings + 8, sizeof array);
    if (array != NULL && count > 0) {
        uint32_t threshold = 2;
        for (int i = 0; i < count; i++) {
            uint8_t *record = array + (size_t)i * 12;
            uint32_t sample = 0;
            memcpy(&sample, record, sizeof sample);
            if (sample > threshold && threshold <= 0x3fffffffu) {
                threshold <<= 1;
            }
            int supported = device != nil && sample == threshold &&
                            [device supportsTextureSampleCount:sample];
            record[4] = supported ? 1 : 0;
        }
    }
    uint8_t one = 1;
    uint32_t anisotropy = 0;
    uint64_t span = 0x30000000300ULL;
    uint32_t constant = 0x400;
    memcpy((uint8_t *)settings + 0x10, &one, 1);
    memcpy((uint8_t *)settings + 0x14, &anisotropy, 4);
    memcpy((uint8_t *)settings + 0x18, &span, 8);
    memcpy((uint8_t *)settings + 0x20, &constant, 4);
    return 0;
}
