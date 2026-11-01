#import "../benchmark/eu4_gfx_init.h"
#import "../benchmark/eu4_metal_events.h"

#import <Metal/Metal.h>
#import <QuartzCore/CAMetalLayer.h>
#import <stdio.h>
#import <stdlib.h>
#import <string.h>
#import <unistd.h>

static void fail(const char *message) {
    fprintf(stderr, "%s\n", message);
    exit(1);
}

static uint32_t load_u32(const uint8_t *desc, int offset) {
    uint32_t value;
    memcpy(&value, desc + offset, sizeof value);
    return value;
}

static void test_writes(void) {
    uint8_t desc[EU4_GFX_DEPTH_DESC_SIZE];
    memset(desc, 0xa5, sizeof desc);
    eu4_gfx_write_default_depth_stencil(desc);
    if (desc[0] != 1 || load_u32(desc, 4) != 1 || load_u32(desc, 8) != 3 || desc[0xc] != 0) {
        fail("depth enable, write, or compare bytes");
    }
    if (load_u32(desc, 0x10) != 0xffffffffu || load_u32(desc, 0x14) != 0xffffffffu) {
        fail("stencil masks");
    }
    if (load_u32(desc, 0x18) != 0 || load_u32(desc, 0x1c) != 0 || load_u32(desc, 0x20) != 0 ||
        load_u32(desc, 0x24) != 7) {
        fail("stencil op or compare bytes");
    }
    const int gaps[] = {1, 2, 3, 0xd, 0xe, 0xf};
    for (int i = 0; i < 6; i++) {
        if (desc[gaps[i]] != 0xa5) {
            fail("unwritten description byte was changed");
        }
    }
}

static void test_view(const uint8_t *desc) {
    eu4_gfx_depth_view view;
    memset(&view, 0, sizeof view);
    if (eu4_gfx_default_depth_stencil_view(desc, &view) != 0) {
        fail("default description was refused");
    }
    if (view.depth_compare != (int)MTLCompareFunctionLessEqual || view.depth_write != 1) {
        fail("depth descriptor properties");
    }
    if (view.stencil_compare != (int)MTLCompareFunctionAlways ||
        view.stencil_fail != (int)MTLStencilOperationKeep ||
        view.stencil_depth_fail != (int)MTLStencilOperationKeep ||
        view.stencil_pass != (int)MTLStencilOperationKeep ||
        view.stencil_read_mask != 0xffffffffu || view.stencil_write_mask != 0xffffffffu) {
        fail("stencil descriptor properties");
    }
    uint8_t other[EU4_GFX_DEPTH_DESC_SIZE];
    memcpy(other, desc, sizeof other);
    other[0] = 0;
    if (eu4_gfx_default_depth_stencil_view(other, &view) == 0) {
        fail("disabled depth was accepted");
    }
    if (eu4_gfx_default_depth_stencil_view(NULL, &view) == 0) {
        fail("null description was accepted");
    }
}

static void test_state(const uint8_t *desc) {
    id<MTLDevice> device = MTLCreateSystemDefaultDevice();
    if (device == nil) {
        fail("no Metal device");
    }
    void *raw = eu4_gfx_default_depth_stencil((__bridge void *)device, desc);
    if (raw == NULL) {
        fail("default state was not created");
    }
    @autoreleasepool {
        id<MTLDepthStencilState> state = (__bridge_transfer id<MTLDepthStencilState>)raw;
        if (state.device != device) {
            fail("state device is not the borrowed device");
        }
    }
    uint8_t rejected[EU4_GFX_DEPTH_DESC_SIZE];
    memcpy(rejected, desc, sizeof rejected);
    rejected[0] = 0;
    if (eu4_gfx_default_depth_stencil((__bridge void *)device, rejected) != NULL) {
        fail("rejected description created a state");
    }
    if (eu4_gfx_default_depth_stencil(NULL, desc) != NULL) {
        fail("null device created a state");
    }
    if (eu4_gfx_default_depth_stencil((__bridge void *)device, NULL) != NULL) {
        fail("null description created a state");
    }
    id<MTLBuffer> buffer = [device newBufferWithLength:4 options:MTLResourceStorageModeShared];
    if (buffer == nil) {
        fail("device was not usable after the state was released");
    }
}

static int g_stopped;

static void on_stop(const char *reason) {
    (void)reason;
    g_stopped = 1;
}

static void *fake_window(uint32_t window_id) { return window_id == 7 ? (void *)0x11 : NULL; }

static void fake_size(void *window, int *width, int *height) {
    (void)window;
    *width = 800;
    *height = 600;
}

static void write_depth(void *dest) { eu4_gfx_write_default_depth_stencil((uint8_t *)dest); }

static void bad_blend(void *dest) { memset(dest, 0xff, 0x24); }

static uint32_t load_at(const void *base, int offset) {
    uint32_t value;
    memcpy(&value, (const uint8_t *)base + offset, 4);
    return value;
}

static void test_backend(void) {
    char dir[] = "/tmp/eu4-pr3-XXXXXX";
    if (mkdtemp(dir) == NULL) {
        fail("temp dir");
    }
    eu4_metal_events_set_dir(dir);
    eu4_gfx_set_terminate(on_stop);
    eu4_gfx_set_sdl(fake_window, fake_size);
    eu4_gfx_set_default_writers(eu4_gfx_write_default_blend, write_depth, eu4_gfx_write_default_raster);
    id<MTLDevice> device = MTLCreateSystemDefaultDevice();
    if (device == nil) {
        fail("no Metal device");
    }
    CAMetalLayer *layer = [CAMetalLayer layer];
    layer.device = device;
    eu4_gfx_set_layer((__bridge void *)layer);

    uint8_t records[16 * 12];
    memset(records, 0, sizeof records);
    for (int i = 0; i < 15; i++) {
        uint32_t sample = (uint32_t)(i + 2);
        memcpy(records + i * 12, &sample, 4);
    }
    records[15 * 12] = 0xab;
    uint8_t settings[0x28];
    memset(settings, 0x5a, sizeof settings);
    void *array = records;
    memcpy(settings + 8, &array, sizeof array);
    if (eu4_gfx_settings(1, 0, 15, settings) != 0) {
        fail("settings return");
    }
    uint32_t threshold = 2;
    for (int i = 0; i < 15; i++) {
        uint32_t sample = (uint32_t)(i + 2);
        if (sample > threshold && threshold <= 0x3fffffffu) {
            threshold <<= 1;
        }
        int want = sample == threshold && [device supportsTextureSampleCount:sample];
        if (records[i * 12 + 4] != (want ? 1 : 0) || load_at(records, i * 12) != sample) {
            fail("sample record flag or order");
        }
    }
    if (records[15 * 12] != 0xab || settings[0x10] != 1 || load_at(settings, 0x14) != 0 ||
        load_at(settings, 0x20) != 0x400) {
        fail("settings bounds or immediates");
    }
    uint64_t span = 0;
    memcpy(&span, settings + 0x18, 8);
    if (span != 0x30000000300ULL) {
        fail("settings span");
    }
    eu4_gfx_test_no_device(1);
    memset(records, 0xab, 15 * 12);
    for (int i = 0; i < 15; i++) {
        uint32_t sample = (uint32_t)(i + 2);
        memcpy(records + i * 12, &sample, 4);
    }
    if (eu4_gfx_settings(1, 0, 0, settings) != 0 || records[4] != 0xab) {
        fail("zero count wrote a record");
    }
    eu4_gfx_settings(1, 0, 15, settings);
    for (int i = 0; i < 15; i++) {
        if (records[i * 12 + 4] != 0) {
            fail("missing device marked a sample");
        }
    }
    eu4_gfx_test_no_device(0);

    uint8_t handle[0x20];
    memset(handle, 0, sizeof handle);
    uint32_t window_id = 7, source = 9;
    memcpy(handle, &window_id, 4);
    memcpy(handle + 0x18, &source, 4);
    void *slot = (void *)0x1;
    eu4_gfx_set_image(NULL, &slot);
    eu4_gfx_test_throw_on_alloc(1);
    uint8_t ok = 1;
    if (eu4_gfx_init(handle, &ok) != NULL || ok != 0 || slot != (void *)0x1) {
        fail("master allocation failure escaped or returned a block");
    }
    eu4_gfx_test_throw_on_alloc(3);
    int releases = eu4_gfx_backend_releases();
    ok = 1;
    void *partial = eu4_gfx_init(handle, &ok);
    if (ok != 0 || partial == NULL || *(void **)((uint8_t *)partial + 0x180) != NULL ||
        *(void **)((uint8_t *)partial + 0x80) != NULL || *(void **)((uint8_t *)partial + 0x88) != NULL) {
        fail("header failure left a live pointer");
    }
    if (eu4_gfx_backend_releases() != releases + 1) {
        fail("header failure did not release the backend once");
    }
    eu4_gfx_shutdown(partial);
    if (eu4_gfx_backend_releases() != releases + 1 || slot != NULL) {
        fail("partial shutdown released the backend again");
    }
    eu4_gfx_test_throw_on_alloc(0);

    eu4_gfx_set_default_writers(bad_blend, write_depth, eu4_gfx_write_default_raster);
    g_stopped = 0;
    ok = 1;
    void *rejected = eu4_gfx_init(handle, &ok);
    if (ok != 0 || g_stopped || rejected == NULL) {
        fail("unsupported description during init did not stay a failed init");
    }
    eu4_gfx_shutdown(rejected);
    eu4_gfx_set_default_writers(eu4_gfx_write_default_blend, write_depth, eu4_gfx_write_default_raster);

    ok = 0;
    void *master = eu4_gfx_init(handle, &ok);
    if (ok != 1 || master == NULL || *(void **)master != master || slot != master) {
        fail("init did not publish the master");
    }
    if (load_at(master, 0xa0) != 800 || load_at(master, 0xa4) != 600 || load_at(master, 0xa8) != 9) {
        fail("window size or source");
    }
    if (*(void **)((uint8_t *)master + 0x178) != (void *)0x11 ||
        *(void **)((uint8_t *)master + 0x70) != NULL || ((uint8_t *)master)[0x194] != 0 ||
        ((uint8_t *)master)[0x128] != 0 || ((uint8_t *)master)[0x170] != 0) {
        fail("window, shader, wireframe, or dirty byte");
    }
    uint8_t view[0x18];
    memcpy(view, (uint8_t *)master + 0xac, sizeof view);
    for (int i = 0; i < 0x18; i++) {
        if (view[i] != 0) {
            fail("viewport was not left zero");
        }
    }
    uint32_t expect_limit =
        ([device supportsFamily:MTLGPUFamilyApple3] || [device supportsFamily:MTLGPUFamilyMac2])
            ? 16384
            : 8192;
    if (![device supportsFamily:MTLGPUFamilyApple3] && ![device supportsFamily:MTLGPUFamilyMac2] &&
        [device supportsFamily:MTLGPUFamilyApple2]) {
        expect_limit = 8192;
    }
    if (load_at(master, 0x188) != expect_limit) {
        fail("max texture");
    }
    for (int i = 4; i < 11; i++) {
        if (((uint8_t *)master)[0x188 + i] != 0) {
            fail("capability span was not otherwise zero");
        }
    }
    EU4GfxBackend *backend = (__bridge EU4GfxBackend *)*(void **)((uint8_t *)master + 0x180);
    id<MTLDevice> owned = backend.device;
    id<MTLCommandQueue> queue = backend.queue;
    id<MTLDepthStencilState> depth = backend.depthState;
    id<MTLTexture> texture = backend.fallback;
    if (owned != device || queue == nil || depth == nil || texture == nil || texture.width != 4 ||
        texture.height != 4 || texture.pixelFormat != MTLPixelFormatBGRA8Unorm) {
        fail("backend Metal objects");
    }
    if ((texture.usage & MTLTextureUsageRenderTarget) != 0) {
        fail("fallback texture is a render target");
    }
    uint8_t pixels[64];
    [texture getBytes:pixels bytesPerRow:16 fromRegion:MTLRegionMake2D(0, 0, 4, 4) mipmapLevel:0];
    for (int i = 0; i < 64; i++) {
        if (pixels[i] != 0xff) {
            fail("fallback texels");
        }
    }
    void *blend = *(void **)((uint8_t *)master + 0x80);
    void *depth_header = *(void **)((uint8_t *)master + 0x88);
    void *raster = *(void **)((uint8_t *)master + 0x90);
    void *tex_header = *(void **)((uint8_t *)master + 0x98);
    if (blend != backend.blendHeader || depth_header != backend.depthHeader ||
        raster != backend.rasterHeader || tex_header != backend.textureHeader) {
        fail("master slots are not the backend headers");
    }
    if (load_at(tex_header, 4) != 0 || load_at(tex_header, 0x28) != 4 || load_at(tex_header, 0x30) != 0xb ||
        load_at(tex_header, 0x40) != 1) {
        fail("fallback header");
    }
    if (load_at(master, 0xc + 0xc) == load_at(blend, 0x24 + 0xc)) {
        fail("blend snapshot copied the description");
    }
    if (load_at(master, 0x30 + 4) != 0x203 || ((uint8_t *)master)[0x31] != 1) {
        fail("depth snapshot");
    }
    if (load_at(master, 0x54) != 0x405 || ((uint8_t *)master)[0x54 + 0xd] != 1) {
        fail("rasterizer snapshot");
    }

    uint8_t saved[0x24];
    memcpy(saved, (uint8_t *)master + 0xc, sizeof saved);
    uint8_t junk[0x48];
    memset(junk, 0, sizeof junk);
    g_stopped = 0;
    eu4_gfx_set_blend(master, junk);
    if (!g_stopped || memcmp((uint8_t *)master + 0xc, saved, sizeof saved) != 0) {
        fail("unsupported state after success did not stop");
    }
    char init_path[96];
    snprintf(init_path, sizeof init_path, "%s/init.json", dir);
    FILE *init_file = fopen(init_path, "r");
    char first[512];
    if (init_file == NULL || fgets(first, sizeof first, init_file) == NULL) {
        fail("init.json missing");
    }
    fclose(init_file);
    backend = nil;
    releases = eu4_gfx_backend_releases();
    ok = 1;
    if (eu4_gfx_init(handle, &ok) != master || ok != 0) {
        fail("reentry changed the master");
    }
    init_file = fopen(init_path, "r");
    char second[512];
    if (init_file == NULL || fgets(second, sizeof second, init_file) == NULL ||
        strcmp(first, second) != 0) {
        fail("reentry rewrote init.json");
    }
    fclose(init_file);
    if (eu4_gfx_backend_releases() != releases) {
        fail("reentry created a backend");
    }
    eu4_gfx_shutdown(master);
    if (eu4_gfx_backend_releases() != releases + 1 || slot != NULL) {
        fail("shutdown did not release the backend once");
    }
}

int main(void) {
    uint8_t desc[EU4_GFX_DEPTH_DESC_SIZE];
    memset(desc, 0xa5, sizeof desc);
    test_writes();
    eu4_gfx_write_default_depth_stencil(desc);
    test_view(desc);
    test_state(desc);
    test_backend();
    return 0;
}
