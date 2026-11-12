/* Offline GPU replay of the authentic sprite draw.
 *
 * The live engine now reaches and GPU-completes the selected draw. This host
 * remains the exact isolated pixel qualification: it drives the same sprite
 * encoder with the authentic original-GL inputs (vertex, constants, texture
 * and pre-draw) so the sprite can be compared without the unreconstructed
 * loading-screen background. It is not a second renderer; it is a replay
 * boundary around eu4_gfx_draw_encode_into.
 *
 * Usage:
 *   gfx_m5_replay_host --reference <gl-ref-dir> --out <dir> [--constant-words <n>]
 */

#import "eu4_gfx_buffers.h"
#import "eu4_gfx_draw_encode.h"
#import "eu4_gfx_init.h"
#import "eu4_gfx_shader.h"
#import "eu4_gfx_sprite_draw.h"
#import "eu4_metal_events.h"

#import <CommonCrypto/CommonDigest.h>
#import <Metal/Metal.h>
#import <QuartzCore/CAMetalLayer.h>

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

enum {
    kLogicalWidth = 1728,
    kLogicalHeight = 1117,
    kImageBytes = 0x2300000,
    kCallerRva = 0x14f6c1b,
};

static uint8_t *g_image;
static void *g_slot;
static void *g_master;

static void fail(const char *message) {
    fprintf(stderr, "FAIL: %s\n", message);
    exit(1);
}

static void *fake_window(uint32_t window_id) { return window_id == 7 ? (void *)0x11 : NULL; }

static void fake_size(void *window, int *width, int *height) {
    (void)window;
    *width = 64;
    *height = 64;
}

static void write_depth(void *dest) { eu4_gfx_write_default_depth_stencil((uint8_t *)dest); }

static void on_stop(const char *reason) { (void)reason; }

static uint8_t *read_file(const char *path, size_t *out_length) {
    FILE *file = fopen(path, "rb");
    if (file == NULL) {
        fprintf(stderr, "cannot open %s\n", path);
        exit(1);
    }
    if (fseek(file, 0, SEEK_END) != 0) {
        exit(1);
    }
    long length = ftell(file);
    if (length <= 0 || fseek(file, 0, SEEK_SET) != 0) {
        exit(1);
    }
    uint8_t *data = (uint8_t *)malloc((size_t)length);
    if (data == NULL || fread(data, 1, (size_t)length, file) != (size_t)length) {
        exit(1);
    }
    fclose(file);
    *out_length = (size_t)length;
    return data;
}

static void sha256_hex(const void *bytes, size_t length, char out[65]) {
    uint8_t digest[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256(bytes, (CC_LONG)length, digest);
    static const char digits[] = "0123456789abcdef";
    for (int i = 0; i < CC_SHA256_DIGEST_LENGTH; i++) {
        out[i * 2] = digits[digest[i] >> 4];
        out[i * 2 + 1] = digits[digest[i] & 0xf];
    }
    out[64] = 0;
}

static void hex_bytes(const uint8_t *bytes, size_t length, char *out) {
    static const char digits[] = "0123456789abcdef";
    for (size_t i = 0; i < length; i++) {
        out[i * 2] = digits[bytes[i] >> 4];
        out[i * 2 + 1] = digits[bytes[i] & 0xf];
    }
    out[length * 2] = 0;
}

static uint8_t *flip_rows(const uint8_t *src, uint32_t width, uint32_t height) {
    size_t row = (size_t)width * 4u;
    uint8_t *out = (uint8_t *)malloc(row * height);
    if (out == NULL) {
        fail("row flip allocation");
    }
    for (uint32_t y = 0; y < height; y++) {
        memcpy(out + (size_t)y * row,
               src + (size_t)(height - 1 - y) * row, row);
    }
    return out;
}

static id<MTLTexture> make_texture(id<MTLDevice> device, uint32_t width,
                                   uint32_t height, MTLTextureUsage usage) {
    MTLTextureDescriptor *descriptor =
        [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm
                                                           width:width
                                                          height:height
                                                       mipmapped:NO];
    descriptor.usage = usage;
    descriptor.storageMode = MTLStorageModeShared;
    return [device newTextureWithDescriptor:descriptor];
}

static void boot(void) {
    g_image = (uint8_t *)mmap(NULL, kImageBytes, PROT_READ | PROT_WRITE,
                              MAP_PRIVATE | MAP_ANON, -1, 0);
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
    layer.drawableSize = CGSizeMake(64, 64);
    eu4_gfx_set_layer((__bridge void *)layer);
    eu4_gfx_publish_physical_geometry(64, 64);
    char dir[] = "/tmp/eu4-m5-replay-XXXXXX";
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
}

int main(int argc, char **argv) {
    const char *reference = NULL;
    const char *out_dir = NULL;
    uint32_t constant_words = 28; /* 112-byte authentic payload / 4 */
    for (int i = 1; i + 1 < argc; i += 2) {
        if (strcmp(argv[i], "--reference") == 0) {
            reference = argv[i + 1];
        } else if (strcmp(argv[i], "--out") == 0) {
            out_dir = argv[i + 1];
        } else if (strcmp(argv[i], "--constant-words") == 0) {
            char *end = NULL;
            unsigned long value = strtoul(argv[i + 1], &end, 0);
            if (end == argv[i + 1] || *end != 0 || value > 0xfffffffful) {
                fprintf(stderr, "invalid --constant-words\n");
                return 2;
            }
            constant_words = (uint32_t)value;
        }
    }
    if (reference == NULL || out_dir == NULL) {
        fprintf(stderr, "usage: %s --reference <dir> --out <dir>\n", argv[0]);
        return 2;
    }
    char path[1200];
    size_t vertex_length = 0, constant_length = 0, texture_length = 0,
           pre_length = 0;
    snprintf(path, sizeof path, "%s/gl-reference-vertex.bin", reference);
    uint8_t *vertex = read_file(path, &vertex_length);
    snprintf(path, sizeof path, "%s/gl-reference-constant.bin", reference);
    uint8_t *constant = read_file(path, &constant_length);
    snprintf(path, sizeof path, "%s/gl-reference-texture.bgra", reference);
    uint8_t *texture_bytes = read_file(path, &texture_length);
    snprintf(path, sizeof path, "%s/gl-reference-pre.bgra", reference);
    uint8_t *pre = read_file(path, &pre_length);
    if (vertex_length != 80 || constant_length < 96 ||
        texture_length != (size_t)1235 * 175 * 4 ||
        pre_length != (size_t)kLogicalWidth * kLogicalHeight * 4) {
        fail("captured input size mismatch");
    }

    boot();
    id<MTLDevice> device = MTLCreateSystemDefaultDevice();
    id<MTLCommandQueue> queue = [device newCommandQueue];
    if (queue == nil) {
        fail("queue");
    }

    void *shader = eu4_gfx_create_shader();
    if (shader == NULL ||
        !eu4_gfx_shader_test_compile_identity(shader, EU4_GFX_SHADER_SPRITE_IDENTITY)) {
        fail("sprite shader compile");
    }
    struct {
        void *master;
    } ctx = {g_master};
    eu4_gfx_set_shader(&ctx, shader);

    void *vertex_header = eu4_gfx_create_vertex_buffer(NULL, vertex, 4, 20, 0);
    if (vertex_header == NULL) {
        fail("vertex buffer");
    }
    memcpy((uint8_t *)g_master + EU4_GFX_MASTER_VERTEX_HEADER, &vertex_header,
           sizeof vertex_header);
    uint32_t vertex_offset = 0, vertex_stride = 20, count = 4, mode = 5;
    memcpy((uint8_t *)g_master + EU4_GFX_MASTER_VERTEX_OFFSET, &vertex_offset, 4);
    memcpy((uint8_t *)g_master + EU4_GFX_MASTER_VERTEX_STRIDE, &vertex_stride, 4);
    memcpy((uint8_t *)g_master + EU4_GFX_MASTER_MODE, &mode, 4);
    memcpy((uint8_t *)g_master + EU4_GFX_MASTER_COUNT, &count, 4);

    struct {
        uint32_t words;
        uint32_t padding;
        const void *data;
    } constant_header = {constant_words, 0, constant};
    void *constant_ptr = &constant_header;
    memcpy((uint8_t *)g_master + EU4_GFX_MASTER_CONSTANT_SLOT2, &constant_ptr,
           sizeof constant_ptr);
    memcpy((uint8_t *)g_master + 0xc8, &constant_words, 4);

    static const uint8_t blend[0x24] = {
        0x01, 0x00, 0x00, 0x00, 0x02, 0x03, 0x00, 0x00, 0x03, 0x03, 0x00,
        0x00, 0x06, 0x80, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00,
        0x00, 0x00, 0x06, 0x80, 0x00, 0x00, 0x06, 0x02, 0x00, 0x00, 0x01,
        0x01, 0x01, 0x01,
    };
    static const uint8_t raster[0x10] = {
        0x05, 0x04, 0x00, 0x00, 0x02, 0x1b, 0x00, 0x00,
        0x00, 0x09, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00,
    };
    uint8_t blend_copy[0x24];
    memcpy(blend_copy, blend, sizeof blend_copy);
    if (getenv("EU4_M5_REPLAY_BAD_STATE") != NULL) {
        static const uint8_t unsupported[0x24] = {
            0x00, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00,
            0x00, 0x00, 0x06, 0x80, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00,
            0x00, 0x00, 0x00, 0x00, 0x06, 0x80, 0x00, 0x00, 0x06, 0x02,
            0x00, 0x00, 0x01, 0x01, 0x01, 0x01,
        };
        memcpy(blend_copy, unsupported, sizeof blend_copy);
    }
    memcpy((uint8_t *)g_master + 0xc, blend_copy, sizeof blend_copy);
    memcpy((uint8_t *)g_master + 0x54, raster, sizeof raster);
    uint32_t rect[6] = {kLogicalWidth, kLogicalHeight, 0x3f800000u, 0, 0, 0};
    eu4_gfx_set_viewport(g_master, rect);

    id<MTLTexture> target = make_texture(
        device, kLogicalWidth, kLogicalHeight,
        MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead);
    id<MTLTexture> sprite = make_texture(
        device, 1235, 175, MTLTextureUsageShaderRead);
    if (target == nil || sprite == nil) {
        fail("texture allocation");
    }
    uint8_t *pre_top = flip_rows(pre, kLogicalWidth, kLogicalHeight);
    [target replaceRegion:MTLRegionMake2D(0, 0, kLogicalWidth, kLogicalHeight)
              mipmapLevel:0
                withBytes:pre_top
              bytesPerRow:(NSUInteger)kLogicalWidth * 4u];
    free(pre_top);
    [sprite replaceRegion:MTLRegionMake2D(0, 0, 1235, 175)
              mipmapLevel:0
                withBytes:texture_bytes
              bytesPerRow:(NSUInteger)1235 * 4u];

    eu4_gfx_simple_draw_inputs inputs = {};
    inputs.device = (__bridge void *)device;
    inputs.target = (__bridge void *)target;
    inputs.texture = (__bridge void *)sprite;
    inputs.target_width = kLogicalWidth;
    inputs.target_height = kLogicalHeight;
    inputs.logical_width = kLogicalWidth;
    inputs.logical_height = kLogicalHeight;
    inputs.viewport_x = 0;
    inputs.viewport_y = 0;
    inputs.viewport_width = kLogicalWidth;
    inputs.viewport_height = kLogicalHeight;
    inputs.clear_color = NULL;

    id<MTLCommandBuffer> command = [queue commandBuffer];
    char diagnostic[256];
    eu4_gfx_simple_draw_state state;
    eu4_gfx_draw_encode_result encoded = eu4_gfx_draw_encode_into(
        &ctx, 4, 0, &inputs, (__bridge void *)command, diagnostic,
        sizeof diagnostic, &state);
    if (encoded != EU4_GFX_DRAW_ENCODE_OK) {
        fprintf(stderr, "encode failed: %s\n", diagnostic);
        return 1;
    }
    [command commit];
    [command waitUntilCompleted];
    if (command.status != MTLCommandBufferStatusCompleted || command.error != nil) {
        fail("command did not complete");
    }
    size_t need = (size_t)kLogicalWidth * kLogicalHeight * 4u;
    uint8_t *pixels = (uint8_t *)malloc(need);
    if (pixels == NULL) {
        fail("readback allocation");
    }
    [target getBytes:pixels
         bytesPerRow:(NSUInteger)kLogicalWidth * 4u
          fromRegion:MTLRegionMake2D(0, 0, kLogicalWidth, kLogicalHeight)
         mipmapLevel:0];
    char digest[65];
    sha256_hex(pixels, need, digest);
    snprintf(path, sizeof path, "%s/metal-logical.bgra", out_dir);
    FILE *raw = fopen(path, "wb");
    if (raw == NULL || fwrite(pixels, 1, need, raw) != need || fclose(raw) != 0) {
        fail("payload write");
    }
    char vertex_hex[193], constant_hex[257];
    hex_bytes(vertex, 80, vertex_hex);
    hex_bytes(constant, 96, constant_hex);
    char constant_sha[65], texture_sha[65];
    sha256_hex(constant, constant_length, constant_sha);
    sha256_hex(texture_bytes, texture_length, texture_sha);
    const char *executable = getenv("EU4_GFX_EXPECTED_EXE");
    const char *executable_sha = getenv("EU4_GFX_EXECUTABLE_SHA256");
    char body[8192];
    int length = snprintf(
        body, sizeof body,
        "{\"schema\":\"gfx-metal-logical/v1\",\"backend\":\"metal\","
        "\"provenance\":{\"executable\":\"%s\",\"executable_sha256\":\"%s\","
        "\"library_sha256\":\"m5-replay-host\",\"session\":\"m5-replay-%d\","
        "\"pid\":%d},"
        "\"draw\":{\"symbol\":\"_GfxDraw\",\"caller_rva\":\"0x%x\","
        "\"caller_known\":true,\"mode\":5,\"effective_count\":4,\"first\":0,"
        "\"shader_identity\":\"%s\",\"vertex_hex\":\"%s\"},"
        "\"target\":{\"width\":%d,\"height\":%d,\"row_stride\":%d,"
        "\"pixel_format\":\"BGRA8\",\"origin\":\"top_left\","
        "\"storage\":\"BGRA8Unorm\",\"physical_scale\":[%d,%d]},"
        "\"gpu\":{\"completed\":true,\"evidence\":\"MTLCommandBufferStatusCompleted"
        " + shared target readback\",\"command_status\":%d},"
        "\"capture\":{\"raw_file\":\"metal-logical.bgra\",\"raw_bytes\":%zu,"
        "\"raw_sha256\":\"%s\",\"pixel_format\":\"BGRA8\"},"
        "\"state\":{\"blend_supported\":%d,\"depth_default\":%d,"
        "\"raster_default\":%d,\"scissor_full\":%d,\"vertex_stride\":%u,"
        "\"vertex_offset\":%u,\"vertex_bytes\":%llu,"
        "\"constant_hex\":\"%s\",\"constant_sha256\":\"%s\","
        "\"texture_size\":[%u,%u],\"texture_sha256\":\"%s\","
        "\"sampler\":{\"min_filter\":%u,\"mag_filter\":%u,"
        "\"wrap_s\":%u,\"wrap_t\":%u},"
        "\"logical_viewport\":[%u,%u,%u,%u]},"
        "\"shader\":{\"identity\":\"%s\"},"
        "\"prefix\":{\"count\":1,\"draws\":[{\"caller_rva\":\"0x%x\","
        "\"shader_identity\":\"%s\",\"count\":4,\"first\":0}]},"
        "\"presentation\":null}\n",
        executable != NULL ? executable : "",
        executable_sha != NULL ? executable_sha : "", (int)getpid(),
        (int)getpid(), kCallerRva, state.shader_identity, vertex_hex,
        kLogicalWidth, kLogicalHeight, kLogicalWidth * 4, kLogicalWidth,
        kLogicalHeight, (int)MTLCommandBufferStatusCompleted, need, digest,
        state.blend_supported, state.depth_default, state.raster_default,
        state.scissor_full, state.vertex_stride, state.vertex_offset,
        (unsigned long long)state.vertex_bytes, constant_hex, constant_sha,
        1235, 175, texture_sha, state.sampler_min_filter, state.sampler_mag_filter,
        state.sampler_wrap_s, state.sampler_wrap_t, inputs.viewport_x, inputs.viewport_y,
        inputs.viewport_width, inputs.viewport_height, state.shader_identity,
        kCallerRva, state.shader_identity);
    snprintf(path, sizeof path, "%s/metal-logical.json", out_dir);
    FILE *json = fopen(path, "wb");
    if (json == NULL || fwrite(body, 1, (size_t)length, json) != (size_t)length ||
        fclose(json) != 0) {
        fail("descriptor write");
    }
    printf("replay=ok command_status=%d raw_sha256=%s\n",
           (int)MTLCommandBufferStatusCompleted, digest);
    return 0;
}
