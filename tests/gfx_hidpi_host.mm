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

static int g_stopped;
static int g_logical_width = 16;
static int g_logical_height = 16;
static void *g_master_handle;

static void on_stop(const char *reason) {
    (void)reason;
    g_stopped = 1;
}

static void *fake_window(uint32_t window_id) { return window_id == 7 ? (void *)0x11 : NULL; }

static void fake_size(void *window, int *width, int *height) {
    (void)window;
    *width = g_logical_width;
    *height = g_logical_height;
}

static void write_depth(void *dest) { eu4_gfx_write_default_depth_stencil((uint8_t *)dest); }

static EU4GfxBackend *backend_of(void *master) {
    (void)master;
    return (__bridge EU4GfxBackend *)eu4_gfx_backend_token();
}

static void expect_uniform_bgra(const uint8_t *bytes, size_t len, uint8_t b, uint8_t g, uint8_t r,
                                uint8_t a) {
    if (len % 4 != 0) {
        fail("readback length");
    }
    for (size_t i = 0; i + 3 < len; i += 4) {
        if (bytes[i] != b || bytes[i + 1] != g || bytes[i + 2] != r || bytes[i + 3] != a) {
            fail("pixel mismatch");
        }
    }
}

static id<MTLTexture> make_destination(id<MTLDevice> device, uint32_t width, uint32_t height) {
    MTLTextureDescriptor *desc =
        [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm
                                                           width:width
                                                          height:height
                                                       mipmapped:NO];
    desc.usage = MTLTextureUsageShaderRead | MTLTextureUsageShaderWrite |
                 MTLTextureUsageRenderTarget;
    desc.storageMode = MTLStorageModeShared;
    return [device newTextureWithDescriptor:desc];
}

static void expect_master_logical(void *master, uint32_t width, uint32_t height) {
    uint32_t got_width = 0, got_height = 0;
    memcpy(&got_width, (uint8_t *)master + 0xa0, sizeof got_width);
    memcpy(&got_height, (uint8_t *)master + 0xa4, sizeof got_height);
    if (got_width != width || got_height != height) {
        fail("master logical size changed");
    }
}

static void expect_viewport(void *master, uint32_t width, uint32_t height) {
    uint32_t got_width = 0, got_height = 0, x = 0, y = 0;
    memcpy(&got_height, (uint8_t *)master + 0xac, sizeof got_height);
    memcpy(&got_width, (uint8_t *)master + 0xb0, sizeof got_width);
    memcpy(&x, (uint8_t *)master + 0xbc, sizeof x);
    memcpy(&y, (uint8_t *)master + 0xc0, sizeof y);
    if (got_width != width || got_height != height || x != 0 || y != 0) {
        fail("logical viewport mismatch");
    }
}

static void expect_physical_backbuffer(EU4GfxBackend *backend, uint32_t width, uint32_t height) {
    if (backend == nil || backend.gameColorTarget == nil || backend.gameWidth != width ||
        backend.gameHeight != height) {
        fail("physical backbuffer mismatch");
    }
}

static void boot(void *layer_ptr, uint32_t logical_width, uint32_t logical_height,
                 uint32_t physical_width, uint32_t physical_height) {
    CAMetalLayer *layer = (__bridge CAMetalLayer *)layer_ptr;
    id<MTLDevice> device = MTLCreateSystemDefaultDevice();
    if (device == nil) {
        fail("no Metal device");
    }
    layer.device = device;
    layer.pixelFormat = MTLPixelFormatBGRA8Unorm;
    layer.framebufferOnly = NO;
    layer.drawableSize = CGSizeMake(physical_width, physical_height);
    eu4_gfx_publish_physical_geometry(physical_width, physical_height);
    char dir[] = "/tmp/eu4-p511-hidpi-XXXXXX";
    if (mkdtemp(dir) == NULL) {
        fail("temp dir");
    }
    eu4_metal_events_set_dir(dir);
    g_logical_width = (int)logical_width;
    g_logical_height = (int)logical_height;
    eu4_gfx_set_terminate(on_stop);
    eu4_gfx_set_sdl(fake_window, fake_size);
    eu4_gfx_set_default_writers(eu4_gfx_write_default_blend, write_depth, eu4_gfx_write_default_raster);
    eu4_gfx_set_layer(layer_ptr);
    eu4_gfx_enable_game_window();
    eu4_gfx_set_image(NULL, NULL);
    uint8_t handle_bytes[0x20];
    memset(handle_bytes, 0, sizeof handle_bytes);
    uint32_t window_id = 7;
    memcpy(handle_bytes, &window_id, sizeof window_id);
    uint8_t ok = 0;
    void *master = eu4_gfx_init(handle_bytes, &ok);
    if (ok != 1 || master == NULL) {
        fail("init");
    }
    g_master_handle = master;
    EU4GfxBackend *backend = backend_of(master);
    expect_physical_backbuffer(backend, physical_width, physical_height);
    expect_master_logical(master, logical_width, logical_height);
    if (backend.logicalWidth != logical_width || backend.logicalHeight != logical_height ||
        backend.physicalWidth != physical_width || backend.physicalHeight != physical_height ||
        eu4_gfx_geometry_generation(master) == 0) {
        fail("initial geometry publish");
    }
}

static void boot_layer(CAMetalLayer **out_layer, uint32_t logical_width, uint32_t logical_height,
                       uint32_t physical_width, uint32_t physical_height) {
    CAMetalLayer *layer = [CAMetalLayer layer];
    *out_layer = layer;
    boot((__bridge void *)layer, logical_width, logical_height, physical_width, physical_height);
}

static void publish_physical(CAMetalLayer *layer, uint32_t width, uint32_t height) {
    layer.drawableSize = CGSizeMake(width, height);
    eu4_gfx_publish_physical_geometry(width, height);
}

static void clear_game(void *master, float r, float g, float b) {
    float color[4] = {r, g, b, 1.f};
    g_stopped = 0;
    eu4_gfx_clear(master, 1, color);
    if (g_stopped) {
        fail("production clear stopped");
    }
}

static void complete_game(void *master) {
    if (eu4_gfx_complete_game(master, NULL) != EU4_GFX_CLEAR_OK) {
        fail("complete game");
    }
}

/* Production present through an injected destination of the physical size. */
static void present_injected(void *master, id<MTLDevice> device, uint32_t width, uint32_t height,
                             uint8_t b, uint8_t g, uint8_t r, uint8_t a) {
    id<MTLTexture> destination = make_destination(device, width, height);
    if (destination == nil) {
        fail("injected destination");
    }
    size_t len = (size_t)width * height * 4u;
    uint8_t *pixels = (uint8_t *)calloc(len, 1);
    if (pixels == NULL) {
        fail("present readback");
    }
    memset(pixels, 0x5a, len);
    eu4_gfx_test_set_present_drawable((__bridge void *)destination);
    eu4_gfx_test_set_present_readback(pixels, len);
    g_stopped = 0;
    if (eu4_gfx_present(master) != 1 || g_stopped) {
        fail("production present failed");
    }
    expect_uniform_bgra(pixels, len, b, g, r, a);
    eu4_gfx_test_set_present_drawable(NULL);
    eu4_gfx_test_set_present_readback(NULL, 0);
    free(pixels);
}

/* Production present that must skip as stale: returns 1 and writes no pixels,
   with no drawable acquired. */
static void present_stale(void *master, uint32_t width, uint32_t height) {
    size_t len = (size_t)width * height * 4u;
    uint8_t *pixels = (uint8_t *)malloc(len);
    if (pixels == NULL) {
        fail("stale readback");
    }
    memset(pixels, 0x5a, len);
    eu4_gfx_test_set_present_drawable(NULL);
    eu4_gfx_test_set_present_readback(pixels, len);
    g_stopped = 0;
    if (eu4_gfx_present(master) != 1 || g_stopped) {
        fail("stale present did not return normally");
    }
    for (size_t i = 0; i + 3 < len; i += 4) {
        if (pixels[i] != 0x5a || pixels[i + 1] != 0x5a || pixels[i + 2] != 0x5a ||
            pixels[i + 3] != 0x5a) {
            fail("stale present wrote pixels");
        }
    }
    eu4_gfx_test_set_present_readback(NULL, 0);
    free(pixels);
}

static void select_default_target(void *master) {
    if (eu4_gfx_set_render_target_checked(master, NULL) != EU4_GFX_CLEAR_OK) {
        fail("SetRT null");
    }
}

/* Booting at 2x keeps the engine logical size and presents 1:1 physical. */
static void run_two_x_boot(void) {
    CAMetalLayer *layer = nil;
    boot_layer(&layer, 16, 16, 32, 32);
    void *master = g_master_handle;
    EU4GfxBackend *backend = backend_of(master);
    id<MTLDevice> device = backend.device;
    select_default_target(master);
    expect_viewport(master, 16, 16);
    clear_game(master, 0.f, 0.f, 1.f);
    present_injected(master, device, 32, 32, 0xff, 0x00, 0x00, 0xff);
    expect_master_logical(master, 16, 16);
    eu4_gfx_shutdown(master);
    (void)layer;
}

/* Scale-only change: no engine resize, stale present, then a fresh physical
   backbuffer on the next clear. */
static void run_physical_scale_change(void) {
    CAMetalLayer *layer = nil;
    boot_layer(&layer, 16, 16, 16, 16);
    void *master = g_master_handle;
    EU4GfxBackend *backend = backend_of(master);
    id<MTLDevice> device = backend.device;
    select_default_target(master);
    expect_viewport(master, 16, 16);
    clear_game(master, 1.f, 0.f, 0.f);
    present_injected(master, device, 16, 16, 0x00, 0x00, 0xff, 0xff);

    publish_physical(layer, 32, 32);
    /* Publication alone never mutates the renderer or the engine master. */
    expect_master_logical(master, 16, 16);
    if (backend.physicalWidth != 16 || backend.physicalHeight != 16) {
        fail("publish mutated renderer geometry");
    }
    present_stale(master, 32, 32);
    expect_master_logical(master, 16, 16);
    if (backend.logicalWidth != 16 || backend.logicalHeight != 16 ||
        backend.physicalWidth != 32 || backend.physicalHeight != 32) {
        fail("present did not reconcile published geometry");
    }
    if (!backend.awaitingFreshClear || backend.readyForPresent) {
        fail("scale change did not mark stale");
    }

    clear_game(master, 0.f, 1.f, 0.f);
    expect_physical_backbuffer(backend, 32, 32);
    expect_master_logical(master, 16, 16);
    expect_viewport(master, 16, 16);
    present_injected(master, device, 32, 32, 0x00, 0xff, 0x00, 0xff);
    eu4_gfx_shutdown(master);
    (void)layer;
}

/* Changed and idle: the next clear reconciles and clears the new buffer. */
static void run_scale_change_clear_directly(void) {
    CAMetalLayer *layer = nil;
    boot_layer(&layer, 16, 16, 16, 16);
    void *master = g_master_handle;
    EU4GfxBackend *backend = backend_of(master);
    id<MTLDevice> device = backend.device;
    select_default_target(master);
    clear_game(master, 1.f, 0.f, 0.f);
    complete_game(master);

    publish_physical(layer, 32, 32);
    clear_game(master, 0.f, 1.f, 0.f);
    expect_physical_backbuffer(backend, 32, 32);
    expect_master_logical(master, 16, 16);
    expect_viewport(master, 16, 16);
    present_injected(master, device, 32, 32, 0x00, 0xff, 0x00, 0xff);
    eu4_gfx_shutdown(master);
    (void)layer;
}

/* Resize between clear and present: drain the old command, then go stale
   without acquiring a drawable. */
static void run_resize_between_clear_and_present(void) {
    CAMetalLayer *layer = nil;
    boot_layer(&layer, 16, 16, 16, 16);
    void *master = g_master_handle;
    EU4GfxBackend *backend = backend_of(master);
    id<MTLDevice> device = backend.device;
    select_default_target(master);
    clear_game(master, 1.f, 0.f, 0.f);
    publish_physical(layer, 32, 32);
    present_stale(master, 32, 32);
    if (backend.pendingGameCommand != nil) {
        fail("resize present did not drain pending");
    }
    if (!backend.gameContentsKnown || backend.readyForPresent) {
        fail("stale present advertised ready contents");
    }
    clear_game(master, 0.f, 1.f, 0.f);
    present_injected(master, device, 32, 32, 0x00, 0xff, 0x00, 0xff);
    eu4_gfx_shutdown(master);
    (void)layer;
}

/* Changed with a pending clear: the production clear is a stale no-op. */
static void run_pending_resize_then_clear_skips(void) {
    CAMetalLayer *layer = nil;
    boot_layer(&layer, 16, 16, 16, 16);
    void *master = g_master_handle;
    EU4GfxBackend *backend = backend_of(master);
    id<MTLDevice> device = backend.device;
    select_default_target(master);
    clear_game(master, 1.f, 0.f, 0.f);
    id pending = backend.pendingGameCommand;
    uint64_t pending_generation = backend.pendingGameGeneration;
    uint64_t generation = backend.geometryGeneration;
    if (pending == nil) {
        fail("clear did not leave a pending command");
    }
    publish_physical(layer, 32, 32);
    clear_game(master, 0.f, 1.f, 0.f);
    if (backend.pendingGameCommand != pending ||
        backend.pendingGameGeneration != pending_generation) {
        fail("stale clear submitted or replaced a command");
    }
    if (backend.geometryGeneration == generation || !backend.awaitingFreshClear) {
        fail("resize did not advance generation");
    }
    present_stale(master, 32, 32);
    if (backend.pendingGameCommand != nil) {
        fail("old pending command not drained");
    }
    if (!backend.gameContentsKnown || backend.clearedGeneration == backend.geometryGeneration) {
        fail("old-generation contents misclassified");
    }
    clear_game(master, 0.f, 1.f, 0.f);
    expect_physical_backbuffer(backend, 32, 32);
    present_injected(master, device, 32, 32, 0x00, 0xff, 0x00, 0xff);
    eu4_gfx_shutdown(master);
    (void)layer;
}

/* Without a transition, SetRT keeps the P5.1 pending rejection and writes
   nothing to the master. */
static void run_unrelated_setrt_while_pending_rejected(void) {
    CAMetalLayer *layer = nil;
    boot_layer(&layer, 16, 16, 16, 16);
    void *master = g_master_handle;
    EU4GfxBackend *backend = backend_of(master);
    id<MTLDevice> device = backend.device;
    select_default_target(master);
    clear_game(master, 1.f, 0.f, 0.f);
    id pending = backend.pendingGameCommand;
    uint8_t before[EU4_GFX_MASTER_SIZE];
    memcpy(before, master, sizeof before);
    if (eu4_gfx_set_render_target_checked(master, NULL) != EU4_GFX_CLEAR_UNSUPPORTED) {
        fail("SetRT while pending was not rejected");
    }
    if (backend.pendingGameCommand != pending || memcmp(master, before, sizeof before) != 0) {
        fail("rejected SetRT mutated state");
    }
    present_injected(master, device, 16, 16, 0x00, 0x00, 0xff, 0xff);
    eu4_gfx_shutdown(master);
    (void)layer;
}

/* Repeating present while awaiting a fresh clear stays stale. */
static void run_repeated_present_stays_stale(void) {
    CAMetalLayer *layer = nil;
    boot_layer(&layer, 16, 16, 16, 16);
    void *master = g_master_handle;
    EU4GfxBackend *backend = backend_of(master);
    select_default_target(master);
    clear_game(master, 1.f, 0.f, 0.f);
    complete_game(master);
    publish_physical(layer, 32, 32);
    present_stale(master, 32, 32);
    present_stale(master, 32, 32);
    if (eu4_gfx_present_checked(master) != EU4_GFX_CLEAR_GEOMETRY_STALE) {
        fail("checked present returned OK for a stale skip");
    }
    if (!backend.awaitingFreshClear || backend.readyForPresent || !backend.gameContentsKnown) {
        fail("repeated stale present changed state");
    }
    eu4_gfx_shutdown(master);
    (void)layer;
}

/* SetRT(null) may drain on a transition and must allocate before mutating. */
static void run_set_render_target_drains_pending_resize(void) {
    CAMetalLayer *layer = nil;
    boot_layer(&layer, 16, 16, 16, 16);
    void *master = g_master_handle;
    EU4GfxBackend *backend = backend_of(master);
    id<MTLDevice> device = backend.device;
    select_default_target(master);
    clear_game(master, 1.f, 0.f, 0.f);
    id old_target = backend.gameColorTarget;
    if (backend.pendingGameCommand == nil) {
        fail("expected pending clear");
    }
    publish_physical(layer, 32, 32);
    g_stopped = 0;
    eu4_gfx_set_render_target(master, NULL);
    if (g_stopped) {
        fail("production SetRT stopped");
    }
    expect_physical_backbuffer(backend, 32, 32);
    if (backend.gameColorTarget == old_target || backend.pendingGameCommand != nil) {
        fail("SetRT did not drain and replace");
    }
    expect_master_logical(master, 16, 16);
    expect_viewport(master, 16, 16);
    clear_game(master, 0.f, 0.f, 0.f);
    present_injected(master, device, 32, 32, 0x00, 0x00, 0x00, 0xff);
    eu4_gfx_shutdown(master);
    (void)layer;
}

/* A failed replacement allocation keeps the old texture and the master. */
static void run_set_render_target_alloc_failure(void) {
    CAMetalLayer *layer = nil;
    boot_layer(&layer, 16, 16, 16, 16);
    void *master = g_master_handle;
    EU4GfxBackend *backend = backend_of(master);
    id<MTLDevice> device = backend.device;
    select_default_target(master);
    clear_game(master, 1.f, 0.f, 0.f);
    id old_target = backend.gameColorTarget;
    publish_physical(layer, 32, 32);
    uint8_t before[EU4_GFX_MASTER_SIZE];
    memcpy(before, master, sizeof before);
    eu4_gfx_test_fail_game_alloc(1);
    if (eu4_gfx_set_render_target_checked(master, NULL) != EU4_GFX_CLEAR_FAILED) {
        fail("SetRT alloc failure not reported");
    }
    if (backend.gameColorTarget != old_target || backend.gameWidth != 16 ||
        memcmp(master, before, sizeof before) != 0) {
        fail("SetRT alloc failure did not keep old state");
    }
    /* The failed path left a drain behind; the next clear takes the new size. */
    clear_game(master, 0.f, 1.f, 0.f);
    expect_physical_backbuffer(backend, 32, 32);
    present_injected(master, device, 32, 32, 0x00, 0xff, 0x00, 0xff);
    eu4_gfx_shutdown(master);
    (void)layer;
}

/* A logical-only change advances the generation and refreshes the viewport
   without touching the physical backbuffer. */
static void run_logical_only_generation_bump(void) {
    CAMetalLayer *layer = nil;
    boot_layer(&layer, 16, 16, 16, 16);
    void *master = g_master_handle;
    EU4GfxBackend *backend = backend_of(master);
    id<MTLDevice> device = backend.device;
    select_default_target(master);
    clear_game(master, 1.f, 0.f, 0.f);
    complete_game(master);
    uint64_t before = eu4_gfx_geometry_generation(master);
    uint32_t logical_width = 20, logical_height = 18;
    memcpy((uint8_t *)master + 0xa0, &logical_width, sizeof logical_width);
    memcpy((uint8_t *)master + 0xa4, &logical_height, sizeof logical_height);
    select_default_target(master);
    uint64_t after = eu4_gfx_geometry_generation(master);
    if (after == before || after == 0) {
        fail("logical-only change did not advance generation");
    }
    expect_physical_backbuffer(backend, 16, 16);
    expect_viewport(master, 20, 18);
    present_stale(master, 16, 16);
    clear_game(master, 0.f, 1.f, 0.f);
    present_injected(master, device, 16, 16, 0x00, 0xff, 0x00, 0xff);
    expect_master_logical(master, 20, 18);
    eu4_gfx_shutdown(master);
    (void)layer;
}

/* Returning to a seen tuple recovers its generation: 1x -> 2x -> 1x. */
static void run_generation_identity(void) {
    CAMetalLayer *layer = nil;
    boot_layer(&layer, 16, 16, 16, 16);
    void *master = g_master_handle;
    uint64_t first = eu4_gfx_geometry_generation(master);
    publish_physical(layer, 32, 32);
    present_stale(master, 32, 32);
    uint64_t second = eu4_gfx_geometry_generation(master);
    if (first == 0 || second == first) {
        fail("2x generation did not change");
    }
    publish_physical(layer, 16, 16);
    present_stale(master, 16, 16);
    uint64_t third = eu4_gfx_geometry_generation(master);
    if (third != first) {
        fail("1x -> 2x -> 1x generation identity");
    }
    eu4_gfx_shutdown(master);
    (void)layer;
}

/* With current geometry, a missing drawable is a failure, not a stale skip. */
static void run_missing_drawable_is_failure(void) {
    CAMetalLayer *layer = nil;
    boot_layer(&layer, 16, 16, 16, 16);
    void *master = g_master_handle;
    EU4GfxBackend *backend = backend_of(master);
    select_default_target(master);
    clear_game(master, 1.f, 0.f, 0.f);
    complete_game(master);
    uint64_t generation = backend.geometryGeneration;
    eu4_gfx_test_set_present_drawable(NULL);
    eu4_gfx_test_fail_next_drawable(1);
    if (eu4_gfx_present_checked(master) != EU4_GFX_CLEAR_FAILED) {
        fail("missing drawable did not fail");
    }
    if (!backend.gameContentsKnown || !backend.readyForPresent ||
        backend.geometryGeneration != generation) {
        fail("missing drawable changed geometry state");
    }
    eu4_gfx_shutdown(master);
    (void)layer;
}

static int real_drawable_unavailable(void) {
    CAMetalLayer *layer = [CAMetalLayer layer];
    id<MTLDevice> device = MTLCreateSystemDefaultDevice();
    if (device == nil) {
        return 1;
    }
    layer.device = device;
    layer.pixelFormat = MTLPixelFormatBGRA8Unorm;
    layer.framebufferOnly = NO;
    layer.drawableSize = CGSizeMake(16, 16);
    id<CAMetalDrawable> drawable = [layer nextDrawable];
    return drawable == nil ? 1 : 0;
}

/* Opt-in: acquire real CAMetalLayer drawables across a scale change. */
static void run_real_drawable(void) {
    CAMetalLayer *layer = nil;
    boot_layer(&layer, 16, 16, 16, 16);
    void *master = g_master_handle;
    EU4GfxBackend *backend = backend_of(master);
    select_default_target(master);
    clear_game(master, 1.f, 0.f, 0.f);

    uint8_t *pixels = (uint8_t *)malloc(32 * 32 * 4);
    if (pixels == NULL) {
        fail("real readback");
    }
    memset(pixels, 0x5a, 16 * 16 * 4);
    eu4_gfx_test_set_present_drawable(NULL);
    eu4_gfx_test_set_present_readback(pixels, 16 * 16 * 4);
    g_stopped = 0;
    if (eu4_gfx_present(master) != 1 || g_stopped) {
        fail("real drawable present before update");
    }
    expect_uniform_bgra(pixels, 16 * 16 * 4, 0x00, 0x00, 0xff, 0xff);
    eu4_gfx_test_set_present_readback(NULL, 0);

    publish_physical(layer, 32, 32);
    present_stale(master, 32, 32);
    clear_game(master, 0.f, 1.f, 0.f);
    memset(pixels, 0x5a, 32 * 32 * 4);
    eu4_gfx_test_set_present_readback(pixels, 32 * 32 * 4);
    g_stopped = 0;
    if (eu4_gfx_present(master) != 1 || g_stopped) {
        fail("real drawable present after update");
    }
    expect_uniform_bgra(pixels, 32 * 32 * 4, 0x00, 0xff, 0x00, 0xff);
    eu4_gfx_test_set_present_readback(NULL, 0);
    free(pixels);
    eu4_gfx_shutdown(master);
    (void)backend;
    (void)layer;
}

/* Separate process mode: an ordinary pending second clear is fatal (exit 2),
   unlike a geometry-transition stale skip. */
static int run_pending_double_clear_fatal(void) {
    CAMetalLayer *layer = nil;
    boot_layer(&layer, 16, 16, 16, 16);
    void *master = g_master_handle;
    select_default_target(master);
    clear_game(master, 1.f, 0.f, 0.f);
    float green[4] = {0.f, 1.f, 0.f, 1.f};
    eu4_gfx_clear(master, 1, green);
    fprintf(stderr, "second clear was not fatal\n");
    eu4_gfx_shutdown(master);
    return 1;
}

int main(int argc, char **argv) {
    if (argc > 1 && strcmp(argv[1], "real") == 0) {
        if (real_drawable_unavailable()) {
            fprintf(stderr, "unavailable: CAMetalLayer.nextDrawable returned nil\n");
            return 77;
        }
        run_real_drawable();
        return 0;
    }
    if (argc > 1 && strcmp(argv[1], "pending-double-clear") == 0) {
        return run_pending_double_clear_fatal();
    }
    run_two_x_boot();
    run_physical_scale_change();
    run_scale_change_clear_directly();
    run_resize_between_clear_and_present();
    run_pending_resize_then_clear_skips();
    run_unrelated_setrt_while_pending_rejected();
    run_repeated_present_stays_stale();
    run_set_render_target_drains_pending_resize();
    run_set_render_target_alloc_failure();
    run_logical_only_generation_bump();
    run_generation_identity();
    run_missing_drawable_is_failure();
    return 0;
}
