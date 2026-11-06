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

static void on_stop(const char *reason) {
    (void)reason;
    g_stopped = 1;
}

static void *fake_window(uint32_t window_id) { return window_id == 7 ? (void *)0x11 : NULL; }

static void fake_size(void *window, int *width, int *height) {
    (void)window;
    *width = 16;
    *height = 16;
}

static void write_depth(void *dest) { eu4_gfx_write_default_depth_stencil((uint8_t *)dest); }

static void *load_rt(void *master) {
    void *value = NULL;
    memcpy(&value, (uint8_t *)master + 0x78, sizeof value);
    return value;
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

static id<MTLTexture> make_drawable_format(id<MTLDevice> device, uint32_t width, uint32_t height,
                                          MTLPixelFormat format) {
    MTLTextureDescriptor *desc =
        [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:format
                                                           width:width
                                                          height:height
                                                       mipmapped:NO];
    desc.usage = MTLTextureUsageShaderRead | MTLTextureUsageShaderWrite | MTLTextureUsageRenderTarget;
    desc.storageMode = MTLStorageModeShared;
    return [device newTextureWithDescriptor:desc];
}

static id<MTLTexture> make_drawable(id<MTLDevice> device, uint32_t width, uint32_t height) {
    return make_drawable_format(device, width, height, MTLPixelFormatBGRA8Unorm);
}

static void *boot(CAMetalLayer *layer) {
    char dir[] = "/tmp/eu4-p51-XXXXXX";
    if (mkdtemp(dir) == NULL) {
        fail("temp dir");
    }
    eu4_metal_events_set_dir(dir);
    eu4_gfx_set_terminate(on_stop);
    eu4_gfx_set_sdl(fake_window, fake_size);
    eu4_gfx_set_default_writers(eu4_gfx_write_default_blend, write_depth, eu4_gfx_write_default_raster);
    eu4_gfx_set_layer((__bridge void *)layer);
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
    return master;
}

static void test_lifecycle(void) {
    id<MTLDevice> device = MTLCreateSystemDefaultDevice();
    if (device == nil) {
        fail("no Metal device");
    }
    CAMetalLayer *layer = [CAMetalLayer layer];
    layer.device = device;
    layer.framebufferOnly = NO;
    layer.pixelFormat = MTLPixelFormatBGRA8Unorm;
    void *master = boot(layer);
    EU4GfxBackend *backend = (__bridge EU4GfxBackend *)eu4_gfx_backend_token();
    if (backend == nil || !backend.gameWindow || backend.gameColorTarget == nil ||
        backend.gameWidth != 16 || backend.gameHeight != 16) {
        fail("game backbuffer missing after init");
    }
    void *handle = master;
    uint8_t before[EU4_GFX_MASTER_SIZE];
    memcpy(before, master, sizeof before);
    if (eu4_gfx_set_render_target_checked(&handle, (void *)0x1) != EU4_GFX_CLEAR_UNSUPPORTED ||
        memcmp(master, before, sizeof before) != 0) {
        fail("non-null SetRT mutated master");
    }
    if (eu4_gfx_set_render_target_checked(&handle, NULL) != EU4_GFX_CLEAR_OK) {
        fail("SetRT null");
    }
    eu4_gfx_set_render_target(&handle, NULL);
    if (load_rt(master) != NULL) {
        fail("SetRT did not clear +0x78");
    }
    uint32_t view_h = 0, view_w = 0, view_x = 0, view_y = 0;
    memcpy(&view_h, (uint8_t *)master + 0xac, 4);
    memcpy(&view_w, (uint8_t *)master + 0xb0, 4);
    memcpy(&view_x, (uint8_t *)master + 0xbc, 4);
    memcpy(&view_y, (uint8_t *)master + 0xc0, 4);
    if (view_h != 16 || view_w != 16 || view_x != 0 || view_y != 0) {
        fail("SetRT viewport");
    }

    id<MTLTexture> dest = make_drawable(device, 16, 16);
    if (dest == nil) {
        fail("inject drawable");
    }
    float red[4] = {1.f, 0.f, 0.f, 1.f};
    g_stopped = 0;
    eu4_gfx_clear(&handle, 1, red);
    if (g_stopped) {
        fail("game clear stopped");
    }
    if (eu4_gfx_game_contents_known(master) || eu4_gfx_ready_for_present(master)) {
        fail("known/ready set before complete");
    }
    uint8_t presented[16 * 16 * 4];
    memset(presented, 0x5a, sizeof presented);
    eu4_gfx_test_set_present_drawable((__bridge void *)dest);
    eu4_gfx_test_set_present_readback(presented, sizeof presented);
    if (eu4_gfx_present_checked(master) != EU4_GFX_CLEAR_OK) {
        fail("pending present drain");
    }
    expect_uniform_bgra(presented, sizeof presented, 0x00, 0x00, 0xff, 0xff);
    if (!eu4_gfx_game_contents_known(master) || eu4_gfx_ready_for_present(master)) {
        fail("present should clear ready only");
    }
    if (eu4_gfx_present_checked(master) != EU4_GFX_CLEAR_UNSUPPORTED) {
        fail("repeat present accepted");
    }

    if (eu4_gfx_configure_offscreen(master, 8, 8) != EU4_GFX_CLEAR_OK) {
        fail("configure diagnostic");
    }
    float black[4] = {0.f, 0.f, 0.f, 1.f};
    eu4_gfx_clear(&handle, 1, black);
    int status = 0;
    if (eu4_gfx_complete_game(master, &status) != EU4_GFX_CLEAR_OK) {
        fail("game complete after isolation clear");
    }
    id<MTLTexture> game_before = backend.gameColorTarget;
    int32_t view8[6] = {8, 8, 0, 0, 0, 0};
    eu4_gfx_set_viewport(master, view8);
    if (eu4_gfx_clear_checked(&handle, 1, red) != EU4_GFX_CLEAR_OK ||
        eu4_gfx_complete_offscreen(master, &status) != EU4_GFX_CLEAR_OK) {
        fail("diagnostic clear");
    }
    uint8_t diag[8 * 8 * 4];
    size_t diag_len = 0;
    if (eu4_gfx_read_offscreen(master, diag, sizeof diag, &diag_len) != EU4_GFX_CLEAR_OK) {
        fail("diagnostic read");
    }
    expect_uniform_bgra(diag, diag_len, 0x00, 0x00, 0xff, 0xff);
    if (backend.gameColorTarget != game_before || !eu4_gfx_game_contents_known(master) ||
        !eu4_gfx_ready_for_present(master)) {
        fail("diagnostic clear disturbed game target");
    }
    uint8_t game_bytes[16 * 16 * 4];
    [backend.gameColorTarget getBytes:game_bytes
                          bytesPerRow:16 * 4
                           fromRegion:MTLRegionMake2D(0, 0, 16, 16)
                          mipmapLevel:0];
    expect_uniform_bgra(game_bytes, sizeof game_bytes, 0x00, 0x00, 0x00, 0xff);

    if (eu4_gfx_set_render_target_checked(&handle, NULL) != EU4_GFX_CLEAR_OK) {
        fail("SetRT restore");
    }
    eu4_gfx_test_set_present_drawable((__bridge void *)dest);
    eu4_gfx_test_set_present_readback(presented, sizeof presented);
    if (eu4_gfx_present_checked(master) != EU4_GFX_CLEAR_OK) {
        fail("present black after isolation");
    }
    expect_uniform_bgra(presented, sizeof presented, 0x00, 0x00, 0x00, 0xff);

    eu4_gfx_clear(&handle, 1, red);
    if (eu4_gfx_complete_game(master, NULL) != EU4_GFX_CLEAR_OK) {
        fail("clear for production present");
    }
    eu4_gfx_test_set_present_drawable((__bridge void *)dest);
    eu4_gfx_test_set_present_readback(presented, sizeof presented);
    if (eu4_gfx_present(master) != 1) {
        fail("production present");
    }
    expect_uniform_bgra(presented, sizeof presented, 0x00, 0x00, 0xff, 0xff);

    eu4_gfx_clear(&handle, 1, red);
    if (eu4_gfx_complete_game(master, NULL) != EU4_GFX_CLEAR_OK) {
        fail("clear before seams");
    }
    eu4_gfx_test_set_present_drawable(NULL);
    eu4_gfx_test_fail_next_drawable(1);
    if (eu4_gfx_present_checked(master) != EU4_GFX_CLEAR_FAILED ||
        !eu4_gfx_ready_for_present(master) || !eu4_gfx_game_contents_known(master)) {
        fail("nil drawable seam");
    }
    eu4_gfx_test_set_present_drawable((__bridge void *)dest);
    eu4_gfx_test_set_present_readback(presented, sizeof presented);
    eu4_gfx_test_fail_present_staging_alloc(1);
    if (eu4_gfx_present_checked(master) != EU4_GFX_CLEAR_FAILED ||
        !eu4_gfx_ready_for_present(master)) {
        fail("staging alloc seam");
    }
    eu4_gfx_test_set_present_readback(presented, sizeof presented);
    if (eu4_gfx_present_checked(master) != EU4_GFX_CLEAR_OK) {
        fail("present after seams");
    }
    expect_uniform_bgra(presented, sizeof presented, 0x00, 0x00, 0xff, 0xff);

    eu4_gfx_clear(&handle, 1, red);
    if (eu4_gfx_complete_game(master, NULL) != EU4_GFX_CLEAR_OK) {
        fail("clear before destination failures");
    }
    id<MTLTexture> unequal_destination = make_drawable(device, 32, 16);
    id<MTLTexture> oversize_destination = make_drawable(device, 32, 32);
    id<MTLTexture> bad_format_destination =
        make_drawable_format(device, 16, 16, MTLPixelFormatRGBA8Unorm);
    if (unequal_destination == nil || oversize_destination == nil ||
        bad_format_destination == nil) {
        fail("destination failure textures");
    }
    id<MTLTexture> rejected[3] = {unequal_destination, oversize_destination,
                                  bad_format_destination};
    for (int i = 0; i < 3; i++) {
        eu4_gfx_test_set_present_drawable((__bridge void *)rejected[i]);
        if (eu4_gfx_present_checked(master) != EU4_GFX_CLEAR_FAILED ||
            !eu4_gfx_game_contents_known(master) || !eu4_gfx_ready_for_present(master)) {
            fail("destination size/format gate");
        }
    }
    eu4_gfx_test_set_present_drawable((__bridge void *)dest);
    eu4_gfx_test_set_present_readback(presented, sizeof presented);
    if (eu4_gfx_present_checked(master) != EU4_GFX_CLEAR_OK) {
        fail("present after destination failures");
    }
    expect_uniform_bgra(presented, sizeof presented, 0x00, 0x00, 0xff, 0xff);

    eu4_gfx_clear(&handle, 1, black);
    backend = nil;
    dest = nil;
    eu4_gfx_shutdown(master);
    if (eu4_gfx_backend_token() != NULL) {
        fail("shutdown left backend");
    }
}

static void test_alloc_fail_rolls_back(void) {
    id<MTLDevice> device = MTLCreateSystemDefaultDevice();
    if (device == nil) {
        fail("no Metal device");
    }
    CAMetalLayer *layer = [CAMetalLayer layer];
    layer.device = device;
    char dir[] = "/tmp/eu4-p51-XXXXXX";
    if (mkdtemp(dir) == NULL) {
        fail("temp dir");
    }
    eu4_metal_events_set_dir(dir);
    eu4_gfx_set_terminate(on_stop);
    eu4_gfx_set_sdl(fake_window, fake_size);
    eu4_gfx_set_default_writers(eu4_gfx_write_default_blend, write_depth, eu4_gfx_write_default_raster);
    eu4_gfx_set_layer((__bridge void *)layer);
    eu4_gfx_enable_game_window();
    eu4_gfx_set_image(NULL, NULL);
    eu4_gfx_test_fail_game_alloc(1);
    uint8_t handle_bytes[0x20];
    memset(handle_bytes, 0, sizeof handle_bytes);
    uint32_t window_id = 7;
    memcpy(handle_bytes, &window_id, sizeof window_id);
    uint8_t ok = 1;
    void *master = eu4_gfx_init(handle_bytes, &ok);
    if (ok != 0 || eu4_gfx_phase() != 0 || master == NULL) {
        fail("game alloc failure did not roll back init");
    }
    char path[96];
    snprintf(path, sizeof path, "%s/init.json", dir);
    if (access(path, F_OK) == 0) {
        fail("failed init wrote init.json");
    }
    eu4_gfx_shutdown(master);
}

int main(void) {
    test_lifecycle();
    test_alloc_fail_rolls_back();
    return 0;
}
