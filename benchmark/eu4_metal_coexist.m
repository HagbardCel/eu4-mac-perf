// Offscreen Metal clear inside a process that is also running OpenGL.
// Loaded only when EU4_METAL_COEXIST_RECEIPT is an absolute path.
// Does not patch EU4, does not replace the GL view, and does not call SDL_GL_SwapWindow.
#import <AppKit/AppKit.h>
#import <Metal/Metal.h>
#import <OpenGL/OpenGL.h>
#import <QuartzCore/CAMetalLayer.h>
#import <dlfcn.h>
#import <pthread.h>
#import <stdatomic.h>
#import <stdio.h>
#import <string.h>
#import <unistd.h>

enum { kWidth = 4, kHeight = 4, kBytes = kWidth * kHeight * 4 };

typedef void *SDL_Window;
typedef void *SDL_MetalView;
typedef SDL_Window *(*FocusFn)(void);
typedef SDL_MetalView (*CreateViewFn)(SDL_Window *);
typedef void (*DestroyViewFn)(SDL_MetalView);
typedef void *(*GetLayerFn)(SDL_MetalView);

static const uint8_t kClearBGRA[4] = {0, 0, 255, 255}; // RGBA clear (1, 0, 0, 1)
static char g_receipt[1024];
static atomic_int g_flushes;

// Counts real presents. SDL calls this through OpenGL.framework; the GL view is unchanged.
static CGLError eu4_metal_coexist_flush(CGLContextObj context) {
    CGLError result = CGLFlushDrawable(context);
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

static void write_receipt(const char *path, const char *gpu, int matches,
                          const char *cause, const char *create_view,
                          const char *get_layer, const char *window_drawable,
                          const char *optional_view, const char *optional_cause) {
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
            "  \"width\": %d,\n"
            "  \"height\": %d,\n"
            "  \"format\": \"BGRA8Unorm\",\n"
            "  \"clear_rgba\": [1, 0, 0, 1],\n"
            "  \"cause\": %s%s%s,\n"
            "  \"sdl_metal_create_view\": \"%s\",\n"
            "  \"sdl_metal_get_layer\": \"%s\",\n"
            "  \"window_drawable\": \"%s\",\n"
            "  \"optional_view\": \"%s\",\n"
            "  \"optional_view_cause\": %s%s%s\n"
            "}\n",
            gpu, matches ? "true" : "false", kWidth, kHeight,
            cause ? "\"" : "null", cause ? cause : "", cause ? "\"" : "",
            create_view, get_layer, window_drawable, optional_view,
            optional_cause ? "\"" : "null", optional_cause ? optional_cause : "",
            optional_cause ? "\"" : "");
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

// Optional, after the offscreen readback. A missing symbol or a failed view
// leaves the GL window alone and does not change the offscreen result.
static void try_diagnostic_view(id<MTLDevice> device, const char *path, const char *create_name,
                                const char *layer_name) {
    FocusFn focus = (FocusFn)dlsym(RTLD_DEFAULT, "SDL_GetKeyboardFocus");
    CreateViewFn create = (CreateViewFn)dlsym(RTLD_DEFAULT, "SDL_Metal_CreateView");
    DestroyViewFn destroy = (DestroyViewFn)dlsym(RTLD_DEFAULT, "SDL_Metal_DestroyView");
    GetLayerFn get_layer = (GetLayerFn)dlsym(RTLD_DEFAULT, "SDL_Metal_GetLayer");
    if (focus == NULL || create == NULL || destroy == NULL || get_layer == NULL) {
        write_receipt(path, "offscreen_coexistence_verified", 1, NULL, create_name, layer_name,
                      "window_drawable_not_tested", "skipped", "sdl metal exports not visible");
        return;
    }
    __block SDL_Window *window = NULL;
    for (int attempt = 0; attempt < 40 && window == NULL; attempt++) {
        dispatch_semaphore_t gate = dispatch_semaphore_create(0);
        dispatch_async(dispatch_get_main_queue(), ^{
            window = focus();
            dispatch_semaphore_signal(gate);
        });
        if (dispatch_semaphore_wait(gate, dispatch_time(DISPATCH_TIME_NOW, 2 * NSEC_PER_SEC)) != 0) {
            usleep(200000);
            continue;
        }
        if (window == NULL) {
            usleep(250000);
        }
    }
    if (window == NULL) {
        write_receipt(path, "offscreen_coexistence_verified", 1, NULL, "resolved", "resolved",
                      "window_drawable_not_tested", "skipped", "no sdl window");
        return;
    }
    __block const char *view_cause = "view was not created";
    __block int tested = 0;
    dispatch_semaphore_t done = dispatch_semaphore_create(0);
    dispatch_async(dispatch_get_main_queue(), ^{
        @autoreleasepool {
            SDL_MetalView metal_view = create(window);
            if (metal_view == NULL) {
                view_cause = "SDL_Metal_CreateView returned null";
                dispatch_semaphore_signal(done);
                return;
            }
            NSView *ns_view = (__bridge NSView *)metal_view;
            ns_view.frame = NSMakeRect(8, 8, 32, 32);
            CAMetalLayer *layer = (__bridge CAMetalLayer *)get_layer(metal_view);
            if (layer == nil) {
                destroy(metal_view);
                view_cause = "SDL_Metal_GetLayer returned null";
                dispatch_semaphore_signal(done);
                return;
            }
            layer.device = device;
            layer.pixelFormat = MTLPixelFormatBGRA8Unorm;
            layer.drawableSize = CGSizeMake(32, 32);
            layer.framebufferOnly = NO;
            id<CAMetalDrawable> drawable = [layer nextDrawable];
            id<MTLCommandQueue> queue = [device newCommandQueue];
            if (drawable == nil || queue == nil) {
                destroy(metal_view);
                view_cause = "metal drawable was not produced";
                dispatch_semaphore_signal(done);
                return;
            }
            id<MTLCommandBuffer> buffer = [queue commandBuffer];
            MTLRenderPassDescriptor *pass = [MTLRenderPassDescriptor renderPassDescriptor];
            pass.colorAttachments[0].texture = drawable.texture;
            pass.colorAttachments[0].loadAction = MTLLoadActionClear;
            pass.colorAttachments[0].storeAction = MTLStoreActionStore;
            pass.colorAttachments[0].clearColor = MTLClearColorMake(0, 1, 0, 1);
            id<MTLRenderCommandEncoder> encoder = [buffer renderCommandEncoderWithDescriptor:pass];
            [encoder endEncoding];
            [buffer presentDrawable:drawable];
            [buffer commit];
            [buffer waitUntilCompleted];
            destroy(metal_view);
            tested = buffer.status == MTLCommandBufferStatusCompleted;
            view_cause = tested ? NULL : "diagnostic view command did not complete";
            dispatch_semaphore_signal(done);
        }
    });
    if (dispatch_semaphore_wait(done, dispatch_time(DISPATCH_TIME_NOW, 5 * NSEC_PER_SEC)) != 0) {
        write_receipt(path, "offscreen_coexistence_verified", 1, NULL, "resolved", "resolved",
                      "window_drawable_not_tested", "failed", "view attempt timed out");
        return;
    }
    write_receipt(path, "offscreen_coexistence_verified", 1, NULL, "resolved", "resolved",
                  tested ? "window_drawable_tested" : "window_drawable_not_tested",
                  tested ? "tested" : "failed", view_cause);
}

struct ViewJob {
    id<MTLDevice> device;
    char path[1024];
    char create_name[16];
    char layer_name[16];
};

static void *run_view_job(void *argument) {
    struct ViewJob *job = argument;
    @autoreleasepool {
        try_diagnostic_view(job->device, job->path, job->create_name, job->layer_name);
    }
    free(job);
    return NULL;
}

void eu4_metal_coexist_run(const char *path) {
    snprintf(g_receipt, sizeof g_receipt, "%s", path);
    @autoreleasepool {
        id<MTLDevice> device = MTLCreateSystemDefaultDevice();
        void *create = dlsym(RTLD_DEFAULT, "SDL_Metal_CreateView");
        void *layer = dlsym(RTLD_DEFAULT, "SDL_Metal_GetLayer");
        const char *create_name = create ? "resolved" : "absent";
        const char *layer_name = layer ? "resolved" : "absent";
        if (device == nil) {
            write_receipt(path, "offscreen_coexistence_failed", 0, "no metal device",
                          create_name, layer_name, "window_drawable_not_tested", "skipped",
                          NULL);
            return;
        }
        id<MTLTexture> texture = offscreen_texture(device);
        id<MTLBuffer> readback =
            [device newBufferWithLength:kBytes options:MTLResourceStorageModeShared];
        if (texture == nil || readback == nil) {
            write_receipt(path, "offscreen_coexistence_failed", 0, "texture or readback allocation failed",
                          create_name, layer_name, "window_drawable_not_tested", "skipped", NULL);
            return;
        }
        int matches = encode_clear(device, texture, readback);
        if (!matches) {
            write_receipt(path, "offscreen_coexistence_failed", 0, "readback did not match the clear",
                          create_name, layer_name, "window_drawable_not_tested", "skipped", NULL);
            return;
        }
        if (create == NULL || layer == NULL) {
            write_receipt(path, "offscreen_coexistence_verified", 1, NULL, create_name, layer_name,
                          "window_drawable_not_tested", "skipped", "sdl metal exports not visible");
            return;
        }
        write_receipt(path, "offscreen_coexistence_verified", 1, NULL, create_name, layer_name,
                      "window_drawable_not_tested", "pending", NULL);
        struct ViewJob *job = calloc(1, sizeof *job);
        if (job == NULL) {
            write_receipt(path, "offscreen_coexistence_verified", 1, NULL, create_name, layer_name,
                          "window_drawable_not_tested", "skipped", "out of memory");
            return;
        }
        job->device = device;
        snprintf(job->path, sizeof job->path, "%s", path);
        snprintf(job->create_name, sizeof job->create_name, "%s", create_name);
        snprintf(job->layer_name, sizeof job->layer_name, "%s", layer_name);
        pthread_t thread;
        if (pthread_create(&thread, NULL, run_view_job, job) != 0) {
            free(job);
            write_receipt(path, "offscreen_coexistence_verified", 1, NULL, create_name, layer_name,
                          "window_drawable_not_tested", "skipped", "could not start view thread");
            return;
        }
        pthread_detach(thread);
    }
}

__attribute__((constructor)) static void eu4_metal_coexist_init(void) {
    const char *path = getenv("EU4_METAL_COEXIST_RECEIPT");
    if (path == NULL || path[0] != '/') {
        return;
    }
    eu4_metal_coexist_run(path);
}
