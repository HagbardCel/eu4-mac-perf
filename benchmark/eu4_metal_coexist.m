// Offscreen Metal clear inside a process that is also running OpenGL.
// Loaded only when EU4_METAL_COEXIST_RECEIPT is an absolute path.
// Does not patch EU4, does not replace the GL view, and does not call SDL_GL_SwapWindow.
#import <Metal/Metal.h>
#import <OpenGL/OpenGL.h>
#import <stdatomic.h>
#import <stdio.h>
#import <string.h>

enum { kWidth = 4, kHeight = 4, kBytes = kWidth * kHeight * 4 };

static const uint8_t kClearBGRA[4] = {0, 0, 255, 255}; // RGBA clear (1, 0, 0, 1)
static char g_receipt[1024];
static atomic_int g_flushes;

// Counts presents that completed. A failed flush is not a present.
static CGLError eu4_metal_coexist_flush(CGLContextObj context) {
    CGLError result = CGLFlushDrawable(context);
    if (result != kCGLNoError) {
        return result;
    }
    int count = atomic_fetch_add(&g_flushes, 1) + 1;
    if (count == 1 && g_receipt[0] == '/') {
        char side[1100];
        snprintf(side, sizeof side, "%s.flushes", g_receipt);
        FILE *out = fopen(side, "w");
        if (out != NULL) {
            fprintf(out, "%d\n", count);
            fclose(out);
        }
    }
    return result;
}

__attribute__((used, section("__DATA,__interpose")))
static const struct {
    const void *replacement;
    const void *replacee;
} eu4_metal_coexist_flush_interpose = {
    (const void *)eu4_metal_coexist_flush,
    (const void *)CGLFlushDrawable,
};

static int pixels_match(const uint8_t *bytes) {
    for (int i = 0; i < kBytes; i += 4) {
        if (memcmp(bytes + i, kClearBGRA, 4) != 0) {
            return 0;
        }
    }
    return 1;
}

static void write_receipt(const char *path, const char *gpu, int matches, const char *cause) {
    char temporary[1024];
    int length = snprintf(temporary, sizeof temporary, "%s.tmp", path);
    if (length < 0 || (size_t)length >= sizeof temporary) {
        return;
    }
    FILE *out = fopen(temporary, "w");
    if (out == NULL) {
        return;
    }
    fprintf(out,
            "{\n"
            "  \"gpu_coexistence\": \"%s\",\n"
            "  \"readback_matches\": %s,\n"
            "  \"metal_command_completed\": %s,\n"
            "  \"width\": %d,\n"
            "  \"height\": %d,\n"
            "  \"format\": \"BGRA8Unorm\",\n"
            "  \"clear_rgba\": [1, 0, 0, 1],\n"
            "  \"cause\": %s%s%s,\n"
            "  \"window_drawable\": \"window_drawable_not_tested\"\n"
            "}\n",
            gpu, matches ? "true" : "false", matches ? "true" : "false", kWidth, kHeight,
            cause ? "\"" : "null", cause ? cause : "", cause ? "\"" : "");
    fclose(out);
    rename(temporary, path);
}

static int encode_clear(id<MTLDevice> device, id<MTLTexture> texture, id<MTLBuffer> readback) {
    id<MTLCommandQueue> queue = [device newCommandQueue];
    if (queue == nil) {
        return 0;
    }
    id<MTLCommandBuffer> buffer = [queue commandBuffer];
    MTLRenderPassDescriptor *pass = [MTLRenderPassDescriptor renderPassDescriptor];
    pass.colorAttachments[0].texture = texture;
    pass.colorAttachments[0].loadAction = MTLLoadActionClear;
    pass.colorAttachments[0].storeAction = MTLStoreActionStore;
    pass.colorAttachments[0].clearColor = MTLClearColorMake(1, 0, 0, 1);
    id<MTLRenderCommandEncoder> encoder = [buffer renderCommandEncoderWithDescriptor:pass];
    [encoder endEncoding];
    id<MTLBlitCommandEncoder> blit = [buffer blitCommandEncoder];
    [blit copyFromTexture:texture
              sourceSlice:0
              sourceLevel:0
             sourceOrigin:MTLOriginMake(0, 0, 0)
               sourceSize:MTLSizeMake(kWidth, kHeight, 1)
                 toBuffer:readback
        destinationOffset:0
   destinationBytesPerRow:kWidth * 4
 destinationBytesPerImage:kBytes];
    [blit endEncoding];
    [buffer commit];
    [buffer waitUntilCompleted];
    return buffer.status == MTLCommandBufferStatusCompleted && pixels_match(readback.contents);
}

static id<MTLTexture> offscreen_texture(id<MTLDevice> device) {
    MTLTextureDescriptor *desc =
        [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm
                                                           width:kWidth
                                                          height:kHeight
                                                       mipmapped:NO];
    desc.usage = MTLTextureUsageRenderTarget;
    desc.storageMode = MTLStorageModePrivate;
    return [device newTextureWithDescriptor:desc];
}

void eu4_metal_coexist_run(const char *path) {
    snprintf(g_receipt, sizeof g_receipt, "%s", path);
    @autoreleasepool {
        id<MTLDevice> device = MTLCreateSystemDefaultDevice();
        if (device == nil) {
            write_receipt(path, "offscreen_coexistence_failed", 0, "no metal device");
            return;
        }
        id<MTLTexture> texture = offscreen_texture(device);
        id<MTLBuffer> readback =
            [device newBufferWithLength:kBytes options:MTLResourceStorageModeShared];
        if (texture == nil || readback == nil) {
            write_receipt(path, "offscreen_coexistence_failed", 0, "texture or readback allocation failed");
            return;
        }
        if (!encode_clear(device, texture, readback)) {
            write_receipt(path, "offscreen_coexistence_failed", 0, "readback did not match the clear");
            return;
        }
        write_receipt(path, "offscreen_coexistence_verified", 1, NULL);
    }
}

__attribute__((constructor)) static void eu4_metal_coexist_init(void) {
    const char *path = getenv("EU4_METAL_COEXIST_RECEIPT");
    if (path == NULL || path[0] != '/') {
        return;
    }
    eu4_metal_coexist_run(path);
}
