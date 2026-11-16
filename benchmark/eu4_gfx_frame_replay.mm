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
#import "eu4_gfx_draw_semantics.h"
#include "eu4_gfx_replay_semantics.h"
#include "eu4_gfx_metal_replay.h"

#include <CommonCrypto/CommonDigest.h>
#include <time.h>
#include <sys/stat.h>

#include <stdio.h>
#include <string.h>
#include <stdlib.h>

static NSString *const kErrorDomain = @"eu4.frame.replay";
static uint64_t replay_now_ns(void);
static BOOL wait_for_transfer(id<MTLCommandBuffer> buffer, NSString *operation,
                              NSError **error);

typedef struct {
    __unsafe_unretained id<MTLTexture> color;
    __unsafe_unretained id<MTLTexture> depth;
    NSUInteger width;
    NSUInteger height;
} FrameTarget;

/* All mutable replay state lives in the session.  The TLS pointer only selects
   the active session for the synchronous execution on this thread. */
@interface EU4MetalReplayState : NSObject
@property (nonatomic, strong) id<MTLDevice> device;
@property (nonatomic, strong) id<MTLCommandQueue> queue;
@property (nonatomic, strong) NSMutableDictionary<NSString *, id> *pipelines;
@property (nonatomic, strong) NSMutableDictionary<NSString *, id> *functions;
@property (nonatomic, strong) NSMutableDictionary<NSString *, id> *textures;
@property (nonatomic, strong) NSMutableDictionary<NSString *, id> *samplers;
@property (nonatomic, strong) NSMutableDictionary<NSNumber *, NSMutableDictionary *> *targets;
@property (nonatomic, strong) NSMutableDictionary<NSString *, NSMutableDictionary *> *render_targets;
@property (nonatomic, strong) NSMutableDictionary<NSString *, id> *alpha_textures;
@property (nonatomic, strong) NSMutableDictionary<NSString *, id> *orientation_textures;
@property (nonatomic, strong) id<MTLComputePipelineState> orientation_pipeline;
@property (nonatomic, strong) id<MTLComputePipelineState> pipeline_for_copy;
@property (nonatomic, strong) id<MTLComputePipelineState> alpha_pipeline;
@property (nonatomic, strong) id<MTLFunction> debug_fragment;
@property (nonatomic, strong) id<MTLFunction> debug_vertex;
@property (nonatomic, strong) id<MTLFunction> debug_uv_fragment;
@property (nonatomic, strong) id<MTLFunction> debug_attr_vertex;
@property (nonatomic, strong) id<MTLFunction> debug_texture_fragment;
@property (nonatomic, strong) NSMutableArray *provenance;
@property (nonatomic, strong) NSMutableDictionary *provenance_seen;
@property (nonatomic, strong) NSDictionary *checkpoints_by_event;
@property (nonatomic, strong) NSMutableArray *checkpoint_results;
@property (nonatomic, strong) NSMutableArray *transfer_records;
@property (nonatomic, assign) NSUInteger virtual_target_id;
@property (nonatomic, assign) NSUInteger current_event;
@property (nonatomic, assign) uint64_t timeout_ms;
@property (nonatomic, assign) uint64_t deadline_ns;
@property (nonatomic, assign) int deadline_expired;
@property (nonatomic, assign) uint64_t events_count;
@property (nonatomic, assign) uint64_t encoded_count;
@property (nonatomic, assign) uint64_t failed_count;
@property (nonatomic, assign) uint64_t started_ns;
@end

@implementation EU4MetalReplayState
@end

static thread_local EU4MetalReplayState *__unsafe_unretained g_state;
static const NSUInteger kVertexBufferBase = 8;
static thread_local FrameTarget *g_default_target_ptr;

#define g_device (g_state.device)
#define g_queue (g_state.queue)
#define g_pipelines (g_state.pipelines)
#define g_functions (g_state.functions)
#define g_textures (g_state.textures)
#define g_samplers (g_state.samplers)
#define g_targets (g_state.targets)
#define g_render_targets (g_state.render_targets)
#define g_alpha_textures (g_state.alpha_textures)
#define g_orientation_textures (g_state.orientation_textures)
#define g_orientation_pipeline (g_state.orientation_pipeline)
#define g_copy_pipeline (g_state.pipeline_for_copy)
#define g_alpha_pipeline (g_state.alpha_pipeline)
#define g_debug_fragment (g_state.debug_fragment)
#define g_debug_vertex (g_state.debug_vertex)
#define g_debug_uv_fragment (g_state.debug_uv_fragment)
#define g_debug_attr_vertex (g_state.debug_attr_vertex)
#define g_debug_texture_fragment (g_state.debug_texture_fragment)
#define g_virtual_target_id (g_state.virtual_target_id)
#define g_provenance (g_state.provenance)
#define g_provenance_seen (g_state.provenance_seen)
#define g_checkpoints_by_event (g_state.checkpoints_by_event)
#define g_checkpoint_results (g_state.checkpoint_results)
#define g_transfer_records (g_state.transfer_records)
#define g_current_event (g_state.current_event)
#define g_timeout_ms (g_state.timeout_ms)
#define g_deadline_ns (g_state.deadline_ns)
#define g_deadline_expired (g_state.deadline_expired)
#define g_events_count (g_state.events_count)
#define g_encoded_count (g_state.encoded_count)
#define g_failed_count (g_state.failed_count)
#define g_started_ns (g_state.started_ns)
#define g_default_target (*g_default_target_ptr)

static NSMutableDictionary *target_for(NSDictionary *target, NSError **error);
/* Provenance of every texture binding and of replay-time checkpoint reads.
   The coordinator uses this to distinguish external captured inputs from
   renderer-generated targets, so a replay that silently consumed a captured
   intermediate cannot be reported as complete. */

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

static NSString *const kOrientationKernelMSL =
    @"#include <metal_stdlib>\n"
     "using namespace metal;\n"
     "kernel void orient(texture2d<float, access::read> source [[texture(0)]],\n"
     "                   texture2d<float, access::write> destination [[texture(1)]],\n"
     "                   constant uint &flags [[buffer(0)]],\n"
     "                   uint2 gid [[thread_position_in_grid]]) {\n"
     "    uint y = (flags & 1u) != 0u ? (source.get_height() - 1u - gid.y) : gid.y;\n"
     "    float4 value = source.read(uint2(gid.x, y));\n"
     "    if ((flags & 2u) != 0u) value.a = 1.0;\n"
     "    destination.write(value, gid);\n"
     "}\n";

static NSString *const kCopyKernelMSL =
    @"#include <metal_stdlib>\n"
     "using namespace metal;\n"
     "kernel void copy_rect(texture2d<float, access::read> source [[texture(0)]],\n"
     "                      texture2d<float, access::write> destination [[texture(1)]],\n"
     "                      constant int4 &rect [[buffer(0)]],\n"
     "                      constant int2 &size [[buffer(1)]],\n"
     "                      uint2 gid [[thread_position_in_grid]]) {\n"
     "    if ((int)gid.x >= size.x || (int)gid.y >= size.y) return;\n"
     "    int x = rect.x + (int)gid.x;\n"
     "    int y = rect.y + (int)gid.y;\n"
     "    int dx = rect.z + (int)gid.x;\n"
     "    int dy = rect.w + (int)gid.y;\n"
     "    if (x < 0 || y < 0 || dx < 0 || dy < 0) return;\n"
     "    if (x >= (int)source.get_width() || y >= (int)source.get_height()) return;\n"
     "    if (dx >= (int)destination.get_width() || dy >= (int)destination.get_height()) return;\n"
     "    destination.write(source.read(uint2((uint)x, (uint)y)), uint2((uint)dx, (uint)dy));\n"
     "}\n";

static id<MTLComputePipelineState> copy_pipeline(NSError **error) {
    if (g_copy_pipeline != nil) {
        return g_copy_pipeline;
    }
    NSError *compile_error = nil;
    id<MTLLibrary> library = [g_device newLibraryWithSource:kCopyKernelMSL
                                                    options:nil
                                                      error:&compile_error];
    id<MTLFunction> function = [library newFunctionWithName:@"copy_rect"];
    if (function != nil) {
        g_copy_pipeline = [g_device newComputePipelineStateWithFunction:function
                                                                  error:&compile_error];
    }
    if (g_copy_pipeline == nil && error != NULL) {
        *error = compile_error;
    }
    return g_copy_pipeline;
}

static NSString *target_format_name(int internal_format) {
    switch (internal_format) {
        case 0x8058: return @"RGBA8"; /* GL_RGBA8 */
        case 0x8051: return @"RGB8";  /* GL_RGB8 */
        case 0x8814: return @"RGBA32F";
        case 0x8815: return @"RGB32F";
        case 0x881A: return @"RGBA16F";
        case 0x881B: return @"RGB16F";
        default: return @"other";
    }
}

static NSMutableDictionary *target_for_texture(NSString *name, NSUInteger width,
                                               NSUInteger height,
                                               NSString *format,
                                               NSError **error) {
    NSUInteger key_value = g_virtual_target_id++;
    NSDictionary *target = @{
        @"framebuffer": @(key_value),
        @"width": @(width),
        @"height": @(height),
        @"color_attachment": @{
            @"internal_format": format ?: @"other",
            @"width": @(width),
            @"height": @(height),
        },
    };
    NSMutableDictionary *info = target_for(target, error);
    if (info != nil) {
        info[@"virtual_name"] = name ?: @"";
    }
    return info;
}

static id<MTLComputePipelineState> orientation_pipeline(NSError **error) {
    if (g_orientation_pipeline != nil) {
        return g_orientation_pipeline;
    }
    NSError *compile_error = nil;
    id<MTLLibrary> library = [g_device newLibraryWithSource:kOrientationKernelMSL
                                                    options:nil
                                                      error:&compile_error];
    id<MTLFunction> function = [library newFunctionWithName:@"orient"];
    if (function != nil) {
        g_orientation_pipeline =
            [g_device newComputePipelineStateWithFunction:function
                                                    error:&compile_error];
    }
    if (g_orientation_pipeline == nil && error != NULL) {
        *error = compile_error;
    }
    return g_orientation_pipeline;
}

/* A Metal render target stores GL row 0 at the opposite end of the texture
 * from a GL-uploaded asset.  Sampling it with the engine's GL texture
 * coordinates therefore needs a V flip; an RGB target additionally needs
 * alpha forced to 1.0.  The transform is cached per target generation. */
static id<MTLTexture> sample_ready_target(id<MTLTexture> source,
                                          NSNumber *generation,
                                          BOOL force_alpha, BOOL flip_y,
                                          NSError **error) {
    if (source == nil || (!force_alpha && !flip_y)) {
        return source;
    }
    NSString *key = [NSString stringWithFormat:@"%p-%@-%d-%d", source,
                     generation, force_alpha ? 1 : 0, flip_y ? 1 : 0];
    id<MTLTexture> cached = g_orientation_textures[key];
    if (cached != nil) {
        return cached;
    }
    MTLTextureDescriptor *descriptor = [MTLTextureDescriptor new];
    descriptor.textureType = MTLTextureType2D;
    descriptor.pixelFormat = source.pixelFormat;
    descriptor.width = source.width;
    descriptor.height = source.height;
    descriptor.usage = MTLTextureUsageShaderRead | MTLTextureUsageShaderWrite;
    descriptor.storageMode = MTLStorageModePrivate;
    id<MTLTexture> destination =
        [g_device newTextureWithDescriptor:descriptor];
    id<MTLComputePipelineState> pipeline = orientation_pipeline(error);
    if (destination == nil || pipeline == nil) {
        return nil;
    }
    uint32_t flags = (flip_y ? 1u : 0u) | (force_alpha ? 2u : 0u);
    id<MTLCommandBuffer> command = [g_queue commandBuffer];
    id<MTLComputeCommandEncoder> encoder = [command computeCommandEncoder];
    [encoder setComputePipelineState:pipeline];
    [encoder setTexture:source atIndex:0];
    [encoder setTexture:destination atIndex:1];
    [encoder setBytes:&flags length:sizeof(flags) atIndex:0];
    MTLSize threads_per_group = MTLSizeMake(8, 8, 1);
    MTLSize groups = MTLSizeMake((source.width + 7) / 8,
                                 (source.height + 7) / 8, 1);
    [encoder dispatchThreadgroups:groups threadsPerThreadgroup:threads_per_group];
    [encoder endEncoding];
    [command commit];
    (void)wait_for_transfer(command, @"command completion", NULL);
    if (command.status != MTLCommandBufferStatusCompleted) {
        if (error != NULL) {
            *error = [NSError errorWithDomain:kErrorDomain code:67
                                     userInfo:@{NSLocalizedDescriptionKey:
                                                    @"target orientation pass failed"}];
        }
        return nil;
    }
    g_orientation_textures[key] = destination;
    return destination;
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
    if (!wait_for_transfer(compute_command, @"alpha transform", NULL) ||
        compute_command.status != MTLCommandBufferStatusCompleted) {
        return nil;
    }
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

/* GL texture names are recorded as JSON numbers.  They are logical resource
   identities, not pointers: the same decimal name must map to the same Metal
   render target that later passes sample. */
static NSString *texture_key(id value) {
    if ([value isKindOfClass:[NSString class]]) {
        return value;
    }
    if ([value isKindOfClass:[NSNumber class]]) {
        return [value stringValue];
    }
    return nil;
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
    int topology = eu4_gfx_metal_topology((uint32_t)mode);
    if (topology >= 0) {
        return (MTLPrimitiveType)topology;
    }
    if (error != NULL) {
        NSString *reason = mode == 2
            ? @"line-loop primitive is unsupported"
            : [NSString stringWithFormat:@"unsupported primitive mode %lu",
                                         (unsigned long)mode];
        *error = [NSError errorWithDomain:kErrorDomain code:13
                                 userInfo:@{NSLocalizedDescriptionKey: reason}];
    }
    return MTLPrimitiveTypePoint;
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
    [key appendFormat:@"%@-%@-%lu", texture_key(texture[@"name"]) ?: @"",
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
        depth_descriptor.pixelFormat = MTLPixelFormatDepth32Float_Stencil8;
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
        NSDictionary *state = event[@"state"] ?: @{};
        NSDictionary *blend = state[@"blend"] ?: @{};
        NSDictionary *depth_state = state[@"depth"] ?: @{};
        NSDictionary *cull = state[@"cull"] ?: @{};
        NSDictionary *stencil_state = state[@"stencil"] ?: @{};
        NSDictionary *polygon = state[@"polygon"] ?: @{};
        if (number(stencil_state[@"test"], 0) != 0) {
            if (error != NULL) {
                *error = [NSError errorWithDomain:kErrorDomain code:69
                                         userInfo:@{NSLocalizedDescriptionKey:
                                                        @"captured stencil test is not reconstructed"}];
            }
            return NO;
        }
        for (id mode_value in polygon[@"mode"] ?: @[]) {
            unsigned long mode = strtoul([string_value(mode_value) ?: @"0" UTF8String],
                                         NULL, 0);
            if (mode != 0x1b02) {
                if (error != NULL) {
                    *error = [NSError errorWithDomain:kErrorDomain code:70
                                             userInfo:@{NSLocalizedDescriptionKey:
                                                            @"captured non-fill polygon mode is not reconstructed"}];
                }
                return NO;
            }
        }
        if (number(polygon[@"offset_fill"], 0) != 0) {
            if (error != NULL) {
                *error = [NSError errorWithDomain:kErrorDomain code:71
                                         userInfo:@{NSLocalizedDescriptionKey:
                                                        @"captured polygon offset is not reconstructed"}];
            }
            return NO;
        }
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
        /* Captured attribute blobs are rebased to their first effective
           vertex; the replay index must stay inside this many vertices. */
        NSUInteger replay_vertex_limit = NSUIntegerMax;
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
                MTLVertexStepFunctionConstant;
            vertex_descriptor.layouts[kVertexBufferBase + location].stepRate = 0;
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
                    MTLVertexStepFunctionConstant;
                vertex_descriptor.layouts[kVertexBufferBase + location].stepRate = 0;
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
            if (number(attribute[@"read_error"], 0) != 0 ||
                number(attribute[@"range_truncated"], 0) != 0) {
                if (error != NULL) {
                    *error = [NSError errorWithDomain:kErrorDomain code:61
                                             userInfo:@{NSLocalizedDescriptionKey:
                                                            @"captured attribute range is invalid"}];
                }
                return NO;
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
            if (number(attribute[@"divisor"], 0) == 0 && stride > 0) {
                NSUInteger element_bytes = 4;
                switch (gl_type) {
                    case 0x1400: case 0x1401: element_bytes = 1; break;
                    case 0x1402: case 0x1403: case 0x140b: element_bytes = 2; break;
                    default: element_bytes = 4; break;
                }
                NSUInteger attribute_bytes = components * element_bytes;
                NSUInteger vertices = data.length >= attribute_bytes
                    ? (data.length - attribute_bytes) / stride + 1 : 0;
                if (vertices < replay_vertex_limit) {
                    replay_vertex_limit = vertices;
                }
            }
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
            if (divisor > 0) {
                vertex_descriptor.layouts[kVertexBufferBase + location].stepFunction =
                    MTLVertexStepFunctionPerInstance;
                vertex_descriptor.layouts[kVertexBufferBase + location].stepRate = divisor;
            } else {
                vertex_descriptor.layouts[kVertexBufferBase + location].stepFunction =
                    MTLVertexStepFunctionPerVertex;
                vertex_descriptor.layouts[kVertexBufferBase + location].stepRate = 1;
            }
            NSUInteger instance_available = 0;
            if (divisor > 0 && stride > 0) {
                NSUInteger element_bytes = 4;
                switch (gl_type) {
                    case 0x1400: case 0x1401: element_bytes = 1; break;
                    case 0x1402: case 0x1403: case 0x140b: element_bytes = 2; break;
                    default: element_bytes = 4; break;
                }
                NSUInteger attribute_bytes = components * element_bytes;
                instance_available = data.length >= attribute_bytes
                    ? (data.length - attribute_bytes) / stride + 1 : 0;
            }
            [attribute_plan addObject:@{
                @"location": @(location),
                @"components": @(components),
                @"gl_type": @(gl_type),
                @"normalized": @(normalized),
                @"stride": @(stride),
                @"divisor": @(divisor),
                @"instances": @(instance_available),
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
            if (u != nil && u.length >= 16 * sizeof(float)) {
                const float *floats = (const float *)u.bytes;
                fprintf(stderr,
                        "  uniform bytes %lu first %.4f %.4f %.4f %.4f | %.4f %.4f %.4f %.4f\n",
                        (unsigned long)u.length,
                        floats[0], floats[1], floats[2], floats[3],
                        floats[12], floats[13], floats[14], floats[15]);
            } else {
                fprintf(stderr, "  uniform bytes %lu (unavailable)\n",
                        (unsigned long)(u != nil ? u.length : 0));
            }
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
                descriptor.depthAttachmentPixelFormat =
                    MTLPixelFormatDepth32Float_Stencil8;
                descriptor.stencilAttachmentPixelFormat =
                    MTLPixelFormatDepth32Float_Stencil8;
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
            pass.stencilAttachment.texture = target_info[@"depth"];
            pass.stencilAttachment.loadAction = MTLLoadActionLoad;
            pass.stencilAttachment.storeAction = MTLStoreActionStore;
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
        NSMutableDictionary *texture_by_unit = [NSMutableDictionary dictionary];
        for (NSDictionary *texture in event[@"textures"] ?: @[]) {
            texture_by_name[string_value(texture[@"sampler_name"]) ?: @""] = texture;
            NSNumber *unit = texture[@"unit"];
            if (unit != nil) {
                texture_by_unit[unit] = texture;
            }
        }
        const char *shift_env = getenv("EU4_FRAME_REPLAY_SAMPLER_SHIFT");
        NSInteger sampler_shift =
            shift_env != NULL ? (NSInteger)strtol(shift_env, NULL, 10) : 0;
        const char *shift_min_env = getenv("EU4_FRAME_REPLAY_SAMPLER_SHIFT_MIN");
        NSInteger shift_min = shift_min_env != NULL
            ? (NSInteger)strtol(shift_min_env, NULL, 10) : 7;
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
                NSString *expected_texture_name =
                    texture_key(texture[@"name"]) ?: @"";
                NSDictionary *selected_texture = texture;
                NSInteger selected_engine_unit = -1;
                BOOL shifted = NO;
                BOOL replacement_missing = NO;
                /* Controlled fault injection only (Phase 5): reproduce the
                   historical missing-tex6 selection defect.  The shader and
                   Metal slot are unchanged; the payload is taken from the
                   neighbouring engine texture unit, exactly as a
                   declaration-order unit table would select it. */
                if (sampler_shift != 0 && [name hasPrefix:@"tex"]) {
                    NSString *suffix = [name substringFromIndex:3];
                    NSInteger unit_number = suffix.integerValue;
                    if (suffix.length > 0 && unit_number >= shift_min) {
                        NSInteger requested = unit_number + sampler_shift;
                        if (requested >= 0) {
                            NSDictionary *replacement = texture_by_unit[@(requested)];
                            selected_engine_unit = requested;
                            shifted = YES;
                            if (replacement != nil) {
                                selected_texture = replacement;
                                texture = replacement;
                            } else {
                                replacement_missing = YES;
                            }
                            /* A missing unit is exactly the historical
                               missing-tex6 case: the shader would sample the
                               default texture.  Keep the expected payload and
                               record the missing unit rather than silently
                               falling back to the correct texture. */
                        }
                    }
                }
                id<MTLTexture> metal_texture = nil;
                NSString *cache_name = texture_key(texture[@"name"]);
                NSMutableDictionary *render_target = g_render_targets[cache_name];
                if (render_target != nil) {
                    NSString *format = string_value(render_target[@"format"]) ?: @"";
                    BOOL rgb_source = [format isEqualToString:@"RGB8"] ||
                                      [format isEqualToString:@"SRGB8_ALPHA8"];
                    /* Renderer-generated targets are stored with GL row 0 at
                       the opposite end of the texture from a GL-uploaded
                       asset.  The replay must flip V when sampling them; this
                       is the contract the production resolve also implements.
                       The disabling switch exists only for controlled
                       divergence experiments. */
                    const char *disable_flip =
                        getenv("EU4_FRAME_REPLAY_DISABLE_RT_FLIP");
                    BOOL flip_y = !(disable_flip != NULL && disable_flip[0] == '1');
                    metal_texture = sample_ready_target(
                        render_target[@"color"], render_target[@"generation"],
                        rgb_source, flip_y, error);
                    if (metal_texture == nil) {
                        if (error != NULL) {
                            *error = [NSError errorWithDomain:kErrorDomain code:68
                                                     userInfo:@{NSLocalizedDescriptionKey:
                                                                    @"render target sampling preparation failed"}];
                        }
                        [encoder endEncoding];
                        return NO;
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
                NSUInteger texture_index =
                    number(binding[@"texture_index"], 0);
                NSUInteger sampler_index =
                    number(binding[@"sampler_index"], 0);
                NSNumber *generation = render_target != nil
                    ? render_target[@"generation"] : @(-1);
                NSMutableDictionary *sample = [@{
                    @"event": @(g_current_event),
                    @"stage": stage_name,
                    @"texture": cache_name ?: @"",
                    @"sampler": name ?: @"",
                    @"unit": @(texture_index),
                    @"source": render_target != nil
                        ? @"metal_render_target" : @"external_capture",
                    @"generation": generation,
                    @"fault_sampler_shift": @(shifted ? sampler_shift : 0),
                    @"fault_shader_unit": @(number(binding[@"texture_index"], 0)),
                    @"fault_bound_unit": @(texture_index),
                    @"expected_texture": expected_texture_name,
                    @"selected_engine_unit": @(selected_engine_unit),
                    @"selected_texture": texture_key(selected_texture[@"name"]) ?: @"",
                    @"fault_missing_engine_unit": @(replacement_missing
                        ? selected_engine_unit : -1),
                } mutableCopy];
                if (render_target != nil) {
                    sample[@"width"] = render_target[@"width"] ?: @0;
                    sample[@"height"] = render_target[@"height"] ?: @0;
                    sample[@"format"] = render_target[@"format"] ?: @"";
                    if (number(generation, -1) == 0 &&
                        g_provenance_seen[cache_name] == nil) {
                        g_provenance_seen[cache_name] = @YES;
                        sample[@"first_sample_before_any_write"] = @YES;
                    }
                }
                [g_provenance addObject:sample];
                if ([stage_name isEqualToString:@"vertex"]) {
                    [encoder setVertexTexture:metal_texture
                                      atIndex:texture_index];
                } else {
                    [encoder setFragmentTexture:metal_texture
                                        atIndex:texture_index];
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
                                           atIndex:sampler_index];
                } else {
                    [encoder setFragmentSamplerState:sampler
                                             atIndex:sampler_index];
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
        NSInteger base_vertex = [draw[@"base_vertex"] isKindOfClass:[NSNumber class]]
            ? [draw[@"base_vertex"] integerValue] : 0;
        NSInteger vertex_origin = 0;
        NSUInteger instances = MAX((NSUInteger)1, number(draw[@"instance_count"], 1));
        for (NSDictionary *plan in attribute_plan) {
            NSUInteger divisor = number(plan[@"divisor"], 0);
            if (divisor == 0) {
                continue;
            }
            NSUInteger available = number(plan[@"instances"], 0);
            NSUInteger last = instances > 0 ? (instances - 1) / divisor : 0;
            if (last >= available) {
                if (error != NULL) {
                    *error = [NSError errorWithDomain:kErrorDomain code:69
                                             userInfo:@{NSLocalizedDescriptionKey:
                                                            @"instance attribute is outside its captured range"}];
                }
                [encoder endEncoding];
                return NO;
            }
        }
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
            vertex_origin = [indices[@"vertex_origin"] isKindOfClass:[NSNumber class]]
                ? [indices[@"vertex_origin"] integerValue] : 0;
            if (number(indices[@"range_valid"], 1) == 0 ||
                number(indices[@"read_error"], 0) != 0) {
                if (error != NULL) {
                    *error = [NSError errorWithDomain:kErrorDomain code:68
                                             userInfo:@{NSLocalizedDescriptionKey:
                                                            @"captured index range is invalid"}];
                }
                [encoder endEncoding];
                return NO;
            }
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
            NSMutableData *rebased = [NSMutableData dataWithLength:count * 2];
            uint16_t *destination = (uint16_t *)rebased.mutableBytes;
            const uint16_t *source = (const uint16_t *)index_data.bytes;
            for (NSUInteger i = 0; i < count; i++) {
                int64_t effective = eu4_gfx_replay_index(source[i],
                                                         (int32_t)base_vertex,
                                                         (int32_t)vertex_origin);
                if (effective < 0 || effective > 0xffff ||
                    (replay_vertex_limit != NSUIntegerMax &&
                     effective >= (int64_t)replay_vertex_limit)) {
                    if (error != NULL) {
                        *error = [NSError errorWithDomain:kErrorDomain code:68
                                                 userInfo:@{NSLocalizedDescriptionKey:
                                                                @"captured index is outside its vertex range"}];
                    }
                    [encoder endEncoding];
                    return NO;
                }
                destination[i] = (uint16_t)effective;
            }
            id<MTLBuffer> index_buffer = [g_device newBufferWithBytes:rebased.bytes
                                                               length:rebased.length
                                                              options:MTLResourceStorageModeManaged];
            [encoder drawIndexedPrimitives:primitive
                               indexCount:count
                                indexType:MTLIndexTypeUInt16
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

static BOOL encode_copy_event(NSDictionary *event, NSString *frameDir,
                             NSError **error) {
    (void)frameDir;
    NSString *operation = string_value(event[@"operation"]);
    /* A missing operation is not "the default copy": the captured variant must
       be explicitly present and recognized. */
    if (operation == nil ||
        ![operation isEqualToString:@"glCopyTexSubImage2D"]) {
        if (error != NULL) {
            *error = [NSError errorWithDomain:kErrorDomain code:96
                                     userInfo:@{NSLocalizedDescriptionKey:
                                                    @"unsupported resolve/copy variant"}];
        }
        return NO;
    }
    for (NSString *field in @[@"source_framebuffer", @"read_buffer",
                              @"source", @"destination", @"source_level",
                              @"destination_level", @"source_rect",
                              @"destination_offset", @"source_width",
                              @"source_height", @"destination_width",
                              @"destination_height", @"internal_format",
                              @"source_resolved", @"destination_resolved",
                              @"width", @"height"]) {
        if (event[field] == nil) {
            if (error != NULL) {
                *error = [NSError errorWithDomain:kErrorDomain code:96
                                         userInfo:@{NSLocalizedDescriptionKey:
                                                        [NSString stringWithFormat:@"resolve/copy is missing %@", field]}];
            }
            return NO;
        }
    }
    if (event[@"source_internal_format"] == nil) {
        if (error != NULL) {
            *error = [NSError errorWithDomain:kErrorDomain code:96
                                     userInfo:@{NSLocalizedDescriptionKey:
                                                    @"resolve/copy is missing source_internal_format"}];
        }
        return NO;
    }
    if (number(event[@"source_width"], 0) <= 0 ||
        number(event[@"source_height"], 0) <= 0 ||
        number(event[@"destination_width"], 0) <= 0 ||
        number(event[@"destination_height"], 0) <= 0 ||
        number(event[@"internal_format"], 0) <= 0) {
        if (error != NULL) {
            *error = [NSError errorWithDomain:kErrorDomain code:96
                                     userInfo:@{NSLocalizedDescriptionKey:
                                                    @"resolve/copy has invalid resource dimensions or format"}];
        }
        return NO;
    }
    if (number(event[@"is_blit"], 0) != 0 ||
        number(event[@"source_level"], -1) != 0 ||
        number(event[@"destination_level"], -1) != 0) {
        if (error != NULL) {
            *error = [NSError errorWithDomain:kErrorDomain code:96
                                     userInfo:@{NSLocalizedDescriptionKey:
                                                    @"blit or nonzero mip level is unsupported"}];
        }
        return NO;
    }
    if (number(event[@"source_resolved"], 0) == 0 ||
        number(event[@"destination_resolved"], 0) == 0) {
        if (error != NULL) {
            *error = [NSError errorWithDomain:kErrorDomain code:96
                                     userInfo:@{NSLocalizedDescriptionKey:
                                                    @"resolve/copy source or destination was unresolved"}];
        }
        return NO;
    }
    NSString *destination = texture_key(event[@"destination"]);
    NSMutableDictionary *destination_info = g_render_targets[destination];
    if (destination_info == nil) {
        if (error != NULL) {
            *error = [NSError errorWithDomain:kErrorDomain code:97
                                     userInfo:@{NSLocalizedDescriptionKey:
                                                    @"resolve destination target missing"}];
        }
        return NO;
    }
    NSString *source_name = texture_key(event[@"source"]);
    NSMutableDictionary *source_info = nil;
    if (number(event[@"source_framebuffer"], 0) == 0) {
        /* Only the back buffer is represented by the Metal default target.
           A front-buffer read is a different, uncaptured input. */
        if (number(event[@"read_buffer"], 0) != 0x0405 ||
            (source_name.length > 0 &&
             ![source_name isEqualToString:@"0"])) {
            if (error != NULL) {
                *error = [NSError errorWithDomain:kErrorDomain code:98
                                         userInfo:@{NSLocalizedDescriptionKey:
                                                        @"default-framebuffer copy does not read GL_BACK"}];
            }
            return NO;
        }
        source_info = target_for(@{@"framebuffer": @0,
                                   @"width": @(g_default_target.width),
                                   @"height": @(g_default_target.height)},
                                 error);
    } else {
        if (source_name.length == 0 || [source_name isEqualToString:@"0"]) {
            if (error != NULL) {
                *error = [NSError errorWithDomain:kErrorDomain code:98
                                         userInfo:@{NSLocalizedDescriptionKey:
                                                        @"non-default copy has no source attachment"}];
            }
            return NO;
        }
        if (event[@"source_attachment"] == nil ||
            number(event[@"source_attachment"], -1) !=
                number(event[@"read_buffer"], -1)) {
            if (error != NULL) {
                *error = [NSError errorWithDomain:kErrorDomain code:98
                                         userInfo:@{NSLocalizedDescriptionKey:
                                                        @"non-default copy attachment does not match its read buffer"}];
            }
            return NO;
        }
        source_info = g_render_targets[source_name];
    }
    if (source_info == nil) {
        if (error != NULL) {
            *error = [NSError errorWithDomain:kErrorDomain code:98
                                     userInfo:@{NSLocalizedDescriptionKey:
                                                    @"resolve source target missing"}];
        }
        return NO;
    }
    if (source_info == destination_info) {
        if (error != NULL) {
            *error = [NSError errorWithDomain:kErrorDomain code:98
                                     userInfo:@{NSLocalizedDescriptionKey:
                                                    @"resolve source and destination are the same target"}];
        }
        return NO;
    }
    NSArray *rect = event[@"source_rect"];
    NSArray *offset = event[@"destination_offset"];
    if (rect.count != 4 || offset.count != 4 - 2) {
        if (error != NULL) {
            *error = [NSError errorWithDomain:kErrorDomain code:99
                                     userInfo:@{NSLocalizedDescriptionKey:
                                                    @"resolve rectangle is malformed"}];
        }
        return NO;
    }
    NSInteger x = number(rect[0], 0);
    NSInteger y = number(rect[1], 0);
    NSInteger width = number(rect[2], 0);
    NSInteger height = number(rect[3], 0);
    NSInteger dx = number(offset[0], 0);
    NSInteger dy = number(offset[1], 0);
    id<MTLTexture> source_texture = source_info[@"color"];
    id<MTLTexture> destination_texture = destination_info[@"color"];
    if (number(event[@"source_width"], 0) != (NSInteger)source_texture.width ||
        number(event[@"source_height"], 0) != (NSInteger)source_texture.height ||
        number(event[@"destination_width"], 0) !=
            (NSInteger)destination_texture.width ||
        number(event[@"destination_height"], 0) !=
            (NSInteger)destination_texture.height) {
        if (error != NULL) {
            *error = [NSError errorWithDomain:kErrorDomain code:100
                                     userInfo:@{NSLocalizedDescriptionKey:
                                                    @"resolve/copy dimensions disagree with its resources"}];
        }
        return NO;
    }
    if (width <= 0 || height <= 0 ||
        x < 0 || y < 0 || dx < 0 || dy < 0 ||
        x + width > (NSInteger)source_texture.width ||
        y + height > (NSInteger)source_texture.height ||
        dx + width > (NSInteger)destination_texture.width ||
        dy + height > (NSInteger)destination_texture.height) {
        if (error != NULL) {
            *error = [NSError errorWithDomain:kErrorDomain code:100
                                     userInfo:@{NSLocalizedDescriptionKey:
                                                    @"resolve rectangle is outside its resources"}];
        }
        return NO;
    }
    NSInteger source_generation = number(event[@"source_framebuffer"], 0) == 0
        ? -1 : number(source_info[@"generation"], -1);
    NSInteger destination_generation_before =
        number(destination_info[@"generation"], 0);
    id<MTLComputePipelineState> pipeline = copy_pipeline(error);
    if (pipeline == nil) {
        return NO;
    }
    /* Both render targets store GL row 0 at the opposite end of the texture,
       so the copy preserves the stored row order. */
    int32_t copy_rect[4] = {(int32_t)x,
                      (int32_t)(source_texture.height - (NSUInteger)y - (NSUInteger)height),
                      (int32_t)dx,
                      (int32_t)(destination_texture.height - (NSUInteger)dy - (NSUInteger)height)};
    id<MTLCommandBuffer> command = [g_queue commandBuffer];
    id<MTLComputeCommandEncoder> encoder = [command computeCommandEncoder];
    [encoder setComputePipelineState:pipeline];
    [encoder setTexture:source_texture atIndex:0];
    [encoder setTexture:destination_texture atIndex:1];
    [encoder setBytes:copy_rect length:sizeof(copy_rect) atIndex:0];
    int32_t copy_size[2] = {(int32_t)width, (int32_t)height};
    [encoder setBytes:copy_size length:sizeof(copy_size) atIndex:1];
    MTLSize threads_per_group = MTLSizeMake(8, 8, 1);
    MTLSize groups = MTLSizeMake(((NSUInteger)width + 7) / 8,
                                 ((NSUInteger)height + 7) / 8, 1);
    [encoder dispatchThreadgroups:groups threadsPerThreadgroup:threads_per_group];
    [encoder endEncoding];
    [command commit];
    (void)wait_for_transfer(command, @"command completion", NULL);
    const char *copy_fault = getenv("EU4_FRAME_REPLAY_COPY_FAULT");
    BOOL forced_failure = copy_fault != NULL &&
                          strcmp(copy_fault, "command") == 0;
    if (forced_failure || command.status != MTLCommandBufferStatusCompleted) {
        if (error != NULL) {
            *error = [NSError errorWithDomain:kErrorDomain code:101
                                     userInfo:@{NSLocalizedDescriptionKey:
                                                    @"resolve copy command failed"}];
        }
        return NO;
    }
    destination_info[@"generation"] =
        @(number(destination_info[@"generation"], 0) + 1);
    NSInteger destination_generation_after =
        number(destination_info[@"generation"], 0);
    [g_transfer_records addObject:@{
        @"event": @(g_current_event),
        @"kind": event[@"kind"] ?: @"copy",
        @"operation": operation,
        @"source": number(event[@"source_framebuffer"], 0) == 0
            ? @"0" : source_name,
        @"source_kind": number(event[@"source_framebuffer"], 0) == 0
            ? @"metal_default" : @"metal_render_target",
        @"destination": destination,
        @"source_generation": @(source_generation),
        @"destination_generation_before": @(destination_generation_before),
        @"destination_generation_after": @(destination_generation_after),
        @"status": @"ok",
    }];
    return YES;
}

static BOOL encode_clear(NSDictionary *event, NSString *frameDir,
                         id<MTLCommandBuffer> command, NSError **error) {
    (void)frameDir;
    NSMutableDictionary *target_info = target_for(event[@"target"] ?: @{}, error);
    if (target_info == nil) {
        return NO;
    }
    NSUInteger flags = number(event[@"flags"], 1);
    BOOL clear_color = eu4_gfx_clear_color((uint32_t)flags) != 0;
    BOOL clear_depth = eu4_gfx_clear_depth((uint32_t)flags) != 0;
    BOOL clear_stencil = eu4_gfx_clear_stencil((uint32_t)flags) != 0;
    if (clear_stencil && target_info[@"depth"] == nil) {
        if (error != NULL) {
            *error = [NSError errorWithDomain:kErrorDomain code:70
                                     userInfo:@{NSLocalizedDescriptionKey:
                                                    @"stencil clear has no stencil attachment"}];
        }
        return NO;
    }
    if (clear_color) {
        target_info[@"generation"] =
            @(number(target_info[@"generation"], 0) + 1);
    }
    NSArray *color = event[@"color"];
    MTLRenderPassDescriptor *pass = [MTLRenderPassDescriptor renderPassDescriptor];
    pass.colorAttachments[0].texture = target_info[@"color"];
    pass.colorAttachments[0].storeAction = MTLStoreActionStore;
    if (clear_color) {
        pass.colorAttachments[0].loadAction = MTLLoadActionClear;
        double r = 0, g = 0, b = 0, a = 1;
        if ([color isKindOfClass:[NSArray class]] && color.count == 4) {
            r = double_number(color[0], 0);
            g = double_number(color[1], 0);
            b = double_number(color[2], 0);
            a = double_number(color[3], 1);
        }
        pass.colorAttachments[0].clearColor = MTLClearColorMake(r, g, b, a);
    } else {
        pass.colorAttachments[0].loadAction = MTLLoadActionLoad;
    }
    if (target_info[@"depth"] != nil) {
        pass.depthAttachment.texture = target_info[@"depth"];
        pass.depthAttachment.storeAction = MTLStoreActionStore;
        pass.stencilAttachment.texture = target_info[@"depth"];
        pass.stencilAttachment.storeAction = MTLStoreActionStore;
        if (clear_depth) {
            pass.depthAttachment.loadAction = MTLLoadActionClear;
            pass.depthAttachment.clearDepth =
                double_number(event[@"depth"], 1.0);
        } else {
            pass.depthAttachment.loadAction = MTLLoadActionLoad;
        }
        if (clear_stencil) {
            pass.stencilAttachment.loadAction = MTLLoadActionClear;
            pass.stencilAttachment.clearStencil =
                (uint32_t)number(event[@"stencil"], 0);
        } else {
            pass.stencilAttachment.loadAction = MTLLoadActionLoad;
        }
    }
    id<MTLRenderCommandEncoder> encoder =
        [command renderCommandEncoderWithDescriptor:pass];
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
    if (g_deadline_ns == 0) {
        [buffer waitUntilCompleted];
    } else {
        uint64_t now = replay_now_ns();
        if (now >= g_deadline_ns) {
            g_deadline_expired = 1;
            if (error != NULL) {
                *error = [NSError errorWithDomain:kErrorDomain code:83
                                         userInfo:@{NSLocalizedDescriptionKey:
                                                        [NSString stringWithFormat:
                                                             @"%@ exceeded the replay deadline",
                                                             operation]}];
            }
            return NO;
        }
        /* Wait off the engine thread so the timeout is real.  The block retains
           the command buffer; on timeout the caller reports failure without
           touching the still-running submission. */
        dispatch_semaphore_t completed = dispatch_semaphore_create(0);
        dispatch_async(dispatch_get_global_queue(
                           QOS_CLASS_USER_INITIATED, 0), ^{
            [buffer waitUntilCompleted];
            dispatch_semaphore_signal(completed);
        });
        dispatch_time_t deadline = dispatch_time(
            DISPATCH_TIME_NOW, (int64_t)(g_deadline_ns - now));
        if (dispatch_semaphore_wait(completed, deadline) != 0) {
            g_deadline_expired = 1;
            if (error != NULL) {
                *error = [NSError errorWithDomain:kErrorDomain code:83
                                         userInfo:@{NSLocalizedDescriptionKey:
                                                        [NSString stringWithFormat:
                                                             @"%@ exceeded the replay deadline",
                                                             operation]}];
            }
            return NO;
        }
    }
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
    if (!wait_for_transfer(blit, @"checkpoint readback", NULL)) {
        return NO;
    }
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

static uint64_t replay_now_ns(void) {
    struct timespec spec;
    clock_gettime(CLOCK_MONOTONIC, &spec);
    return (uint64_t)spec.tv_sec * 1000000000ull + (uint64_t)spec.tv_nsec;
}

static int replay_sha256_file(const char *path, char out[65]) {
    out[0] = 0;
    FILE *file = fopen(path, "rb");
    if (file == NULL) {
        return -1;
    }
    CC_SHA256_CTX context;
    CC_SHA256_Init(&context);
    uint8_t buffer[64 * 1024];
    size_t read_bytes = 0;
    while ((read_bytes = fread(buffer, 1, sizeof(buffer), file)) > 0) {
        CC_SHA256_Update(&context, buffer, (CC_LONG)read_bytes);
    }
    int failed = ferror(file) != 0;
    fclose(file);
    if (failed) {
        return -1;
    }
    unsigned char digest[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256_Final(digest, &context);
    static const char hex[] = "0123456789abcdef";
    for (int i = 0; i < CC_SHA256_DIGEST_LENGTH; i++) {
        out[i * 2] = hex[digest[i] >> 4];
        out[i * 2 + 1] = hex[digest[i] & 0xf];
    }
    out[64] = 0;
    return 0;
}

static BOOL verify_file_sha(NSString *path, uint64_t expected_size,
                            const char *expected_sha, NSError **error) {
    struct stat st;
    if (stat(path.fileSystemRepresentation, &st) != 0) {
        if (error != NULL) {
            *error = [NSError errorWithDomain:kErrorDomain code:110
                                     userInfo:@{NSLocalizedDescriptionKey:
                                                    [NSString stringWithFormat:@"missing file %@", path]}];
        }
        return NO;
    }
    if (expected_size != 0 && (uint64_t)st.st_size != expected_size) {
        if (error != NULL) {
            *error = [NSError errorWithDomain:kErrorDomain code:111
                                     userInfo:@{NSLocalizedDescriptionKey:
                                                    [NSString stringWithFormat:@"size mismatch for %@", path]}];
        }
        return NO;
    }
    if (expected_sha == NULL || expected_sha[0] == 0) {
        return YES;
    }
    char sha[65] = {0};
    if (replay_sha256_file(path.fileSystemRepresentation, sha) != 0 ||
        strcmp(sha, expected_sha) != 0) {
        if (error != NULL) {
            *error = [NSError errorWithDomain:kErrorDomain code:112
                                     userInfo:@{NSLocalizedDescriptionKey:
                                                    [NSString stringWithFormat:@"digest mismatch for %@", path]}];
        }
        return NO;
    }
    return YES;
}

static BOOL verify_reference_object(id object, NSString *frame_dir,
                                    NSMutableDictionary<NSString *, NSDictionary *> *seen,
                                    NSError **error) {
    if ([object isKindOfClass:[NSArray class]]) {
        for (id value in (NSArray *)object) {
            if (!verify_reference_object(value, frame_dir, seen, error)) {
                return NO;
            }
        }
        return YES;
    }
    if (![object isKindOfClass:[NSDictionary class]]) {
        return YES;
    }
    NSDictionary *dictionary = (NSDictionary *)object;
    id file_value = dictionary[@"file"];
    if (file_value == nil) {
        file_value = dictionary[@"data"];
    }
    if (file_value != nil) {
        if (![file_value isKindOfClass:[NSString class]]) {
            return NO;
        }
        NSString *name = (NSString *)file_value;
        if (name.length == 0 || [name isEqualToString:@"."] ||
            [name isEqualToString:@".."] || [name containsString:@"/"] ||
            [name containsString:@"\\"] ||
            name.pathComponents.count != 1) {
            if (error != NULL) {
                *error = [NSError errorWithDomain:kErrorDomain code:113
                                         userInfo:@{NSLocalizedDescriptionKey:
                                                        @"invalid blob path"}];
            }
            return NO;
        }
        id sha_value = dictionary[@"sha256"];
        if (![sha_value isKindOfClass:[NSString class]] ||
            [(NSString *)sha_value length] != 64) {
            if (error != NULL) {
                *error = [NSError errorWithDomain:kErrorDomain code:113
                                         userInfo:@{NSLocalizedDescriptionKey:
                                                        [NSString stringWithFormat:
                                                             @"blob %@ has no valid sha256", name]}];
            }
            return NO;
        }
        NSString *sha = (NSString *)sha_value;
        for (NSUInteger index = 0; index < sha.length; index++) {
            unichar character = [sha characterAtIndex:index];
            if (!((character >= '0' && character <= '9') ||
                  (character >= 'a' && character <= 'f') ||
                  (character >= 'A' && character <= 'F'))) {
                if (error != NULL) {
                    *error = [NSError errorWithDomain:kErrorDomain code:113
                                             userInfo:@{NSLocalizedDescriptionKey:
                                                            @"invalid blob sha256"}];
                }
                return NO;
            }
        }
        id bytes_value = dictionary[@"bytes"];
        uint64_t expected_size = 0;
        if (bytes_value != nil) {
            if (![bytes_value isKindOfClass:[NSNumber class]] ||
                [bytes_value longLongValue] < 0) {
                return NO;
            }
            expected_size = (uint64_t)[bytes_value unsignedLongLongValue];
        }
        NSDictionary *prior = seen[name];
        if (prior != nil) {
            if (![prior[@"sha256"] isEqualToString:sha] ||
                (bytes_value != nil &&
                 [prior[@"bytes"] unsignedLongLongValue] != expected_size)) {
                if (error != NULL) {
                    *error = [NSError errorWithDomain:kErrorDomain code:115
                                             userInfo:@{NSLocalizedDescriptionKey:
                                                            [NSString stringWithFormat:
                                                                 @"conflicting repeated blob reference %@", name]}];
                }
                return NO;
            }
        } else {
            NSString *path = [[frame_dir stringByAppendingPathComponent:@"blobs"]
                stringByAppendingPathComponent:name];
            if (!verify_file_sha(path, expected_size, sha.UTF8String, error)) {
                return NO;
            }
            seen[name] = @{@"sha256": sha, @"bytes": @(expected_size)};
        }
    }
    for (id value in dictionary.allValues) {
        if (!verify_reference_object(value, frame_dir, seen, error)) {
            return NO;
        }
    }
    return YES;
}

static BOOL verify_blob_references(NSString *frame_dir, NSError **error) {
    NSString *events_path =
        [frame_dir stringByAppendingPathComponent:@"frame-events.jsonl"];
    NSData *data = [NSData dataWithContentsOfFile:events_path];
    if (data == nil) {
        return NO;
    }
    NSString *text = [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding];
    if (text == nil) {
        return NO;
    }
    NSMutableDictionary<NSString *, NSDictionary *> *seen =
        [NSMutableDictionary dictionary];
    for (NSString *line in [text componentsSeparatedByString:@"\n"]) {
        if (line.length == 0) {
            continue;
        }
        NSData *line_data = [line dataUsingEncoding:NSUTF8StringEncoding];
        id object = [NSJSONSerialization JSONObjectWithData:line_data
                                                    options:0
                                                      error:NULL];
        if (object == nil) {
            if (error != NULL) {
                *error = [NSError errorWithDomain:kErrorDomain code:116
                                         userInfo:@{NSLocalizedDescriptionKey:
                                                        @"malformed frame event"}];
            }
            return NO;
        }
        if (!verify_reference_object(object, frame_dir, seen, error)) {
            return NO;
        }
    }
    return YES;
}

static BOOL verify_sealed_inputs(NSString *frame_dir, NSString *shader_dir,
                                 const eu4_metal_replay_config *config,
                                 NSError **error) {
    if (config == NULL || config->expected_event_stream_sha256 == NULL ||
        config->expected_event_stream_sha256[0] == 0) {
        return YES;
    }
    NSString *events_path =
        [frame_dir stringByAppendingPathComponent:@"frame-events.jsonl"];
    NSString *manifest_path =
        [frame_dir stringByAppendingPathComponent:@"frame-trace.json"];
    NSString *reference_path =
        [frame_dir stringByAppendingPathComponent:@"frame-reference.bgra"];
    NSString *shader_json =
        [shader_dir stringByAppendingPathComponent:@"shaders.json"];
    if (!verify_file_sha(events_path, config->expected_event_stream_bytes,
                         config->expected_event_stream_sha256, error) ||
        !verify_file_sha(manifest_path, 0, config->expected_manifest_sha256,
                         error) ||
        !verify_file_sha(reference_path, 0, config->expected_reference_sha256,
                         error) ||
        (config->expected_shader_json_sha256 != NULL &&
         config->expected_shader_json_sha256[0] != 0 &&
         !verify_file_sha(shader_json, 0,
                         config->expected_shader_json_sha256, error))) {
        return NO;
    }
    return verify_blob_references(frame_dir, error);
}

static int run_replay(EU4MetalReplayState *state, FrameTarget *default_target,
                      NSString *frame_path, NSString *shader_path,
                      NSString *output_path, NSString *checkpoint_path,
                      NSInteger stop_after, NSInteger only_event,
                      BOOL seed_marked, BOOL capture_post_images,
                      BOOL capture_pre_images, BOOL depth_from_prefix,
                      uint64_t timeout_ms,
                      const eu4_metal_replay_config *config,
                      eu4_metal_replay_result *out_result) {
    g_state = state;
    g_default_target_ptr = default_target;
    g_timeout_ms = timeout_ms;
    g_deadline_ns = timeout_ms == 0
                        ? 0
                        : replay_now_ns() + timeout_ms * 1000000ull;
    g_deadline_expired = 0;
    g_events_count = 0;
    g_encoded_count = 0;
    g_failed_count = 0;
    uint64_t started_ns = replay_now_ns();
    g_started_ns = started_ns;
    @autoreleasepool {
        NSError *verify_error = nil;
        if (!verify_sealed_inputs(frame_path, shader_path, config, &verify_error)) {
            fprintf(stderr, "sealed input verification: %s\n",
                    verify_error.localizedDescription.UTF8String);
            if (out_result != NULL) {
                snprintf(out_result->error, sizeof(out_result->error),
                         "sealed input verification failed: %s",
                         verify_error.localizedDescription.UTF8String ?: "unknown");
            }
            return 11;
        }
        [[NSFileManager defaultManager] createDirectoryAtPath:output_path
                                  withIntermediateDirectories:YES
                                                   attributes:nil
                                                        error:NULL];
        g_provenance = [NSMutableArray array];
        g_provenance_seen = [NSMutableDictionary dictionary];
        g_checkpoint_results = [NSMutableArray array];
        g_transfer_records = [NSMutableArray array];
        g_checkpoints_by_event = nil;
        g_current_event = 0;
        memset(g_default_target_ptr, 0, sizeof(*g_default_target_ptr));

        NSError *error = nil;
        NSDictionary *manifest = read_json(
            [frame_path stringByAppendingPathComponent:@"frame-trace.json"], &error);
        if (manifest == nil) {
            fprintf(stderr, "manifest: %s\n", error.localizedDescription.UTF8String);
            return 4;
        }
        NSArray *events = read_json_lines(
            [frame_path stringByAppendingPathComponent:@"frame-events.jsonl"], &error);
        g_events_count = events.count;
        if (out_result != NULL) {
            out_result->events = (uint32_t)events.count;
        }
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
        if (checkpoint_path.length > 0) {
            NSDictionary *document = read_json(checkpoint_path, &error);
            if (document == nil) {
                fprintf(stderr, "checkpoints: %s\n",
                        error.localizedDescription.UTF8String);
                return 4;
            }
            NSMutableDictionary *by_event = [NSMutableDictionary dictionary];
            for (NSDictionary *item in document[@"checkpoints"] ?: @[]) {
                NSNumber *event_index = item[@"event"];
                NSString *target = item[@"target"];
                NSString *file = item[@"file"];
                if (event_index == nil || target.length == 0 || file.length == 0) {
                    continue;
                }
                NSMutableArray *bucket = by_event[event_index];
                if (bucket == nil) {
                    bucket = [NSMutableArray array];
                    by_event[event_index] = bucket;
                }
                [bucket addObject:item];
            }
            g_checkpoints_by_event = by_event;
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
        NSMutableDictionary *default_info =
            target_for(@{@"framebuffer": @0,
                         @"width": @(g_default_target.width),
                         @"height": @(g_default_target.height)},
                       &error);
        if (default_info != nil) {
            /* target_for() may return the cached FB0 entry without repopulating
               the session default target (session reuse). */
            g_default_target.color = default_info[@"color"];
            g_default_target.depth = default_info[@"depth"];
            g_default_target.width = number(default_info[@"width"], 0);
            g_default_target.height = number(default_info[@"height"], 0);
            g_render_targets[@"0"] = default_info;
        }

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
            NSString *attachment_name = texture_key(attachment[@"name"]);
            if (attachment_name.length == 0) {
                continue;
            }
            if (g_render_targets[attachment_name] == nil) {
                g_render_targets[attachment_name] = info;
            }
        }
        /* Resolve/copy destinations are not framebuffer colour attachments in
           a draw event, so allocate their Metal targets from the captured
           destination metadata before execution. */
        for (NSDictionary *event in events) {
            NSString *kind = event[@"kind"];
            if (![kind isEqualToString:@"resolve"] &&
                ![kind isEqualToString:@"copy"]) {
                continue;
            }
            NSString *destination = texture_key(event[@"destination"]);
            if (destination.length == 0 ||
                g_render_targets[destination] != nil) {
                continue;
            }
            NSUInteger width = number(event[@"destination_width"],
                                      number(event[@"width"], 0));
            NSUInteger height = number(event[@"destination_height"],
                                       number(event[@"height"], 0));
            if (width == 0 || height == 0) {
                continue;
            }
            NSMutableDictionary *info = target_for_texture(
                destination, width, height,
                target_format_name((int)number(event[@"internal_format"], 0)),
                NULL);
            if (info != nil) {
                g_render_targets[destination] = info;
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
            if (config != NULL && config->test_inject_failure == 1 && index == 1) {
                g_deadline_ns = replay_now_ns() - 1;
            }
            if (stop_after >= 0 && (NSInteger)encoded >= stop_after) {
                break;
            }
            g_current_event = index;
            /* Execute one event per submission.  Render-target sampling may
               need a compute pass in its own command buffer; that pass must
               observe every prior event as completed or it reads stale
               contents.  Serializing also makes replay checkpoints exact. */
            if (encoded > 0) {
                [command commit];
                if (!wait_for_transfer(command, @"event boundary", &error)) {
                    fprintf(stderr, "event boundary: %s\n",
                            error.localizedDescription.UTF8String);
                    return 7;
                }
                command = [g_queue commandBuffer];
            }
            NSArray *due_checkpoints =
                isolated ? nil : g_checkpoints_by_event[@(index)];
            if (due_checkpoints.count > 0) {
                /* The checkpoint observes the state left by all prior events,
                   before this event executes. */
                command = [g_queue commandBuffer];
                for (NSDictionary *item in due_checkpoints) {
                    NSString *target_name = item[@"target"];
                    NSMutableDictionary *info = g_render_targets[target_name];
                    NSMutableDictionary *checkpoint = [@{
                        @"event": @(index),
                        @"target": target_name,
                        @"file": item[@"file"],
                        @"reference_file": item[@"reference_file"] ?: @"",
                        @"reference_sha256": item[@"reference_sha256"] ?: @"",
                        @"ok": @NO,
                    } mutableCopy];
                    if (info == nil) {
                        checkpoint[@"error"] = @"target was not created";
                        [g_checkpoint_results addObject:checkpoint];
                        continue;
                    }
                    checkpoint[@"width"] = info[@"width"] ?: @0;
                    checkpoint[@"height"] = info[@"height"] ?: @0;
                    checkpoint[@"format"] = info[@"format"] ?: @"";
                    checkpoint[@"generation"] = info[@"generation"] ?: @0;
                    NSString *path = [output_path stringByAppendingPathComponent:
                                          item[@"file"]];
                    if (capture_target_to_file(info, path)) {
                        checkpoint[@"ok"] = @YES;
                    } else {
                        checkpoint[@"error"] = @"checkpoint readback failed";
                    }
                    [g_checkpoint_results addObject:checkpoint];
                }
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
            } else if ([kind isEqualToString:@"resolve"] ||
                       [kind isEqualToString:@"copy"]) {
                ok = encode_copy_event(event, frame_path, &event_error);
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
            g_encoded_count = encoded;
            g_failed_count = failed;
        }
        [command commit];
        if (config != NULL && config->test_inject_failure == 2) {
            g_deadline_ns = replay_now_ns() - 1;
        }
        BOOL final_completed = wait_for_transfer(command, @"frame completion", &error);
        if (config != NULL && config->test_inject_failure == 4) {
            final_completed = NO;
            error = [NSError errorWithDomain:kErrorDomain code:120
                                    userInfo:@{NSLocalizedDescriptionKey:
                                                   @"injected command failure"}];
        }
        if (config != NULL && config->test_inject_failure == 3) {
            g_deadline_ns = replay_now_ns() - 1;
        }
        if (!final_completed) {
            failed++;
            [failures addObject:@{
                @"event": @(events.count),
                @"kind": @"frame",
                @"caller": @"",
                @"error": error.localizedDescription ?: @"frame did not complete",
            }];
        }
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
            BOOL readback_completed =
                wait_for_transfer(blit, @"frame readback", &error);
            if (!readback_completed) {
                failed++;
                [failures addObject:@{
                    @"event": @(events.count),
                    @"kind": @"readback",
                    @"caller": @"",
                    @"error": error.localizedDescription ?: @"readback did not complete",
                }];
            }
            uint8_t *readback = readback_completed
                                    ? (uint8_t *)malloc(bytes_per_row * height)
                                    : NULL;
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
        NSUInteger external_bindings = 0, target_bindings = 0;
        NSMutableSet *external_textures = [NSMutableSet set];
        NSMutableSet *target_textures = [NSMutableSet set];
        NSMutableArray *uninitialized = [NSMutableArray array];
        for (NSDictionary *sample in g_provenance) {
            if ([sample[@"source"] isEqualToString:@"metal_render_target"]) {
                target_bindings++;
                if ([sample[@"texture"] length] > 0) {
                    [target_textures addObject:sample[@"texture"]];
                }
            } else {
                external_bindings++;
                if ([sample[@"texture"] length] > 0) {
                    [external_textures addObject:sample[@"texture"]];
                }
            }
            if (sample[@"first_sample_before_any_write"] != nil) {
                [uninitialized addObject:@{
                    @"event": sample[@"event"],
                    @"texture": sample[@"texture"],
                    @"sampler": sample[@"sampler"],
                }];
            }
        }
        result[@"provenance"] = @{
            @"schema": @"gfx-metal-replay-provenance/v1",
            @"bindings": @(g_provenance.count),
            @"metal_render_target_bindings": @(target_bindings),
            @"external_capture_bindings": @(external_bindings),
            @"external_textures": [external_textures.allObjects
                sortedArrayUsingSelector:@selector(compare:)],
            @"render_target_textures": [target_textures.allObjects
                sortedArrayUsingSelector:@selector(compare:)],
            @"first_sample_before_any_write": uninitialized,
        };
        BOOL provenance_written = NO;
        {
            NSMutableData *lines = [NSMutableData data];
            for (NSDictionary *sample in g_provenance) {
                NSData *line = [NSJSONSerialization dataWithJSONObject:sample
                                                               options:0
                                                                 error:NULL];
                if (line != nil) {
                    [lines appendData:line];
                    [lines appendBytes:"\n" length:1];
                }
            }
            provenance_written = [lines writeToFile:[output_path
                                   stringByAppendingPathComponent:
                                       @"replay-provenance.jsonl"]
                    atomically:YES];
        }
        if (g_checkpoints_by_event != nil) {
            result[@"checkpoints"] = g_checkpoint_results;
        }
        result[@"transfers"] = g_transfer_records;
        result[@"readback"] = @(readback_ok);
        BOOL partial_mode = stop_after >= 0 || only_event >= 0;
        if (!provenance_written) {
            failed++;
            [failures addObject:@{
                @"event": @(events.count),
                @"kind": @"artifact",
                @"caller": @"",
                @"error": @"replay-provenance.jsonl write failed",
            }];
        }
        BOOL status_ok = readback_ok && failed == 0 &&
                         (partial_mode || encoded == events.count);
        result[@"failed"] = @(failed);
        result[@"failures"] = failures;
        result[@"partial"] = @(partial_mode);
        result[@"timed_out"] = @(g_deadline_expired ? YES : NO);
        if (status_ok) {
            result[@"status"] = @"ok";
        } else if (g_deadline_expired) {
            result[@"status"] = @"timeout";
        } else {
            result[@"status"] = @"failed";
        }
        NSData *json = [NSJSONSerialization dataWithJSONObject:result
                                                       options:NSJSONWritingPrettyPrinted
                                                         error:NULL];
        BOOL json_written =
            json != nil &&
            [json writeToFile:[output_path stringByAppendingPathComponent:@"replay.json"]
                   atomically:YES];
        if (!json_written) {
            failed++;
            [failures addObject:@{
                @"event": @(events.count),
                @"kind": @"artifact",
                @"caller": @"",
                @"error": @"replay.json write failed",
            }];
            status_ok = NO;
        }
        if (out_result != NULL) {
            memset(out_result, 0, sizeof(*out_result));
            out_result->struct_size = (uint32_t)sizeof(*out_result);
            out_result->abi_version = EU4_METAL_REPLAY_ABI_VERSION;
            out_result->command_status = (int32_t)command.status;
            out_result->readback_ok = readback_ok ? 1 : 0;
            out_result->events = (uint32_t)events.count;
            out_result->encoded = (uint32_t)encoded;
            out_result->failed = (uint32_t)failed;
            out_result->timed_out = g_deadline_expired ? 1 : 0;
            out_result->partial = partial_mode ? 1 : 0;
            out_result->ok = (status_ok && provenance_written && json_written &&
                              failed == 0 && readback_ok &&
                              (partial_mode || encoded == events.count) &&
                              command.status == MTLCommandBufferStatusCompleted)
                                 ? 1
                                 : 0;
            out_result->duration_ns = replay_now_ns() - started_ns;
            snprintf(out_result->result_json, sizeof(out_result->result_json),
                     "%s/replay.json", output_path.UTF8String);
            snprintf(out_result->provenance_jsonl,
                     sizeof(out_result->provenance_jsonl), "%s/%s",
                     output_path.UTF8String, "replay-provenance.jsonl");
            NSString *metal_frame_path =
                [output_path stringByAppendingPathComponent:@"metal-frame.bgra"];
            snprintf(out_result->metal_frame, sizeof(out_result->metal_frame),
                     "%s", metal_frame_path.UTF8String);
            NSString *manifest_reference =
                (manifest[@"frame"] ?: @{})[@"reference_sha256"] ?: @"";
            snprintf(out_result->manifest_reference_sha256,
                     sizeof(out_result->manifest_reference_sha256), "%s",
                     manifest_reference.UTF8String);
            (void)replay_sha256_file(out_result->metal_frame,
                                     out_result->metal_frame_sha256);
            if (!out_result->ok) {
                if (failed > 0 && failures.count > 0) {
                    snprintf(out_result->error, sizeof(out_result->error),
                             "%s", [[failures.firstObject[@"error"] ?: @"replay failed"
                                     description] UTF8String]);
                } else {
                    snprintf(out_result->error, sizeof(out_result->error),
                             "%s", readback_ok ? "replay did not complete"
                                               : "metal readback failed");
                }
            }
        }
        return failed == 0 && readback_ok ? 0 : 1;
    }
}


/* ------------------------------------------------------------------------- */
/* Stable C ABI                                                              */
/* ------------------------------------------------------------------------- */

struct eu4_metal_replay_session {
    void *state; /* retained EU4MetalReplayState */
    FrameTarget default_target;
    char error[512];
};

static void session_error(eu4_metal_replay_session *session, const char *text) {
    if (session == NULL || text == NULL) {
        return;
    }
    snprintf(session->error, sizeof(session->error), "%s", text);
}

extern "C" eu4_metal_replay_session *eu4_metal_replay_create(
    const eu4_metal_replay_config *config, char *error, size_t error_size) {
    if (error != NULL && error_size > 0) {
        error[0] = 0;
    }
    if (config == NULL ||
        config->struct_size < sizeof(eu4_metal_replay_config) ||
        config->abi_version != EU4_METAL_REPLAY_ABI_VERSION ||
        config->frame_dir == NULL || config->shader_dir == NULL ||
        config->output_dir == NULL) {
        if (error != NULL && error_size > 0) {
            snprintf(error, error_size, "invalid replay config");
        }
        return NULL;
    }
    @autoreleasepool {
        EU4MetalReplayState *state = [EU4MetalReplayState new];
        state.device = MTLCreateSystemDefaultDevice();
        if (state.device == nil) {
            if (error != NULL && error_size > 0) {
                snprintf(error, error_size, "no Metal device");
            }
            return NULL;
        }
        state.queue = [state.device newCommandQueue];
        state.pipelines = [NSMutableDictionary dictionary];
        state.functions = [NSMutableDictionary dictionary];
        state.textures = [NSMutableDictionary dictionary];
        state.samplers = [NSMutableDictionary dictionary];
        state.targets = [NSMutableDictionary dictionary];
        state.render_targets = [NSMutableDictionary dictionary];
        state.alpha_textures = [NSMutableDictionary dictionary];
        state.orientation_textures = [NSMutableDictionary dictionary];
        state.virtual_target_id = 100000;
        eu4_metal_replay_session *session =
            (eu4_metal_replay_session *)calloc(1, sizeof(*session));
        if (session == NULL) {
            if (error != NULL && error_size > 0) {
                snprintf(error, error_size, "out of memory");
            }
            return NULL;
        }
        session->state = (__bridge_retained void *)state;
        return session;
    }
}

extern "C" int eu4_metal_replay_frame(eu4_metal_replay_session *session,
                                      const eu4_metal_replay_config *config,
                                      eu4_metal_replay_result *result) {
    if (result == NULL) {
        return -1;
    }
    memset(result, 0, sizeof(*result));
    result->struct_size = (uint32_t)sizeof(*result);
    result->abi_version = EU4_METAL_REPLAY_ABI_VERSION;
    if (session == NULL || config == NULL ||
        config->struct_size < sizeof(eu4_metal_replay_config) ||
        config->abi_version != EU4_METAL_REPLAY_ABI_VERSION ||
        config->frame_dir == NULL || config->shader_dir == NULL ||
        config->output_dir == NULL) {
        snprintf(result->error, sizeof(result->error),
                 "invalid replay session or config");
        return -1;
    }
    snprintf(result->result_json, sizeof(result->result_json), "%s/replay.json",
             config->output_dir);
    snprintf(result->metal_frame, sizeof(result->metal_frame),
             "%s/metal-frame.bgra", config->output_dir);
    snprintf(result->provenance_jsonl, sizeof(result->provenance_jsonl),
             "%s/replay-provenance.jsonl", config->output_dir);
    EU4MetalReplayState *state =
        (__bridge EU4MetalReplayState *)session->state;
    NSString *frame_path = @(config->frame_dir);
    NSString *shader_path = @(config->shader_dir);
    NSString *output_path = @(config->output_dir);
    NSString *checkpoint_path =
        config->checkpoint_path != NULL ? @(config->checkpoint_path) : nil;
    int code = run_replay(state, &session->default_target, frame_path,
                          shader_path, output_path, checkpoint_path,
                          (NSInteger)config->stop_after,
                          (NSInteger)config->only_event,
                          config->seed_marked != 0,
                          config->capture_post_images != 0,
                          config->capture_pre_images != 0,
                          config->depth_from_prefix != 0,
                          config->timeout_ms, config, result);
    if (!result->ok) {
        if (result->events == 0 && g_events_count > 0) {
            result->events = (uint32_t)g_events_count;
        }
        if (result->encoded == 0 && g_encoded_count > 0) {
            result->encoded = (uint32_t)g_encoded_count;
        }
        if (result->failed == 0 && g_failed_count > 0) {
            result->failed = (uint32_t)g_failed_count;
        }
        result->timed_out = g_deadline_expired ? 1 : 0;
        if (result->duration_ns == 0) {
            result->duration_ns = replay_now_ns() - state.started_ns;
        }
    }
    /* Do not leave the thread-local selector pointing at returned state. */
    g_state = nil;
    g_default_target_ptr = NULL;
    if (result->ok) {
        session->error[0] = 0;
        return 0;
    }
    if (result->error[0] == 0) {
        snprintf(result->error, sizeof(result->error),
                 "replay failed (code %d)", code);
    }
    session_error(session, result->error);
    return code != 0 ? code : 1;
}

extern "C" void eu4_metal_replay_destroy(eu4_metal_replay_session *session) {
    if (session == NULL) {
        return;
    }
    if (session->state != NULL) {
        EU4MetalReplayState *state =
            (__bridge EU4MetalReplayState *)session->state;
        if (g_state == state) {
            g_state = nil;
            g_default_target_ptr = NULL;
        }
        CFRelease((CFTypeRef)session->state);
        session->state = NULL;
    }
    free(session);
}

extern "C" const char *eu4_metal_replay_last_error(
    eu4_metal_replay_session *session) {
    return session != NULL ? session->error : "";
}
