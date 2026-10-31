#import "eu4_gfx_init.h"

#import <Metal/Metal.h>
#import <string.h>

void eu4_gfx_write_default_depth_stencil(uint8_t desc[EU4_GFX_DEPTH_DESC_SIZE]) {
    uint64_t depth_word = 0x300000001ULL;
    uint32_t mask = 0xffffffffu;
    uint32_t zero = 0;
    uint64_t stencil_word = 0x700000000ULL;
    desc[0] = 1;
    memcpy(desc + 4, &depth_word, sizeof depth_word);
    desc[0xc] = 0;
    memcpy(desc + 0x10, &mask, sizeof mask);
    memcpy(desc + 0x14, &mask, sizeof mask);
    memcpy(desc + 0x18, &zero, sizeof zero);
    memcpy(desc + 0x1c, &zero, sizeof zero);
    memcpy(desc + 0x20, &stencil_word, sizeof stencil_word);
}

static uint32_t load_u32(const uint8_t *desc, int offset) {
    uint32_t value;
    memcpy(&value, desc + offset, sizeof value);
    return value;
}

/* Accepts only the stores in GetDefaultDepthStencil. Other patterns are refused. */
static int accept_default(const uint8_t *desc) {
    if (desc == NULL) {
        return -1;
    }
    if (desc[0] != 1 || load_u32(desc, 4) != 1 || load_u32(desc, 8) != 3 || desc[0xc] != 0) {
        return -1;
    }
    if (load_u32(desc, 0x10) != 0xffffffffu || load_u32(desc, 0x14) != 0xffffffffu) {
        return -1;
    }
    if (load_u32(desc, 0x18) != 0 || load_u32(desc, 0x1c) != 0 || load_u32(desc, 0x20) != 0 ||
        load_u32(desc, 0x24) != 7) {
        return -1;
    }
    return 0;
}

static void apply_stencil(MTLStencilDescriptor *face) {
    /* Compare 7 is GL_ALWAYS. Op 0 is GL_KEEP (0x1e00). Both masks are all-ones. */
    face.stencilCompareFunction = MTLCompareFunctionAlways;
    face.stencilFailureOperation = MTLStencilOperationKeep;
    face.depthFailureOperation = MTLStencilOperationKeep;
    face.depthStencilPassOperation = MTLStencilOperationKeep;
    face.readMask = 0xffffffffu;
    face.writeMask = 0xffffffffu;
}

static MTLDepthStencilDescriptor *descriptor_for(const uint8_t *desc) {
    if (accept_default(desc) != 0) {
        return nil;
    }
    MTLDepthStencilDescriptor *descriptor = [MTLDepthStencilDescriptor new];
    /* Engine compare 3 is GL_LEQUAL (3 + 0x200). */
    descriptor.depthCompareFunction = MTLCompareFunctionLessEqual;
    descriptor.depthWriteEnabled = YES;
    descriptor.frontFaceStencil = [MTLStencilDescriptor new];
    descriptor.backFaceStencil = [MTLStencilDescriptor new];
    apply_stencil(descriptor.frontFaceStencil);
    apply_stencil(descriptor.backFaceStencil);
    return descriptor;
}

static int face_matches(MTLStencilDescriptor *face, const eu4_gfx_depth_view *view) {
    return face != nil && (int)face.stencilCompareFunction == view->stencil_compare &&
           (int)face.stencilFailureOperation == view->stencil_fail &&
           (int)face.depthFailureOperation == view->stencil_depth_fail &&
           (int)face.depthStencilPassOperation == view->stencil_pass &&
           face.readMask == view->stencil_read_mask && face.writeMask == view->stencil_write_mask;
}

int eu4_gfx_default_depth_stencil_view(const uint8_t *desc, eu4_gfx_depth_view *out) {
    MTLDepthStencilDescriptor *descriptor = descriptor_for(desc);
    if (descriptor == nil || out == NULL) {
        return -1;
    }
    out->depth_compare = (int)descriptor.depthCompareFunction;
    out->depth_write = descriptor.isDepthWriteEnabled ? 1 : 0;
    out->stencil_compare = (int)descriptor.frontFaceStencil.stencilCompareFunction;
    out->stencil_fail = (int)descriptor.frontFaceStencil.stencilFailureOperation;
    out->stencil_depth_fail = (int)descriptor.frontFaceStencil.depthFailureOperation;
    out->stencil_pass = (int)descriptor.frontFaceStencil.depthStencilPassOperation;
    out->stencil_read_mask = descriptor.frontFaceStencil.readMask;
    out->stencil_write_mask = descriptor.frontFaceStencil.writeMask;
    if (!face_matches(descriptor.backFaceStencil, out)) {
        return -1;
    }
    return 0;
}

void *eu4_gfx_default_depth_stencil(void *device, const uint8_t *desc) {
    if (device == NULL) {
        return NULL;
    }
    MTLDepthStencilDescriptor *descriptor = descriptor_for(desc);
    if (descriptor == nil) {
        return NULL;
    }
    id<MTLDevice> borrowed = (__bridge id<MTLDevice>)device;
    id<MTLDepthStencilState> state = [borrowed newDepthStencilStateWithDescriptor:descriptor];
    if (state == nil) {
        return NULL;
    }
    return (__bridge_retained void *)state;
}
