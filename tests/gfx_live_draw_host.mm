/* Live backend route: _GfxDraw -> shared Simple encoder -> backend queue ->
   checkpoint readback. This host never launches EU4; it drives the same
   replacement entry points the game calls. */

#import "../renderer/metal/eu4_gfx_buffers.h"
#import "eu4_gfx_draw_capture.h"
#import "../renderer/integration/eu4_gfx_checkpoint.h"
#import "../renderer/integration/eu4_gfx_draw_route.h"
#import "../renderer/integration/eu4_gfx_init.h"
#import "../renderer/metal/eu4_gfx_generic_draw.h"
#import "../renderer/metal/eu4_gfx_shader.h"
#import "../renderer/metal/eu4_gfx_simple_draw.h"
#import "../renderer/metal/eu4_gfx_sprite_draw.h"
#import "../renderer/metal/eu4_gfx_texture.h"
#import "../renderer/metal/eu4_gfx_draw_semantics.h"
#import "eu4_gfx_perf.h"
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
#import <time.h>
#import <pthread.h>
#import <dirent.h>

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

static char g_event_dir[256];

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
    snprintf(g_event_dir, sizeof g_event_dir, "%s", dir);
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
    uint8_t red_bgr[4 * 4 * 3] = {};
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
    uint8_t red_bgr[4 * 4 * 3] = {};
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

static int run_color_write_mask(void) {
    /* The engine records the four channel enables at master+0x2c. This host
       applies the shared conversion to Metal's colour-write mask and checks
       that disabled channels keep their destination values. */
    boot();
    id<MTLDevice> device = (__bridge id<MTLDevice>)eu4_gfx_metal_device();
    if (device == nil) {
        fail("colour mask device");
    }
    NSString *source = @
        "#include <metal_stdlib>\n"
        "using namespace metal;\n"
        "struct VOut { float4 position [[position]]; };\n"
        "vertex VOut v_main(uint vid [[vertex_id]]) {\n"
        "  float2 p[3] = {float2(-1.0,-1.0), float2(3.0,-1.0), float2(-1.0,3.0)};\n"
        "  VOut o; o.position = float4(p[vid], 0.0, 1.0); return o;\n"
        "}\n"
        "fragment float4 f_main() { return float4(0.0, 1.0, 0.0, 1.0); }\n";
    NSError *error = nil;
    id<MTLLibrary> library = [device newLibraryWithSource:source options:nil error:&error];
    if (library == nil) {
        fail("colour mask library");
    }
    MTLTextureDescriptor *td =
        [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm
                                                           width:2 height:2 mipmapped:NO];
    td.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead;
    td.storageMode = MTLStorageModeShared;
    id<MTLCommandQueue> queue = [device newCommandQueue];
    const uint8_t masks[5] = {15, 0, 1, 14, 10};
    int ok = 1;
    for (int m = 0; m < 5; m++) {
        MTLRenderPipelineDescriptor *pd = [MTLRenderPipelineDescriptor new];
        pd.vertexFunction = [library newFunctionWithName:@"v_main"];
        pd.fragmentFunction = [library newFunctionWithName:@"f_main"];
        pd.colorAttachments[0].pixelFormat = MTLPixelFormatBGRA8Unorm;
        const uint8_t bytes[4] = {(uint8_t)((masks[m] & 8) != 0), /* R */
                                  (uint8_t)((masks[m] & 4) != 0), /* G */
                                  (uint8_t)((masks[m] & 2) != 0), /* B */
                                  (uint8_t)((masks[m] & 1) != 0)}; /* A */
        pd.colorAttachments[0].writeMask =
            (MTLColorWriteMask)eu4_gfx_color_write_mask(bytes);
        id<MTLRenderPipelineState> pipeline =
            [device newRenderPipelineStateWithDescriptor:pd error:&error];
        if (pipeline == nil) {
            ok = 0;
            continue;
        }
        id<MTLTexture> target = [device newTextureWithDescriptor:td];
        const uint8_t initial[4] = {10, 20, 30, 40};
        uint8_t fill[2 * 2 * 4];
        for (int i = 0; i < 4; i++) {
            memcpy(fill + i * 4, initial, 4);
        }
        [target replaceRegion:MTLRegionMake2D(0, 0, 2, 2) mipmapLevel:0
                    withBytes:fill bytesPerRow:2 * 4];
        id<MTLCommandBuffer> command = [queue commandBuffer];
        MTLRenderPassDescriptor *pass = [MTLRenderPassDescriptor renderPassDescriptor];
        pass.colorAttachments[0].texture = target;
        pass.colorAttachments[0].loadAction = MTLLoadActionLoad;
        pass.colorAttachments[0].storeAction = MTLStoreActionStore;
        id<MTLRenderCommandEncoder> encoder = [command renderCommandEncoderWithDescriptor:pass];
        [encoder setRenderPipelineState:pipeline];
        [encoder drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
        [encoder endEncoding];
        [command commit];
        [command waitUntilCompleted];
        if (command.status != MTLCommandBufferStatusCompleted) {
            ok = 0;
            continue;
        }
        uint32_t pixels[4];
        read_target_texture(target, queue, 2 * 4, pixels);
        const uint8_t *pixel = (const uint8_t *)&pixels[0];
        /* Memory order is B,G,R,A; MTLColorWriteMask is alpha/blues/green/red. */
        const uint8_t want[4] = {
            (uint8_t)((masks[m] & 2) ? 0 : 10),   /* B from green is 0 */
            (uint8_t)((masks[m] & 4) ? 255 : 20), /* G */
            (uint8_t)((masks[m] & 8) ? 0 : 30),   /* R from green is 0 */
            (uint8_t)((masks[m] & 1) ? 255 : 40), /* A */
        };
        for (int c = 0; c < 4; c++) {
            if (abs((int)pixel[c] - (int)want[c]) > 1) {
                fprintf(stderr, "FAIL: mask %u channel %d got %u want %u\n",
                        masks[m], c, pixel[c], want[c]);
                ok = 0;
            }
        }
    }
    printf("color_write_mask=%d masks=5\n", ok);
    return ok ? 0 : 1;
}

static int run_indexed_base_vertex(void) {
    /* glDrawElementsBaseVertex adds a signed base. This host draws the same
       three indices with base 4 and base 0 through Metal's native baseVertex
       overload and checks the actually fetched vertex colours. The merged
       #189 path rewrote index-base (clamping negatives), which selects the
       wrong vertices. */
    boot();
    id<MTLDevice> device = (__bridge id<MTLDevice>)eu4_gfx_metal_device();
    if (device == nil) {
        fail("base vertex device");
    }
    NSString *source = @
        "#include <metal_stdlib>\n"
        "using namespace metal;\n"
        "struct VIn { float4 position [[attribute(0)]]; float4 color [[attribute(1)]]; };\n"
        "struct VOut { float4 position [[position]]; float4 color; };\n"
        "vertex VOut v_main(VIn in [[stage_in]]) { VOut o; o.position = in.position; o.color = in.color; return o; }\n"
        "fragment float4 f_main(VOut in [[stage_in]]) { return in.color; }\n";
    NSError *error = nil;
    id<MTLLibrary> library = [device newLibraryWithSource:source options:nil error:&error];
    if (library == nil) {
        fail("base vertex library");
    }
    MTLVertexDescriptor *vd = [MTLVertexDescriptor vertexDescriptor];
    vd.attributes[0].format = MTLVertexFormatFloat4;
    vd.attributes[0].offset = 0;
    vd.attributes[0].bufferIndex = 0;
    vd.attributes[1].format = MTLVertexFormatFloat4;
    vd.attributes[1].offset = 16;
    vd.attributes[1].bufferIndex = 0;
    vd.layouts[0].stride = 32;
    vd.layouts[0].stepFunction = MTLVertexStepFunctionPerVertex;
    vd.layouts[0].stepRate = 1;
    MTLRenderPipelineDescriptor *pd = [MTLRenderPipelineDescriptor new];
    pd.vertexFunction = [library newFunctionWithName:@"v_main"];
    pd.fragmentFunction = [library newFunctionWithName:@"f_main"];
    pd.vertexDescriptor = vd;
    pd.colorAttachments[0].pixelFormat = MTLPixelFormatBGRA8Unorm;
    id<MTLRenderPipelineState> pipeline =
        [device newRenderPipelineStateWithDescriptor:pd error:&error];
    if (pipeline == nil) {
        fail("base vertex pipeline");
    }
    typedef struct { float p[4]; float c[4]; } vertex;
    vertex vertices[8];
    memset(vertices, 0, sizeof vertices);
    const float positions[3][2] = {{-1.0f, -1.0f}, {3.0f, -1.0f}, {-1.0f, 3.0f}};
    for (int i = 0; i < 3; i++) {
        vertices[i].p[0] = positions[i][0];
        vertices[i].p[1] = positions[i][1];
        vertices[i].p[2] = 0.0f;
        vertices[i].p[3] = 1.0f;
        vertices[i].c[0] = 1.0f; /* red */
        vertices[i].c[3] = 1.0f;
        vertices[4 + i].p[0] = positions[i][0];
        vertices[4 + i].p[1] = positions[i][1];
        vertices[4 + i].p[2] = 0.0f;
        vertices[4 + i].p[3] = 1.0f;
        vertices[4 + i].c[1] = 1.0f; /* green */
        vertices[4 + i].c[3] = 1.0f;
    }
    id<MTLBuffer> vb = [device newBufferWithBytes:vertices length:sizeof vertices
                                          options:MTLResourceStorageModeShared];
    const uint16_t indices[3] = {0, 1, 2};
    id<MTLBuffer> ib = [device newBufferWithBytes:indices length:sizeof indices
                                          options:MTLResourceStorageModeShared];
    MTLTextureDescriptor *td =
        [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm
                                                           width:4 height:4 mipmapped:NO];
    td.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead;
    td.storageMode = MTLStorageModeShared;
    id<MTLCommandQueue> queue = [device newCommandQueue];
    int ok = 1;
    BOOL saw_green = NO, saw_red = NO;
    for (int pass = 0; pass < 2; pass++) {
        NSInteger base = pass == 0 ? 4 : 0;
        id<MTLTexture> target = [device newTextureWithDescriptor:td];
        id<MTLCommandBuffer> command = [queue commandBuffer];
        MTLRenderPassDescriptor *pass_descriptor = [MTLRenderPassDescriptor renderPassDescriptor];
        pass_descriptor.colorAttachments[0].texture = target;
        pass_descriptor.colorAttachments[0].loadAction = MTLLoadActionClear;
        pass_descriptor.colorAttachments[0].clearColor = MTLClearColorMake(0, 0, 0, 1);
        pass_descriptor.colorAttachments[0].storeAction = MTLStoreActionStore;
        id<MTLRenderCommandEncoder> encoder =
            [command renderCommandEncoderWithDescriptor:pass_descriptor];
        [encoder setRenderPipelineState:pipeline];
        [encoder setVertexBuffer:vb offset:0 atIndex:0];
        [encoder drawIndexedPrimitives:MTLPrimitiveTypeTriangle
                            indexCount:3
                             indexType:MTLIndexTypeUInt16
                           indexBuffer:ib
                     indexBufferOffset:0
                         instanceCount:1
                            baseVertex:base
                          baseInstance:0];
        [encoder endEncoding];
        [command commit];
        [command waitUntilCompleted];
        if (command.status != MTLCommandBufferStatusCompleted) {
            ok = 0;
            continue;
        }
        uint32_t pixels[16];
        read_target_texture(target, queue, 4 * 4, pixels);
        const uint8_t *pixel = (const uint8_t *)&pixels[10];
        if (pass == 0 && pixel[1] > 200 && pixel[2] < 40) {
            saw_green = YES;
        }
        if (pass == 1 && pixel[2] > 200 && pixel[1] < 40) {
            saw_red = YES;
        }
    }
    ok = ok && saw_green && saw_red;
    printf("indexed_base_vertex=%d base4_green=%d base0_red=%d\n",
           ok, saw_green, saw_red);
    return ok ? 0 : 1;
}

static int run_vertex_fetch(void) {
    boot();
    id<MTLDevice> device = (__bridge id<MTLDevice>)eu4_gfx_metal_device();
    if (device == nil) {
        fail("vertex fetch device");
    }
    NSString *source = @
        "#include <metal_stdlib>\n"
        "using namespace metal;\n"
        "constant uint kColumns [[function_constant(0)]];\n"
        "struct VIn { float4 color [[attribute(0)]]; };\n"
        "struct VOut { float4 position [[position]]; float4 color; };\n"
        "vertex VOut v_main(VIn in [[stage_in]], uint vid [[vertex_id]],\n"
        "                   uint iid [[instance_id]]) {\n"
        "  float x0 = -1.0 + 2.0 * float(iid) / float(kColumns);\n"
        "  float x1 = -1.0 + 2.0 * float(iid + 1) / float(kColumns);\n"
        "  float2 p[4] = {float2(x0, -1.0), float2(x1, -1.0),\n"
        "                 float2(x0, 1.0), float2(x1, 1.0)};\n"
        "  VOut o; o.position = float4(p[vid], 0.0, 1.0);\n"
        "  o.color = in.color; return o;\n"
        "}\n"
        "fragment float4 f_main(VOut in [[stage_in]]) { return in.color; }\n";
    NSError *error = nil;
    id<MTLLibrary> library = [device newLibraryWithSource:source options:nil error:&error];
    if (library == nil) {
        fail("vertex fetch library");
    }
    id<MTLCommandQueue> queue = [device newCommandQueue];
    MTLTextureDescriptor *td =
        [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm
                                                           width:3 height:3 mipmapped:NO];
    td.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead;
    td.storageMode = MTLStorageModeShared;

    const float colors[3][4] = {
        {0.0f, 1.0f, 0.0f, 1.0f}, /* green */
        {1.0f, 0.0f, 0.0f, 1.0f}, /* red */
        {0.0f, 0.0f, 1.0f, 1.0f}, /* blue */
    };
    int ok = 1;
    int one_instance_ok = 1;
    int multi_instance_ok = 1;
    int constant_ok = 1;

    /* Runs one pipeline and returns the pixels of the middle row. */
    for (int mode = 0; mode < 3; mode++) {
        uint32_t columns = mode == 1 ? 3 : (mode == 2 ? 3 : 1);
        MTLFunctionConstantValues *constants = [MTLFunctionConstantValues new];
        [constants setConstantValue:&columns type:MTLDataTypeUInt atIndex:0];
        NSError *function_error = nil;
        id<MTLFunction> vs =
            [library newFunctionWithName:@"v_main" constantValues:constants
                                   error:&function_error];
        id<MTLFunction> fs = [library newFunctionWithName:@"f_main"];
        if (vs == nil || fs == nil) {
            ok = 0;
            break;
        }
        MTLVertexDescriptor *vd = [MTLVertexDescriptor vertexDescriptor];
        vd.attributes[0].format = MTLVertexFormatFloat4;
        vd.attributes[0].offset = 0;
        vd.attributes[0].bufferIndex = 0;
        vd.layouts[0].stride = 16;
        if (mode == 2) {
            vd.layouts[0].stepFunction = MTLVertexStepFunctionConstant;
            vd.layouts[0].stepRate = 0;
        } else {
            vd.layouts[0].stepFunction = MTLVertexStepFunctionPerInstance;
            vd.layouts[0].stepRate = mode == 0 ? 1 : 2;
        }
        MTLRenderPipelineDescriptor *pd = [MTLRenderPipelineDescriptor new];
        pd.vertexFunction = vs;
        pd.fragmentFunction = fs;
        pd.vertexDescriptor = vd;
        pd.colorAttachments[0].pixelFormat = MTLPixelFormatBGRA8Unorm;
        id<MTLRenderPipelineState> pipeline =
            [device newRenderPipelineStateWithDescriptor:pd error:&error];
        if (pipeline == nil) {
            ok = 0;
            break;
        }
        const void *bytes = colors;
        NSUInteger length = 3 * 16;
        if (mode == 2) {
            bytes = colors[0];
            length = 16;
        }
        id<MTLBuffer> buffer =
            [device newBufferWithBytes:bytes length:length
                               options:MTLResourceStorageModeShared];
        id<MTLTexture> target = [device newTextureWithDescriptor:td];
        id<MTLCommandBuffer> command = [queue commandBuffer];
        MTLRenderPassDescriptor *pass = [MTLRenderPassDescriptor renderPassDescriptor];
        pass.colorAttachments[0].texture = target;
        pass.colorAttachments[0].loadAction = MTLLoadActionClear;
        pass.colorAttachments[0].clearColor = MTLClearColorMake(0, 0, 0, 1);
        pass.colorAttachments[0].storeAction = MTLStoreActionStore;
        id<MTLRenderCommandEncoder> encoder =
            [command renderCommandEncoderWithDescriptor:pass];
        [encoder setRenderPipelineState:pipeline];
        [encoder setVertexBuffer:buffer offset:0 atIndex:0];
        [encoder drawPrimitives:MTLPrimitiveTypeTriangleStrip
                    vertexStart:0 vertexCount:4
                  instanceCount:(mode == 0 ? 1 : 3)];
        [encoder endEncoding];
        [command commit];
        [command waitUntilCompleted];
        if (command.status != MTLCommandBufferStatusCompleted) {
            ok = 0;
            break;
        }
        uint32_t pixels[9];
        read_target_texture(target, queue, 3 * 4, pixels);
        for (int x = 0; x < 3; x++) {
            const uint8_t *pixel = (const uint8_t *)&pixels[1 * 3 + x];
            int green = pixel[1] > 200 && pixel[0] < 40 && pixel[2] < 40;
            int red = pixel[2] > 200 && pixel[0] < 40 && pixel[1] < 40;
            int blue = pixel[0] > 200 && pixel[1] < 40 && pixel[2] < 40;
            if (mode == 0 && !green) one_instance_ok = 0;
            if (mode == 1) {
                if ((x < 2 && !green) || (x == 2 && !red)) multi_instance_ok = 0;
            }
            if (mode == 2 && !green) constant_ok = 0;
            (void)blue;
        }
    }
    ok = ok && one_instance_ok && multi_instance_ok && constant_ok;
    printf("vertex_fetch=%d one_instance=%d multi_instance=%d constant=%d\n",
           ok, one_instance_ok, multi_instance_ok, constant_ok);
    return ok ? 0 : 1;
}

static int run_production_bounds(void) {
    int ok = 1;
    char reason[256] = "";
    const uint32_t float3 = (uint32_t)MTLVertexFormatFloat3;
    const uint32_t float2 = (uint32_t)MTLVertexFormatFloat2;
    const uint32_t float4 = (uint32_t)MTLVertexFormatFloat4;

    uint32_t formats[2] = {float3, 0};
    uint64_t offsets[2] = {0, 0};
    uint64_t strides[2] = {20, 0};
    uint64_t lengths[2] = {72, 0};
    uint32_t divisors[2] = {0, 0};
    ok &= eu4_gfx_generic_bounds_test(formats, offsets, strides, lengths,
                                      divisors, 1, 3, 1, reason,
                                      sizeof reason) == 1;
    lengths[0] = 71;
    ok &= eu4_gfx_generic_bounds_test(formats, offsets, strides, lengths,
                                      divisors, 1, 3, 1, reason,
                                      sizeof reason) == 0;
    /* Four 20-byte vertices, UV at offset 12, tightly sized at 80. */
    formats[0] = float2;
    offsets[0] = 12;
    strides[0] = 20;
    lengths[0] = 80;
    ok &= eu4_gfx_generic_bounds_test(formats, offsets, strides, lengths,
                                      divisors, 1, 3, 1, reason,
                                      sizeof reason) == 1;
    lengths[0] = 79;
    ok &= eu4_gfx_generic_bounds_test(formats, offsets, strides, lengths,
                                      divisors, 1, 3, 1, reason,
                                      sizeof reason) == 0;
    /* Nonzero slot/attribute offset. */
    formats[0] = float3;
    offsets[0] = 40;
    strides[0] = 20;
    lengths[0] = 112;
    ok &= eu4_gfx_generic_bounds_test(formats, offsets, strides, lengths,
                                      divisors, 1, 3, 1, reason,
                                      sizeof reason) == 1;
    /* Per-instance attribute with divisor 2 and five instances. */
    formats[0] = float4;
    offsets[0] = 0;
    strides[0] = 16;
    lengths[0] = 48;
    divisors[0] = 2;
    ok &= eu4_gfx_generic_bounds_test(formats, offsets, strides, lengths,
                                      divisors, 1, 0, 5, reason,
                                      sizeof reason) == 1;
    lengths[0] = 47;
    ok &= eu4_gfx_generic_bounds_test(formats, offsets, strides, lengths,
                                      divisors, 1, 0, 5, reason,
                                      sizeof reason) == 0;
    /* One per-instance element, tightly sized: 16 bytes valid, 15 invalid. */
    formats[0] = float4;
    offsets[0] = 0;
    strides[0] = 16;
    lengths[0] = 16;
    divisors[0] = 1;
    ok &= eu4_gfx_generic_bounds_test(formats, offsets, strides, lengths,
                                      divisors, 1, 0, 1, reason,
                                      sizeof reason) == 1;
    lengths[0] = 15;
    ok &= eu4_gfx_generic_bounds_test(formats, offsets, strides, lengths,
                                      divisors, 1, 0, 1, reason,
                                      sizeof reason) == 0;

    /* Unknown format fails closed. */
    formats[0] = 0;
    lengths[0] = 1024;
    divisors[0] = 0;
    ok &= eu4_gfx_generic_bounds_test(formats, offsets, strides, lengths,
                                      divisors, 1, 3, 1, reason,
                                      sizeof reason) == 0;

    /* All 17 admitted attributes participate in validation. */
    uint32_t formats17[17];
    uint64_t offsets17[17];
    uint64_t strides17[17];
    uint64_t lengths17[17];
    uint32_t divisors17[17];
    for (int i = 0; i < 17; i++) {
        formats17[i] = float3;
        offsets17[i] = 0;
        strides17[i] = 20;
        lengths17[i] = 72;
        divisors17[i] = 0;
    }
    int attributes17_ok = eu4_gfx_generic_bounds_test(
        formats17, offsets17, strides17, lengths17, divisors17, 17, 3, 1,
        reason, sizeof reason) == 1;
    lengths17[16] = 71;
    attributes17_ok &= eu4_gfx_generic_bounds_test(
        formats17, offsets17, strides17, lengths17, divisors17, 17, 3, 1,
        reason, sizeof reason) == 0;
    lengths17[16] = 72;
    lengths17[15] = 71;
    attributes17_ok &= eu4_gfx_generic_bounds_test(
        formats17, offsets17, strides17, lengths17, divisors17, 17, 3, 1,
        reason, sizeof reason) == 0;
    lengths17[15] = 72;

    /* Metal capacity guard: 17 attributes from base slot 8 fit (next_buffer
       25); 31/32 and location 31 are refused with diagnostics. */
    char capacity_reason[256] = "";
    int capacity_ok =
        eu4_gfx_generic_descriptor_capacity_test(25, 30, capacity_reason,
                                                 sizeof capacity_reason) == 1 &&
        eu4_gfx_generic_descriptor_capacity_test(31, 30, capacity_reason,
                                                 sizeof capacity_reason) == 0 &&
        eu4_gfx_generic_descriptor_capacity_test(25, 31, capacity_reason,
                                                 sizeof capacity_reason) == 0;
    ok &= attributes17_ok && capacity_ok;
    printf("production_bounds=%d attributes_17=%d capacity=%d\n",
           ok, attributes17_ok, capacity_ok);
    return ok ? 0 : 1;
}

static int run_production_pipeline(void) {
    boot();
    id<MTLDevice> device = (__bridge id<MTLDevice>)eu4_gfx_metal_device();
    if (device == nil) {
        fail("production pipeline device");
    }
    NSString *source = @
        "#include <metal_stdlib>\n"
        "using namespace metal;\n"
        "struct VOut { float4 position [[position]]; };\n"
        "vertex VOut v_main(uint vid [[vertex_id]]) {\n"
        "  float2 p[3] = {float2(-1.0,-1.0), float2(3.0,-1.0), float2(-1.0,3.0)};\n"
        "  VOut o; o.position = float4(p[vid], 0.0, 1.0); return o;\n"
        "}\n"
        "fragment float4 f_main() { return float4(0.0, 1.0, 0.0, 1.0); }\n";
    NSError *error = nil;
    id<MTLLibrary> library = [device newLibraryWithSource:source options:nil error:&error];
    if (library == nil) {
        fail("production pipeline library");
    }
    id<MTLFunction> vs = [library newFunctionWithName:@"v_main"];
    id<MTLFunction> fs = [library newFunctionWithName:@"f_main"];
    MTLVertexDescriptor *vd = [MTLVertexDescriptor vertexDescriptor];
    uint8_t all_blend[0x24] = {0};
    uint8_t alpha_blend[0x24] = {0};
    all_blend[0x20] = all_blend[0x21] = all_blend[0x22] = all_blend[0x23] = 1;
    alpha_blend[0x23] = 1;
    void *all_pipeline = eu4_gfx_generic_pipeline_test(
        (__bridge void *)device, (__bridge void *)vs, (__bridge void *)fs,
        (__bridge void *)vd, (unsigned)MTLPixelFormatBGRA8Unorm, all_blend);
    void *alpha_pipeline = eu4_gfx_generic_pipeline_test(
        (__bridge void *)device, (__bridge void *)vs, (__bridge void *)fs,
        (__bridge void *)vd, (unsigned)MTLPixelFormatBGRA8Unorm, alpha_blend);
    int ok = all_pipeline != NULL && alpha_pipeline != NULL &&
             all_pipeline != alpha_pipeline;
    MTLTextureDescriptor *td =
        [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm
                                                           width:2 height:2 mipmapped:NO];
    td.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead;
    td.storageMode = MTLStorageModeShared;
    id<MTLCommandQueue> queue = [device newCommandQueue];
    for (int pass = 0; pass < 2 && ok; pass++) {
        id<MTLTexture> target = [device newTextureWithDescriptor:td];
        const uint8_t initial[4] = {10, 20, 30, 40};
        uint8_t fill[16];
        for (int i = 0; i < 4; i++) {
            memcpy(fill + i * 4, initial, 4);
        }
        [target replaceRegion:MTLRegionMake2D(0, 0, 2, 2) mipmapLevel:0
                    withBytes:fill bytesPerRow:2 * 4];
        id<MTLCommandBuffer> command = [queue commandBuffer];
        MTLRenderPassDescriptor *pass_desc = [MTLRenderPassDescriptor renderPassDescriptor];
        pass_desc.colorAttachments[0].texture = target;
        pass_desc.colorAttachments[0].loadAction = MTLLoadActionLoad;
        pass_desc.colorAttachments[0].storeAction = MTLStoreActionStore;
        id<MTLRenderCommandEncoder> encoder =
            [command renderCommandEncoderWithDescriptor:pass_desc];
        [encoder setRenderPipelineState:(__bridge id<MTLRenderPipelineState>)
                                            (pass == 0 ? all_pipeline : alpha_pipeline)];
        [encoder drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
        [encoder endEncoding];
        [command commit];
        [command waitUntilCompleted];
        if (command.status != MTLCommandBufferStatusCompleted) {
            ok = 0;
            break;
        }
        uint32_t pixels[4];
        read_target_texture(target, queue, 2 * 4, pixels);
        const uint8_t *pixel = (const uint8_t *)&pixels[0];
        const uint8_t want[4] = {
            (uint8_t)(pass == 0 ? 0 : 10), (uint8_t)(pass == 0 ? 255 : 20),
            (uint8_t)(pass == 0 ? 0 : 30), 255};
        for (int c = 0; c < 4; c++) {
            if (pixel[c] != want[c]) {
                fprintf(stderr, "FAIL: production mask pass %d channel %d got %u want %u\n",
                        pass, c, pixel[c], want[c]);
                ok = 0;
            }
        }
    }
    printf("production_pipeline=%d distinct=%d\n", ok,
           all_pipeline != alpha_pipeline);
    return ok ? 0 : 1;
}

static int run_draw_semantics(void) {
    int ok = 1;
    ok &= eu4_gfx_metal_topology(0) == 0; /* point */
    ok &= eu4_gfx_metal_topology(1) == 1; /* line */
    ok &= eu4_gfx_metal_topology(3) == 2; /* line strip */
    ok &= eu4_gfx_metal_topology(4) == 3; /* triangle */
    ok &= eu4_gfx_metal_topology(5) == 4; /* triangle strip */
    ok &= eu4_gfx_metal_topology(2) == -1; /* line loop refused */
    ok &= eu4_gfx_metal_topology(6) == -1;
    const uint8_t all[4] = {1, 1, 1, 1};
    const uint8_t none[4] = {0, 0, 0, 0};
    const uint8_t alpha[4] = {0, 0, 0, 1};
    const uint8_t rgb[4] = {1, 1, 1, 0};
    const uint8_t partial[4] = {1, 0, 1, 0};
    ok &= eu4_gfx_color_write_mask(all) == 15;
    ok &= eu4_gfx_color_write_mask(none) == 0;
    ok &= eu4_gfx_color_write_mask(alpha) == 1;
    ok &= eu4_gfx_color_write_mask(rgb) == 14;
    ok &= eu4_gfx_color_write_mask(partial) == 10;
    const uint16_t zero[3] = {0, 1, 2};
    const uint16_t positioned[3] = {2, 3, 4};
    const uint16_t sparse[3] = {0, 4, 7};
    const uint16_t max_index[1] = {65535};
    const uint16_t negative[3] = {0, 1, 2};
    int32_t first = 0, last = 0;
    ok &= eu4_gfx_indexed_range(zero, 3, 0, &first, &last) == 0 &&
          first == 0 && last == 2;
    ok &= eu4_gfx_indexed_range(zero, 3, 4, &first, &last) == 0 &&
          first == 4 && last == 6;
    ok &= eu4_gfx_indexed_range(positioned, 3, -2, &first, &last) == 0 &&
          first == 0 && last == 2;
    ok &= eu4_gfx_indexed_range(sparse, 3, 3, &first, &last) == 0 &&
          first == 3 && last == 10;
    ok &= eu4_gfx_indexed_range(max_index, 1, 0, &first, &last) == 0 &&
          first == 65535 && last == 65535;
    ok &= eu4_gfx_indexed_range(negative, 3, -1, &first, &last) == -1;
    ok &= eu4_gfx_replay_index(3, 4, 2) == 5;
    ok &= eu4_gfx_replay_index(2, -2, 0) == 0;
    uint64_t end = 0;
    /* Four 20-byte vertices, 8-byte UV at offset 12: ends exactly at 80. */
    ok &= eu4_gfx_attribute_end(80, 12, 20, 3, 8, &end) == 1 && end == 80;
    ok &= eu4_gfx_attribute_end(79, 12, 20, 3, 8, &end) == 0;
    /* Four 20-byte vertices, float3 at offset 0: ends exactly at 72. */
    ok &= eu4_gfx_attribute_end(72, 0, 20, 3, 12, &end) == 1 && end == 72;
    ok &= eu4_gfx_attribute_end(71, 0, 20, 3, 12, &end) == 0;
    /* Nonzero slot + attribute offset. */
    ok &= eu4_gfx_attribute_end(120, 40, 20, 3, 12, &end) == 1 && end == 112;
    /* Overflow is refused. */
    ok &= eu4_gfx_attribute_end(UINT64_MAX, UINT64_MAX - 4, 20, 1, 12, &end) == 0;
    /* Instance-rate final element: five instances, divisor 2 -> index 2. */
    ok &= eu4_gfx_attribute_end(48, 0, 16, 2, 16, &end) == 1 && end == 48;
    ok &= eu4_gfx_attribute_end(47, 0, 16, 2, 16, &end) == 0;
    /* Interleaved attributes with different element offsets. */
    ok &= eu4_gfx_attribute_end(80, 12, 20, 3, 8, &end) == 1 && end == 80;
    ok &= eu4_gfx_attribute_end(80, 0, 20, 3, 12, &end) == 1 && end == 72;
    ok &= eu4_gfx_gl_type_size(0x1406) == 4;
    ok &= eu4_gfx_gl_type_size(0x1403) == 2;
    ok &= eu4_gfx_gl_type_size(0x1401) == 1;
    ok &= eu4_gfx_gl_type_size(0x9999) == 0;
    int32_t range_first = 0, range_last = 0;
    /* Nonindexed, first 8 count 3, per-vertex. */
    ok &= eu4_gfx_capture_vertex_range(0, 0, 1, 8, 3, 0, 0,
                                       &range_first, &range_last) == 0 &&
          range_first == 8 && range_last == 10;
    /* Same draw, positive divisors fetch element 0 regardless of first. */
    ok &= eu4_gfx_capture_vertex_range(1, 0, 1, 8, 3, 0, 0,
                                       &range_first, &range_last) == 0 &&
          range_first == 0 && range_last == 0;
    ok &= eu4_gfx_capture_vertex_range(2, 0, 1, 8, 3, 0, 0,
                                       &range_first, &range_last) == 0 &&
          range_first == 0 && range_last == 0;
    /* Indexed [0,4,7] base 3 -> effective 3..10. */
    ok &= eu4_gfx_capture_vertex_range(0, 1, 1, 0, 3, 3, 10,
                                       &range_first, &range_last) == 0 &&
          range_first == 3 && range_last == 10;
    /* Five indexed instances, divisor 2 -> elements 0..2. */
    ok &= eu4_gfx_capture_vertex_range(2, 1, 5, 0, 3, 0, 0,
                                       &range_first, &range_last) == 0 &&
          range_first == 0 && range_last == 2;
    /* Overflow and invalid ranges fail without clamping. */
    ok &= eu4_gfx_capture_vertex_range(0, 0, 1, INT32_MAX, 2, 0, 0,
                                       &range_first, &range_last) == -1;
    ok &= eu4_gfx_capture_vertex_range(0, 0, 1, -1, 3, 0, 0,
                                       &range_first, &range_last) == -1;
    ok &= eu4_gfx_capture_attribute_count_ok(16, 16) == 1;
    ok &= eu4_gfx_capture_attribute_count_ok(17, 16) == 0;
    printf("draw_semantics=%d\n", ok);
    return ok ? 0 : 1;
}

static int shadow_matches(id<MTLTexture> shadow, id<MTLCommandQueue> queue,
                          const uint32_t pattern[16]) {
    if (shadow == nil) {
        return 0;
    }
    uint32_t converted[16];
    read_target_texture(shadow, queue, 4 * 4, converted);
    for (uint32_t y = 0; y < 4; y++) {
        for (uint32_t x = 0; x < 4; x++) {
            uint32_t want = pattern[(3 - y) * 4 + x] & 0x00ffffffu;
            uint32_t got = converted[y * 4 + x] & 0x00ffffffu;
            if (got != want) {
                fprintf(stderr, "FAIL: shadow row %u col %u got %08x want %08x\n",
                        y, x, got, want);
                return 0;
            }
        }
    }
    return 1;
}

static int run_sampled_render_target_orientation(void) {
    /* The menu labels are directly sampled render-target textures. Metal
       stores render targets top-left, while the engine's GL UVs address row 0
       as the bottom. Sampling the raw render target reflects the label.
       This test pins the reusable conversion: a render-target header resolves
       to a GL-row shadow, an ordinary texture is returned unchanged, and the
       GPU output is checked row by row. */
    boot();
    id<MTLDevice> device = (__bridge id<MTLDevice>)eu4_gfx_metal_device();
    if (device == nil) {
        fail("sampled RT device");
    }
    EU4GfxBackend *backend = (__bridge EU4GfxBackend *)eu4_gfx_backend_token();
    if (backend == nil || backend.queue == nil) {
        fail("sampled RT backend");
    }
    /* Distinct per-pixel colours: a coarse row swap or a linearly blurred
       copy both change at least one output pixel. */
    uint32_t pattern[16] = {
        0x000000ffu, 0x0000ff00u, 0x00ff0000u, 0x00ffffffu,
        0x0000ffffu, 0x00ff00ffu, 0x00ffff00u, 0x00000000u,
        0x00000080u, 0x00008000u, 0x00800000u, 0x00808080u,
        0x000080ffu, 0x008000ffu, 0x00ff0080u, 0x00c0c0c0u,
    };
    void *header = eu4_gfx_create_render_target_texture(NULL, 4, 4, 11, 0, 0);
    if (header == NULL) {
        fail("sampled RT header");
    }
    id<MTLTexture> target =
        (__bridge id<MTLTexture>)eu4_gfx_texture_for_header(header);
    if (target == nil) {
        fail("sampled RT texture");
    }
    [target replaceRegion:MTLRegionMake2D(0, 0, 4, 4) mipmapLevel:0
                withBytes:pattern bytesPerRow:4 * 4];
    if (backend.boundTextures == nil) {
        backend.boundTextures = [NSMutableArray array];
    }
    [backend.boundTextures removeAllObjects];
    [backend.boundTextures addObject:[NSValue valueWithPointer:header]];
    id<MTLCommandBuffer> flip_command = [backend.queue commandBuffer];
    char prepare_reason[256] = "";
    uint64_t sampling_token = 0;
    if (!eu4_gfx_prepare_sampled_textures((__bridge void *)flip_command,
                                          &sampling_token, prepare_reason,
                                          sizeof prepare_reason)) {
        fprintf(stderr, "FAIL: initial shadow conversion: %s\n", prepare_reason);
        return 1;
    }
    [flip_command commit];
    [flip_command waitUntilCompleted];
    if (flip_command.status != MTLCommandBufferStatusCompleted) {
        fail("sampled RT flip command");
    }
    void *sampled = eu4_gfx_texture_for_sampling(header, sampling_token);
    if (sampled == NULL || sampled == (__bridge void *)target) {
        fprintf(stderr, "FAIL: render target was not converted for sampling\n");
        return 1;
    }
    int flipped = shadow_matches((__bridge id<MTLTexture>)sampled,
                                 backend.queue, pattern);

    /* Repeated sampling without a write reuses the same committed shadow. */
    id<MTLCommandBuffer> repeat_command = [backend.queue commandBuffer];
    uint64_t repeat_token = 0;
    if (!eu4_gfx_prepare_sampled_textures((__bridge void *)repeat_command,
                                          &repeat_token, prepare_reason,
                                          sizeof prepare_reason)) {
        fail("repeated shadow prepare");
    }
    [repeat_command commit];
    [repeat_command waitUntilCompleted];
    void *repeated = eu4_gfx_texture_for_sampling(header, repeat_token);
    int repeated_ok = repeated == sampled &&
        shadow_matches((__bridge id<MTLTexture>)repeated, backend.queue, pattern);

    /* A write invalidates the shadow; the next sample converts the new rows. */
    uint32_t modified[16];
    for (int i = 0; i < 16; i++) {
        modified[i] = pattern[(i + 4) % 16];
    }
    [target replaceRegion:MTLRegionMake2D(0, 0, 4, 4) mipmapLevel:0
                withBytes:modified bytesPerRow:4 * 4];
    eu4_gfx_note_render_target_write(header);
    id<MTLCommandBuffer> modify_command = [backend.queue commandBuffer];
    uint64_t modify_token = 0;
    if (!eu4_gfx_prepare_sampled_textures((__bridge void *)modify_command,
                                          &modify_token, prepare_reason,
                                          sizeof prepare_reason)) {
        fail("modified shadow prepare");
    }
    [modify_command commit];
    [modify_command waitUntilCompleted];
    void *modified_sample = eu4_gfx_texture_for_sampling(header, modify_token);
    int modified_ok = shadow_matches((__bridge id<MTLTexture>)modified_sample,
                                     backend.queue, modified);

    /* An abandoned command must not make its shadow permanently valid. */
    uint32_t second[16];
    for (int i = 0; i < 16; i++) {
        second[i] = pattern[(i + 8) % 16];
    }
    [target replaceRegion:MTLRegionMake2D(0, 0, 4, 4) mipmapLevel:0
                withBytes:second bytesPerRow:4 * 4];
    eu4_gfx_note_render_target_write(header);
    uint64_t abandoned_before = eu4_gfx_sampling_conversion_count();
    @autoreleasepool {
        id<MTLCommandBuffer> abandoned = [backend.queue commandBuffer];
        uint64_t abandoned_token = 0;
        if (!eu4_gfx_prepare_sampled_textures((__bridge void *)abandoned,
                                              &abandoned_token, prepare_reason,
                                              sizeof prepare_reason)) {
            fail("abandoned shadow prepare");
        }
        /* Do not commit; release the command at the autorelease-pool boundary
           so a later command may receive the same address. */
    }
    uint64_t abandoned_after_a = eu4_gfx_sampling_conversion_count();
    id<MTLCommandBuffer> recovery = [backend.queue commandBuffer];
    uint64_t recovery_token = 0;
    if (!eu4_gfx_prepare_sampled_textures((__bridge void *)recovery,
                                          &recovery_token, prepare_reason,
                                          sizeof prepare_reason)) {
        fail("recovery shadow prepare");
    }
    uint64_t abandoned_after_b = eu4_gfx_sampling_conversion_count();
    [recovery commit];
    [recovery waitUntilCompleted];
    void *recovered = eu4_gfx_texture_for_sampling(header, recovery_token);
    int abandoned_ok = shadow_matches((__bridge id<MTLTexture>)recovered,
                                      backend.queue, second) &&
                       abandoned_after_a > abandoned_before &&
                       abandoned_after_b > abandoned_after_a;

    /* An older completed command must not promote its stale generation after a
       newer render-target write. Invalidate first so command A actually
       encodes a pending conversion; advance the generation before A completes;
       then prepare B for the newer generation. */
    uint32_t stale_pattern[16];
    for (int i = 0; i < 16; i++) {
        stale_pattern[i] = pattern[(i + 4) % 16];
    }
    [target replaceRegion:MTLRegionMake2D(0, 0, 4, 4) mipmapLevel:0
                withBytes:stale_pattern bytesPerRow:4 * 4];
    eu4_gfx_note_render_target_write(header);
    uint64_t conversions_before_a = eu4_gfx_sampling_conversion_count();
    id<MTLCommandBuffer> stale_command = [backend.queue commandBuffer];
    uint64_t stale_token = 0;
    if (!eu4_gfx_prepare_sampled_textures((__bridge void *)stale_command,
                                          &stale_token, prepare_reason,
                                          sizeof prepare_reason)) {
        fail("stale shadow prepare");
    }
    uint64_t conversions_after_a = eu4_gfx_sampling_conversion_count();

    uint32_t third[16];
    for (int i = 0; i < 16; i++) {
        third[i] = pattern[(i + 12) % 16];
    }
    [target replaceRegion:MTLRegionMake2D(0, 0, 4, 4) mipmapLevel:0
                withBytes:third bytesPerRow:4 * 4];
    eu4_gfx_note_render_target_write(header);
    id<MTLCommandBuffer> newer = [backend.queue commandBuffer];
    uint64_t newer_token = 0;
    if (!eu4_gfx_prepare_sampled_textures((__bridge void *)newer,
                                          &newer_token, prepare_reason,
                                          sizeof prepare_reason)) {
        fail("newer shadow prepare");
    }
    uint64_t conversions_after_b = eu4_gfx_sampling_conversion_count();
    [stale_command commit];
    [newer commit];
    [stale_command waitUntilCompleted];
    [newer waitUntilCompleted];
    void *newer_sample = eu4_gfx_texture_for_sampling(header, newer_token);
    int latest_ok = shadow_matches((__bridge id<MTLTexture>)newer_sample,
                                   backend.queue, third);

    /* A subsequent preparation for the unchanged newer generation must reuse
       the completed conversion rather than encode another one. */
    uint64_t conversions_before_c = eu4_gfx_sampling_conversion_count();
    id<MTLCommandBuffer> settled = [backend.queue commandBuffer];
    uint64_t settled_token = 0;
    if (!eu4_gfx_prepare_sampled_textures((__bridge void *)settled,
                                          &settled_token, prepare_reason,
                                          sizeof prepare_reason)) {
        fail("settled shadow prepare");
    }
    [settled commit];
    [settled waitUntilCompleted];
    uint64_t conversions_after_c = eu4_gfx_sampling_conversion_count();
    void *settled_sample = eu4_gfx_texture_for_sampling(header, settled_token);
    int settled_ok = shadow_matches((__bridge id<MTLTexture>)settled_sample,
                                    backend.queue, third) &&
                     conversions_after_c == conversions_before_c;
    int stale_ok = conversions_after_a > conversions_before_a &&
                   conversions_after_b > conversions_after_a &&
                   latest_ok && settled_ok;

    /* A depth render-target header has no colour conversion; fail closed. */
    uint8_t depth_header[0x44];
    memset(depth_header, 0, sizeof depth_header);
    depth_header[0x19] = 1;
    MTLTextureDescriptor *dd =
        [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatDepth32Float
                                                           width:4 height:4 mipmapped:NO];
    dd.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead;
    dd.storageMode = MTLStorageModeShared;
    id<MTLTexture> depth_texture = [device newTextureWithDescriptor:dd];
    backend.textures[[NSValue valueWithPointer:depth_header]] = depth_texture;
    [backend.boundTextures removeAllObjects];
    [backend.boundTextures addObject:[NSValue valueWithPointer:depth_header]];
    char failure_reason[256] = "";
    id<MTLCommandBuffer> failure_command = [backend.queue commandBuffer];
    uint64_t failure_token = 0;
    int refused = !eu4_gfx_prepare_sampled_textures((__bridge void *)failure_command,
                                                    &failure_token,
                                                    failure_reason,
                                                    sizeof failure_reason);

    /* A nonnull render-target header with no registered Metal texture must not
       silently substitute the generic fallback. */
    uint8_t missing_header[0x44];
    memset(missing_header, 0, sizeof missing_header);
    missing_header[0x19] = 1;
    [backend.boundTextures removeAllObjects];
    [backend.boundTextures addObject:[NSValue valueWithPointer:missing_header]];
    char missing_reason[256] = "";
    uint64_t missing_token = 0;
    id<MTLCommandBuffer> missing_command = [backend.queue commandBuffer];
    int missing_refused =
        !eu4_gfx_prepare_sampled_textures((__bridge void *)missing_command,
                                          &missing_token, missing_reason,
                                          sizeof missing_reason);
    int missing_ok = missing_refused &&
                     strstr(missing_reason, "no backing texture") != NULL &&
                     eu4_gfx_texture_for_sampling(missing_header, missing_token) == NULL &&
                     eu4_gfx_texture_for_sampling(NULL, 0) == NULL;
    int cache_ok = repeated_ok && modified_ok && abandoned_ok && stale_ok &&
                   refused && missing_ok;

    /* A previously correct ordinary texture keeps its exact rows. */
    uint8_t normal_header[0x44];
    memset(normal_header, 0, sizeof normal_header);
    MTLTextureDescriptor *nd =
        [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm
                                                           width:4 height:4 mipmapped:NO];
    nd.usage = MTLTextureUsageShaderRead;
    nd.storageMode = MTLStorageModeShared;
    id<MTLTexture> normal = [device newTextureWithDescriptor:nd];
    if (normal == nil) {
        fail("normal texture");
    }
    [normal replaceRegion:MTLRegionMake2D(0, 0, 4, 4) mipmapLevel:0
                withBytes:pattern bytesPerRow:4 * 4];
    if (backend.textures == nil) {
        backend.textures = [NSMutableDictionary dictionary];
    }
    backend.textures[[NSValue valueWithPointer:normal_header]] = normal;
    [backend.boundTextures removeAllObjects];
    [backend.boundTextures addObject:[NSValue valueWithPointer:normal_header]];
    id<MTLCommandBuffer> normal_command = [backend.queue commandBuffer];
    uint64_t normal_token = 0;
    if (!eu4_gfx_prepare_sampled_textures((__bridge void *)normal_command,
                                          &normal_token, prepare_reason,
                                          sizeof prepare_reason)) {
        fail("ordinary texture prepare");
    }
    [normal_command commit];
    [normal_command waitUntilCompleted];
    void *normal_sampled = eu4_gfx_texture_for_sampling(normal_header,
                                                        normal_token);
    int preserved = normal_sampled == (__bridge void *)normal;
    uint32_t normal_out[16];
    read_target_texture(normal, backend.queue, 4 * 4, normal_out);
    for (uint32_t i = 0; i < 16; i++) {
        if ((normal_out[i] & 0x00ffffffu) != (pattern[i] & 0x00ffffffu)) {
            preserved = 0;
        }
    }
    printf("sampled_rt_orientation=%d rt_flip=%d normal_preserved=%d "
           "shadow_cache=%d repeated=%d modified=%d abandoned=%d stale=%d "
           "refused=%d missing_storage=%d\n",
           flipped && preserved && cache_ok, flipped, preserved, cache_ok,
           repeated_ok, modified_ok, abandoned_ok, stale_ok, refused,
           missing_ok);
    return flipped && preserved && cache_ok ? 0 : 1;
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
    uint8_t red_bgr[4 * 4 * 3] = {};
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

/* ---- deterministic draw-stream benchmark (PR P0) ------------------------- */
/* Synthetic geometry isolates backend submission/allocation overhead. It is
 * not a gameplay benchmark and makes no EU4 performance claim. */

static uint64_t bench_now_ns(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC_RAW, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
}

static void bench_sha256_hex(const char *text, char out[65]) {
    uint8_t digest[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256(text, (CC_LONG)strlen(text), digest);
    static const char digits[] = "0123456789abcdef";
    for (int i = 0; i < CC_SHA256_DIGEST_LENGTH; i++) {
        out[i * 2] = digits[digest[i] >> 4];
        out[i * 2 + 1] = digits[digest[i] & 0xf];
    }
    out[CC_SHA256_DIGEST_LENGTH * 2] = 0;
}

static int bench_write_file(const char *path, const char *text) {
    FILE *file = fopen(path, "wb");
    if (file == NULL) {
        return 0;
    }
    size_t length = strlen(text);
    int ok = fwrite(text, 1, length, file) == length;
    fclose(file);
    return ok;
}

static void bench_put_u32(uint8_t *base, size_t offset, uint32_t value) {
    memcpy(base + offset, &value, 4);
}

static uint8_t g_bench_elements[2 * 0x28];
static char g_bench_cache_dir[256];

static uint8_t g_custom_element_tables[4][8 * 0x28];
static int g_custom_element_table_count;

/* Test-only synthetic cache: lets a host choose the MSL entry names, emitted
   attribute locations and engine element table. */
static void *make_generic_custom_shader(
    const char *vs_msl, const char *ps_msl, const char *vs_entry,
    const char *ps_entry, const char *locations_json,
    const char *attributes_json, const uint32_t *element_specs,
    int element_count) {
    char dir[] = "/tmp/eu4-gfx-custom-XXXXXX";
    if (mkdtemp(dir) == NULL) {
        fail("custom shader cache dir");
    }
    char vs_path[320], ps_path[320], cache_path[320];
    snprintf(vs_path, sizeof vs_path, "%s/custom.vert.metal", dir);
    snprintf(ps_path, sizeof ps_path, "%s/custom.frag.metal", dir);
    snprintf(cache_path, sizeof cache_path, "%s/custom-cache.json", dir);
    if (!bench_write_file(vs_path, vs_msl) || !bench_write_file(ps_path, ps_msl)) {
        fail("custom shader cache files");
    }
    char vs_input[256], ps_input[256];
    snprintf(vs_input, sizeof vs_input,
             "// synthetic custom compiler input (vertex) %s\n", vs_entry);
    snprintf(ps_input, sizeof ps_input,
             "// synthetic custom compiler input (pixel) %s\n", ps_entry);
    char vs_sha[65], ps_sha[65];
    bench_sha256_hex(vs_input, vs_sha);
    bench_sha256_hex(ps_input, ps_sha);
    char cache[4096];
    snprintf(cache, sizeof cache,
             "{\"schema\":\"gfx-shader-cache/v1\",\"stages\":{"
             "\"%s\":{\"stage\":\"vertex\",\"msl_file\":\"custom.vert.metal\","
             "\"msl_entry\":\"%s\",\"msl_uniform_buffer\":0,"
             "\"attribute_locations\":%s,\"attributes\":%s,"
             "\"uniforms\":[],\"samplers\":[],\"varyings\":[]},"
             "\"%s\":{\"stage\":\"pixel\",\"msl_file\":\"custom.frag.metal\","
             "\"msl_entry\":\"%s\",\"msl_uniform_buffer\":0,"
             "\"attribute_locations\":{},\"attributes\":[],"
             "\"uniforms\":[],\"samplers\":[],\"varyings\":[]}}}",
             vs_sha, vs_entry, locations_json, attributes_json,
             ps_sha, ps_entry);
    if (!bench_write_file(cache_path, cache)) {
        fail("custom shader cache json");
    }
    if (eu4_gfx_shader_load_cache(cache_path) <= 0) {
        fail("custom shader cache load");
    }
    void *shader = eu4_gfx_create_shader();
    if (shader == NULL ||
        eu4_gfx_add_vertex_shader(NULL, shader, vs_input) != 1 ||
        eu4_gfx_add_pixel_shader(NULL, shader, ps_input) != 1 ||
        eu4_gfx_link_shader(NULL, shader) != 1 ||
        !eu4_gfx_shader_is_generic(shader)) {
        fail("custom shader admission");
    }
    if (g_custom_element_table_count >= 4 || element_count > 8) {
        fail("custom element table capacity");
    }
    uint8_t *table = g_custom_element_tables[g_custom_element_table_count++];
    memset(table, 0, 8 * 0x28);
    for (int i = 0; i < element_count; i++) {
        uint8_t *entry = table + (size_t)i * 0x28;
        bench_put_u32(entry, 0x0c, element_specs[i * 4 + 0]); /* location */
        bench_put_u32(entry, 0x10, element_specs[i * 4 + 1]); /* components */
        bench_put_u32(entry, 0x14, element_specs[i * 4 + 2]); /* GL type */
        bench_put_u32(entry, 0x18, element_specs[i * 4 + 3]); /* offset */
        bench_put_u32(entry, 0x1c, 0);                         /* slot 0 */
    }
    void *elements = table;
    int32_t count = element_count;
    memcpy((uint8_t *)shader + 0x10, &elements, sizeof elements);
    memcpy((uint8_t *)shader + 0x18, &count, sizeof count);
    return shader;
}

static void *make_generic_bench_shader(void) {
    snprintf(g_bench_cache_dir, sizeof g_bench_cache_dir,
             "/tmp/eu4-gfx-bench-XXXXXX");
    if (mkdtemp(g_bench_cache_dir) == NULL) {
        fail("benchmark cache dir");
    }
    const char *vs_msl =
        "#include <metal_stdlib>\n"
        "using namespace metal;\n"
        "struct VIn { float4 position [[attribute(0)]]; float4 color [[attribute(1)]]; };\n"
        "struct VOut { float4 position [[position]]; float4 color [[user(locn0)]]; };\n"
        "vertex VOut bench_vs(VIn in [[stage_in]]) {\n"
        "    VOut o; o.position = in.position; o.color = in.color; return o;\n"
        "}\n";
    const char *ps_msl =
        "#include <metal_stdlib>\n"
        "using namespace metal;\n"
        "struct VOut { float4 position [[position]]; float4 color [[user(locn0)]]; };\n"
        "fragment float4 bench_ps(VOut in [[stage_in]]) { return in.color; }\n";
    char vs_path[320], ps_path[320], cache_path[320];
    snprintf(vs_path, sizeof vs_path, "%s/bench.vert.metal", g_bench_cache_dir);
    snprintf(ps_path, sizeof ps_path, "%s/bench.frag.metal", g_bench_cache_dir);
    snprintf(cache_path, sizeof cache_path, "%s/bench-cache.json", g_bench_cache_dir);
    if (!bench_write_file(vs_path, vs_msl) || !bench_write_file(ps_path, ps_msl)) {
        fail("benchmark shader cache files");
    }
    const char *vs_input =
        "// synthetic benchmark compiler input (vertex)\n"
        "attribute vec4 a_position; attribute vec4 a_color;\n";
    const char *ps_input =
        "// synthetic benchmark compiler input (pixel)\n"
        "varying vec4 v_color;\n";
    char vs_sha[65], ps_sha[65];
    bench_sha256_hex(vs_input, vs_sha);
    bench_sha256_hex(ps_input, ps_sha);
    char cache[4096];
    snprintf(cache, sizeof cache,
             "{\"schema\":\"gfx-shader-cache/v1\",\"stages\":{"
             "\"%s\":{\"stage\":\"vertex\",\"msl_file\":\"bench.vert.metal\","
             "\"msl_entry\":\"bench_vs\",\"msl_uniform_buffer\":0,"
             "\"attribute_locations\":{\"a_position\":0,\"a_color\":1},"
             "\"attributes\":[{\"name\":\"a_position\",\"type\":\"float4\"},"
             "{\"name\":\"a_color\",\"type\":\"float4\"}],"
             "\"uniforms\":[],\"samplers\":[],\"varyings\":[]},"
             "\"%s\":{\"stage\":\"pixel\",\"msl_file\":\"bench.frag.metal\","
             "\"msl_entry\":\"bench_ps\",\"msl_uniform_buffer\":0,"
             "\"attribute_locations\":{},\"attributes\":[],"
             "\"uniforms\":[],\"samplers\":[],\"varyings\":[]}}}",
             vs_sha, ps_sha);
    if (!bench_write_file(cache_path, cache)) {
        fail("benchmark shader cache json");
    }
    if (eu4_gfx_shader_load_cache(cache_path) <= 0) {
        fail("benchmark shader cache load");
    }
    void *shader = eu4_gfx_create_shader();
    if (shader == NULL ||
        eu4_gfx_add_vertex_shader(NULL, shader, vs_input) != 1 ||
        eu4_gfx_add_pixel_shader(NULL, shader, ps_input) != 1 ||
        eu4_gfx_link_shader(NULL, shader) != 1 ||
        !eu4_gfx_shader_is_generic(shader)) {
        fail("generic benchmark shader admission");
    }
    memset(g_bench_elements, 0, sizeof g_bench_elements);
    bench_put_u32(g_bench_elements, 0x0c, 0);       /* location a_position */
    bench_put_u32(g_bench_elements, 0x10, 4);       /* components */
    bench_put_u32(g_bench_elements, 0x14, 0x1406);  /* GL_FLOAT */
    bench_put_u32(g_bench_elements, 0x18, 0);       /* offset */
    bench_put_u32(g_bench_elements, 0x1c, 0);       /* slot 0 */
    bench_put_u32(g_bench_elements + 0x28, 0x0c, 1);      /* location a_color */
    bench_put_u32(g_bench_elements + 0x28, 0x10, 4);
    bench_put_u32(g_bench_elements + 0x28, 0x14, 0x1406);
    bench_put_u32(g_bench_elements + 0x28, 0x18, 16);     /* offset */
    bench_put_u32(g_bench_elements + 0x28, 0x1c, 0);
    void *elements = g_bench_elements;
    int32_t element_count = 2;
    memcpy((uint8_t *)shader + 0x10, &elements, sizeof elements);
    memcpy((uint8_t *)shader + 0x18, &element_count, sizeof element_count);
    return shader;
}

static void bench_vertices(uint8_t out[8 * 32]) {
    memset(out, 0, 8 * 32);
    const float positions[3][2] = {{-1.f, -1.f}, {3.f, -1.f}, {-1.f, 3.f}};
    for (int i = 0; i < 8; i++) {
        float *v = (float *)(out + i * 32);
        v[0] = positions[i % 3][0];
        v[1] = positions[i % 3][1];
        v[2] = 0.5f;
        v[3] = 1.f;
        if (i < 4) {
            v[4] = 1.f; /* red */
        } else {
            v[5] = 1.f; /* green */
        }
        v[7] = 1.f;
    }
}

static void bench_vertices_blend(uint8_t out[8 * 32]) {
    memset(out, 0, 8 * 32);
    const float positions[3][2] = {{-1.f, -1.f}, {3.f, -1.f}, {-1.f, 3.f}};
    for (int i = 0; i < 8; i++) {
        float *v = (float *)(out + i * 32);
        v[0] = positions[i % 3][0];
        v[1] = positions[i % 3][1];
        v[2] = 0.5f;
        v[3] = 1.f;
        if (i < 4) {
            v[4] = 1.f; /* opaque red */
            v[7] = 1.f;
        } else {
            v[5] = 1.f; /* half-transparent green */
            v[7] = 0.5f;
        }
    }
}

static void bench_install_generic(void *shader, void *vertex_header,
                                  void *index_header) {
    uint8_t *master = (uint8_t *)g_master;
    struct {
        void *master;
    } ctx = {g_master};
    uint32_t mode = 4, count = 3;
    memcpy(master + EU4_GFX_MASTER_MODE, &mode, 4);
    memcpy(master + EU4_GFX_MASTER_COUNT, &count, 4);
    void *headers[1] = {vertex_header};
    eu4_gfx_set_vertex_buffers(&ctx, 1, headers, 0, NULL, NULL);
    eu4_gfx_set_shader(&ctx, shader);
    eu4_gfx_set_index_buffer(&ctx, index_header);
    uint32_t rect[6] = {kSize, kSize, 0x3f800000u, 0, 0, 0};
    eu4_gfx_set_viewport(master, rect);
    uint32_t mask = 0x01010101u;
    memcpy(master + 0x2c, &mask, sizeof mask);
    uint32_t fill = 0x1b02;
    memcpy(master + 0x58, &fill, sizeof fill);
    master[0x38] = 0;
    master[0x60] = 0;
    /* Synthetic geometry is wound for the measuring stream; keep the engine's
       default raster orientation out of the backend-overhead measurement. */
    master[0x61] = 0;
}

static int run_benchmark(const char *kind, int draws, int frames) {
    if (draws < 1 || frames < 1 || draws > 100000 || frames > 1000) {
        fail("benchmark bounds");
    }
    eu4_gfx_perf_set_enabled(1);
    boot();
    id<MTLDevice> device = (__bridge id<MTLDevice>)eu4_gfx_metal_device();
    if (device == nil) {
        fail("benchmark device");
    }

    int generic = strcmp(kind, "simple") != 0;
    /* The engine's constant-slot header points at this storage for the whole
       run; keep it at function scope rather than in a setup branch. */
    float bench_matrix[16];
    uint8_t bench_constant[0x10];
    void *shader = NULL;
    void *vertex_header = NULL;
    void *index_header = NULL;
    uint8_t red_header[0x44], green_header[0x44];
    uint8_t red_bgr[4 * 4 * 3] = {}, green_bgr[4 * 4 * 3] = {};
    for (int i = 0; i < 4 * 4; i++) {
        red_bgr[i * 3 + 0] = 0;
        red_bgr[i * 3 + 1] = 0;
        red_bgr[i * 3 + 2] = 0xff;
        green_bgr[i * 3 + 0] = 0;
        green_bgr[i * 3 + 1] = 0xff;
        green_bgr[i * 3 + 2] = 0;
    }
    make_texture(4, 4, red_bgr, red_header);
    make_texture(4, 4, green_bgr, green_header);

    if (generic) {
        shader = make_generic_bench_shader();
        uint8_t vertices[8 * 32];
        bench_vertices(vertices);
        vertex_header = eu4_gfx_create_vertex_buffer(NULL, vertices, 8, 32, 0);
        const uint16_t indices[3] = {0, 1, 2};
        index_header = eu4_gfx_create_index_buffer(NULL, indices, 3);
        if (vertex_header == NULL || index_header == NULL) {
            fail("benchmark generic geometry");
        }
        bench_install_generic(shader, vertex_header, index_header);
    } else {
        shader = make_simple_shader();
        uint8_t quad[96];
        make_quad(quad);
        vertex_header = eu4_gfx_create_vertex_buffer(NULL, quad, 4, 24, 0);
        fill_matrix(bench_matrix);
        memset(bench_constant, 0, sizeof bench_constant);
        uint32_t words = 16;
        memcpy(bench_constant, &words, 4);
        void *constant_data = bench_matrix;
        memcpy(bench_constant + 8, &constant_data, sizeof constant_data);
        install_geometry(shader, vertex_header, bench_constant, red_header);
    }

    /* Present into an injected drawable so the frame boundary is observable
       without a window server. */
    MTLTextureDescriptor *desc =
        [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm
                                                           width:kSize
                                                          height:kSize
                                                       mipmapped:NO];
    desc.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead |
                 MTLTextureUsageShaderWrite;
    desc.storageMode = MTLStorageModeShared;
    id<MTLTexture> destination = [device newTextureWithDescriptor:desc];
    if (destination == nil) {
        fail("benchmark present drawable");
    }
    eu4_gfx_test_set_present_drawable((__bridge void *)destination);

    struct {
        void *master;
    } ctx = {g_master};
    uint8_t *master = (uint8_t *)g_master;
    const float black[4] = {0.f, 0.f, 0.f, 1.f};
    uint8_t default_blend[0x24];
    memcpy(default_blend, master + 0xc, sizeof default_blend);
    g_stopped = 0;
    const int warmup = 3;
    for (int frame = 0; frame < warmup + frames; frame++) {
        eu4_gfx_perf_reset();
        uint64_t wall_start = bench_now_ns();
        if (generic) {
            uint32_t full_view[6] = {kSize, kSize, 0x3f800000u, 0, 0, 0};
            eu4_gfx_set_viewport(master, full_view);
        }
        eu4_gfx_clear(&ctx, 7, black);
        if (g_stopped) {
            fail("benchmark clear stopped");
        }
        for (int i = 0; i < draws; i++) {
            eu4_gfx_draw_raw raw;
            memset(&raw, 0, sizeof raw);
            raw.rdi = (uint64_t)(uintptr_t)&ctx;
            raw.retaddr = (uint64_t)(uintptr_t)(g_image + kCallerRva);
            if (generic) {
                raw.which = 2;
                raw.rsi = 3;
                /* Signed base vertices: alternate red (0) and green (4). */
                raw.rcx = (i % 2 == 0) ? 0 : 4;
                if (strcmp(kind, "mixed") == 0) {
                    if (i % 3 == 0) {
                        uint32_t enabled = 1;
                        memcpy(master + 0xc, &enabled, 4);
                    } else {
                        memcpy(master + 0xc, default_blend, 0x24);
                    }
                    master[0x61] = (uint8_t)((i / 2) % 3 == 0 ? 1 : 0);
                    uint32_t mask = (i % 2 == 0) ? 0x01010101u : 0x00ff00ffu;
                    memcpy(master + 0x2c, &mask, 4);
                    /* Viewport changes are Metal encoder state, not a pass
                       boundary. Alternate between full and inset viewports. */
                    uint32_t view[6] = {(i % 2 == 0) ? (uint32_t)kSize : 32,
                                        (i % 2 == 0) ? (uint32_t)kSize : 32,
                                        0x3f800000u, 0, 0, 0};
                    eu4_gfx_set_viewport(master, view);
                }
            } else {
                raw.which = 1;
                raw.rsi = 4;
                void *texture = (i % 2 == 0) ? red_header : green_header;
                eu4_gfx_set_textures(&ctx, 1, &texture, 1, 0, NULL);
            }
            if (eu4_gfx_draw_capture(&raw) != 1) {
                fail("benchmark draw was not handled");
            }
        }
        if (!generic) {
            void *texture = red_header;
            eu4_gfx_set_textures(&ctx, 1, &texture, 1, 0, NULL);
        } else {
            memcpy(master + 0xc, default_blend, 0x24);
            uint32_t mask = 0x01010101u;
            memcpy(master + 0x2c, &mask, 4);
        }
        if (eu4_gfx_present(g_master) != 1 || g_stopped) {
            fail("benchmark present failed");
        }
        uint64_t wall_ns = bench_now_ns() - wall_start;
        if (!eu4_gfx_perf_wait_gpu_timing(3000)) {
            fail("benchmark GPU timing completion");
        }
        if (frame < warmup) {
            continue;
        }
        char perf[4096];
        if (eu4_gfx_perf_snapshot_json(perf, sizeof perf) == 0) {
            fail("benchmark snapshot");
        }
        printf("BENCH {\"schema\":\"gfx-bench-frame/v1\",\"kind\":\"%s\","
               "\"draws\":%d,\"frame\":%d,\"wall_ns\":%llu,\"perf\":%s}\n",
               kind, draws, frame - warmup, (unsigned long long)wall_ns, perf);
        fflush(stdout);
    }
    return 0;
}

static int run_indexed_resource(void) {
    /* The indexed draw must consume the engine's own Metal buffer: no CPU copy
       and no temporary Metal allocation. An update must change the resource the
       next draw sees, and a recycled header must not resolve to the old one. */
    eu4_gfx_perf_set_enabled(1);
    eu4_gfx_perf_reset();
    boot();
    void *shader = make_generic_bench_shader();
    uint8_t vertices[8 * 32];
    bench_vertices(vertices);
    void *vertex_header = eu4_gfx_create_vertex_buffer(NULL, vertices, 8, 32, 0);
    const uint16_t red_indices[3] = {0, 1, 2};
    void *index_header = eu4_gfx_create_index_buffer(NULL, red_indices, 3);
    if (vertex_header == NULL || index_header == NULL) {
        fail("indexed-resource geometry");
    }
    bench_install_generic(shader, vertex_header, index_header);
    struct {
        void *master;
    } ctx = {g_master};
    const float black[4] = {0.f, 0.f, 0.f, 1.f};
    eu4_gfx_clear(&ctx, 7, black);

    void *first_buffer = eu4_gfx_index_buffer_for_header(index_header);
    if (first_buffer == NULL || !eu4_gfx_index_buffer_shared(index_header)) {
        fail("indexed-resource accessor");
    }

    eu4_gfx_draw_raw raw;
    memset(&raw, 0, sizeof raw);
    raw.which = 2;
    raw.rdi = (uint64_t)(uintptr_t)&ctx;
    raw.rsi = 3;
    raw.rcx = 0;
    raw.retaddr = (uint64_t)(uintptr_t)(g_image + kCallerRva);
    if (eu4_gfx_draw_capture(&raw) != 1) {
        fail("first indexed draw");
    }
    if (eu4_gfx_draw_capture(&raw) != 1) {
        fail("second indexed draw");
    }
    int reuse = eu4_gfx_index_buffer_for_header(index_header) == first_buffer;
    size_t length = 0;
    uint8_t *pixels = read_target(&length);
    expect_colour(pixels, 0, 0, 0xff, "indexed base 0");
    free(pixels);

    const uint16_t green_indices[3] = {4, 5, 6};
    eu4_gfx_update_index_buffer(NULL, index_header, green_indices, 3);
    void *updated_buffer = eu4_gfx_index_buffer_for_header(index_header);
    int update_replaced = updated_buffer != NULL && updated_buffer != first_buffer;
    if (eu4_gfx_draw_capture(&raw) != 1) {
        fail("draw after index update");
    }
    pixels = read_target(&length);
    expect_colour(pixels, 0, 0xff, 0, "indexed update");
    free(pixels);

    eu4_gfx_perf_snapshot perf;
    eu4_gfx_perf_read(&perf);
    int zero_temp = perf.index_temp_allocs == 0 && perf.index_temp_bytes == 0 &&
                    perf.draws_encoded == 3;

    eu4_gfx_destroy_index_buffer(index_header);
    int stale_null = eu4_gfx_index_buffer_for_header(index_header) == NULL;
    void *recycled_header = eu4_gfx_create_index_buffer(NULL, red_indices, 3);
    void *recycled_buffer = eu4_gfx_index_buffer_for_header(recycled_header);
    int recycled_fresh = recycled_header != NULL && recycled_buffer != NULL &&
                         recycled_buffer != updated_buffer;
    if (recycled_header == NULL) {
        fail("indexed-resource recycle");
    }
    eu4_gfx_set_index_buffer(&ctx, recycled_header);
    if (eu4_gfx_draw_capture(&raw) != 1) {
        fail("draw after index recycle");
    }
    pixels = read_target(&length);
    expect_colour(pixels, 0, 0, 0xff, "indexed recycle");
    free(pixels);

    raw.rcx = -1;
    char diagnostic[256];
    eu4_gfx_simple_draw_state state;
    int negative_refused =
        eu4_gfx_draw_route_live(&raw, &state, diagnostic, sizeof diagnostic) ==
            EU4_GFX_DRAW_ROUTE_UNSUPPORTED &&
        strstr(diagnostic, "negative effective vertex") != NULL;
    raw.rcx = 0;
    const uint16_t outside_indices[3] = {100, 101, 102};
    eu4_gfx_update_index_buffer(NULL, recycled_header, outside_indices, 3);
    int bounds_refused =
        eu4_gfx_draw_route_live(&raw, &state, diagnostic, sizeof diagnostic) ==
            EU4_GFX_DRAW_ROUTE_UNSUPPORTED &&
        strstr(diagnostic, "past the bound vertex attributes") != NULL;

    int ok = reuse && update_replaced && zero_temp && stale_null && recycled_fresh &&
             negative_refused && bounds_refused;
    printf("indexed_resource=%d reuse=%d update=%d recycle=%d negative_refused=%d "
           "bounds_refused=%d zero_alloc=%d\n",
           ok, reuse, update_replaced, recycled_fresh, negative_refused,
           bounds_refused, zero_temp);
    return ok ? 0 : 1;
}

typedef struct {
    uint32_t width;
    uint32_t height;
} geometry_publish_args;

static void *publish_transition_thread(void *argument) {
    geometry_publish_args *args = (geometry_publish_args *)argument;
    eu4_gfx_publish_geometry_transition(args->width, args->height);
    return NULL;
}

static void *publish_physical_thread(void *argument) {
    geometry_publish_args *args = (geometry_publish_args *)argument;
    eu4_gfx_publish_physical_geometry(args->width, args->height);
    return NULL;
}

static int run_scissor_handoff(void) {
    /* A partial generic scissor must not survive into the admitted Simple or
       Sprite pass when they share the encoder. */
    eu4_gfx_perf_set_enabled(1);
    boot();
    EU4GfxBackend *backend = (__bridge EU4GfxBackend *)eu4_gfx_backend_token();
    uint8_t *master = (uint8_t *)g_master;
    struct {
        void *master;
    } ctx = {g_master};

    void *simple_shader = make_simple_shader();
    void *sprite_shader = eu4_gfx_create_shader();
    if (sprite_shader == NULL ||
        !eu4_gfx_shader_test_compile_identity(sprite_shader,
                                              EU4_GFX_SHADER_SPRITE_IDENTITY)) {
        fail("scissor sprite shader");
    }
    void *generic_shader = make_generic_bench_shader();

    uint8_t generic_vertices[8 * 32];
    bench_vertices(generic_vertices);
    void *generic_vb =
        eu4_gfx_create_vertex_buffer(NULL, generic_vertices, 8, 32, 0);
    const uint16_t indices[3] = {0, 1, 2};
    void *index_header = eu4_gfx_create_index_buffer(NULL, indices, 3);
    if (generic_vb == NULL || index_header == NULL) {
        fail("scissor generic geometry");
    }
    bench_install_generic(generic_shader, generic_vb, index_header);

    uint8_t quad[96];
    make_quad(quad);
    void *simple_vb = eu4_gfx_create_vertex_buffer(NULL, quad, 4, 24, 0);
    static float sprite_matrix[24];
    memset(sprite_matrix, 0, sizeof sprite_matrix);
    sprite_matrix[0] = sprite_matrix[5] = sprite_matrix[10] = sprite_matrix[15] = 1.0f;
    sprite_matrix[16] = sprite_matrix[17] = sprite_matrix[18] = sprite_matrix[19] = 1.0f;
    static uint8_t sprite_quad[4 * 20];
    memset(sprite_quad, 0, sizeof sprite_quad);
    const float sprite_positions[4][2] = {
        {-0.5f, -0.5f}, {0.5f, -0.5f}, {-0.5f, 0.5f}, {0.5f, 0.5f}};
    for (int i = 0; i < 4; i++) {
        float *v = (float *)(sprite_quad + i * 20);
        v[0] = sprite_positions[i][0];
        v[1] = sprite_positions[i][1];
        v[2] = 0.5f;
        v[3] = (i == 1 || i == 3) ? 1.0f : 0.0f;
        v[4] = (i >= 2) ? 1.0f : 0.0f;
    }
    void *sprite_vb = eu4_gfx_create_vertex_buffer(NULL, sprite_quad, 4, 20, 0);
    if (simple_vb == NULL || sprite_vb == NULL) {
        fail("scissor legacy vertex buffers");
    }

    uint8_t green_bgr[4 * 4 * 3] = {};
    uint8_t blue_bgr[4 * 4 * 3] = {};
    for (int i = 0; i < 4 * 4; i++) {
        green_bgr[i * 3 + 1] = 0xff;
        blue_bgr[i * 3 + 0] = 0xff;
    }
    uint8_t green_header[0x44], blue_header[0x44];
    make_texture(4, 4, green_bgr, green_header);
    make_texture(4, 4, blue_bgr, blue_header);

    const float black[4] = {0.f, 0.f, 0.f, 1.f};
    eu4_gfx_clear(&ctx, 7, black);
    eu4_gfx_perf_reset();

    int16_t partial[4] = {0, 0, 32, 32};
    eu4_gfx_set_scissor_rectangle(&ctx, partial);
    eu4_gfx_draw_raw raw;
    memset(&raw, 0, sizeof raw);
    raw.rdi = (uint64_t)(uintptr_t)&ctx;
    raw.retaddr = (uint64_t)(uintptr_t)(g_image + kCallerRva);
    raw.which = 2;
    raw.rsi = 3;
    if (eu4_gfx_draw_capture(&raw) != 1) {
        fail("scissor generic draw");
    }

    int16_t full[4] = {0, 0, 64, 64};
    eu4_gfx_set_scissor_rectangle(&ctx, full);
    /* Generic setup disabled culling for synthetic geometry; the legacy passes
       admit only the engine default raster snapshot. Restore it. */
    uint32_t raster_back = 0x405, raster_fill = 0x1b02, raster_winding = 0x900;
    memcpy(master + 0x54, &raster_back, 4);
    memcpy(master + 0x58, &raster_fill, 4);
    memcpy(master + 0x5c, &raster_winding, 4);
    master[0x60] = 0;
    master[0x61] = 1;

    /* Simple draw: full-target green. */
    uint8_t simple_constant[0x10];
    memset(simple_constant, 0, sizeof simple_constant);
    uint32_t words = 16;
    memcpy(simple_constant, &words, 4);
    float simple_matrix[16];
    fill_matrix(simple_matrix);
    void *simple_data = simple_matrix;
    memcpy(simple_constant + 8, &simple_data, sizeof simple_data);
    install_geometry(simple_shader, simple_vb, simple_constant, green_header);
    raw.which = 1;
    raw.rsi = 4;
    raw.rdx = 0;
    if (eu4_gfx_draw_capture(&raw) != 1) {
        fail("scissor simple draw");
    }

    /* Sprite draw: centered blue quad, distinct raster/blend contract. */
    uint8_t sprite_constant[0x10];
    memset(sprite_constant, 0, sizeof sprite_constant);
    uint32_t sprite_words = 24;
    memcpy(sprite_constant, &sprite_words, 4);
    void *sprite_data = sprite_matrix;
    memcpy(sprite_constant + 8, &sprite_data, sizeof sprite_data);
    install_geometry(sprite_shader, sprite_vb, sprite_constant, blue_header);
    uint32_t enabled = 1, src = 0x0302, dst = 0x0303, add = 0x8006;
    uint32_t one = 1, zero = 0, compare = 0x206, mask = 0x01010101;
    memcpy(master + 0xc, &enabled, 4);
    memcpy(master + 0x10, &src, 4);
    memcpy(master + 0x14, &dst, 4);
    memcpy(master + 0x18, &add, 4);
    memcpy(master + 0x1c, &one, 4);
    memcpy(master + 0x20, &zero, 4);
    memcpy(master + 0x24, &add, 4);
    memcpy(master + 0x28, &compare, 4);
    memcpy(master + 0x2c, &mask, 4);
    if (eu4_gfx_draw_capture(&raw) != 1) {
        fail("scissor sprite draw");
    }

    eu4_gfx_perf_snapshot perf;
    eu4_gfx_perf_read(&perf);
    int one_encoder = perf.draws_encoded == 3 &&
                      perf.render_encoders_created == 1 &&
                      perf.render_encoders_reused >= 2;
    size_t length = 0;
    uint8_t *pixels = read_target(&length);
    const uint8_t *corner = pixels + ((size_t)63 * kSize + 63) * 4;
    /* Sprite quad spans 16..48; this pixel is inside it but outside the old
       partial scissor. */
    const uint8_t *center = pixels + ((size_t)44 * kSize + 44) * 4;
    /* Outside the old partial scissor: the Simple pass must have drawn (the
       stale-scissor failure leaves this pixel at the generic/clear colour). */
    int corner_green = corner[1] > 200;
    int center_blue = center[0] > 200 && center[0] > center[2];
    free(pixels);
    int ok = one_encoder && corner_green && center_blue;
    printf("scissor_handoff=%d one_encoder=%d corner_green=%d center_blue=%d "
           "corner=(%u,%u,%u,%u) center=(%u,%u,%u,%u)\n",
           ok, one_encoder, corner_green, center_blue,
           corner[0], corner[1], corner[2], corner[3],
           center[0], center[1], center[2], center[3]);
    return ok ? 0 : 1;
}

static int run_encoder_cleanup(void) {
    /* A failed generic draw must close the pass it opened and leave earlier
       encodes in the batch valid for later draws and presentation. */
    eu4_gfx_perf_set_enabled(1);
    boot();
    EU4GfxBackend *backend = (__bridge EU4GfxBackend *)eu4_gfx_backend_token();
    uint8_t *master = (uint8_t *)g_master;
    struct {
        void *master;
    } ctx = {g_master};
    void *simple_shader = make_simple_shader();
    uint8_t quad[96];
    make_quad(quad);
    void *simple_vb = eu4_gfx_create_vertex_buffer(NULL, quad, 4, 24, 0);
    uint8_t green_bgr[4 * 4 * 3] = {}, blue_bgr[4 * 4 * 3] = {};
    for (int i = 0; i < 4 * 4; i++) {
        green_bgr[i * 3 + 1] = 0xff;
        blue_bgr[i * 3 + 0] = 0xff;
    }
    uint8_t green_header[0x44], blue_header[0x44];
    make_texture(4, 4, green_bgr, green_header);
    make_texture(4, 4, blue_bgr, blue_header);
    float matrix[16];
    fill_matrix(matrix);
    uint8_t constant[0x10];
    memset(constant, 0, sizeof constant);
    uint32_t words = 16;
    memcpy(constant, &words, 4);
    void *constant_data = matrix;
    memcpy(constant + 8, &constant_data, sizeof constant_data);
    install_geometry(simple_shader, simple_vb, constant, green_header);
    const float black[4] = {0.f, 0.f, 0.f, 1.f};
    eu4_gfx_clear(&ctx, 7, black);
    eu4_gfx_draw_raw raw = draw_frame(&ctx);
    if (eu4_gfx_draw_capture(&raw) != 1) {
        fail("cleanup first draw");
    }

    /* A generic shader whose Metal entry is sprite_vs, but with the synthetic
       two-attribute layout, reaches the sprite-uniform fallback after the pass
       has opened. The 16-dword constant slot is insufficient for sprite's 24. */
    const char *vs_msl =
        "#include <metal_stdlib>\n"
        "using namespace metal;\n"
        "struct VIn { float4 position [[attribute(0)]]; float4 color [[attribute(1)]]; };\n"
        "struct VOut { float4 position [[position]]; float4 color [[user(locn0)]]; };\n"
        "vertex VOut sprite_vs(VIn in [[stage_in]]) {\n"
        "    VOut o; o.position = in.position; o.color = in.color; return o;\n"
        "}\n";
    const char *ps_msl =
        "#include <metal_stdlib>\n"
        "using namespace metal;\n"
        "struct VOut { float4 position [[position]]; float4 color [[user(locn0)]]; };\n"
        "fragment float4 sprite_fs(VOut in [[stage_in]]) { return in.color; }\n";
    const char *locations = "{\"a_position\":0,\"a_color\":1}";
    const char *attributes =
        "[{\"name\":\"a_position\",\"type\":\"float4\"},"
        "{\"name\":\"a_color\",\"type\":\"float4\"}]";
    uint32_t elements[8] = {0, 4, 0x1406, 0, 1, 4, 0x1406, 16};
    void *cleanup_shader = make_generic_custom_shader(
        vs_msl, ps_msl, "sprite_vs", "sprite_fs", locations, attributes,
        elements, 2);
    uint8_t vertices[8 * 32];
    bench_vertices(vertices);
    void *bench_vb = eu4_gfx_create_vertex_buffer(NULL, vertices, 8, 32, 0);
    uint32_t mode = 4, count = 3;
    memcpy(master + EU4_GFX_MASTER_MODE, &mode, 4);
    memcpy(master + EU4_GFX_MASTER_COUNT, &count, 4);
    void *headers[1] = {bench_vb};
    eu4_gfx_set_vertex_buffers(&ctx, 1, headers, 0, NULL, NULL);
    eu4_gfx_set_shader(&ctx, cleanup_shader);
    raw.which = 1;
    raw.rsi = 3;
    raw.rdx = 0;
    char diagnostic[256];
    eu4_gfx_simple_draw_state state;
    int refused =
        eu4_gfx_draw_route_live(&raw, &state, diagnostic, sizeof diagnostic) !=
        EU4_GFX_DRAW_ROUTE_HANDLED;
    int diagnostic_ok =
        strstr(diagnostic, "synthetic stage has no engine uniform block") != NULL;
    int no_active_encoder = eu4_gfx_batch_render_pass_active() == 0;
    eu4_gfx_perf_snapshot after_failure;
    eu4_gfx_perf_read(&after_failure);
    int preserved_before = after_failure.draws_encoded == 1;
    int failed = refused && diagnostic_ok && no_active_encoder && preserved_before;

    /* Subsequent successful draw: blue full-target Simple. */
    install_geometry(simple_shader, simple_vb, constant, blue_header);
    raw.which = 1;
    raw.rsi = 4;
    raw.rdx = 0;
    if (eu4_gfx_draw_capture(&raw) != 1) {
        fail("cleanup subsequent draw");
    }
    eu4_gfx_perf_snapshot perf;
    eu4_gfx_perf_read(&perf);
    int preserved = perf.draws_encoded == 2;

    MTLTextureDescriptor *desc =
        [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm
                                                           width:kSize height:kSize mipmapped:NO];
    desc.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead |
                 MTLTextureUsageShaderWrite;
    desc.storageMode = MTLStorageModeShared;
    id<MTLTexture> destination = [backend.device newTextureWithDescriptor:desc];
    if (destination == nil) {
        fail("cleanup present target");
    }
    eu4_gfx_test_set_present_drawable((__bridge void *)destination);
    int presented = eu4_gfx_present(g_master) == 1 && !g_stopped;
    uint8_t pixel[4] = {0, 0, 0, 0};
    [destination getBytes:pixel bytesPerRow:4
               fromRegion:MTLRegionMake2D(kSize / 2, kSize / 2, 1, 1)
              mipmapLevel:0];
    eu4_gfx_test_set_present_drawable(NULL);
    /* The second Simple draw must dominate; texture alpha leaves a small
       green contribution because the clear/first draw shared the attachment. */
    int pixels_ok = pixel[0] > 200 && pixel[0] > pixel[1];
    int ok = failed && preserved && presented && pixels_ok;
    printf("encoder_cleanup=%d failed=%d refused=%d diagnostic=%d "
           "no_encoder=%d preserved_before=%d preserved=%d presented=%d "
           "pixels=%d rgba=(%u,%u,%u,%u)\n",
           ok, failed, refused, diagnostic_ok, no_active_encoder,
           preserved_before, preserved, presented, pixels_ok,
           pixel[0], pixel[1], pixel[2], pixel[3]);
    return ok ? 0 : 1;
}

static int file_contains(const char *dir, const char *needle) {
    DIR *handle = opendir(dir);
    if (handle == NULL) {
        return 0;
    }
    int found = 0;
    struct dirent *entry = NULL;
    while (!found && (entry = readdir(handle)) != NULL) {
        if (strncmp(entry->d_name, "elements-", 9) != 0) {
            continue;
        }
        char path[512];
        snprintf(path, sizeof path, "%s/%s", dir, entry->d_name);
        FILE *file = fopen(path, "rb");
        if (file == NULL) {
            continue;
        }
        char text[4096];
        size_t length = fread(text, 1, sizeof text - 1, file);
        fclose(file);
        text[length] = 0;
        found = strstr(text, needle) != NULL;
    }
    closedir(handle);
    return found;
}

static int run_element_locations(void) {
    /* Cached and fallback layouts must preserve nonzero engine attribute
       locations in the element trace. */
    eu4_gfx_perf_set_enabled(1);
    boot();
    EU4GfxBackend *backend = (__bridge EU4GfxBackend *)eu4_gfx_backend_token();
    uint8_t *master = (uint8_t *)g_master;
    struct {
        void *master;
    } ctx = {g_master};
    const char *vs_msl =
        "#include <metal_stdlib>\n"
        "using namespace metal;\n"
        "struct VIn { float4 a [[attribute(0)]]; float4 c [[attribute(2)]];"
        " float4 d [[attribute(5)]]; };\n"
        "struct VOut { float4 position [[position]]; float4 color [[user(locn0)]]; };\n"
        "vertex VOut multi_vs(VIn in [[stage_in]]) {\n"
        "    VOut o; o.position = in.a; o.color = in.c + in.d; return o;\n"
        "}\n";
    const char *ps_msl =
        "#include <metal_stdlib>\n"
        "using namespace metal;\n"
        "struct VOut { float4 position [[position]]; float4 color [[user(locn0)]]; };\n"
        "fragment float4 multi_fs(VOut in [[stage_in]]) { return in.color; }\n";
    const char *locations = "{\"a_pos\":0,\"a_col\":2,\"a_aux\":5}";
    const char *attributes =
        "[{\"name\":\"a_pos\",\"type\":\"float4\"},"
        "{\"name\":\"a_col\",\"type\":\"float4\"},"
        "{\"name\":\"a_aux\",\"type\":\"float4\"}]";
    uint32_t float_elements[12] = {0, 4, 0x1406, 0, 2, 4, 0x1406, 16,
                                   5, 4, 0x1406, 32};
    void *cached_shader = make_generic_custom_shader(
        vs_msl, ps_msl, "multi_vs", "multi_fs", locations, attributes,
        float_elements, 3);
    uint8_t vertices[4 * 48];
    memset(vertices, 0, sizeof vertices);
    const float positions[3][2] = {{-1.f, -1.f}, {3.f, -1.f}, {-1.f, 3.f}};
    for (int i = 0; i < 3; i++) {
        float *v = (float *)(vertices + i * 48);
        v[0] = positions[i][0];
        v[1] = positions[i][1];
        v[2] = 0.5f;
        v[3] = 1.f;
        v[4] = 0.25f;
        v[5] = 0.25f;
        v[8] = 0.25f;
        v[9] = 0.25f;
    }
    void *vb = eu4_gfx_create_vertex_buffer(NULL, vertices, 4, 48, 0);
    uint32_t mode = 4, count = 3;
    memcpy(master + EU4_GFX_MASTER_MODE, &mode, 4);
    memcpy(master + EU4_GFX_MASTER_COUNT, &count, 4);
    void *headers[1] = {vb};
    eu4_gfx_set_vertex_buffers(&ctx, 1, headers, 0, NULL, NULL);
    eu4_gfx_set_shader(&ctx, cached_shader);
    uint8_t *master_ptr = (uint8_t *)g_master;
    uint32_t element_rect[6] = {kSize, kSize, 0x3f800000u, 0, 0, 0};
    eu4_gfx_set_viewport(master_ptr, element_rect);
    uint32_t element_mask = 0x01010101u;
    memcpy(master_ptr + 0x2c, &element_mask, 4);
    float color[4] = {0.f, 0.f, 0.f, 1.f};
    eu4_gfx_clear(&ctx, 7, color);
    eu4_gfx_perf_reset();
    setenv("EU4_GFX_ELEMENT_TRACE", "1", 1);
    eu4_gfx_draw_raw raw = draw_frame(&ctx);
    raw.which = 1;
    raw.rsi = 3;
    if (eu4_gfx_draw_capture(&raw) != 1) {
        fail("element cached draw");
    }
    uint32_t cached[8] = {0};
    int cached_count = eu4_gfx_generic_last_element_count();
    for (int i = 0; i < cached_count && i < 8; i++) {
        eu4_gfx_generic_last_element_location(i, &cached[i]);
    }
    int cached_ok = cached_count == 3 && cached[0] == 0 && cached[1] == 2 &&
                    cached[2] == 5;
    int trace_ok = file_contains(g_event_dir, "\"location\":0") &&
                   file_contains(g_event_dir, "\"location\":2") &&
                   file_contains(g_event_dir, "\"location\":5");

    /* Fallback path: integer attribute conversion builds the descriptor in the
       uncached path; locations must still match. */
    uint32_t mixed_elements[12] = {0, 4, 0x1402, 0, 2, 4, 0x1406, 16,
                                   5, 4, 0x1406, 32};
    void *fallback_shader = make_generic_custom_shader(
        vs_msl, ps_msl, "multi_vs", "multi_fs", locations, attributes,
        mixed_elements, 3);
    eu4_gfx_set_shader(&ctx, fallback_shader);
    if (eu4_gfx_draw_capture(&raw) != 1) {
        fail("element fallback draw");
    }
    uint32_t fallback[8] = {0};
    int fallback_count = eu4_gfx_generic_last_element_count();
    for (int i = 0; i < fallback_count && i < 8; i++) {
        eu4_gfx_generic_last_element_location(i, &fallback[i]);
    }
    int fallback_ok = fallback_count == 3 && fallback[0] == 0 &&
                      fallback[1] == 2 && fallback[2] == 5;

    /* Dynamic slot offsets must not create pipeline variants. Warm the
       cached shader/pipeline, then reset and draw at two offsets. */
    eu4_gfx_set_shader(&ctx, cached_shader);
    if (eu4_gfx_draw_capture(&raw) != 1) {
        fail("element cached warm draw");
    }
    eu4_gfx_perf_reset();
    if (eu4_gfx_draw_capture(&raw) != 1) {
        fail("element cached offset-0 draw");
    }
    uint32_t slot_offset = 16;
    memcpy(master_ptr + 0x150, &slot_offset, 4);
    if (eu4_gfx_draw_capture(&raw) != 1) {
        fail("element cached offset-16 draw");
    }
    slot_offset = 0;
    memcpy(master_ptr + 0x150, &slot_offset, 4);
    eu4_gfx_perf_snapshot perf;
    eu4_gfx_perf_read(&perf);
    int stable = perf.pipeline_misses == 0 && perf.pipeline_hits >= 2 &&
                 perf.render_encoders_created == 0;
    unsetenv("EU4_GFX_ELEMENT_TRACE");
    int ok = cached_ok && trace_ok && fallback_ok && stable;
    printf("element_locations=%d cached=%d trace=%d fallback=%d stable=%d "
           "misses=%llu hits=%llu enc_new=%llu enc_reuse=%llu draws=%llu\n",
           ok, cached_ok, trace_ok, fallback_ok, stable,
           (unsigned long long)perf.pipeline_misses,
           (unsigned long long)perf.pipeline_hits,
           (unsigned long long)perf.render_encoders_created,
           (unsigned long long)perf.render_encoders_reused,
           (unsigned long long)perf.draws_encoded);
    (void)backend;
    return ok ? 0 : 1;
}

static int run_update_drain_failure(const char *kind) {
    /* The update must stop before mutating storage when the drain fails. */
    boot();
    void *simple_shader = make_simple_shader();
    uint8_t quad[96];
    make_quad(quad);
    void *vertex_header = eu4_gfx_create_vertex_buffer(NULL, quad, 4, 24, 0);
    uint8_t green_bgr[4 * 4 * 3] = {};
    for (int i = 0; i < 4 * 4; i++) {
        green_bgr[i * 3 + 1] = 0xff;
    }
    uint8_t texture_header[0x44];
    make_texture(4, 4, green_bgr, texture_header);
    float matrix[16];
    fill_matrix(matrix);
    uint8_t constant[0x10];
    memset(constant, 0, sizeof constant);
    uint32_t words = 16;
    memcpy(constant, &words, 4);
    void *constant_data = matrix;
    memcpy(constant + 8, &constant_data, sizeof constant_data);
    install_geometry(simple_shader, vertex_header, constant, texture_header);
    struct {
        void *master;
    } ctx = {g_master};
    float black[4] = {0.f, 0.f, 0.f, 1.f};
    eu4_gfx_clear(&ctx, 7, black);
    eu4_gfx_draw_raw raw = draw_frame(&ctx);
    if (eu4_gfx_draw_capture(&raw) != 1) {
        fail("update failure draw");
    }
    fprintf(stderr, "EVENT_DIR=%s\n", g_event_dir);
    fflush(stderr);
    eu4_gfx_test_fail_next_game_drain(1);
    if (strcmp(kind, "vertex") == 0) {
        eu4_gfx_update_vertex_buffer(&ctx, vertex_header, quad, 4, 0, 0);
    } else if (strcmp(kind, "multiple") == 0) {
        const void *data[1] = {quad};
        int32_t counts[1] = {4};
        eu4_gfx_update_vertex_buffer_multiple(&ctx, vertex_header, data, counts, 1);
    } else {
        eu4_gfx_update_texture(NULL, green_bgr, 4, 4, 0, texture_header);
    }
    printf("update_unexpected_return=1\n");
    return 1;
}

static int run_update_success(void) {
    boot();
    void *simple_shader = make_simple_shader();
    uint8_t quad[96];
    make_quad(quad);
    void *vertex_header = eu4_gfx_create_vertex_buffer(NULL, quad, 4, 24, 0);
    uint8_t green_bgr[4 * 4 * 3] = {}, blue_bgr[4 * 4 * 3] = {};
    for (int i = 0; i < 4 * 4; i++) {
        green_bgr[i * 3 + 1] = 0xff;
        blue_bgr[i * 3 + 0] = 0xff;
    }
    uint8_t texture_header[0x44];
    make_texture(4, 4, green_bgr, texture_header);
    float matrix[16];
    fill_matrix(matrix);
    uint8_t constant[0x10];
    memset(constant, 0, sizeof constant);
    uint32_t words = 16;
    memcpy(constant, &words, 4);
    void *constant_data = matrix;
    memcpy(constant + 8, &constant_data, sizeof constant_data);
    install_geometry(simple_shader, vertex_header, constant, texture_header);
    struct {
        void *master;
    } ctx = {g_master};
    float black[4] = {0.f, 0.f, 0.f, 1.f};
    eu4_gfx_clear(&ctx, 7, black);
    eu4_gfx_draw_raw raw = draw_frame(&ctx);
    if (eu4_gfx_draw_capture(&raw) != 1) {
        fail("update success draw 1");
    }
    eu4_gfx_update_vertex_buffer(&ctx, vertex_header, quad, 4, 0, 0);
    install_geometry(simple_shader, vertex_header, constant, texture_header);
    if (eu4_gfx_draw_capture(&raw) != 1) {
        fail("update success draw 2");
    }
    size_t length = 0;
    uint8_t *pixels = read_target(&length);
    const uint8_t *center = pixels + ((size_t)32 * kSize + 32) * 4;
    int pixels_ok = center[1] > 200;
    free(pixels);
    printf("update_success=%d\n", pixels_ok);
    (void)blue_bgr;
    return pixels_ok ? 0 : 1;
}

static int run_gpu_timing(void) {
    /* One completion record per committed command buffer, independent of the
       number of draws sharing it; a reset frame must not inherit late records. */
    eu4_gfx_perf_set_enabled(1);
    boot();
    EU4GfxBackend *backend = (__bridge EU4GfxBackend *)eu4_gfx_backend_token();
    void *shader = make_simple_shader();
    uint8_t quad[96];
    make_quad(quad);
    void *vertex_header = eu4_gfx_create_vertex_buffer(NULL, quad, 4, 24, 0);
    uint8_t green_bgr[4 * 4 * 3] = {};
    for (int i = 0; i < 4 * 4; i++) {
        green_bgr[i * 3 + 1] = 0xff;
    }
    uint8_t texture_header[0x44];
    make_texture(4, 4, green_bgr, texture_header);
    float matrix[16];
    fill_matrix(matrix);
    uint8_t constant[0x10];
    memset(constant, 0, sizeof constant);
    uint32_t words = 16;
    memcpy(constant, &words, 4);
    void *constant_data = matrix;
    memcpy(constant + 8, &constant_data, sizeof constant_data);
    install_geometry(shader, vertex_header, constant, texture_header);
    struct {
        void *master;
    } ctx = {g_master};
    const float black[4] = {0.f, 0.f, 0.f, 1.f};
    MTLTextureDescriptor *desc =
        [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm
                                                           width:kSize height:kSize mipmapped:NO];
    desc.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead |
                 MTLTextureUsageShaderWrite;
    desc.storageMode = MTLStorageModeShared;
    id<MTLTexture> destination = [backend.device newTextureWithDescriptor:desc];
    if (destination == nil) {
        fail("gpu timing drawable");
    }
    eu4_gfx_test_set_present_drawable((__bridge void *)destination);

    int records_ok = 1;
    uint64_t durations[2] = {0, 0};
    int counts[2] = {1, 1000};
    for (int phase = 0; phase < 2; phase++) {
        eu4_gfx_perf_reset();
        uint64_t wall_start = bench_now_ns();
        eu4_gfx_clear(&ctx, 7, black);
        eu4_gfx_draw_raw timing_draw = draw_frame(&ctx);
        for (int i = 0; i < counts[phase]; i++) {
            if (eu4_gfx_draw_capture(&timing_draw) != 1) {
                fail("gpu timing draw");
            }
        }
        if (eu4_gfx_present(g_master) != 1 || g_stopped) {
            fail("gpu timing present");
        }
        uint64_t wall_ns = bench_now_ns() - wall_start;
        if (!eu4_gfx_perf_wait_gpu_timing(3000)) {
            fail("gpu timing completion");
        }
        eu4_gfx_perf_snapshot perf;
        eu4_gfx_perf_read(&perf);
        durations[phase] = perf.gpu_execution_ns;
        /* clear + draw batch + present */
        records_ok = records_ok && perf.gpu_timing_records == 3 &&
                     perf.gpu_timing_pending == 0 &&
                     perf.draws_encoded == (uint64_t)counts[phase] &&
                     perf.gpu_execution_ns < wall_ns;
    }
    eu4_gfx_test_set_present_drawable(NULL);

    /* A completion registered before a reset must not contaminate the reset
       frame. */
    id<MTLCommandBuffer> stale = [backend.queue commandBuffer];
    eu4_gfx_perf_reset();
    eu4_gfx_perf_track_command_buffer((__bridge void *)stale);
    eu4_gfx_perf_reset();
    id<MTLBlitCommandEncoder> blit = [stale blitCommandEncoder];
    [blit endEncoding];
    [stale commit];
    [stale waitUntilCompleted];
    (void)eu4_gfx_perf_wait_gpu_timing(1000);
    eu4_gfx_perf_snapshot after_stale;
    eu4_gfx_perf_read(&after_stale);
    int epoch_isolated = after_stale.gpu_timing_records == 0 &&
                         after_stale.gpu_execution_ns == 0;


    int ok = records_ok && epoch_isolated;
    printf("gpu_timing=%d records=%d epoch_isolated=%d "
           "one_draw_ns=%llu thousand_draw_ns=%llu\n",
           ok, records_ok, epoch_isolated,
           (unsigned long long)durations[0], (unsigned long long)durations[1]);
    return ok ? 0 : 1;
}

static int run_geometry_thread(void) {
    /* Cocoa publishes geometry from the view thread while the renderer owns an
       open batch. Publication must only update the snapshot. */
    eu4_gfx_perf_set_enabled(1);
    boot();
    EU4GfxBackend *backend = (__bridge EU4GfxBackend *)eu4_gfx_backend_token();
    void *shader = make_simple_shader();
    uint8_t quad[96];
    make_quad(quad);
    void *vertex_header = eu4_gfx_create_vertex_buffer(NULL, quad, 4, 24, 0);
    uint8_t red_bgr[4 * 4 * 3] = {};
    for (int i = 0; i < 4 * 4; i++) {
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
    struct {
        void *master;
    } ctx = {g_master};
    const float black[4] = {0.f, 0.f, 0.f, 1.f};
    eu4_gfx_clear(&ctx, 7, black);
    eu4_gfx_draw_raw raw = draw_frame(&ctx);
    if (eu4_gfx_draw_capture(&raw) != 1) {
        fail("geometry thread draw");
    }
    id pending_before = backend.pendingGameCommand;
    id active_before = backend.activeCommandBuffer;
    if (active_before == nil || !eu4_gfx_batch_render_pass_active()) {
        fail("geometry thread batch");
    }
    eu4_gfx_perf_snapshot before;
    eu4_gfx_perf_read(&before);
    eu4_gfx_physical_geometry_state snapshot_before;
    if (!eu4_gfx_physical_geometry_snapshot(&snapshot_before)) {
        fail("geometry snapshot before");
    }

    geometry_publish_args next = {32, 32};
    pthread_t thread;
    if (pthread_create(&thread, NULL, publish_transition_thread, &next) != 0) {
        fail("geometry publisher thread");
    }
    pthread_join(thread, NULL);

    eu4_gfx_perf_snapshot after;
    eu4_gfx_perf_read(&after);
    int no_encoder_end = eu4_gfx_batch_render_pass_active() == 1;
    int no_submit = before.command_buffers_created == after.command_buffers_created &&
                    before.command_buffers_committed == after.command_buffers_committed &&
                    before.batch_flushes == after.batch_flushes &&
                    backend.pendingGameCommand == pending_before &&
                    backend.activeCommandBuffer == active_before;
    eu4_gfx_physical_geometry_state transition;
    int observed_transition = eu4_gfx_physical_geometry_snapshot(&transition) &&
                              transition.transition_in_progress == 1 &&
                              transition.width == snapshot_before.width;
    char diagnostic[256];
    eu4_gfx_simple_draw_state state;
    int draw_refused =
        eu4_gfx_draw_route_live(&raw, &state, diagnostic, sizeof diagnostic) ==
            EU4_GFX_DRAW_ROUTE_UNSUPPORTED &&
        strstr(diagnostic, "transition") != NULL;

    geometry_publish_args committed = {32, 32};
    if (pthread_create(&thread, NULL, publish_physical_thread, &committed) != 0) {
        fail("geometry physical thread");
    }
    pthread_join(thread, NULL);
    eu4_gfx_physical_geometry_state observed;
    int observed_epoch = eu4_gfx_physical_geometry_snapshot(&observed) &&
                         observed.epoch > snapshot_before.epoch &&
                         observed.transition_in_progress == 0 &&
                         observed.width == 32 && observed.height == 32;
    int older_refused =
        eu4_gfx_draw_route_live(&raw, &state, diagnostic, sizeof diagnostic) ==
            EU4_GFX_DRAW_ROUTE_UNSUPPORTED &&
        strstr(diagnostic, "older physical generation") != NULL;

    /* New-generation presentation must not accept the old batch. */
    MTLTextureDescriptor *desc =
        [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm
                                                           width:32 height:32 mipmapped:NO];
    desc.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead |
                 MTLTextureUsageShaderWrite;
    desc.storageMode = MTLStorageModeShared;
    id<MTLTexture> destination = [backend.device newTextureWithDescriptor:desc];
    if (destination == nil) {
        fail("geometry present target");
    }
    uint32_t sentinel[32 * 32];
    for (int i = 0; i < 32 * 32; i++) {
        sentinel[i] = 0x00112233u;
    }
    [destination replaceRegion:MTLRegionMake2D(0, 0, 32, 32) mipmapLevel:0
                    withBytes:sentinel bytesPerRow:32 * 4];
    eu4_gfx_test_set_present_drawable((__bridge void *)destination);
    int presented = eu4_gfx_present(g_master);
    uint32_t first = 0;
    [destination getBytes:&first bytesPerRow:4
               fromRegion:MTLRegionMake2D(0, 0, 1, 1) mipmapLevel:0];
    int stale_present = presented == 1 && first == 0x00112233u &&
                        !backend.readyForPresent;
    eu4_gfx_test_set_present_drawable(NULL);

    /* Same-size re-observation: epoch still advances, batch untouched. */
    eu4_gfx_clear(&ctx, 7, black);
    uint64_t epoch_before_same = observed.epoch;
    eu4_gfx_perf_read(&before);
    void *same_command = eu4_gfx_batch_acquire(
        g_master, eu4_gfx_geometry_generation(g_master));
    int created = 0;
    void *same_encoder = eu4_gfx_batch_encoder(
        (__bridge void *)backend.gameColorTarget, NULL, NULL, &created);
    (void)same_encoder;
    int same_encoder_active = eu4_gfx_batch_render_pass_active() == 1;
    geometry_publish_args same = {32, 32};
    pthread_create(&thread, NULL, publish_transition_thread, &same);
    pthread_join(thread, NULL);
    pthread_create(&thread, NULL, publish_physical_thread, &same);
    pthread_join(thread, NULL);
    eu4_gfx_perf_read(&after);
    eu4_gfx_physical_geometry_state same_state;
    int same_size = eu4_gfx_physical_geometry_snapshot(&same_state) &&
                    same_state.width == 32 && same_state.height == 32 &&
                    same_state.epoch > epoch_before_same &&
                    same_encoder_active &&
                    eu4_gfx_batch_render_pass_active() == 1 &&
                    before.command_buffers_committed ==
                        after.command_buffers_committed;
    (void)same_command;

    eu4_gfx_batch_end_pass();
    eu4_gfx_batch_draw_failed(0);
    int ok = no_encoder_end && no_submit && observed_transition && draw_refused &&
             observed_epoch && older_refused && stale_present && same_size;
    printf("geometry_thread=%d no_encoder_end=%d no_submit=%d transition=%d "
           "draw_refused=%d epoch=%d older_refused=%d stale_present=%d "
           "same_size=%d\n",
           ok, no_encoder_end, no_submit, observed_transition, draw_refused,
           observed_epoch, older_refused, stale_present, same_size);
    return ok ? 0 : 1;
}

static int run_layout_stability(void) {
    /* A dynamic slot offset moves to setVertexBuffer:offset:, so it must not
       create a new vertex layout or pipeline variant. */
    eu4_gfx_perf_set_enabled(1);
    boot();
    void *shader = make_generic_bench_shader();
    uint8_t vertices[8 * 32];
    bench_vertices(vertices);
    void *vertex_header = eu4_gfx_create_vertex_buffer(NULL, vertices, 8, 32, 0);
    const uint16_t indices[3] = {0, 1, 2};
    void *index_header = eu4_gfx_create_index_buffer(NULL, indices, 3);
    if (vertex_header == NULL || index_header == NULL) {
        fail("layout stability geometry");
    }
    bench_install_generic(shader, vertex_header, index_header);
    uint8_t *master = (uint8_t *)g_master;
    struct {
        void *master;
    } ctx = {g_master};
    const float black[4] = {0.f, 0.f, 0.f, 1.f};
    eu4_gfx_clear(&ctx, 7, black);
    eu4_gfx_draw_raw raw;
    memset(&raw, 0, sizeof raw);
    raw.which = 2;
    raw.rdi = (uint64_t)(uintptr_t)&ctx;
    raw.rsi = 3;
    raw.retaddr = (uint64_t)(uintptr_t)(g_image + kCallerRva);
    if (eu4_gfx_draw_capture(&raw) != 1) {
        fail("layout stability warm draw");
    }
    eu4_gfx_perf_reset();
    if (eu4_gfx_draw_capture(&raw) != 1) {
        fail("layout stability first draw");
    }
    uint32_t slot_offset = 32;
    memcpy(master + 0x150, &slot_offset, 4);
    if (eu4_gfx_draw_capture(&raw) != 1) {
        fail("layout stability offset draw");
    }
    slot_offset = 0;
    memcpy(master + 0x150, &slot_offset, 4);
    eu4_gfx_perf_snapshot perf;
    eu4_gfx_perf_read(&perf);
    int stable = perf.draws_encoded == 2 && perf.pipeline_misses == 0 &&
                 perf.pipeline_hits == 2 && perf.render_encoders_created == 0 &&
                 perf.render_encoders_reused >= 2;
    printf("layout_stability=%d misses=%llu hits=%llu encoders=%llu reused=%llu\n",
           stable, (unsigned long long)perf.pipeline_misses,
           (unsigned long long)perf.pipeline_hits,
           (unsigned long long)perf.render_encoders_created,
           (unsigned long long)perf.render_encoders_reused);
    return stable ? 0 : 1;
}

static int run_sampled_multi(void) {
    /* Two render targets converted in one externally owned command must both
       resolve to their prepared shadows, and completion identity must survive a
       superseding conversion and abandonment. */
    boot();
    EU4GfxBackend *backend = (__bridge EU4GfxBackend *)eu4_gfx_backend_token();
    if (backend == nil || backend.queue == nil) {
        fail("sampled multi backend");
    }
    uint32_t pattern_a[16], pattern_b[16];
    for (int i = 0; i < 16; i++) {
        pattern_a[i] = 0x00000000u | (uint32_t)(i * 0x00010101u);
        pattern_b[i] = 0x00ffffffu ^ (uint32_t)(i * 0x00030303u);
    }
    void *header_a = eu4_gfx_create_render_target_texture(NULL, 4, 4, 11, 0, 0);
    void *header_b = eu4_gfx_create_render_target_texture(NULL, 4, 4, 11, 0, 0);
    if (header_a == NULL || header_b == NULL) {
        fail("sampled multi headers");
    }
    id<MTLTexture> source_a =
        (__bridge id<MTLTexture>)eu4_gfx_texture_for_header(header_a);
    id<MTLTexture> source_b =
        (__bridge id<MTLTexture>)eu4_gfx_texture_for_header(header_b);
    [source_a replaceRegion:MTLRegionMake2D(0, 0, 4, 4) mipmapLevel:0
                 withBytes:pattern_a bytesPerRow:4 * 4];
    [source_b replaceRegion:MTLRegionMake2D(0, 0, 4, 4) mipmapLevel:0
                 withBytes:pattern_b bytesPerRow:4 * 4];
    if (backend.boundTextures == nil) {
        backend.boundTextures = [NSMutableArray array];
    }
    [backend.boundTextures removeAllObjects];
    [backend.boundTextures addObject:[NSValue valueWithPointer:header_a]];
    [backend.boundTextures addObject:[NSValue valueWithPointer:header_b]];

    char reason[256] = "";
    uint64_t before = eu4_gfx_sampling_conversion_count();
    id<MTLCommandBuffer> external = [backend.queue commandBuffer];
    uint64_t token = 0;
    if (!eu4_gfx_prepare_sampled_textures((__bridge void *)external, &token,
                                          reason, sizeof reason)) {
        fail("sampled multi external prepare");
    }
    int external_conversions = (int)(eu4_gfx_sampling_conversion_count() - before) == 2;
    void *shadow_a = eu4_gfx_texture_for_sampling(header_a, token);
    void *shadow_b = eu4_gfx_texture_for_sampling(header_b, token);
    int external_bindings = shadow_a != NULL && shadow_b != NULL &&
                            shadow_a != (__bridge void *)source_a &&
                            shadow_b != (__bridge void *)source_b &&
                            shadow_a != shadow_b;
    [external commit];
    [external waitUntilCompleted];
    int external_pixels =
        shadow_matches((__bridge id<MTLTexture>)shadow_a, backend.queue, pattern_a) &&
        shadow_matches((__bridge id<MTLTexture>)shadow_b, backend.queue, pattern_b);

    /* Second preparation after one source update: only that target reconverts,
       and both bindings remain valid. */
    uint32_t modified_a[16];
    for (int i = 0; i < 16; i++) {
        modified_a[i] = pattern_a[(i + 5) % 16];
    }
    [source_a replaceRegion:MTLRegionMake2D(0, 0, 4, 4) mipmapLevel:0
                 withBytes:modified_a bytesPerRow:4 * 4];
    eu4_gfx_note_render_target_write(header_a);
    uint64_t before_second = eu4_gfx_sampling_conversion_count();
    id<MTLCommandBuffer> second = [backend.queue commandBuffer];
    uint64_t token2 = 0;
    if (!eu4_gfx_prepare_sampled_textures((__bridge void *)second, &token2,
                                          reason, sizeof reason)) {
        fail("sampled multi second prepare");
    }
    int second_one = (int)(eu4_gfx_sampling_conversion_count() - before_second) == 1;
    void *second_a = eu4_gfx_texture_for_sampling(header_a, token2);
    void *second_b = eu4_gfx_texture_for_sampling(header_b, token2);
    [second commit];
    [second waitUntilCompleted];
    int second_pixels = second_a != NULL && second_b != NULL &&
                        shadow_matches((__bridge id<MTLTexture>)second_a,
                                       backend.queue, modified_a) &&
                        shadow_matches((__bridge id<MTLTexture>)second_b,
                                       backend.queue, pattern_b);

    /* Superseded conversion: two preparations in one external command. The
       older completion must not clear the newer pending record. */
    uint32_t newest_a[16];
    for (int i = 0; i < 16; i++) {
        newest_a[i] = pattern_a[(i + 11) % 16];
    }
    [source_a replaceRegion:MTLRegionMake2D(0, 0, 4, 4) mipmapLevel:0
                 withBytes:pattern_b bytesPerRow:4 * 4];
    eu4_gfx_note_render_target_write(header_a);
    id<MTLCommandBuffer> superseded = [backend.queue commandBuffer];
    uint64_t first_token = 0, last_token = 0;
    if (!eu4_gfx_prepare_sampled_textures((__bridge void *)superseded,
                                          &first_token, reason, sizeof reason)) {
        fail("sampled multi supersede 1");
    }
    [source_a replaceRegion:MTLRegionMake2D(0, 0, 4, 4) mipmapLevel:0
                 withBytes:newest_a bytesPerRow:4 * 4];
    eu4_gfx_note_render_target_write(header_a);
    if (!eu4_gfx_prepare_sampled_textures((__bridge void *)superseded,
                                          &last_token, reason, sizeof reason)) {
        fail("sampled multi supersede 2");
    }
    [superseded commit];
    [superseded waitUntilCompleted];
    void *newest = eu4_gfx_texture_for_sampling(header_a, last_token);
    int superseded_ok = newest != NULL &&
                        shadow_matches((__bridge id<MTLTexture>)newest,
                                       backend.queue, newest_a);

    /* Abandoned external command: the next preparation must reconvert. */
    [source_a replaceRegion:MTLRegionMake2D(0, 0, 4, 4) mipmapLevel:0
                 withBytes:pattern_a bytesPerRow:4 * 4];
    eu4_gfx_note_render_target_write(header_a);
    uint64_t before_abandon = eu4_gfx_sampling_conversion_count();
    id<MTLCommandBuffer> abandoned = [backend.queue commandBuffer];
    uint64_t abandoned_token = 0;
    if (!eu4_gfx_prepare_sampled_textures((__bridge void *)abandoned,
                                          &abandoned_token, reason,
                                          sizeof reason)) {
        fail("sampled multi abandoned prepare");
    }
    eu4_gfx_sampling_abandon_command((__bridge void *)abandoned);
    id<MTLCommandBuffer> recovery = [backend.queue commandBuffer];
    uint64_t recovery_token = 0;
    if (!eu4_gfx_prepare_sampled_textures((__bridge void *)recovery,
                                          &recovery_token, reason,
                                          sizeof reason)) {
        fail("sampled multi recovery prepare");
    }
    int abandoned_ok =
        (int)(eu4_gfx_sampling_conversion_count() - before_abandon) == 2 &&
        recovery_token != abandoned_token;
    [recovery commit];
    [recovery waitUntilCompleted];
    void *recovered = eu4_gfx_texture_for_sampling(header_a, recovery_token);
    abandoned_ok = abandoned_ok && recovered != NULL &&
                   shadow_matches((__bridge id<MTLTexture>)recovered,
                                  backend.queue, pattern_a);

    /* Repeated sampling in the active batch reuses both conversions. */
    eu4_gfx_note_render_target_write(header_a);
    eu4_gfx_note_render_target_write(header_b);
    uint64_t before_active = eu4_gfx_sampling_conversion_count();
    void *active = eu4_gfx_batch_acquire(
        g_master, eu4_gfx_geometry_generation(g_master));
    uint64_t active_token1 = 0, active_token2 = 0;
    if (active == NULL ||
        !eu4_gfx_prepare_sampled_textures(active, &active_token1, reason,
                                          sizeof reason) ||
        !eu4_gfx_prepare_sampled_textures(active, &active_token2, reason,
                                          sizeof reason)) {
        fail("sampled multi active prepare");
    }
    int active_ok =
        (int)(eu4_gfx_sampling_conversion_count() - before_active) == 2 &&
        active_token1 == active_token2 &&
        eu4_gfx_texture_for_sampling(header_a, active_token1) != NULL &&
        eu4_gfx_texture_for_sampling(header_b, active_token1) != NULL;
    eu4_gfx_batch_draw_failed(0);

    int ok = external_conversions && external_bindings && external_pixels &&
             second_one && second_pixels && superseded_ok && abandoned_ok &&
             active_ok;
    printf("sampled_multi=%d external=%d bindings=%d pixels=%d second=%d "
           "superseded=%d abandoned=%d active=%d\n",
           ok, external_conversions, external_bindings, external_pixels,
           second_one && second_pixels, superseded_ok, abandoned_ok, active_ok);
    return ok ? 0 : 1;
}

static int run_sampled_batch(void) {
    /* Same command buffer samples one unchanged render target twice; the
       conversion is encoded once and both draws can bind the shadow. Dropping
       the command must invalidate it so the next batch reconverts. */
    boot();
    EU4GfxBackend *backend = (__bridge EU4GfxBackend *)eu4_gfx_backend_token();
    if (backend == nil || backend.queue == nil) {
        fail("sampled batch backend");
    }
    uint32_t pattern[16];
    for (int i = 0; i < 16; i++) {
        pattern[i] = 0x00000000u | (uint32_t)(i * 0x00110000u);
    }
    void *header = eu4_gfx_create_render_target_texture(NULL, 4, 4, 11, 0, 0);
    if (header == NULL) {
        fail("sampled batch header");
    }
    id<MTLTexture> target =
        (__bridge id<MTLTexture>)eu4_gfx_texture_for_header(header);
    [target replaceRegion:MTLRegionMake2D(0, 0, 4, 4) mipmapLevel:0
                withBytes:pattern bytesPerRow:4 * 4];
    if (backend.boundTextures == nil) {
        backend.boundTextures = [NSMutableArray array];
    }
    [backend.boundTextures removeAllObjects];
    [backend.boundTextures addObject:[NSValue valueWithPointer:header]];

    uint64_t before = eu4_gfx_sampling_conversion_count();
    uint64_t generation = eu4_gfx_geometry_generation(g_master);
    void *command = eu4_gfx_batch_acquire(g_master, generation);
    if (command == NULL) {
        fail("sampled batch command");
    }
    char reason[256] = "";
    uint64_t token1 = 0, token2 = 0;
    if (!eu4_gfx_prepare_sampled_textures(command, &token1, reason, sizeof reason) ||
        !eu4_gfx_prepare_sampled_textures(command, &token2, reason, sizeof reason)) {
        fail("sampled batch prepare");
    }
    uint64_t reused = eu4_gfx_sampling_conversion_count() - before;
    void *shadow1 = eu4_gfx_texture_for_sampling(header, token1);
    void *shadow2 = eu4_gfx_texture_for_sampling(header, token2);
    int shadow_ok = shadow1 != NULL && shadow1 != (__bridge void *)target &&
                    shadow1 == shadow2;

    eu4_gfx_batch_draw_failed(0); /* drop the unsubmitted command */
    void *next_command = eu4_gfx_batch_acquire(g_master, generation);
    uint64_t token3 = 0;
    if (next_command == NULL ||
        !eu4_gfx_prepare_sampled_textures(next_command, &token3, reason,
                                          sizeof reason)) {
        fail("sampled batch reconvert");
    }
    uint64_t total = eu4_gfx_sampling_conversion_count() - before;
    int reconverted = total == 2 && token3 != token1;

    int ok = reused == 1 && shadow_ok && reconverted;
    printf("sampled_batch=%d reused=%llu shadow=%d reconverted=%d\n", ok,
           (unsigned long long)reused, shadow_ok, reconverted);
    return ok ? 0 : 1;
}

static int run_blend_order(void) {
    /* Two overlapping transparent primitives with distinct blend states in one
       render pass. The exact pixel proves draw order and accumulation, and the
       encoder counters prove they shared a pass. */
    eu4_gfx_perf_set_enabled(1);
    boot();
    void *shader = make_generic_bench_shader();
    uint8_t vertices[8 * 32];
    bench_vertices_blend(vertices);
    void *vertex_header = eu4_gfx_create_vertex_buffer(NULL, vertices, 8, 32, 0);
    const uint16_t indices[3] = {0, 1, 2};
    void *index_header = eu4_gfx_create_index_buffer(NULL, indices, 3);
    if (vertex_header == NULL || index_header == NULL) {
        fail("blend geometry");
    }
    bench_install_generic(shader, vertex_header, index_header);
    uint8_t *master = (uint8_t *)g_master;
    struct {
        void *master;
    } ctx = {g_master};
    const float black[4] = {0.f, 0.f, 0.f, 1.f};

    eu4_gfx_perf_reset();
    eu4_gfx_clear(&ctx, 7, black);
    eu4_gfx_draw_raw raw;
    memset(&raw, 0, sizeof raw);
    raw.which = 2;
    raw.rdi = (uint64_t)(uintptr_t)&ctx;
    raw.rsi = 3;
    raw.retaddr = (uint64_t)(uintptr_t)(g_image + kCallerRva);

    /* Opaque red first. */
    raw.rcx = 0;
    memcpy(master + 0xc, (uint32_t[]){0}, 4);
    if (eu4_gfx_draw_capture(&raw) != 1) {
        fail("blend draw 1");
    }
    /* Alpha green second: SRC_ALPHA / ONE_MINUS_SRC_ALPHA. */
    raw.rcx = 4;
    uint32_t enabled = 1, src = 0x302, dst = 0x303, add = 0x8006;
    memcpy(master + 0xc, &enabled, 4);
    memcpy(master + 0x10, &src, 4);
    memcpy(master + 0x14, &dst, 4);
    memcpy(master + 0x18, &add, 4);
    memcpy(master + 0x1c, &src, 4);
    memcpy(master + 0x20, &dst, 4);
    memcpy(master + 0x24, &add, 4);
    if (eu4_gfx_draw_capture(&raw) != 1) {
        fail("blend draw 2");
    }
    eu4_gfx_perf_snapshot perf;
    eu4_gfx_perf_read(&perf);
    int one_pass = perf.render_encoders_created == 1 &&
                   perf.render_encoders_reused >= 1 &&
                   perf.draws_encoded == 2;

    size_t length = 0;
    uint8_t *pixels = read_target(&length);
    const uint8_t *pixel = pixels + ((size_t)32 * kSize + 32) * 4;
    int accumulation = abs((int)pixel[2] - 128) <= 8 &&
                       abs((int)pixel[1] - 128) <= 8 && pixel[0] <= 8;
    free(pixels);
    /* Reversing the order would leave opaque red; a missing second draw would
       leave opaque red as well. */
    int order = accumulation;

    /* Clear between draws must split the pass and discard the first draw. */
    eu4_gfx_perf_reset();
    eu4_gfx_clear(&ctx, 7, black);
    raw.rcx = 0;
    memcpy(master + 0xc, (uint32_t[]){0}, 4);
    if (eu4_gfx_draw_capture(&raw) != 1) {
        fail("blend clear draw 1");
    }
    eu4_gfx_clear(&ctx, 7, black);
    raw.rcx = 4;
    memcpy(master + 0xc, &enabled, 4);
    memcpy(master + 0x10, &src, 4);
    memcpy(master + 0x14, &dst, 4);
    memcpy(master + 0x18, &add, 4);
    memcpy(master + 0x1c, &src, 4);
    memcpy(master + 0x20, &dst, 4);
    memcpy(master + 0x24, &add, 4);
    if (eu4_gfx_draw_capture(&raw) != 1) {
        fail("blend clear draw 2");
    }
    pixels = read_target(&length);
    pixel = pixels + ((size_t)32 * kSize + 32) * 4;
    int clear_split = pixel[2] <= 8 && abs((int)pixel[1] - 128) <= 8;
    free(pixels);

    int ok = one_pass && order && clear_split;
    printf("blend_order=%d one_pass=%d order=%d clear_split=%d\n",
           ok, one_pass, order, clear_split);
    return ok ? 0 : 1;
}

static int run_batch_sequences(void) {
    /* Ordering/ownership sequences required by the shared-command-buffer
       design: a refused draw must not discard earlier encodes, an independent
       target change must flush, and a homogeneous stream must submit once. */
    eu4_gfx_perf_set_enabled(1);
    boot();
    void *shader = make_generic_bench_shader();
    uint8_t vertices[8 * 32];
    bench_vertices(vertices);
    void *vertex_header = eu4_gfx_create_vertex_buffer(NULL, vertices, 8, 32, 0);
    const uint16_t indices[3] = {0, 1, 2};
    void *index_header = eu4_gfx_create_index_buffer(NULL, indices, 3);
    if (vertex_header == NULL || index_header == NULL) {
        fail("batch geometry");
    }
    bench_install_generic(shader, vertex_header, index_header);
    struct {
        void *master;
    } ctx = {g_master};
    const float black[4] = {0.f, 0.f, 0.f, 1.f};
    eu4_gfx_draw_raw raw;
    memset(&raw, 0, sizeof raw);
    raw.which = 2;
    raw.rdi = (uint64_t)(uintptr_t)&ctx;
    raw.rsi = 3;
    raw.retaddr = (uint64_t)(uintptr_t)(g_image + kCallerRva);

    eu4_gfx_perf_reset();
    eu4_gfx_clear(&ctx, 7, black);
    if (eu4_gfx_draw_capture(&raw) != 1 || eu4_gfx_draw_capture(&raw) != 1) {
        fail("batch preserved draws");
    }
    /* Refusal after two successful encodes: the out-of-range index fails after
       the batch was acquired. Earlier work must still be presented. */
    const uint16_t outside[3] = {100, 101, 102};
    eu4_gfx_update_index_buffer(NULL, index_header, outside, 3);
    char diagnostic[256];
    eu4_gfx_simple_draw_state state;
    int refused =
        eu4_gfx_draw_route_live(&raw, &state, diagnostic, sizeof diagnostic) ==
        EU4_GFX_DRAW_ROUTE_UNSUPPORTED;
    eu4_gfx_perf_snapshot perf;
    eu4_gfx_perf_read(&perf);
    int preserved_draws = perf.draws_encoded == 2;
    size_t length = 0;
    uint8_t *pixels = read_target(&length);
    int preserved_pixels = pixels[((size_t)32 * kSize + 32) * 4 + 2] > 200;
    free(pixels);

    /* Independent target selection between draws flushes the active batch. */
    eu4_gfx_update_index_buffer(NULL, index_header, indices, 3);
    eu4_gfx_clear(&ctx, 7, black);
    if (eu4_gfx_draw_capture(&raw) != 1) {
        fail("batch target draw 1");
    }
    eu4_gfx_set_render_target(&ctx, NULL);
    if (eu4_gfx_draw_capture(&raw) != 1) {
        fail("batch target draw 2");
    }
    pixels = read_target(&length);
    int target_switch = pixels[((size_t)32 * kSize + 32) * 4 + 2] > 200;
    free(pixels);

    /* In-place vertex-buffer update between draws: the batch flushes and
       waits before the shared storage is overwritten. */
    eu4_gfx_perf_reset();
    if (eu4_gfx_draw_capture(&raw) != 1) {
        fail("batch update draw 1");
    }
    eu4_gfx_update_vertex_buffer(&ctx, vertex_header, vertices, 8, 0, 0);
    if (eu4_gfx_draw_capture(&raw) != 1) {
        fail("batch update draw 2");
    }
    pixels = read_target(&length);
    int resource_update = pixels[((size_t)32 * kSize + 32) * 4 + 2] > 200;
    free(pixels);
    eu4_gfx_perf_read(&perf);
    resource_update = resource_update && perf.draws_encoded == 2 &&
                      perf.batch_flushes >= 1;

    /* Homogeneous stream: one draw-stream command buffer, plus clear/present. */
    eu4_gfx_perf_reset();
    eu4_gfx_clear(&ctx, 7, black);
    for (int i = 0; i < 1000; i++) {
        if (eu4_gfx_draw_capture(&raw) != 1) {
            fail("batch homogeneous draw");
        }
    }
    MTLTextureDescriptor *desc =
        [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm
                                                           width:kSize
                                                          height:kSize
                                                       mipmapped:NO];
    desc.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead |
                 MTLTextureUsageShaderWrite;
    desc.storageMode = MTLStorageModeShared;
    EU4GfxBackend *backend = (__bridge EU4GfxBackend *)eu4_gfx_backend_token();
    id<MTLTexture> present_target = [backend.device newTextureWithDescriptor:desc];
    if (present_target == nil) {
        fail("batch present target");
    }
    eu4_gfx_test_set_present_drawable((__bridge void *)present_target);
    if (eu4_gfx_present(g_master) != 1 || g_stopped) {
        fail("batch present");
    }
    eu4_gfx_perf_read(&perf);
    int one_submission = perf.command_buffers_created <= 4 &&
                         perf.draws_encoded == 1000;
    int flushes = perf.batch_flushes >= 1;

    /* Shutdown with an encoded but unsubmitted draw. */
    if (eu4_gfx_draw_capture(&raw) != 1) {
        fail("batch shutdown draw");
    }
    eu4_gfx_shutdown(g_master);
    int shutdown_ok = !g_stopped;
    g_master = NULL;

    int ok = refused && preserved_draws && preserved_pixels && target_switch &&
             resource_update && one_submission && flushes && shutdown_ok;
    printf("batch_sequences=%d refused=%d preserved_draws=%d preserved_pixels=%d "
           "target_switch=%d resource_update=%d one_submission=%d flushes=%d "
           "shutdown=%d command_buffers=%llu\n",
           ok, refused, preserved_draws, preserved_pixels, target_switch,
           resource_update, one_submission, flushes, shutdown_ok,
           (unsigned long long)perf.command_buffers_created);
    return ok ? 0 : 1;
}

int main(int argc, char **argv) {
    if (argc >= 2 && strcmp(argv[1], "--batch-sequences") == 0) {
        return run_batch_sequences();
    }
    if (argc >= 2 && strcmp(argv[1], "--blend-order") == 0) {
        return run_blend_order();
    }
    if (argc >= 2 && strcmp(argv[1], "--sampled-batch") == 0) {
        return run_sampled_batch();
    }
    if (argc >= 2 && strcmp(argv[1], "--sampled-multi") == 0) {
        return run_sampled_multi();
    }
    if (argc >= 2 && strcmp(argv[1], "--layout-stability") == 0) {
        return run_layout_stability();
    }
    if (argc >= 2 && strcmp(argv[1], "--geometry-thread") == 0) {
        return run_geometry_thread();
    }
    if (argc >= 2 && strcmp(argv[1], "--gpu-timing") == 0) {
        return run_gpu_timing();
    }
    if (argc >= 2 && strcmp(argv[1], "--encoder-cleanup") == 0) {
        return run_encoder_cleanup();
    }
    if (argc >= 2 && strcmp(argv[1], "--element-locations") == 0) {
        return run_element_locations();
    }
    if (argc >= 3 && strcmp(argv[1], "--update-drain-failure") == 0) {
        return run_update_drain_failure(argv[2]);
    }
    if (argc >= 2 && strcmp(argv[1], "--update-success") == 0) {
        return run_update_success();
    }
    if (argc >= 2 && strcmp(argv[1], "--scissor-handoff") == 0) {
        return run_scissor_handoff();
    }
    if (argc >= 2 && strcmp(argv[1], "--indexed-resource") == 0) {
        return run_indexed_resource();
    }
    if (argc >= 5 && strcmp(argv[1], "--bench") == 0) {
        return run_benchmark(argv[2], atoi(argv[3]), atoi(argv[4]));
    }
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
    if (argc >= 2 && strcmp(argv[1], "--sampled-rt-orientation") == 0) {
        return run_sampled_render_target_orientation();
    }
    if (argc >= 2 && strcmp(argv[1], "--draw-semantics") == 0) {
        return run_draw_semantics();
    }
    if (argc >= 2 && strcmp(argv[1], "--production-bounds") == 0) {
        return run_production_bounds();
    }
    if (argc >= 2 && strcmp(argv[1], "--production-pipeline") == 0) {
        return run_production_pipeline();
    }
    if (argc >= 2 && strcmp(argv[1], "--vertex-fetch") == 0) {
        return run_vertex_fetch();
    }
    if (argc >= 2 && strcmp(argv[1], "--indexed-base-vertex") == 0) {
        return run_indexed_base_vertex();
    }
    if (argc >= 2 && strcmp(argv[1], "--color-write-mask") == 0) {
        return run_color_write_mask();
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
    uint8_t red_bgr[4 * 4 * 3] = {};
    uint8_t green_bgr[4 * 4 * 3] = {};
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
