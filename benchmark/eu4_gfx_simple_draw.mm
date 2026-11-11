#import "eu4_gfx_simple_draw.h"

#import "eu4_gfx_buffers.h"
#import "eu4_gfx_shader.h"
#import "eu4_gfx_texture.h"

#import <Metal/Metal.h>
#import <string.h>

/* Sampler contract from the Simple effect asset (asset-maincode-v0):
   AddressU/V = Wrap, Min/Mag/Mip = Linear. The engine sampler object is not
   decoded yet, so the asset is the only admitted source. */
static id<MTLSamplerState> simple_sampler(id<MTLDevice> device) {
    static id<MTLSamplerState> cached;
    static void *cached_device;
    if (cached != nil && cached_device == (__bridge void *)device) {
        return cached;
    }
    MTLSamplerDescriptor *desc = [MTLSamplerDescriptor new];
    desc.minFilter = MTLSamplerMinMagFilterLinear;
    desc.magFilter = MTLSamplerMinMagFilterLinear;
    desc.mipFilter = MTLSamplerMipFilterLinear;
    desc.sAddressMode = MTLSamplerAddressModeRepeat;
    desc.tAddressMode = MTLSamplerAddressModeRepeat;
    desc.rAddressMode = MTLSamplerAddressModeRepeat;
    cached = [device newSamplerStateWithDescriptor:desc];
    cached_device = (__bridge void *)device;
    return cached;
}

const char *eu4_gfx_simple_draw_status_name(eu4_gfx_simple_draw_status status) {
    switch (status) {
    case EU4_GFX_SIMPLE_DRAW_OK:
        return "ok";
    case EU4_GFX_SIMPLE_DRAW_CONTEXT:
        return "context";
    case EU4_GFX_SIMPLE_DRAW_SHADER:
        return "shader";
    case EU4_GFX_SIMPLE_DRAW_PRIMITIVE:
        return "primitive";
    case EU4_GFX_SIMPLE_DRAW_COUNT:
        return "count";
    case EU4_GFX_SIMPLE_DRAW_VERTEX:
        return "vertex";
    case EU4_GFX_SIMPLE_DRAW_CONSTANT:
        return "constant";
    case EU4_GFX_SIMPLE_DRAW_TEXTURE:
        return "texture";
    case EU4_GFX_SIMPLE_DRAW_TARGET:
        return "target";
    case EU4_GFX_SIMPLE_DRAW_PIPELINE:
        return "pipeline";
    case EU4_GFX_SIMPLE_DRAW_ENCODE:
        return "encode";
    }
    return "unknown";
}

static void *load_ptr(const uint8_t *base, size_t offset) {
    void *value = NULL;
    memcpy(&value, base + offset, sizeof value);
    return value;
}

static uint32_t load_u32(const uint8_t *base, size_t offset) {
    uint32_t value = 0;
    memcpy(&value, base + offset, sizeof value);
    return value;
}

static eu4_gfx_simple_draw_status refuse(eu4_gfx_simple_draw_status status, const char **reason,
                                         const char *why) {
    if (reason != NULL) {
        *reason = why;
    }
    return status;
}

eu4_gfx_simple_draw_status eu4_gfx_simple_draw(const void *immediate_context, int32_t count,
                                               int32_t first,
                                               const eu4_gfx_simple_draw_inputs *inputs,
                                               const char **reason) {
    if (reason != NULL) {
        *reason = NULL;
    }
    if (immediate_context == NULL || inputs == NULL) {
        return refuse(EU4_GFX_SIMPLE_DRAW_CONTEXT, reason, "null context or inputs");
    }
    const uint8_t *master = (const uint8_t *)load_ptr((const uint8_t *)immediate_context, 0);
    if (master == NULL) {
        return refuse(EU4_GFX_SIMPLE_DRAW_CONTEXT, reason, "null master");
    }

    void *shader = load_ptr(master, EU4_GFX_MASTER_SHADER);
    if (shader == NULL || !eu4_gfx_shader_is_linked(shader) ||
        eu4_gfx_shader_identity(shader) == NULL) {
        return refuse(EU4_GFX_SIMPLE_DRAW_SHADER, reason, "no linked Simple shader");
    }

    if ((int32_t)load_u32(master, EU4_GFX_MASTER_MODE) != 5) {
        return refuse(EU4_GFX_SIMPLE_DRAW_PRIMITIVE, reason,
                      "mode is not GL_TRIANGLE_STRIP");
    }
    if (count < 1) {
        count = (int32_t)load_u32(master, EU4_GFX_MASTER_COUNT);
    }
    if (count < 3) {
        return refuse(EU4_GFX_SIMPLE_DRAW_COUNT, reason, "effective count below 3");
    }

    void *vertex_header = load_ptr(master, EU4_GFX_MASTER_VERTEX_HEADER);
    uint32_t vertex_offset = load_u32(master, EU4_GFX_MASTER_VERTEX_OFFSET);
    uint32_t vertex_stride = load_u32(master, EU4_GFX_MASTER_VERTEX_STRIDE);
    if (vertex_header == NULL) {
        return refuse(EU4_GFX_SIMPLE_DRAW_VERTEX, reason, "no vertex buffer in slot 0");
    }
    if (vertex_stride != EU4_GFX_SIMPLE_VERTEX_STRIDE) {
        return refuse(EU4_GFX_SIMPLE_DRAW_VERTEX, reason,
                      "vertex stride does not match the admitted Simple layout");
    }
    void *vertex_buffer = eu4_gfx_vertex_buffer_for_header(vertex_header);
    if (vertex_buffer == NULL) {
        return refuse(EU4_GFX_SIMPLE_DRAW_VERTEX, reason, "slot 0 has no Metal buffer");
    }
    size_t vertex_length = eu4_gfx_vertex_buffer_length(vertex_header);
    uint64_t needed = (uint64_t)vertex_offset + (uint64_t)count * vertex_stride;
    if (needed > vertex_length) {
        return refuse(EU4_GFX_SIMPLE_DRAW_VERTEX, reason,
                      "vertex range exceeds the buffer length");
    }

    const uint8_t *constant = (const uint8_t *)load_ptr(master, EU4_GFX_MASTER_CONSTANT_SLOT2);
    if (constant == NULL) {
        return refuse(EU4_GFX_SIMPLE_DRAW_CONSTANT, reason, "constant slot 2 is unset");
    }
    uint32_t constant_words = load_u32(constant, 0);
    const void *constant_data = load_ptr(constant, 8);
    if (constant_data == NULL || constant_words < 16) {
        return refuse(EU4_GFX_SIMPLE_DRAW_CONSTANT, reason,
                      "constant slot 2 does not hold a 4x4 matrix");
    }

    void *texture_binding =
        inputs->texture != NULL ? inputs->texture : eu4_gfx_bound_texture();
    if (texture_binding == NULL) {
        return refuse(EU4_GFX_SIMPLE_DRAW_TEXTURE, reason, "no SimpleTexture image");
    }
    if (inputs->device == NULL || inputs->target == NULL || inputs->target_width == 0 ||
        inputs->target_height == 0 || inputs->viewport_width == 0 ||
        inputs->viewport_height == 0 ||
        (uint64_t)inputs->viewport_x + inputs->viewport_width > inputs->target_width ||
        (uint64_t)inputs->viewport_y + inputs->viewport_height > inputs->target_height) {
        return refuse(EU4_GFX_SIMPLE_DRAW_TARGET, reason, "target or viewport invalid");
    }

    id<MTLDevice> device = (__bridge id<MTLDevice>)inputs->device;
    id<MTLTexture> target = (__bridge id<MTLTexture>)inputs->target;
    id<MTLTexture> texture = (__bridge id<MTLTexture>)texture_binding;
    if (target.pixelFormat != MTLPixelFormatBGRA8Unorm) {
        return refuse(EU4_GFX_SIMPLE_DRAW_TARGET, reason, "target is not BGRA8Unorm");
    }

    EU4GfxPipelineKey key = {};
    key.mtl_device = inputs->device;
    key.pixel_format = (unsigned)MTLPixelFormatBGRA8Unorm;
    key.sample_count = 1;
    key.color_write_mask = (unsigned)MTLColorWriteMaskAll;
    key.blend_enabled = 1; /* SRC_ALPHA / ONE_MINUS_SRC_ALPHA from simple.shader */
    void *pipeline_raw = eu4_gfx_shader_pipeline_for_key(shader, inputs->device, &key);
    if (pipeline_raw == NULL) {
        return refuse(EU4_GFX_SIMPLE_DRAW_PIPELINE, reason,
                      "Simple pipeline state could not be created");
    }
    id<MTLRenderPipelineState> pipeline = (__bridge id<MTLRenderPipelineState>)pipeline_raw;

    id<MTLSamplerState> sampler = simple_sampler(device);
    if (sampler == nil) {
        return refuse(EU4_GFX_SIMPLE_DRAW_PIPELINE, reason, "sampler state could not be created");
    }

    /* Matrix bytes are copied before encoding: the engine buffer is caller-owned
       and may be rewritten by the next UpdateConstantBuffer. */
    uint8_t matrix[64];
    memcpy(matrix, constant_data, sizeof matrix);
    id<MTLBuffer> uniforms = [device newBufferWithBytes:matrix
                                                 length:sizeof matrix
                                                options:MTLResourceStorageModeShared];

    id<MTLCommandQueue> queue = [device newCommandQueue];
    if (uniforms == nil || queue == nil) {
        return refuse(EU4_GFX_SIMPLE_DRAW_ENCODE, reason, "Metal allocation failed");
    }

    MTLRenderPassDescriptor *pass = [MTLRenderPassDescriptor renderPassDescriptor];
    pass.colorAttachments[0].texture = target;
    pass.colorAttachments[0].loadAction =
        inputs->clear_color != NULL ? MTLLoadActionClear : MTLLoadActionLoad;
    pass.colorAttachments[0].storeAction = MTLStoreActionStore;
    if (inputs->clear_color != NULL) {
        pass.colorAttachments[0].clearColor =
            MTLClearColorMake(inputs->clear_color[0], inputs->clear_color[1],
                              inputs->clear_color[2], inputs->clear_color[3]);
    }

    id<MTLCommandBuffer> command = [queue commandBuffer];
    id<MTLRenderCommandEncoder> encoder = [command renderCommandEncoderWithDescriptor:pass];
    if (command == nil || encoder == nil) {
        return refuse(EU4_GFX_SIMPLE_DRAW_ENCODE, reason, "command encoding failed");
    }
    [encoder setRenderPipelineState:pipeline];
    [encoder setViewport:(MTLViewport){(double)inputs->viewport_x, (double)inputs->viewport_y,
                                       (double)inputs->viewport_width,
                                       (double)inputs->viewport_height, 0.0, 1.0}];
    [encoder setVertexBuffer:(__bridge id<MTLBuffer>)vertex_buffer
                      offset:(NSUInteger)vertex_offset
                     atIndex:0];
    [encoder setVertexBuffer:uniforms offset:0 atIndex:1];
    [encoder setFragmentTexture:texture atIndex:0];
    [encoder setFragmentSamplerState:sampler atIndex:0];
    [encoder drawPrimitives:MTLPrimitiveTypeTriangleStrip
                vertexStart:(NSUInteger)first
                vertexCount:(NSUInteger)count];
    [encoder endEncoding];
    [command commit];
    [command waitUntilCompleted];
    if (command.status != MTLCommandBufferStatusCompleted) {
        return refuse(EU4_GFX_SIMPLE_DRAW_ENCODE, reason, "command buffer did not complete");
    }
    return EU4_GFX_SIMPLE_DRAW_OK;
}
