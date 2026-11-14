/* Offline complete-frame Metal replay.
 *
 * Reads one authentic original-OpenGL frame trace (frame-events.jsonl), the
 * translated Metal Shading Language sources (shaders.json) and executes the
 * frame's draw sequence through Metal in order, one render pass per event,
 * preserving render-target contents. The default framebuffer is read back for
 * the differential.
 *
 * This is a replay boundary, not a second renderer: every input comes from the
 * captured original GL state.
 */

#import <Foundation/Foundation.h>
#import <Metal/Metal.h>

#include <stdio.h>
#include <string.h>
#include <stdlib.h>

static NSString *const kErrorDomain = @"eu4.frame.replay";

static id<MTLDevice> g_device;
static id<MTLCommandQueue> g_queue;
static NSMutableDictionary<NSString *, id> *g_pipelines;
static NSMutableDictionary<NSString *, id> *g_functions;
static NSMutableDictionary<NSString *, id> *g_textures;
static NSMutableDictionary<NSString *, id> *g_samplers;
static NSMutableDictionary<NSNumber *, NSMutableDictionary *> *g_targets;
static NSMutableDictionary<NSString *, NSMutableDictionary *> *g_render_targets;
static NSMutableDictionary<NSString *, id> *g_alpha_textures;
/* Metal's vertex buffer table is shared with vertex-stage constant buffers, so
   vertex streams start above the translated uniform block indices. */
static const NSUInteger kVertexBufferBase = 8;
static id<MTLComputePipelineState> g_alpha_pipeline;
static id<MTLFunction> g_debug_fragment;
static id<MTLFunction> g_debug_vertex;
static id<MTLFunction> g_debug_uv_fragment;
static id<MTLFunction> g_debug_attr_vertex;
static id<MTLFunction> g_debug_texture_fragment;

static NSString *const kDebugTextureMSL =
    @"#include <metal_stdlib>\n"
     "using namespace metal;\n"
     "struct TexOut { float4 color [[color(0)]]; };\n"
     "fragment TexOut debug_texture(texture2d<float> tex0 [[texture(0)]],\n"
     "                              sampler tex0Smplr [[sampler(0)]]) {\n"
     "    TexOut out; out.color = tex0.sample(tex0Smplr, float2(0.25, 0.5));\n"
     "    return out;\n"
     "}\n";

static NSString *const kDebugAttrVertexMSL =
    @"#include <metal_stdlib>\n"
     "using namespace metal;\n"
     "struct AttrIn { float3 position [[attribute(0)]]; };\n"
     "struct AttrOut { float4 position [[position]]; };\n"
     "vertex AttrOut debug_attr_vertex(AttrIn in [[stage_in]]) {\n"
     "    AttrOut out; out.position = float4(in.position.x / 100.0, in.position.y / 100.0, 0.0, 1.0);\n"
     "    return out;\n"
     "}\n";

static NSString *const kDebugUVMSL =
    @"#include <metal_stdlib>\n"
     "using namespace metal;\n"
     "struct UVOut { float4 color [[color(0)]]; };\n"
     "struct UVIn { float2 uv [[user(locn0)]]; };\n"
     "fragment UVOut debug_uv(UVIn in [[stage_in]]) {\n"
     "    UVOut out; out.color = float4(in.uv, 0.5, 1.0); return out;\n"
     "}\n";

static NSString *const kDebugVertexMSL =
    @"#include <metal_stdlib>\n"
     "using namespace metal;\n"
     "struct DebugVOut { float4 position [[position]]; };\n"
     "vertex DebugVOut debug_vertex(uint vid [[vertex_id]]) {\n"
     "    float2 positions[4] = { float2(-1.0, -1.0), float2(1.0, -1.0),\n"
     "                            float2(-1.0, 1.0), float2(1.0, 1.0) };\n"
     "    DebugVOut out; out.position = float4(positions[vid & 3], 0.0, 1.0);\n"
     "    return out;\n"
     "}\n";

static NSString *const kDebugFragmentMSL =
    @"#include <metal_stdlib>\n"
     "using namespace metal;\n"
     "struct DebugOut { float4 color [[color(0)]]; };\n"
     "fragment DebugOut debug_fragment() {\n"
     "    DebugOut out; out.color = float4(1.0, 0.0, 1.0, 1.0); return out;\n"
     "}\n";

static NSString *const kAlphaKernelMSL =
    @"#include <metal_stdlib>\n"
     "using namespace metal;\n"
     "kernel void force_alpha(texture2d<float, access::read> source [[texture(0)]],\n"
     "                        texture2d<float, access::write> destination [[texture(1)]],\n"
     "                        uint2 gid [[thread_position_in_grid]]) {\n"
     "    float4 value = source.read(gid);\n"
     "    destination.write(float4(value.rgb, 1.0), gid);\n"
     "}\n";

static id<MTLComputePipelineState> alpha_pipeline(NSError **error) {
    if (g_alpha_pipeline != nil) {
        return g_alpha_pipeline;
    }
    NSError *compile_error = nil;
    id<MTLLibrary> library = [g_device newLibraryWithSource:kAlphaKernelMSL
                                                    options:nil
                                                      error:&compile_error];
    if (library == nil) {
        if (error != NULL) {
            *error = compile_error;
        }
        return nil;
    }
    id<MTLFunction> function = [library newFunctionWithName:@"force_alpha"];
    if (function == nil) {
        return nil;
    }
    g_alpha_pipeline = [g_device newComputePipelineStateWithFunction:function
                                                               error:&compile_error];
    if (g_alpha_pipeline == nil && error != NULL) {
        *error = compile_error;
    }
    return g_alpha_pipeline;
}

/* Samples of a GL RGB render target expose alpha = 1.0 because the texture has
 * no alpha channel. Metal has no RGB8 render target format, so the target is
 * allocated as BGRA8; before it is sampled, its contents are copied through a
 * compute kernel that forces alpha to 1.0. */
static id<MTLTexture> alpha_normalized(id<MTLTexture> source,
                                       NSNumber *generation,
                                       id<MTLCommandBuffer> command,
                                       NSError **error) {
    if (source == nil) {
        return nil;
    }
    NSString *key = [NSString stringWithFormat:@"%p-%@", source, generation];
    id<MTLTexture> cached = g_alpha_textures[key];
    if (cached != nil) {
        return cached;
    }
    MTLTextureDescriptor *descriptor = [MTLTextureDescriptor new];
    descriptor.textureType = MTLTextureType2D;
    descriptor.pixelFormat = MTLPixelFormatRGBA8Unorm;
    descriptor.width = source.width;
    descriptor.height = source.height;
    descriptor.usage = MTLTextureUsageShaderRead | MTLTextureUsageShaderWrite;
    descriptor.storageMode = MTLStorageModePrivate;
    id<MTLTexture> destination = [g_device newTextureWithDescriptor:descriptor];
    id<MTLComputePipelineState> pipeline = alpha_pipeline(error);
    if (destination == nil || pipeline == nil) {
        return nil;
    }
    /* The caller may already have an active render encoder, so the compute
       pass owns its own command buffer. */
    id<MTLCommandBuffer> compute_command = [g_queue commandBuffer];
    id<MTLComputeCommandEncoder> encoder = [compute_command computeCommandEncoder];
    [encoder setComputePipelineState:pipeline];
    [encoder setTexture:source atIndex:0];
    [encoder setTexture:destination atIndex:1];
    MTLSize threads_per_group = MTLSizeMake(8, 8, 1);
    MTLSize groups = MTLSizeMake(
        (source.width + threads_per_group.width - 1) / threads_per_group.width,
        (source.height + threads_per_group.height - 1) / threads_per_group.height,
        1);
    [encoder dispatchThreadgroups:groups threadsPerThreadgroup:threads_per_group];
    [encoder endEncoding];
    [compute_command commit];
    [compute_command waitUntilCompleted];
    (void)command;
    g_alpha_textures[key] = destination;
    return destination;
}

static NSData *read_blob(NSString *dir, NSString *name) {
    if (name.length == 0) {
        return nil;
    }
    NSString *path = [dir stringByAppendingPathComponent:@"blobs"];
    path = [path stringByAppendingPathComponent:name];
    return [NSData dataWithContentsOfFile:path];
}

static NSData *read_file(NSString *path) {
    return [NSData dataWithContentsOfFile:path];
}

static NSArray *read_json_lines(NSString *path, NSError **error) {
    NSString *text = [NSString stringWithContentsOfFile:path
                                              encoding:NSUTF8StringEncoding
                                                 error:error];
    if (text == nil) {
        return nil;
    }
    NSMutableArray *rows = [NSMutableArray array];
    for (NSString *line in [text componentsSeparatedByString:@"\n"]) {
        if (line.length == 0) {
            continue;
        }
        NSData *data = [line dataUsingEncoding:NSUTF8StringEncoding];
        id value = [NSJSONSerialization JSONObjectWithData:data options:0 error:error];
        if (![value isKindOfClass:[NSDictionary class]]) {
            if (error != NULL && *error == nil) {
                *error = [NSError errorWithDomain:kErrorDomain code:1
                                         userInfo:@{NSLocalizedDescriptionKey:
                                                        @"frame event is not an object"}];
            }
            return nil;
        }
        [rows addObject:value];
    }
    return rows;
}

static NSDictionary *read_json(NSString *path, NSError **error) {
    NSData *data = [NSData dataWithContentsOfFile:path];
    if (data == nil) {
        if (error != NULL) {
            *error = [NSError errorWithDomain:kErrorDomain code:2
                                     userInfo:@{NSLocalizedDescriptionKey:
                                                    [@"missing " stringByAppendingString:path]}];
        }
        return nil;
    }
    id value = [NSJSONSerialization JSONObjectWithData:data options:0 error:error];
    return [value isKindOfClass:[NSDictionary class]] ? value : nil;
}

static NSUInteger number(id value, NSUInteger fallback) {
    if ([value isKindOfClass:[NSNumber class]]) {
        return (NSUInteger)[(NSNumber *)value unsignedLongLongValue];
    }
    return fallback;
}

static double double_number(id value, double fallback) {
    if ([value isKindOfClass:[NSNumber class]]) {
        return [(NSNumber *)value doubleValue];
    }
    return fallback;
}

/* GL enums are recorded as JSON strings ("0x2600") while counts and flags are
   numbers. Both forms must resolve; a string silently falling back to a default
   would select the wrong sampler or depth state. */
static NSUInteger enum_value(id value, NSUInteger fallback) {
    if ([value isKindOfClass:[NSNumber class]]) {
        return (NSUInteger)[(NSNumber *)value unsignedLongLongValue];
    }
    if ([value isKindOfClass:[NSString class]]) {
        const char *text = [(NSString *)value UTF8String];
        if (text != NULL && text[0] != 0) {
            char *end = NULL;
            unsigned long long parsed = strtoull(text, &end, 0);
            if (end != NULL && *end == 0) {
                return (NSUInteger)parsed;
            }
        }
    }
    return fallback;
}

static NSString *string_value(id value) {
    return [value isKindOfClass:[NSString class]] ? value : nil;
}

/* ------------------------------------------------------------------------- */
/* Format mapping                                                             */
/* ------------------------------------------------------------------------- */

static MTLVertexFormat vertex_format(NSUInteger gl_type, NSUInteger components,
                                     BOOL normalized, NSError **error) {
    if (gl_type == 0x1406) { /* GL_FLOAT */
        switch (components) {
            case 1: return MTLVertexFormatFloat;
            case 2: return MTLVertexFormatFloat2;
            case 3: return MTLVertexFormatFloat3;
            case 4: return MTLVertexFormatFloat4;
            default: break;
        }
    } else if (gl_type == 0x1402) { /* GL_SHORT */
        switch (components) {
            case 1: return normalized ? MTLVertexFormatShortNormalized
                                      : MTLVertexFormatShort;
            case 2: return normalized ? MTLVertexFormatShort2Normalized
                                      : MTLVertexFormatShort2;
            case 3: return normalized ? MTLVertexFormatShort3Normalized
                                      : MTLVertexFormatShort3;
            case 4: return normalized ? MTLVertexFormatShort4Normalized
                                      : MTLVertexFormatShort4;
            default: break;
        }
    } else if (gl_type == 0x1401) { /* GL_UNSIGNED_BYTE */
        switch (components) {
            case 4: return normalized ? MTLVertexFormatUChar4Normalized
                                      : MTLVertexFormatUChar4;
            case 2: return normalized ? MTLVertexFormatUChar2Normalized
                                      : MTLVertexFormatUChar2;
            case 3: return normalized ? MTLVertexFormatUChar3Normalized
                                      : MTLVertexFormatUChar3;
            case 1: return normalized ? MTLVertexFormatUCharNormalized
                                      : MTLVertexFormatUChar;
            default: break;
        }
    } else if (gl_type == 0x1403) { /* GL_UNSIGNED_SHORT */
        switch (components) {
            case 2: return normalized ? MTLVertexFormatUShort2Normalized
                                      : MTLVertexFormatUShort2;
            case 3: return normalized ? MTLVertexFormatUShort3Normalized
                                      : MTLVertexFormatUShort3;
            case 4: return normalized ? MTLVertexFormatUShort4Normalized
                                      : MTLVertexFormatUShort4;
            default: break;
        }
    } else if (gl_type == 0x140B) { /* GL_HALF_FLOAT */
        switch (components) {
            case 2: return MTLVertexFormatHalf2;
            case 3: return MTLVertexFormatHalf3;
            case 4: return MTLVertexFormatHalf4;
            case 1: return MTLVertexFormatHalf;
            default: break;
        }
    }
    if (error != NULL) {
        *error = [NSError errorWithDomain:kErrorDomain code:10
                                 userInfo:@{NSLocalizedDescriptionKey:
                                                [NSString stringWithFormat:
                                                     @"unsupported vertex format type=0x%lx components=%lu normalized=%d",
                                                     (unsigned long)gl_type,
                                                     (unsigned long)components,
                                                     (int)normalized]}];
    }
    return (MTLVertexFormat)0;
}

static MTLBlendFactor blend_factor(NSUInteger gl, NSError **error) {
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
        default: break;
    }
    if (error != NULL) {
        *error = [NSError errorWithDomain:kErrorDomain code:11
                                 userInfo:@{NSLocalizedDescriptionKey:
                                                [NSString stringWithFormat:
                                                     @"unsupported blend factor 0x%lx",
                                                     (unsigned long)gl]}];
    }
    return (MTLBlendFactor)0;
}

static MTLBlendOperation blend_operation(NSUInteger gl, NSError **error) {
    switch (gl) {
        case 0x8006: return MTLBlendOperationAdd;
        case 0x800A: return MTLBlendOperationSubtract;
        case 0x800B: return MTLBlendOperationReverseSubtract;
        case 0x8007: return MTLBlendOperationMin;
        case 0x8008: return MTLBlendOperationMax;
        default: break;
    }
    if (error != NULL) {
        *error = [NSError errorWithDomain:kErrorDomain code:12
                                 userInfo:@{NSLocalizedDescriptionKey:
                                                [NSString stringWithFormat:
                                                     @"unsupported blend equation 0x%lx",
                                                     (unsigned long)gl]}];
    }
    return (MTLBlendOperation)0;
}

static MTLCompareFunction compare_function(NSUInteger gl) {
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

static MTLSamplerMinMagFilter min_filter(NSUInteger gl) {
    switch (gl) {
        case 0x2600: return MTLSamplerMinMagFilterNearest;
        case 0x2601: return MTLSamplerMinMagFilterLinear;
        case 0x2700: return MTLSamplerMinMagFilterNearest;
        case 0x2701: return MTLSamplerMinMagFilterLinear;
        case 0x2702: return MTLSamplerMinMagFilterNearest;
        case 0x2703: return MTLSamplerMinMagFilterLinear;
        default: return MTLSamplerMinMagFilterLinear;
    }
}

static MTLSamplerMipFilter mip_filter(NSUInteger gl) {
    switch (gl) {
        case 0x2700: return MTLSamplerMipFilterNearest;
        case 0x2701: return MTLSamplerMipFilterNearest;
        case 0x2702: return MTLSamplerMipFilterLinear;
        case 0x2703: return MTLSamplerMipFilterLinear;
        default: return MTLSamplerMipFilterNotMipmapped;
    }
}

static MTLSamplerAddressMode address_mode(NSUInteger gl) {
    switch (gl) {
        case 0x2901: return MTLSamplerAddressModeRepeat;
        case 0x8370: return MTLSamplerAddressModeMirrorRepeat;
        case 0x812F: return MTLSamplerAddressModeClampToEdge;
        case 0x812D: return MTLSamplerAddressModeClampToBorderColor;
        default: return MTLSamplerAddressModeClampToEdge;
    }
}

static MTLPrimitiveType primitive_type(NSUInteger mode, NSError **error) {
    switch (mode) {
        case 0: return MTLPrimitiveTypePoint;
        case 1: return MTLPrimitiveTypeLine;
        case 2: return MTLPrimitiveTypeLineStrip;
        case 3: return MTLPrimitiveTypeTriangleStrip;
        case 4: return MTLPrimitiveTypeTriangle;
        case 5: return MTLPrimitiveTypeTriangleStrip;
        default: break;
    }
    if (error != NULL) {
        *error = [NSError errorWithDomain:kErrorDomain code:13
                                 userInfo:@{NSLocalizedDescriptionKey:
                                                [NSString stringWithFormat:
                                                     @"unsupported primitive mode %lu",
                                                     (unsigned long)mode]}];
    }
    return MTLPrimitiveTypeTriangle;
}

/* ------------------------------------------------------------------------- */
/* Textures                                                                   */
/* ------------------------------------------------------------------------- */

static void flip_rows(const uint8_t *source, uint8_t *destination,
                      NSUInteger width, NSUInteger height,
                      NSUInteger bytes_per_pixel) {
    NSUInteger stride = width * bytes_per_pixel;
    for (NSUInteger row = 0; row < height; row++) {
        memcpy(destination + row * stride,
               source + (height - 1 - row) * stride, stride);
    }
}

static MTLPixelFormat pixel_format_for(NSString *name) {
    if ([name isEqualToString:@"BGRA8"]) {
        return MTLPixelFormatBGRA8Unorm;
    }
    if ([name isEqualToString:@"RGBA8"]) {
        return MTLPixelFormatRGBA8Unorm;
    }
    if ([name isEqualToString:@"RGBA32F"]) {
        return MTLPixelFormatRGBA32Float;
    }
    if ([name isEqualToString:@"DEPTH"]) {
        return MTLPixelFormatDepth32Float;
    }
    return MTLPixelFormatInvalid;
}

/* Creates and uploads one captured texture dictionary. All captured rows are
 * GL lower-left origin, so rows are flipped for Metal's top-left origin. */
static id<MTLTexture> build_texture(NSDictionary *texture, NSString *frameDir,
                                    NSError **error) {
    NSArray *levels = texture[@"levels"];
    if (![levels isKindOfClass:[NSArray class]] || levels.count == 0) {
        if (error != NULL) {
            *error = [NSError errorWithDomain:kErrorDomain code:20
                                     userInfo:@{NSLocalizedDescriptionKey:
                                                    @"texture has no captured levels"}];
        }
        return nil;
    }
    NSDictionary *first = levels[0];
    NSUInteger width = number(first[@"width"], 0);
    NSUInteger height = number(first[@"height"], 0);
    NSString *format = string_value(first[@"pixel_format"]);
    MTLPixelFormat pixel_format = pixel_format_for(format);
    BOOL cube = [string_value(texture[@"target"]) isEqualToString:@"cube"];
    if (pixel_format == MTLPixelFormatInvalid || width == 0 || height == 0) {
        if (error != NULL) {
            *error = [NSError errorWithDomain:kErrorDomain code:21
                                     userInfo:@{NSLocalizedDescriptionKey:
                                                    [NSString stringWithFormat:
                                                         @"unsupported texture format %@ (%lux%lu)",
                                                         format, (unsigned long)width,
                                                         (unsigned long)height]}];
        }
        return nil;
    }
    MTLTextureDescriptor *descriptor = [MTLTextureDescriptor new];
    descriptor.textureType = cube ? MTLTextureTypeCube : MTLTextureType2D;
    descriptor.pixelFormat = pixel_format;
    descriptor.width = width;
    descriptor.height = height;
    descriptor.mipmapLevelCount = levels.count;
    descriptor.usage = MTLTextureUsageShaderRead;
    descriptor.storageMode = MTLStorageModeManaged;
    id<MTLTexture> texture_handle = [g_device newTextureWithDescriptor:descriptor];
    if (texture_handle == nil) {
        if (error != NULL) {
            *error = [NSError errorWithDomain:kErrorDomain code:22
                                     userInfo:@{NSLocalizedDescriptionKey:
                                                    @"newTextureWithDescriptor failed"}];
        }
        return nil;
    }
    NSUInteger bytes_per_pixel = [format isEqualToString:@"RGBA32F"] ? 16 : 4;
    for (NSDictionary *level in levels) {
        NSUInteger level_index = number(level[@"level"], 0);
        NSUInteger level_width = number(level[@"width"], width);
        NSUInteger level_height = number(level[@"height"], height);
        NSUInteger level_bytes = number(level[@"bytes"], 0);
        NSMutableArray *face_files = [NSMutableArray array];
        if ([level[@"faces"] isKindOfClass:[NSArray class]]) {
            for (NSDictionary *face in level[@"faces"]) {
                [face_files addObject:face];
            }
        }
        if (face_files.count == 0) {
            [face_files addObject:level];
        }
        for (NSUInteger face = 0; face < face_files.count; face++) {
            NSDictionary *entry = face_files[face];
            NSData *blob = read_blob(frameDir, string_value(entry[@"file"]));
            if (blob == nil) {
                if (error != NULL) {
                    *error = [NSError errorWithDomain:kErrorDomain code:23
                                             userInfo:@{NSLocalizedDescriptionKey:
                                                            @"texture blob is missing"}];
                }
                return nil;
            }
            NSUInteger expected = level_width * level_height * bytes_per_pixel;
            if (blob.length != expected || (level_bytes != 0 &&
                                            level_bytes % face_files.count == 0 &&
                                            level_bytes / face_files.count != expected)) {
                if (error != NULL) {
                    *error = [NSError errorWithDomain:kErrorDomain code:24
                                             userInfo:@{NSLocalizedDescriptionKey:
                                                            [NSString stringWithFormat:
                                                                 @"texture level %lu size mismatch (%lu != %lu)",
                                                                 (unsigned long)level_index,
                                                                 (unsigned long)blob.length,
                                                                 (unsigned long)expected]}];
                }
                return nil;
            }
            /* No row flip. GL and Metal both address the first uploaded row as
               texture coordinate 0; the captured UVs were authored for that
               convention, so the same bytes must be uploaded unchanged (this is
               the convention the M5 sprite replay qualified exactly). */
            MTLRegion region = MTLRegionMake2D(0, 0, level_width, level_height);
            if (cube) {
                [texture_handle replaceRegion:region
                                 mipmapLevel:level_index
                                       slice:face
                                   withBytes:blob.bytes
                                 bytesPerRow:level_width * bytes_per_pixel
                               bytesPerImage:level_width * level_height * bytes_per_pixel];
            } else {
                [texture_handle replaceRegion:region
                                 mipmapLevel:level_index
                                   withBytes:blob.bytes
                                 bytesPerRow:level_width * bytes_per_pixel];
            }
        }
    }
    return texture_handle;
}

static NSString *texture_cache_key(NSDictionary *texture) {
    NSMutableString *key = [NSMutableString string];
    [key appendFormat:@"%@-%@-%lu", string_value(texture[@"name"]) ?: @"",
                       string_value(texture[@"target"]) ?: @"",
                       (unsigned long)number(texture[@"width"], 0)];
    for (NSDictionary *level in texture[@"levels"] ?: @[]) {
        [key appendFormat:@"-%@", string_value(level[@"sha256"]) ?: @""];
        for (NSDictionary *face in level[@"faces"] ?: @[]) {
            [key appendFormat:@".%@", string_value(face[@"sha256"]) ?: @""];
        }
    }
    return key;
}

/* ------------------------------------------------------------------------- */
/* Render targets                                                             */
/* ------------------------------------------------------------------------- */

typedef struct {
    __unsafe_unretained id<MTLTexture> color;
    __unsafe_unretained id<MTLTexture> depth;
    NSUInteger width;
    NSUInteger height;
} FrameTarget;

static FrameTarget g_default_target;

static NSMutableDictionary *target_for(NSDictionary *target, NSError **error) {
    NSNumber *key = @(number(target[@"framebuffer"], 0));
    NSMutableDictionary *existing = g_targets[key];
    if (existing != nil) {
        return existing;
    }
    NSUInteger framebuffer = [key unsignedIntegerValue];
    NSUInteger width = number(target[@"width"], 0);
    NSUInteger height = number(target[@"height"], 0);
    NSDictionary *attachment = target[@"color_attachment"];
    NSString *format_name = attachment ? string_value(attachment[@"internal_format"]) : nil;
    MTLPixelFormat pixel_format = MTLPixelFormatBGRA8Unorm;
    if (format_name != nil) {
        if ([format_name isEqualToString:@"RGBA8"] ||
            [format_name isEqualToString:@"other"]) {
            pixel_format = MTLPixelFormatRGBA8Unorm;
        } else if ([format_name isEqualToString:@"RGB8"] ||
                   [format_name isEqualToString:@"SRGB8_ALPHA8"]) {
            pixel_format = MTLPixelFormatBGRA8Unorm;
        }
    }
    if (width == 0 || height == 0) {
        width = g_default_target.width;
        height = g_default_target.height;
    }
    MTLTextureDescriptor *color_descriptor = [MTLTextureDescriptor new];
    color_descriptor.textureType = MTLTextureType2D;
    color_descriptor.pixelFormat = pixel_format;
    color_descriptor.width = width;
    color_descriptor.height = height;
    color_descriptor.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead;
    color_descriptor.storageMode = MTLStorageModePrivate;
    id<MTLTexture> color = [g_device newTextureWithDescriptor:color_descriptor];
    id<MTLTexture> depth = nil;
    if (target[@"depth_attachment"] != nil || framebuffer == 0) {
        MTLTextureDescriptor *depth_descriptor = [MTLTextureDescriptor new];
        depth_descriptor.textureType = MTLTextureType2D;
        depth_descriptor.pixelFormat = MTLPixelFormatDepth32Float;
        depth_descriptor.width = width;
        depth_descriptor.height = height;
        depth_descriptor.usage = MTLTextureUsageRenderTarget;
        depth_descriptor.storageMode = MTLStorageModePrivate;
        depth = [g_device newTextureWithDescriptor:depth_descriptor];
    }
    if (color == nil) {
        if (error != NULL) {
            *error = [NSError errorWithDomain:kErrorDomain code:30
                                     userInfo:@{NSLocalizedDescriptionKey:
                                                    @"render target allocation failed"}];
        }
        return nil;
    }
    NSMutableDictionary *value = [@{
        @"color": color,
        @"width": @(width),
        @"height": @(height),
        @"format": format_name ?: @"BGRA8",
        @"framebuffer": key,
        @"generation": @0,
    } mutableCopy];
    if (depth != nil) {
        value[@"depth"] = depth;
    }
    g_targets[key] = value;
    if (framebuffer == 0) {
        g_default_target.color = color;
        g_default_target.depth = depth;
        g_default_target.width = width;
        g_default_target.height = height;
    }
    return value;
}

/* ------------------------------------------------------------------------- */
/* Uniform packing                                                            */
/* ------------------------------------------------------------------------- */

static NSUInteger uniform_size(NSDictionary *member) {
    NSUInteger components = number(member[@"components"], 0);
    NSUInteger elements = MAX((NSUInteger)1, number(member[@"elements"], 1));
    return components * elements * 4;
}

static NSData *pack_uniforms(NSDictionary *stage, NSDictionary *program,
                             NSString *frameDir, NSError **error) {
    NSArray *members = stage[@"uniforms"] ?: @[];
    {
        NSMutableString *names = [NSMutableString string];
        for (NSDictionary *member in members) {
            [names appendFormat:@"%@@%@ ", member[@"name"], member[@"offset"]];
        }
    }
    if (members.count == 0) {
        return [NSData data];
    }
    NSUInteger size = 0;
    for (NSDictionary *member in members) {
        NSUInteger end = number(member[@"offset"], 0) + uniform_size(member);
        size = MAX(size, end);
    }
    size = (size + 15) / 16 * 16;
    if (size == 0) {
        size = 16;
    }
    NSMutableData *buffer = [NSMutableData dataWithLength:size];
    NSMutableDictionary *values = [NSMutableDictionary dictionary];
    for (NSDictionary *uniform in program[@"uniforms"] ?: @[]) {
        NSString *name = string_value(uniform[@"name"]);
        if (name.length == 0 || [uniform[@"type"] isEqualToString:@"sampler"]) {
            continue;
        }
        /* GL reports array uniforms as NAME[0]; the block member is NAME. */
        NSRange bracket = [name rangeOfString:@"[0]"];
        if (bracket.location != NSNotFound && bracket.location + 3 == name.length) {
            name = [name substringToIndex:bracket.location];
        }
        values[name] = uniform;
    }
    for (NSDictionary *member in members) {
        NSString *name = string_value(member[@"name"]);
        NSDictionary *uniform = values[name];
        if (uniform == nil) {
            continue;
        }
        NSData *blob = read_blob(frameDir, string_value(uniform[@"file"]));
        if (blob == nil) {
            if (error != NULL) {
                *error = [NSError errorWithDomain:kErrorDomain code:40
                                         userInfo:@{NSLocalizedDescriptionKey:
                                                        [@"uniform blob missing: "
                                                            stringByAppendingString:name]}];
            }
            return nil;
        }
        NSUInteger copy = MIN(blob.length, uniform_size(member));
        memcpy((uint8_t *)buffer.mutableBytes + number(member[@"offset"], 0),
               blob.bytes, copy);
    }
    return buffer;
}

/* ------------------------------------------------------------------------- */
/* Shader libraries                                                           */
/* ------------------------------------------------------------------------- */

static id<MTLFunction> function_for(NSString *shaderDir, NSDictionary *stage,
                                    NSError **error) {
    NSString *file = string_value(stage[@"msl_file"]);
    NSString *entry = string_value(stage[@"msl_entry"]);
    if (file.length == 0 || entry.length == 0) {
        if (error != NULL) {
            *error = [NSError errorWithDomain:kErrorDomain code:50
                                     userInfo:@{NSLocalizedDescriptionKey:
                                                    @"stage metadata incomplete"}];
        }
        return nil;
    }
    NSString *key = [file stringByAppendingFormat:@"#%@", entry];
    id<MTLFunction> cached = g_functions[key];
    if (cached != nil) {
        return cached;
    }
    NSString *path = [shaderDir stringByAppendingPathComponent:file];
    NSError *read_error = nil;
    NSString *source = [NSString stringWithContentsOfFile:path
                                                encoding:NSUTF8StringEncoding
                                                   error:&read_error];
    if (source == nil) {
        if (error != NULL) {
            *error = read_error ?: [NSError errorWithDomain:kErrorDomain code:51
                                                    userInfo:@{NSLocalizedDescriptionKey:
                                                                   @"MSL source unreadable"}];
        }
        return nil;
    }
    MTLCompileOptions *options = [MTLCompileOptions new];
    options.languageVersion = MTLLanguageVersion2_1;
    id<MTLLibrary> library = [g_device newLibraryWithSource:source
                                                    options:options
                                                      error:&read_error];
    if (library == nil) {
        if (error != NULL) {
            *error = read_error ?: [NSError errorWithDomain:kErrorDomain code:52
                                                    userInfo:@{NSLocalizedDescriptionKey:
                                                                   @"MSL compile failed"}];
        }
        return nil;
    }
    id<MTLFunction> function = [library newFunctionWithName:entry];
    if (function == nil) {
        if (error != NULL) {
            *error = [NSError errorWithDomain:kErrorDomain code:53
                                     userInfo:@{NSLocalizedDescriptionKey:
                                                    [@"MSL entry missing: " stringByAppendingString:entry]}];
        }
        return nil;
    }
    g_functions[key] = function;
    return function;
}

/* ------------------------------------------------------------------------- */
/* Draw execution                                                             */
/* ------------------------------------------------------------------------- */

static NSString *state_key(NSDictionary *event, NSDictionary *target,
                           NSArray *attribute_plan) {
    NSMutableString *key = [NSMutableString string];
    NSDictionary *state = event[@"state"] ?: @{};
    NSDictionary *blend = state[@"blend"] ?: @{};
    NSDictionary *depth = state[@"depth"] ?: @{};
    NSDictionary *cull = state[@"cull"] ?: @{};
    [key appendFormat:@"b%@-%@-%@-%@-%@-%@-%@|",
                      blend[@"enabled"], blend[@"src_rgb"], blend[@"dst_rgb"],
                      blend[@"src_alpha"], blend[@"dst_alpha"],
                      blend[@"equation_rgb"], blend[@"equation_alpha"]];
    [key appendFormat:@"d%@-%@-%@|", depth[@"test"], depth[@"func"], depth[@"write"]];
    [key appendFormat:@"c%@-%@-%@|", cull[@"enabled"], cull[@"mode"], cull[@"front"]];
    [key appendFormat:@"m%@|", state[@"color_mask"]];
    [key appendFormat:@"t%lu-%@-%@|", (unsigned long)number(target[@"width"], 0),
                      target[@"format"] ?: @"", target[@"depth"] ? @"d" : @"-"];
    for (NSDictionary *attribute in attribute_plan) {
        [key appendFormat:@"a%@-%@-%@-%@-%@-%@-", attribute[@"location"],
                          attribute[@"components"], attribute[@"gl_type"],
                          attribute[@"normalized"], attribute[@"stride"],
                          attribute[@"divisor"] ?: @0];
    }
    return key;
}

static BOOL encode_draw(NSDictionary *event, NSDictionary *program_entry,
                        NSString *frameDir, NSString *shaderDir,
                        id<MTLCommandBuffer> command, NSError **error) {
    @autoreleasepool {
        NSDictionary *program = event[@"program"] ?: @{};
        NSDictionary *stages = program_entry[@"stages"] ?: @{};
        NSDictionary *vs = stages[@"vertex"];
        NSDictionary *fs = stages[@"pixel"];
        if (vs == nil || fs == nil) {
            if (error != NULL) {
                *error = [NSError errorWithDomain:kErrorDomain code:60
                                         userInfo:@{NSLocalizedDescriptionKey:
                                                        @"translated stages missing"}];
            }
            return NO;
        }
        id<MTLFunction> vertex_function = function_for(shaderDir, vs, error);
        id<MTLFunction> fragment_function = function_for(shaderDir, fs, error);
        const char *debug_attr_vertex_env = getenv("EU4_FRAME_REPLAY_DEBUG_ATTR_VERTEX");
        if (debug_attr_vertex_env != NULL && debug_attr_vertex_env[0] == '1') {
            if (g_debug_attr_vertex == nil) {
                NSError *debug_error = nil;
                id<MTLLibrary> library = [g_device newLibraryWithSource:kDebugAttrVertexMSL
                                                                options:nil
                                                                  error:&debug_error];
                g_debug_attr_vertex = [library newFunctionWithName:@"debug_attr_vertex"];
            }
            if (g_debug_attr_vertex != nil) {
                vertex_function = g_debug_attr_vertex;
            }
        }
        const char *debug_vertex_env = getenv("EU4_FRAME_REPLAY_DEBUG_VERTEX");
        if (debug_vertex_env != NULL && debug_vertex_env[0] == '1') {
            if (g_debug_vertex == nil) {
                NSError *debug_error = nil;
                id<MTLLibrary> library = [g_device newLibraryWithSource:kDebugVertexMSL
                                                                options:nil
                                                                  error:&debug_error];
                g_debug_vertex = [library newFunctionWithName:@"debug_vertex"];
            }
            if (g_debug_vertex != nil) {
                vertex_function = g_debug_vertex;
            }
        }
        const char *debug_fragment_env = getenv("EU4_FRAME_REPLAY_DEBUG_FRAGMENT");
        if (debug_fragment_env != NULL && strcmp(debug_fragment_env, "texture") == 0) {
            if (g_debug_texture_fragment == nil) {
                NSError *debug_error = nil;
                id<MTLLibrary> library = [g_device newLibraryWithSource:kDebugTextureMSL
                                                                options:nil
                                                                  error:&debug_error];
                g_debug_texture_fragment = [library newFunctionWithName:@"debug_texture"];
            }
            if (g_debug_texture_fragment != nil) {
                fragment_function = g_debug_texture_fragment;
            }
        } else if (debug_fragment_env != NULL && strcmp(debug_fragment_env, "uv") == 0) {
            if (g_debug_uv_fragment == nil) {
                NSError *debug_error = nil;
                id<MTLLibrary> library = [g_device newLibraryWithSource:kDebugUVMSL
                                                                options:nil
                                                                  error:&debug_error];
                g_debug_uv_fragment = [library newFunctionWithName:@"debug_uv"];
            }
            if (g_debug_uv_fragment != nil) {
                fragment_function = g_debug_uv_fragment;
            }
        } else if (debug_fragment_env != NULL && debug_fragment_env[0] == '1') {
            if (g_debug_fragment == nil) {
                NSError *debug_error = nil;
                id<MTLLibrary> library = [g_device newLibraryWithSource:kDebugFragmentMSL
                                                                options:nil
                                                                  error:&debug_error];
                g_debug_fragment = [library newFunctionWithName:@"debug_fragment"];
            }
            if (g_debug_fragment != nil) {
                fragment_function = g_debug_fragment;
            }
        }
        if (vertex_function == nil || fragment_function == nil) {
            return NO;
        }
        NSDictionary *draw_info = event[@"draw"] ?: @{};
        BOOL instanced_draw = number(draw_info[@"instance_count"], 1) > 1;
        NSDictionary *state = event[@"state"] ?: @{};
        NSDictionary *blend = state[@"blend"] ?: @{};
        NSDictionary *depth_state = state[@"depth"] ?: @{};
        NSDictionary *cull = state[@"cull"] ?: @{};
        BOOL blend_enabled = number(blend[@"enabled"], 0) != 0;
        BOOL depth_test = number(depth_state[@"test"], 0) != 0;
        BOOL depth_write = number(depth_state[@"write"], 0) != 0;
        BOOL cull_enabled = number(cull[@"enabled"], 0) != 0;
        NSMutableDictionary *target_info = target_for(event[@"target"] ?: @{}, error);
        if (target_info == nil) {
            return NO;
        }
        target_info[@"generation"] = @(number(target_info[@"generation"], 0) + 1);
        MTLTextureDescriptor *color_descriptor = [MTLTextureDescriptor new];
        color_descriptor.pixelFormat =
            ((id<MTLTexture>)target_info[@"color"]).pixelFormat;

        NSArray *attribute_list = event[@"attributes"] ?: @[];
        NSMutableArray *attribute_plan = [NSMutableArray array];
        NSMutableDictionary *buffers = [NSMutableDictionary dictionary];
        MTLVertexDescriptor *vertex_descriptor = [MTLVertexDescriptor vertexDescriptor];
        /* Declared attribute types, so constant (unbound) attributes get a
           format that matches the MSL stage_in type. */
        NSMutableDictionary *declared_components = [NSMutableDictionary dictionary];
        for (NSDictionary *declared in vs[@"attributes"] ?: @[]) {
            NSString *type = string_value(declared[@"type"]) ?: @"float";
            NSUInteger components = 1;
            if ([type isEqualToString:@"vec2"] || [type isEqualToString:@"float2"]) components = 2;
            else if ([type isEqualToString:@"vec3"] || [type isEqualToString:@"float3"]) components = 3;
            else if ([type isEqualToString:@"vec4"] || [type isEqualToString:@"float4"]) components = 4;
            declared_components[string_value(declared[@"name"]) ?: @""] = @(components);
        }
        NSMutableSet *captured_locations = [NSMutableSet set];
        for (NSDictionary *attribute in attribute_list) {
            [captured_locations addObject:@(number(attribute[@"location"], 0))];
        }
        NSDictionary *declared_locations = vs[@"attribute_locations"] ?: @{};
        NSMutableArray *constant_locations = [NSMutableArray array];
        for (NSString *name in declared_locations) {
            NSNumber *location = declared_locations[name];
            if ([captured_locations containsObject:location]) {
                continue;
            }
            [constant_locations addObject:@[name, location]];
        }
        [constant_locations sortUsingComparator:^NSComparisonResult(id a, id b) {
            return [a[1] compare:b[1]];
        }];
        for (NSArray *item in constant_locations) {
            NSString *name = item[0];
            NSUInteger location = [item[1] unsignedIntegerValue];
            NSUInteger components = [declared_components[name] unsignedIntegerValue];
            if (components == 0) {
                components = 4;
            }
            float constant_value[4] = {0.0f, 0.0f, 0.0f, 1.0f};
            id<MTLBuffer> buffer = [g_device newBufferWithBytes:constant_value
                                                         length:sizeof(constant_value)
                                                        options:MTLResourceStorageModeManaged];
            buffers[@(location)] = buffer;
            MTLVertexFormat format = vertex_format(0x1406, components, NO, error);
            if (format == 0) {
                return NO;
            }
            MTLVertexAttributeDescriptor *descriptor = vertex_descriptor.attributes[location];
            descriptor.format = format;
            descriptor.offset = 0;
            descriptor.bufferIndex = kVertexBufferBase + location;
            vertex_descriptor.layouts[kVertexBufferBase + location].stride = 16;
            vertex_descriptor.layouts[kVertexBufferBase + location].stepFunction =
                MTLVertexStepFunctionPerInstance;
            vertex_descriptor.layouts[kVertexBufferBase + location].stepRate = 1;
            [attribute_plan addObject:@{
                @"location": @(location),
                @"components": @(components),
                @"gl_type": @(0x1406),
                @"normalized": @NO,
                @"stride": @16,
                @"constant": @YES,
            }];
        }
        for (NSDictionary *attribute in attribute_list) {
            BOOL attribute_enabled = number(attribute[@"enabled"], 1) != 0;
            if (!attribute_enabled) {
                /* GL supplies the current generic attribute value; it is not a
                   vertex array. Replay it as a per-instance constant. */
                NSUInteger location = number(attribute[@"location"], 0);
                NSArray *constant_values = attribute[@"constant_value"];
                float constant_value[4] = {0.0f, 0.0f, 0.0f, 1.0f};
                if ([constant_values isKindOfClass:[NSArray class]]) {
                    for (NSUInteger i = 0; i < 4 && i < constant_values.count; i++) {
                        constant_value[i] = (float)double_number(constant_values[i], constant_value[i]);
                    }
                }
                NSString *declared_name = string_value(attribute[@"name"]) ?: @"";
                NSUInteger components =
                    [declared_components[declared_name] unsignedIntegerValue];
                if (components == 0) {
                    components = number(attribute[@"components"], 4);
                }
                id<MTLBuffer> buffer = [g_device newBufferWithBytes:constant_value
                                                             length:sizeof(constant_value)
                                                            options:MTLResourceStorageModeManaged];
                buffers[@(location)] = buffer;
                MTLVertexFormat format = vertex_format(0x1406, components, NO, error);
                if (format == 0) {
                    return NO;
                }
                MTLVertexAttributeDescriptor *descriptor =
                    vertex_descriptor.attributes[location];
                descriptor.format = format;
                descriptor.offset = 0;
                descriptor.bufferIndex = kVertexBufferBase + location;
                vertex_descriptor.layouts[kVertexBufferBase + location].stride = 16;
                vertex_descriptor.layouts[kVertexBufferBase + location].stepFunction =
                    MTLVertexStepFunctionPerInstance;
                vertex_descriptor.layouts[kVertexBufferBase + location].stepRate = instanced_draw ? 0 : 1;
                [attribute_plan addObject:@{
                    @"location": @(location),
                    @"components": @(components),
                    @"gl_type": @(0x1406),
                    @"normalized": @NO,
                    @"stride": @16,
                    @"constant": @YES,
                    @"divisor": @0,
                }];
                continue;
            }
            NSString *data_file = string_value(attribute[@"data"]);
            NSData *data = read_blob(frameDir, data_file);
            if (data == nil || data.length == 0) {
                if (error != NULL) {
                    *error = [NSError errorWithDomain:kErrorDomain code:61
                                             userInfo:@{NSLocalizedDescriptionKey:
                                                            [NSString stringWithFormat:
                                                                 @"vertex data missing for attribute %@",
                                                                 attribute[@"name"]]}];
                }
                return NO;
            }
            NSUInteger location = number(attribute[@"location"], 0);
            NSUInteger components = number(attribute[@"components"], 4);
            NSUInteger gl_type = (NSUInteger)strtoul(
                [string_value(attribute[@"gl_type"]) ?: @"0x1406" UTF8String],
                NULL, 0);
            BOOL normalized = number(attribute[@"normalized"], 0) != 0;
            NSUInteger stride = number(attribute[@"stride"], 0);
            MTLVertexFormat format = vertex_format(gl_type, components,
                                                   normalized, error);
            if (format == 0) {
                return NO;
            }
            id<MTLBuffer> buffer = [g_device newBufferWithBytes:data.bytes
                                                         length:data.length
                                                        options:MTLResourceStorageModeManaged];
            if (buffer == nil) {
                if (error != NULL) {
                    *error = [NSError errorWithDomain:kErrorDomain code:62
                                             userInfo:@{NSLocalizedDescriptionKey:
                                                            @"vertex buffer allocation failed"}];
                }
                return NO;
            }
            buffers[@(location)] = buffer;
            if (getenv("EU4_FRAME_REPLAY_DEBUG_DESCRIPTOR") != NULL) {
                const float *raw = (const float *)data.bytes;
                fprintf(stderr, "  attr %lu data %lu bytes first %.3f %.3f %.3f %.3f\n",
                        (unsigned long)location, (unsigned long)data.length,
                        raw[0], raw[1], raw[2], raw[3]);
            }
            MTLVertexAttributeDescriptor *descriptor = vertex_descriptor.attributes[location];
            descriptor.format = format;
            descriptor.offset = 0;
            descriptor.bufferIndex = kVertexBufferBase + location;
            vertex_descriptor.layouts[kVertexBufferBase + location].stride = stride;
            NSUInteger divisor = number(attribute[@"divisor"], 0);
            if (instanced_draw && divisor > 0) {
                vertex_descriptor.layouts[kVertexBufferBase + location].stepFunction =
                    MTLVertexStepFunctionPerInstance;
                vertex_descriptor.layouts[kVertexBufferBase + location].stepRate = divisor;
            } else {
                vertex_descriptor.layouts[kVertexBufferBase + location].stepFunction =
                    MTLVertexStepFunctionPerVertex;
                vertex_descriptor.layouts[kVertexBufferBase + location].stepRate = 1;
            }
            [attribute_plan addObject:@{
                @"location": @(location),
                @"components": @(components),
                @"gl_type": @(gl_type),
                @"normalized": @(normalized),
                @"stride": @(stride),
                @"divisor": @(instanced_draw ? divisor : 0),
            }];
        }
        NSString *key = [state_key(event, target_info, attribute_plan)
            stringByAppendingFormat:@"|p%@|v%@|f%@",
            program[@"name"], vs[@"msl_file"], fs[@"msl_file"]];
        if (getenv("EU4_FRAME_REPLAY_DEBUG_DESCRIPTOR") != NULL) {
            fprintf(stderr, "debug descriptor program %s:\n",
                    [[program[@"name"] description] UTF8String]);
            for (NSNumber *location in buffers) {
                NSUInteger loc = [location unsignedIntegerValue];
                MTLVertexAttributeDescriptor *descriptor =
                    vertex_descriptor.attributes[loc];
                fprintf(stderr,
                        "  attr %lu fmt %lu off %lu buf %lu stride %lu step %lu rate %lu\n",
                        (unsigned long)loc, (unsigned long)descriptor.format,
                        (unsigned long)descriptor.offset,
                        (unsigned long)descriptor.bufferIndex,
                        (unsigned long)vertex_descriptor.layouts[descriptor.bufferIndex].stride,
                        (unsigned long)vertex_descriptor.layouts[descriptor.bufferIndex].stepFunction,
                        (unsigned long)vertex_descriptor.layouts[descriptor.bufferIndex].stepRate);
            }
            NSData *u = pack_uniforms(vs, program, frameDir, nil);
            const float *floats = (const float *)u.bytes;
            fprintf(stderr, "  uniform bytes %lu first %.4f %.4f %.4f %.4f | %.4f %.4f %.4f %.4f\n",
                    (unsigned long)u.length,
                    floats[0], floats[1], floats[2], floats[3],
                    floats[12], floats[13], floats[14], floats[15]);
        }
        id<MTLRenderPipelineState> pipeline = g_pipelines[key];
        if (pipeline == nil) {
            MTLRenderPipelineDescriptor *descriptor = [MTLRenderPipelineDescriptor new];
            descriptor.vertexFunction = vertex_function;
            descriptor.fragmentFunction = fragment_function;
            descriptor.vertexDescriptor = vertex_descriptor;
            descriptor.colorAttachments[0].pixelFormat =
                ((id<MTLTexture>)target_info[@"color"]).pixelFormat;
            if (target_info[@"depth"] != nil) {
                descriptor.depthAttachmentPixelFormat = MTLPixelFormatDepth32Float;
            }
            MTLRenderPipelineColorAttachmentDescriptor *color =
                descriptor.colorAttachments[0];
            if (blend_enabled) {
                color.blendingEnabled = YES;
                color.sourceRGBBlendFactor = blend_factor(
                    (NSUInteger)strtoul([string_value(blend[@"src_rgb"]) ?: @"0x1" UTF8String], NULL, 0),
                    error);
                color.destinationRGBBlendFactor = blend_factor(
                    (NSUInteger)strtoul([string_value(blend[@"dst_rgb"]) ?: @"0x0" UTF8String], NULL, 0),
                    error);
                color.sourceAlphaBlendFactor = blend_factor(
                    (NSUInteger)strtoul([string_value(blend[@"src_alpha"]) ?: @"0x1" UTF8String], NULL, 0),
                    error);
                color.destinationAlphaBlendFactor = blend_factor(
                    (NSUInteger)strtoul([string_value(blend[@"dst_alpha"]) ?: @"0x0" UTF8String], NULL, 0),
                    error);
                color.rgbBlendOperation = blend_operation(
                    (NSUInteger)strtoul([string_value(blend[@"equation_rgb"]) ?: @"0x8006" UTF8String], NULL, 0),
                    error);
                color.alphaBlendOperation = blend_operation(
                    (NSUInteger)strtoul([string_value(blend[@"equation_alpha"]) ?: @"0x8006" UTF8String], NULL, 0),
                    error);
                if (error != NULL && *error != nil) {
                    return NO;
                }
            }
            NSArray *mask = state[@"color_mask"];
            MTLColorWriteMask write_mask = MTLColorWriteMaskNone;
            if ([mask isKindOfClass:[NSArray class]] && mask.count == 4) {
                if (number(mask[0], 0)) write_mask |= MTLColorWriteMaskRed;
                if (number(mask[1], 0)) write_mask |= MTLColorWriteMaskGreen;
                if (number(mask[2], 0)) write_mask |= MTLColorWriteMaskBlue;
                if (number(mask[3], 0)) write_mask |= MTLColorWriteMaskAlpha;
            } else {
                write_mask = MTLColorWriteMaskAll;
            }
            color.writeMask = write_mask;
            NSError *pipeline_error = nil;
            pipeline = [g_device newRenderPipelineStateWithDescriptor:descriptor
                                                                error:&pipeline_error];
            if (pipeline == nil) {
                if (error != NULL) {
                    *error = pipeline_error ?: [NSError errorWithDomain:kErrorDomain
                                                                   code:63
                                                               userInfo:@{NSLocalizedDescriptionKey:
                                                                              @"pipeline creation failed"}];
                }
                return NO;
            }
            g_pipelines[key] = pipeline;
        }

        MTLRenderPassDescriptor *pass = [MTLRenderPassDescriptor renderPassDescriptor];
        pass.colorAttachments[0].texture = target_info[@"color"];
        pass.colorAttachments[0].loadAction = MTLLoadActionLoad;
        pass.colorAttachments[0].storeAction = MTLStoreActionStore;
        if (target_info[@"depth"] != nil) {
            pass.depthAttachment.texture = target_info[@"depth"];
            pass.depthAttachment.loadAction = MTLLoadActionLoad;
            pass.depthAttachment.storeAction = MTLStoreActionStore;
        }
        id<MTLRenderCommandEncoder> encoder = [command renderCommandEncoderWithDescriptor:pass];
        if (encoder == nil) {
            if (error != NULL) {
                *error = [NSError errorWithDomain:kErrorDomain code:64
                                         userInfo:@{NSLocalizedDescriptionKey:
                                                        @"render encoder creation failed"}];
            }
            return NO;
        }
        [encoder setRenderPipelineState:pipeline];
        NSUInteger target_width = number(target_info[@"width"], g_default_target.width);
        NSUInteger target_height = number(target_info[@"height"], g_default_target.height);
        NSArray *vp = state[@"viewport"];
        MTLViewport metal_viewport;
        if ([vp isKindOfClass:[NSArray class]] && vp.count == 4) {
            NSUInteger x = number(vp[0], 0);
            NSUInteger y = number(vp[1], 0);
            NSUInteger w = number(vp[2], target_width);
            NSUInteger h = number(vp[3], target_height);
            metal_viewport.originX = (double)x;
            metal_viewport.originY = (double)((NSInteger)target_height - (NSInteger)y - (NSInteger)h);
            metal_viewport.width = (double)w;
            metal_viewport.height = (double)h;
        } else {
            metal_viewport.originX = 0;
            metal_viewport.originY = 0;
            metal_viewport.width = (double)target_width;
            metal_viewport.height = (double)target_height;
        }
        metal_viewport.znear = 0.0;
        metal_viewport.zfar = 1.0;
        [encoder setViewport:metal_viewport];
        NSDictionary *scissor = state[@"scissor"] ?: @{};
        if (number(scissor[@"enabled"], 0) != 0) {
            NSArray *box = scissor[@"box"];
            if ([box isKindOfClass:[NSArray class]] && box.count == 4) {
                NSInteger x = (NSInteger)number(box[0], 0);
                NSInteger y = (NSInteger)number(box[1], 0);
                NSInteger w = (NSInteger)number(box[2], target_width);
                NSInteger h = (NSInteger)number(box[3], target_height);
                NSInteger top = (NSInteger)target_height - y - h;
                MTLScissorRect rect;
                rect.x = (NSUInteger)MAX((NSInteger)0, x);
                rect.y = (NSUInteger)MAX((NSInteger)0, top);
                rect.width = (NSUInteger)MAX((NSInteger)0, MIN((NSInteger)target_width - x, w));
                rect.height = (NSUInteger)MAX((NSInteger)0, MIN((NSInteger)target_height - top, h));
                [encoder setScissorRect:rect];
            }
        } else {
            MTLScissorRect rect = {0, 0, target_width, target_height};
            [encoder setScissorRect:rect];
        }
        const char *no_cull = getenv("EU4_FRAME_REPLAY_NO_CULL");
        if (cull_enabled && !(no_cull != NULL && no_cull[0] == '1')) {
            [encoder setCullMode:MTLCullModeBack];
            /* The captured GL front-face winding maps directly to Metal's
               winding: with the flipped viewport origin, a primitive the engine
               treats as front-facing is front-facing for Metal as well. This is
               validated by the per-draw differential (a complemented mapping
               culls the menu's sprite quads). */
            NSUInteger gl_front = (NSUInteger)strtoul(
                [string_value(cull[@"front"]) ?: @"0x901" UTF8String], NULL, 0);
            const char *flip = getenv("EU4_FRAME_REPLAY_FLIP_WINDING");
            if (flip != NULL && flip[0] == '1') {
                gl_front = gl_front == 0x901 ? 0x900 : 0x901;
            }
            [encoder setFrontFacingWinding:gl_front == 0x901
                       ? MTLWindingCounterClockwise
                       : MTLWindingClockwise];
        } else {
            [encoder setCullMode:MTLCullModeNone];
        }
        if (depth_test) {
            MTLDepthStencilDescriptor *depth_descriptor = [MTLDepthStencilDescriptor new];
            depth_descriptor.depthCompareFunction = compare_function(
                enum_value(depth_state[@"func"], 0x203));
            depth_descriptor.depthWriteEnabled = depth_write;
            id<MTLDepthStencilState> depth_object =
                [g_device newDepthStencilStateWithDescriptor:depth_descriptor];
            [encoder setDepthStencilState:depth_object];
        }

        NSData *vertex_uniforms = pack_uniforms(vs, program, frameDir, error);
        if (vertex_uniforms == nil) {
            [encoder endEncoding];
            return NO;
        }
        NSData *fragment_uniforms = pack_uniforms(fs, program, frameDir, error);
        if (fragment_uniforms == nil) {
            [encoder endEncoding];
            return NO;
        }
        if (number(vs[@"msl_uniform_buffer"], NSUIntegerMax) != NSUIntegerMax &&
            vertex_uniforms.length > 0) {
            [encoder setVertexBytes:vertex_uniforms.bytes
                             length:vertex_uniforms.length
                            atIndex:number(vs[@"msl_uniform_buffer"], 0)];
        }
        if (number(fs[@"msl_uniform_buffer"], NSUIntegerMax) != NSUIntegerMax &&
            fragment_uniforms.length > 0) {
            [encoder setFragmentBytes:fragment_uniforms.bytes
                               length:fragment_uniforms.length
                              atIndex:number(fs[@"msl_uniform_buffer"], 0)];
        } else if (fragment_uniforms.length > 0) {
            [encoder setFragmentBytes:fragment_uniforms.bytes
                               length:fragment_uniforms.length
                              atIndex:0];
        }

        /* Textures and samplers by MSL name. */
        NSMutableDictionary *texture_by_name = [NSMutableDictionary dictionary];
        for (NSDictionary *texture in event[@"textures"] ?: @[]) {
            texture_by_name[string_value(texture[@"sampler_name"]) ?: @""] = texture;
        }
        for (NSString *stage_name in @[@"vertex", @"pixel"]) {
            NSDictionary *stage = [stage_name isEqualToString:@"vertex"] ? vs : fs;
            for (NSDictionary *binding in stage[@"msl_samplers"] ?: @[]) {
                NSString *name = string_value(binding[@"name"]);
                NSDictionary *texture = texture_by_name[name];
                if (texture == nil) {
                    if (error != NULL) {
                        *error = [NSError errorWithDomain:kErrorDomain code:65
                                                 userInfo:@{NSLocalizedDescriptionKey:
                                                                [NSString stringWithFormat:
                                                                     @"no captured texture for sampler %@", name]}];
                    }
                    [encoder endEncoding];
                    return NO;
                }
                /* A texture that is also a render target in this frame must use
                   the live target contents so composition is preserved. */
                id<MTLTexture> metal_texture = nil;
                NSString *cache_name = string_value(texture[@"name"]);
                NSMutableDictionary *render_target = g_render_targets[cache_name];
                if (render_target != nil) {
                    NSString *format = string_value(render_target[@"format"]) ?: @"";
                    if ([format isEqualToString:@"RGB8"] ||
                        [format isEqualToString:@"SRGB8_ALPHA8"]) {
                        metal_texture = alpha_normalized(
                            render_target[@"color"],
                            render_target[@"generation"], command, error);
                    } else {
                        metal_texture = render_target[@"color"];
                    }
                } else {
                    NSString *key = texture_cache_key(texture);
                    metal_texture = g_textures[key];
                    if (metal_texture == nil) {
                        metal_texture = build_texture(texture, frameDir, error);
                        if (metal_texture == nil) {
                            [encoder endEncoding];
                            return NO;
                        }
                        g_textures[key] = metal_texture;
                    }
                }
                if ([stage_name isEqualToString:@"vertex"]) {
                    [encoder setVertexTexture:metal_texture
                                      atIndex:number(binding[@"texture_index"], 0)];
                } else {
                    [encoder setFragmentTexture:metal_texture
                                        atIndex:number(binding[@"texture_index"], 0)];
                }
                NSDictionary *sampler_state = texture[@"sampler"] ?: @{};
                NSString *sampler_key = [NSString stringWithFormat:@"%@-%@-%@-%@-%@",
                                          sampler_state[@"min"] ?: @0,
                                          sampler_state[@"mag"] ?: @0,
                                          sampler_state[@"wrap_s"] ?: @0,
                                          sampler_state[@"wrap_t"] ?: @0,
                                          sampler_state[@"lod_bias"] ?: @0];
                id<MTLSamplerState> sampler = g_samplers[sampler_key];
                if (sampler == nil) {
                    MTLSamplerDescriptor *descriptor = [MTLSamplerDescriptor new];
                    const char *linear_min = getenv("EU4_FRAME_REPLAY_LINEAR_MIN");
                    descriptor.minFilter = (linear_min != NULL && linear_min[0] == '1')
                        ? MTLSamplerMinMagFilterLinear
                        : min_filter(enum_value(sampler_state[@"min"], 0x2601));
                    descriptor.magFilter =
                        enum_value(sampler_state[@"mag"], 0x2601) == 0x2600
                            ? MTLSamplerMinMagFilterNearest
                            : MTLSamplerMinMagFilterLinear;
                    const char *linear_all = getenv("EU4_FRAME_REPLAY_LINEAR_ALL");
                    if (linear_all != NULL && linear_all[0] == '1') {
                        descriptor.minFilter = MTLSamplerMinMagFilterLinear;
                        descriptor.magFilter = MTLSamplerMinMagFilterLinear;
                    }
                    const char *nearest_all = getenv("EU4_FRAME_REPLAY_NEAREST_ALL");
                    if (nearest_all != NULL && nearest_all[0] == '1') {
                        descriptor.minFilter = MTLSamplerMinMagFilterNearest;
                        descriptor.magFilter = MTLSamplerMinMagFilterNearest;
                    }
                    descriptor.mipFilter = mip_filter(enum_value(sampler_state[@"min"], 0x2601));
                    descriptor.sAddressMode = address_mode(enum_value(sampler_state[@"wrap_s"], 0x812F));
                    descriptor.tAddressMode = address_mode(enum_value(sampler_state[@"wrap_t"], 0x812F));
                    descriptor.rAddressMode = address_mode(enum_value(sampler_state[@"wrap_r"], 0x812F));
                    descriptor.lodMinClamp = (float)double_number(sampler_state[@"min_lod"], 0);
                    descriptor.lodMaxClamp = (float)double_number(sampler_state[@"max_lod"], 1000);
                    descriptor.lodBias = (float)double_number(sampler_state[@"lod_bias"], 0);
                    if (enum_value(sampler_state[@"compare_mode"], 0) == 0x884C) {
                        descriptor.compareFunction = compare_function(
                            enum_value(sampler_state[@"compare_func"], 0x0203));
                    }
                    sampler = [g_device newSamplerStateWithDescriptor:descriptor];
                    if (sampler == nil) {
                        if (error != NULL) {
                            *error = [NSError errorWithDomain:kErrorDomain code:66
                                                     userInfo:@{NSLocalizedDescriptionKey:
                                                                    @"sampler creation failed"}];
                        }
                        [encoder endEncoding];
                        return NO;
                    }
                    g_samplers[sampler_key] = sampler;
                }
                if ([stage_name isEqualToString:@"vertex"]) {
                    [encoder setVertexSamplerState:sampler
                                           atIndex:number(binding[@"sampler_index"], 0)];
                } else {
                    [encoder setFragmentSamplerState:sampler
                                             atIndex:number(binding[@"sampler_index"], 0)];
                }
            }
        }

        for (NSNumber *location in buffers) {
            [encoder setVertexBuffer:buffers[location]
                              offset:0
                             atIndex:kVertexBufferBase + [location unsignedIntegerValue]];
        }
        NSDictionary *draw = event[@"draw"] ?: @{};
        NSUInteger mode = number(draw[@"mode"], 4);
        NSUInteger count = number(draw[@"count"], 0);
        NSUInteger base_vertex = number(draw[@"base_vertex"], 0);
        NSUInteger instances = MAX((NSUInteger)1, number(draw[@"instance_count"], 1));
        NSUInteger indexed = number(draw[@"indexed"], 0);
        NSError *primitive_error = nil;
        MTLPrimitiveType primitive = primitive_type(mode, &primitive_error);
        if (primitive_error != nil) {
            if (error != NULL) {
                *error = primitive_error;
            }
            [encoder endEncoding];
            return NO;
        }
        if (count == 0) {
            [encoder endEncoding];
            return YES;
        }
        if (indexed) {
            NSDictionary *indices = event[@"indices"] ?: @{};
            NSData *index_data = read_blob(frameDir, string_value(indices[@"file"]));
            if (index_data == nil || index_data.length < count * 2) {
                if (error != NULL) {
                    *error = [NSError errorWithDomain:kErrorDomain code:67
                                             userInfo:@{NSLocalizedDescriptionKey:
                                                            @"index buffer data missing"}];
                }
                [encoder endEncoding];
                return NO;
            }
            NSMutableData *rewritten = [NSMutableData dataWithLength:count * 4];
            uint32_t *destination = (uint32_t *)rewritten.mutableBytes;
            const uint16_t *source = (const uint16_t *)index_data.bytes;
            for (NSUInteger i = 0; i < count; i++) {
                uint32_t value = source[i];
                destination[i] = value >= base_vertex ? value - (uint32_t)base_vertex : 0;
            }
            id<MTLBuffer> index_buffer = [g_device newBufferWithBytes:rewritten.bytes
                                                               length:rewritten.length
                                                              options:MTLResourceStorageModeManaged];
            [encoder drawIndexedPrimitives:primitive
                               indexCount:count
                                indexType:MTLIndexTypeUInt32
                              indexBuffer:index_buffer
                        indexBufferOffset:0
                            instanceCount:instances];
        } else {
            [encoder drawPrimitives:primitive
                        vertexStart:0
                        vertexCount:count
                      instanceCount:instances];
        }
        [encoder endEncoding];
        return YES;
    }
}

static BOOL encode_clear(NSDictionary *event, NSString *frameDir,
                         id<MTLCommandBuffer> command, NSError **error) {
    (void)frameDir;
    NSMutableDictionary *target_info = target_for(event[@"target"] ?: @{}, error);
    if (target_info == nil) {
        return NO;
    }
    target_info[@"generation"] = @(number(target_info[@"generation"], 0) + 1);
    NSUInteger flags = number(event[@"flags"], 1);
    BOOL before_first = number(event[@"before_first_draw"], 0) != 0;
    NSArray *color = event[@"color"];
    MTLRenderPassDescriptor *pass = [MTLRenderPassDescriptor renderPassDescriptor];
    pass.colorAttachments[0].texture = target_info[@"color"];
    pass.colorAttachments[0].storeAction = MTLStoreActionStore;
    if (before_first || (flags & 1) == 0) {
        pass.colorAttachments[0].loadAction = MTLLoadActionLoad;
    } else {
        pass.colorAttachments[0].loadAction = MTLLoadActionClear;
        double r = 0, g = 0, b = 0, a = 1;
        if ([color isKindOfClass:[NSArray class]] && color.count == 4) {
            r = double_number(color[0], 0);
            g = double_number(color[1], 0);
            b = double_number(color[2], 0);
            a = double_number(color[3], 1);
        }
        pass.colorAttachments[0].clearColor = MTLClearColorMake(r, g, b, a);
    }
    if (target_info[@"depth"] != nil && (flags & 7) != 0) {
        pass.depthAttachment.texture = target_info[@"depth"];
        pass.depthAttachment.storeAction = MTLStoreActionStore;
        if ((flags & 3) != 0) {
            pass.depthAttachment.loadAction = MTLLoadActionClear;
            pass.depthAttachment.clearDepth = double_number(event[@"depth"], 1.0);
        } else {
            pass.depthAttachment.loadAction = MTLLoadActionLoad;
        }
    }
    id<MTLRenderCommandEncoder> encoder = [command renderCommandEncoderWithDescriptor:pass];
    if (encoder == nil) {
        if (error != NULL) {
            *error = [NSError errorWithDomain:kErrorDomain code:70
                                     userInfo:@{NSLocalizedDescriptionKey:
                                                    @"clear encoder creation failed"}];
        }
        return NO;
    }
    [encoder endEncoding];
    return YES;
}

/* A transfer that did not complete leaves the attachment undefined; reporting
   success would turn a harness failure into an apparent renderer difference. */
static BOOL wait_for_transfer(id<MTLCommandBuffer> buffer, NSString *operation,
                              NSError **error) {
    [buffer waitUntilCompleted];
    if (buffer.status != MTLCommandBufferStatusCompleted) {
        if (error != NULL) {
            *error = [NSError errorWithDomain:kErrorDomain code:82
                                     userInfo:@{NSLocalizedDescriptionKey:
                                                    [NSString stringWithFormat:
                                                         @"%@ did not complete (status %ld)",
                                                         operation, (long)buffer.status]}];
        }
        return NO;
    }
    return YES;
}

static BOOL upload_seed(NSString *frameDir, NSDictionary *seed,
                        NSDictionary *first_target, NSError **error) {
    if (seed == nil || first_target == nil) {
        return YES;
    }
    NSData *pixels = read_blob(frameDir, string_value(seed[@"file"]));
    if (pixels == nil) {
        if (error != NULL) {
            *error = [NSError errorWithDomain:kErrorDomain code:80
                                     userInfo:@{NSLocalizedDescriptionKey:
                                                    @"frame seed blob missing"}];
        }
        return NO;
    }
    NSMutableDictionary *target_info = target_for(first_target, error);
    if (target_info == nil) {
        return NO;
    }
    NSUInteger width = number(seed[@"width"], 0);
    NSUInteger height = number(seed[@"height"], 0);
    if (pixels.length != width * height * 4 || width == 0 || height == 0) {
        if (error != NULL) {
            *error = [NSError errorWithDomain:kErrorDomain code:81
                                     userInfo:@{NSLocalizedDescriptionKey:
                                                    @"frame seed size mismatch"}];
        }
        return NO;
    }
    id<MTLTexture> color = target_info[@"color"];
    if (color.storageMode == MTLStorageModePrivate) {
        /* Render targets are private; seed through a staging texture and a
           blit so the contents are defined before the first draw. */
        MTLTextureDescriptor *staging_descriptor = [MTLTextureDescriptor new];
        staging_descriptor.textureType = MTLTextureType2D;
        staging_descriptor.pixelFormat = color.pixelFormat;
        staging_descriptor.width = width;
        staging_descriptor.height = height;
        staging_descriptor.usage = MTLTextureUsageShaderRead;
        staging_descriptor.storageMode = MTLStorageModeManaged;
        id<MTLTexture> staging = [g_device newTextureWithDescriptor:staging_descriptor];
        uint8_t *flipped = (uint8_t *)malloc(pixels.length);
        if (staging == nil || flipped == NULL) {
            free(flipped);
            return NO;
        }
        flip_rows((const uint8_t *)pixels.bytes, flipped, width, height, 4);
        [staging replaceRegion:MTLRegionMake2D(0, 0, width, height)
                   mipmapLevel:0
                     withBytes:flipped
                   bytesPerRow:width * 4];
        free(flipped);
        id<MTLCommandBuffer> blit = [g_queue commandBuffer];
        id<MTLBlitCommandEncoder> encoder = [blit blitCommandEncoder];
        [encoder copyFromTexture:staging
                     sourceSlice:0
                     sourceLevel:0
                    sourceOrigin:MTLOriginMake(0, 0, 0)
                      sourceSize:MTLSizeMake(width, height, 1)
                       toTexture:color
                destinationSlice:0
                destinationLevel:0
               destinationOrigin:MTLOriginMake(0, 0, 0)];
        [encoder endEncoding];
        [blit commit];
        if (!wait_for_transfer(blit, @"frame seed upload", error)) {
            return NO;
        }
        return YES;
    }
    uint8_t *flipped = (uint8_t *)malloc(pixels.length);
    if (flipped == NULL) {
        return NO;
    }
    flip_rows((const uint8_t *)pixels.bytes, flipped, width, height, 4);
    [color replaceRegion:MTLRegionMake2D(0, 0, width, height)
             mipmapLevel:0
               withBytes:flipped
             bytesPerRow:width * 4];
    free(flipped);
    return YES;
}

/* Uploads a captured GL-origin (lower-left) BGRA image into a target texture. */
static BOOL upload_image_to_target(NSString *frameDir, NSDictionary *image,
                                   NSDictionary *target_info,
                                   NSError **error) {
    if (image == nil || target_info == nil) {
        return YES;
    }
    NSData *pixels = read_blob(frameDir, string_value(image[@"file"]));
    NSUInteger width = number(image[@"width"], 0);
    NSUInteger height = number(image[@"height"], 0);
    if (pixels == nil || width == 0 || height == 0 ||
        pixels.length != width * height * 4) {
        if (error != NULL) {
            *error = [NSError errorWithDomain:kErrorDomain code:100
                                     userInfo:@{NSLocalizedDescriptionKey:
                                                    @"captured image size mismatch"}];
        }
        return NO;
    }
    uint8_t *flipped = (uint8_t *)malloc(pixels.length);
    if (flipped == NULL) {
        return NO;
    }
    flip_rows((const uint8_t *)pixels.bytes, flipped, width, height, 4);
    MTLTextureDescriptor *staging_descriptor = [MTLTextureDescriptor new];
    staging_descriptor.textureType = MTLTextureType2D;
    staging_descriptor.pixelFormat = MTLPixelFormatBGRA8Unorm;
    staging_descriptor.width = width;
    staging_descriptor.height = height;
    staging_descriptor.usage = MTLTextureUsageShaderRead;
    staging_descriptor.storageMode = MTLStorageModeManaged;
    id<MTLTexture> staging = [g_device newTextureWithDescriptor:staging_descriptor];
    if (staging == nil) {
        free(flipped);
        return NO;
    }
    [staging replaceRegion:MTLRegionMake2D(0, 0, width, height)
               mipmapLevel:0
                 withBytes:flipped
               bytesPerRow:width * 4];
    free(flipped);
    id<MTLTexture> color = target_info[@"color"];
    id<MTLCommandBuffer> blit = [g_queue commandBuffer];
    id<MTLBlitCommandEncoder> encoder = [blit blitCommandEncoder];
    [encoder copyFromTexture:staging
                 sourceSlice:0
                 sourceLevel:0
                sourceOrigin:MTLOriginMake(0, 0, 0)
                  sourceSize:MTLSizeMake(MIN(width, color.width),
                                         MIN(height, color.height), 1)
                   toTexture:color
            destinationSlice:0
            destinationLevel:0
           destinationOrigin:MTLOriginMake(0, 0, 0)];
    [encoder endEncoding];
    [blit commit];
    if (!wait_for_transfer(blit, @"pre-image upload", error)) {
        return NO;
    }
    return YES;
}

/* Reads a target back and writes it in the captured GL origin convention. */
static BOOL capture_target_to_file(NSDictionary *target_info, NSString *path) {
    id<MTLTexture> color = target_info[@"color"];
    NSUInteger width = color.width;
    NSUInteger height = color.height;
    NSUInteger bytes_per_row = width * 4;
    id<MTLBuffer> buffer = [g_device newBufferWithLength:bytes_per_row * height
                                                 options:MTLResourceStorageModeShared];
    id<MTLCommandBuffer> blit = [g_queue commandBuffer];
    id<MTLBlitCommandEncoder> encoder = [blit blitCommandEncoder];
    [encoder copyFromTexture:color
                 sourceSlice:0
                 sourceLevel:0
                sourceOrigin:MTLOriginMake(0, 0, 0)
                  sourceSize:MTLSizeMake(width, height, 1)
                    toBuffer:buffer
           destinationOffset:0
      destinationBytesPerRow:bytes_per_row
    destinationBytesPerImage:bytes_per_row * height];
    [encoder endEncoding];
    [blit commit];
    [blit waitUntilCompleted];
    uint8_t *flipped = (uint8_t *)malloc(bytes_per_row * height);
    if (flipped == NULL) {
        return NO;
    }
    flip_rows((const uint8_t *)buffer.contents, flipped, width, height, 4);
    NSData *data = [NSData dataWithBytes:flipped length:bytes_per_row * height];
    free(flipped);
    return [data writeToFile:path atomically:YES];
}

/* GL_NEVER and GL_ALWAYS make the depth buffer irrelevant to the result: the
   selected draw either rejects every fragment or accepts every fragment
   regardless of the stored depth. */
static BOOL draw_depth_matters(NSDictionary *event) {
    NSDictionary *depth = (event[@"state"] ?: @{})[@"depth"] ?: @{};
    if (number(depth[@"test"], 0) == 0) {
        return NO;
    }
    NSUInteger func = enum_value(depth[@"func"], 0x203);
    return func != 0x0200 && func != 0x0207;
}

/* ------------------------------------------------------------------------- */
/* Main                                                                       */
/* ------------------------------------------------------------------------- */

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        NSString *frame_path = nil;
        NSString *shader_path = nil;
        NSString *output_path = nil;
        NSInteger stop_after = -1;
        NSInteger only_event = -1;
        BOOL seed_marked = NO;
        BOOL capture_post_images = NO;
        BOOL capture_pre_images = NO;
        BOOL depth_from_prefix = NO;
        for (int index = 1; index < argc; index++) {
            if (strcmp(argv[index], "--frame") == 0 && index + 1 < argc) {
                frame_path = @(argv[++index]);
            } else if (strcmp(argv[index], "--shaders") == 0 && index + 1 < argc) {
                shader_path = @(argv[++index]);
            } else if (strcmp(argv[index], "--out") == 0 && index + 1 < argc) {
                output_path = @(argv[++index]);
            } else if (strcmp(argv[index], "--stop-after") == 0 && index + 1 < argc) {
                stop_after = atoi(argv[++index]);
            } else if (strcmp(argv[index], "--only-event") == 0 && index + 1 < argc) {
                only_event = atoi(argv[++index]);
            } else if (strcmp(argv[index], "--seed-marked") == 0) {
                seed_marked = YES;
            } else if (strcmp(argv[index], "--capture-post") == 0) {
                capture_post_images = YES;
            } else if (strcmp(argv[index], "--capture-pre") == 0) {
                capture_pre_images = YES;
            } else if (strcmp(argv[index], "--depth-from-prefix") == 0) {
                depth_from_prefix = YES;
            }
        }
        if (frame_path.length == 0 || shader_path.length == 0 ||
            output_path.length == 0) {
            fprintf(stderr,
                    "usage: %s --frame <dir> --shaders <dir> --out <dir> "
                    "[--stop-after N]\n",
                    argv[0]);
            return 2;
        }
        [[NSFileManager defaultManager] createDirectoryAtPath:output_path
                                  withIntermediateDirectories:YES
                                                   attributes:nil
                                                        error:NULL];
        g_device = MTLCreateSystemDefaultDevice();
        if (g_device == nil) {
            fprintf(stderr, "no Metal device\n");
            return 3;
        }
        g_queue = [g_device newCommandQueue];
        g_pipelines = [NSMutableDictionary dictionary];
        g_functions = [NSMutableDictionary dictionary];
        g_textures = [NSMutableDictionary dictionary];
        g_samplers = [NSMutableDictionary dictionary];
        g_targets = [NSMutableDictionary dictionary];
        g_render_targets = [NSMutableDictionary dictionary];
        g_alpha_textures = [NSMutableDictionary dictionary];

        NSError *error = nil;
        NSDictionary *manifest = read_json(
            [frame_path stringByAppendingPathComponent:@"frame-trace.json"], &error);
        if (manifest == nil) {
            fprintf(stderr, "manifest: %s\n", error.localizedDescription.UTF8String);
            return 4;
        }
        NSArray *events = read_json_lines(
            [frame_path stringByAppendingPathComponent:@"frame-events.jsonl"], &error);
        if (events == nil) {
            fprintf(stderr, "events: %s\n", error.localizedDescription.UTF8String);
            return 4;
        }
        NSDictionary *shader_report = read_json(
            [shader_path stringByAppendingPathComponent:@"shaders.json"], &error);
        if (shader_report == nil) {
            fprintf(stderr, "shaders: %s\n", error.localizedDescription.UTF8String);
            return 4;
        }
        NSMutableDictionary *programs = [NSMutableDictionary dictionary];
        for (NSDictionary *entry in shader_report[@"programs"] ?: @[]) {
            NSNumber *program_number = entry[@"program"];
            if (program_number != nil && [entry[@"status"] isEqualToString:@"ok"]) {
                programs[program_number] = entry;
            }
        }
        /* Establish the default target before any FB reference. */
        NSDictionary *frame_info = manifest[@"frame"] ?: @{};
        g_default_target.width = number(frame_info[@"width"], 0);
        g_default_target.height = number(frame_info[@"height"], 0);
        target_for(@{@"framebuffer": @0,
                     @"width": @(g_default_target.width),
                     @"height": @(g_default_target.height)},
                   &error);

        NSDictionary *first_draw = nil;
        for (NSDictionary *event in events) {
            if ([event[@"kind"] isEqualToString:@"draw"]) {
                first_draw = event;
                break;
            }
        }
        if (first_draw == nil) {
            fprintf(stderr, "frame has no draws\n");
            return 5;
        }
        NSDictionary *seed = first_draw[@"seed"];
        if (seed != nil) {
            if (!upload_seed(frame_path, seed, first_draw[@"target"], &error)) {
                fprintf(stderr, "seed: %s\n", error.localizedDescription.UTF8String);
                return 6;
            }
        }
        /* Register render-target textures under the GL names that sample them,
           so composition inside the frame uses the rendered contents. */
        for (NSDictionary *event in events) {
            NSDictionary *target = event[@"target"] ?: @{};
            NSDictionary *attachment = target[@"color_attachment"];
            if (![attachment isKindOfClass:[NSDictionary class]]) {
                continue;
            }
            NSMutableDictionary *info = target_for(target, &error);
            if (info == nil) {
                continue;
            }
            NSString *attachment_name = string_value(attachment[@"name"]);
            if (attachment_name.length == 0) {
                continue;
            }
            if (g_render_targets[attachment_name] == nil) {
                g_render_targets[attachment_name] = info;
            }
        }
        id<MTLCommandBuffer> command = [g_queue commandBuffer];
        NSUInteger encoded = 0;
        NSUInteger failed = 0;
        NSMutableArray *failures = [NSMutableArray array];
        NSDictionary *first_target = first_draw[@"target"];

        /* Isolated mode consumes the authentic GL pre-image as the color
           attachment. Attachments are initialized first, the image is uploaded
           and verified second, and only then is the selected draw encoded, so
           no later clear can destroy the supplied pre-image. */
        BOOL isolated = only_event >= 0;
        BOOL isolated_pre_supplied = NO;
        BOOL isolated_depth_matters = NO;
        BOOL isolated_pre_capture_ok = NO;
        NSString *isolated_pre_capture = @"";
        NSString *color_initialization = @"not_isolated";
        NSString *depth_contents = @"not_isolated";
        NSDictionary *isolated_event = nil;
        NSDictionary *isolated_target_info = nil;
        NSUInteger isolated_prefix_events = 0;
        if (isolated) {
            if (only_event >= (NSInteger)events.count) {
                fprintf(stderr, "only-event %ld is outside the frame (%lu events)\n",
                        (long)only_event, (unsigned long)events.count);
                return 7;
            }
            isolated_event = events[(NSUInteger)only_event];
            isolated_target_info = target_for(isolated_event[@"target"] ?: @{}, &error);
            if (isolated_target_info == nil) {
                fprintf(stderr, "isolated target: %s\n",
                        error.localizedDescription.UTF8String);
                return 7;
            }
            isolated_pre_supplied = seed_marked && isolated_event[@"pre_image"] != nil;
            isolated_depth_matters = draw_depth_matters(isolated_event);
            if (isolated_pre_supplied) {
                color_initialization = @"supplied_pre_image";
            } else {
                color_initialization = @"synthetic_black";
            }
            if (!isolated_depth_matters) {
                depth_contents = @"not_required";
            } else if (depth_from_prefix && isolated_pre_supplied) {
                depth_contents = @"replayed_prefix";
            } else {
                depth_contents = @"cleared_unverified";
            }
        }

        for (NSUInteger index = 0; index < events.count; index++) {
            if (stop_after >= 0 && (NSInteger)encoded >= stop_after) {
                break;
            }
            BOOL is_selected = isolated && (NSInteger)index == only_event;
            if (isolated && !is_selected) {
                /* Prior events are replayed only when their attachment contents
                   (depth) are an input to the selected draw. */
                if (!(depth_from_prefix && isolated_pre_supplied &&
                      (NSInteger)index < only_event)) {
                    continue;
                }
                isolated_prefix_events++;
            }
            NSDictionary *event = events[index];
            if (is_selected && isolated_pre_supplied) {
                if (isolated_depth_matters && !depth_from_prefix) {
                    /* Defined depth, but not the authentic prior contents. */
                    NSDictionary *depth_event = @{
                        @"kind": @"clear",
                        @"flags": @6, /* depth only; the color is loaded */
                        @"depth": @1,
                        @"target": event[@"target"] ?: @{},
                    };
                    if (!encode_clear(depth_event, frame_path, command, &error)) {
                        fprintf(stderr, "depth initialization: %s\n",
                                error.localizedDescription.UTF8String);
                        return 7;
                    }
                }
                /* Drain every prior command before the upload: an uncommitted
                   clear encoded earlier would otherwise execute afterwards. */
                [command commit];
                if (!wait_for_transfer(command, @"attachment initialization",
                                       &error)) {
                    fprintf(stderr, "attachment initialization: %s\n",
                            error.localizedDescription.UTF8String);
                    return 7;
                }
                command = [g_queue commandBuffer];
                if (!upload_image_to_target(frame_path, event[@"pre_image"],
                                            isolated_target_info, &error)) {
                    fprintf(stderr, "pre-image upload: %s\n",
                            error.localizedDescription.UTF8String);
                    return 7;
                }
                if (capture_pre_images) {
                    NSString *name = [NSString stringWithFormat:
                                          @"isolated-pre-%lu.bgra",
                                          (unsigned long)index];
                    NSString *path =
                        [output_path stringByAppendingPathComponent:name];
                    if (!capture_target_to_file(isolated_target_info, path)) {
                        fprintf(stderr, "pre-image readback failed\n");
                        return 7;
                    }
                    isolated_pre_capture_ok = YES;
                    isolated_pre_capture = name;
                }
            } else if (is_selected) {
                /* Explicit black-target diagnostic. It is never combined with
                   an authentic pre-image. */
                NSDictionary *clear_event = @{
                    @"kind": @"clear",
                    @"flags": @7,
                    @"color": @[@0, @0, @0, @1],
                    @"depth": @1,
                    @"target": event[@"target"] ?: @{},
                };
                (void)encode_clear(clear_event, frame_path, command, NULL);
            }
            NSString *kind = event[@"kind"];
            NSError *event_error = nil;
            BOOL ok = YES;
            if ([kind isEqualToString:@"clear"]) {
                ok = encode_clear(event, frame_path, command, &event_error);
            } else if ([kind isEqualToString:@"draw"]) {
                NSNumber *program = event[@"program"][@"name"];
                NSDictionary *entry = programs[program];
                if (entry == nil) {
                    ok = NO;
                    event_error = [NSError errorWithDomain:kErrorDomain code:90
                                                 userInfo:@{NSLocalizedDescriptionKey:
                                                                [NSString stringWithFormat:
                                                                     @"program %@ has no translated stages",
                                                                     program]}];
                } else {
                    NSDictionary *target_info = target_for(event[@"target"] ?: @{}, NULL);
                    if (ok) {
                        ok = encode_draw(event, entry, frame_path, shader_path,
                                         command, &event_error);
                    }
                    if (ok && capture_post_images && event[@"post_image"] != nil &&
                        target_info != nil && (!isolated || is_selected)) {
                        /* The draw was encoded into the frame command buffer; commit
                           and drain it so the isolated post-image includes this draw
                           and no later event. */
                        [command commit];
                        if (!wait_for_transfer(command, @"post-draw completion",
                                               &event_error)) {
                            ok = NO;
                        }
                        command = [g_queue commandBuffer];
                        if (ok) {
                            NSString *name = [NSString stringWithFormat:
                                                  @"isolated-post-%lu.bgra",
                                                  (unsigned long)index];
                            NSString *path =
                                [output_path stringByAppendingPathComponent:name];
                            if (!capture_target_to_file(target_info, path)) {
                                ok = NO;
                                event_error = [NSError errorWithDomain:kErrorDomain
                                                                  code:101
                                                              userInfo:@{NSLocalizedDescriptionKey:
                                                                             @"post readback failed"}];
                            }
                        }
                    }
                }
            }
            if (!ok) {
                failed++;
                [failures addObject:@{
                    @"event": @(index),
                    @"kind": kind ?: @"unknown",
                    @"caller": event[@"draw"][@"caller_rva"] ?: @"",
                    @"error": event_error.localizedDescription ?: @"unknown",
                }];
            }
            encoded++;
        }
        [command commit];
        [command waitUntilCompleted];
        NSMutableDictionary *result = [@{
            @"schema": @"gfx-metal-frame-replay/v1",
            @"events": @(events.count),
            @"encoded": @(encoded),
            @"failed": @(failed),
            @"failures": failures,
            @"first_target": first_target ?: @{},
            @"command_status": @(command.status),
            @"seed": seed ?: @{},
            @"manifest_reference_sha256":
                (manifest[@"frame"] ?: @{})[@"reference_sha256"] ?: @"",
        } mutableCopy];
        if (isolated) {
            result[@"isolated"] = @{
                @"event": @(only_event),
                @"kind": isolated_event[@"kind"] ?: @"unknown",
                @"caller": isolated_event[@"draw"][@"caller_rva"] ?: @"",
                @"program": isolated_event[@"program"][@"name"] ?: @0,
                @"pre_image": isolated_event[@"pre_image"] ?: @{},
                @"pre_image_supplied": @(isolated_pre_supplied),
                @"pre_capture": isolated_pre_capture,
                @"pre_capture_ok": @(isolated_pre_capture_ok),
                @"color_initialization": color_initialization,
                @"depth_matters": @(isolated_depth_matters),
                @"depth_contents": depth_contents,
                @"prefix_events": @(isolated_prefix_events),
                @"exact": @(isolated_pre_supplied &&
                            isolated_pre_capture_ok &&
                            (!isolated_depth_matters ||
                             [depth_contents isEqualToString:@"replayed_prefix"])),
            };
        }
        BOOL readback_ok = NO;
        if (failed == 0) {
            NSUInteger width = g_default_target.width;
            NSUInteger height = g_default_target.height;
            NSUInteger bytes_per_row = width * 4;
            id<MTLBuffer> buffer =
                [g_device newBufferWithLength:bytes_per_row * height
                                      options:MTLResourceStorageModeShared];
            id<MTLCommandBuffer> blit = [g_queue commandBuffer];
            id<MTLBlitCommandEncoder> encoder = [blit blitCommandEncoder];
            [encoder copyFromTexture:g_default_target.color
                         sourceSlice:0
                         sourceLevel:0
                        sourceOrigin:MTLOriginMake(0, 0, 0)
                          sourceSize:MTLSizeMake(width, height, 1)
                            toBuffer:buffer
                   destinationOffset:0
              destinationBytesPerRow:bytes_per_row
            destinationBytesPerImage:bytes_per_row * height];
            [encoder endEncoding];
            [blit commit];
            [blit waitUntilCompleted];
            uint8_t *readback = (uint8_t *)malloc(bytes_per_row * height);
            if (readback != NULL) {
                flip_rows((const uint8_t *)buffer.contents, readback, width, height, 4);
                NSString *path = [output_path stringByAppendingPathComponent:@"metal-frame.bgra"];
                NSData *data = [NSData dataWithBytes:readback length:bytes_per_row * height];
                readback_ok = [data writeToFile:path atomically:YES];
                result[@"frame"] = @{
                    @"width": @(width),
                    @"height": @(height),
                    @"origin": @"lower_left",
                    @"pixel_format": @"BGRA8",
                    @"row_stride": @(bytes_per_row),
                    @"raw_file": @"metal-frame.bgra",
                    @"raw_bytes": @(data.length),
                };
                free(readback);
            }
        }
        result[@"readback"] = @(readback_ok);
        result[@"status"] = readback_ok && failed == 0 ? @"ok" : @"failed";
        NSData *json = [NSJSONSerialization dataWithJSONObject:result
                                                       options:NSJSONWritingPrettyPrinted
                                                         error:NULL];
        [json writeToFile:[output_path stringByAppendingPathComponent:@"replay.json"]
               atomically:YES];
        printf("%s\n", [[[NSString alloc] initWithData:json
                                              encoding:NSUTF8StringEncoding] UTF8String]);
        return failed == 0 && readback_ok ? 0 : 1;
    }
}
