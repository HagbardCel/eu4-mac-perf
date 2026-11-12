/* Live backend route: _GfxDraw -> shared Simple encoder -> backend queue ->
   checkpoint readback. This host never launches EU4; it drives the same
   replacement entry points the game calls. */

#import "eu4_gfx_buffers.h"
#import "eu4_gfx_draw_capture.h"
#import "eu4_gfx_checkpoint.h"
#import "eu4_gfx_draw_route.h"
#import "eu4_gfx_init.h"
#import "eu4_gfx_shader.h"
#import "eu4_gfx_simple_draw.h"
#import "eu4_gfx_texture.h"
#import "eu4_metal_events.h"

#import <CommonCrypto/CommonDigest.h>
#import <Metal/Metal.h>
#import <QuartzCore/CAMetalLayer.h>
#import <stdio.h>
#import <stdlib.h>
#import <string.h>
#import <sys/mman.h>
#import <unistd.h>
#import <sys/stat.h>

enum {
    kImageBytes = 0x2300000,
    kSize = 64,
    kCallerRva = 0xedfcd1,
};

static uint8_t *g_image;
static void *g_slot;
static void *g_master;
static int g_stopped;

static void fail(const char *message) {
    fprintf(stderr, "FAIL: %s\n", message);
    exit(1);
}

static void on_stop(const char *reason) {
    (void)reason;
    g_stopped = 1;
}

static void *fake_window(uint32_t window_id) { return window_id == 7 ? (void *)0x11 : NULL; }

static void fake_size(void *window, int *width, int *height) {
    (void)window;
    *width = kSize;
    *height = kSize;
}

static void write_depth(void *dest) { eu4_gfx_write_default_depth_stencil((uint8_t *)dest); }

typedef struct {
    uint32_t level;
    uint32_t face;
    uint32_t width;
    uint32_t height;
    uint32_t bytes;
    uint32_t pad;
    const void *pixels;
} image_record;

static void boot(void) {
    g_image = (uint8_t *)mmap(NULL, kImageBytes, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON,
                              -1, 0);
    if (g_image == MAP_FAILED) {
        fail("image map");
    }
    eu4_gfx_set_image(g_image, &g_slot);
    eu4_metal_events_set_image((uintptr_t)g_image, (uintptr_t)g_image + kImageBytes);
    eu4_gfx_set_sdl(fake_window, fake_size);
    eu4_gfx_set_default_writers(eu4_gfx_write_default_blend, write_depth,
                                eu4_gfx_write_default_raster);
    eu4_gfx_set_terminate(on_stop);
    id<MTLDevice> device = MTLCreateSystemDefaultDevice();
    if (device == nil) {
        fail("no Metal device");
    }
    CAMetalLayer *layer = [CAMetalLayer layer];
    layer.device = device;
    layer.pixelFormat = MTLPixelFormatBGRA8Unorm;
    layer.drawableSize = CGSizeMake(kSize, kSize);
    eu4_gfx_set_layer((__bridge void *)layer);
    eu4_gfx_publish_physical_geometry(kSize, kSize);
    char dir[] = "/tmp/eu4-live-draw-XXXXXX";
    if (mkdtemp(dir) == NULL) {
        fail("event dir");
    }
    eu4_metal_events_set_dir(dir);
    eu4_gfx_enable_game_window();
    uint8_t handle[0x20];
    memset(handle, 0, sizeof handle);
    uint32_t window_id = 7, source = 9;
    memcpy(handle, &window_id, 4);
    memcpy(handle + 0x18, &source, 4);
    uint8_t ok = 0;
    g_master = eu4_gfx_init(handle, &ok);
    if (ok != 1 || g_master == NULL) {
        fail("init");
    }
    eu4_gfx_draw_route_install(eu4_gfx_draw_route_live);
}

static void register_source(const char *source, int stage) {
    uint8_t digest[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256(source, (CC_LONG)strlen(source), digest);
    if (!eu4_gfx_shader_register_qualified_sha256(
            stage, digest, EU4_GFX_SHADER_SIMPLE_IDENTITY)) {
        fail("qualified source registration");
    }
}

static void *make_simple_shader(void) {
    eu4_gfx_shader_test_reset();
    register_source(eu4_gfx_simple_vertex_source(), EU4_GFX_SHADER_STAGE_VERTEX);
    register_source(eu4_gfx_simple_pixel_source(), EU4_GFX_SHADER_STAGE_PIXEL);
    eu4_gfx_shader_set_allow_transitional_markers(0);
    void *shader = eu4_gfx_create_shader();
    if (shader == NULL) {
        fail("shader create");
    }
    if (eu4_gfx_add_vertex_shader(NULL, shader, eu4_gfx_simple_vertex_source()) != 1 ||
        eu4_gfx_add_pixel_shader(NULL, shader, eu4_gfx_simple_pixel_source()) != 1 ||
        eu4_gfx_link_shader(NULL, shader) != 1) {
        fail("Simple stage admission or link");
    }
    return shader;
}

static void make_texture(uint32_t width, uint32_t height, const uint8_t *bgr,
                         uint8_t header[0x44]) {
    memset(header, 0, 0x44);
    memset(header + 4, 0xff, 4);
    uint32_t desc[5] = {width, height, 1, 9, 0};
    image_record image = {0, 0, width, height, 0, 0, bgr};
    eu4_gfx_initialize_texture(NULL, header, desc, &image, 1);
    if (header[4] != 0) {
        fail("texture initialize did not clear the GL name");
    }
}

static void install_geometry(void *shader, void *vertex_header, void *constant,
                             const uint8_t *texture_header) {
    uint8_t *master = (uint8_t *)g_master;
    struct {
        void *master;
    } ctx = {g_master};
    int32_t mode = 5, count = 4;
    memcpy(master + EU4_GFX_MASTER_MODE, &mode, 4);
    memcpy(master + EU4_GFX_MASTER_COUNT, &count, 4);
    void *headers[1] = {vertex_header};
    eu4_gfx_set_vertex_buffers(&ctx, 1, headers, 0, NULL, NULL);
    eu4_gfx_set_shader(&ctx, shader);
    void *cb = constant;
    memcpy(master + EU4_GFX_MASTER_CONSTANT_SLOT2, &cb, sizeof cb);
    uint32_t rect[6] = {kSize, kSize, 0x3f800000u, 0, 0, 0};
    eu4_gfx_set_viewport(master, rect);
    uint32_t mask = 0x01010101u;
    memcpy(master + 0x2c, &mask, sizeof mask);
    void *texture = (void *)texture_header;
    eu4_gfx_set_textures(&ctx, 1, &texture, 1, 0, NULL);
}

static void make_quad(uint8_t out[96]) {
    float *values = (float *)out;
    const float vertices[4][5] = {
        {-1.f, -1.f, 0.5f, 0.f, 0.f},
        {1.f, -1.f, 0.5f, 1.f, 0.f},
        {-1.f, 1.f, 0.5f, 0.f, 1.f},
        {1.f, 1.f, 0.5f, 1.f, 1.f},
    };
    memset(out, 0, 96);
    for (int i = 0; i < 4; i++) {
        memcpy(values + i * 6, vertices[i], 5 * sizeof(float));
        uint8_t *colour = out + i * 24 + 20;
        colour[0] = colour[1] = colour[2] = colour[3] = 0xff;
    }
}

static void fill_matrix(float out[16]) {
    memset(out, 0, 16 * sizeof(float));
    out[0] = 1.f;
    out[5] = 1.f;
    out[10] = 1.f;
    out[15] = 1.f;
}

static eu4_gfx_draw_raw draw_frame(void *ctx) {
    eu4_gfx_draw_raw raw;
    memset(&raw, 0, sizeof raw);
    raw.which = 1;
    raw.rdi = (uint64_t)(uintptr_t)ctx;
    raw.rsi = 0;
    raw.rdx = 0;
    raw.retaddr = (uint64_t)(uintptr_t)(g_image + kCallerRva);
    return raw;
}

static uint8_t *read_target(size_t *length) {
    size_t need = (size_t)kSize * kSize * 4;
    uint8_t *pixels = (uint8_t *)malloc(need);
    if (pixels == NULL) {
        fail("readback allocation");
    }
    size_t got = 0;
    uint32_t width = 0, height = 0, stride = 0;
    uint64_t generation = 0;
    int status = -1;
    if (eu4_gfx_read_game_target(g_master, pixels, need, &got, &width, &height, &stride,
                                 &generation, &status) != EU4_GFX_CLEAR_OK ||
        got != need || width != kSize || height != kSize || stride != kSize * 4 ||
        status != (int)MTLCommandBufferStatusCompleted) {
        fail("game target readback");
    }
    *length = need;
    return pixels;
}

static void expect_colour(const uint8_t *pixels, uint8_t b, uint8_t g, uint8_t r,
                          const char *what) {
    const uint8_t *pixel = pixels + ((size_t)(kSize / 2) * kSize + (kSize / 2)) * 4;
    if (abs((int)pixel[0] - (int)b) > 4 || abs((int)pixel[1] - (int)g) > 4 ||
        abs((int)pixel[2] - (int)r) > 4) {
        fprintf(stderr, "FAIL: %s got (%u,%u,%u) want (%u,%u,%u)\n", what, pixel[0], pixel[1],
                pixel[2], b, g, r);
        exit(1);
    }
}


static void setup_scene(void *shader, void *vertex_header, void *constant,
                        const uint8_t *texture_header) {
    install_geometry(shader, vertex_header, constant, texture_header);
}

static int run_clear_flags(int flags) {
    boot();
    struct {
        void *master;
    } ctx = {g_master};
    uint8_t *master = (uint8_t *)g_master;
    uint32_t rect[6] = {kSize, kSize, 0x3f800000u, 0, 0, 0};
    eu4_gfx_set_viewport(master, rect);
    uint32_t mask = 0x01010101u;
    memcpy(master + 0x2c, &mask, sizeof mask);
    const float black[4] = {0.f, 0.f, 0.f, 1.f};
    eu4_gfx_clear(&ctx, (unsigned)flags, black);
    int status = -1;
    if (eu4_gfx_complete_game(g_master, &status) != EU4_GFX_CLEAR_OK ||
        status != (int)MTLCommandBufferStatusCompleted) {
        fail("clear completion");
    }
    printf("clear_ok=%d\n", flags);
    return 0;
}

static int run_clear_refusal(const char *directory) {
    boot();
    struct {
        void *master;
    } ctx = {g_master};
    uint8_t *master = (uint8_t *)g_master;
    uint32_t rect[6] = {kSize, kSize, 0x3f800000u, 0, 0, 0};
    eu4_gfx_set_viewport(master, rect);
    uint32_t mask = 0x01010101u;
    memcpy(master + 0x2c, &mask, sizeof mask);
    int16_t scissor[4] = {0, 0, 8, 8};
    eu4_gfx_set_scissor_rectangle(&ctx, scissor);
    eu4_metal_events_set_dir(directory);
    const float black[4] = {0.f, 0.f, 0.f, 1.f};
    eu4_gfx_clear(&ctx, 7, black);
    fail("partial-scissor clear did not stop");
    return 1;
}

static int run_checkpoint_inner(const char *operation, const char *directory,
                                uint64_t selector, int expect_fire) {
    boot();
    eu4_metal_events_set_dir(directory);
    void *shader = make_simple_shader();
    uint8_t quad[96];
    make_quad(quad);
    void *vertex_header = eu4_gfx_create_vertex_buffer(NULL, quad, 4, 24, 0);
    if (vertex_header == NULL) {
        fail("vertex buffer");
    }
    uint8_t red_bgr[4 * 4 * 3];
    for (int i = 0; i < 4 * 4; i++) {
        red_bgr[i * 3 + 0] = 0;
        red_bgr[i * 3 + 1] = 0;
        red_bgr[i * 3 + 2] = 0xff;
    }
    uint8_t red_header[0x44];
    make_texture(4, 4, red_bgr, red_header);
    float matrix[16];
    fill_matrix(matrix);
    uint8_t constant[0x10];
    memset(constant, 0, sizeof constant);
    uint32_t words = 16;
    memcpy(constant, &words, 4);
    void *constant_data = matrix;
    memcpy(constant + 8, &constant_data, sizeof constant_data);
    setup_scene(shader, vertex_header, constant, red_header);

    struct {
        void *master;
    } ctx = {g_master};
    const float black[4] = {0.f, 0.f, 0.f, 1.f};
    eu4_gfx_checkpoint_configure(operation, selector, 1, directory, 1);
    if (strcmp(operation, "after-clear") == 0) {
        eu4_gfx_clear(&ctx, 1, black);
        fail("after-clear checkpoint did not stop");
    }
    eu4_gfx_clear(&ctx, 1, black);
    eu4_gfx_draw_raw raw = draw_frame(&ctx);
    if (strcmp(operation, "after-draw") == 0) {
        if (eu4_gfx_draw_capture(&raw) != 1) {
            fail("checkpoint draw was not handled");
        }
        if (expect_fire) {
            fail("after-draw checkpoint did not stop");
        }
        printf("checkpoint_miss=1\n");
        return 0;
    }
    if (strcmp(operation, "before-present") == 0) {
        if (eu4_gfx_draw_capture(&raw) != 1) {
            fail("pre-present draw was not handled");
        }
        eu4_gfx_present(g_master);
        fail("before-present checkpoint did not stop");
    }
    fail("unknown checkpoint operation");
    return 2;
}

static int run_checkpoint(const char *operation, const char *directory) {
    uint64_t selector = strcmp(operation, "after-draw") == 0 ? kCallerRva : 0;
    return run_checkpoint_inner(operation, directory, selector, 1);
}

static int run_checkpoint_env(void) {
    int enabled = eu4_gfx_checkpoint_configure_from_env();
    printf("enabled=%d occurrence=%d\n", enabled,
           eu4_gfx_checkpoint_test_occurrence());
    return 0;
}

int main(int argc, char **argv) {
    if (argc >= 2 && strcmp(argv[1], "--checkpoint-env") == 0) {
        return run_checkpoint_env();
    }
    if (argc >= 4 && strcmp(argv[1], "--checkpoint") == 0) {
        return run_checkpoint(argv[2], argv[3]);
    }
    if (argc >= 3 && strcmp(argv[1], "--checkpoint-any") == 0) {
        return run_checkpoint_inner("after-draw", argv[2], 0, 1);
    }
    if (argc >= 3 && strcmp(argv[1], "--checkpoint-miss") == 0) {
        return run_checkpoint_inner("after-draw", argv[2], 0x123456, 0);
    }
    if (argc >= 3 && strcmp(argv[1], "--clear-refusal") == 0) {
        return run_clear_refusal(argv[2]);
    }
    if (argc >= 3 && strcmp(argv[1], "--clear-flags") == 0) {
        return run_clear_flags(atoi(argv[2]));
    }
    boot();
    uint8_t *master = (uint8_t *)g_master;
    EU4GfxBackend *backend = (__bridge EU4GfxBackend *)eu4_gfx_backend_token();
    if (backend == nil || backend.gameColorTarget == nil) {
        fail("game backend");
    }
    void *shader = make_simple_shader();
    uint8_t quad[96];
    make_quad(quad);
    void *vertex_header = eu4_gfx_create_vertex_buffer(NULL, quad, 4, 24, 0);
    if (vertex_header == NULL) {
        fail("vertex buffer");
    }
    uint8_t red_bgr[4 * 4 * 3];
    uint8_t green_bgr[4 * 4 * 3];
    for (int i = 0; i < 4 * 4; i++) {
        red_bgr[i * 3 + 0] = 0;
        red_bgr[i * 3 + 1] = 0;
        red_bgr[i * 3 + 2] = 0xff;
        green_bgr[i * 3 + 0] = 0;
        green_bgr[i * 3 + 1] = 0xff;
        green_bgr[i * 3 + 2] = 0;
    }
    uint8_t red_header[0x44], green_header[0x44];
    make_texture(4, 4, red_bgr, red_header);
    make_texture(4, 4, green_bgr, green_header);
    float matrix[16];
    fill_matrix(matrix);
    uint8_t constant[0x10];
    memset(constant, 0, sizeof constant);
    uint32_t words = 16;
    memcpy(constant, &words, 4);
    void *constant_data = matrix;
    memcpy(constant + 8, &constant_data, sizeof constant_data);
    install_geometry(shader, vertex_header, constant, red_header);

    struct {
        void *master;
    } ctx = {g_master};
    const float black[4] = {0.f, 0.f, 0.f, 1.f};
    g_stopped = 0;
    eu4_gfx_clear(&ctx, 7, black);
    if (g_stopped) {
        fail("production clear stopped");
    }
    eu4_gfx_draw_raw raw = draw_frame(&ctx);
    if (eu4_gfx_draw_capture(&raw) != 1) {
        fail("live draw was not handled");
    }
    size_t length = 0;
    uint8_t *pixels = read_target(&length);
    expect_colour(pixels, 0, 0, 0xff, "clear -> draw");
    free(pixels);

    /* Two draws preserve order on one queue; the last bound texture wins. */
    void *green = green_header;
    eu4_gfx_set_textures(&ctx, 1, &green, 1, 0, NULL);
    if (eu4_gfx_draw_capture(&raw) != 1) {
        fail("second live draw was not handled");
    }
    pixels = read_target(&length);
    expect_colour(pixels, 0, 0xff, 0, "clear -> two draws");
    free(pixels);

    /* Present after the draw, into an injected physical drawable. */
    MTLTextureDescriptor *desc =
        [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm
                                                           width:kSize
                                                          height:kSize
                                                       mipmapped:NO];
    desc.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead |
                 MTLTextureUsageShaderWrite;
    desc.storageMode = MTLStorageModeShared;
    id<MTLTexture> destination = [backend.device newTextureWithDescriptor:desc];
    if (destination == nil) {
        fail("present drawable");
    }
    eu4_gfx_test_set_present_drawable((__bridge void *)destination);
    if (eu4_gfx_present(g_master) != 1 || g_stopped) {
        fail("present failed");
    }
    uint8_t presented[4];
    [destination getBytes:presented
              bytesPerRow:4
               fromRegion:MTLRegionMake2D(kSize / 2, kSize / 2, 1, 1)
              mipmapLevel:0];
    if (abs((int)presented[1] - 0xff) > 4 || presented[0] > 4 || presented[2] > 4) {
        fail("present did not publish the draw");
    }

    /* Geometry transition refuses a pending-generation draw. */
    eu4_gfx_publish_geometry_transition(kSize * 2, kSize * 2);
    char diagnostic[256];
    eu4_gfx_simple_draw_state state;
    if (eu4_gfx_draw_route_live(&raw, &state, diagnostic, sizeof diagnostic) !=
        EU4_GFX_DRAW_ROUTE_UNSUPPORTED) {
        fail("geometry-transition draw was admitted");
    }
    if (strstr(diagnostic, "transition") == NULL &&
        strstr(diagnostic, "older physical generation") == NULL) {
        fail("geometry diagnostic is not actionable");
    }
    eu4_gfx_publish_physical_geometry(kSize, kSize);

    /* Unsupported shader identity stops with a precise diagnostic. */
    void *other = eu4_gfx_create_shader();
    if (other == NULL ||
        eu4_gfx_add_vertex_shader(NULL, other, eu4_gfx_simple_vertex_source()) != 1 ||
        eu4_gfx_add_pixel_shader(NULL, other, eu4_gfx_simple_pixel_source()) != 1 ||
        eu4_gfx_link_shader(NULL, other) != 1 ||
        !eu4_gfx_shader_test_set_identity(other, "test/other-linked-effect")) {
        fail("other shader setup");
    }
    eu4_gfx_set_shader(&ctx, other);
    if (eu4_gfx_draw_route_live(&raw, &state, diagnostic, sizeof diagnostic) !=
        EU4_GFX_DRAW_ROUTE_FAILED) {
        fail("unsupported shader was admitted");
    }
    if (strstr(diagnostic, "shader") == NULL) {
        fail("shader diagnostic is not actionable");
    }
    eu4_gfx_set_shader(&ctx, shader);
    eu4_gfx_destroy_shader(other);

    /* Missing vertex resource also fails closed. */
    void *saved = NULL;
    memcpy(&saved, master + EU4_GFX_MASTER_VERTEX_HEADER, sizeof saved);
    memset(master + EU4_GFX_MASTER_VERTEX_HEADER, 0, sizeof saved);
    if (eu4_gfx_draw_route_live(&raw, &state, diagnostic, sizeof diagnostic) !=
        EU4_GFX_DRAW_ROUTE_FAILED) {
        fail("missing vertex resource was admitted");
    }
    if (strstr(diagnostic, "vertex") == NULL) {
        fail("vertex diagnostic is not actionable");
    }
    memcpy(master + EU4_GFX_MASTER_VERTEX_HEADER, &saved, sizeof saved);

    /* GPU command failure path: submit one more draw, then make the next
       drain report a failed command. The backend must clear ownership, mark
       the contents unknown, and poison itself rather than declare success. */
    eu4_gfx_set_shader(&ctx, shader);
    eu4_gfx_set_textures(&ctx, 1, &green, 1, 0, NULL);
    if (eu4_gfx_draw_capture(&raw) != 1) {
        fail("failure-path draw was not handled");
    }
    eu4_gfx_test_fail_next_game_drain(1);
    uint8_t *failed_pixels = (uint8_t *)malloc(length);
    size_t failed_got = 0;
    uint32_t failed_w = 0, failed_h = 0, failed_stride = 0;
    uint64_t failed_generation = 0;
    int failed_status = -1;
    eu4_gfx_clear_status failed_read = eu4_gfx_read_game_target(
        g_master, failed_pixels, length, &failed_got, &failed_w, &failed_h,
        &failed_stride, &failed_generation, &failed_status);
    free(failed_pixels);
    if (failed_read != EU4_GFX_CLEAR_FAILED) {
        fail("injected GPU failure was not reported");
    }
    if (!backend.poisoned || backend.pendingGameCommand != nil ||
        backend.gameContentsKnown || backend.readyForPresent) {
        fail("GPU failure did not clean up backend ownership/readiness");
    }
    if (eu4_gfx_read_game_target(g_master, (void *)quad, sizeof quad, &failed_got,
                                 &failed_w, &failed_h, &failed_stride,
                                 &failed_generation, &failed_status) !=
        EU4_GFX_CLEAR_UNSUPPORTED) {
        fail("poisoned backend accepted another readback");
    }

    printf("live_route=ok clear_draw=1 two_draws=1 present=1 transition=1 unsupported=1 gpu_failure=1\n");
    return 0;
}
