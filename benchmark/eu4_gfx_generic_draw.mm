#import "eu4_gfx_generic_draw.h"

#import "eu4_gfx_buffers.h"
#import "eu4_gfx_init.h"
#import "eu4_gfx_shader.h"
#import "eu4_gfx_texture.h"
#import "eu4_metal_events.h"

#import <Metal/Metal.h>
#import <Foundation/Foundation.h>
#import <string.h>
#import <pthread.h>

/* Generic live encoder for cached shaders. Game-independent: it reads engine
 * state, never caller RVAs, and has no scene-specific branches. */

static uint64_t g_generic_draws;

uint64_t eu4_gfx_generic_draw_count(void) { return g_generic_draws; }

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

static float load_f32(const uint8_t *base, size_t offset) {
    float value = 0;
    memcpy(&value, base + offset, sizeof value);
    return value;
}

/* Cache JSON may carry null for absent optional fields; NSNull does not
   respond to unsignedIntegerValue. */
static NSUInteger num(id value, NSUInteger fallback) {
    if ([value isKindOfClass:[NSNumber class]]) {
        return [(NSNumber *)value unsignedIntegerValue];
    }
    return fallback;
}

static const char *fail(char *reason, size_t capacity, const char *text) {
    if (reason != NULL && capacity > 0) {
        snprintf(reason, capacity, "%s", text);
    }
    return text;
}

static MTLVertexFormat vertex_format(uint32_t type, uint32_t size,
                                     int normalized) {
    switch (type) {
        case 0x1406: /* GL_FLOAT */
            switch (size) {
                case 1: return MTLVertexFormatFloat;
                case 2: return MTLVertexFormatFloat2;
                case 3: return MTLVertexFormatFloat3;
                case 4: return MTLVertexFormatFloat4;
            }
            break;
        case 0x1401: /* GL_UNSIGNED_BYTE */
            switch (size) {
                case 1: return normalized ? MTLVertexFormatUCharNormalized
                                          : MTLVertexFormatUChar;
                case 2: return normalized ? MTLVertexFormatUChar2Normalized
                                          : MTLVertexFormatUChar2;
                case 3: return normalized ? MTLVertexFormatUChar3Normalized
                                          : MTLVertexFormatUChar3;
                case 4: return normalized ? MTLVertexFormatUChar4Normalized
                                          : MTLVertexFormatUChar4;
            }
            break;
        case 0x1402: /* GL_SHORT */
            switch (size) {
                case 2: return normalized ? MTLVertexFormatShort2Normalized
                                          : MTLVertexFormatShort2;
                case 4: return normalized ? MTLVertexFormatShort4Normalized
                                          : MTLVertexFormatShort4;
            }
            break;
        case 0x1403: /* GL_UNSIGNED_SHORT */
            switch (size) {
                case 2: return normalized ? MTLVertexFormatUShort2Normalized
                                          : MTLVertexFormatUShort2;
                case 4: return normalized ? MTLVertexFormatUShort4Normalized
                                          : MTLVertexFormatUShort4;
            }
            break;
    }
    return (MTLVertexFormat)0;
}

static MTLBlendFactor blend_factor(uint32_t gl) {
    switch (gl) {
        case 0x0000: return MTLBlendFactorZero;
        case 0x0001: return MTLBlendFactorOne;
        case 0x0300: return MTLBlendFactorSourceColor;
        case 0x0301: return MTLBlendFactorOneMinusSourceColor;
        case 0x0302: return MTLBlendFactorSourceAlpha;
        case 0x0303: return MTLBlendFactorOneMinusSourceAlpha;
        case 0x0304: return MTLBlendFactorDestinationAlpha;
        case 0x0305: return MTLBlendFactorOneMinusDestinationAlpha;
        case 0x0306: return MTLBlendFactorDestinationColor;
        case 0x0307: return MTLBlendFactorOneMinusDestinationColor;
        case 0x0308: return MTLBlendFactorSourceAlphaSaturated;
        default: return MTLBlendFactorOne;
    }
}

static MTLBlendOperation blend_operation(uint32_t gl) {
    switch (gl) {
        case 0x8006: return MTLBlendOperationAdd;
        case 0x800A: return MTLBlendOperationSubtract;
        case 0x800B: return MTLBlendOperationReverseSubtract;
        case 0x8007: return MTLBlendOperationMin;
        case 0x8008: return MTLBlendOperationMax;
        default: return MTLBlendOperationAdd;
    }
}

static MTLCompareFunction compare_function(uint32_t gl) {
    switch (gl) {
        case 0x0200: return MTLCompareFunctionNever;
        case 0x0201: return MTLCompareFunctionLess;
        case 0x0202: return MTLCompareFunctionEqual;
        case 0x0203: return MTLCompareFunctionLessEqual;
        case 0x0204: return MTLCompareFunctionGreater;
        case 0x0205: return MTLCompareFunctionNotEqual;
        case 0x0206: return MTLCompareFunctionGreaterEqual;
        case 0x0207: return MTLCompareFunctionAlways;
        default: return MTLCompareFunctionAlways;
    }
}

static id<MTLSamplerState> generic_sampler(id<MTLDevice> device,
                                           NSString *key, BOOL nearest) {
    static NSMutableDictionary *cache;
    static id<MTLDevice> cached_device;
    if (cache == nil || cached_device != device) {
        cache = [NSMutableDictionary dictionary];
        cached_device = device;
    }
    id<MTLSamplerState> sampler = cache[key];
    if (sampler != nil) {
        return sampler;
    }
    MTLSamplerDescriptor *descriptor = [MTLSamplerDescriptor new];
    descriptor.minFilter = nearest ? MTLSamplerMinMagFilterNearest
                                   : MTLSamplerMinMagFilterLinear;
    descriptor.magFilter = nearest ? MTLSamplerMinMagFilterNearest
                                   : MTLSamplerMinMagFilterLinear;
    descriptor.mipFilter = MTLSamplerMipFilterLinear;
    descriptor.sAddressMode = MTLSamplerAddressModeClampToEdge;
    descriptor.tAddressMode = MTLSamplerAddressModeClampToEdge;
    descriptor.rAddressMode = MTLSamplerAddressModeClampToEdge;
    sampler = [device newSamplerStateWithDescriptor:descriptor];
    if (sampler != nil) {
        cache[key] = sampler;
    }
    return sampler;
}

/* Packs one stage's std140 uniform block from the engine constant slots. */
static NSData *pack_stage_uniforms(NSDictionary *stage, const uint8_t *master,
                                   char *reason, size_t capacity) {
    NSArray *members = stage[@"uniforms"];
    if (![members isKindOfClass:[NSArray class]] || members.count == 0) {
        return [NSData data];
    }
    NSUInteger size = 0;
    for (NSDictionary *member in members) {
        NSUInteger components = num(member[@"components"], 0);
        NSUInteger elements = MAX((NSUInteger)1, num(member[@"elements"], 0));
        NSUInteger end = num(member[@"offset"], 0) + components * elements * 4;
        size = MAX(size, end);
    }
    size = (size + 15) / 16 * 16;
    if (size == 0) {
        size = 16;
    }
    NSMutableData *data = [NSMutableData dataWithLength:size];
    for (NSDictionary *member in members) {
        NSString *name = member[@"name"];
        NSRange prefix = [name rangeOfString:@"UNIFORM"];
        if (prefix.location != 0 || name.length <= prefix.length) {
            fail(reason, capacity, "uniform block member is not an engine constant array");
            return nil;
        }
        NSString *index_text = [name substringFromIndex:prefix.length];
        NSUInteger slot = (NSUInteger)index_text.integerValue;
        if (slot >= 6) {
            fail(reason, capacity, "constant slot out of range");
            return nil;
        }
        /* The engine's constant-slot records are 16 bytes apart: the original
           draw bodies index them with a byte offset `rbx` that advances by 4
           and address `0xc8 + rbx*4` / `0xd0 + rbx*4` (= 16*slot). */
        uint32_t count = load_u32(master, 0xc8 + slot * 16);
        void *header = load_ptr(master, 0xd0 + slot * 16);
        if (count == 0 || header == NULL) {
            continue;
        }
        const void *values = load_ptr((const uint8_t *)header, 8);
        if (values == NULL) {
            continue;
        }
        NSUInteger components = num(member[@"components"], 0);
        NSUInteger elements = MAX((NSUInteger)1, num(member[@"elements"], 0));
        NSUInteger member_bytes = components * elements * 4;
        NSUInteger available = (NSUInteger)count * 16;
        NSUInteger copy = MIN(member_bytes, available);
        NSUInteger member_offset = num(member[@"offset"], 0);
        if (member_offset >= data.length) {
            continue;
        }
        if (copy > data.length - member_offset) {
            copy = data.length - member_offset;
        }
        if (getenv("EU4_GFX_UNIFORM_TRACE") != NULL) {
            char body[768];
            int n = snprintf(
                body, sizeof body,
                "{\"schema\":\"gfx-uniform-pack/v1\","
                "\"stage\":\"%s\",\"slot\":%u,\"count\":%u,"
                "\"header\":\"%p\",\"values\":\"%p\","
                "\"member_offset\":%lu,\"copy\":%lu,\"master\":\"%p\","
                "\"pid\":%d}\n",
                [[stage[@"msl_file"] description] UTF8String] ?: "",
                slot, count, header, values, (unsigned long)member_offset,
                (unsigned long)copy, master, (int)getpid());
            if (n > 0 && (size_t)n < sizeof body) {
                (void)eu4_metal_events_write("uniform-pack.json", body);
            }
        }
        memcpy((uint8_t *)data.mutableBytes + member_offset, values, copy);
    }
    return data;
}

/* Collects the engine vertex elements and builds a Metal vertex descriptor. */
static BOOL build_vertex_descriptor(void *shader, const uint8_t *master,
                                    id<MTLDevice> device,
                                    MTLVertexDescriptor *descriptor,
                                    NSMutableDictionary *buffers,
                                    NSDictionary *stage,
                                    int instanced_draw,
                                    char *reason, size_t capacity) {
    void *elements = load_ptr((const uint8_t *)shader, 0x10);
    int32_t count = (int32_t)load_u32((const uint8_t *)shader, 0x18);
    if (elements == NULL || count <= 0 || count > 64) {
        fail(reason, capacity, "shader has no vertex element table");
        return NO;
    }
    NSDictionary *locations = stage[@"attribute_locations"];
    NSMutableSet *declared = [NSMutableSet set];
    NSMutableSet *covered = [NSMutableSet set];
    /* Metal has 31 vertex buffer slots and one is the uniform block. Assign
       compact indices instead of 8+location: locations can reach 30 (the
       translator's collision-free range), which would exceed the table. */
    NSUInteger next_buffer = 8;
    if ([locations isKindOfClass:[NSDictionary class]]) {
        for (NSString *name in locations) {
            [declared addObject:locations[name]];
        }
    }
    for (int32_t i = 0; i < count; i++) {
        const uint8_t *element = (const uint8_t *)elements + (size_t)i * 0x28;
        uint32_t location = 0, size = 0, type = 0, offset = 0;
        int32_t slot = 0;
        uint8_t divisor = 0, normalized = 0;
        memcpy(&location, element + 0xc, 4);
        memcpy(&size, element + 0x10, 4);
        memcpy(&type, element + 0x14, 4);
        memcpy(&offset, element + 0x18, 4);
        memcpy(&slot, element + 0x1c, 4);
        divisor = element[0x20];
        normalized = element[0x21];
        if (location >= 31 || slot < 0 || slot >= EU4_GFX_VERTEX_SLOTS) {
            fail(reason, capacity, "vertex element location or slot out of range");
            return NO;
        }
        if ([declared count] > 0 && ![declared containsObject:@(location)]) {
            /* The engine layout can carry attributes the linked program does
               not consume; they are not part of the Metal descriptor. */
            continue;
        }
        void *header = load_ptr(master, 0x130 + (size_t)slot * 8);
        uint32_t slot_offset = load_u32(master, 0x150 + (size_t)slot * 4);
        uint32_t stride = load_u32(master, 0x160 + (size_t)slot * 4);
        if (header == NULL || stride == 0) {
            fail(reason, capacity, "vertex buffer slot is incomplete");
            return NO;
        }
        void *metal_buffer = eu4_gfx_vertex_buffer_for_header(header);
        if (metal_buffer == NULL) {
            fail(reason, capacity, "vertex buffer has no Metal storage");
            return NO;
        }
        MTLVertexFormat format = vertex_format(type, size, normalized != 0);
        if (format == 0) {
            fail(reason, capacity, "unsupported vertex element format");
            return NO;
        }
        NSUInteger buffer_index = next_buffer++;
        MTLVertexAttributeDescriptor *attribute = descriptor.attributes[location];
        attribute.format = format;
        attribute.offset = offset + slot_offset;
        attribute.bufferIndex = buffer_index;
        descriptor.layouts[buffer_index].stride = stride;
        if (instanced_draw && divisor > 0) {
            descriptor.layouts[buffer_index].stepFunction = MTLVertexStepFunctionPerInstance;
            descriptor.layouts[buffer_index].stepRate = divisor;
        } else {
            descriptor.layouts[buffer_index].stepFunction = MTLVertexStepFunctionPerVertex;
            descriptor.layouts[buffer_index].stepRate = 1;
        }
        buffers[@(buffer_index)] = @{@"buffer": (__bridge id)metal_buffer,
                                     @"offset": @0};
        [covered addObject:@(location)];
    }
    /* Attributes the translated program declares but the engine's layout does
       not provide are supplied as a constant generic value. Metal requires a
       descriptor entry for every stage_in attribute. */
    for (NSNumber *location in declared) {
        if ([covered containsObject:location]) {
            continue;
        }
        NSUInteger buffer_index = next_buffer++;
        MTLVertexAttributeDescriptor *attribute = descriptor.attributes[location.unsignedIntegerValue];
        attribute.format = MTLVertexFormatFloat4;
        attribute.offset = 0;
        attribute.bufferIndex = buffer_index;
        descriptor.layouts[buffer_index].stride = 16;
        descriptor.layouts[buffer_index].stepFunction = MTLVertexStepFunctionPerVertex;
        descriptor.layouts[buffer_index].stepRate = 1;
        buffers[@(buffer_index)] = @{@"constant": @[@0.0f, @0.0f, @0.0f, @1.0f]};
    }
    return YES;
}

static id<MTLRenderPipelineState> pipeline_for(EU4GfxPipelineKey *unused,
                                               id<MTLDevice> device,
                                               id<MTLFunction> vertex,
                                               id<MTLFunction> fragment,
                                               MTLVertexDescriptor *descriptor,
                                               MTLPixelFormat color_format,
                                               MTLPixelFormat depth_format,
                                               const uint8_t *blend_snapshot,
                                               BOOL depth_test, BOOL depth_write,
                                               uint32_t depth_compare) {
    (void)unused;
    static NSMutableDictionary *cache;
    static id<MTLDevice> cached_device;
    if (cache == nil || cached_device != device) {
        cache = [NSMutableDictionary dictionary];
        cached_device = device;
    }
    NSMutableString *vertex_signature = [NSMutableString string];
    /* Metal exposes attribute indices 0..30 (31 in total). */
    for (NSUInteger i = 0; i < 31; i++) {
        MTLVertexAttributeDescriptor *attribute = descriptor.attributes[i];
        if (attribute.format != 0) {
            [vertex_signature appendFormat:@"a%lu:%lu:%lu;", (unsigned long)i,
                                           (unsigned long)attribute.format,
                                           (unsigned long)attribute.bufferIndex];
        }
        MTLVertexBufferLayoutDescriptor *layout = descriptor.layouts[i];
        if (layout.stride != 0) {
            [vertex_signature appendFormat:@"l%lu:%lu:%lu:%lu;", (unsigned long)i,
                                           (unsigned long)layout.stride,
                                           (unsigned long)layout.stepFunction,
                                           (unsigned long)layout.stepRate];
        }
    }
    uint32_t enabled = load_u32(blend_snapshot, 0);
    uint32_t src = load_u32(blend_snapshot, 4);
    uint32_t dst = load_u32(blend_snapshot, 8);
    uint32_t equation = load_u32(blend_snapshot, 0xc);
    uint32_t src_a = load_u32(blend_snapshot, 0x10);
    uint32_t dst_a = load_u32(blend_snapshot, 0x14);
    uint32_t equation_a = load_u32(blend_snapshot, 0x18);
    NSString *key = [NSString stringWithFormat:@"%p-%p-%lu-%lu-%u-%u-%u-%u-%u-%u-%u-%u-%d-%d-%u",
                     (__bridge void *)vertex, (__bridge void *)fragment,
                     (unsigned long)color_format, (unsigned long)depth_format, enabled, src, dst, equation,
                     src_a, dst_a, equation_a, depth_test ? 1u : 0u,
                     depth_write ? 1 : 0, (unsigned)depth_compare,
                     (unsigned)descriptor.hash];
    id<MTLRenderPipelineState> pipeline = cache[key];
    if (pipeline != nil) {
        return pipeline;
    }
    MTLRenderPipelineDescriptor *pipeline_descriptor = [MTLRenderPipelineDescriptor new];
    pipeline_descriptor.vertexFunction = vertex;
    pipeline_descriptor.fragmentFunction = fragment;
    pipeline_descriptor.vertexDescriptor = descriptor;
    pipeline_descriptor.colorAttachments[0].pixelFormat = color_format;
    if (depth_format != MTLPixelFormatInvalid) {
        pipeline_descriptor.depthAttachmentPixelFormat = depth_format;
    }
    MTLRenderPipelineColorAttachmentDescriptor *color = pipeline_descriptor.colorAttachments[0];
    if (enabled != 0) {
        color.blendingEnabled = YES;
        color.sourceRGBBlendFactor = blend_factor(src);
        color.destinationRGBBlendFactor = blend_factor(dst);
        color.sourceAlphaBlendFactor = blend_factor(src_a);
        color.destinationAlphaBlendFactor = blend_factor(dst_a);
        color.rgbBlendOperation = blend_operation(equation);
        color.alphaBlendOperation = blend_operation(equation_a);
    }
    NSError *error = nil;
    pipeline = [device newRenderPipelineStateWithDescriptor:pipeline_descriptor
                                                       error:&error];
    if (pipeline != nil) {
        cache[key] = pipeline;
    } else {
        NSString *description = error != nil ? error.localizedDescription : @"unknown";
        char body[1024];
        int n = snprintf(body, sizeof body,
                         "{\"schema\":\"gfx-generic-pipeline-error/v1\","
                         "\"error\":\"%s\",\"color_format\":%lu,"
                         "\"depth_format\":%lu,\"pid\":%d}\n",
                         description.UTF8String ?: "",
                         (unsigned long)color_format, (unsigned long)depth_format,
                         (int)getpid());
        if (n > 0 && (size_t)n < sizeof body) {
            (void)eu4_metal_events_write("generic-pipeline-error.json", body);
        }
    }
    return pipeline;
}

static int prepare(const void *immediate_context,
                   const eu4_gfx_generic_call *call,
                   const eu4_gfx_simple_draw_inputs *inputs,
                   uint8_t **master_out, void **shader_out,
                   NSDictionary **vertex_stage_out, NSDictionary **pixel_stage_out,
                   char *reason, size_t capacity) {
    if (immediate_context == NULL || call == NULL) {
        fail(reason, capacity, "generic draw has no context or call");
        return 0;
    }
    uint8_t *master = (uint8_t *)load_ptr((const uint8_t *)immediate_context, 0);
    if (master == NULL) {
        fail(reason, capacity, "generic draw has no master");
        return 0;
    }
    void *shader = load_ptr(master, 0x70);
    if (shader == NULL) {
        fail(reason, capacity, "generic draw has no current shader");
        return 0;
    }
    NSDictionary *vertex_stage = (__bridge NSDictionary *)eu4_gfx_shader_vertex_stage(shader);
    NSDictionary *pixel_stage = (__bridge NSDictionary *)eu4_gfx_shader_pixel_stage(shader);
    if (vertex_stage == nil || pixel_stage == nil) {
        fail(reason, capacity, "shader is not admitted from the runtime cache");
        return 0;
    }
    *master_out = master;
    *shader_out = shader;
    *vertex_stage_out = vertex_stage;
    *pixel_stage_out = pixel_stage;
    return 1;
}

int eu4_gfx_generic_draw_supported(const void *immediate_context,
                                   const eu4_gfx_generic_call *call,
                                   char *reason, size_t capacity) {
    uint8_t *master = NULL;
    void *shader = NULL;
    NSDictionary *vertex_stage = nil;
    NSDictionary *pixel_stage = nil;
    if (!prepare(immediate_context, call, NULL, &master, &shader, &vertex_stage,
                 &pixel_stage, reason, capacity)) {
        return 0;
    }
    (void)vertex_stage;
    (void)pixel_stage;
    return 1;
}

static int generic_draw_encode_impl(const void *immediate_context,
                                    const eu4_gfx_generic_call *call,
                                    const eu4_gfx_simple_draw_inputs *inputs,
                                    void *command_buffer, const char **reason,
                                    eu4_gfx_simple_draw_state *state) {
    char local_reason[256] = "";
    uint8_t *master = NULL;
    void *shader = NULL;
    NSDictionary *vertex_stage = nil;
    NSDictionary *pixel_stage = nil;
    if (reason != NULL) {
        *reason = local_reason;
    }
    if (!prepare(immediate_context, call, inputs, &master, &shader, &vertex_stage,
                 &pixel_stage, local_reason, sizeof(local_reason))) {
        return 0;
    }
    id<MTLDevice> device = inputs != NULL ? (__bridge id<MTLDevice>)inputs->device : nil;
    id<MTLCommandBuffer> command = (__bridge id<MTLCommandBuffer>)command_buffer;
    id<MTLTexture> target = (__bridge id<MTLTexture>)inputs->target;
    if (inputs == NULL || device == nil || command == nil || target == nil) {
        fail(local_reason, sizeof(local_reason), "generic draw has no Metal target");
        return 0;
    }
    void *vertex_function = NULL;
    void *fragment_function = NULL;
    if (!eu4_gfx_shader_generic_functions(shader, (__bridge void *)device,
                                         &vertex_function, &fragment_function) ||
        vertex_function == NULL || fragment_function == NULL) {
        fail(local_reason, sizeof(local_reason), "cached MSL did not compile");
        return 0;
    }
    int instanced_draw = call->instance_count > 1;
    MTLVertexDescriptor *descriptor = [MTLVertexDescriptor vertexDescriptor];
    NSMutableDictionary *buffers = [NSMutableDictionary dictionary];
    if (!build_vertex_descriptor(shader, master, device, descriptor, buffers,
                                 vertex_stage, instanced_draw, local_reason,
                                 sizeof(local_reason))) {
        return 0;
    }
    NSData *vertex_uniforms = pack_stage_uniforms(vertex_stage, master, local_reason,
                                                  sizeof(local_reason));
    NSData *pixel_uniforms = pack_stage_uniforms(pixel_stage, master, local_reason,
                                                 sizeof(local_reason));
    if (vertex_uniforms == nil || pixel_uniforms == nil) {
        return 0;
    }
    uint8_t blend_snapshot[0x24];
    memcpy(blend_snapshot, master + 0xc, sizeof blend_snapshot);
    __unsafe_unretained EU4GfxBackend *backend =
        (__bridge EU4GfxBackend *)load_ptr(master, 0x180);
    id<MTLTexture> depth_texture = backend != nil ? backend.currentTargetDepth : nil;
    BOOL depth_test = depth_texture != nil && load_u32(master + 0x30, 0) != 0;
    BOOL depth_write = depth_texture != nil && ((const uint8_t *)master)[0x31] != 0;
    uint32_t depth_compare = load_u32(master + 0x34, 0);
    if (depth_compare == 0) {
        depth_compare = 0x0207;
    }
    id<MTLRenderPipelineState> pipeline =
        pipeline_for(NULL, device, (__bridge id<MTLFunction>)vertex_function,
                     (__bridge id<MTLFunction>)fragment_function, descriptor,
                     target.pixelFormat,
                     depth_texture != nil ? MTLPixelFormatDepth32Float
                                          : MTLPixelFormatInvalid,
                     blend_snapshot, depth_test, depth_write, depth_compare);
    if (pipeline == nil) {
        fail(local_reason, sizeof(local_reason), "generic pipeline creation failed");
        return 0;
    }
    MTLRenderPassDescriptor *pass = [MTLRenderPassDescriptor renderPassDescriptor];
    pass.colorAttachments[0].texture = target;
    pass.colorAttachments[0].loadAction = MTLLoadActionLoad;
    pass.colorAttachments[0].storeAction = MTLStoreActionStore;
    if (depth_texture != nil) {
        pass.depthAttachment.texture = depth_texture;
        pass.depthAttachment.loadAction = MTLLoadActionLoad;
        pass.depthAttachment.storeAction = MTLStoreActionStore;
    }
    id<MTLRenderCommandEncoder> encoder = [command renderCommandEncoderWithDescriptor:pass];
    if (encoder == nil) {
        fail(local_reason, sizeof(local_reason), "generic render encoder failed");
        return 0;
    }
    [encoder setRenderPipelineState:pipeline];
    /* The engine viewport is in logical pixels; the backbuffer can be the
       physical drawable (HiDPI). Scale exactly like the shared encoders. */
    double scale_x = 1.0;
    double scale_y = 1.0;
    if (inputs->logical_width > 0 && inputs->logical_height > 0) {
        scale_x = (double)inputs->target_width / (double)inputs->logical_width;
        scale_y = (double)inputs->target_height / (double)inputs->logical_height;
    }
    double viewport_x = (double)inputs->viewport_x * scale_x;
    double viewport_y = (double)inputs->viewport_y * scale_y;
    double viewport_width = (double)inputs->viewport_width * scale_x;
    double viewport_height = (double)inputs->viewport_height * scale_y;
    MTLViewport viewport = {
        viewport_x,
        (double)inputs->target_height - viewport_y - viewport_height,
        viewport_width, viewport_height, 0.0, 1.0};
    [encoder setViewport:viewport];
    MTLScissorRect scissor = {0, 0, target.width, target.height};
    [encoder setScissorRect:scissor];
    uint8_t raster[0x10];
    memcpy(raster, master + 0x54, sizeof raster);
    uint32_t cull_face = load_u32(raster, 0);
    uint32_t front_face = load_u32(raster, 8);
    [encoder setFrontFacingWinding:front_face == 0x900 ? MTLWindingClockwise
                                                       : MTLWindingCounterClockwise];
    if (raster[0xc] != 0 && cull_face == 0x405) {
        [encoder setCullMode:MTLCullModeBack];
    } else if (raster[0xc] != 0 && cull_face == 0x404) {
        [encoder setCullMode:MTLCullModeFront];
    } else {
        [encoder setCullMode:MTLCullModeNone];
    }
    if (depth_texture != nil) {
        MTLDepthStencilDescriptor *depth_descriptor = [MTLDepthStencilDescriptor new];
        depth_descriptor.depthCompareFunction = depth_test
            ? compare_function(depth_compare) : MTLCompareFunctionAlways;
        depth_descriptor.depthWriteEnabled = depth_write ? YES : NO;
        id<MTLDepthStencilState> depth_state =
            [device newDepthStencilStateWithDescriptor:depth_descriptor];
        if (depth_state != nil) {
            [encoder setDepthStencilState:depth_state];
        }
    }
    NSUInteger vertex_uniform_buffer = num(vertex_stage[@"msl_uniform_buffer"], 0);
    NSUInteger pixel_uniform_buffer = num(pixel_stage[@"msl_uniform_buffer"], 0);
    if (vertex_uniforms.length > 0) {
        [encoder setVertexBytes:vertex_uniforms.bytes
                         length:vertex_uniforms.length
                        atIndex:vertex_uniform_buffer];
    }
    if (pixel_uniforms.length > 0) {
        [encoder setFragmentBytes:pixel_uniforms.bytes
                           length:pixel_uniforms.length
                          atIndex:pixel_uniform_buffer];
    }
    /* Textures: the engine binds units in the shader's sampler declaration
       order, which is the translated stage's binding order. */
    NSArray *bound = backend != nil ? backend.boundTextures : nil;
    for (NSString *stage_name in @[@"vertex", @"pixel"]) {
        NSDictionary *stage = [stage_name isEqualToString:@"vertex"] ? vertex_stage : pixel_stage;
        NSArray *samplers = stage[@"samplers"];
        NSArray *msl_samplers = stage[@"msl_samplers"];
        for (NSUInteger index = 0; index < samplers.count; index++) {
            NSDictionary *sampler_entry = samplers[index];
            NSUInteger unit = num(sampler_entry[@"binding"], index);
            void *header = NULL;
            if (bound != nil && unit < bound.count) {
                [(NSValue *)bound[unit] getValue:&header];
            }
            id<MTLTexture> texture = nil;
            if (header != NULL) {
                texture = (__bridge id<MTLTexture>)eu4_gfx_texture_for_header(header);
            }
            if (texture == nil) {
                texture = backend.fallback;
            }
            if (texture == nil) {
                [encoder endEncoding];
                fail(local_reason, sizeof(local_reason), "sampled texture is absent");
                return 0;
            }
            NSDictionary *binding = index < msl_samplers.count ? msl_samplers[index] : nil;
            NSUInteger texture_index = binding != nil
                ? num(binding[@"texture_index"], index) : index;
            NSUInteger sampler_index = binding != nil
                ? num(binding[@"sampler_index"], index) : index;
            id<MTLSamplerState> sampler = generic_sampler(device, @"linear", NO);
            if ([stage_name isEqualToString:@"vertex"]) {
                [encoder setVertexTexture:texture atIndex:texture_index];
                [encoder setVertexSamplerState:sampler atIndex:sampler_index];
            } else {
                [encoder setFragmentTexture:texture atIndex:texture_index];
                [encoder setFragmentSamplerState:sampler atIndex:sampler_index];
            }
        }
    }
    for (NSNumber *key in buffers) {
        NSDictionary *entry = buffers[key];
        if (entry[@"constant"] != nil) {
            float values[4] = {0.0f, 0.0f, 0.0f, 1.0f};
            NSArray *constant = entry[@"constant"];
            for (NSUInteger i = 0; i < 4 && i < constant.count; i++) {
                values[i] = [constant[i] floatValue];
            }
            [encoder setVertexBytes:values
                             length:sizeof values
                            atIndex:[key unsignedIntegerValue]];
        } else {
            [encoder setVertexBuffer:entry[@"buffer"]
                              offset:[entry[@"offset"] unsignedIntegerValue]
                             atIndex:[key unsignedIntegerValue]];
        }
    }
    MTLPrimitiveType primitive = MTLPrimitiveTypeTriangle;
    switch (load_u32(master, 0x08)) {
        case 0: primitive = MTLPrimitiveTypePoint; break;
        case 1: primitive = MTLPrimitiveTypeLine; break;
        case 2: primitive = MTLPrimitiveTypeLineStrip; break;
        case 3: primitive = MTLPrimitiveTypeTriangleStrip; break;
        case 4: primitive = MTLPrimitiveTypeTriangle; break;
        case 5: primitive = MTLPrimitiveTypeTriangleStrip; break;
        default:
            [encoder endEncoding];
            fail(local_reason, sizeof(local_reason), "unsupported primitive mode");
            return 0;
    }
    NSUInteger instance_count = call->instance_count > 0 ? (NSUInteger)call->instance_count : 1;
    if (call->indexed) {
        void *index_header = eu4_gfx_current_index_header();
        if (index_header == NULL) {
            [encoder endEncoding];
            fail(local_reason, sizeof(local_reason), "indexed draw has no bound index buffer");
            return 0;
        }
        size_t bytes = eu4_gfx_index_buffer_length(index_header);
        size_t needed = (size_t)call->count * 2;
        if (bytes == (size_t)-1 || bytes < needed || bytes == 0) {
            [encoder endEncoding];
            fail(local_reason, sizeof(local_reason), "index buffer is too short");
            return 0;
        }
        uint16_t *indices = (uint16_t *)malloc(bytes);
        if (indices == NULL || eu4_gfx_index_buffer_read(index_header, indices, bytes) != 0) {
            free(indices);
            [encoder endEncoding];
            fail(local_reason, sizeof(local_reason), "index buffer read failed");
            return 0;
        }
        NSMutableData *rewritten = [NSMutableData dataWithLength:needed * 2];
        uint32_t *destination = (uint32_t *)rewritten.mutableBytes;
        for (int32_t i = 0; i < call->count; i++) {
            uint32_t value = indices[i];
            destination[i] = value >= (uint32_t)call->base_vertex
                ? value - (uint32_t)call->base_vertex : 0;
        }
        free(indices);
        id<MTLBuffer> index_buffer = [device newBufferWithBytes:rewritten.bytes
                                                         length:rewritten.length
                                                        options:MTLResourceStorageModeManaged];
        [encoder drawIndexedPrimitives:primitive
                            indexCount:(NSUInteger)call->count
                             indexType:MTLIndexTypeUInt32
                           indexBuffer:index_buffer
                     indexBufferOffset:0
                         instanceCount:instance_count];
    } else {
        [encoder drawPrimitives:primitive
                    vertexStart:(NSUInteger)(call->first > 0 ? call->first : 0)
                    vertexCount:(NSUInteger)call->count
                  instanceCount:instance_count];
    }
    [encoder endEncoding];
    g_generic_draws++;
    if (state != NULL) {
        memset(state, 0, sizeof *state);
    }
    return 1;
}

int eu4_gfx_generic_draw_encode_into(const void *immediate_context,
                                     const eu4_gfx_generic_call *call,
                                     const eu4_gfx_simple_draw_inputs *inputs,
                                     void *command_buffer, const char **reason,
                                     eu4_gfx_simple_draw_state *state) {
    @try {
        return generic_draw_encode_impl(immediate_context, call, inputs,
                                        command_buffer, reason, state);
    } @catch (NSException *exception) {
        char body[1024];
        int n = snprintf(
            body, sizeof body,
            "{\"schema\":\"gfx-generic-exception/v1\",\"name\":\"%s\","
            "\"reason\":\"%s\",\"thread\":%llu,\"pid\":%d}\n",
            exception.name.UTF8String ?: "",
            exception.reason.UTF8String ?: "",
            (unsigned long long)pthread_self(), (int)getpid());
        if (n > 0 && (size_t)n < sizeof body) {
            (void)eu4_metal_events_write("generic-exception.json", body);
        }
        if (reason != NULL) {
            *reason = "Objective-C exception in the generic encoder";
        }
        return 0;
    }
}
