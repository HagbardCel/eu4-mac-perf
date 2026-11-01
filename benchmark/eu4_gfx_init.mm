#import "eu4_gfx_init.h"
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
static int g_phase;
static int g_succeeded;
static void *g_master;
static int g_releases;
static uint64_t g_init_thread;

static uint64_t current_thread(void) {
    uint64_t thread = 0;
    (void)pthread_threadid_np(pthread_self(), &thread);
    return thread;
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

int eu4_gfx_phase(void) { return g_phase; }

int eu4_gfx_backend_releases(void) { return g_releases; }

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

static void fill_depth_snap(uint8_t snap[0x24]) {
    uint32_t compare = 0x203, mask = 0xffffffffu, always = 0x207, keep = 0x1e00;
    memset(snap, 0, 0x24);
    snap[0] = 1;
    snap[1] = 1;
    memcpy(snap + 4, &compare, 4);
    memcpy(snap + 0xc, &mask, 4);
    memcpy(snap + 0x10, &mask, 4);
    memcpy(snap + 0x14, &always, 4);
    memcpy(snap + 0x18, &keep, 4);
    memcpy(snap + 0x1c, &keep, 4);
    memcpy(snap + 0x20, &keep, 4);
}

static void fill_raster_snap(uint8_t snap[0x10]) {
    uint32_t back = 0x405, fill = 0x1b02, winding = 0x900;
    memset(snap, 0, 0x10);
    memcpy(snap, &back, 4);
    memcpy(snap + 4, &fill, 4);
    memcpy(snap + 8, &winding, 4);
    snap[0xd] = 1;
}

static int apply_snap(void *master, int offset, const uint8_t *snap, int size, int force) {
    uint8_t *bytes = (uint8_t *)master;
    if (!force && memcmp(bytes + offset, snap, (size_t)size) == 0) {
        return 0;
    }
    memcpy(bytes + offset, snap, (size_t)size);
    return 0;
}

static int apply_blend(void *master, void *state, int force) {
    if (state == NULL || !blend_supported((uint8_t *)state + 0x24)) {
        return -1;
    }
    uint8_t snap[0x24];
    fill_blend_snap(snap);
    return apply_snap(master, 0xc, snap, 0x24, force);
}

static int apply_depth(void *master, void *state, int force) {
    eu4_gfx_depth_view ignored;
    if (state == NULL || eu4_gfx_default_depth_stencil_view((uint8_t *)state + 0x24, &ignored) != 0) {
        return -1;
    }
    uint8_t snap[0x24];
    fill_depth_snap(snap);
    return apply_snap(master, 0x30, snap, 0x24, force);
}

static int apply_raster(void *master, void *state, int force) {
    if (state == NULL || !raster_supported((uint8_t *)state + 0x10)) {
        return -1;
    }
    uint8_t snap[0x10];
    fill_raster_snap(snap);
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
        "\"queue\":\"0x%llx\",\"depth\":\"0x%llx\",\"texture\":\"0x%llx\",\"thread\":%llu}\n",
        (unsigned long long)(uintptr_t)master,
        (unsigned long long)(uintptr_t)(__bridge void *)backend.layer,
        (unsigned long long)(uintptr_t)(__bridge void *)backend.device,
        (unsigned long long)(uintptr_t)(__bridge void *)backend.queue,
        (unsigned long long)(uintptr_t)(__bridge void *)backend.depthState,
        (unsigned long long)(uintptr_t)(__bridge void *)backend.fallback,
        (unsigned long long)g_init_thread);
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

void eu4_gfx_shutdown(void *master) {
    if (master != NULL) {
        void *raw = load_ptr(master, 0x180);
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

static void set_state(void *handle, void *state, int slot, int (*apply)(void *, void *, int),
                       const char *symbol) {
    void *master = NULL;
    if (handle != NULL) {
        memcpy(&master, handle, sizeof master);
    }
    if (master == NULL) {
        return;
    }
    if (state == NULL) {
        state = load_ptr(master, slot);
    }
    if (apply(master, state, 0) != 0 && g_succeeded) {
        stop_now("after_init", symbol, "unsupported", __builtin_return_address(0));
    }
}

void eu4_gfx_set_blend(void *handle, void *state) {
    set_state(handle, state, 0x80, apply_blend, "_GfxSetBlendState");
}

void eu4_gfx_set_depth(void *handle, void *state) {
    set_state(handle, state, 0x88, apply_depth, "_GfxSetDepthStencilState");
}

void eu4_gfx_set_raster(void *handle, void *state) {
    set_state(handle, state, 0x90, apply_raster, "_GfxSetRasterizerState");
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
