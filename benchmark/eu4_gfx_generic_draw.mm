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

static float g_logged_positions[16];
static int g_have_positions;
typedef struct {
    uint32_t location, type, size, normalized, divisor;
    unsigned long slot, offset, stride, format;
    int converted;
} eu4_logged_element;
static eu4_logged_element g_logged_elements[16];
static int g_logged_element_count;

/* Metal rejects binding a cube texture to a texture2d parameter (and vice
   versa) even when the unit is not sampled. The engine's GL bindings include
   such units; substitute a compatible 1x1 placeholder for the declared type so
   the draw is not rejected. */
static id<MTLTexture> generic_type_placeholder(id<MTLDevice> device, BOOL cube) {
    static id<MTLTexture> flat;
    static id<MTLTexture> cubemap;
    static id<MTLDevice> cached_device;
    if (device == nil) {
        return nil;
    }
    if (cached_device != device) {
        flat = nil;
        cubemap = nil;
        cached_device = device;
    }
    if (cube) {
        if (cubemap == nil) {
            MTLTextureDescriptor *descriptor =
                [MTLTextureDescriptor textureCubeDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm
                                                                      size:1
                                                                 mipmapped:NO];
            cubemap = [device newTextureWithDescriptor:descriptor];
        }
        return cubemap;
    }
    if (flat == nil) {
        MTLTextureDescriptor *descriptor =
            [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm
                                                                 width:1
                                                                height:1
                                                             mipmapped:NO];
        flat = [device newTextureWithDescriptor:descriptor];
    }
    return flat;
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
    if (getenv("EU4_GFX_UNIFORM_DUMP") != NULL && data.length > 0) {
        NSString *file = stage[@"msl_file"] ?: @"";
        NSString *stem = file.lastPathComponent.stringByDeletingPathExtension;
        if (stem.length > 16) {
            stem = [stem substringToIndex:16];
        }
        const char *dir = getenv("EU4_GFX_UNIFORM_DUMP");
        char bin[512];
        char meta[512];
        snprintf(bin, sizeof bin, "%s/uniform-%s.bin", dir, stem.UTF8String);
        snprintf(meta, sizeof meta, "%s/uniform-%s.json", dir, stem.UTF8String);
        FILE *bf = fopen(bin, "wb");
        if (bf != NULL) {
            fwrite(data.bytes, 1, data.length, bf);
            fclose(bf);
        }
        NSMutableString *info = [NSMutableString stringWithFormat:
            @"{\"stage\":\"%s\",\"size\":%lu,\"slots\":[",
            stem.UTF8String, (unsigned long)data.length];
        for (int slot = 0; slot < 6; slot++) {
            uint32_t count = load_u32(master, 0xc8 + slot * 16);
            void *header = load_ptr(master, 0xd0 + slot * 16);
            const void *values = header != NULL ? load_ptr((const uint8_t *)header, 8) : NULL;
            [info appendFormat:@"%@{\"slot\":%d,\"count\":%u,\"header\":\"%p\",\"values\":\"%p\"}",
                               slot > 0 ? @"," : @"", slot, count, header, values];
        }
        [info appendString:@"]}\n"];
        FILE *mf = fopen(meta, "w");
        if (mf != NULL) {
            fputs(info.UTF8String, mf);
            fclose(mf);
        }
    }
    return data;
}

/* Collects the engine vertex elements and builds a Metal vertex descriptor. */
static BOOL glsl_type_is_float(NSString *type) {
    /* The cached stage metadata carries the translated MSL type names
       (float/float2..float4); older entries use GLSL names. */
    return [type isEqualToString:@"float"] || [type isEqualToString:@"float2"] ||
           [type isEqualToString:@"float3"] || [type isEqualToString:@"float4"] ||
           [type isEqualToString:@"vec2"] || [type isEqualToString:@"vec3"] ||
           [type isEqualToString:@"vec4"];
}

static BOOL gl_type_is_integer(uint32_t type) {
    return type == 0x1401 || type == 0x1402 || type == 0x1403 ||
           type == 0x1404 || type == 0x1405;
}

/* GL converts unnormalized integer vertex data to floats verbatim; Metal has
   no such vertex format, so the data is expanded to float32 once per
   (buffer, layout) and cached. */
/* Converts an interleaved GL integer attribute stream to float32. Exported for
   focused host tests. Returns the number of converted vertices; *out is
   malloc'ed (caller frees) or NULL on failure. The final vertex is included
   when its full component set fits in the buffer. */
size_t eu4_gfx_convert_attribute(const uint8_t *raw, size_t length, size_t start,
                                 size_t row, uint32_t gl_type,
                                 uint32_t components, float **out) {
    if (out != NULL) {
        *out = NULL;
    }
    if (raw == NULL || out == NULL || components == 0 || components > 4 ||
        row == 0) {
        return 0;
    }
    size_t component_bytes = 4;
    switch (gl_type) {
        case 0x1401: component_bytes = 1; break;
        case 0x1402:
        case 0x1403: component_bytes = 2; break;
        case 0x1404:
        case 0x1405:
        case 0x1406: component_bytes = 4; break;
        default: return 0;
    }
    size_t vertices = 0;
    while (start + vertices * row + (size_t)components * component_bytes <=
           length) {
        vertices++;
    }
    if (vertices == 0) {
        return 0;
    }
    float *values = (float *)calloc(vertices, (size_t)components * sizeof(float));
    if (values == NULL) {
        return 0;
    }
    for (size_t i = 0; i < vertices; i++) {
        const uint8_t *source = raw + start + i * row;
        for (uint32_t c = 0; c < components; c++) {
            const uint8_t *component = source + (size_t)c * component_bytes;
            float value = 0.0f;
            switch (gl_type) {
                case 0x1401: /* GL_UNSIGNED_BYTE */
                    value = (float)component[0];
                    break;
                case 0x1402: { /* GL_SHORT */
                    int16_t v = 0;
                    memcpy(&v, component, 2);
                    value = (float)v;
                    break;
                }
                case 0x1403: { /* GL_UNSIGNED_SHORT */
                    uint16_t v = 0;
                    memcpy(&v, component, 2);
                    value = (float)v;
                    break;
                }
                case 0x1404: { /* GL_INT (signed 32-bit) */
                    int32_t v = 0;
                    memcpy(&v, component, 4);
                    value = (float)v;
                    break;
                }
                case 0x1405: { /* GL_UNSIGNED_INT (unsigned 32-bit) */
                    uint32_t v = 0;
                    memcpy(&v, component, 4);
                    value = (float)v;
                    break;
                }
                case 0x1406: { /* GL_FLOAT */
                    float v = 0.0f;
                    memcpy(&v, component, 4);
                    value = v;
                    break;
                }
                default:
                    break;
            }
            values[i * components + c] = value;
        }
    }
    *out = values;
    return vertices;
}

/* The conversion is rebuilt on every call: the same vertex-buffer header can
   be updated with new contents, so a header-keyed cache would be stale. The
   converted path only serves integer attributes (a handful of draws). */
static id<MTLBuffer> converted_attribute_buffer(id<MTLDevice> device, void *header,
                                                uint32_t element_offset,
                                                uint32_t slot_offset,
                                                uint32_t stride, uint32_t gl_type,
                                                uint32_t components,
                                                NSUInteger *out_stride) {
    size_t length = eu4_gfx_vertex_buffer_length(header);
    if (length == (size_t)-1 || length == 0) {
        return nil;
    }
    uint8_t *raw = (uint8_t *)malloc(length);
    if (raw == NULL) {
        return nil;
    }
    if (eu4_gfx_vertex_buffer_read(header, raw, length) != 0) {
        free(raw);
        return nil;
    }
    size_t start = (size_t)slot_offset + element_offset;
    size_t row = stride != 0 ? stride : (size_t)components * 4;
    if (start >= length) {
        free(raw);
        return nil;
    }
    float *out = NULL;
    size_t vertices = eu4_gfx_convert_attribute(raw, length, start, row, gl_type,
                                                components, &out);
    free(raw);
    if (vertices == 0 || out == NULL) {
        free(out);
        return nil;
    }
    size_t out_row = (size_t)components * 4;
    id<MTLBuffer> buffer = [device newBufferWithBytes:out
                                               length:vertices * out_row
                                              options:MTLResourceStorageModeManaged];
    free(out);
    if (buffer != nil && out_stride != NULL) {
        *out_stride = (NSUInteger)out_row;
    }
    return buffer;
}

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
    NSMutableDictionary *attribute_types = [NSMutableDictionary dictionary];
    if ([stage[@"attributes"] isKindOfClass:[NSArray class]] &&
        [locations isKindOfClass:[NSDictionary class]]) {
        for (NSDictionary *attribute in stage[@"attributes"]) {
            NSNumber *location = locations[attribute[@"name"]];
            if (location != nil) {
                attribute_types[location] = attribute[@"type"] ?: @"";
            }
        }
    }
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
        if (g_logged_element_count < 16) {
            eu4_logged_element *logged = &g_logged_elements[g_logged_element_count++];
            logged->location = location;
            logged->type = type;
            logged->size = size;
            logged->normalized = normalized;
            logged->divisor = divisor;
            logged->slot = slot;
            logged->offset = offset;
            logged->stride = stride;
            logged->format = (unsigned long)format;
            logged->converted = 0;
        }
        if (format == 0) {
            fail(reason, capacity, "unsupported vertex element format");
            return NO;
        }
        NSUInteger buffer_index = next_buffer++;
        MTLVertexAttributeDescriptor *attribute = descriptor.attributes[location];
        NSString *declared_type = attribute_types[@(location)] ?: @"";
        BOOL convert = glsl_type_is_float(declared_type) && gl_type_is_integer(type) &&
                       normalized == 0 && size >= 1 && size <= 4 && divisor == 0;
        id<MTLBuffer> bound_buffer = (__bridge id<MTLBuffer>)metal_buffer;
        NSUInteger bound_stride = stride;
        NSUInteger bound_offset = 0;
        if (convert) {
            NSUInteger converted_stride = 0;
            id<MTLBuffer> converted = converted_attribute_buffer(
                device, header, offset, slot_offset, stride, type, size,
                &converted_stride);
            if (converted == nil) {
                fail(reason, capacity, "integer attribute conversion failed");
                return NO;
            }
            switch (size) {
                case 1: format = MTLVertexFormatFloat; break;
                case 2: format = MTLVertexFormatFloat2; break;
                case 3: format = MTLVertexFormatFloat3; break;
                default: format = MTLVertexFormatFloat4; break;
            }
            bound_buffer = converted;
            bound_stride = converted_stride;
            bound_offset = 0;
            if (g_logged_element_count > 0) {
                g_logged_elements[g_logged_element_count - 1].converted = 1;
            }
            if (location == 0 && converted.contents != NULL &&
                converted.length >= 16 * sizeof(float)) {
                memcpy(g_logged_positions, converted.contents,
                       16 * sizeof(float));
                g_have_positions = 1;
            }
        } else {
            bound_offset = offset + slot_offset;
        }
        attribute.format = format;
        attribute.offset = bound_offset;
        attribute.bufferIndex = buffer_index;
        descriptor.layouts[buffer_index].stride = bound_stride;
        if (instanced_draw && divisor > 0) {
            descriptor.layouts[buffer_index].stepFunction = MTLVertexStepFunctionPerInstance;
            descriptor.layouts[buffer_index].stepRate = divisor;
        } else {
            descriptor.layouts[buffer_index].stepFunction = MTLVertexStepFunctionPerVertex;
            descriptor.layouts[buffer_index].stepRate = 1;
        }
        buffers[@(buffer_index)] = @{@"buffer": bound_buffer,
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

static void dump_texture_file(id<MTLTexture> texture, const char *path) {
    if (texture == nil || path == NULL) {
        return;
    }
    id<MTLDevice> device = texture.device;
    if (device == nil) {
        return;
    }
    NSUInteger row = texture.width * 4;
    id<MTLBuffer> buffer = [device newBufferWithLength:row * texture.height
                                              options:MTLResourceStorageModeShared];
    if (buffer == nil) {
        return;
    }
    EU4GfxBackend *dump_backend = (__bridge EU4GfxBackend *)eu4_gfx_backend_token();
    id<MTLCommandQueue> queue = (dump_backend != nil && dump_backend.queue != nil)
        ? dump_backend.queue : [device newCommandQueue];
    id<MTLCommandBuffer> command = [queue commandBuffer];
    id<MTLBlitCommandEncoder> blit = [command blitCommandEncoder];
    [blit copyFromTexture:texture
              sourceSlice:0
              sourceLevel:0
             sourceOrigin:MTLOriginMake(0, 0, 0)
               sourceSize:MTLSizeMake(texture.width, texture.height, 1)
                 toBuffer:buffer
        destinationOffset:0
   destinationBytesPerRow:row
 destinationBytesPerImage:row * texture.height];
    [blit endEncoding];
    [command commit];
    [command waitUntilCompleted];
    uint8_t *flipped = (uint8_t *)malloc(row * texture.height);
    if (flipped == NULL) {
        return;
    }
    const uint8_t *src = (const uint8_t *)buffer.contents;
    for (NSUInteger y = 0; y < texture.height; y++) {
        memcpy(flipped + y * row, src + (texture.height - 1 - y) * row, row);
    }
    FILE *file = fopen(path, "wb");
    if (file != NULL) {
        fwrite(flipped, 1, row * texture.height, file);
        fclose(file);
    }
    free(flipped);
}

static int generic_draw_encode_impl(const void *immediate_context,
                                    const eu4_gfx_generic_call *call,
                                    const eu4_gfx_simple_draw_inputs *inputs,
                                    void *command_buffer, char *reason,
                                    size_t reason_capacity,
                                    eu4_gfx_simple_draw_state *state) {
    memset(g_logged_positions, 0, sizeof g_logged_positions);
    g_have_positions = 0;
    g_logged_element_count = 0;
    if (reason != NULL && reason_capacity > 0) {
        reason[0] = 0;
    }
    uint8_t *master = NULL;
    void *shader = NULL;
    NSDictionary *vertex_stage = nil;
    NSDictionary *pixel_stage = nil;
    if (!prepare(immediate_context, call, inputs, &master, &shader, &vertex_stage,
                 &pixel_stage, reason, reason_capacity)) {
        return 0;
    }
    id<MTLDevice> device = inputs != NULL ? (__bridge id<MTLDevice>)inputs->device : nil;
    id<MTLCommandBuffer> command = (__bridge id<MTLCommandBuffer>)command_buffer;
    id<MTLTexture> target = (__bridge id<MTLTexture>)inputs->target;
    if (inputs == NULL || device == nil || command == nil || target == nil) {
        fail(reason, reason_capacity, "generic draw has no Metal target");
        return 0;
    }
    void *vertex_function = NULL;
    void *fragment_function = NULL;
    if (!eu4_gfx_shader_generic_functions(shader, (__bridge void *)device,
                                         &vertex_function, &fragment_function) ||
        vertex_function == NULL || fragment_function == NULL) {
        fail(reason, reason_capacity, "cached MSL did not compile");
        return 0;
    }
    int instanced_draw = call->instance_count > 1;
    MTLVertexDescriptor *descriptor = [MTLVertexDescriptor vertexDescriptor];
    NSMutableDictionary *buffers = [NSMutableDictionary dictionary];
    if (!build_vertex_descriptor(shader, master, device, descriptor, buffers,
                                 vertex_stage, instanced_draw, reason,
                                 reason_capacity)) {
        return 0;
    }
    NSData *vertex_uniforms = pack_stage_uniforms(vertex_stage, master, reason,
                                                  reason_capacity);
    NSData *pixel_uniforms = pack_stage_uniforms(pixel_stage, master, reason,
                                                 reason_capacity);
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
        fail(reason, reason_capacity, "generic pipeline creation failed");
        return 0;
    }
    BOOL composite_dump = NO;
    const char *composite_stage = getenv("EU4_GFX_COMPOSITE_STAGE");
    const char *composite_dir = getenv("EU4_GFX_COMPOSITE_DUMP");
    if (composite_stage != NULL && composite_dir != NULL && composite_stage[0] != 0) {
        NSString *file = vertex_stage[@"msl_file"] ?: @"";
        NSString *stem = file.lastPathComponent.stringByDeletingPathExtension;
        composite_dump = [stem hasPrefix:[NSString stringWithUTF8String:composite_stage]];
    }
    if (composite_dump) {
        char path[512];
        snprintf(path, sizeof path, "%s/before-composite.bgra", composite_dir);
        dump_texture_file(target, path);
        NSArray *bound_textures = backend != nil ? backend.boundTextures : nil;
        for (NSUInteger unit = 0; unit < bound_textures.count; unit++) {
            void *header = NULL;
            [(NSValue *)bound_textures[unit] getValue:&header];
            id<MTLTexture> texture = header != NULL
                ? (__bridge id<MTLTexture>)eu4_gfx_texture_for_header(header) : nil;
            if (texture != nil && texture.width >= 800) {
                snprintf(path, sizeof path, "%s/sample-u%lu-%lux%lu.bgra", composite_dir,
                         (unsigned long)unit, (unsigned long)texture.width,
                         (unsigned long)texture.height);
                dump_texture_file(texture, path);
            }
        }
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
        fail(reason, reason_capacity, "generic render encoder failed");
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
    if (getenv("EU4_GFX_NO_CULL") != NULL) {
        [encoder setCullMode:MTLCullModeNone];
    } else if (raster[0xc] != 0 && cull_face == 0x405) {
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
    NSMutableString *binding_log = getenv("EU4_GFX_TEXTURE_BIND_TRACE") != NULL
        ? [NSMutableString string] : nil;
    for (NSString *stage_name in @[@"vertex", @"pixel"]) {
        NSDictionary *stage = [stage_name isEqualToString:@"vertex"] ? vertex_stage : pixel_stage;
        NSArray *samplers = stage[@"samplers"];
        NSArray *msl_samplers = stage[@"msl_samplers"];
        /* msl_samplers is ordered by Metal binding; each entry names the GL
           sampler it corresponds to. Bind by name, not by array position: the
           names decide which texture unit feeds each Metal texture slot. */
        NSMutableDictionary *sampler_by_name = [NSMutableDictionary dictionary];
        for (NSDictionary *entry in samplers) {
            NSString *name = entry[@"name"] ?: @"";
            if (name.length > 0) {
                sampler_by_name[name] = entry;
            }
        }
        NSArray *bind_order = msl_samplers.count > 0 ? msl_samplers : samplers;
        for (NSUInteger index = 0; index < bind_order.count; index++) {
            NSDictionary *binding = bind_order[index];
            NSDictionary *sampler_entry = nil;
            NSUInteger unit = index;
            NSUInteger texture_index = index;
            NSUInteger sampler_index = index;
            if (msl_samplers.count > 0) {
                NSString *name = binding[@"name"] ?: @"";
                sampler_entry = sampler_by_name[name] ?: @{};
                unit = num(sampler_entry[@"binding"], index);
                texture_index = num(binding[@"texture_index"], index);
                sampler_index = num(binding[@"sampler_index"], index);
            } else {
                sampler_entry = binding;
                unit = num(binding[@"binding"], index);
            }
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
                fail(reason, reason_capacity, "sampled texture is absent");
                return 0;
            }
            BOOL substituted = NO;
            NSString *declared_type = sampler_entry[@"type"] ?: @"";
            if (declared_type.length == 0 && msl_samplers.count > 0) {
                declared_type = [binding[@"kind"] isEqualToString:@"texturecube"]
                    ? @"samplerCube" : @"sampler2D";
            }
            if (texture != nil && declared_type.length > 0) {
                BOOL expects_cube =
                    [declared_type rangeOfString:@"Cube" options:NSCaseInsensitiveSearch].location != NSNotFound;
                BOOL is_cube = texture.textureType == MTLTextureTypeCube ||
                               texture.textureType == MTLTextureTypeCubeArray;
                BOOL mismatched = expects_cube != is_cube;
                if (mismatched) {
                    id<MTLTexture> placeholder = generic_type_placeholder(device, expects_cube);
                    if (placeholder != nil) {
                        texture = placeholder;
                        substituted = YES;
                    }
                }
                if (getenv("EU4_GFX_TEXTURE_TYPE_TRACE") != NULL) {
                    char type_body[320];
                    int length = snprintf(
                        type_body, sizeof type_body,
                        "{\"stage\":\"%s\",\"unit\":%lu,\"texture_index\":%lu,"
                        "\"expected\":\"%s\",\"bound_type\":%lu,"
                        "\"mismatched\":%d,\"substituted\":%d}\n",
                        (((NSString *)stage[@"msl_file"]).UTF8String ?: ""),
                        (unsigned long)unit, (unsigned long)texture_index,
                        declared_type.UTF8String ?: "",
                        (unsigned long)texture.textureType, mismatched, substituted);
                    if (length > 0 && (size_t)length < sizeof type_body) {
                        (void)eu4_metal_events_append("texture-type-trace.jsonl", type_body);
                    }
                }
            }
            if (binding_log != nil) {
                uint32_t width = 0, height = 0, format = 0;
                if (header != NULL) {
                    memcpy(&width, (uint8_t *)header + 0x28, 4);
                    memcpy(&height, (uint8_t *)header + 0x2c, 4);
                    memcpy(&format, (uint8_t *)header + 0x30, 4);
                }
                BOOL resolved = header != NULL &&
                    eu4_gfx_texture_for_header(header) != NULL;
                [binding_log appendFormat:@"%@{\"unit\":%lu,\"stage\":\"%@\",\"texture_index\":%lu,\"header\":\"%p\",\"width\":%u,\"height\":%u,\"format\":%u,\"resolved\":%@,\"texture\":\"%p\"}",
                                          binding_log.length > 0 ? @"," : @"",
                                          (unsigned long)unit, stage_name,
                                          (unsigned long)texture_index, header,
                                          width, height, format,
                                          resolved ? @"true" : @"false",
                                          (__bridge void *)(header != NULL ? (__bridge id<MTLTexture>)eu4_gfx_texture_for_header(header) : nil)];
            }
            {
                /* Targeted sampled-texture dump: verifies whether a stage's
                   inputs contain data (black inputs explain black outputs). */
                const char *sample_stage = getenv("EU4_GFX_SAMPLE_TEXTURE_STAGE");
                const char *sample_dir = getenv("EU4_GFX_SAMPLE_TEXTURE_DUMP");
                if (sample_stage != NULL && sample_dir != NULL && sample_stage[0] != 0) {
                    NSString *vfile = vertex_stage[@"msl_file"] ?: @"";
                    NSString *pfile = pixel_stage[@"msl_file"] ?: @"";
                    NSString *vstem = vfile.lastPathComponent.stringByDeletingPathExtension;
                    NSString *pstem = pfile.lastPathComponent.stringByDeletingPathExtension;
                    NSString *wanted = [NSString stringWithUTF8String:sample_stage];
                    if ([vstem rangeOfString:wanted].location != NSNotFound ||
                        [pstem rangeOfString:wanted].location != NSNotFound) {
                        static NSMutableSet *dumped;
                        if (dumped == nil) {
                            dumped = [NSMutableSet set];
                        }
                        NSString *key = [NSString stringWithFormat:@"%lu-%lux%lu", (unsigned long)unit,
                                         (unsigned long)texture.width, (unsigned long)texture.height];
                        if (![dumped containsObject:key]) {
                            [dumped addObject:key];
                            char path[512];
                            snprintf(path, sizeof path, "%s/tex-u%lu-%lux%lu.bgra", sample_dir,
                                     (unsigned long)unit, (unsigned long)texture.width,
                                     (unsigned long)texture.height);
                            dump_texture_file(texture, path);
                        }
                    }
                }
            }
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
    /* The engine's draw bodies read the primitive at execution time; the
       entry-time snapshot can still hold the previous primitive. */
    uint32_t primitive_mode = load_u32(master, 0x08);
    switch (primitive_mode) {
        case 0: primitive = MTLPrimitiveTypePoint; break;
        case 1: primitive = MTLPrimitiveTypeLine; break;
        case 2: primitive = MTLPrimitiveTypeLineStrip; break;
        case 3: primitive = MTLPrimitiveTypeTriangleStrip; break;
        case 4: primitive = MTLPrimitiveTypeTriangle; break;
        case 5: primitive = MTLPrimitiveTypeTriangleStrip; break;
        default:
            [encoder endEncoding];
            fail(reason, reason_capacity, "unsupported primitive mode");
            return 0;
    }
    NSUInteger instance_count = call->instance_count > 0 ? (NSUInteger)call->instance_count : 1;
    if (call->indexed) {
        void *index_header = eu4_gfx_current_index_header();
        if (index_header == NULL) {
            [encoder endEncoding];
            fail(reason, reason_capacity, "indexed draw has no bound index buffer");
            return 0;
        }
        size_t bytes = eu4_gfx_index_buffer_length(index_header);
        size_t needed = (size_t)call->count * 2;
        if (bytes == (size_t)-1 || bytes < needed || bytes == 0) {
            [encoder endEncoding];
            fail(reason, reason_capacity, "index buffer is too short");
            return 0;
        }
        uint16_t *indices = (uint16_t *)malloc(bytes);
        if (indices == NULL || eu4_gfx_index_buffer_read(index_header, indices, bytes) != 0) {
            free(indices);
            [encoder endEncoding];
            fail(reason, reason_capacity, "index buffer read failed");
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
    if (composite_dump) {
        char path[512];
        snprintf(path, sizeof path, "%s/after-composite.bgra", composite_dir);
        dump_texture_file(target, path);
    }
    if (getenv("EU4_GFX_ELEMENT_TRACE") != NULL && g_logged_element_count > 0) {
        NSMutableString *elements = [NSMutableString string];
        for (int i = 0; i < g_logged_element_count; i++) {
            eu4_logged_element *e = &g_logged_elements[i];
            [elements appendFormat:
                @"%@{\"location\":%u,\"type\":%u,\"size\":%u,\"normalized\":%u,"
                 "\"divisor\":%u,\"slot\":%lu,\"offset\":%lu,\"stride\":%lu,"
                 "\"format\":%lu,\"converted\":%d}",
                elements.length > 0 ? @"," : @"", e->location, e->type, e->size,
                e->normalized, e->divisor, e->slot, e->offset, e->stride,
                e->format, e->converted];
        }
        NSString *file = vertex_stage[@"msl_file"] ?: @"";
        NSString *stem = file.lastPathComponent.stringByDeletingPathExtension;
        if (stem.length > 16) {
            stem = [stem substringToIndex:16];
        }
        NSString *json = [NSString stringWithFormat:
            @"{\"schema\":\"gfx-elements/v1\",\"stage\":\"%s\",\"count\":%d,\"elements\":[%@]}\n",
            file.UTF8String ?: "", call->count, elements];
        NSString *name = [NSString stringWithFormat:@"elements-%@.json", stem];
        (void)eu4_metal_events_write(name.UTF8String, json.UTF8String);
    }
    if (binding_log != nil) {
        NSString *vertex_file = vertex_stage[@"msl_file"] ?: @"";
        NSString *stem = vertex_file.lastPathComponent.stringByDeletingPathExtension;
        if (stem.length > 16) {
            stem = [stem substringToIndex:16];
        }
        NSString *json = [NSString stringWithFormat:
            @"{\"schema\":\"gfx-texture-bind/v1\",\"stage\":\"%s\","
             "\"bindings\":[%@],\"count\":%d,\"instances\":%d,"
             "\"target\":\"%p\",\"target_size\":[%u,%u],"
             "\"viewport\":[%u,%u,%u,%u],"
             "\"scaled_viewport\":[%.1f,%.1f,%.1f,%.1f],"
             "\"primitive\":%d,\"positions\":\"%.3f,%.3f,%.3f,%.3f,"
             "%.3f,%.3f,%.3f,%.3f,%.3f,%.3f,%.3f,%.3f,%.3f,%.3f,%.3f,%.3f\","
             "\"has_positions\":%d,"
             "\"master\":\"%p\",\"master8\":%u,"
             "\"immediate\":\"%p\",\"immediate8\":%u,"
             "\"pid\":%d}\n",
            vertex_file.UTF8String ?: "",
            binding_log, call->count, call->instance_count, inputs->target,
            inputs->target_width, inputs->target_height, inputs->viewport_x,
            inputs->viewport_y, inputs->viewport_width, inputs->viewport_height,
            viewport_x, viewport_y, viewport_width, viewport_height,
            (int)primitive,
            g_logged_positions[0], g_logged_positions[1], g_logged_positions[2],
            g_logged_positions[3], g_logged_positions[4], g_logged_positions[5],
            g_logged_positions[6], g_logged_positions[7], g_logged_positions[8],
            g_logged_positions[9], g_logged_positions[10], g_logged_positions[11],
            g_logged_positions[12], g_logged_positions[13], g_logged_positions[14],
            g_logged_positions[15], g_have_positions,
            (void *)master,
            master != NULL ? *(const uint32_t *)((const uint8_t *)master + 8) : 0,
            eu4_gfx_immediate_master(),
            eu4_gfx_immediate_master() != NULL
                ? *(const uint32_t *)((const uint8_t *)eu4_gfx_immediate_master() + 8)
                : 0,
            (int)getpid()];
        NSString *name = [NSString stringWithFormat:@"bind-%@.json", stem];
        if (json.length > 0) {
            (void)eu4_metal_events_write(name.UTF8String, json.UTF8String);
        }
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
                                     void *command_buffer, char *reason,
                                     size_t reason_capacity,
                                     eu4_gfx_simple_draw_state *state) {
    @try {
        return generic_draw_encode_impl(immediate_context, call, inputs,
                                        command_buffer, reason, reason_capacity,
                                        state);
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
        if (reason != NULL && reason_capacity > 0) {
            snprintf(reason, reason_capacity,
                     "Objective-C exception in the generic encoder");
        }
        return 0;
    }
}
