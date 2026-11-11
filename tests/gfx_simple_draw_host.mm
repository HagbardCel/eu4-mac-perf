/* Real-Metal execution of the authentic Simple loading-screen draw.
   Inputs: L3.2b-I packet payloads (vertex buffer + matrix) and an optional
   original loading-screen DDS. Renders through the replacement modules and
   checks the readback against CPU sampling of the same image and transform. */

#import "eu4_gfx_buffers.h"
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

enum {
    kIndexCacheRva = 0x2238590,
    kIndexCacheSlots = 128,
    kScreenWidth = 1728,
    kScreenHeight = 1117,
};

static uint8_t *g_image;
static void *g_slot;
static void *g_master;

typedef struct {
    uint32_t level;
    uint32_t face;
    uint32_t width;
    uint32_t height;
    uint32_t bytes;
    uint32_t pad;
    const void *pixels;
} image_record;

static void fail(const char *message) {
    fprintf(stderr, "FAIL: %s\n", message);
    exit(1);
}

static void *fake_window(uint32_t window_id) { return window_id == 7 ? (void *)0x11 : NULL; }

static void fake_size(void *window, int *width, int *height) {
    (void)window;
    *width = kScreenWidth;
    *height = kScreenHeight;
}

static void write_depth(void *dest) { eu4_gfx_write_default_depth_stencil((uint8_t *)dest); }

static void boot(void) {
    size_t bytes = kIndexCacheRva + (size_t)kIndexCacheSlots * sizeof(void *);
    g_image = (uint8_t *)mmap(NULL, bytes, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
    if (g_image == MAP_FAILED) {
        fail("image map");
    }
    eu4_gfx_set_image(g_image, &g_slot);
    eu4_gfx_set_sdl(fake_window, fake_size);
    eu4_gfx_set_default_writers(eu4_gfx_write_default_blend, write_depth,
                                eu4_gfx_write_default_raster);
    id<MTLDevice> device = MTLCreateSystemDefaultDevice();
    if (device == nil) {
        fail("no Metal device");
    }
    CAMetalLayer *layer = [CAMetalLayer layer];
    layer.device = device;
    eu4_gfx_set_layer((__bridge void *)layer);
    char dir[] = "/tmp/eu4-simple-draw-XXXXXX";
    if (mkdtemp(dir) == NULL) {
        fail("event dir");
    }
    eu4_metal_events_set_dir(dir);
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
}

static uint8_t *read_file(const char *path, size_t *length) {
    FILE *file = fopen(path, "rb");
    if (file == NULL) {
        return NULL;
    }
    fseek(file, 0, SEEK_END);
    long size = ftell(file);
    fseek(file, 0, SEEK_SET);
    if (size <= 0) {
        fclose(file);
        return NULL;
    }
    uint8_t *bytes = (uint8_t *)malloc((size_t)size);
    if (bytes == NULL || fread(bytes, 1, (size_t)size, file) != (size_t)size) {
        fclose(file);
        free(bytes);
        return NULL;
    }
    fclose(file);
    *length = (size_t)size;
    return bytes;
}

static void register_source(const char *source) {
    uint8_t digest[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256(source, (CC_LONG)strlen(source), digest);
    if (!eu4_gfx_shader_register_qualified_sha256(digest)) {
        fail("qualified source registration");
    }
}

static void *make_simple_shader(void) {
    eu4_gfx_shader_test_reset();
    register_source(eu4_gfx_simple_vertex_source());
    register_source(eu4_gfx_simple_pixel_source());
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
    if (!eu4_gfx_shader_reflect_validate(shader)) {
        fail("Simple reflection validation");
    }
    return shader;
}

/* Creates an engine texture header through the replacement entry. */
static void make_texture(uint32_t width, uint32_t height, uint32_t format, const void *pixels,
                         uint8_t header[0x44]) {
    memset(header, 0, 0x44);
    memset(header + 4, 0xff, 4);
    uint32_t desc[5] = {width, height, 1, format, 0};
    image_record image = {0, 0, width, height, 0, 0, pixels};
    eu4_gfx_initialize_texture(NULL, header, desc, &image, 1);
    if (header[4] != 0) {
        fail("texture initialize did not clear the GL name");
    }
}

typedef struct {
    uint32_t width;
    uint32_t height;
    const uint8_t *bgr; /* tightly packed rows, B,G,R */
} cpu_image;

static void cpu_texel(const cpu_image *image, int x, int y, double out[3]) {
    if (x < 0) x = 0;
    if (y < 0) y = 0;
    if (x >= (int)image->width) x = (int)image->width - 1;
    if (y >= (int)image->height) y = (int)image->height - 1;
    const uint8_t *px = image->bgr + ((size_t)y * image->width + (size_t)x) * 3;
    out[0] = px[2] / 255.0;
    out[1] = px[1] / 255.0;
    out[2] = px[0] / 255.0;
}

static void cpu_texel_repeat(const cpu_image *image, int x, int y, double out[3]) {
    int width = (int)image->width;
    int height = (int)image->height;
    x %= width;
    if (x < 0) x += width;
    y %= height;
    if (y < 0) y += height;
    cpu_texel(image, x, y, out);
}

/* Same bilinear filter, but with the sampler's Repeat address mode instead of
   the edge clamp used by the authentic 0..1 quad reference. */
static void cpu_bilinear_repeat(const cpu_image *image, double u, double v, double out[3]) {
    double fx = u * image->width - 0.5;
    double fy = v * image->height - 0.5;
    int x0 = (int)floor(fx);
    int y0 = (int)floor(fy);
    double tx = fx - x0;
    double ty = fy - y0;
    double p00[3], p10[3], p01[3], p11[3];
    cpu_texel_repeat(image, x0, y0, p00);
    cpu_texel_repeat(image, x0 + 1, y0, p10);
    cpu_texel_repeat(image, x0, y0 + 1, p01);
    cpu_texel_repeat(image, x0 + 1, y0 + 1, p11);
    for (int c = 0; c < 3; c++) {
        double top = p00[c] * (1 - tx) + p10[c] * tx;
        double bottom = p01[c] * (1 - tx) + p11[c] * tx;
        out[c] = top * (1 - ty) + bottom * ty;
    }
}

static void cpu_bilinear(const cpu_image *image, double u, double v, double out[3]) {
    double fx = u * image->width - 0.5;
    double fy = v * image->height - 0.5;
    int x0 = (int)floor(fx);
    int y0 = (int)floor(fy);
    double tx = fx - x0;
    double ty = fy - y0;
    double p00[3], p10[3], p01[3], p11[3];
    cpu_texel(image, x0, y0, p00);
    cpu_texel(image, x0 + 1, y0, p10);
    cpu_texel(image, x0, y0 + 1, p01);
    cpu_texel(image, x0 + 1, y0 + 1, p11);
    for (int c = 0; c < 3; c++) {
        double top = p00[c] * (1 - tx) + p10[c] * tx;
        double bottom = p01[c] * (1 - tx) + p11[c] * tx;
        out[c] = top * (1 - ty) + bottom * ty;
    }
}

/* Pixel centre -> interpolated Simple texture coordinate. The quad is the
   reconstructed fullscreen loading-screen quad (+/-512 x +/-384, uv 0..1) and
   the matrix is the authentic L3.2b-I 4x4 (scale x, y only). */
static void expected_uv(int px, int py, int width, int height, const float *matrix, double *u,
                        double *v) {
    double ndc_x = ((double)px + 0.5) / width * 2.0 - 1.0;
    double ndc_y = 1.0 - ((double)py + 0.5) / height * 2.0;
    double world_x = ndc_x / matrix[0];
    double world_y = ndc_y / matrix[5];
    *u = (world_x + 512.0) / 1024.0;
    *v = (384.0 - world_y) / 768.0;
}

static uint8_t *read_target(id<MTLTexture> texture, uint32_t width, uint32_t height) {
    uint8_t *bytes = (uint8_t *)malloc((size_t)width * height * 4);
    if (bytes == NULL) {
        fail("readback allocation");
    }
    [texture getBytes:bytes
          bytesPerRow:(NSUInteger)width * 4
           fromRegion:MTLRegionMake2D(0, 0, width, height)
          mipmapLevel:0];
    return bytes;
}

/* Synthetic boundary (not an original-game observation): the same quad and
   matrix carry texcoords outside [0,1] against a 4x4 texture. The sampler
   contract is Repeat, so the CPU reference wraps and the clamped reference must
   disagree on a meaningful fraction of the target. */
static void test_repeat_addressing(uint8_t *master, const uint8_t *vb, const float *matrix,
                                   EU4GfxBackend *backend) {
    const uint32_t size = 64;
    uint8_t pattern[4 * 4 * 4];
    uint8_t bgr_pattern[4 * 4 * 3];
    for (int y = 0; y < 4; y++) {
        for (int x = 0; x < 4; x++) {
            uint8_t *px = pattern + ((size_t)y * 4 + x) * 4;
            uint8_t *bgr_px = bgr_pattern + ((size_t)y * 4 + x) * 3;
            px[0] = bgr_px[0] = (uint8_t)(x * 60 + 12);      /* B */
            px[1] = bgr_px[1] = (uint8_t)(y * 60 + 12);      /* G */
            px[2] = bgr_px[2] = (uint8_t)(x * 37 + y * 53);  /* R */
            px[3] = 255;
        }
    }
    uint8_t repeat_header[0x44];
    make_texture(4, 4, 0xb, pattern, repeat_header);
    void *repeat_texture = eu4_gfx_texture_for_header(repeat_header);
    if (repeat_texture == NULL) {
        fail("repeat texture registry lookup");
    }

    uint8_t repeated_vb[96];
    memcpy(repeated_vb, vb, sizeof repeated_vb);
    const float uvs[4][2] = {{-1.0f, -1.0f}, {2.0f, -1.0f}, {-1.0f, 2.0f}, {2.0f, 2.0f}};
    for (int i = 0; i < 4; i++) {
        memcpy(repeated_vb + (size_t)i * EU4_GFX_SIMPLE_VERTEX_STRIDE + 12, uvs[i],
               sizeof uvs[i]);
    }
    void *repeat_vertex = eu4_gfx_create_vertex_buffer(NULL, repeated_vb, 4,
                                                       EU4_GFX_SIMPLE_VERTEX_STRIDE, 0);
    if (repeat_vertex == NULL) {
        fail("repeat vertex buffer create");
    }

    struct {
        void *master;
    } ctx = {master};
    void *headers[1] = {repeat_vertex};
    eu4_gfx_set_vertex_buffers(&ctx, 1, headers, 0, NULL, NULL);
    if (eu4_gfx_configure_offscreen(master, size, size) != EU4_GFX_CLEAR_OK) {
        fail("repeat target configure");
    }
    uint32_t rect[6] = {size, size, 0x3f800000u, 0, 0, 0};
    eu4_gfx_set_viewport(master, rect);
    const float clear[4] = {0.0f, 0.0f, 0.0f, 1.0f};
    int command_status = 0;
    if (eu4_gfx_clear_checked(&ctx, 1, clear) != EU4_GFX_CLEAR_OK ||
        eu4_gfx_complete_offscreen(master, &command_status) != EU4_GFX_CLEAR_OK ||
        command_status != (int)MTLCommandBufferStatusCompleted) {
        fail("repeat target clear");
    }

    void *texture_pointer = repeat_header;
    eu4_gfx_set_textures(&ctx, 1, &texture_pointer, 1, 0, NULL);
    if (eu4_gfx_bound_texture() != repeat_texture) {
        fail("repeat texture binding was not recorded");
    }
    id<MTLTexture> target = backend.colorTarget;
    eu4_gfx_simple_draw_inputs inputs = {};
    inputs.device = (__bridge void *)backend.device;
    inputs.target = (__bridge void *)target;
    inputs.target_width = size;
    inputs.target_height = size;
    inputs.viewport_width = size;
    inputs.viewport_height = size;
    inputs.queue = (__bridge void *)backend.queue;
    const char *reason = NULL;
    if (eu4_gfx_simple_draw(&ctx, 0, 0, &inputs, &reason) != EU4_GFX_SIMPLE_DRAW_OK) {
        fail("repeat draw");
    }

    uint8_t *pixels = read_target(target, size, size);
    cpu_image image = {4, 4, bgr_pattern};
    int checked = 0;
    int repeat_bad = 0;
    int repeat_differences = 0;
    double worst = 0.0;
    for (uint32_t py = 0; py < size; py++) {
        for (uint32_t px = 0; px < size; px++) {
            double ndc_x = ((double)px + 0.5) / size * 2.0 - 1.0;
            double ndc_y = 1.0 - ((double)py + 0.5) / size * 2.0;
            double world_x = ndc_x / matrix[0];
            double world_y = ndc_y / matrix[5];
            double u = (world_x + 512.0) / 1024.0 * 3.0 - 1.0;
            double v = (384.0 - world_y) / 768.0 * 3.0 - 1.0;
            double want[3], clamped[3];
            cpu_bilinear_repeat(&image, u, v, want);
            cpu_bilinear(&image, u, v, clamped);
            const uint8_t *pixel = pixels + ((size_t)py * size + px) * 4;
            double got[3] = {pixel[2] / 255.0, pixel[1] / 255.0, pixel[0] / 255.0};
            double clamp_delta = 0.0;
            for (int c = 0; c < 3; c++) {
                double delta = fabs(got[c] - want[c]);
                if (delta > worst) {
                    worst = delta;
                }
                if (delta > 8.0 / 255.0) {
                    repeat_bad++;
                }
                clamp_delta += fabs(got[c] - clamped[c]);
            }
            if (clamp_delta > 24.0 / 255.0) {
                repeat_differences++;
            }
            checked++;
        }
    }
    if (repeat_bad != 0) {
        fprintf(stderr, "FAIL: repeat addressing %d/%d pixels, worst %.4f\n", repeat_bad,
                checked, worst);
        exit(1);
    }
    if (repeat_differences < checked / 4) {
        fprintf(stderr, "FAIL: repeat boundary indistinguishable from clamp (%d/%d)\n",
                repeat_differences, checked);
        exit(1);
    }
    free(pixels);
    printf("repeat_bad=0 repeat_differences=%d worst_repeat=%.4f\n", repeat_differences, worst);
}

static void test_authentic(const uint8_t *vb, size_t vb_len, const float *matrix, void *shader,
                           const uint8_t *bgr, uint32_t image_width, uint32_t image_height) {
    if (vb_len != 96) {
        fail("authentic vertex buffer length");
    }
    if (vb[20] != 0xff || vb[21] != 0xff || vb[22] != 0xff || vb[23] != 0xff) {
        fail("authentic vertex colour is not white");
    }
    void *vertex_header = eu4_gfx_create_vertex_buffer(NULL, vb, 4, EU4_GFX_SIMPLE_VERTEX_STRIDE, 0);
    if (vertex_header == NULL) {
        fail("vertex buffer create");
    }

    uint8_t constant[0x10];
    memset(constant, 0, sizeof constant);
    uint32_t words = 16;
    memcpy(constant, &words, 4);
    float *cb_data = (float *)malloc(16 * sizeof(float));
    memcpy(cb_data, matrix, 16 * sizeof(float));
    void *cb_ptr = cb_data;
    memcpy(constant + 8, &cb_ptr, sizeof cb_ptr);

    uint8_t texture_header[0x44];
    make_texture(image_width, image_height, 9, bgr, texture_header);
    void *texture = eu4_gfx_texture_for_header(texture_header);
    if (texture == NULL) {
        fail("texture registry lookup");
    }
    EU4GfxBackend *backend = (__bridge EU4GfxBackend *)eu4_gfx_backend_token();
    if (backend == nil || backend.device == nil) {
        fail("backend token");
    }

    /* The real immediate-context master from _GfxInit: backend APIs require it.
       Established through replacement functions where one exists:
         - slot 0 header/offset/stride/count: eu4_gfx_set_vertex_buffers
           (this is the replacement for _GfxSetVertexBuffers);
         - current shader: eu4_gfx_set_shader (_GfxSetShader), asserted below;
         - viewport image: eu4_gfx_set_viewport (_GfxSetViewport image form).
       Still synthesized directly because no replacement API exists:
         - primitive mode +0x08 (the engine reaches mode 5 through
           _GfxSetPrimitiveType, which is not reconstructed here);
         - constant-buffer object +0xf0 (only the capture/observer path exists);
         - colour write mask +0x2c (read by _GfxClear, no setter replacement). */
    uint8_t *master = (uint8_t *)g_master;
    int32_t mode = 5, first = 0;
    memcpy(master + EU4_GFX_MASTER_MODE, &mode, 4);
    struct {
        void *master;
    } ctx = {master};
    void *headers[1] = {vertex_header};
    eu4_gfx_set_vertex_buffers(&ctx, 1, headers, 0, NULL, NULL);
    eu4_gfx_set_shader(&ctx, shader);
    if (!eu4_gfx_shader_is_current(&ctx, shader)) {
        fail("_GfxSetShader did not establish the current shader");
    }
    void *cb = constant;
    memcpy(master + EU4_GFX_MASTER_CONSTANT_SLOT2, &cb, sizeof cb);
    /* CGraphics::Setup2dGraphics viewport image for 1728x1117 and the default
       blend colour mask. _GfxClear refuses a viewport that does not cover the
       attachment. */
    uint32_t rect[6] = {kScreenHeight, kScreenWidth, 0x3f800000u, 0, 0, 0};
    eu4_gfx_set_viewport(master, rect);
    uint32_t color_mask = 0x01010101u;
    memcpy(master + 0x2c, &color_mask, sizeof color_mask);

    if (eu4_gfx_configure_offscreen(master, kScreenWidth, kScreenHeight) != EU4_GFX_CLEAR_OK) {
        fail("offscreen configure");
    }
    id<MTLTexture> target = backend.colorTarget;
    if (target == nil) {
        fail("target allocation");
    }

    const float clear[4] = {0.0f, 0.0f, 0.0f, 1.0f};
    if (eu4_gfx_clear_checked(&ctx, 1, clear) != EU4_GFX_CLEAR_OK) {
        fail("offscreen clear");
    }
    int command_status = 0;
    if (eu4_gfx_complete_offscreen(master, &command_status) != EU4_GFX_CLEAR_OK ||
        command_status != (int)MTLCommandBufferStatusCompleted) {
        fail("offscreen clear completion");
    }

    eu4_gfx_simple_draw_inputs inputs = {};
    inputs.device = (__bridge void *)backend.device;
    inputs.target = (__bridge void *)target;
    inputs.texture = texture;
    inputs.target_width = kScreenWidth;
    inputs.target_height = kScreenHeight;
    inputs.viewport_width = kScreenWidth;
    inputs.viewport_height = kScreenHeight;
    inputs.queue = (__bridge void *)backend.queue;

    const char *reason = NULL;
    /* Fail-closed boundary cases before the real draw. */
    {
        uint8_t saved = master[EU4_GFX_MASTER_MODE];
        master[EU4_GFX_MASTER_MODE] = 4;
        if (eu4_gfx_simple_draw(&ctx, 0, first, &inputs, &reason) !=
            EU4_GFX_SIMPLE_DRAW_PRIMITIVE) {
            fail("mode boundary was not refused");
        }
        master[EU4_GFX_MASTER_MODE] = saved;
        int32_t wrong_stride = 40;
        memcpy(master + EU4_GFX_MASTER_VERTEX_STRIDE, &wrong_stride, 4);
        if (eu4_gfx_simple_draw(&ctx, 0, first, &inputs, &reason) != EU4_GFX_SIMPLE_DRAW_VERTEX) {
            fail("stride boundary was not refused");
        }
        int32_t stride = EU4_GFX_SIMPLE_VERTEX_STRIDE;
        memcpy(master + EU4_GFX_MASTER_VERTEX_STRIDE, &stride, 4);
        if (eu4_gfx_simple_draw(&ctx, 2, first, &inputs, &reason) != EU4_GFX_SIMPLE_DRAW_COUNT) {
            fail("count boundary was not refused");
        }
        /* Highest referenced vertex, not just count * stride. A four-vertex
           buffer accepts first=1,count=3 and rejects first=1,count=4. */
        if (eu4_gfx_simple_draw(&ctx, 4, 0, &inputs, &reason) !=
            EU4_GFX_SIMPLE_DRAW_OK) {
            fail("first=0,count=4 was refused");
        }
        if (eu4_gfx_simple_draw(&ctx, 3, 1, &inputs, &reason) !=
            EU4_GFX_SIMPLE_DRAW_OK) {
            fail("first=1,count=3 was refused");
        }
        if (eu4_gfx_simple_draw(&ctx, 4, 1, &inputs, &reason) !=
            EU4_GFX_SIMPLE_DRAW_VERTEX) {
            fail("first=1,count=4 exceeded the buffer but was accepted");
        }
        if (eu4_gfx_simple_draw(&ctx, 4, -1, &inputs, &reason) !=
            EU4_GFX_SIMPLE_DRAW_VERTEX) {
            fail("negative first was accepted");
        }
        if (eu4_gfx_simple_draw(&ctx, INT32_MAX, INT32_MAX, &inputs, &reason) !=
            EU4_GFX_SIMPLE_DRAW_VERTEX) {
            fail("oversized vertex range was accepted or overflowed");
        }
        if (eu4_gfx_simple_draw(&ctx, 3, INT32_MAX, &inputs, &reason) !=
            EU4_GFX_SIMPLE_DRAW_VERTEX) {
            fail("oversized starting vertex was accepted or overflowed");
        }
        eu4_gfx_simple_draw_inputs none = inputs;
        none.texture = NULL;
        if (eu4_gfx_simple_draw(&ctx, 0, first, &none, &reason) != EU4_GFX_SIMPLE_DRAW_TEXTURE) {
            fail("missing texture was not refused");
        }
    }

    /* Shader identity boundary: a linked shader that is not the admitted Simple
       effect must not receive the Simple vertex layout or pipeline. */
    {
        void *other = eu4_gfx_create_shader();
        if (other == NULL ||
            eu4_gfx_add_vertex_shader(NULL, other, eu4_gfx_simple_vertex_source()) != 1 ||
            eu4_gfx_add_pixel_shader(NULL, other, eu4_gfx_simple_pixel_source()) != 1 ||
            eu4_gfx_link_shader(NULL, other) != 1 ||
            !eu4_gfx_shader_test_set_identity(other, "test/other-linked-effect")) {
            fail("other linked shader setup");
        }
        eu4_gfx_set_shader(&ctx, other);
        if (!eu4_gfx_shader_is_current(&ctx, other)) {
            fail("_GfxSetShader did not establish the other shader");
        }
        if (eu4_gfx_simple_draw(&ctx, 4, first, &inputs, &reason) !=
            EU4_GFX_SIMPLE_DRAW_SHADER) {
            fail("non-Simple linked shader was admitted");
        }
        eu4_gfx_set_shader(&ctx, shader);
        if (!eu4_gfx_shader_is_current(&ctx, shader)) {
            fail("Simple shader was not restored as current");
        }
        eu4_gfx_destroy_shader(other);
    }

    /* Bind through the replacement _GfxSetTextures, then let the encoder
       resolve unit 0 from the backend instead of passing a Metal pointer. */
    void *texture_pointer = texture_header;
    eu4_gfx_set_textures(&ctx, 1, &texture_pointer, 1, 0, NULL);
    if (eu4_gfx_bound_texture() != texture) {
        fail("unit-0 texture binding was not recorded");
    }
    inputs.texture = NULL;

    eu4_gfx_simple_draw_status status = eu4_gfx_simple_draw(&ctx, 0, first, &inputs, &reason);
    if (status != EU4_GFX_SIMPLE_DRAW_OK) {
        fprintf(stderr, "FAIL: draw refused: %s (%s)\n",
                eu4_gfx_simple_draw_status_name(status), reason != NULL ? reason : "?");
        exit(1);
    }

    uint8_t *pixels = read_target(target, kScreenWidth, kScreenHeight);
    cpu_image image = {image_width, image_height, bgr};
    int checked = 0;
    int mismatches = 0;
    int flip_differences = 0;
    double worst = 0.0;
    const int grid = 40;
    for (int iy = 0; iy < grid; iy++) {
        for (int ix = 0; ix < grid; ix++) {
            int px = 2 + ix * (kScreenWidth - 5) / (grid - 1);
            int py = 2 + iy * (kScreenHeight - 5) / (grid - 1);
            double u = 0.0, v = 0.0;
            expected_uv(px, py, kScreenWidth, kScreenHeight, matrix, &u, &v);
            double want[3], flipped[3];
            cpu_bilinear(&image, u, v, want);
            cpu_bilinear(&image, u, 1.0 - v, flipped);
            const uint8_t *pixel = pixels + ((size_t)py * kScreenWidth + px) * 4;
            double got[3] = {pixel[2] / 255.0, pixel[1] / 255.0, pixel[0] / 255.0};
            int bad = 0;
            double flip_delta = 0.0;
            for (int c = 0; c < 3; c++) {
                double delta = fabs(got[c] - want[c]);
                if (delta > worst) {
                    worst = delta;
                }
                if (delta > 6.0 / 255.0) {
                    bad = 1;
                }
                flip_delta += fabs(got[c] - flipped[c]);
            }
            if (bad) {
                mismatches++;
            }
            if (flip_delta > 24.0 / 255.0) {
                flip_differences++;
            }
            checked++;
        }
    }
    if (mismatches != 0) {
        fprintf(stderr, "FAIL: %d/%d samples mismatched, worst %.4f\n", mismatches, checked,
                worst);
        exit(1);
    }
    if (flip_differences < checked / 10) {
        fprintf(stderr, "FAIL: texture too uniform to prove orientation (%d/%d)\n",
                flip_differences, checked);
        exit(1);
    }
    free(pixels);

    /* Blend boundary: same geometry and shader, opaque 50%-alpha source over a
       cleared blue target. Contract: SRC_ALPHA / ONE_MINUS_SRC_ALPHA. */
    uint8_t source[4 * 4 * 4];
    for (int i = 0; i < 16; i++) {
        source[i * 4 + 0] = 0;    /* B */
        source[i * 4 + 1] = 0;    /* G */
        source[i * 4 + 2] = 255;  /* R */
        source[i * 4 + 3] = 128;  /* A */
    }
    uint8_t alpha_header[0x44];
    make_texture(4, 4, 0xb, source, alpha_header);
    if (eu4_gfx_texture_for_header(alpha_header) == NULL) {
        fail("alpha texture registry lookup");
    }
    const uint32_t small = 64;
    if (eu4_gfx_configure_offscreen(master, small, small) != EU4_GFX_CLEAR_OK) {
        fail("small offscreen configure");
    }
    id<MTLTexture> small_target = backend.colorTarget;
    if (small_target == nil) {
        fail("small target allocation");
    }
    uint32_t small_rect[6] = {small, small, 0x3f800000u, 0, 0, 0};
    eu4_gfx_set_viewport(master, small_rect);
    const float blue[4] = {0.0f, 0.0f, 1.0f, 1.0f};
    if (eu4_gfx_clear_checked(&ctx, 1, blue) != EU4_GFX_CLEAR_OK ||
        eu4_gfx_complete_offscreen(master, &command_status) != EU4_GFX_CLEAR_OK) {
        fail("blend target clear");
    }
    void *alpha_pointer = alpha_header;
    eu4_gfx_set_textures(&ctx, 1, &alpha_pointer, 1, 0, NULL);
    if (eu4_gfx_bound_texture() != eu4_gfx_texture_for_header(alpha_header)) {
        fail("alpha texture binding was not recorded");
    }
    eu4_gfx_simple_draw_inputs blend = inputs;
    blend.target = (__bridge void *)small_target;
    blend.texture = NULL;
    blend.target_width = small;
    blend.target_height = small;
    blend.viewport_width = small;
    blend.viewport_height = small;
    if (eu4_gfx_simple_draw(&ctx, 0, first, &blend, &reason) != EU4_GFX_SIMPLE_DRAW_OK) {
        fail("blend draw");
    }
    uint8_t *blend_pixels = read_target(small_target, small, small);
    int blend_bad = 0;
    for (int y = 0; y < (int)small; y++) {
        for (int x = 0; x < (int)small; x++) {
            const uint8_t *px = blend_pixels + ((size_t)y * small + x) * 4;
            int b = px[0], g = px[1], r = px[2];
            if (abs(b - 127) > 3 || abs(g - 0) > 3 || abs(r - 128) > 3) {
                blend_bad++;
            }
        }
    }
    if (blend_bad != 0) {
        fprintf(stderr, "FAIL: %d blend pixels outside tolerance\n", blend_bad);
        exit(1);
    }
    free(blend_pixels);

    /* Submission-order boundary: commit a green clear without waiting, then draw
       the 50% red source on the same backend queue. The expected (127,127,0)
       proves the draw is ordered after the clear; a private queue could run it
       first and the later clear would leave the target green. */
    const uint32_t order_size = 32;
    if (eu4_gfx_configure_offscreen(master, order_size, order_size) != EU4_GFX_CLEAR_OK) {
        fail("order target configure");
    }
    id<MTLTexture> order_target = backend.colorTarget;
    uint32_t order_rect[6] = {order_size, order_size, 0x3f800000u, 0, 0, 0};
    eu4_gfx_set_viewport(master, order_rect);
    const float green[4] = {0.0f, 1.0f, 0.0f, 1.0f};
    if (eu4_gfx_clear_checked(&ctx, 1, green) != EU4_GFX_CLEAR_OK) {
        fail("order clear");
    }
    eu4_gfx_simple_draw_inputs order = inputs;
    order.target = (__bridge void *)order_target;
    order.texture = NULL; /* bound alpha texture */
    order.target_width = order_size;
    order.target_height = order_size;
    order.viewport_width = order_size;
    order.viewport_height = order_size;
    if (eu4_gfx_simple_draw(&ctx, 0, first, &order, &reason) != EU4_GFX_SIMPLE_DRAW_OK) {
        fail("order draw");
    }
    uint8_t *order_pixels = read_target(order_target, order_size, order_size);
    const uint8_t *order_px = order_pixels + ((size_t)order_size / 2 * order_size +
                                              order_size / 2) * 4;
    if (abs(order_px[0] - 0) > 3 || abs(order_px[1] - 127) > 3 ||
        abs(order_px[2] - 127) > 3) {
        fprintf(stderr, "FAIL: clear/draw ordering (got %u,%u,%u)\n", order_px[0],
                order_px[1], order_px[2]);
        exit(1);
    }
    free(order_pixels);
    /* The ordering case intentionally left its clear pending. The draw waited
       on the shared queue, but the backend still owns the pending record. */
    if (eu4_gfx_complete_offscreen(master, &command_status) != EU4_GFX_CLEAR_OK ||
        command_status != (int)MTLCommandBufferStatusCompleted) {
        fail("order clear completion");
    }
    printf("ok checked=%d worst=%.4f flip_differences=%d blend_bad=%d order_bad=0\n", checked,
           worst, flip_differences, blend_bad);
    test_repeat_addressing(master, vb, matrix, backend);
}

static void parse_args(int argc, char **argv, const char **vb_path, const char **matrix_path,
                       const char **dds_path) {
    *vb_path = NULL;
    *matrix_path = NULL;
    *dds_path = NULL;
    for (int i = 1; i + 1 < argc; i += 2) {
        if (strcmp(argv[i], "--vb") == 0) {
            *vb_path = argv[i + 1];
        } else if (strcmp(argv[i], "--matrix") == 0) {
            *matrix_path = argv[i + 1];
        } else if (strcmp(argv[i], "--dds") == 0) {
            *dds_path = argv[i + 1];
        } else {
            fail("unknown argument");
        }
    }
    if (*vb_path == NULL || *matrix_path == NULL) {
        fail("--vb and --matrix are required");
    }
}

int main(int argc, char **argv) {
    const char *vb_path = NULL, *matrix_path = NULL, *dds_path = NULL;
    parse_args(argc, argv, &vb_path, &matrix_path, &dds_path);

    size_t vb_len = 0, matrix_len = 0;
    uint8_t *vb = (uint8_t *)read_file(vb_path, &vb_len);
    uint8_t *matrix_bytes = (uint8_t *)read_file(matrix_path, &matrix_len);
    if (vb == NULL || vb_len != 96 || matrix_bytes == NULL || matrix_len != 64) {
        fail("packet payload read");
    }
    float matrix[16];
    memcpy(matrix, matrix_bytes, sizeof matrix);

    uint8_t *bgr = NULL;
    uint32_t image_width = 2048, image_height = 1536;
    if (dds_path != NULL) {
        size_t dds_len = 0;
        uint8_t *dds = (uint8_t *)read_file(dds_path, &dds_len);
        if (dds == NULL || dds_len < 128 || memcmp(dds, "DDS ", 4) != 0) {
            fail("DDS header");
        }
        memcpy(&image_height, dds + 12, 4);
        memcpy(&image_width, dds + 16, 4);
        if (dds_len != 128 + (size_t)image_width * image_height * 3) {
            fail("DDS payload size");
        }
        bgr = dds + 128;
        printf("texture=original-dds %ux%u\n", image_width, image_height);
    } else {
        image_width = 2048;
        image_height = 1536;
        bgr = (uint8_t *)malloc((size_t)image_width * image_height * 3);
        if (bgr == NULL) {
            fail("synthetic texture allocation");
        }
        for (uint32_t y = 0; y < image_height; y++) {
            for (uint32_t x = 0; x < image_width; x++) {
                uint8_t *px = bgr + ((size_t)y * image_width + x) * 3;
                px[0] = (uint8_t)(x * 7 + y * 3);
                px[1] = (uint8_t)(y * 5);
                px[2] = (uint8_t)(x * 3 + y * 11);
            }
        }
        printf("texture=synthetic-bgr %ux%u\n", image_width, image_height);
    }

    boot();
    void *shader = make_simple_shader();
    test_authentic(vb, vb_len, matrix, shader, bgr, image_width, image_height);
    printf("done\n");
    return 0;
}
