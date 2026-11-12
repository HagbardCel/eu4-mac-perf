#import "eu4_gfx_logical_capture.h"

#import <CommonCrypto/CommonDigest.h>
#import <Metal/Metal.h>

#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

static _Atomic int g_attempted;
static _Atomic int g_completed;

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

int eu4_gfx_logical_capture_from_env(
    void *device, void *queue, const void *immediate_context, int32_t count,
    int32_t first, const eu4_gfx_simple_draw_inputs *base_inputs,
    const eu4_gfx_simple_draw_state *state, uint64_t caller_rva) {
    const char *enabled = getenv("EU4_GFX_LOGICAL_CAPTURE");
    const char *dir = getenv("EU4_GFX_CHECKPOINT_DIR");
    if (enabled == NULL || enabled[0] == 0 || strcmp(enabled, "1") != 0) {
        return 0;
    }
    if (device == NULL || queue == NULL || base_inputs == NULL ||
        immediate_context == NULL) {
        return 0;
    }
    if (dir == NULL || dir[0] != '/' || strlen(dir) >= 1024) {
        return 0;
    }
    int expected = 0;
    if (!atomic_compare_exchange_strong_explicit(
            &g_attempted, &expected, 1, memory_order_acq_rel,
            memory_order_relaxed)) {
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
    MTLTextureDescriptor *descriptor =
        [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm
                                                           width:width
                                                          height:height
                                                       mipmapped:NO];
    descriptor.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead;
    descriptor.storageMode = MTLStorageModePrivate;
    id<MTLTexture> target = [metal_device newTextureWithDescriptor:descriptor];
    if (target == nil) {
        write_failure(dir, "logical target allocation failed", caller_rva);
        return 0;
    }
    eu4_gfx_simple_draw_inputs inputs = *base_inputs;
    inputs.device = device;
    inputs.queue = queue;
    inputs.target = (__bridge void *)target;
    inputs.target_width = width;
    inputs.target_height = height;
    inputs.logical_width = width;
    inputs.logical_height = height;
    inputs.clear_color = NULL;
    const char *reason = NULL;
    eu4_gfx_simple_draw_state observed;
    memset(&observed, 0, sizeof observed);
    eu4_gfx_simple_draw_status status =
        eu4_gfx_simple_draw_with_state(immediate_context, count, first, &inputs,
                                       &reason, &observed);
    if (status != EU4_GFX_SIMPLE_DRAW_OK) {
        char detail[256];
        char text[512];
        snprintf(detail, sizeof detail, "%s",
                 reason != NULL ? reason : "no detail");
        snprintf(text, sizeof text,
                 "logical encode %s: %s",
                 eu4_gfx_simple_draw_status_name(status), detail);
        write_failure(dir, text, caller_rva);
        return 0;
    }
    MTLTextureDescriptor *staging_descriptor =
        [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm
                                                           width:width
                                                          height:height
                                                       mipmapped:NO];
    staging_descriptor.usage = MTLTextureUsageShaderRead;
    staging_descriptor.storageMode = MTLStorageModeShared;
    id<MTLTexture> staging = [metal_device newTextureWithDescriptor:staging_descriptor];
    if (staging == nil) {
        write_failure(dir, "logical staging allocation failed", caller_rva);
        return 0;
    }
    id<MTLCommandBuffer> command = [metal_queue commandBuffer];
    if (command == nil) {
        write_failure(dir, "logical command buffer failed", caller_rva);
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
    char body[4096];
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
        "\"presentation\":null}\n",
        env_text("EU4_GFX_EXPECTED_EXE"),
        env_text("EU4_GFX_EXECUTABLE_SHA256"),
        env_text("EU4_GFX_LIBRARY_SHA256"), env_text("EU4_GFX_STARTUP_ID"),
        (int)getpid(), (unsigned long long)caller_rva,
        (int)(state != NULL ? state->primitive : 5), (int)count, (int)first,
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
        state != NULL ? state->shader_identity : "");
    char json_path[1200];
    if (length <= 0 || (size_t)length >= sizeof body ||
        snprintf(json_path, sizeof json_path, "%s/metal-logical.json", dir) >=
            (int)sizeof json_path ||
        !write_atomic(json_path, body, (size_t)length)) {
        write_failure(dir, "logical descriptor write failed", caller_rva);
        return 0;
    }
    atomic_store_explicit(&g_completed, 1, memory_order_release);
    return 1;
}
