/* Live backend route: _GfxDraw -> shared Simple encoder -> backend queue ->
   checkpoint readback. This host never launches EU4; it drives the same
   replacement entry points the game calls. */

#import "eu4_gfx_buffers.h"
#import "eu4_gfx_draw_capture.h"
#import "eu4_gfx_checkpoint.h"
#import "eu4_gfx_draw_route.h"
#import "eu4_gfx_init.h"
#import "eu4_gfx_generic_draw.h"
#import "eu4_gfx_shader.h"
#import "eu4_gfx_simple_draw.h"
#import "eu4_gfx_sprite_draw.h"
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

static int run_present_caller_filter(const char *directory) {
    eu4_gfx_checkpoint_configure("after-present", 0xabc, 1, directory, 0);
    int unrelated = eu4_gfx_checkpoint_present_select(0xdef);
    int selected = eu4_gfx_checkpoint_present_select(0xabc);
    int later = eu4_gfx_checkpoint_present_select(0xabc);
    printf("unrelated=%d selected=%d later=%d\n", unrelated, selected, later);
    return unrelated == 0 && selected == 1 && later == 0 ? 0 : 1;
}

static int run_checkpoint_env(void) {
    int enabled = eu4_gfx_checkpoint_configure_from_env();
    printf("enabled=%d occurrence=%d\n", enabled,
           eu4_gfx_checkpoint_test_occurrence());
    return 0;
}

static uint8_t *read_file_bytes(const char *path, size_t *length) {
    FILE *file = fopen(path, "rb");
    if (file == NULL) {
        return NULL;
    }
    if (fseek(file, 0, SEEK_END) != 0) {
        fclose(file);
        return NULL;
    }
    long size = ftell(file);
    if (size <= 0 || fseek(file, 0, SEEK_SET) != 0) {
        fclose(file);
        return NULL;
    }
    uint8_t *data = (uint8_t *)malloc((size_t)size);
    if (data == NULL || fread(data, 1, (size_t)size, file) != (size_t)size) {
        free(data);
        fclose(file);
        return NULL;
    }
    fclose(file);
    *length = (size_t)size;
    return data;
}

static int run_prefix_two_frames(void) {
    boot();
    uint8_t *master = (uint8_t *)g_master;
    void *shader = make_simple_shader();
    uint8_t quad[96];
    make_quad(quad);
    void *vertex_header = eu4_gfx_create_vertex_buffer(NULL, quad, 4, 24, 0);
    if (vertex_header == NULL) {
        fail("prefix vertex buffer");
    }
    uint8_t red_bgr[4 * 4 * 3];
    for (int i = 0; i < 4 * 4; i++) {
        red_bgr[i * 3 + 0] = 0;
        red_bgr[i * 3 + 1] = 0;
        red_bgr[i * 3 + 2] = 0xff;
    }
    uint8_t texture_header[0x44];
    make_texture(4, 4, red_bgr, texture_header);
    float matrix[16];
    fill_matrix(matrix);
    uint8_t constant[0x10];
    memset(constant, 0, sizeof constant);
    uint32_t words = 16;
    memcpy(constant, &words, 4);
    void *constant_data = matrix;
    memcpy(constant + 8, &constant_data, sizeof constant_data);
    install_geometry(shader, vertex_header, constant, texture_header);
    uint32_t full_rect[6] = {64, 64, 0x3f800000u, 0, 0, 0};
    uint32_t draw_rect[6] = {32, 32, 0x3f800000u, 0, 0, 0};
    eu4_gfx_set_viewport(master, full_rect);

    char dir[] = "/tmp/eu4-prefix-XXXXXX";
    if (mkdtemp(dir) == NULL || setenv("EU4_GFX_LOGICAL_CAPTURE", "prefix", 1) != 0 ||
        setenv("EU4_GFX_CHECKPOINT_DIR", dir, 1) != 0 ||
        setenv("EU4_GFX_LOGICAL_STOP_CALLER", "0xedfcd1", 1) != 0) {
        fail("prefix environment");
    }
    unsetenv("EU4_GFX_CHECKPOINT");

    struct {
        void *master;
    } ctx = {g_master};
    const float red[4] = {1.f, 0.f, 0.f, 1.f};
    const float green[4] = {0.f, 1.f, 0.f, 1.f};
    char pre_path[256];
    snprintf(pre_path, sizeof pre_path, "%s/metal-prefix-pre.bgra", dir);
    uint8_t frame1[64 * 64 * 4];
    uint8_t frame2[64 * 64 * 4];

    eu4_gfx_clear(&ctx, 7, red);
    eu4_gfx_set_viewport(master, draw_rect);
    eu4_gfx_draw_raw raw = draw_frame(&ctx);
    if (eu4_gfx_draw_route_live(&raw, NULL, NULL, 0) != EU4_GFX_DRAW_ROUTE_HANDLED) {
        fail("prefix frame 1 route");
    }
    size_t length = 0;
    uint8_t *pre = read_file_bytes(pre_path, &length);
    if (pre == NULL || length != sizeof frame1) {
        fail("prefix frame 1 pre-draw");
    }
    memcpy(frame1, pre, sizeof frame1);
    free(pre);
    int command_status = -1;
    if (eu4_gfx_complete_game(g_master, &command_status) != EU4_GFX_CLEAR_OK ||
        command_status != (int)MTLCommandBufferStatusCompleted) {
        fail("prefix frame 1 completion");
    }

    eu4_gfx_set_viewport(master, full_rect);
    eu4_gfx_clear(&ctx, 7, green);
    eu4_gfx_set_viewport(master, draw_rect);
    raw = draw_frame(&ctx);
    if (eu4_gfx_draw_route_live(&raw, NULL, NULL, 0) != EU4_GFX_DRAW_ROUTE_HANDLED) {
        fail("prefix frame 2 route");
    }
    pre = read_file_bytes(pre_path, &length);
    if (pre == NULL || length != sizeof frame2) {
        fail("prefix frame 2 pre-draw");
    }
    memcpy(frame2, pre, sizeof frame2);
    free(pre);

    /* The draw covers the lower-left 32x32. The top-right pixel is the clear
       colour; a stale target would still show frame 1's red there. */
    size_t corner = ((size_t)0 * 64 + 63) * 4;
    int frame1_red = frame1[corner + 2] > 200 && frame1[corner + 1] < 60 &&
                     frame1[corner + 0] < 60;
    int frame2_green = frame2[corner + 1] > 200 && frame2[corner + 2] < 60 &&
                       frame2[corner + 0] < 60;
    printf("frame1_red=%d frame2_green=%d stale_red=%d\n", frame1_red, frame2_green,
           frame2[corner + 2] > 200 && frame2[corner + 1] < 60);
    return frame1_red && frame2_green ? 0 : 1;
}

static int run_frame_start(void) {
    boot();
    uint8_t *master = (uint8_t *)g_master;
    if (master == NULL) {
        fail("frame-start master");
    }
    void *fake_current = (void *)0x1234;
    memcpy(master + 0x70, &fake_current, sizeof fake_current);
    int texture_lod = master[0x18e];
    int begin = eu4_gfx_begin_scene(master);
    void *current = NULL;
    memcpy(&current, master + 0x70, sizeof current);
    static const uint8_t expected_simple_snapshot[0x24] = {
        0x00, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
        0x06, 0x80, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
        0x06, 0x80, 0x00, 0x00, 0x06, 0x02, 0x00, 0x00, 0x01, 0x01, 0x01, 0x01,
    };
    int blend_ok = memcmp(master + 0xc, expected_simple_snapshot,
                          sizeof expected_simple_snapshot) == 0;

    uint8_t header[0x44];
    memset(header, 0, sizeof header);
    header[0x1a] = 1;
    header[0x19] = 1;
    header[0x1b] = 1;
    uint32_t marker = 0x11223344u;
    memcpy(header + 0x10, &marker, 4);
    memcpy(header + 0x8, &marker, 4);
    memcpy(header + 0x14, &marker, 4);
    memcpy(header + 0xc, &marker, 4);
    memcpy(header + 4, &marker, 4);
    eu4_gfx_clear_texture(header);
    int clear_ok = header[0x1a] == 0 && header[0x19] == 0 && header[0x1b] == 0 &&
                   *(uint32_t *)(header + 0x10) == 0 &&
                   *(uint32_t *)(header + 0x8) == 0 &&
                   *(uint32_t *)(header + 0x14) == 0 &&
                   *(uint32_t *)(header + 0xc) == 0 &&
                   *(uint32_t *)(header + 4) == 0 &&
                   eu4_gfx_texture_for_header(header) == NULL;
    int ok = begin == 1 && current == NULL && blend_ok && clear_ok &&
             texture_lod == 1;
    printf("begin_scene=%d current_cleared=%d blend_default=%d clear_texture=%d "
           "texture_lod=%d\n",
           begin == 1, current == NULL, blend_ok, clear_ok, texture_lod);
    return ok ? 0 : 1;
}

static void read_target_texture(id<MTLTexture> texture, id<MTLCommandQueue> queue,
                                NSUInteger row_bytes, void *out) {
    NSUInteger length = row_bytes * texture.height;
    id<MTLBuffer> buffer = [texture.device newBufferWithLength:length
                                                       options:MTLResourceStorageModeShared];
    id<MTLCommandBuffer> command = [queue commandBuffer];
    id<MTLBlitCommandEncoder> blit = [command blitCommandEncoder];
    [blit copyFromTexture:texture
              sourceSlice:0
              sourceLevel:0
             sourceOrigin:MTLOriginMake(0, 0, 0)
               sourceSize:MTLSizeMake(texture.width, texture.height, 1)
                 toBuffer:buffer
        destinationOffset:0
   destinationBytesPerRow:row_bytes
 destinationBytesPerImage:length];
    [blit endEncoding];
    [command commit];
    [command waitUntilCompleted];
    memcpy(out, buffer.contents, length);
}

static int run_texture_type_decision(void) {
    boot();
    id<MTLDevice> device = (__bridge id<MTLDevice>)eu4_gfx_metal_device();
    if (device == nil) {
        return 1;
    }
    MTLTextureDescriptor *flat_descriptor =
        [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm
                                                           width:2
                                                          height:2
                                                       mipmapped:NO];
    MTLTextureDescriptor *cube_descriptor =
        [MTLTextureDescriptor textureCubeDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm
                                                              size:2
                                                         mipmapped:NO];
    id<MTLTexture> flat = [device newTextureWithDescriptor:flat_descriptor];
    id<MTLTexture> cube = [device newTextureWithDescriptor:cube_descriptor];
    int substitute = -1;
    int flat_ok = eu4_gfx_texture_binding_decision(0, (int)flat.textureType, 0,
                                                   &substitute);
    int cube_ok = eu4_gfx_texture_binding_decision(1, (int)cube.textureType, 0,
                                                   &substitute);
    int cube_in_2d_refused = eu4_gfx_texture_binding_decision(
        0, (int)cube.textureType, 0, &substitute);
    int cube_in_2d_allowed = eu4_gfx_texture_binding_decision(
        0, (int)cube.textureType, 1, &substitute);
    int substituted = substitute == 1;
    int flat_in_cube_refused = eu4_gfx_texture_binding_decision(
        1, (int)flat.textureType, 0, &substitute);
    int ok = flat_ok == 1 && cube_ok == 1 && cube_in_2d_refused == 0 &&
             cube_in_2d_allowed == 1 && substituted &&
             flat_in_cube_refused == 0;
    printf("texture_type_decision=%d\n", ok);
    return ok ? 0 : 1;
}

static int run_scissor_helper(void) {
    eu4_gfx_metal_scissor box = {0, 0, 0, 0};
    /* Unset: the caller keeps its full-attachment default. */
    int unset = eu4_gfx_scissor_for_attachment(0, 0, 0, 0, 0, 100, 50, &box);
    /* Partial: GL lower-left box converted to the Metal top-left origin. */
    eu4_gfx_metal_scissor partial = {0, 0, 0, 0};
    int valid = eu4_gfx_scissor_for_attachment(1, 10, 5, 20, 10, 100, 50, &partial);
    /* Empty: zero span. */
    eu4_gfx_metal_scissor empty = {0, 0, 0, 0};
    int empty_status = eu4_gfx_scissor_for_attachment(1, 10, 5, 0, 0, 100, 50, &empty);
    /* Clipped: a box reaching past the attachment. */
    eu4_gfx_metal_scissor clipped = {0, 0, 0, 0};
    int clip_status = eu4_gfx_scissor_for_attachment(1, 90, 40, 20, 20, 100, 50, &clipped);
    int ok = unset == EU4_GFX_SCISSOR_UNSET && valid == EU4_GFX_SCISSOR_VALID &&
             partial.x == 10 && partial.y == 35 && partial.width == 20 &&
             partial.height == 10 && empty_status == EU4_GFX_SCISSOR_EMPTY &&
             clip_status == EU4_GFX_SCISSOR_VALID && clipped.x == 90 &&
             clipped.y == 0 && clipped.width == 10 && clipped.height == 10;
    printf("scissor_helper=%d\n", ok);
    return ok ? 0 : 1;
}

static void *backend_array_value(NSArray *array, NSUInteger index) {
    if (array == nil || index >= array.count) {
        return NULL;
    }
    void *value = NULL;
    [(NSValue *)array[index] getValue:&value];
    return value;
}

static int run_texture_ranges(void) {
    boot();
    uint8_t *master = (uint8_t *)g_master;
    uint8_t fallback_header[0x44];
    memset(fallback_header, 0, sizeof fallback_header);
    void *fallback = fallback_header;
    memcpy(master + 0x98, &fallback, sizeof fallback);
    static uint8_t headers[4][0x44];
    for (int i = 0; i < 4; i++) {
        memset(headers[i], 0, sizeof headers[i]);
    }
    void *a = headers[0], *b = headers[1], *c = headers[2];
    void *textures[2] = {a, b};
    void *handle = master;
    EU4GfxBackend *backend = (__bridge EU4GfxBackend *)eu4_gfx_backend_token();
    if (backend == nil) {
        fail("texture range backend");
    }
    /* Bit 0 only. */
    eu4_gfx_set_textures(&handle, 2, textures, 1, 0, NULL);
    int ok = backend_array_value(backend.boundTextures, 0) == a &&
             backend_array_value(backend.boundTextures, 1) == b &&
             backend.boundTexture0 == a;
    /* Bit 1 only leaves unit 0 alone and binds at base+0xf. */
    void *single[1] = {c};
    eu4_gfx_set_textures(&handle, 1, single, 2, 0, NULL);
    ok = ok && backend_array_value(backend.boundTextures, 0) == a &&
         backend_array_value(backend.boundTextures, 15) == c &&
         backend.boundTexture0 == a;
    /* Both ranges, nonzero base, preserving the earlier units. */
    eu4_gfx_set_textures(&handle, 1, single, 3, 3, NULL);
    ok = ok && backend_array_value(backend.boundTextures, 0) == a &&
         backend_array_value(backend.boundTextures, 3) == c &&
         backend_array_value(backend.boundTextures, 18) == c &&
         backend.boundTexture0 == a;
    /* Partial update keeps unrelated units. */
    eu4_gfx_set_textures(&handle, 1, single, 1, 1, NULL);
    ok = ok && backend_array_value(backend.boundTextures, 0) == a &&
         backend_array_value(backend.boundTextures, 1) == c &&
         backend_array_value(backend.boundTextures, 3) == c;
    /* Null entries substitute the engine default header. */
    void *nulls[1] = {NULL};
    eu4_gfx_set_textures(&handle, 1, nulls, 1, 0, NULL);
    ok = ok && backend_array_value(backend.boundTextures, 0) == fallback &&
         backend.boundTexture0 == fallback;
    /* Unsupported flags are refused without touching the bindings. */
    eu4_gfx_set_textures(&handle, 1, single, 8, 0, NULL);
    ok = ok && backend_array_value(backend.boundTextures, 0) == fallback;
    printf("texture_ranges=%d\n", ok);
    return ok ? 0 : 1;
}

static int run_dump_texture(void) {
    boot();
    EU4GfxBackend *backend = (__bridge EU4GfxBackend *)eu4_gfx_backend_token();
    if (backend == nil || backend.device == nil) {
        fail("dump texture device");
    }
    id<MTLDevice> device = backend.device;
    MTLTextureDescriptor *descriptor = [MTLTextureDescriptor
        texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm
                                     width:4
                                    height:2
                                 mipmapped:NO];
    descriptor.usage = MTLTextureUsageShaderRead;
    id<MTLTexture> texture = [device newTextureWithDescriptor:descriptor];
    uint8_t pixels[4 * 2 * 4];
    for (int i = 0; i < 4 * 2; i++) {
        pixels[i * 4 + 0] = (uint8_t)(i * 8);
        pixels[i * 4 + 1] = 64;
        pixels[i * 4 + 2] = 128;
        pixels[i * 4 + 3] = 255;
    }
    [texture replaceRegion:MTLRegionMake2D(0, 0, 4, 2)
                mipmapLevel:0
                  withBytes:pixels
                bytesPerRow:16];
    char path[256];
    snprintf(path, sizeof path, "%s/dump-texture-test.bgra",
             getenv("TMPDIR") ?: "/tmp");
    int ok = eu4_gfx_dump_texture((__bridge void *)texture, path) == 0;
    FILE *file = fopen(path, "rb");
    if (file == NULL) {
        ok = 0;
    } else {
        fseek(file, 0, SEEK_END);
        long size = ftell(file);
        fclose(file);
        if (size != 4 * 2 * 4) {
            ok = 0;
        }
    }
    remove(path);
    ok = ok && eu4_gfx_dump_texture((__bridge void *)texture,
                                    "/nonexistent-dir/dump-test.bgra") != 0;
    /* The BC path must also fail visibly for an unwritable destination. */
    if (device.supportsBCTextureCompression) {
        const uint8_t bc1_block[8] = {0x00, 0xF8, 0x00, 0x00, 0, 0, 0, 0};
        MTLTextureDescriptor *bc1 = [MTLTextureDescriptor
            texture2DDescriptorWithPixelFormat:MTLPixelFormatBC1_RGBA
                                         width:4
                                        height:4
                                     mipmapped:NO];
        bc1.usage = MTLTextureUsageShaderRead;
        id<MTLTexture> bc1_texture = [device newTextureWithDescriptor:bc1];
        [bc1_texture replaceRegion:MTLRegionMake2D(0, 0, 4, 4)
                       mipmapLevel:0
                         withBytes:bc1_block
                       bytesPerRow:8];
        char bc_path[256];
        snprintf(bc_path, sizeof bc_path, "%s/dump-bc-test.bgra",
                 getenv("TMPDIR") ?: "/tmp");
        ok = ok && eu4_gfx_dump_texture((__bridge void *)bc1_texture, bc_path) == 0;
        ok = ok && eu4_gfx_dump_texture((__bridge void *)bc1_texture,
                                        "/nonexistent-dir/dump-bc.bgra") != 0;
        char sidecar[300];
        snprintf(sidecar, sizeof sidecar, "%s.json", bc_path);
        FILE *meta = fopen(sidecar, "rb");
        ok = ok && meta != NULL;
        if (meta != NULL) {
            fclose(meta);
        }
        remove(bc_path);
        remove(sidecar);
    }
    printf("dump_texture=%d\n", ok);
    return ok ? 0 : 1;
}

static int run_bc_decode(void) {
    boot();
    EU4GfxBackend *backend = (__bridge EU4GfxBackend *)eu4_gfx_backend_token();
    if (backend == nil || backend.device == nil) {
        fail("bc decode device");
    }
    id<MTLDevice> device = backend.device;
    if (!device.supportsBCTextureCompression) {
        printf("bc_decode=skip\n");
        return 0;
    }
    int ok = 1;
    /* Uncompressed RGBA8 with a unique value per texel, non-square extent. */
    uint8_t texels[8 * 4 * 4];
    for (int y = 0; y < 4; y++) {
        for (int x = 0; x < 8; x++) {
            uint8_t *p = texels + (y * 8 + x) * 4;
            p[0] = (uint8_t)(x * 16 + y);
            p[1] = (uint8_t)(x);
            p[2] = (uint8_t)(y);
            p[3] = 255;
        }
    }
    MTLTextureDescriptor *rgba = [MTLTextureDescriptor
        texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm
                                     width:8
                                    height:4
                                 mipmapped:NO];
    rgba.usage = MTLTextureUsageShaderRead;
    id<MTLTexture> rgba_texture = [device newTextureWithDescriptor:rgba];
    [rgba_texture replaceRegion:MTLRegionMake2D(0, 0, 8, 4)
                    mipmapLevel:0
                      withBytes:texels
                    bytesPerRow:8 * 4];
    unsigned char *decoded = NULL;
    size_t row = 0, height = 0;
    int rc = eu4_gfx_decode_texture_rgba8((__bridge void *)rgba_texture, &decoded,
                                          &row, &height);
    if (rc != 0 || row != 32 || height != 4) {
        ok = 0;
    } else {
        /* Bottom-up rows: output row 0 is texture row 3. BGRA order. */
        for (int y = 0; y < 4 && ok; y++) {
            for (int x = 0; x < 8; x++) {
                const uint8_t *p = decoded + (size_t)y * row + x * 4;
                uint8_t expected = (uint8_t)(x * 16 + (3 - y));
                if (p[0] != (uint8_t)(3 - y) || p[1] != (uint8_t)x ||
                    p[2] != expected || p[3] != 255) {
                    ok = 0;
                    break;
                }
            }
        }
    }
    free(decoded);
    decoded = NULL;
    /* BC1: a solid red block (565 red, zero indices). */
    const uint8_t bc1_block[8] = {0x00, 0xF8, 0x00, 0x00, 0, 0, 0, 0};
    MTLTextureDescriptor *bc1 = [MTLTextureDescriptor
        texture2DDescriptorWithPixelFormat:MTLPixelFormatBC1_RGBA
                                     width:4
                                    height:4
                                 mipmapped:NO];
    bc1.usage = MTLTextureUsageShaderRead;
    id<MTLTexture> bc1_texture = [device newTextureWithDescriptor:bc1];
    [bc1_texture replaceRegion:MTLRegionMake2D(0, 0, 4, 4)
                   mipmapLevel:0
                     withBytes:bc1_block
                   bytesPerRow:8];
    int bc1_rc = eu4_gfx_decode_texture_rgba8((__bridge void *)bc1_texture, &decoded,
                                             &row, &height);
    if (bc1_rc != 0) {
        ok = 0;
    } else {
        for (int i = 0; i < 16 && ok; i++) {
            const uint8_t *p = decoded + i * 4;
            if (p[2] < 250 || p[1] > 4 || p[0] > 4 || p[3] != 255) {
                ok = 0;
            }
        }
    }
    free(decoded);
    decoded = NULL;
    /* BC3 lays out the 8-byte alpha block first, then the colour block. Use two
       distinct alpha values: texel 0 selects endpoint 0xFF, the rest 0x00. */
    uint8_t bc3_block[16];
    memset(bc3_block, 0, sizeof bc3_block);
    bc3_block[0] = 0xFF;
    bc3_block[1] = 0x00;
    {
        uint8_t indices[6] = {0};
        for (int texel = 0; texel < 16; texel++) {
            int value = texel == 0 ? 0 : 1;
            for (int bit = 0; bit < 3; bit++) {
                int position = texel * 3 + bit;
                indices[position / 8] |=
                    (uint8_t)(((value >> bit) & 1) << (position % 8));
            }
        }
        memcpy(bc3_block + 2, indices, sizeof indices);
    }
    bc3_block[8] = 0x00;
    bc3_block[9] = 0xF8;
    MTLTextureDescriptor *bc3 = [MTLTextureDescriptor
        texture2DDescriptorWithPixelFormat:MTLPixelFormatBC3_RGBA
                                     width:4
                                    height:4
                                 mipmapped:NO];
    bc3.usage = MTLTextureUsageShaderRead;
    id<MTLTexture> bc3_texture = [device newTextureWithDescriptor:bc3];
    [bc3_texture replaceRegion:MTLRegionMake2D(0, 0, 4, 4)
                   mipmapLevel:0
                     withBytes:bc3_block
                   bytesPerRow:16];
    int bc3_rc = eu4_gfx_decode_texture_rgba8((__bridge void *)bc3_texture, &decoded,
                                             &row, &height);
    if (bc3_rc != 0) {
        ok = 0;
    } else {
        /* Decoded rows are bottom-up: texture (0,0) lands at output row 3. */
        if (decoded[(3 * 4 + 0) * 4 + 3] != 255 ||
            decoded[(3 * 4 + 1) * 4 + 3] != 0) {
            ok = 0;
        }
        for (int i = 0; i < 16 && ok; i++) {
            const uint8_t *p = decoded + i * 4;
            if (p[2] < 250) {
                ok = 0;
            }
        }
    }
    free(decoded);
    printf("bc_decode=%d\n", ok);
    return ok ? 0 : 1;
}

static int run_sampler_decode(void) {
    boot();
    /* Linear filtering with repeat addressing, as the map/terrain draws use. */
    uint8_t linear[0x40];
    memset(linear, 0, sizeof linear);
    uint32_t linear_min = 0x2601, linear_mag = 0x2601, repeat = 0x2901, mip = 0x2601;
    memcpy(linear + 0x18, &repeat, 4);
    memcpy(linear + 0x1c, &repeat, 4);
    memcpy(linear + 0x20, &linear_mag, 4);
    memcpy(linear + 0x24, &linear_min, 4);
    memcpy(linear + 0x28, &mip, 4);
    eu4_gfx_sampler_state a = {0};
    int ok_a = eu4_gfx_decode_sampler(linear, &a) == 0 &&
               a.min_filter == 0x2601 && a.mag_filter == 0x2601 &&
               a.wrap_s == 0x2901;
    /* Nearest filtering with clamp-to-edge. */
    uint8_t nearest[0x40];
    memset(nearest, 0, sizeof nearest);
    uint32_t nearest_min = 0x2600, nearest_mag = 0x2600, clamp = 0x812f;
    memcpy(nearest + 0x18, &clamp, 4);
    memcpy(nearest + 0x1c, &clamp, 4);
    memcpy(nearest + 0x20, &nearest_mag, 4);
    memcpy(nearest + 0x24, &nearest_min, 4);
    memcpy(nearest + 0x28, &mip, 4);
    eu4_gfx_sampler_state b = {0};
    int ok_b = eu4_gfx_decode_sampler(nearest, &b) == 0 &&
               b.min_filter == 0x2600 && b.mag_filter == 0x2600 &&
               b.wrap_s == 0x812f;
    /* An unsupported state must be refused, not approximated. */
    uint32_t bad = 0x1234;
    memcpy(nearest + 0x20, &bad, 4);
    eu4_gfx_sampler_state c = {0};
    int refused = eu4_gfx_decode_sampler(nearest, &c) != 0;
    int ok = ok_a && ok_b && refused;
    printf("sampler_decode=%d\n", ok);
    return ok ? 0 : 1;
}

static int run_sprite_uniform(void) {
    boot();
    uint8_t *master = (uint8_t *)g_master;
    static uint8_t slot[16];
    static uint8_t block[96];
    for (int i = 0; i < 96; i++) {
        block[i] = (uint8_t)(i * 3 + 1);
    }
    uint32_t words = 24;
    memcpy(slot, &words, 4);
    void *data = block;
    memcpy(slot + 8, &data, sizeof data);
    void *slot_ptr = slot;
    memcpy(master + EU4_GFX_MASTER_CONSTANT_SLOT2, &slot_ptr, sizeof slot_ptr);
    uint8_t out[96];
    memset(out, 0, sizeof out);
    int ok = eu4_gfx_sprite_uniform_data(master, out, sizeof out) == 0 &&
             memcmp(out, block, sizeof block) == 0;
    /* The block is read as 24 dwords; the header stores word counts. */
    memcpy(slot, &words, 4);
    void *no_slot = NULL;
    memcpy(master + EU4_GFX_MASTER_CONSTANT_SLOT2, &no_slot, sizeof no_slot);
    int refused = eu4_gfx_sprite_uniform_data(master, out, sizeof out) != 0;
    memcpy(master + EU4_GFX_MASTER_CONSTANT_SLOT2, &slot_ptr, sizeof slot_ptr);
    uint32_t too_few = 23;
    memcpy(slot, &too_few, 4);
    int short_refused = eu4_gfx_sprite_uniform_data(master, out, sizeof out) != 0;
    int ok_all = ok && refused && short_refused;
    printf("sprite_uniform=%d\n", ok_all);
    return ok_all ? 0 : 1;
}

static int run_depth_state(void) {
    boot();
    static const struct {
        int has_depth;
        uint8_t enable;
        uint8_t write;
        int test;
        int write_on;
    } cases[] = {
        {1, 0, 0, 0, 0}, /* test off / write off */
        {1, 0, 1, 0, 0}, /* test off / write mask on */
        {1, 1, 0, 1, 0}, /* test on / write off */
        {1, 1, 1, 1, 1}, /* test on / write on */
        {0, 1, 1, 0, 0}, /* no attachment */
    };
    int ok = 1;
    for (size_t i = 0; i < sizeof(cases) / sizeof(cases[0]); i++) {
        int test = -1, write_on = -1;
        eu4_gfx_effective_depth_state(cases[i].has_depth, cases[i].enable,
                                      cases[i].write, &test, &write_on);
        if (test != cases[i].test || write_on != cases[i].write_on) {
            ok = 0;
        }
    }
    printf("depth_state=%d\n", ok);
    return ok ? 0 : 1;
}

static int run_sampler_effective(void) {
    eu4_gfx_sampler_state state = {0};
    state.min_filter = 0x2601;
    state.mag_filter = 0x2601;
    state.mip_filter = 0x2701;
    uint32_t with_mips = eu4_gfx_effective_min_filter(&state, 1);
    uint32_t without_mips = eu4_gfx_effective_min_filter(&state, 0);
    eu4_gfx_sampler_state explicit_min = {0};
    explicit_min.min_filter = 0x2703;
    explicit_min.mag_filter = 0x2600;
    explicit_min.mip_filter = 0;
    uint32_t explicit_result = eu4_gfx_effective_min_filter(&explicit_min, 1);
    int ok = with_mips == 0x2701 && without_mips == 0x2601 &&
             explicit_result == 0x2703;
    printf("sampler_effective=%d with_mips=%u without_mips=%u explicit=%u\n",
           ok, with_mips, without_mips, explicit_result);
    return ok ? 0 : 1;
}

static uint32_t quadrant_colour(uint32_t x, uint32_t y, uint32_t w, uint32_t h) {
    // BGRA8: red / green / blue / yellow, top-left origin.
    int right = x >= w / 2;
    int bottom = y >= h / 2;
    if (!bottom) return right ? 0xff00ff00u : 0xff0000ffu; // green / red
    return right ? 0xff00ffffu : 0xffff0000u;             // yellow / blue
}

static int run_resolve_orientation(void) {
    boot();
    id<MTLDevice> device = (__bridge id<MTLDevice>)eu4_gfx_metal_device();
    if (device == nil) {
        fail("resolve device");
    }
    MTLTextureDescriptor *sd =
        [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm
                                                           width:4 height:4 mipmapped:NO];
    sd.usage = MTLTextureUsageShaderRead;
    sd.storageMode = MTLStorageModeManaged;
    id<MTLTexture> src = [device newTextureWithDescriptor:sd];
    uint32_t source_pixels[16];
    for (uint32_t y = 0; y < 4; y++) {
        for (uint32_t x = 0; x < 4; x++) {
            source_pixels[y * 4 + x] = quadrant_colour(x, y, 4, 4);
        }
    }
    [src replaceRegion:MTLRegionMake2D(0, 0, 4, 4) mipmapLevel:0
             withBytes:source_pixels bytesPerRow:4 * 4];
    int ok = 1;
    for (uint32_t size = 2; size <= 4; size += 2) {
        MTLTextureDescriptor *dd =
            [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm
                                                               width:size height:size mipmapped:NO];
        dd.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead;
        dd.storageMode = MTLStorageModeShared;
        id<MTLTexture> dst = [device newTextureWithDescriptor:dd];
        if (eu4_gfx_resolve_orientation_test((__bridge void *)src,
                                             (__bridge void *)dst) != 0) {
            fail("resolve orientation call");
        }
        uint32_t out[16];
        id<MTLCommandQueue> queue = [device newCommandQueue];
        read_target_texture(dst, queue, size * 4, out);
        // The resolve converts a top-left source to GL row order: source
        // bottom rows become destination top rows.
        uint32_t tl = quadrant_colour(0, 4, 4, 4); // source bottom-left
        uint32_t tr = quadrant_colour(4, 4, 4, 4); // source bottom-right
        uint32_t bl = quadrant_colour(0, 0, 4, 4); // source top-left
        uint32_t br = quadrant_colour(4, 0, 4, 4); // source top-right
        uint32_t expect[4] = {tl, tr, bl, br};
        for (uint32_t i = 0; i < 4; i++) {
            uint32_t x = i % 2, y = i / 2;
            uint32_t got = out[(y * size / 2) * size + x * size / 2];
            if ((got & 0x00ffffffu) != (expect[i] & 0x00ffffffu)) {
                fprintf(stderr, "FAIL: resolve %ux%u quadrant %u got %08x want %08x\n",
                        size, size, i, got, expect[i]);
                ok = 0;
            }
        }
    }
    printf("resolve_orientation=%d\n", ok);
    return ok ? 0 : 1;
}

static int run_offscreen_viewport(void) {
    /* An offscreen target is allocated at the engine's logical size. The
       window is 64x64 (HiDPI would scale the backbuffer), but the target is
       32x32 and its full-viewport draw must cover all of it. Before the route
       fix the backbuffer scale (32/64) shrank the viewport to a 16x16 corner. */
    boot();
    void *shader = make_simple_shader();
    uint8_t quad[96];
    make_quad(quad);
    void *vertex_header = eu4_gfx_create_vertex_buffer(NULL, quad, 4, 24, 0);
    if (vertex_header == NULL) {
        fail("offscreen vertex buffer");
    }
    uint8_t red_bgr[4 * 4 * 3];
    for (int i = 0; i < 4 * 4; i++) {
        red_bgr[i * 3 + 0] = 0;
        red_bgr[i * 3 + 1] = 0;
        red_bgr[i * 3 + 2] = 0xff;
    }
    uint8_t texture_header[0x44];
    make_texture(4, 4, red_bgr, texture_header);
    float matrix[16];
    fill_matrix(matrix);
    uint8_t constant[0x10];
    memset(constant, 0, sizeof constant);
    uint32_t words = 16;
    memcpy(constant, &words, 4);
    void *constant_data = matrix;
    memcpy(constant + 8, &constant_data, sizeof constant_data);

    void *rt = eu4_gfx_create_render_target_texture(g_master, 32, 32, 0, 1, 0);
    if (rt == NULL) {
        fail("offscreen render target");
    }
    struct {
        void *master;
    } ctx = {g_master};
    uint32_t window_rect[6] = {64, 64, 0x3f800000u, 0, 0, 0};
    eu4_gfx_set_viewport(g_master, window_rect);
    const float black[4] = {0.f, 0.f, 0.f, 1.f};
    eu4_gfx_clear(&ctx, 1, black);
    if (eu4_gfx_complete_game(g_master, NULL) != EU4_GFX_CLEAR_OK) {
        fail("offscreen frame clear");
    }
    eu4_gfx_set_render_target(g_master, rt);
    install_geometry(shader, vertex_header, constant, texture_header);
    uint32_t rect[6] = {32, 32, 0x3f800000u, 0, 0, 0};
    eu4_gfx_set_viewport(g_master, rect);

    eu4_gfx_draw_raw raw = draw_frame(&ctx);
    char diagnostic[256];
    if (eu4_gfx_draw_route_live(&raw, NULL, diagnostic, sizeof diagnostic) !=
        EU4_GFX_DRAW_ROUTE_HANDLED) {
        fail(diagnostic[0] != 0 ? diagnostic : "offscreen draw route");
    }
    if (eu4_gfx_complete_game(g_master, NULL) != EU4_GFX_CLEAR_OK) {
        fail("offscreen draw completion");
    }
    id<MTLTexture> color =
        (__bridge id<MTLTexture>)eu4_gfx_texture_for_header(rt);
    id<MTLDevice> device = (__bridge id<MTLDevice>)eu4_gfx_metal_device();
    id<MTLCommandQueue> queue = [device newCommandQueue];
    uint8_t pixels[32 * 32 * 4];
    read_target_texture(color, queue, 32 * 4, pixels);
    int covered = 0;
    for (int i = 0; i < 32 * 32; i++) {
        if (pixels[i * 4 + 2] > 200 && pixels[i * 4 + 1] < 60 &&
            pixels[i * 4 + 0] < 60) {
            covered++;
        }
    }
    printf("offscreen_viewport=%d covered=%d of=%d\n", covered == 32 * 32,
           covered, 32 * 32);
    return covered == 32 * 32 ? 0 : 1;
}

static int run_target_clear(void) {
    boot();
    void *handle = g_master;
    id<MTLDevice> device = (__bridge id<MTLDevice>)eu4_gfx_metal_device();
    id<MTLCommandQueue> queue = [device newCommandQueue];
    void *rt = eu4_gfx_create_render_target_texture(handle, 16, 16, 0, 1, 0);
    if (rt == NULL) {
        fail("target texture creation");
    }
    eu4_gfx_set_render_target(handle, rt);
    id<MTLTexture> color = (__bridge id<MTLTexture>)eu4_gfx_texture_for_header(rt);
    id<MTLTexture> depth = (__bridge id<MTLTexture>)eu4_gfx_depth_texture_for_header(rt);
    if (color == nil || depth == nil) {
        fail("target textures");
    }
    /* Known depth value so preservation is observable. */
    MTLRenderPassDescriptor *pass = [MTLRenderPassDescriptor renderPassDescriptor];
    pass.depthAttachment.texture = depth;
    pass.depthAttachment.loadAction = MTLLoadActionClear;
    pass.depthAttachment.clearDepth = 0.25;
    pass.depthAttachment.storeAction = MTLStoreActionStore;
    id<MTLCommandBuffer> command = [queue commandBuffer];
    id<MTLRenderCommandEncoder> encoder = [command renderCommandEncoderWithDescriptor:pass];
    [encoder endEncoding];
    [command commit];
    [command waitUntilCompleted];

    uint8_t pixels[16 * 16 * 4];
    float depths[16 * 16];

    /* Colour-only clear: colour changes, depth is preserved. */
    float red[4] = {1.f, 0.f, 0.f, 1.f};
    eu4_gfx_clear(handle, 1, red);
    if (eu4_gfx_complete_game(handle, NULL) != EU4_GFX_CLEAR_OK) {
        fail("target clear did not complete");
    }
    read_target_texture(color, queue, 16 * 4, pixels);
    read_target_texture(depth, queue, 16 * 4, depths);
    int color_cleared = pixels[0] == 0 && pixels[1] == 0 && pixels[2] == 0xff &&
                        pixels[3] == 0xff;
    int depth_preserved = fabsf(depths[0] - 0.25f) < 1e-6f;

    /* Colour+depth clear: both attachments change. */
    float green[4] = {0.f, 1.f, 0.f, 1.f};
    eu4_gfx_clear(handle, 3, green);
    if (eu4_gfx_complete_game(handle, NULL) != EU4_GFX_CLEAR_OK) {
        fail("target clear did not complete");
    }
    read_target_texture(color, queue, 16 * 4, pixels);
    read_target_texture(depth, queue, 16 * 4, depths);
    int color_cleared2 = pixels[0] == 0 && pixels[1] == 0xff && pixels[2] == 0;
    int depth_cleared = fabsf(depths[0] - 1.0f) < 1e-6f;

    /* Depth+stencil only: colour is preserved. */
    float blue[4] = {0.f, 0.f, 1.f, 1.f};
    eu4_gfx_clear(handle, 6, blue);
    if (eu4_gfx_complete_game(handle, NULL) != EU4_GFX_CLEAR_OK) {
        fail("target clear did not complete");
    }
    read_target_texture(color, queue, 16 * 4, pixels);
    int color_preserved = pixels[0] == 0 && pixels[1] == 0xff && pixels[2] == 0;

    printf("target_clear=%d depth_preserved=%d depth_cleared=%d "
           "color_preserved=%d\n",
           color_cleared && color_cleared2, depth_preserved, depth_cleared,
           color_preserved);
    return color_cleared && color_cleared2 && depth_preserved && depth_cleared &&
                   color_preserved
               ? 0
               : 1;
}

int main(int argc, char **argv) {
    if (argc >= 2 && strcmp(argv[1], "--target-clear") == 0) {
        return run_target_clear();
    }
    if (argc >= 2 && strcmp(argv[1], "--offscreen-viewport") == 0) {
        return run_offscreen_viewport();
    }
    if (argc >= 2 && strcmp(argv[1], "--sampler-effective") == 0) {
        return run_sampler_effective();
    }
    if (argc >= 2 && strcmp(argv[1], "--resolve-orientation") == 0) {
        return run_resolve_orientation();
    }
    if (argc >= 2 && strcmp(argv[1], "--depth-state") == 0) {
        return run_depth_state();
    }
    if (argc >= 2 && strcmp(argv[1], "--sprite-uniform") == 0) {
        return run_sprite_uniform();
    }
    if (argc >= 2 && strcmp(argv[1], "--sampler-decode") == 0) {
        return run_sampler_decode();
    }
    if (argc >= 2 && strcmp(argv[1], "--bc-decode") == 0) {
        return run_bc_decode();
    }
    if (argc >= 2 && strcmp(argv[1], "--dump-texture") == 0) {
        return run_dump_texture();
    }
    if (argc >= 2 && strcmp(argv[1], "--texture-ranges") == 0) {
        return run_texture_ranges();
    }
    if (argc >= 2 && strcmp(argv[1], "--scissor-helper") == 0) {
        return run_scissor_helper();
    }
    if (argc >= 2 && strcmp(argv[1], "--texture-type-decision") == 0) {
        return run_texture_type_decision();
    }
    if (argc >= 2 && strcmp(argv[1], "--frame-start") == 0) {
        return run_frame_start();
    }
    if (argc >= 2 && strcmp(argv[1], "--prefix-two-frames") == 0) {
        return run_prefix_two_frames();
    }
    if (argc >= 2 && strcmp(argv[1], "--checkpoint-env") == 0) {
        return run_checkpoint_env();
    }
    if (argc >= 3 && strcmp(argv[1], "--present-caller-filter") == 0) {
        return run_present_caller_filter(argv[2]);
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
