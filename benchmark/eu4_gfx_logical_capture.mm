#import "eu4_gfx_logical_capture.h"

#import "eu4_gfx_draw_encode.h"

#import <CommonCrypto/CommonDigest.h>
#import <Metal/Metal.h>

#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

/* Diagnostic logical-extent capture.
 *
 * EU4_GFX_LOGICAL_CAPTURE=1 replays one selected draw into a fresh target
 * (the M4 contract). EU4_GFX_LOGICAL_CAPTURE=prefix keeps one target for the
 * configured prefix: every handled draw is encoded in order through the same
 * semantic dispatcher as the physical target, then the selected draw's target
 * is read back. Prior framebuffer contents are therefore part of the compared
 * image rather than silently missing. */

static _Atomic int g_attempted;
static _Atomic int g_completed;
static int g_prefix_initialized;
static id<MTLTexture> g_prefix_target;
static char g_prefix_draws[4096];
static size_t g_prefix_draws_used;
static int g_prefix_draw_count;

static const char *env_text(const char *name) {
    const char *value = getenv(name);
    return value != NULL ? value : "";
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

static int write_atomic(const char *path, const void *bytes, size_t length) {
    char partial[1300];
    if (snprintf(partial, sizeof partial, "%s.partial", path) >=
        (int)sizeof partial) {
        return 0;
    }
    FILE *file = fopen(partial, "wb");
    if (file == NULL) {
        return 0;
    }
    int ok = fwrite(bytes, 1, length, file) == length;
    if (ok) {
        ok = fflush(file) == 0;
    }
    if (ok) {
        ok = fsync(fileno(file)) == 0;
    }
    if (fclose(file) != 0) {
        ok = 0;
    }
    if (!ok) {
        (void)unlink(partial);
        return 0;
    }
    if (rename(partial, path) != 0) {
        (void)unlink(partial);
        return 0;
    }
    return 1;
}

static void write_failure(const char *dir, const char *reason,
                          uint64_t caller_rva) {
    char body[768];
    int length = snprintf(
        body, sizeof body,
        "{\"schema\":\"gfx-metal-logical-failure/v1\",\"reason\":\"%s\","
        "\"caller_rva\":\"0x%llx\",\"pid\":%d}\n",
        reason, (unsigned long long)caller_rva, (int)getpid());
    char path[1200];
    if (length > 0 && (size_t)length < sizeof body &&
        snprintf(path, sizeof path, "%s/metal-logical-failure.json", dir) <
            (int)sizeof path) {
        (void)write_atomic(path, body, (size_t)length);
    }
}

static id<MTLTexture> make_target(id<MTLDevice> device, uint32_t width,
                                  uint32_t height) {
    MTLTextureDescriptor *descriptor =
        [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm
                                                           width:width
                                                          height:height
                                                       mipmapped:NO];
    descriptor.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead;
    descriptor.storageMode = MTLStorageModePrivate;
    return [device newTextureWithDescriptor:descriptor];
}

/* Encode one handled draw into the diagnostic target through the same
   dispatcher the physical target uses. Returns 1 only after GPU completion. */
static int encode_into_target(id<MTLDevice> device, id<MTLCommandQueue> queue,
                              const void *immediate_context, int32_t count,
                              int32_t first,
                              const eu4_gfx_simple_draw_inputs *base_inputs,
                              id<MTLTexture> target, uint32_t width,
                              uint32_t height, const float *clear_color,
                              eu4_gfx_simple_draw_state *observed,
                              uint64_t caller_rva, const char *dir) {
    eu4_gfx_simple_draw_inputs inputs = *base_inputs;
    inputs.device = (__bridge void *)device;
    inputs.queue = (__bridge void *)queue;
    inputs.target = (__bridge void *)target;
    inputs.target_width = width;
    inputs.target_height = height;
    inputs.logical_width = width;
    inputs.logical_height = height;
    inputs.clear_color = clear_color;
    memset(observed, 0, sizeof *observed);
    id<MTLCommandBuffer> command = [queue commandBuffer];
    if (command == nil) {
        write_failure(dir, "logical command buffer failed", caller_rva);
        return 0;
    }
    char diagnostic[256];
    eu4_gfx_draw_encode_result encoded = eu4_gfx_draw_encode_into(
        immediate_context, count, first, &inputs, (__bridge void *)command,
        diagnostic, sizeof diagnostic, observed);
    if (encoded != EU4_GFX_DRAW_ENCODE_OK) {
        char text[384];
        snprintf(text, sizeof text, "logical encode: %s",
                 diagnostic[0] != 0 ? diagnostic : "draw encoder refused");
        write_failure(dir, text, caller_rva);
        return 0;
    }
    [command commit];
    [command waitUntilCompleted];
    if (command.status != MTLCommandBufferStatusCompleted || command.error != nil) {
        write_failure(dir, "logical encode command did not complete", caller_rva);
        return 0;
    }
    return 1;
}

static int write_logical_output(id<MTLDevice> device, id<MTLCommandQueue> queue,
                                id<MTLTexture> target, uint32_t width,
                                uint32_t height,
                                const eu4_gfx_simple_draw_inputs *base_inputs,
                                const eu4_gfx_simple_draw_state *state,
                                uint64_t caller_rva, const char *dir,
                                const char *prefix_json, int prefix_count) {
    MTLTextureDescriptor *staging_descriptor =
        [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm
                                                           width:width
                                                          height:height
                                                       mipmapped:NO];
    staging_descriptor.usage = MTLTextureUsageShaderRead;
    staging_descriptor.storageMode = MTLStorageModeShared;
    id<MTLTexture> staging = [device newTextureWithDescriptor:staging_descriptor];
    if (staging == nil) {
        write_failure(dir, "logical staging allocation failed", caller_rva);
        return 0;
    }
    id<MTLCommandBuffer> command = [queue commandBuffer];
    if (command == nil) {
        write_failure(dir, "logical readback command buffer failed", caller_rva);
        return 0;
    }
    id<MTLBlitCommandEncoder> blit = [command blitCommandEncoder];
    if (blit == nil) {
        write_failure(dir, "logical blit encoder failed", caller_rva);
        return 0;
    }
    [blit copyFromTexture:target
              sourceSlice:0
              sourceLevel:0
             sourceOrigin:MTLOriginMake(0, 0, 0)
               sourceSize:MTLSizeMake(width, height, 1)
                toTexture:staging
         destinationSlice:0
         destinationLevel:0
        destinationOrigin:MTLOriginMake(0, 0, 0)];
    [blit endEncoding];
    [command commit];
    [command waitUntilCompleted];
    if (command.status != MTLCommandBufferStatusCompleted || command.error != nil) {
        write_failure(dir, "logical readback command did not complete", caller_rva);
        return 0;
    }
    size_t need = (size_t)width * height * 4u;
    uint8_t *pixels = (uint8_t *)malloc(need);
    if (pixels == NULL) {
        write_failure(dir, "logical payload allocation failed", caller_rva);
        return 0;
    }
    [staging getBytes:pixels
          bytesPerRow:(NSUInteger)width * 4u
           fromRegion:MTLRegionMake2D(0, 0, width, height)
          mipmapLevel:0];
    char digest[65];
    sha256_hex(pixels, need, digest);
    const char *raw_name = "metal-logical.bgra";
    char raw_path[1200];
    int wrote = 0;
    if (snprintf(raw_path, sizeof raw_path, "%s/%s", dir, raw_name) <
        (int)sizeof raw_path) {
        wrote = write_atomic(raw_path, pixels, need);
    }
    free(pixels);
    if (!wrote) {
        write_failure(dir, "logical payload write failed", caller_rva);
        return 0;
    }
    char body[8192];
    int length = snprintf(
        body, sizeof body,
        "{\"schema\":\"gfx-metal-logical/v1\",\"backend\":\"metal\","
        "\"provenance\":{\"executable\":\"%s\",\"executable_sha256\":\"%s\","
        "\"library_sha256\":\"%s\",\"session\":\"%s\",\"pid\":%d},"
        "\"draw\":{\"symbol\":\"_GfxDraw\",\"caller_rva\":\"0x%llx\","
        "\"caller_known\":true,\"mode\":%d,\"effective_count\":%d,"
        "\"first\":%d,\"shader_identity\":\"%s\","
        "\"vertex_hex\":\"%s\",\"vertex_sha256_fnv\":\"0x%llx\"},"
        "\"target\":{\"width\":%u,\"height\":%u,\"row_stride\":%u,"
        "\"pixel_format\":\"BGRA8\",\"origin\":\"top_left\","
        "\"storage\":\"BGRA8Unorm\",\"physical_scale\":[%u,%u]},"
        "\"gpu\":{\"completed\":true,"
        "\"evidence\":\"MTLCommandBufferStatusCompleted + staging blit\","
        "\"command_status\":%d},"
        "\"capture\":{\"raw_file\":\"%s\",\"raw_bytes\":%zu,"
        "\"raw_sha256\":\"%s\",\"pixel_format\":\"BGRA8\"},"
        "\"state\":{\"blend_supported\":%d,\"depth_default\":%d,"
        "\"raster_default\":%d,\"scissor_full\":%d,"
        "\"vertex_stride\":%u,\"vertex_offset\":%u,"
        "\"vertex_bytes\":%llu,\"vertex_sha256_fnv\":\"0x%llx\","
        "\"vertex_hex\":\"%s\",\"constant_hex\":\"%s\","
        "\"texture_size\":[%u,%u],"
        "\"logical_viewport\":[%u,%u,%u,%u]},"
        "\"shader\":{\"identity\":\"%s\"},"
        "\"prefix\":{\"count\":%d,\"draws\":%s},"
        "\"presentation\":null}\n",
        env_text("EU4_GFX_EXPECTED_EXE"),
        env_text("EU4_GFX_EXECUTABLE_SHA256"),
        env_text("EU4_GFX_LIBRARY_SHA256"), env_text("EU4_GFX_STARTUP_ID"),
        (int)getpid(), (unsigned long long)caller_rva,
        (int)(state != NULL ? state->primitive : 5),
        (int)(state != NULL ? state->count : 0),
        (int)(state != NULL ? state->first : 0),
        state != NULL ? state->shader_identity : "",
        state != NULL ? state->vertex_hex : "",
        state != NULL ? (unsigned long long)state->vertex_hash : 0,
        width, height, width * 4u,
        base_inputs->target_width, base_inputs->target_height,
        (int)MTLCommandBufferStatusCompleted, raw_name, need, digest,
        state != NULL ? state->blend_supported : 0,
        state != NULL ? state->depth_default : 0,
        state != NULL ? state->raster_default : 0,
        state != NULL ? state->scissor_full : 0,
        state != NULL ? state->vertex_stride : 0,
        state != NULL ? state->vertex_offset : 0,
        state != NULL ? (unsigned long long)state->vertex_bytes : 0,
        state != NULL ? (unsigned long long)state->vertex_hash : 0,
        state != NULL ? state->vertex_hex : "",
        state != NULL ? state->constant_hex : "",
        state != NULL ? state->texture_width : 0,
        state != NULL ? state->texture_height : 0,
        base_inputs->viewport_x, base_inputs->viewport_y,
        base_inputs->viewport_width, base_inputs->viewport_height,
        state != NULL ? state->shader_identity : "", prefix_count,
        prefix_json != NULL ? prefix_json : "[]");
    char json_path[1200];
    if (length <= 0 || (size_t)length >= sizeof body ||
        snprintf(json_path, sizeof json_path, "%s/metal-logical.json", dir) >=
            (int)sizeof json_path ||
        !write_atomic(json_path, body, (size_t)length)) {
        write_failure(dir, "logical descriptor write failed", caller_rva);
        return 0;
    }
    return 1;
}

int eu4_gfx_logical_capture_from_env(
    void *device, void *queue, const void *immediate_context, int32_t count,
    int32_t first, const eu4_gfx_simple_draw_inputs *base_inputs,
    const eu4_gfx_simple_draw_state *state, uint64_t caller_rva) {
    const char *enabled = getenv("EU4_GFX_LOGICAL_CAPTURE");
    const char *dir = getenv("EU4_GFX_CHECKPOINT_DIR");
    if (enabled == NULL || enabled[0] == 0 ||
        (strcmp(enabled, "1") != 0 && strcmp(enabled, "prefix") != 0)) {
        return 0;
    }
    if (device == NULL || queue == NULL || base_inputs == NULL ||
        immediate_context == NULL) {
        return 0;
    }
    if (dir == NULL || dir[0] != '/' || strlen(dir) >= 1024) {
        return 0;
    }
    id<MTLDevice> metal_device = (__bridge id<MTLDevice>)device;
    id<MTLCommandQueue> metal_queue = (__bridge id<MTLCommandQueue>)queue;
    uint32_t width = base_inputs->logical_width != 0
                         ? base_inputs->logical_width
                         : base_inputs->target_width;
    uint32_t height = base_inputs->logical_height != 0
                          ? base_inputs->logical_height
                          : base_inputs->target_height;
    if (width == 0 || height == 0 || (uint64_t)width * height > 64u * 1024u * 1024u) {
        write_failure(dir, "invalid logical extent", caller_rva);
        return 0;
    }

    const int prefix_mode = strcmp(enabled, "prefix") == 0;
    id<MTLTexture> target = nil;
    const float *clear_color = NULL;
    static const float black[4] = {0.0f, 0.0f, 0.0f, 1.0f};
    if (prefix_mode) {
        if (g_prefix_target == nil) {
            g_prefix_target = make_target(metal_device, width, height);
            if (g_prefix_target == nil) {
                write_failure(dir, "logical target allocation failed", caller_rva);
                return 0;
            }
            g_prefix_initialized = 0;
        }
        if (g_prefix_target.width != width || g_prefix_target.height != height) {
            write_failure(dir, "logical target extent changed across the prefix",
                          caller_rva);
            return 0;
        }
        target = g_prefix_target;
        if (!g_prefix_initialized) {
            clear_color = black;
        }
    } else {
        int expected = 0;
        if (!atomic_compare_exchange_strong_explicit(
                &g_attempted, &expected, 1, memory_order_acq_rel,
                memory_order_relaxed)) {
            return 0;
        }
        target = make_target(metal_device, width, height);
        if (target == nil) {
            write_failure(dir, "logical target allocation failed", caller_rva);
            return 0;
        }
    }

    eu4_gfx_simple_draw_state observed;
    if (!encode_into_target(metal_device, metal_queue, immediate_context, count,
                            first, base_inputs, target, width, height,
                            clear_color, &observed, caller_rva, dir)) {
        return 0;
    }
    if (prefix_mode) {
        g_prefix_initialized = 1;
        int wrote = snprintf(
            g_prefix_draws + g_prefix_draws_used,
            sizeof g_prefix_draws - g_prefix_draws_used,
            "%s{\"caller_rva\":\"0x%llx\",\"shader_identity\":\"%s\","
            "\"count\":%d,\"first\":%d}",
            g_prefix_draw_count == 0 ? "" : ",",
            (unsigned long long)caller_rva, observed.shader_identity,
            (int)observed.count, (int)observed.first);
        if (wrote < 0 ||
            (size_t)wrote >= sizeof g_prefix_draws - g_prefix_draws_used) {
            write_failure(dir, "logical prefix record overflow", caller_rva);
            return 0;
        }
        g_prefix_draws_used += (size_t)wrote;
        g_prefix_draw_count += 1;
    }

    /* In prefix mode, only the configured final draw is read back. Without a
       stop caller the last handled draw wins. */
    const char *stop_text = env_text("EU4_GFX_LOGICAL_STOP_CALLER");
    uint64_t stop_caller = 0;
    if (stop_text[0] != 0) {
        char *end = NULL;
        stop_caller = strtoull(stop_text, &end, 0);
        if (end == stop_text || *end != 0) {
            write_failure(dir, "invalid logical stop caller", caller_rva);
            return 0;
        }
    }
    if (stop_caller != 0 && caller_rva != stop_caller) {
        return 1;
    }

    char prefix_json[4096];
    int prefix_count = 0;
    if (prefix_mode) {
        if (g_prefix_draws_used + 3 <= sizeof g_prefix_draws) {
            memcpy(g_prefix_draws + g_prefix_draws_used, "]", 2);
            g_prefix_draws[g_prefix_draws_used + 1] = 0;
        }
        snprintf(prefix_json, sizeof prefix_json, "[%s", g_prefix_draws);
        prefix_count = g_prefix_draw_count;
    } else {
        snprintf(prefix_json, sizeof prefix_json, "[]");
    }
    if (!write_logical_output(metal_device, metal_queue, target, width, height,
                              base_inputs, &observed, caller_rva, dir,
                              prefix_json, prefix_count)) {
        return 0;
    }
    atomic_store_explicit(&g_completed, 1, memory_order_release);
    return 1;
}
