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

#import <AppKit/AppKit.h>
#import <Metal/Metal.h>
#import <QuartzCore/CAMetalLayer.h>
#import <dlfcn.h>
#import <mach-o/dyld.h>
#import <pthread.h>
#import <stddef.h>
#import <stdint.h>
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
    "Earlier OpenGL activity is outside this result. SDL_WINDOW_METAL is not requested. "
    "A CAMetalLayer is attached to the Cocoa content view. presentDrawable is not an on-screen inspection. "
    "SDL_GL_SetSwapInterval at 0x13cc1e4 does not run because this wrapper does not return.";

/* SDL 2.0.14 SDL_SysWMinfo on x86-64. The Cocoa NSWindow* is the first union field. */
typedef struct {
    uint8_t version[3];
    uint8_t pad0;
    int32_t subsystem;
    uint8_t info[64];
} eu4_sdl_syswm;

_Static_assert(sizeof(eu4_sdl_syswm) == 72, "SDL 2.0.14 SysWMinfo is 72 bytes");
_Static_assert(offsetof(eu4_sdl_syswm, subsystem) == 4, "subsystem is at byte 4");
_Static_assert(offsetof(eu4_sdl_syswm, info) == 8, "info union is at byte 8");

typedef uint32_t (*GetFlagsFn)(void *);
typedef void *(*CreateWindowFn)(const char *, int, int, int, int, uint32_t);
typedef const char *(*GetErrorFn)(void);
typedef void (*ClearErrorFn)(void);
typedef void (*GetVersionFn)(uint8_t version[3]);
typedef int (*GetWMInfoFn)(void *, eu4_sdl_syswm *);

static GetFlagsFn g_get_flags;
static CreateWindowFn g_original;
static GetErrorFn g_get_error;
static ClearErrorFn g_clear_error;
static GetVersionFn g_get_version;
static GetWMInfoFn g_get_wm_info;
static char g_receipt[1024];
static char g_sdl_error[256];
static int g_have_sdl_error;
static int g_record_window_size;
static int g_window_w;
static int g_window_h;
static int g_have_subsystem;
static int32_t g_subsystem;
static int g_have_view_points;
static double g_view_points_w;
static double g_view_points_h;

static void copy_sdl_error(void);

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
        failed = g_record_window_size
                     ? fprintf(out, ",\n  \"window_width\": %d,\n  \"window_height\": %d,\n", g_window_w,
                               g_window_h) < 0
                     : fputs(",\n  \"window_width\": null,\n  \"window_height\": null,\n", out) == EOF;
    }
    if (!failed) {
        failed = g_have_sdl_error ? fprintf(out, "  \"sdl_error\": \"%s\",\n", g_sdl_error) < 0
                                  : fputs("  \"sdl_error\": null,\n", out) == EOF;
    }
    if (!failed) {
        failed = g_have_subsystem ? fprintf(out, "  \"wm_subsystem\": %d,\n", g_subsystem) < 0
                                  : fputs("  \"wm_subsystem\": null,\n", out) == EOF;
    }
    if (!failed) {
        failed = g_have_view_points
                     ? fprintf(out, "  \"view_width_points\": %.2f,\n  \"view_height_points\": %.2f,\n",
                               g_view_points_w, g_view_points_h) < 0
                     : fputs("  \"view_width_points\": null,\n  \"view_height_points\": null,\n", out) == EOF;
    }
    if (!failed) {
        failed = fprintf(out,
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

static void present(CAMetalLayer *layer, uint32_t incoming, uint32_t passed, uint32_t window_flags) {
    id<MTLDevice> device = layer.device;
    if (device == nil) {
        finish("metal_window_boot_failed", "no_metal_device", incoming, passed, 1, window_flags, 1,
               1, 0, 0, NULL, -1);
    }
    id<MTLCommandQueue> queue = [device newCommandQueue];
    id<CAMetalDrawable> drawable = [layer nextDrawable];
    if (drawable == nil) {
        finish("metal_window_boot_failed", "drawable_unavailable_at_window_creation", incoming,
               passed, 1, window_flags, 1, 1, (int)layer.drawableSize.width,
               (int)layer.drawableSize.height, NULL, -1);
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
    if (buffer == nil) {
        finish("metal_window_boot_failed", "command_encoding_failed", incoming, passed, 1,
               window_flags, 1, 1, (int)width, (int)height, NULL, -1);
    }
    MTLRenderPassDescriptor *pass = [MTLRenderPassDescriptor renderPassDescriptor];
    pass.colorAttachments[0].texture = drawable.texture;
    pass.colorAttachments[0].loadAction = MTLLoadActionClear;
    pass.colorAttachments[0].storeAction = MTLStoreActionStore;
    pass.colorAttachments[0].clearColor = MTLClearColorMake(1, 0, 0, 1);
    id<MTLRenderCommandEncoder> encoder = [buffer renderCommandEncoderWithDescriptor:pass];
    if (encoder == nil) {
        finish("metal_window_boot_failed", "command_encoding_failed", incoming, passed, 1,
               window_flags, 1, 1, (int)width, (int)height, NULL, -1);
    }
    [encoder endEncoding];
    id<MTLBlitCommandEncoder> blit = [buffer blitCommandEncoder];
    if (blit == nil) {
        finish("metal_window_boot_failed", "command_encoding_failed", incoming, passed, 1,
               window_flags, 1, 1, (int)width, (int)height, NULL, -1);
    }
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

static void record_view_points(NSView *view) {
    g_have_view_points = 1;
    g_view_points_w = view.bounds.size.width;
    g_view_points_h = view.bounds.size.height;
}

static void boot_window(void *window, uint32_t incoming, uint32_t passed) {
    uint32_t reported = g_get_flags(window);
    if ((reported & kOpenGL) != 0) {
        copy_sdl_error();
        finish("metal_window_boot_failed", "window_flags_still_opengl", incoming, passed, 1,
               reported, 0, 0, 0, 0, NULL, -1);
    }
    if ((reported & kMetal) != 0) {
        copy_sdl_error();
        finish("metal_window_boot_failed", "window_flags_include_metal", incoming, passed, 1,
               reported, 0, 0, 0, 0, NULL, -1);
    }
    eu4_sdl_syswm info;
    memset(&info, 0, sizeof info);
    g_get_version(info.version);
    int wm_ok = g_get_wm_info(window, &info);
    g_have_subsystem = 1;
    g_subsystem = info.subsystem;
    void *raw = NULL;
    memcpy(&raw, info.info, sizeof raw);
    if (!wm_ok || info.subsystem != 4 || raw == NULL) {
        copy_sdl_error();
        finish("metal_window_boot_failed", "cocoa_window_unavailable", incoming, passed, 1, reported,
               0, 0, 0, 0, NULL, -1);
    }
    NSWindow *native = (__bridge NSWindow *)raw;
    NSView *content = native.contentView;
    if (content == nil) {
        finish("metal_window_boot_failed", "native_view_unavailable", incoming, passed, 1, reported,
               0, 0, 0, 0, NULL, -1);
    }
    if (NSIsEmptyRect(content.bounds)) {
        record_view_points(content);
        finish("metal_window_boot_failed", "content_bounds_empty", incoming, passed, 1, reported, 0,
               0, 0, 0, NULL, -1);
    }
    id<MTLDevice> device = MTLCreateSystemDefaultDevice();
    if (device == nil) {
        finish("metal_window_boot_failed", "no_metal_device", incoming, passed, 1, reported, 0, 0,
               0, 0, NULL, -1);
    }
    NSView *child = [[NSView alloc] initWithFrame:content.bounds];
    CAMetalLayer *layer = [CAMetalLayer layer];
    layer.device = device;
    layer.pixelFormat = MTLPixelFormatBGRA8Unorm;
    layer.framebufferOnly = NO;
    child.layer = layer;
    child.wantsLayer = YES;
    [content addSubview:child];
    if (child.layer != layer) {
        finish("metal_window_boot_failed", "metal_layer_attach_failed", incoming, passed, 1,
               reported, 1, 0, 0, 0, NULL, -1);
    }
    record_view_points(child);
    NSRect backing = [child convertRectToBacking:child.bounds];
    layer.drawableSize = backing.size;
    present(layer, incoming, passed, reported);
}

void *eu4_metal_boot_on_create(const char *title, int x, int y, int w, int h, uint32_t flags) {
    uint32_t incoming = flags;
    if ((incoming & kOpenGL) == 0) {
        finish("metal_window_boot_failed", "incoming_flags_lack_opengl", incoming, incoming, 0, 0,
               0, 0, 0, 0, NULL, -1);
    }
    uint32_t passed = incoming & ~(uint32_t)kOpenGL;
    if (g_clear_error != NULL) {
        g_clear_error();
    }
    g_window_w = w;
    g_window_h = h;
    g_record_window_size = 1;
    void *window = g_original(title, x, y, w, h, passed);
    if (window == NULL) {
        copy_sdl_error();
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

/* SDL_GetError points at a buffer the next SDL call may replace. */
static void copy_sdl_error(void) {
    const char *text = g_get_error();
    size_t out = 0;
    g_sdl_error[0] = 0;
    g_have_sdl_error = 1;
    if (text == NULL) {
        return;
    }
    for (size_t i = 0; text[i] != 0; i++) {
        unsigned char c = (unsigned char)text[i];
        if (c == '"' || c == '\\') {
            if (out + 2 >= sizeof g_sdl_error) {
                break;
            }
            g_sdl_error[out++] = '\\';
            g_sdl_error[out++] = (char)c;
        } else if (c < 0x20) {
            if (out + 6 >= sizeof g_sdl_error) {
                break;
            }
            if (snprintf(g_sdl_error + out, sizeof g_sdl_error - out, "\\u%04x", c) != 6) {
                break;
            }
            out += 6;
        } else if (out + 1 >= sizeof g_sdl_error) {
            break;
        } else {
            g_sdl_error[out++] = (char)c;
        }
    }
    g_sdl_error[out] = 0;
}

static int resolve_helpers(void) {
    g_get_flags = (GetFlagsFn)dlsym(RTLD_DEFAULT, "SDL_GetWindowFlags");
    g_get_version = (GetVersionFn)dlsym(RTLD_DEFAULT, "SDL_GetVersion");
    g_get_wm_info = (GetWMInfoFn)dlsym(RTLD_DEFAULT, "SDL_GetWindowWMInfo");
    g_get_error = (GetErrorFn)dlsym(RTLD_DEFAULT, "SDL_GetError");
    g_clear_error = (ClearErrorFn)dlsym(RTLD_DEFAULT, "SDL_ClearError");
    return g_get_flags != NULL && g_get_version != NULL && g_get_wm_info != NULL && g_get_error != NULL;
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
