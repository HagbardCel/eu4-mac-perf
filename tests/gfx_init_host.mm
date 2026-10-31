#import "../benchmark/eu4_gfx_init.h"

#import <CoreFoundation/CoreFoundation.h>
#import <Metal/Metal.h>
#import <stdio.h>
#import <stdlib.h>
#import <string.h>

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
    CFIndex before = CFGetRetainCount((__bridge CFTypeRef)device);
    void *raw = NULL;
    CFIndex held = 0;
    @autoreleasepool {
        raw = eu4_gfx_default_depth_stencil((__bridge void *)device, desc);
        held = CFGetRetainCount((__bridge CFTypeRef)device);
    }
    if (raw == NULL) {
        fail("default state was not created");
    }
    /* The returned state retains the device. Metal then keeps that state cached. */
    if (held != before + 1) {
        fprintf(stderr, "device retain %ld -> %ld\n", (long)before, (long)held);
        fail("state did not retain the borrowed device exactly once");
    }
    @autoreleasepool {
        id<MTLDepthStencilState> state = (__bridge_transfer id<MTLDepthStencilState>)raw;
        if (state.device != device) {
            fail("state device is not the borrowed device");
        }
    }
    if (CFGetRetainCount((__bridge CFTypeRef)device) != before + 1) {
        fail("cached state did not keep exactly one device retain");
    }
    @autoreleasepool {
        void *again = eu4_gfx_default_depth_stencil((__bridge void *)device, desc);
        if (again == NULL) {
            fail("second state was not created");
        }
        if (CFGetRetainCount((__bridge CFTypeRef)device) != before + 1) {
            fail("second state retained the device again");
        }
        id<MTLDepthStencilState> state = (__bridge_transfer id<MTLDepthStencilState>)again;
        if (state.device != device) {
            fail("second state device is not the borrowed device");
        }
    }
    if (CFGetRetainCount((__bridge CFTypeRef)device) != before + 1) {
        fail("releasing the second state changed the device retain");
    }
    uint8_t rejected[EU4_GFX_DEPTH_DESC_SIZE];
    memcpy(rejected, desc, sizeof rejected);
    rejected[0] = 0;
    if (eu4_gfx_default_depth_stencil((__bridge void *)device, rejected) != NULL) {
        fail("rejected description created a state");
    }
    if (CFGetRetainCount((__bridge CFTypeRef)device) != before + 1) {
        fail("rejected description retained the device");
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

int main(void) {
    uint8_t desc[EU4_GFX_DEPTH_DESC_SIZE];
    memset(desc, 0xa5, sizeof desc);
    test_writes();
    eu4_gfx_write_default_depth_stencil(desc);
    test_view(desc);
    test_state(desc);
    return 0;
}
