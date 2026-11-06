#import "../benchmark/eu4_gfx_init.h"
#import "../benchmark/eu4_metal_events.h"

#import <Metal/Metal.h>
#import <QuartzCore/CAMetalLayer.h>
#import <stdio.h>
#import <stdlib.h>
#import <string.h>
#import <sys/mman.h>
#import <spawn.h>
#import <sys/wait.h>
#import <unistd.h>

extern char **environ;

static void fail(const char *message) {
    fprintf(stderr, "%s\n", message);
    exit(1);
}

static uint32_t load_u32(const uint8_t *desc, int offset) {
    uint32_t value;
    memcpy(&value, desc + offset, sizeof value);
    return value;
}

static void test_writes(void) {
    uint8_t desc[EU4_GFX_DEPTH_DESC_SIZE];
    memset(desc, 0xa5, sizeof desc);
    eu4_gfx_write_default_depth_stencil(desc);
    if (desc[0] != 1 || load_u32(desc, 4) != 1 || load_u32(desc, 8) != 3 || desc[0xc] != 0) {
        fail("depth enable, write, or compare bytes");
    }
    if (load_u32(desc, 0x10) != 0xffffffffu || load_u32(desc, 0x14) != 0xffffffffu) {
        fail("stencil masks");
    }
    if (load_u32(desc, 0x18) != 0 || load_u32(desc, 0x1c) != 0 || load_u32(desc, 0x20) != 0 ||
        load_u32(desc, 0x24) != 7) {
        fail("stencil op or compare bytes");
    }
    const int gaps[] = {1, 2, 3, 0xd, 0xe, 0xf};
    for (int i = 0; i < 6; i++) {
        if (desc[gaps[i]] != 0xa5) {
            fail("unwritten description byte was changed");
        }
    }
}

static void test_view(const uint8_t *desc) {
    eu4_gfx_depth_view view;
    memset(&view, 0, sizeof view);
    if (eu4_gfx_default_depth_stencil_view(desc, &view) != 0) {
        fail("default description was refused");
    }
    if (view.depth_compare != (int)MTLCompareFunctionLessEqual || view.depth_write != 1) {
        fail("depth descriptor properties");
    }
    if (view.stencil_compare != (int)MTLCompareFunctionAlways ||
        view.stencil_fail != (int)MTLStencilOperationKeep ||
        view.stencil_depth_fail != (int)MTLStencilOperationKeep ||
        view.stencil_pass != (int)MTLStencilOperationKeep ||
        view.stencil_read_mask != 0xffffffffu || view.stencil_write_mask != 0xffffffffu) {
        fail("stencil descriptor properties");
    }
    uint8_t other[EU4_GFX_DEPTH_DESC_SIZE];
    memcpy(other, desc, sizeof other);
    other[0] = 0;
    if (eu4_gfx_default_depth_stencil_view(other, &view) == 0) {
        fail("disabled depth was accepted");
    }
    if (eu4_gfx_default_depth_stencil_view(NULL, &view) == 0) {
        fail("null description was accepted");
    }
}

static void test_state(const uint8_t *desc) {
    id<MTLDevice> device = MTLCreateSystemDefaultDevice();
    if (device == nil) {
        fail("no Metal device");
    }
    void *raw = eu4_gfx_default_depth_stencil((__bridge void *)device, desc);
    if (raw == NULL) {
        fail("default state was not created");
    }
    @autoreleasepool {
        id<MTLDepthStencilState> state = (__bridge_transfer id<MTLDepthStencilState>)raw;
        if (state.device != device) {
            fail("state device is not the borrowed device");
        }
    }
    uint8_t rejected[EU4_GFX_DEPTH_DESC_SIZE];
    memcpy(rejected, desc, sizeof rejected);
    rejected[0] = 0;
    if (eu4_gfx_default_depth_stencil((__bridge void *)device, rejected) != NULL) {
        fail("rejected description created a state");
    }
    if (eu4_gfx_default_depth_stencil(NULL, desc) != NULL) {
        fail("null device created a state");
    }
    if (eu4_gfx_default_depth_stencil((__bridge void *)device, NULL) != NULL) {
        fail("null description created a state");
    }
    id<MTLBuffer> buffer = [device newBufferWithLength:4 options:MTLResourceStorageModeShared];
    if (buffer == nil) {
        fail("device was not usable after the state was released");
    }
}

static int g_stopped;

static void on_stop(const char *reason) {
    (void)reason;
    g_stopped = 1;
}

static void *fake_window(uint32_t window_id) { return window_id == 7 ? (void *)0x11 : NULL; }

static void fake_size(void *window, int *width, int *height) {
    (void)window;
    *width = 800;
    *height = 600;
}

static void write_depth(void *dest) { eu4_gfx_write_default_depth_stencil((uint8_t *)dest); }

static void bad_blend(void *dest) { memset(dest, 0xff, 0x24); }

static uint32_t load_at(const void *base, int offset) {
    uint32_t value;
    memcpy(&value, (const uint8_t *)base + offset, 4);
    return value;
}

static void test_receipt_rollback(void *handle) {
    int releases = eu4_gfx_backend_releases();
    char blocker[] = "/tmp/eu4-pr3-not-a-dir";
    FILE *block = fopen(blocker, "w");
    if (block == NULL) {
        fail("receipt blocker");
    }
    fclose(block);
    eu4_metal_events_set_dir(blocker);
    void *slot = (void *)0x1;
    eu4_gfx_set_image(NULL, &slot);
    uint8_t ok = 1;
    void *blocked = eu4_gfx_init(handle, &ok);
    if (ok != 0 || eu4_gfx_phase() != 0 || blocked == NULL || slot != blocked) {
        fail("unwritable receipt did not fail init");
    }
    if (*(void **)((uint8_t *)blocked + 0x180) != NULL ||
        *(void **)((uint8_t *)blocked + 0x80) != NULL ||
        *(void **)((uint8_t *)blocked + 0x98) != NULL) {
        fail("unwritable receipt left a header");
    }
    if (eu4_gfx_backend_releases() != releases + 1) {
        fail("unwritable receipt did not release the backend");
    }
    char dir[] = "/tmp/eu4-pr3-XXXXXX";
    if (mkdtemp(dir) == NULL) {
        fail("receipt retry dir");
    }
    eu4_metal_events_set_dir(dir);
    ok = 0;
    void *master = eu4_gfx_init(handle, &ok);
    if (ok != 1 || master == NULL || master == blocked || eu4_gfx_phase() != 2) {
        fail("init after a failed receipt did not succeed");
    }
    if (*(void **)((uint8_t *)blocked + 0x180) != NULL) {
        fail("first master regained a backend");
    }
    char path[96];
    snprintf(path, sizeof path, "%s/init.json", dir);
    FILE *file = fopen(path, "r");
    if (file == NULL) {
        fail("retry did not write init.json");
    }
    fclose(file);
    eu4_gfx_shutdown(master);
    if (eu4_gfx_backend_releases() != releases + 2) {
        fail("retry shutdown did not release once");
    }
    eu4_gfx_shutdown(blocked);
    if (eu4_gfx_backend_releases() != releases + 2) {
        fail("failed master was released twice");
    }
}

static const uint8_t kCustomSnap[0x24] = {
    0, 0, 0, 0, 0x01, 0x02, 0, 0, 1, 0, 0, 0, 0xdd, 0xcc, 0xbb, 0xaa,
    0x44, 0x33, 0x22, 0x11, 0x03, 0x02, 0, 0, 0, 0, 0, 0, 0x00, 0x1e, 0, 0,
    0x00, 0x1e, 0, 0};
static const uint8_t kFarSnap[0x24] = {
    0, 0, 0, 0, 0x03, 0x02, 0, 0, 1, 0, 0, 0, 0xdd, 0xcc, 0xbb, 0xaa,
    0x44, 0x33, 0x22, 0x11, 0x03, 0x02, 0, 0, 0, 0, 0, 0, 0x00, 0x1e, 0, 0,
    0x00, 0x1e, 0, 0};

static int g_depth_anchor;

static void __attribute__((noinline)) call_set_depth(void *handle, void *state) {
    eu4_gfx_set_depth(handle, state);
    g_depth_anchor = 1;
}

static void bind_master(uint8_t *object) {
    void *self = object;
    memcpy(object, &self, sizeof self);
}

static void fill_custom(uint8_t *object, uint32_t depth_compare) {
    memset(object, 0x3c, 0x4c);
    uint8_t *desc = object + 0x24;
    uint32_t write = 2;
    uint32_t mask_a = 0xaabbccdd;
    uint32_t mask_b = 0x11223344;
    uint32_t keep = 0;
    uint32_t zero_op = 1;
    uint32_t far_op = 0xffffffffu;
    uint32_t far_compare = 0xffffffffu;
    memset(desc, 0, 0x28);
    desc[0] = 0;
    desc[0xc] = 1;
    memcpy(desc + 4, &write, 4);
    memcpy(desc + 8, &depth_compare, 4);
    memcpy(desc + 0x10, &mask_a, 4);
    memcpy(desc + 0x14, &mask_b, 4);
    memcpy(desc + 0x18, &zero_op, 4);
    memcpy(desc + 0x1c, &keep, 4);
    memcpy(desc + 0x20, &far_op, 4);
    memcpy(desc + 0x24, &far_compare, 4);
}

static void apply_custom(const uint8_t *want, uint32_t depth_compare, const char *message) {
    uint8_t master[EU4_GFX_MASTER_SIZE];
    uint8_t object[0x4c];
    uint8_t saved_object[0x4c];
    memset(master, 0x5a, sizeof master);
    bind_master(master);
    fill_custom(object, depth_compare);
    memcpy(saved_object, object, sizeof object);
    g_stopped = 0;
    eu4_gfx_set_depth(master, object);
    if (g_stopped || memcmp(master + 0x30, want, 0x24) != 0 ||
        memcmp(object, saved_object, sizeof object) != 0) {
        fail(message);
    }
    for (int i = 8; i < 0x30; i++) {
        if (master[i] != 0x5a) {
            fail("depth wrote below the snapshot");
        }
    }
    for (int i = 0x54; i < EU4_GFX_MASTER_SIZE; i++) {
        if (master[i] != 0x5a) {
            fail("depth wrote above the snapshot");
        }
    }
}

static const void *depth_return_site(void) {
    const uint8_t *code = (const uint8_t *)call_set_depth;
    for (int i = 0; i < 96; i++) {
        if (code[i] != 0xe8) {
            continue;
        }
        int32_t rel = 0;
        memcpy(&rel, code + i + 1, sizeof rel);
        const uint8_t *target = code + i + 5 + rel;
        if (target == (const uint8_t *)eu4_gfx_set_depth) {
            return code + i + 5;
        }
    }
    return NULL;
}

static void test_depth_cases(const char *dir) {
    apply_custom(kCustomSnap, 1, "custom depth snapshot");
    apply_custom(kFarSnap, 0xffffffffu, "out-of-range depth compare");

    uint8_t master[EU4_GFX_MASTER_SIZE];
    uint8_t object[0x4c];
    uint8_t saved_object[0x4c];
    memset(master, 0x5a, sizeof master);
    bind_master(master);
    fill_custom(object, 1);
    memcpy(saved_object, object, sizeof object);
    void *slot = object;
    memcpy(master + 0x88, &slot, sizeof slot);
    g_stopped = 0;
    eu4_gfx_set_depth(master, NULL);
    if (g_stopped || memcmp(master + 0x30, kCustomSnap, sizeof kCustomSnap) != 0 ||
        memcmp(object, saved_object, sizeof object) != 0) {
        fail("null depth argument did not use master +0x88");
    }

    long page = sysconf(_SC_PAGESIZE);
    uint8_t *span = (uint8_t *)mmap(NULL, (size_t)page * 2, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON,
                                    -1, 0);
    if (span == MAP_FAILED) {
        fail("short depth map");
    }
    if (mprotect(span + page, (size_t)page, PROT_NONE) != 0) {
        fail("short depth guard");
    }
    uint8_t *short_state = span + page - 0x30;
    memset(short_state, 0x3c, 0x30);
    uint8_t short_saved[0x30];
    memcpy(short_saved, short_state, sizeof short_saved);
    memset(master, 0x5a, sizeof master);
    bind_master(master);
    uint8_t master_saved[EU4_GFX_MASTER_SIZE];
    memcpy(master_saved, master, sizeof master_saved);
    eu4_metal_events_set_image(0, 0);
    g_stopped = 0;
    eu4_gfx_set_depth(master, short_state);
    char early_path[128];
    snprintf(early_path, sizeof early_path, "%s/depth_state.json", dir);
    if (!g_stopped || memcmp(master, master_saved, sizeof master_saved) != 0 ||
        memcmp(short_state, short_saved, sizeof short_saved) != 0 || access(early_path, F_OK) == 0) {
        fail("short depth description changed state");
    }

    const void *site = depth_return_site();
    if (site == NULL) {
        fail("depth call site");
    }
    uintptr_t base = (uintptr_t)site - 0x149d061;
    eu4_metal_events_set_image(base, (uintptr_t)site + 1);
    g_stopped = 0;
    call_set_depth(master, short_state);
    eu4_metal_events_set_image(0, 0);
    if (!g_stopped || !g_depth_anchor || memcmp(master, master_saved, sizeof master_saved) != 0) {
        fail("depth receipt changed the master");
    }
    FILE *receipt = fopen(early_path, "r");
    char body[1024];
    if (receipt == NULL || fgets(body, sizeof body, receipt) == NULL) {
        fail("depth_state.json missing");
    }
    fclose(receipt);
    char hex[73];
    for (int i = 0; i < 36; i++) {
        memcpy(hex + i * 2, "5a", 2);
    }
    hex[72] = 0;
    char needle[96];
    snprintf(needle, sizeof needle, "\"master_depth\":\"%s\"", hex);
    if (strstr(body, "\"symbol\":\"_GfxSetDepthStencilState\"") == NULL ||
        strstr(body, "\"caller_rva\":\"0x149d061\"") == NULL ||
        strstr(body, "\"source\":\"explicit\"") == NULL ||
        strstr(body, "\"state_want\":76") == NULL || strstr(body, "\"state_got\":48") == NULL ||
        strstr(body, "\"state\":null") == NULL || strstr(body, "\"master_depth_want\":36") == NULL ||
        strstr(body, "\"master_depth_got\":36") == NULL || strstr(body, needle) == NULL) {
        fail("depth_state.json");
    }
    mprotect(span + page, (size_t)page, PROT_READ | PROT_WRITE);
    munmap(span, (size_t)page * 2);

    uint8_t *page_bytes = (uint8_t *)mmap(NULL, (size_t)page, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON,
                                          -1, 0);
    if (page_bytes == MAP_FAILED) {
        fail("read-only depth map");
    }
    memset(page_bytes, 0x5a, (size_t)page);
    bind_master(page_bytes);
    static const uint8_t default_snap[0x24] = {
        1, 1, 0, 0, 0x03, 0x02, 0, 0, 0, 0, 0, 0, 0xff, 0xff, 0xff, 0xff,
        0xff, 0xff, 0xff, 0xff, 0x07, 0x02, 0, 0, 0x00, 0x1e, 0, 0, 0x00, 0x1e, 0, 0,
        0x00, 0x1e, 0, 0};
    memcpy(page_bytes + 0x30, default_snap, sizeof default_snap);
    uint8_t equal[0x4c];
    memset(equal, 0, sizeof equal);
    eu4_gfx_write_default_depth_stencil(equal + 0x24);
    uint8_t *page_saved = (uint8_t *)malloc((size_t)page);
    if (page_saved == NULL) {
        fail("read-only depth image");
    }
    memcpy(page_saved, page_bytes, (size_t)page);
    if (mprotect(page_bytes, (size_t)page, PROT_READ) != 0) {
        fail("read-only depth protect");
    }
    g_stopped = 0;
    eu4_gfx_set_depth(page_bytes, equal);
    if (mprotect(page_bytes, (size_t)page, PROT_READ | PROT_WRITE) != 0) {
        fail("read-only depth restore");
    }
    if (g_stopped || memcmp(page_bytes, page_saved, (size_t)page) != 0) {
        fail("equal depth snapshot wrote a read-only master");
    }
    free(page_saved);
    munmap(page_bytes, (size_t)page);
}

static void set_viewport_height(void *master, uint32_t height) {
    uint8_t view[24];
    memset(view, 0, sizeof view);
    memcpy(view, &height, sizeof height);
    eu4_gfx_set_viewport(master, view);
}

static void check_scissor(void *handle, void *master, const int16_t *rect, int32_t gl_x, int32_t gl_y,
                          int32_t width, int32_t height) {
    int16_t saved[4];
    memcpy(saved, rect, sizeof saved);
    eu4_gfx_set_scissor_rectangle(handle, rect);
    int32_t got[4] = {-1, -1, -1, -1};
    if (!eu4_gfx_scissor_rectangle(master, got)) {
        fail("scissor state was not recorded");
    }
    if (got[0] != gl_x || got[1] != gl_y || got[2] != width || got[3] != height) {
        fail("scissor rectangle");
    }
    if (memcmp(saved, rect, sizeof saved) != 0) {
        fail("scissor changed the source rectangle");
    }
}

static void expect_metal(int set, int32_t gl_x, int32_t gl_y, int32_t width, int32_t height,
                         eu4_gfx_scissor_status want, uint32_t x, uint32_t y, uint32_t w, uint32_t h) {
    eu4_gfx_metal_scissor out;
    eu4_gfx_metal_scissor poison;
    memset(&out, 0x5a, sizeof out);
    memset(&poison, 0x5a, sizeof poison);
    eu4_gfx_scissor_status got = eu4_gfx_scissor_for_attachment(
        set, gl_x, gl_y, width, height, 64, 64, &out);
    if (got != want) {
        fail("scissor status");
    }
    if (want != EU4_GFX_SCISSOR_VALID) {
        if (memcmp(&out, &poison, sizeof out) != 0) {
            fail("empty scissor became a rectangle");
        }
        return;
    }
    if (out.x != x || out.y != y || out.width != w || out.height != h) {
        fail("scissor metal rect");
    }
}

static void test_scissor(void *master, id<MTLDevice> device) {
    int32_t got[4] = {-1, -1, -1, -1};
    if (eu4_gfx_scissor_rectangle(master, got) != 0) {
        fail("scissor was set before any call");
    }
    set_viewport_height(master, 0);
    void *handle_slot = master;
    void *handle = &handle_slot;
    /* Viewport height is still 0, so gl_y is the 32-bit subtract 0 - y2. */
    int16_t a[4] = {10, 20, 110, 70};
    check_scissor(handle, master, a, 10, -70, 100, 50);
    int16_t b[4] = {-5, -10, 25, 40};
    check_scissor(handle, master, b, -5, -40, 30, 50);
    int16_t inverted[4] = {90, 80, 40, 30};
    check_scissor(handle, master, inverted, 90, -30, 0, 0);
    int16_t narrow[4] = {7, 9, 7, 9};
    check_scissor(handle, master, narrow, 7, -9, 0, 0);
    int16_t wrapped[4] = {0, 0, 1, -1};
    check_scissor(handle, master, wrapped, 0, 1, 1, 0);
    set_viewport_height(master, 300);
    int16_t c[4] = {1, 2, 3, 4};
    check_scissor(handle, master, c, 1, 296, 2, 2);
    set_viewport_height(master, 999);
    if (!eu4_gfx_scissor_rectangle(master, got) || got[0] != 1 || got[1] != 296 || got[2] != 2 ||
        got[3] != 2) {
        fail("viewport change recomputed the stored scissor");
    }
    set_viewport_height(master, 0x80000000u);
    int16_t high[4] = {0, 0, 1, 0};
    check_scissor(handle, master, high, 0, (int32_t)0x80000000, 1, 0);
    eu4_gfx_set_scissor_rectangle(NULL, c);
    eu4_gfx_set_scissor_rectangle(handle, NULL);
    void *empty_handle = NULL;
    eu4_gfx_set_scissor_rectangle(&empty_handle, c);
    uint8_t fake[EU4_GFX_MASTER_SIZE];
    memset(fake, 0, sizeof fake);
    *(void **)(fake + 0x180) = (void *)0x1;
    void *fake_slot = fake;
    eu4_gfx_set_scissor_rectangle(&fake_slot, c);
    if (eu4_gfx_scissor_rectangle(fake, got) != 0) {
        fail("a noncurrent master held scissor state");
    }
    if (!eu4_gfx_scissor_rectangle(master, got) || got[0] != 0 || got[1] != (int32_t)0x80000000 ||
        got[2] != 1 || got[3] != 0) {
        fail("null scissor input changed the pending rectangle");
    }
    expect_metal(0, 4, 38, 40, 20, EU4_GFX_SCISSOR_UNSET, 0, 0, 0, 0);
    expect_metal(1, 4, 38, 40, 20, EU4_GFX_SCISSOR_VALID, 4, 6, 40, 20);
    expect_metal(1, -5, -10, 30, 50, EU4_GFX_SCISSOR_VALID, 0, 24, 25, 40);
    expect_metal(1, 50, 10, 30, 20, EU4_GFX_SCISSOR_VALID, 50, 34, 14, 20);
    expect_metal(1, 100, 0, 10, 10, EU4_GFX_SCISSOR_EMPTY, 0, 0, 0, 0);
    expect_metal(1, 7, 9, 0, 0, EU4_GFX_SCISSOR_EMPTY, 0, 0, 0, 0);
    expect_metal(1, 1, 296, 2, 2, EU4_GFX_SCISSOR_EMPTY, 0, 0, 0, 0);
    set_viewport_height(master, 64);
    int16_t chain[4] = {4, 6, 44, 26};
    eu4_gfx_set_scissor_rectangle(handle, chain);
    int32_t stored[4] = {-1, -1, -1, -1};
    int set = eu4_gfx_scissor_rectangle(master, stored);
    if (!set || stored[0] != 4 || stored[1] != 38 || stored[2] != 40 || stored[3] != 20) {
        fail("encoder scissor was not stored");
    }
    eu4_gfx_metal_scissor metal;
    if (eu4_gfx_scissor_for_attachment(
            set, stored[0], stored[1], stored[2], stored[3], 64, 64, &metal) != EU4_GFX_SCISSOR_VALID ||
        metal.x != 4 || metal.y != 6 || metal.width != 40 || metal.height != 20) {
        fail("encoder scissor was not valid");
    }
    MTLTextureDescriptor *desc = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm
                                                                                     width:64
                                                                                    height:64
                                                                                 mipmapped:NO];
    desc.usage = MTLTextureUsageRenderTarget;
    desc.storageMode = MTLStorageModeShared;
    id<MTLTexture> target = [device newTextureWithDescriptor:desc];
    if (target == nil) {
        fail("scissor render target");
    }
    MTLRenderPassDescriptor *pass = [MTLRenderPassDescriptor renderPassDescriptor];
    pass.colorAttachments[0].texture = target;
    pass.colorAttachments[0].loadAction = MTLLoadActionClear;
    pass.colorAttachments[0].storeAction = MTLStoreActionStore;
    pass.colorAttachments[0].clearColor = MTLClearColorMake(0, 0, 0, 1);
    EU4GfxBackend *backend = (__bridge EU4GfxBackend *)*(void **)((uint8_t *)master + 0x180);
    id<MTLCommandBuffer> command = [backend.queue commandBuffer];
    id<MTLRenderCommandEncoder> encoder =
        command != nil ? [command renderCommandEncoderWithDescriptor:pass] : nil;
    if (encoder == nil) {
        fail("scissor encoder");
    }
    [encoder setScissorRect:(MTLScissorRect){metal.x, metal.y, metal.width, metal.height}];
    [encoder endEncoding];
    [command commit];
    [command waitUntilCompleted];
    if (command.status != MTLCommandBufferStatusCompleted || command.error != nil) {
        fail("scissor encoder rejected the rectangle");
    }
}

static void set_full_viewport(void *master, uint32_t width, uint32_t height) {
    int32_t rect[6] = {0, 0, 0, 0, 0, 0};
    rect[0] = (int32_t)height;
    rect[1] = (int32_t)width;
    eu4_gfx_set_viewport(master, rect);
}

static void expect_uniform_bgra(const uint8_t *bytes, size_t len, uint8_t b, uint8_t g, uint8_t r,
                                uint8_t a) {
    if (len % 4 != 0) {
        fail("readback length");
    }
    for (size_t i = 0; i + 3 < len; i += 4) {
        if (bytes[i] != b || bytes[i + 1] != g || bytes[i + 2] != r || bytes[i + 3] != a) {
            fail("clear readback pixels");
        }
    }
}

static void test_backend(void) {
    char dir[] = "/tmp/eu4-pr3-XXXXXX";
    if (mkdtemp(dir) == NULL) {
        fail("temp dir");
    }
    eu4_metal_events_set_dir(dir);
    eu4_gfx_set_terminate(on_stop);
    eu4_gfx_set_sdl(fake_window, fake_size);
    eu4_gfx_set_default_writers(eu4_gfx_write_default_blend, write_depth, eu4_gfx_write_default_raster);
    id<MTLDevice> device = MTLCreateSystemDefaultDevice();
    if (device == nil) {
        fail("no Metal device");
    }
    CAMetalLayer *layer = [CAMetalLayer layer];
    layer.device = device;
    eu4_gfx_set_layer((__bridge void *)layer);

    uint8_t records[16 * 12];
    memset(records, 0, sizeof records);
    for (int i = 0; i < 15; i++) {
        uint32_t sample = (uint32_t)(i + 2);
        memcpy(records + i * 12, &sample, 4);
    }
    records[15 * 12] = 0xab;
    uint8_t settings[0x28];
    memset(settings, 0x5a, sizeof settings);
    void *array = records;
    memcpy(settings + 8, &array, sizeof array);
    if (eu4_gfx_settings(1, 0, 15, settings) != 0) {
        fail("settings return");
    }
    uint32_t threshold = 2;
    for (int i = 0; i < 15; i++) {
        uint32_t sample = (uint32_t)(i + 2);
        if (sample > threshold && threshold <= 0x3fffffffu) {
            threshold <<= 1;
        }
        int want = sample == threshold && [device supportsTextureSampleCount:sample];
        if (records[i * 12 + 4] != (want ? 1 : 0) || load_at(records, i * 12) != sample) {
            fail("sample record flag or order");
        }
    }
    if (records[15 * 12] != 0xab || settings[0x10] != 1 || load_at(settings, 0x14) != 0 ||
        load_at(settings, 0x20) != 0x400) {
        fail("settings bounds or immediates");
    }
    uint64_t span = 0;
    memcpy(&span, settings + 0x18, 8);
    if (span != 0x30000000300ULL) {
        fail("settings span");
    }
    eu4_gfx_test_no_device(1);
    memset(records, 0xab, 15 * 12);
    for (int i = 0; i < 15; i++) {
        uint32_t sample = (uint32_t)(i + 2);
        memcpy(records + i * 12, &sample, 4);
    }
    if (eu4_gfx_settings(1, 0, 0, settings) != 0 || records[4] != 0xab) {
        fail("zero count wrote a record");
    }
    eu4_gfx_settings(1, 0, 15, settings);
    for (int i = 0; i < 15; i++) {
        if (records[i * 12 + 4] != 0) {
            fail("missing device marked a sample");
        }
    }
    eu4_gfx_test_no_device(0);

    uint8_t handle[0x20];
    memset(handle, 0, sizeof handle);
    uint32_t window_id = 7, source = 9;
    memcpy(handle, &window_id, 4);
    memcpy(handle + 0x18, &source, 4);
    void *slot = (void *)0x1;
    eu4_gfx_set_image(NULL, &slot);
    eu4_gfx_test_throw_on_alloc(1);
    uint8_t ok = 1;
    if (eu4_gfx_init(handle, &ok) != NULL || ok != 0 || slot != (void *)0x1) {
        fail("master allocation failure escaped or returned a block");
    }
    eu4_gfx_test_throw_on_alloc(3);
    int releases = eu4_gfx_backend_releases();
    ok = 1;
    void *partial = eu4_gfx_init(handle, &ok);
    if (ok != 0 || partial == NULL || *(void **)((uint8_t *)partial + 0x180) != NULL ||
        *(void **)((uint8_t *)partial + 0x80) != NULL || *(void **)((uint8_t *)partial + 0x88) != NULL) {
        fail("header failure left a live pointer");
    }
    int16_t pending[4] = {1, 2, 3, 4};
    void *partial_slot = partial;
    eu4_gfx_set_scissor_rectangle(&partial_slot, pending);
    int32_t scissor_out[4] = {9, 9, 9, 9};
    if (eu4_gfx_scissor_rectangle(partial, scissor_out) != 0) {
        fail("failed init recorded a scissor");
    }
    if (eu4_gfx_backend_releases() != releases + 1) {
        fail("header failure did not release the backend once");
    }
    eu4_gfx_shutdown(partial);
    if (eu4_gfx_backend_releases() != releases + 1 || slot != NULL) {
        fail("partial shutdown released the backend again");
    }
    eu4_gfx_test_throw_on_alloc(0);

    eu4_gfx_set_default_writers(bad_blend, write_depth, eu4_gfx_write_default_raster);
    g_stopped = 0;
    ok = 1;
    void *rejected = eu4_gfx_init(handle, &ok);
    if (ok != 0 || g_stopped || rejected == NULL) {
        fail("unsupported description during init did not stay a failed init");
    }
    eu4_gfx_shutdown(rejected);
    eu4_gfx_set_default_writers(eu4_gfx_write_default_blend, write_depth, eu4_gfx_write_default_raster);

    ok = 0;
    void *master = eu4_gfx_init(handle, &ok);
    if (ok != 1 || master == NULL || *(void **)master != master || slot != master) {
        fail("init did not publish the master");
    }
    if (load_at(master, 0xa0) != 800 || load_at(master, 0xa4) != 600 || load_at(master, 0xa8) != 9) {
        fail("window size or source");
    }
    if (*(void **)((uint8_t *)master + 0x178) != (void *)0x11 ||
        *(void **)((uint8_t *)master + 0x70) != NULL || ((uint8_t *)master)[0x194] != 0 ||
        ((uint8_t *)master)[0x128] != 0 || ((uint8_t *)master)[0x170] != 0) {
        fail("window, shader, wireframe, or dirty byte");
    }
    uint8_t view[0x18];
    memcpy(view, (uint8_t *)master + 0xac, sizeof view);
    for (int i = 0; i < 0x18; i++) {
        if (view[i] != 0) {
            fail("viewport was not left zero");
        }
    }
    uint32_t expect_limit =
        ([device supportsFamily:MTLGPUFamilyApple3] || [device supportsFamily:MTLGPUFamilyMac2])
            ? 16384
            : 8192;
    if (![device supportsFamily:MTLGPUFamilyApple3] && ![device supportsFamily:MTLGPUFamilyMac2] &&
        [device supportsFamily:MTLGPUFamilyApple2]) {
        expect_limit = 8192;
    }
    if (load_at(master, 0x188) != expect_limit) {
        fail("max texture");
    }
    for (int i = 4; i < 11; i++) {
        if (((uint8_t *)master)[0x188 + i] != 0) {
            fail("capability span was not otherwise zero");
        }
    }
    EU4GfxBackend *backend = (__bridge EU4GfxBackend *)*(void **)((uint8_t *)master + 0x180);
    id<MTLDevice> owned = backend.device;
    id<MTLCommandQueue> queue = backend.queue;
    id<MTLDepthStencilState> depth = backend.depthState;
    id<MTLTexture> texture = backend.fallback;
    if (owned != device || queue == nil || depth == nil || texture == nil || texture.width != 4 ||
        texture.height != 4 || texture.pixelFormat != MTLPixelFormatBGRA8Unorm) {
        fail("backend Metal objects");
    }
    if ((texture.usage & MTLTextureUsageRenderTarget) != 0) {
        fail("fallback texture is a render target");
    }
    uint8_t pixels[64];
    [texture getBytes:pixels bytesPerRow:16 fromRegion:MTLRegionMake2D(0, 0, 4, 4) mipmapLevel:0];
    for (int i = 0; i < 64; i++) {
        if (pixels[i] != 0xff) {
            fail("fallback texels");
        }
    }
    uint8_t viewport_before_clear[24];
    memcpy(viewport_before_clear, (uint8_t *)master + 0xac, sizeof viewport_before_clear);
    void *null_master = NULL;
    if (eu4_gfx_clear_checked(NULL, 1, NULL) != EU4_GFX_CLEAR_UNSUPPORTED ||
        eu4_gfx_clear_checked(&null_master, 1, NULL) != EU4_GFX_CLEAR_UNSUPPORTED) {
        fail("null handle clear accepted");
    }
    uint8_t foreign[EU4_GFX_MASTER_SIZE];
    memset(foreign, 0, sizeof foreign);
    void *foreign_slot = foreign;
    if (eu4_gfx_clear_checked(&foreign_slot, 1, NULL) != EU4_GFX_CLEAR_UNSUPPORTED) {
        fail("noncurrent master clear accepted");
    }
    set_full_viewport(master, 16, 16);
    if (eu4_gfx_configure_offscreen(master, 16, 16) != EU4_GFX_CLEAR_OK) {
        fail("configure offscreen in backend");
    }
    void *clear_handle = master;
    float red[4] = {1.f, 0.f, 0.f, 1.f};
    if (eu4_gfx_clear_checked(&clear_handle, 1, red) != EU4_GFX_CLEAR_OK) {
        fail("backend clear submit");
    }
    if (eu4_gfx_clear_checked(&clear_handle, 1, NULL) != EU4_GFX_CLEAR_UNSUPPORTED) {
        fail("pending clear accepted a second submit");
    }
    int clear_status = 0;
    if (eu4_gfx_complete_offscreen(master, &clear_status) != EU4_GFX_CLEAR_OK) {
        fail("backend clear complete");
    }
    uint8_t cleared[16 * 16 * 4];
    size_t cleared_len = 0;
    if (eu4_gfx_read_offscreen(master, cleared, sizeof cleared, &cleared_len) != EU4_GFX_CLEAR_OK) {
        fail("backend clear readback");
    }
    expect_uniform_bgra(cleared, cleared_len, 0x00, 0x00, 0xff, 0xff);
    eu4_gfx_test_fail_offscreen_alloc(1);
    if (eu4_gfx_configure_offscreen(master, 32, 32) != EU4_GFX_CLEAR_FAILED) {
        fail("backend alloc failure");
    }
    eu4_gfx_test_fail_offscreen_alloc(0);
    if (eu4_gfx_clear_checked(&clear_handle, 1, NULL) != EU4_GFX_CLEAR_OK ||
        eu4_gfx_complete_offscreen(master, &clear_status) != EU4_GFX_CLEAR_OK ||
        eu4_gfx_read_offscreen(master, cleared, sizeof cleared, &cleared_len) != EU4_GFX_CLEAR_OK) {
        fail("prior target lost after failed configure");
    }
    expect_uniform_bgra(cleared, cleared_len, 0x00, 0x00, 0x00, 0xff);
    if (eu4_gfx_clear_checked(&clear_handle, 1, NULL) != EU4_GFX_CLEAR_OK) {
        fail("submit before shutdown drain");
    }
    /* Drain happens inside shutdown; recreate a live master afterward. */
    int releases_before_drain = eu4_gfx_backend_releases();
    backend = nil;
    owned = nil;
    queue = nil;
    depth = nil;
    texture = nil;
    eu4_gfx_shutdown(master);
    int releases_after_drain = eu4_gfx_backend_releases();
    if (eu4_gfx_backend_token() != NULL || releases_after_drain < releases_before_drain + 1) {
        char msg[96];
        snprintf(msg, sizeof msg, "drained shutdown did not release %d->%d", releases_before_drain,
                 releases_after_drain);
        fail(msg);
    }
    ok = 0;
    master = eu4_gfx_init(handle, &ok);
    if (ok != 1 || master == NULL) {
        fail("reinit after drained shutdown");
    }
    backend = (__bridge EU4GfxBackend *)*(void **)((uint8_t *)master + 0x180);
    device = backend.device;
    clear_handle = master;
    set_full_viewport(master, 16, 16);
    if (eu4_gfx_configure_offscreen(master, 16, 16) != EU4_GFX_CLEAR_OK ||
        eu4_gfx_clear_checked(&clear_handle, 1, NULL) != EU4_GFX_CLEAR_OK ||
        eu4_gfx_complete_offscreen(master, &clear_status) != EU4_GFX_CLEAR_OK) {
        fail("clear after reinit");
    }
    memcpy((uint8_t *)master + 0xac, viewport_before_clear, sizeof viewport_before_clear);
    test_scissor(master, device);
    if (eu4_gfx_configure_offscreen(master, 16, 16) != EU4_GFX_CLEAR_OK) {
        fail("configure after scissor");
    }
    set_full_viewport(master, 16, 16);
    clear_handle = master;
    if (eu4_gfx_clear_checked(&clear_handle, 1, NULL) != EU4_GFX_CLEAR_UNSUPPORTED) {
        fail("clear with pending scissor");
    }
    memcpy((uint8_t *)master + 0xac, viewport_before_clear, sizeof viewport_before_clear);
    void *blend = *(void **)((uint8_t *)master + 0x80);
    void *depth_header = *(void **)((uint8_t *)master + 0x88);
    void *raster = *(void **)((uint8_t *)master + 0x90);
    void *tex_header = *(void **)((uint8_t *)master + 0x98);
    if (blend != backend.blendHeader || depth_header != backend.depthHeader ||
        raster != backend.rasterHeader || tex_header != backend.textureHeader) {
        fail("master slots are not the backend headers");
    }
    if (load_at(master, 8) != 4) {
        fail("primitive token");
    }
    if (load_at(tex_header, 4) != 0) {
        fail("fallback header name");
    }
    static const uint32_t description[7] = {4, 4, 0xb, 4, 4, 4, 1};
    uint32_t actual[7];
    memcpy(actual, (uint8_t *)tex_header + 0x28, sizeof actual);
    if (memcmp(actual, description, sizeof actual) != 0) {
        fail("fallback description");
    }
    if (load_at(master, 0xc + 0xc) == load_at(blend, 0x24 + 0xc)) {
        fail("blend snapshot copied the description");
    }
    static const uint8_t default_snap[0x24] = {
        1, 1, 0, 0, 0x03, 0x02, 0, 0, 0, 0, 0, 0, 0xff, 0xff, 0xff, 0xff,
        0xff, 0xff, 0xff, 0xff, 0x07, 0x02, 0, 0, 0x00, 0x1e, 0, 0, 0x00, 0x1e, 0, 0,
        0x00, 0x1e, 0, 0};
    if (memcmp((uint8_t *)master + 0x30, default_snap, sizeof default_snap) != 0) {
        fail("depth snapshot");
    }
    if (load_at(master, 0x54) != 0x405 || ((uint8_t *)master)[0x54 + 0xd] != 1) {
        fail("rasterizer snapshot");
    }

    uint8_t saved[0x24];
    memcpy(saved, (uint8_t *)master + 0xc, sizeof saved);
    uint8_t junk[0x48];
    memset(junk, 0, sizeof junk);
    g_stopped = 0;
    eu4_gfx_set_blend(master, junk);
    if (!g_stopped || memcmp((uint8_t *)master + 0xc, saved, sizeof saved) != 0) {
        fail("unsupported state after success did not stop");
    }

    uint8_t blend_snap[0x24], depth_snap[0x24], raster_snap[0x10];
    memcpy(blend_snap, (uint8_t *)master + 0xc, sizeof blend_snap);
    memcpy(depth_snap, (uint8_t *)master + 0x30, sizeof depth_snap);
    memcpy(raster_snap, (uint8_t *)master + 0x54, sizeof raster_snap);
    memset((uint8_t *)master + 0xc, 0x11, sizeof blend_snap);
    memset((uint8_t *)master + 0x30, 0x22, sizeof depth_snap);
    memset((uint8_t *)master + 0x54, 0x33, sizeof raster_snap);
    ((uint8_t *)master)[0x128] = 0x5a;
    ((uint8_t *)master)[0x170] = 0xa5;
    g_stopped = 0;
    if (eu4_gfx_reset(master) != 1 || g_stopped) {
        fail("reset did not return 1");
    }
    uint8_t expect_blend[0x24], expect_depth[0x24], expect_raster[0x10];
    memset(expect_blend, 0x11, sizeof expect_blend);
    memset(expect_depth, 0x22, sizeof expect_depth);
    memset(expect_raster, 0x33, sizeof expect_raster);
    if (memcmp((uint8_t *)master + 0xc, expect_blend, sizeof expect_blend) != 0 ||
        memcmp((uint8_t *)master + 0x30, expect_depth, sizeof expect_depth) != 0 ||
        memcmp((uint8_t *)master + 0x54, expect_raster, sizeof expect_raster) != 0) {
        fail("reset rewrote a snapshot");
    }
    if (((uint8_t *)master)[0x128] != 0x5a || ((uint8_t *)master)[0x170] != 0xa5) {
        fail("reset changed a dirty byte");
    }
    if (*(void **)((uint8_t *)master + 0x80) != blend ||
        *(void **)((uint8_t *)master + 0x88) != depth_header ||
        *(void **)((uint8_t *)master + 0x90) != raster) {
        fail("reset changed a state object");
    }

    g_stopped = 0;
    eu4_gfx_on_reset_master(master);
    if (g_stopped || memcmp((uint8_t *)master + 0xc, blend_snap, sizeof blend_snap) != 0 ||
        memcmp((uint8_t *)master + 0x30, depth_snap, sizeof depth_snap) != 0 ||
        memcmp((uint8_t *)master + 0x54, raster_snap, sizeof raster_snap) != 0) {
        fail("on-reset did not restore the default snapshots");
    }
    ((uint8_t *)master)[0x128] = 0;
    ((uint8_t *)master)[0x170] = 0;

    void *saved_blend = *(void **)((uint8_t *)master + 0x80);
    uint8_t junk_object[0x48];
    memset(junk_object, 0, sizeof junk_object);
    memcpy(saved, (uint8_t *)master + 0xc, sizeof saved);
    *(void **)((uint8_t *)master + 0x80) = junk_object;
    g_stopped = 0;
    eu4_gfx_on_reset_master(master);
    int stopped = g_stopped;
    *(void **)((uint8_t *)master + 0x80) = saved_blend;
    g_stopped = 0;
    if (!stopped || memcmp((uint8_t *)master + 0xc, saved, sizeof saved) != 0) {
        fail("unsupported object on reset did not stop");
    }
    char init_path[96];
    snprintf(init_path, sizeof init_path, "%s/init.json", dir);
    FILE *init_file = fopen(init_path, "r");
    char first[512];
    if (init_file == NULL || fgets(first, sizeof first, init_file) == NULL) {
        fail("init.json missing");
    }
    fclose(init_file);
    backend = nil;
    releases = eu4_gfx_backend_releases();
    ok = 1;
    if (eu4_gfx_init(handle, &ok) != master || ok != 0) {
        fail("reentry changed the master");
    }
    init_file = fopen(init_path, "r");
    char second[512];
    if (init_file == NULL || fgets(second, sizeof second, init_file) == NULL ||
        strcmp(first, second) != 0) {
        fail("reentry rewrote init.json");
    }
    fclose(init_file);
    if (eu4_gfx_backend_releases() != releases) {
        fail("reentry created a backend");
    }
    test_depth_cases(dir);
    eu4_gfx_shutdown(master);
    if (eu4_gfx_backend_releases() != releases + 1 || slot != NULL) {
        fail("shutdown did not release the backend once");
    }
    test_receipt_rollback(handle);
}

static void test_viewport(void) {
    if (eu4_gfx_phase() != 0 || eu4_gfx_backend_releases() != 0) {
        fail("viewport test started with a backend");
    }
    uint8_t master[EU4_GFX_MASTER_SIZE];
    memset(master, 0xa5, sizeof master);
    uint8_t rect[24];
    for (int i = 0; i < 24; i++) {
        rect[i] = (uint8_t)(0xa3 + i * 13);
    }
    eu4_gfx_set_viewport(master, rect);
    if (memcmp(master + 0xac, rect, 24) != 0) {
        fail("viewport image");
    }
    for (int i = 0; i < 0xac; i++) {
        if (master[i] != 0xa5) {
            fail("viewport wrote below the record");
        }
    }
    for (int i = 0xc4; i < EU4_GFX_MASTER_SIZE; i++) {
        if (master[i] != 0xa5) {
            fail("viewport wrote above the record");
        }
    }
    uint8_t again[24];
    for (int i = 0; i < 24; i++) {
        again[i] = (uint8_t)(0x5c - i);
    }
    eu4_gfx_set_viewport(master, again);
    if (memcmp(master + 0xac, again, 24) != 0) {
        fail("viewport update");
    }
    uint8_t got[24];
    memcpy(got + 0x10, master + 0xbc, 8);
    memcpy(got, master + 0xac, 8);
    memcpy(got + 8, master + 0xb4, 8);
    if (memcmp(got, again, 24) != 0) {
        fail("getter layout");
    }
    if (eu4_gfx_phase() != 0 || eu4_gfx_backend_releases() != 0) {
        fail("viewport touched the backend");
    }
}

static char *g_argv0;

static void run_missing_backend(const char *dir) {
    eu4_metal_events_set_dir(dir);
    eu4_gfx_set_terminate(on_stop);
    eu4_gfx_set_sdl(fake_window, fake_size);
    eu4_gfx_set_default_writers(eu4_gfx_write_default_blend, write_depth, eu4_gfx_write_default_raster);
    id<MTLDevice> device = MTLCreateSystemDefaultDevice();
    if (device == nil) {
        fail("no Metal device");
    }
    CAMetalLayer *layer = [CAMetalLayer layer];
    layer.device = device;
    eu4_gfx_set_layer((__bridge void *)layer);
    uint8_t handle_bytes[0x20];
    memset(handle_bytes, 0, sizeof handle_bytes);
    uint32_t window_id = 7, source = 9;
    memcpy(handle_bytes, &window_id, 4);
    memcpy(handle_bytes + 0x18, &source, 4);
    uint8_t ok = 0;
    void *master = eu4_gfx_init(handle_bytes, &ok);
    if (ok != 1 || master == NULL) {
        fail("missing-backend init");
    }
    *(void **)((uint8_t *)master + 0x180) = NULL;
    void *slot = master;
    int16_t rect[4] = {0, 0, 1, 1};
    eu4_gfx_set_scissor_rectangle(&slot, rect);
    fail("missing backend returned");
}

static void expect_missing_backend(void) {
    char dir[] = "/tmp/eu4-scissor-XXXXXX";
    if (mkdtemp(dir) == NULL) {
        fail("child dir");
    }
    char *argv[] = {g_argv0, (char *)"missing-backend", dir, NULL};
    pid_t pid = 0;
    if (posix_spawn(&pid, g_argv0, NULL, NULL, argv, environ) != 0) {
        fail("spawn");
    }
    int status = 0;
    if (waitpid(pid, &status, 0) != pid || !WIFEXITED(status) || WEXITSTATUS(status) != 2) {
        fail("missing-backend exit");
    }
    char path[80];
    snprintf(path, sizeof path, "%s/stop.json", dir);
    FILE *file = fopen(path, "r");
    char body[1280];
    if (file == NULL || fgets(body, sizeof body, file) == NULL) {
        fail("stop.json");
    }
    fclose(file);
    if (strstr(body, "\"symbol\":\"_GfxSetScissorRectangle\"") == NULL ||
        strstr(body, "\"reason\":\"missing_backend\"") == NULL ||
        strstr(body, "\"phase\":\"after_init\"") == NULL) {
        fail("missing-backend diagnostic");
    }
}

int main(int argc, char **argv) {
    g_argv0 = argv[0];
    if (argc > 2 && strcmp(argv[1], "missing-backend") == 0) {
        run_missing_backend(argv[2]);
        return 0;
    }
    uint8_t desc[EU4_GFX_DEPTH_DESC_SIZE];
    memset(desc, 0xa5, sizeof desc);
    test_viewport();
    test_writes();
    eu4_gfx_write_default_depth_stencil(desc);
    test_view(desc);
    test_state(desc);
    test_backend();
    expect_missing_backend();
    return 0;
}
