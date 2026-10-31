// Primary-window Metal boot. One CALL rel32 in CApplication::CreateApplicationWindow.
//
// The Ghidra snapshot of the pinned GOG binary shows two SDL_CreateWindow calls.
// 0x13cc1d7 is the primary window: title from the caller, centered, stored on
// CPdxWindow, then used for the loading splash. Its five bytes are e8 14 ec ef ff,
// targeting SDL_CreateWindow at RVA 0x12cadf0. The next call is SDL_GL_SetSwapInterval
// at 0x13cc1e4.
// 0x15ed55a is _GfxGetGraphicsSettings creating "PdxDetectWindow" at 128x128, then
// SDL_GL_CreateContext, then destroying both. That call is not patched.
// Earlier OpenGL activity is outside this result.
#import "eu4_metal_boot_patch.h"

#import <Metal/Metal.h>
#import <QuartzCore/CAMetalLayer.h>
#import <dlfcn.h>
#import <mach-o/dyld.h>
#import <pthread.h>
#import <stdio.h>
#import <string.h>
#import <unistd.h>

enum {
    kCallRva = 0x13cc1d7,
    kCreateRva = 0x12cadf0,
    kOpenGL = 0x00000002,
    kMetal = 0x20000000,
    kReadWidth = 4,
    kReadHeight = 4,
    kReadBytes = kReadWidth * kReadHeight * 4,
    kExitFail = 2
};

static const uint8_t kExpectedCall[5] = {0xe8, 0x14, 0xec, 0xef, 0xff};
static const uint8_t kClearBGRA[4] = {0, 0, 255, 255};
static const char kBasis[] =
    "CApplication::CreateApplicationWindow stores this SDL_Window* on CPdxWindow and "
    "draws the loading splash with it. Call RVA 0x13cc1d7 targets SDL_CreateWindow. "
    "The PdxDetectWindow call at 0x15ed55a is a temporary settings probe and is not patched. "
    "Earlier OpenGL activity is outside this result. SDL_GL_SetSwapInterval at 0x13cc1e4 "
    "does not run because this wrapper does not return.";

typedef uint32_t (*GetFlagsFn)(void *);
typedef void *(*CreateViewFn)(void *);
typedef void *(*GetLayerFn)(void *);
typedef void *(*CreateWindowFn)(const char *, int, int, int, int, uint32_t);

static GetFlagsFn g_get_flags;
static CreateViewFn g_create_view;
static GetLayerFn g_get_layer;
static CreateWindowFn g_original;
static char g_receipt[1024];

__attribute__((noreturn)) static void finish(const char *result, const char *cause, uint32_t incoming,
                                                  uint32_t passed,
                   int have_window_flags, uint32_t window_flags, int view_ok, int layer_ok,
                   int width, int height, const char *command_status, int readback_matches) {
    char body[4096];
    int length = snprintf(
        body, sizeof body,
        "{\n"
        "  \"result\": \"%s\",\n"
        "  \"callsite_rva\": \"0x13cc1d7\",\n"
        "  \"preflight_basis\": \"%s\",\n"
        "  \"matched_bytes\": \"e814ecefff\",\n"
        "  \"successor\": \"SDL_GL_SetSwapInterval\",\n"
        "  \"successor_rva\": \"0x13cc1e4\",\n"
        "  \"earlier_gl\": \"not_in_evidence\",\n"
        "  \"incoming_flags\": %u,\n"
        "  \"flags_passed\": %u,\n"
        "  \"window_flags\": ",
        result, kBasis, incoming, passed);
    if (length < 0 || (size_t)length >= sizeof body) {
        _exit(kExitFail);
    }
    FILE *out = fopen(g_receipt, "w");
    if (out == NULL) {
        _exit(kExitFail);
    }
    int failed = fputs(body, out) == EOF;
    if (!failed) {
        failed = have_window_flags ? fprintf(out, "%u", window_flags) < 0 : fputs("null", out) == EOF;
    }
    if (!failed) {
        failed = fprintf(out,
                         ",\n"
                         "  \"view_ok\": %s,\n"
                         "  \"layer_ok\": %s,\n"
                         "  \"drawable_width\": %d,\n"
                         "  \"drawable_height\": %d,\n"
                         "  \"command_status\": %s%s%s,\n"
                         "  \"readback_matches\": %s,\n"
                         "  \"cause\": %s%s%s\n"
                         "}\n",
                         view_ok ? "true" : "false", layer_ok ? "true" : "false", width, height,
                         command_status ? "\"" : "null", command_status ? command_status : "",
                         command_status ? "\"" : "",
                         readback_matches < 0 ? "null" : readback_matches ? "true" : "false",
                         cause ? "\"" : "null", cause ? cause : "", cause ? "\"" : "") < 0;
    }
    if (!failed) {
        failed = fflush(out) != 0;
    }
    if (!failed) {
        int fd = fileno(out);
        failed = fd < 0 || fsync(fd) != 0;
    }
    if (fclose(out) != 0) {
        failed = 1;
    }
    int verified = !failed && strcmp(result, "metal_window_boot_verified") == 0;
    _exit(verified ? 0 : kExitFail);
}

static const struct mach_header_64 *eu4_header(void) {
    uint32_t count = _dyld_image_count();
    for (uint32_t i = 0; i < count; i++) {
        const char *name = _dyld_get_image_name(i);
        if (name == NULL) {
            continue;
        }
        size_t length = strlen(name);
        if (length >= 4 && strcmp(name + length - 4, "/eu4") == 0) {
            return (const struct mach_header_64 *)_dyld_get_image_header(i);
        }
    }
    return NULL;
}

static int pixels_match(const uint8_t *bytes) {
    for (int i = 0; i < kReadBytes; i += 4) {
        if (memcmp(bytes + i, kClearBGRA, 4) != 0) {
            return 0;
        }
    }
    return 1;
}

static void present(void *window, uint32_t incoming, uint32_t passed, uint32_t window_flags) {
    if (g_create_view == NULL || g_get_layer == NULL) {
        finish("metal_window_boot_failed", "sdl_helpers_missing", incoming, passed, 1,
               window_flags, 0, 0, 0, 0, NULL, -1);
    }
    void *view = g_create_view(window);
    if (view == NULL) {
        finish("metal_window_boot_failed", "sdl_metal_create_view_failed", incoming, passed, 1,
               window_flags, 0, 0, 0, 0, NULL, -1);
    }
    CAMetalLayer *layer = (__bridge CAMetalLayer *)g_get_layer(view);
    if (layer == nil) {
        finish("metal_window_boot_failed", "sdl_metal_get_layer_failed", incoming, passed, 1,
               window_flags, 1, 0, 0, 0, NULL, -1);
    }
    id<MTLDevice> device = MTLCreateSystemDefaultDevice();
    if (device == nil) {
        finish("metal_window_boot_failed", "no_metal_device", incoming, passed, 1, window_flags,
               1, 1, 0, 0, NULL, -1);
    }
    id<MTLCommandQueue> queue = [device newCommandQueue];
    layer.device = device;
    layer.pixelFormat = MTLPixelFormatBGRA8Unorm;
    layer.framebufferOnly = NO;
    id<CAMetalDrawable> drawable = [layer nextDrawable];
    if (drawable == nil) {
        finish("metal_window_boot_failed", "drawable_unavailable_at_window_creation", incoming,
               passed, 1, window_flags, 1, 1, 0, 0, NULL, -1);
    }
    NSUInteger width = drawable.texture.width;
    NSUInteger height = drawable.texture.height;
    if (width < kReadWidth || height < kReadHeight) {
        finish("metal_window_boot_failed", "drawable_smaller_than_readback", incoming, passed, 1,
               window_flags, 1, 1, (int)width, (int)height, NULL, -1);
    }
    id<MTLBuffer> readback =
        [device newBufferWithLength:kReadBytes options:MTLResourceStorageModeShared];
    if (queue == nil || readback == nil) {
        finish("metal_window_boot_failed", "command_queue_or_readback_failed", incoming, passed, 1,
               window_flags, 1, 1, (int)width, (int)height, NULL, -1);
    }
    id<MTLCommandBuffer> buffer = [queue commandBuffer];
    MTLRenderPassDescriptor *pass = [MTLRenderPassDescriptor renderPassDescriptor];
    pass.colorAttachments[0].texture = drawable.texture;
    pass.colorAttachments[0].loadAction = MTLLoadActionClear;
    pass.colorAttachments[0].storeAction = MTLStoreActionStore;
    pass.colorAttachments[0].clearColor = MTLClearColorMake(1, 0, 0, 1);
    id<MTLRenderCommandEncoder> encoder = [buffer renderCommandEncoderWithDescriptor:pass];
    [encoder endEncoding];
    id<MTLBlitCommandEncoder> blit = [buffer blitCommandEncoder];
    [blit copyFromTexture:drawable.texture
              sourceSlice:0
              sourceLevel:0
             sourceOrigin:MTLOriginMake(0, 0, 0)
               sourceSize:MTLSizeMake(kReadWidth, kReadHeight, 1)
                 toBuffer:readback
        destinationOffset:0
   destinationBytesPerRow:kReadWidth * 4
 destinationBytesPerImage:kReadBytes];
    [blit endEncoding];
    [buffer presentDrawable:drawable];
    [buffer commit];
    [buffer waitUntilCompleted];
    const char *status = buffer.status == MTLCommandBufferStatusCompleted ? "completed" : "failed";
    int matches = buffer.status == MTLCommandBufferStatusCompleted && pixels_match(readback.contents);
    finish(matches ? "metal_window_boot_verified" : "metal_window_boot_failed",
           matches ? NULL : "clear_readback_mismatch", incoming, passed, 1, window_flags, 1, 1,
           (int)width, (int)height, status, matches);
}

static void boot_window(void *window, uint32_t incoming, uint32_t passed) {
    uint32_t reported = g_get_flags(window);
    if ((reported & kMetal) == 0 || (reported & kOpenGL) != 0) {
        finish("metal_window_boot_failed", "window_flags_are_not_metal", incoming, passed, 1,
               reported, 0, 0, 0, 0, NULL, -1);
    }
    present(window, incoming, passed, reported);
}

void *eu4_metal_boot_on_create(const char *title, int x, int y, int w, int h, uint32_t flags) {
    uint32_t incoming = flags;
    if ((incoming & kOpenGL) == 0) {
        finish("metal_window_boot_failed", "incoming_flags_lack_opengl", incoming, incoming, 0, 0,
               0, 0, 0, 0, NULL, -1);
    }
    uint32_t passed = (incoming & ~(uint32_t)kOpenGL) | (uint32_t)kMetal;
    void *window = g_original(title, x, y, w, h, passed);
    if (window == NULL) {
        finish("metal_window_boot_failed", "sdl_create_window_returned_null", incoming, passed, 0,
               0, 0, 0, 0, 0, NULL, -1);
    }
    if (pthread_main_np() == 0) {
        finish("metal_window_boot_failed", "not_main_thread", incoming, passed, 0, 0, 0, 0, 0, 0,
               NULL, -1);
    }
    boot_window(window, incoming, passed);
    finish("metal_window_boot_failed", "wrapper_returned", incoming, passed, 0, 0, 0, 0, 0, 0,
           NULL, -1);
}

static int resolve_helpers(void) {
    g_get_flags = (GetFlagsFn)dlsym(RTLD_DEFAULT, "SDL_GetWindowFlags");
    g_create_view = (CreateViewFn)dlsym(RTLD_DEFAULT, "SDL_Metal_CreateView");
    g_get_layer = (GetLayerFn)dlsym(RTLD_DEFAULT, "SDL_Metal_GetLayer");
    return g_get_flags != NULL && g_create_view != NULL && g_get_layer != NULL;
}

__attribute__((constructor)) static void eu4_metal_boot_init(void) {
    const char *path = getenv("EU4_METAL_BOOT_RECEIPT");
    if (path == NULL || path[0] != '/') {
        return;
    }
    snprintf(g_receipt, sizeof g_receipt, "%s", path);
    if (!resolve_helpers()) {
        finish("metal_window_boot_failed", "sdl_helper_unresolved", 0, 0, 0, 0, 0, 0, 0, 0, NULL,
               -1);
    }
    const struct mach_header_64 *header = eu4_header();
    if (header == NULL) {
        finish("metal_window_boot_failed", "eu4_image_not_found", 0, 0, 0, 0, 0, 0, 0, 0, NULL,
               -1);
    }
    void *site = (uint8_t *)header + kCallRva;
    void *expected = (uint8_t *)header + kCreateRva;
    eu4_metal_boot_patch patch = {0};
    if (!eu4_metal_boot_install(site, kExpectedCall, expected, eu4_metal_boot_on_create, &patch)) {
        finish("metal_window_boot_failed", "callsite_rejected", 0, 0, 0, 0, 0, 0, 0, 0, NULL, -1);
    }
    g_original = (CreateWindowFn)patch.original;
}
