#import "eu4_gfx_sprite_draw.h"

#import "eu4_gfx_buffers.h"
#import "eu4_gfx_init.h"
#import "eu4_gfx_shader.h"
#import "eu4_gfx_texture.h"

#import <Metal/Metal.h>
#import <string.h>

/* Sampler contract for the loading-screen sprite. The engine sampler object is
   not decoded yet; the transparent sprite is sampled with linear filtering and
   clamp-to-edge, which is the observed GL texture state. A mismatched sampler
   is a declared limitation, not a silently substituted resource. */
static id<MTLSamplerState> sprite_sampler(id<MTLDevice> device) {
    static id<MTLSamplerState> cached;
    static id<MTLDevice> cached_device;
    if (cached != nil && cached_device == device) {
        return cached;
    }
    MTLSamplerDescriptor *desc = [MTLSamplerDescriptor new];
    desc.minFilter = MTLSamplerMinMagFilterLinear;
    desc.magFilter = MTLSamplerMinMagFilterLinear;
    desc.mipFilter = MTLSamplerMipFilterNearest;
    desc.sAddressMode = MTLSamplerAddressModeClampToEdge;
    desc.tAddressMode = MTLSamplerAddressModeClampToEdge;
    desc.rAddressMode = MTLSamplerAddressModeClampToEdge;
    id<MTLSamplerState> sampler = [device newSamplerStateWithDescriptor:desc];
    if (sampler != nil) {
        cached = sampler;
        cached_device = device;
    }
    return sampler;
}

const char *eu4_gfx_sprite_draw_status_name(eu4_gfx_sprite_draw_status status) {
    switch (status) {
    case EU4_GFX_SPRITE_DRAW_OK:
        return "ok";
    case EU4_GFX_SPRITE_DRAW_CONTEXT:
        return "context";
    case EU4_GFX_SPRITE_DRAW_SHADER:
        return "shader";
    case EU4_GFX_SPRITE_DRAW_PRIMITIVE:
        return "primitive";
    case EU4_GFX_SPRITE_DRAW_COUNT:
        return "count";
    case EU4_GFX_SPRITE_DRAW_VERTEX:
        return "vertex";
    case EU4_GFX_SPRITE_DRAW_CONSTANT:
        return "constant";
    case EU4_GFX_SPRITE_DRAW_TEXTURE:
        return "texture";
    case EU4_GFX_SPRITE_DRAW_TARGET:
        return "target";
    case EU4_GFX_SPRITE_DRAW_PIPELINE:
        return "pipeline";
    case EU4_GFX_SPRITE_DRAW_ENCODE:
        return "encode";
    case EU4_GFX_SPRITE_DRAW_STATE:
        return "state";
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

static eu4_gfx_sprite_draw_status refuse(eu4_gfx_sprite_draw_status status,
                                          const char **reason, const char *why) {
    if (reason != NULL) {
        *reason = why;
    }
    return status;
}

static void hex_bytes(const uint8_t *bytes, size_t length, char *out) {
    static const char digits[] = "0123456789abcdef";
    for (size_t i = 0; i < length; i++) {
        out[i * 2] = digits[bytes[i] >> 4];
        out[i * 2 + 1] = digits[bytes[i] & 0xf];
    }
    out[length * 2] = 0;
}

static uint64_t fnv1a(const uint8_t *bytes, size_t length) {
    uint64_t hash = 14695981039346656037ULL;
    for (size_t i = 0; i < length; i++) {
        hash ^= bytes[i];
        hash *= 1099511628211ULL;
    }
    return hash;
}

static void copy_identity(char *dest, size_t capacity, const char *source) {
    if (capacity == 0) {
        return;
    }
    snprintf(dest, capacity, "%s", source != NULL ? source : "");
}

eu4_gfx_sprite_draw_status eu4_gfx_sprite_draw_encode_into(
    const void *immediate_context, int32_t count, int32_t first,
    const eu4_gfx_simple_draw_inputs *inputs, void *command_buffer,
    const char **reason, eu4_gfx_simple_draw_state *state) {
    if (reason != NULL) {
        *reason = NULL;
    }
    if (state != NULL) {
        memset(state, 0, sizeof *state);
    }
    if (immediate_context == NULL || inputs == NULL || command_buffer == NULL) {
        return refuse(EU4_GFX_SPRITE_DRAW_CONTEXT, reason,
                      "null context, inputs or command buffer");
    }
    id<MTLCommandBuffer> command = (__bridge id<MTLCommandBuffer>)command_buffer;
    if (command == nil) {
        return refuse(EU4_GFX_SPRITE_DRAW_CONTEXT, reason, "null command buffer");
    }
    const uint8_t *master = (const uint8_t *)load_ptr((const uint8_t *)immediate_context, 0);
    if (master == NULL) {
        return refuse(EU4_GFX_SPRITE_DRAW_CONTEXT, reason, "null master");
    }

    void *shader = load_ptr(master, EU4_GFX_MASTER_SHADER);
    const char *identity = shader != NULL ? eu4_gfx_shader_identity(shader) : NULL;
    if (shader == NULL || !eu4_gfx_shader_is_linked(shader) || identity == NULL ||
        strcmp(identity, EU4_GFX_SHADER_SPRITE_IDENTITY) != 0) {
        return refuse(EU4_GFX_SPRITE_DRAW_SHADER, reason,
                      "current shader is not the admitted sprite effect");
    }

    if ((int32_t)load_u32(master, EU4_GFX_MASTER_MODE) != 5) {
        return refuse(EU4_GFX_SPRITE_DRAW_PRIMITIVE, reason,
                      "mode is not GL_TRIANGLE_STRIP");
    }
    if (count < 1) {
        count = (int32_t)load_u32(master, EU4_GFX_MASTER_COUNT);
    }
    if (count < 3) {
        return refuse(EU4_GFX_SPRITE_DRAW_COUNT, reason, "effective count below 3");
    }

    void *vertex_header = load_ptr(master, EU4_GFX_MASTER_VERTEX_HEADER);
    uint32_t vertex_offset = load_u32(master, EU4_GFX_MASTER_VERTEX_OFFSET);
    uint32_t vertex_stride = load_u32(master, EU4_GFX_MASTER_VERTEX_STRIDE);
    if (vertex_header == NULL) {
        return refuse(EU4_GFX_SPRITE_DRAW_VERTEX, reason, "no vertex buffer in slot 0");
    }
    if (vertex_stride != EU4_GFX_SPRITE_VERTEX_STRIDE) {
        return refuse(EU4_GFX_SPRITE_DRAW_VERTEX, reason,
                      "vertex stride does not match the admitted sprite layout");
    }
    void *vertex_buffer = eu4_gfx_vertex_buffer_for_header(vertex_header);
    if (vertex_buffer == NULL) {
        return refuse(EU4_GFX_SPRITE_DRAW_VERTEX, reason, "slot 0 has no Metal buffer");
    }
    size_t vertex_length = eu4_gfx_vertex_buffer_length(vertex_header);
    if (first < 0) {
        return refuse(EU4_GFX_SPRITE_DRAW_VERTEX, reason, "negative first vertex");
    }
    uint64_t start = (uint64_t)(uint32_t)first;
    uint64_t vertices = (uint64_t)(uint32_t)count;
    uint64_t stride = (uint64_t)vertex_stride;
    if (start > UINT64_MAX - vertices) {
        return refuse(EU4_GFX_SPRITE_DRAW_VERTEX, reason, "vertex range overflows");
    }
    uint64_t end = start + vertices;
    if (end > (UINT64_MAX - (uint64_t)vertex_offset) / stride) {
        return refuse(EU4_GFX_SPRITE_DRAW_VERTEX, reason, "vertex range overflows");
    }
    uint64_t needed = (uint64_t)vertex_offset + end * stride;
    if (needed > (uint64_t)vertex_length) {
        return refuse(EU4_GFX_SPRITE_DRAW_VERTEX, reason,
                      "vertex range exceeds the buffer length");
    }

    const uint8_t *constant = (const uint8_t *)load_ptr(master, EU4_GFX_MASTER_CONSTANT_SLOT2);
    if (constant == NULL) {
        return refuse(EU4_GFX_SPRITE_DRAW_CONSTANT, reason, "constant slot 2 is unset");
    }
    uint32_t constant_words = load_u32(constant, 0);
    const void *constant_data = load_ptr(constant, 8);
    if (constant_data == NULL || constant_words < 6) {
        return refuse(EU4_GFX_SPRITE_DRAW_CONSTANT, reason,
                      "constant slot 2 does not hold the six sprite uniforms");
    }

    void *texture_binding =
        inputs->texture != NULL ? inputs->texture : eu4_gfx_bound_texture();
    if (texture_binding == NULL) {
        return refuse(EU4_GFX_SPRITE_DRAW_TEXTURE, reason, "no MapTexture image");
    }
    if (inputs->device == NULL || inputs->target == NULL || inputs->target_width == 0 ||
        inputs->target_height == 0 || inputs->viewport_width == 0 ||
        inputs->viewport_height == 0) {
        return refuse(EU4_GFX_SPRITE_DRAW_TARGET, reason, "target or viewport invalid");
    }
    uint32_t logical_width =
        inputs->logical_width != 0 ? inputs->logical_width : inputs->target_width;
    uint32_t logical_height =
        inputs->logical_height != 0 ? inputs->logical_height : inputs->target_height;
    if ((uint64_t)inputs->viewport_x + inputs->viewport_width > logical_width ||
        (uint64_t)inputs->viewport_y + inputs->viewport_height > logical_height) {
        return refuse(EU4_GFX_SPRITE_DRAW_TARGET, reason,
                      "viewport exceeds the logical engine dimensions");
    }

    id<MTLDevice> device = (__bridge id<MTLDevice>)inputs->device;
    id<MTLTexture> target = (__bridge id<MTLTexture>)inputs->target;
    id<MTLTexture> texture = (__bridge id<MTLTexture>)texture_binding;
    if (target.pixelFormat != MTLPixelFormatBGRA8Unorm) {
        return refuse(EU4_GFX_SPRITE_DRAW_TARGET, reason, "target is not BGRA8Unorm");
    }
    if (target.textureType != MTLTextureType2D || target.sampleCount != 1 ||
        target.width != inputs->target_width || target.height != inputs->target_height ||
        (target.usage & MTLTextureUsageRenderTarget) == 0) {
        return refuse(EU4_GFX_SPRITE_DRAW_TARGET, reason,
                      "target is not a single-sample 2D render target of the declared size");
    }
    if (target.device != device) {
        return refuse(EU4_GFX_SPRITE_DRAW_TARGET, reason, "target belongs to another device");
    }
    if (texture.textureType != MTLTextureType2D || texture.sampleCount != 1 ||
        (texture.usage & MTLTextureUsageShaderRead) == 0) {
        return refuse(EU4_GFX_SPRITE_DRAW_TEXTURE, reason,
                      "texture is not a single-sample sampleable 2D texture");
    }
    if (texture.device != device || texture == target) {
        return refuse(EU4_GFX_SPRITE_DRAW_TEXTURE, reason,
                      "texture is on another device or aliases the render target");
    }

    static _Thread_local char state_reason[160];
    if (!eu4_gfx_sprite_state_supported((void *)master, state_reason,
                                        sizeof state_reason)) {
        return refuse(EU4_GFX_SPRITE_DRAW_STATE, reason, state_reason);
    }

    EU4GfxPipelineKey key = {};
    key.mtl_device = inputs->device;
    key.pixel_format = (unsigned)MTLPixelFormatBGRA8Unorm;
    key.sample_count = 1;
    key.color_write_mask = (unsigned)MTLColorWriteMaskAll;
    key.blend_enabled = 1;
    key.vertex_layout = EU4_GFX_VERTEX_LAYOUT_SPRITE_MAP;
    void *pipeline_raw = eu4_gfx_shader_pipeline_for_key(shader, inputs->device, &key);
    if (pipeline_raw == NULL) {
        return refuse(EU4_GFX_SPRITE_DRAW_PIPELINE, reason,
                      "sprite pipeline state could not be created");
    }
    id<MTLRenderPipelineState> pipeline = (__bridge id<MTLRenderPipelineState>)pipeline_raw;

    id<MTLSamplerState> sampler = sprite_sampler(device);
    if (sampler == nil) {
        return refuse(EU4_GFX_SPRITE_DRAW_PIPELINE, reason,
                      "sampler state could not be created");
    }

    uint8_t uniform[EU4_GFX_SPRITE_CONSTANT_BYTES];
    memcpy(uniform, constant_data, sizeof uniform);

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

    id<MTLRenderCommandEncoder> encoder = [command renderCommandEncoderWithDescriptor:pass];
    if (encoder == nil) {
        return refuse(EU4_GFX_SPRITE_DRAW_ENCODE, reason, "command encoding failed");
    }
    [encoder setRenderPipelineState:pipeline];
    /* The admitted raster snapshot is the backend default; the reconstructed
       pass has no depth attachment. The quad's two strip triangles carry the
       same winding, so disabling culling matches the original pass. */
    [encoder setCullMode:MTLCullModeNone];
    double scale_x = (double)inputs->target_width / (double)logical_width;
    double scale_y = (double)inputs->target_height / (double)logical_height;
    double viewport_x = (double)inputs->viewport_x * scale_x;
    double viewport_y =
        ((double)logical_height - ((double)inputs->viewport_y +
                                   (double)inputs->viewport_height)) *
        scale_y;
    double viewport_width = (double)inputs->viewport_width * scale_x;
    double viewport_height = (double)inputs->viewport_height * scale_y;
    [encoder setViewport:(MTLViewport){viewport_x, viewport_y, viewport_width,
                                       viewport_height, 0.0, 1.0}];
    [encoder setVertexBytes:uniform length:sizeof uniform atIndex:1];
    [encoder setFragmentBytes:uniform length:sizeof uniform atIndex:1];
    [encoder setVertexBuffer:(__bridge id<MTLBuffer>)vertex_buffer
                      offset:(NSUInteger)vertex_offset
                     atIndex:0];
    [encoder setFragmentTexture:texture atIndex:0];
    [encoder setFragmentSamplerState:sampler atIndex:0];
    [encoder drawPrimitives:MTLPrimitiveTypeTriangleStrip
                vertexStart:(NSUInteger)first
                vertexCount:(NSUInteger)count];
    [encoder endEncoding];

    if (state != NULL) {
        copy_identity(state->shader_identity, sizeof state->shader_identity, identity);
        state->primitive = 5;
        state->count = count;
        state->first = first;
        state->vertex_stride = vertex_stride;
        state->vertex_offset = vertex_offset;
        state->vertex_bytes = (uint64_t)vertex_length;
        id<MTLBuffer> vertex_mtl = (__bridge id<MTLBuffer>)vertex_buffer;
        const uint8_t *vertex_contents = (const uint8_t *)[vertex_mtl contents];
        if (vertex_contents != NULL) {
            size_t available = vertex_length > vertex_offset
                                   ? (size_t)(vertex_length - vertex_offset)
                                   : 0;
            size_t sample = available < 96 ? available : 96;
            if (sample > 0) {
                const uint8_t *begin = vertex_contents + vertex_offset;
                state->vertex_hash = fnv1a(begin, sample);
                hex_bytes(begin, sample, state->vertex_hex);
            }
        }
        state->constant_hash = fnv1a(uniform, sizeof uniform);
        hex_bytes(uniform, sizeof uniform, state->constant_hex);
        state->texture_pointer = (uint64_t)(uintptr_t)texture_binding;
        state->texture_width = (uint32_t)texture.width;
        state->texture_height = (uint32_t)texture.height;
        state->target_pointer = (uint64_t)(uintptr_t)inputs->target;
        state->target_width = inputs->target_width;
        state->target_height = inputs->target_height;
        state->logical_width = logical_width;
        state->logical_height = logical_height;
        state->viewport_x = inputs->viewport_x;
        state->viewport_y = inputs->viewport_y;
        state->viewport_width = inputs->viewport_width;
        state->viewport_height = inputs->viewport_height;
        state->blend_supported = 1;
        state->depth_default = eu4_gfx_depth_snapshot_is_default((void *)master);
        state->raster_default = 1;
        state->scissor_full = 1;
    }
    return EU4_GFX_SPRITE_DRAW_OK;
}
