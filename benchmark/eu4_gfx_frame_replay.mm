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
#include <stdint.h>

#include <stdio.h>
#include <string.h>
#include <stdlib.h>

static NSString *const kErrorDomain = @"eu4.frame.replay";
static uint64_t replay_now_ns(void);
static BOOL wait_for_transfer(id<MTLCommandBuffer> buffer, NSString *operation,
                              NSError **error);

/* Every diagnostic string that reaches printf/snprintf goes through these
   helpers.  A nil NSError or a failed UTF-8 conversion must never turn into a
   null %s argument, in the replay result or on stderr. */
static const char *safe_utf8(NSString *text, const char *fallback) {
    if (text == nil) {
        return fallback;
    }
    const char *value = text.UTF8String;
    return value != NULL ? value : fallback;
}

static NSString *failure_text(NSError *error, NSString *fallback) {
    NSString *text = error.localizedDescription;
    if (text.length == 0) {
        text = fallback;
    }
    if (text.length == 0) {
        text = @"unknown replay failure";
    }
    return text;
}

/* One stable textual channel for the C ABI: "<stage> (operation N): <detail>".
   replay.json additionally carries the same fields structurally. */
static NSString *failure_message(NSString *stage, NSInteger operation,
                                 NSString *message) {
    NSString *text = message.length > 0 ? message : @"unknown replay failure";
    if (stage.length == 0) {
        stage = @"replay";
    }
    if (operation >= 0) {
        return [NSString stringWithFormat:@"%@ (operation %ld): %@",
                                          stage, (long)operation, text];
    }
    return [NSString stringWithFormat:@"%@: %@", stage, text];
}

static NSDictionary *failure_entry(NSString *stage, NSInteger operation,
                                   NSString *kind, NSString *message,
                                   BOOL timed_out, NSInteger command_status,
                                   NSString *caller) {
    return @{
        @"stage": stage.length > 0 ? stage : @"replay",
        @"operation": @(operation),
        @"event": @(operation),
        @"kind": kind.length > 0 ? kind : @"frame",
        @"caller": caller.length > 0 ? caller : @"",
        @"error": message.length > 0 ? message : @"unknown replay failure",
        @"timed_out": @(timed_out),
        @"command_status": @(command_status),
    };
}

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
/* Depth/stencil attachment identity, kept separate from the colour name map so
   an independent depth deletion invalidates only its own attachment.  A name
   resolves to at most one owning framebuffer; ambiguity fails closed. */
@property (nonatomic, strong) NSMutableDictionary<NSString *, NSMutableDictionary *> *depth_targets;
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
@property (nonatomic, strong) NSDictionary *post_checkpoints_by_event;
@property (nonatomic, strong) NSMutableArray *checkpoint_results;
@property (nonatomic, strong) NSMutableArray *transfer_records;
@property (nonatomic, strong) NSMutableDictionary<NSString *, NSMutableDictionary *> *resource_history;
@property (nonatomic, strong) NSMutableSet<NSString *> *frame_written;
@property (nonatomic, strong) NSMutableSet<NSString *> *frame_targets;
@property (nonatomic, strong) NSSet<NSString *> *current_carried;
@property (nonatomic, strong) NSMutableArray<NSDictionary *> *pending_lifecycle;
@property (nonatomic, strong) NSDictionary *sequence_record;
@property (nonatomic, strong) NSMutableDictionary *last_frame_identity;
@property (nonatomic, assign) uint64_t frame_sequence;
@property (nonatomic, assign) uint32_t persistent_history;
@property (nonatomic, assign) NSUInteger carried_bindings;
@property (nonatomic, assign) NSUInteger missing_history;
@property (nonatomic, assign) NSUInteger stale_history;
@property (nonatomic, assign) uint32_t history_status;
@property (nonatomic, assign) NSUInteger virtual_target_id;
@property (nonatomic, assign) NSUInteger current_event;
@property (nonatomic, assign) uint64_t timeout_ms;
@property (nonatomic, assign) uint64_t deadline_ns;
@property (nonatomic, assign) int deadline_expired;
@property (nonatomic, assign) uint64_t events_count;
@property (nonatomic, assign) uint64_t encoded_count;
@property (nonatomic, assign) uint64_t failed_count;
@property (nonatomic, assign) uint64_t started_ns;
/* Whether this replay wrote each output.  A failed replay must not expose a
   path left by an earlier run in the same directory. */
@property (nonatomic, assign) BOOL current_result_json;
@property (nonatomic, assign) BOOL current_provenance;
@property (nonatomic, assign) BOOL current_metal_frame;
@end

@implementation EU4MetalReplayState
@end

static thread_local EU4MetalReplayState *__unsafe_unretained g_state;
/* Consumed by run_replay after encode_draw returns; see the history failure
   paths below.  Creating the NSError inside encode_draw's autorelease pool
   made its ARC writeback unsafe on this path. */
static _Thread_local char g_history_error[512];
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
#define g_depth_targets (g_state.depth_targets)
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
#define g_post_checkpoints_by_event (g_state.post_checkpoints_by_event)
#define g_checkpoint_results (g_state.checkpoint_results)
#define g_transfer_records (g_state.transfer_records)
#define g_resource_history (g_state.resource_history)
#define g_frame_written (g_state.frame_written)
#define g_frame_targets (g_state.frame_targets)
#define g_current_carried (g_state.current_carried)
#define g_pending_lifecycle (g_state.pending_lifecycle)
#define g_sequence_record (g_state.sequence_record)
#define g_last_frame_identity (g_state.last_frame_identity)
#define g_frame_sequence (g_state.frame_sequence)
#define g_persistent_history (g_state.persistent_history)
#define g_carried_bindings (g_state.carried_bindings)
#define g_missing_history (g_state.missing_history)
#define g_stale_history (g_state.stale_history)
#define g_history_status (g_state.history_status)
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
        case 0x822E: return @"R32F";
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
        if (error != NULL && *error == nil) {
            *error = [NSError errorWithDomain:kErrorDomain code:1
                                     userInfo:@{NSLocalizedDescriptionKey:
                                                    [@"unreadable event stream " stringByAppendingString:path]}];
        }
        return nil;
    }
    NSMutableArray *rows = [NSMutableArray array];
    for (NSString *line in [text componentsSeparatedByString:@"\n"]) {
        if (line.length == 0) {
            continue;
        }
        NSData *data = [line dataUsingEncoding:NSUTF8StringEncoding];
        NSError *line_error = nil;
        id value = [NSJSONSerialization JSONObjectWithData:data options:0
                                                     error:&line_error];
        if (![value isKindOfClass:[NSDictionary class]]) {
            if (error != NULL) {
                *error = line_error ?: [NSError errorWithDomain:kErrorDomain
                                                           code:1
                                                       userInfo:@{NSLocalizedDescriptionKey:
                                                                      [@"frame event is not an object in "
                                                                          stringByAppendingString:path]}];
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
    NSError *json_error = nil;
    id value = [NSJSONSerialization JSONObjectWithData:data options:0
                                                 error:&json_error];
    if (![value isKindOfClass:[NSDictionary class]]) {
        if (error != NULL) {
            *error = json_error ?: [NSError errorWithDomain:kErrorDomain code:2
                                                   userInfo:@{NSLocalizedDescriptionKey:
                                                                  [@"not a JSON object: "
                                                                      stringByAppendingString:path]}];
        }
        return nil;
    }
    return value;
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

static int64_t signed_number(id value, int64_t fallback) {
    if ([value isKindOfClass:[NSNumber class]]) {
        return (int64_t)[(NSNumber *)value longLongValue];
    }
    return fallback;
}

static NSUInteger enum_value(id value, NSUInteger fallback);

/* Full value identity of the sampler properties Metal consumes.
 *
 * R address, compare mode/function and both LOD clamps are part of the
 * descriptor, so they must be part of the cache key.  The ambient override
 * flags are included because they change the effective min/mag filters; a
 * diagnostic override must never reuse a default descriptor. */
static NSString *sampler_key_for_state(NSDictionary *state) {
    NSMutableString *key = [NSMutableString string];
    for (NSString *field in @[@"min", @"mag", @"wrap_s", @"wrap_t",
                              @"wrap_r", @"compare_mode"]) {
        NSUInteger default_value = [field isEqualToString:@"compare_mode"]
            ? 0u : 0x2601u;
        if ([field hasPrefix:@"wrap_"]) default_value = 0x812Fu;
        [key appendFormat:@"%@=%lu|", field,
         (unsigned long)enum_value(state[field], default_value)];
    }
    /* compare_function is inert unless compare-to-texture is enabled. */
    NSUInteger compare_mode = enum_value(state[@"compare_mode"], 0);
    if (compare_mode == 0x884Eu) { /* GL_COMPARE_REF_TO_TEXTURE */
        [key appendFormat:@"compare_func=%lu|",
         (unsigned long)enum_value(state[@"compare_func"], 0x0203u)];
    }
    [key appendFormat:@"min_lod=%.17g|",
     double_number(state[@"min_lod"], 0)];
    [key appendFormat:@"max_lod=%.17g|",
     double_number(state[@"max_lod"], 1000)];
    [key appendFormat:@"lod_bias=%.17g|",
     double_number(state[@"lod_bias"], 0)];
    const char *linear_min = getenv("EU4_FRAME_REPLAY_LINEAR_MIN");
    const char *linear_all = getenv("EU4_FRAME_REPLAY_LINEAR_ALL");
    const char *nearest_all = getenv("EU4_FRAME_REPLAY_NEAREST_ALL");
    [key appendFormat:@"env=%d%d%d",
     linear_min != NULL && linear_min[0] == '1',
     linear_all != NULL && linear_all[0] == '1',
     nearest_all != NULL && nearest_all[0] == '1'];
    return key;
}

/* Map captured GL cull state to Metal.
 *
 * GL_FRONT_AND_BACK has no Metal equivalent.  It is represented as an empty
 * scissor (no fragments, no depth writes) while culling stays disabled, which
 * is the exact observable outcome for every primitive.  An unknown mode fails
 * closed instead of silently culling the wrong face.
 */
static BOOL cull_mode_for(NSDictionary *cull, MTLCullMode *mode,
                          BOOL *empty) {
    NSUInteger raw = enum_value(cull[@"mode"], 0x0405);
    if (empty != NULL) *empty = NO;
    switch (raw) {
        case 0x0404: /* GL_FRONT */
            if (mode) *mode = MTLCullModeFront;
            return YES;
        case 0x0405: /* GL_BACK */
            if (mode) *mode = MTLCullModeBack;
            return YES;
        case 0x0408: /* GL_FRONT_AND_BACK */
            if (mode) *mode = MTLCullModeNone;
            if (empty != NULL) *empty = YES;
            return YES;
        default:
            return NO;
    }
}

/* Convert a signed GL scissor box to a Metal rect.
 *
 * The intersection is computed once from signed near/far endpoints in GL
 * window coordinates and then converted to Metal's top-left origin.  Clipping
 * only the origin (while keeping the raw width) enlarged an off-screen box
 * into visible pixels.  Returns NO for an empty intersection.
 */
static BOOL scissor_rect_for(NSArray *box, NSUInteger target_width,
                             NSUInteger target_height,
                             MTLScissorRect *rect) {
    if (![box isKindOfClass:[NSArray class]] || box.count != 4) {
        return NO;
    }
    int64_t x = signed_number(box[0], 0);
    int64_t y = signed_number(box[1], 0);
    int64_t w = signed_number(box[2], (int64_t)target_width);
    int64_t h = signed_number(box[3], (int64_t)target_height);
    int64_t x0 = MAX((int64_t)0, x);
    int64_t x1 = MIN((int64_t)target_width, x + w);
    int64_t y0 = MAX((int64_t)0, y);
    int64_t y1 = MIN((int64_t)target_height, y + h);
    if (x1 <= x0 || y1 <= y0) {
        return NO;
    }
    rect->x = (NSUInteger)x0;
    rect->y = (NSUInteger)((int64_t)target_height - y1);
    rect->width = (NSUInteger)(x1 - x0);
    rect->height = (NSUInteger)(y1 - y0);
    return YES;
}

static BOOL topology_supports_culling(NSUInteger mode) {
    return mode >= 0x0004u && mode <= 0x0006u;
}

/* Effective polygon culling after enabled state, topology and the diagnostic
 * NO_CULL override.  GL_FRONT_AND_BACK is only emulated when culling would
 * actually be active; a disabled cull state or a non-polygon topology must
 * render normally. */
static BOOL effective_cull(NSDictionary *cull, NSUInteger primitive_mode,
                           BOOL no_cull, MTLCullMode *mode, BOOL *empty) {
    if (mode) *mode = MTLCullModeNone;
    if (empty) *empty = NO;
    if (no_cull || number(cull[@"enabled"], 0) == 0
        || !topology_supports_culling(primitive_mode)) {
        return YES;
    }
    return cull_mode_for(cull, mode, empty);
}

static MTLViewport viewport_for(NSArray *viewport, NSUInteger target_width,
                                NSUInteger target_height) {
    MTLViewport value;
    if ([viewport isKindOfClass:[NSArray class]] && viewport.count == 4) {
        double x = double_number(viewport[0], 0);
        double y = double_number(viewport[1], 0);
        double w = double_number(viewport[2], (double)target_width);
        double h = double_number(viewport[3], (double)target_height);
        value.originX = x;
        value.originY = (double)target_height - y - h;
        value.width = w;
        value.height = h;
    } else {
        value.originX = 0;
        value.originY = 0;
        value.width = (double)target_width;
        value.height = (double)target_height;
    }
    value.znear = 0.0;
    value.zfar = 1.0;
    return value;
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
/* Persistent cross-frame resource history                                    */
/* ------------------------------------------------------------------------- */

static NSString *target_texture_name(NSDictionary *target) {
    NSDictionary *attachment = target[@"color_attachment"];
    if ([attachment isKindOfClass:[NSDictionary class]]) {
        return texture_key(attachment[@"name"]);
    }
    return nil;
}

/* The depth/stencil attachment is an independently owned Metal resource in
   OpenGL.  Its GL name is the identity a lifecycle delete resolves against. */
static NSString *target_depth_name(NSDictionary *target) {
    NSDictionary *attachment = target[@"depth_attachment"];
    if ([attachment isKindOfClass:[NSDictionary class]]) {
        return texture_key(attachment[@"name"]);
    }
    return nil;
}

static NSString *target_resource_name(NSDictionary *target) {
    NSString *name = target_texture_name(target);
    if (name.length != 0) {
        return name;
    }
    NSNumber *framebuffer = target[@"framebuffer"];
    if ([framebuffer isKindOfClass:[NSNumber class]] &&
        [framebuffer integerValue] == 0) {
        return @"0";
    }
    return nil;
}

static NSMutableDictionary *history_entry(NSString *name) {
    if (name.length == 0) {
        return nil;
    }
    NSMutableDictionary *entry = g_resource_history[name];
    if (entry == nil) {
        entry = [@{
            @"name": name,
            @"kind": @"unknown",
            @"width": @0,
            @"height": @0,
            @"format": @"",
            @"generation": @0,
            @"last_frame": @0,
            @"last_event": @(-1),
            @"content_sha256": @"",
        } mutableCopy];
        g_resource_history[name] = entry;
    }
    return entry;
}

static void history_note_target(NSString *name, NSDictionary *info) {
    if (name.length == 0 || info == nil) {
        return;
    }
    NSMutableDictionary *entry = history_entry(name);
    entry[@"kind"] = @"renderer_generated";
    entry[@"width"] = info[@"width"] ?: @0;
    entry[@"height"] = info[@"height"] ?: @0;
    entry[@"format"] = info[@"format"] ?: @"";
    entry[@"generation"] = info[@"generation"] ?: @0;
    entry[@"last_frame"] = @(g_frame_sequence);
    entry[@"last_event"] = @(g_current_event);
}

static void history_note_external(NSString *name, NSDictionary *texture) {
    if (name.length == 0) {
        return;
    }
    NSMutableDictionary *entry = history_entry(name);
    NSArray *levels_for_first = texture[@"levels"];
    NSDictionary *first = [levels_for_first isKindOfClass:[NSArray class]]
        ? levels_for_first.firstObject : nil;
    entry[@"kind"] = @"external";
    entry[@"width"] = texture[@"width"] ?: first[@"width"] ?: @0;
    entry[@"height"] = texture[@"height"] ?: first[@"height"] ?: @0;
    entry[@"format"] = texture[@"internal_format"] ?: @"";
    entry[@"content_sha256"] = first[@"sha256"] ?: @"";
    entry[@"last_frame"] = @(g_frame_sequence);
    entry[@"last_event"] = @(g_current_event);
}

static BOOL sequence_error(NSError **error, NSString *message) {
    if (error != NULL) {
        *error = [NSError errorWithDomain:kErrorDomain code:90
                                 userInfo:@{NSLocalizedDescriptionKey: message}];
    }
    return NO;
}

static BOOL apply_lifecycle_entry(NSDictionary *item) {
    NSString *kind = string_value(item[@"kind"]);
    NSArray *names = item[@"names"] ?: @[];
    if ([kind isEqualToString:@"mutation"]) {
        for (id raw in names) {
            NSString *name = texture_key(raw);
            if (name.length == 0) {
                continue;
            }
            NSDictionary *history = g_resource_history[name];
            BOOL renderer_owned =
                [history[@"kind"] isEqualToString:@"renderer_generated"] ||
                [g_current_carried containsObject:name] ||
                [g_frame_targets containsObject:name];
            if (!renderer_owned) {
                /* External CPU content is captured at each sample; no
                   persistent Metal identity depends on the mutation. */
                continue;
            }
            g_history_status = 2;
            g_missing_history++;
            snprintf(g_history_error, sizeof(g_history_error),
                     "%s was mutated by %s at event %ld; ordered CPU texture "
                     "writes are not reproduced, so this frame cannot claim "
                     "persistent resource equivalence",
                     safe_utf8(name, "?"),
                     safe_utf8(string_value(item[@"operation"]) ?: @"?",
                               "?"),
                     (long)number(item[@"event"], -1));
            return NO;
        }
        return YES;
    }
    if (![kind isEqualToString:@"delete"] &&
        ![kind isEqualToString:@"create"] &&
        ![kind isEqualToString:@"reset"]) {
        return YES;
    }
    for (id raw in names) {
        NSString *name = texture_key(raw);
        if (name.length == 0) {
            continue;
        }
        /* A name that is a colour attachment in this frame is still owned by
           the current frame's producer graph.  Upload/delete/recreate
           ordering for that case is not captured with per-event identity yet;
           removing it here would delete a live checkpointed target.  The
           current frame's later write updates its generation. */
        /* Apply the transition at its recorded event even when the name is
           also a target in this frame: the write/clear that recreates it is
           ordered after this deletion by the event stream.  Skipping it would
           silently reuse the old Metal identity. */
        NSMutableDictionary *color_target = g_render_targets[name];
        NSMutableDictionary *depth_target = g_depth_targets[name];
        if (color_target != nil && depth_target != nil) {
            /* The same numeric name cannot be both the colour attachment of
               one target and the depth attachment of another in a replay that
               owns those identities separately. */
            g_history_status = 2;
            g_missing_history++;
            snprintf(g_history_error, sizeof(g_history_error),
                     "%s is both a colour and a depth attachment identity; "
                     "the attachment lifetime is ambiguous",
                     safe_utf8(name, "?"));
            return NO;
        }
        if (color_target != nil) {
            NSNumber *framebuffer = color_target[@"framebuffer"];
            NSString *known_color = string_value(color_target[@"color_name"]);
            if (![framebuffer isKindOfClass:[NSNumber class]] ||
                (color_target[@"color"] != nil && known_color.length != 0 &&
                 ![known_color isEqualToString:name])) {
                g_history_status = 2;
                g_missing_history++;
                snprintf(g_history_error, sizeof(g_history_error),
                         "%s could not be resolved to its colour attachment "
                         "identity; the replay fails closed",
                         safe_utf8(name, "?"));
                return NO;
            }
            /* Invalidate the colour resource and its generation only.  A live
               depth/stencil attachment keeps its Metal texture and generation
               so a colour-only clear or depth-tested draw still observes the
               original OpenGL depth contents. */
            [color_target removeObjectForKey:@"color"];
            [color_target removeObjectForKey:@"color_name"];
            color_target[@"generation"] = @0;
            if ([framebuffer unsignedIntegerValue] == 0) {
                /* The session's readback pointer is unretained; it must not
                   outlive the removed texture.  The next target_for() fills it
                   again, or the frame fails closed with a missing target. */
                g_default_target.color = nil;
            }
            if (color_target[@"depth"] == nil) {
                [g_targets removeObjectForKey:framebuffer];
            }
            [g_render_targets removeObjectForKey:name];
            [g_frame_written removeObject:name];
        }
        if (depth_target != nil) {
            /* An actual depth/stencil deletion is independent of the colour
               attachment: drop only the depth texture and its generation. */
            [depth_target removeObjectForKey:@"depth"];
            [depth_target removeObjectForKey:@"depth_name"];
            depth_target[@"depth_generation"] = @0;
            if ([depth_target[@"framebuffer"] unsignedIntegerValue] == 0) {
                g_default_target.depth = nil;
            }
            [g_depth_targets removeObjectForKey:name];
        }
        [g_resource_history removeObjectForKey:name];
        if (color_target == nil && depth_target == nil) {
            [g_render_targets removeObjectForKey:name];
        }
        [g_frame_written removeObject:name];
    }
    return YES;
}

static BOOL apply_due_lifecycle(NSInteger event) {
    NSMutableArray *remaining = [NSMutableArray array];
    for (NSDictionary *item in g_pending_lifecycle) {
        NSInteger at = number(item[@"event"], 0);
        if (at <= event) {
            if (!apply_lifecycle_entry(item)) {
                return NO;
            }
        } else {
            [remaining addObject:item];
        }
    }
    g_pending_lifecycle = remaining;
    return YES;
}

/* Reads and validates one continuous-frame record.  A missing record is a
   first-class failure when persistent history is requested: importing the
   captured OpenGL image instead would hide an earlier Metal error. */
static BOOL apply_frame_sequence(NSString *frame_dir, uint64_t expected,
                                 NSError **error) {
    NSString *path =
        [frame_dir stringByAppendingPathComponent:@"frame-sequence.json"];
    NSDictionary *document = nil;
    if ([[NSFileManager defaultManager] fileExistsAtPath:path]) {
        document = read_json(path, error);
        if (document == nil) {
            return NO;
        }
    } else if (expected > 1 || g_persistent_history) {
        return sequence_error(
            error, @"continuous frame is missing frame-sequence.json");
    } else {
        return YES;
    }
    if (![document[@"schema"] isEqualToString:@"gfx-frame-sequence/v1"]) {
        return sequence_error(error, @"continuous frame sequence schema is invalid");
    }
    NSUInteger sequence = number(document[@"sequence_index"], 0);
    if (sequence == 0 || (expected != 0 && sequence != expected)) {
        return sequence_error(
            error, [NSString stringWithFormat:
                        @"frame sequence %lu does not match expected %llu",
                        (unsigned long)sequence,
                        (unsigned long long)expected]);
    }
    NSDictionary *previous = document[@"previous"];
    if (sequence > 1) {
        if (![previous isKindOfClass:[NSDictionary class]]) {
            return sequence_error(
                error, @"continuous frame has no previous-frame identity");
        }
        if (g_last_frame_identity.count == 0) {
            return sequence_error(
                error, @"continuous session has no retained previous frame");
        }
        for (NSString *field in @[@"capture_frame_id", @"manifest_sha256",
                                  @"event_stream_sha256"]) {
            if (![previous[field] isEqual:g_last_frame_identity[field]]) {
                return sequence_error(
                    error, [NSString stringWithFormat:
                                @"previous-frame %@ does not match the session",
                                field]);
            }
        }
    } else if (g_last_frame_identity.count != 0) {
        return sequence_error(
            error, @"frame sequence restarted without destroying the session");
    }
    g_current_carried = [NSSet setWithArray:
                             document[@"carried_renderer_outputs"] ?: @[]];
    id lifecycle = document[@"lifecycle"] ?: @[];
    if (![lifecycle isKindOfClass:[NSArray class]]) {
        return sequence_error(error, @"frame lifecycle is not an array");
    }
    NSMutableArray *pending = [NSMutableArray array];
    NSUInteger sequenced = 0;
    for (NSDictionary *item in lifecycle) {
        if (![item isKindOfClass:[NSDictionary class]]) {
            return sequence_error(error, @"frame lifecycle entry is invalid");
        }
        NSString *kind = string_value(item[@"kind"]);
        NSArray *names = item[@"names"] ?: @[];
        if (![names isKindOfClass:[NSArray class]]) {
            return sequence_error(error, @"frame lifecycle names are invalid");
        }
        if (![kind isEqualToString:@"delete"] &&
            ![kind isEqualToString:@"create"] &&
            ![kind isEqualToString:@"reset"] &&
            ![kind isEqualToString:@"mutation"]) {
            return sequence_error(
                error, [NSString stringWithFormat:
                            @"unsupported lifecycle operation %@", kind]);
        }
        id sequence_field = item[@"lifecycle_sequence"];
        if (sequence_field != nil) {
            if (![sequence_field isKindOfClass:[NSNumber class]] ||
                [sequence_field longLongValue] < 0) {
                return sequence_error(
                    error, @"frame lifecycle sequence is invalid");
            }
            sequenced++;
        }
        [pending addObject:item];
    }
    if (sequenced != 0 && sequenced != pending.count) {
        /* Mixing sequenced and unsequenced transitions leaves the order of
           the unsequenced entries ambiguous. */
        return sequence_error(
            error, @"frame lifecycle mixes sequenced and unsequenced entries");
    }
    if (sequenced == pending.count && pending.count > 0) {
        NSMutableSet *identities = [NSMutableSet set];
        for (NSDictionary *item in pending) {
            NSString *identity = [NSString stringWithFormat:@"%lld:%lld",
                (long long)number(item[@"event"], 0),
                [item[@"lifecycle_sequence"] longLongValue]];
            if ([identities containsObject:identity]) {
                return sequence_error(
                    error, @"frame lifecycle repeats an ordering identity");
            }
            [identities addObject:identity];
        }
    }
    /* Stable ordering: the captured event boundary first, then the monotonic
       intra-event lifecycle sequence.  Legacy frames without a sequence keep
       their recorded order. */
    NSMutableArray *indexed = [NSMutableArray array];
    for (NSUInteger index = 0; index < pending.count; index++) {
        [indexed addObject:@{@"item": pending[index], @"index": @(index)}];
    }
    [indexed sortUsingComparator:^NSComparisonResult(
            NSDictionary *left, NSDictionary *right) {
        NSInteger left_event = number(left[@"item"][@"event"], 0);
        NSInteger right_event = number(right[@"item"][@"event"], 0);
        if (left_event != right_event) {
            return left_event < right_event ? NSOrderedAscending
                                            : NSOrderedDescending;
        }
        if (sequenced != 0) {
            long long left_sequence =
                [left[@"item"][@"lifecycle_sequence"] longLongValue];
            long long right_sequence =
                [right[@"item"][@"lifecycle_sequence"] longLongValue];
            if (left_sequence != right_sequence) {
                return left_sequence < right_sequence ? NSOrderedAscending
                                                      : NSOrderedDescending;
            }
        }
        return [left[@"index"] compare:right[@"index"]];
    }];
    NSMutableArray *ordered = [NSMutableArray arrayWithCapacity:pending.count];
    for (NSDictionary *entry in indexed) {
        [ordered addObject:entry[@"item"]];
    }
    g_pending_lifecycle = ordered;
    g_sequence_record = document;
    g_frame_sequence = sequence;
    return YES;
}

static NSDictionary *history_document(void) {
    NSMutableArray *resources = [NSMutableArray array];
    NSArray *keys = [[g_resource_history allKeys]
        sortedArrayUsingSelector:@selector(compare:)];
    for (NSString *name in keys) {
        [resources addObject:g_resource_history[name]];
    }
    return @{
        @"schema": @"gfx-metal-resource-history/v1",
        @"status": g_history_status == 2 ? @"invalid" :
                   (g_history_status == 0 ? @"disabled" : @"ok"),
        @"sequence": @(g_frame_sequence),
        @"carried_bindings": @(g_carried_bindings),
        @"missing_history": @(g_missing_history),
        @"stale_history": @(g_stale_history),
        @"previous": g_last_frame_identity ?: @{},
        @"resources": resources,
    };
}

/* Attachment identity of every live Metal target.  Colour and depth keep
   separate generations so a colour-only recreation is observable without
   implying that the retained depth contents changed. */
static NSDictionary *target_registry_document(void) {
    NSMutableArray *entries = [NSMutableArray array];
    NSArray *keys = [[g_targets allKeys]
        sortedArrayUsingComparator:^NSComparisonResult(NSNumber *left,
                                                        NSNumber *right) {
            return [left compare:right];
        }];
    for (NSNumber *key in keys) {
        NSDictionary *info = g_targets[key];
        [entries addObject:@{
            @"framebuffer": key,
            @"width": info[@"width"] ?: @0,
            @"height": info[@"height"] ?: @0,
            @"format": info[@"format"] ?: @"",
            @"color_live": @(info[@"color"] != nil),
            @"color_name": info[@"color_name"] ?: @"",
            @"color_generation": info[@"generation"] ?: @0,
            @"depth_live": @(info[@"depth"] != nil),
            @"depth_name": info[@"depth_name"] ?: @"",
            @"depth_generation": info[@"depth_generation"] ?: @0,
        }];
    }
    return @{
        @"schema": @"gfx-metal-target-registry/v1",
        @"targets": entries,
    };
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
typedef NS_ENUM(NSInteger, EU4CompressedChain) {
    EU4CompressedChainDecoded = 0,   /* no block records: legacy decoded path */
    EU4CompressedChainComplete = 1,  /* every level has matching blocks */
    EU4CompressedChainPartial = 2,   /* some levels lack blocks */
    EU4CompressedChainMixed = 3,     /* levels disagree on format/enum */
    EU4CompressedChainUnsupported = 4, /* all blocks, no Metal equivalent */
    EU4CompressedChainInvalid = 5,   /* bad geometry/size/stride */
};

static uint32_t compressed_block_bytes_for_enum(unsigned internal) {
    switch (internal) {
        case 0x83F0u: /* GL_COMPRESSED_RGB_S3TC_DXT1_EXT */
        case 0x83F1u: /* GL_COMPRESSED_RGBA_S3TC_DXT1_EXT */
            return 8;
        case 0x83F2u:
        case 0x83F3u:
            return 16;
        default:
            return 0;
    }
}

/* Classify the captured compressed chain without uploading anything.
 *
 * The distinction matters: an all-decoded legacy texture is a supported
 * fallback, while a partially compressed chain is a capture defect and must be
 * rejected rather than silently decoded.  The original GL enum is preserved so
 * DXT1 RGB and DXT1 one-bit-alpha are not conflated. */
static EU4CompressedChain compressed_chain_status(
    NSArray *levels, NSString **format_out, unsigned *internal_out,
    NSUInteger *block_out, NSUInteger *bad_level_out) {
    NSUInteger compressed_count = 0;
    NSUInteger expected_count = 0;
    NSUInteger count = levels.count;
    NSString *format = nil;
    unsigned internal = 0;
    NSUInteger block = 0;
    for (NSUInteger index = 0; index < count; index++) {
        NSDictionary *level = levels[index];
        if (number(level[@"compressed_expected"], 0) != 0) {
            expected_count++;
        }
        NSDictionary *compressed = level[@"compressed"];
        if (![compressed isKindOfClass:[NSDictionary class]]) {
            continue;
        }
        compressed_count++;
        unsigned raw = (unsigned)number(compressed[@"internal_format"], 0);
        NSString *value = string_value(compressed[@"format"]);
        if (raw == 0) {
            /* Pre-enum captures name the format only; preserve the meaning
             * they were captured with without inventing a raw enum. */
            if ([value isEqualToString:@"DXT1"]
                || [value isEqualToString:@"DXT1_ALPHA"]) {
                raw = 0x83F1u;
            } else if ([value isEqualToString:@"DXT3"]) {
                raw = 0x83F2u;
            } else if ([value isEqualToString:@"DXT5"]) {
                raw = 0x83F3u;
            }
        }
        if (raw == 0 || value.length == 0) {
            if (bad_level_out) *bad_level_out = index;
            return EU4CompressedChainUnsupported;
        }
        if (format == nil) {
            format = value;
            internal = raw;
            block = compressed_block_bytes_for_enum(raw);
            if (block == 0) {
                if (bad_level_out) *bad_level_out = index;
                return EU4CompressedChainUnsupported;
            }
        } else if (![format isEqualToString:value] || internal != raw) {
            if (bad_level_out) *bad_level_out = index;
            return EU4CompressedChainMixed;
        }
        NSUInteger width = number(level[@"width"], 0);
        NSUInteger height = number(level[@"height"], 0);
        NSUInteger bytes = number(compressed[@"bytes"], 0);
        NSUInteger stride = number(compressed[@"row_stride"], 0);
        if (width == 0 || height == 0) {
            if (bad_level_out) *bad_level_out = index;
            return EU4CompressedChainInvalid;
        }
        NSUInteger blocks_wide = (width + 3u) / 4u;
        NSUInteger blocks_high = (height + 3u) / 4u;
        NSUInteger expected = blocks_wide * blocks_high * block;
        if (bytes != expected || stride != blocks_wide * block) {
            if (bad_level_out) *bad_level_out = index;
            return EU4CompressedChainInvalid;
        }
        if (number(level[@"level"], index) != index) {
            if (bad_level_out) *bad_level_out = index;
            return EU4CompressedChainInvalid;
        }
    }
    if (compressed_count == 0 && expected_count > 0) {
        if (bad_level_out) {
            for (NSUInteger index = 0; index < count; index++) {
                if (number(levels[index][@"compressed_expected"], 0) != 0) {
                    *bad_level_out = index;
                    break;
                }
            }
        }
        return EU4CompressedChainInvalid;
    }
    if (compressed_count == 0) {
        return EU4CompressedChainDecoded;
    }
    if (compressed_count != count) {
        for (NSUInteger index = 0; index < count; index++) {
            if (![levels[index][@"compressed"] isKindOfClass:[NSDictionary class]]) {
                if (bad_level_out) *bad_level_out = index;
                break;
            }
        }
        return EU4CompressedChainPartial;
    }
    if (format_out) *format_out = format;
    if (internal_out) *internal_out = internal;
    if (block_out) *block_out = block;
    /* Metal has no BC1 RGB format.  Decoding a GL DXT1 RGB level preserves its
     * guaranteed opaque alpha; using BC1_RGBA would expose the block's
     * one-bit transparent index and change sampled alpha. */
    if (internal == 0x83F0u) {
        return EU4CompressedChainDecoded;
    }
    return EU4CompressedChainComplete;
}

static MTLPixelFormat compressed_pixel_format(NSString *format) {
    if ([format isEqualToString:@"DXT1"]
        || [format isEqualToString:@"DXT1_ALPHA"]) {
        return MTLPixelFormatBC1_RGBA;
    }
    if ([format isEqualToString:@"DXT3"]) {
        return MTLPixelFormatBC2_RGBA;
    }
    if ([format isEqualToString:@"DXT5"]) {
        return MTLPixelFormatBC3_RGBA;
    }
    return MTLPixelFormatInvalid;
}

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
    NSString *compressed_format = nil;
    unsigned compressed_internal = 0;
    NSUInteger compressed_block = 0;
    NSUInteger bad_level = 0;
    EU4CompressedChain chain = cube ? EU4CompressedChainDecoded
        : compressed_chain_status(levels, &compressed_format,
                                  &compressed_internal, &compressed_block,
                                  &bad_level);
    if (chain == EU4CompressedChainPartial
        || chain == EU4CompressedChainMixed
        || chain == EU4CompressedChainUnsupported
        || chain == EU4CompressedChainInvalid) {
        NSString *reason = nil;
        switch (chain) {
            case EU4CompressedChainPartial:
                reason = [NSString stringWithFormat:
                          @"compressed texture chain is partial at level %lu",
                          (unsigned long)bad_level];
                break;
            case EU4CompressedChainMixed:
                reason = [NSString stringWithFormat:
                          @"compressed texture chain mixes formats at level %lu",
                          (unsigned long)bad_level];
                break;
            case EU4CompressedChainUnsupported:
                reason = [NSString stringWithFormat:
                          @"unsupported compressed texture format %@ at level %lu",
                          compressed_format ?: @"<missing>",
                          (unsigned long)bad_level];
                break;
            default:
                reason = [NSString stringWithFormat:
                          @"compressed texture level %lu has invalid geometry, "
                          @"size or stride", (unsigned long)bad_level];
                break;
        }
        if (error != NULL) {
            *error = [NSError errorWithDomain:kErrorDomain code:25
                                     userInfo:@{NSLocalizedDescriptionKey: reason}];
        }
        return nil;
    }
    BOOL compressed = chain == EU4CompressedChainComplete;
    MTLPixelFormat compressed_pixel = compressed
        ? compressed_pixel_format(compressed_format) : MTLPixelFormatInvalid;
    if (compressed && compressed_pixel == MTLPixelFormatInvalid) {
        if (error != NULL) {
            *error = [NSError errorWithDomain:kErrorDomain code:25
                                     userInfo:@{NSLocalizedDescriptionKey:
                                                    [@"unsupported compressed texture format "
                                                     stringByAppendingString:compressed_format ?: @"<missing>"]}];
        }
        return nil;
    }
    if (compressed) {
        BOOL bc_supported = YES;
        if (@available(macOS 11.0, *)) {
            bc_supported = g_device.supportsBCTextureCompression;
        }
        /* Host-test only: exercise the unsupported-device refusal on a device
         * that does support BC.  Never used by a normal replay. */
        const char *force_no_bc = getenv("EU4_FRAME_REPLAY_FORCE_NO_BC");
        if (force_no_bc != NULL && strcmp(force_no_bc, "1") == 0) {
            bc_supported = NO;
        }
        if (!bc_supported) {
            if (error != NULL) {
                *error = [NSError errorWithDomain:kErrorDomain code:27
                                         userInfo:@{NSLocalizedDescriptionKey:
                                                        @"the active Metal device does not support BC texture compression"}];
            }
            return nil;
        }
        pixel_format = compressed_pixel;
    }
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
        if (compressed) {
            NSDictionary *block = level[@"compressed"];
            NSData *blob = read_blob(frameDir, string_value(block[@"file"]));
            NSUInteger expected = number(block[@"bytes"], 0);
            NSUInteger row_bytes = number(block[@"row_stride"], 0);
            NSUInteger expected_width = width >> level_index;
            NSUInteger expected_height = height >> level_index;
            if (expected_width == 0) expected_width = 1;
            if (expected_height == 0) expected_height = 1;
            NSUInteger blocks_wide = (level_width + 3u) / 4u;
            NSUInteger blocks_high = (level_height + 3u) / 4u;
            NSUInteger expected_bytes =
                blocks_wide * blocks_high * compressed_block;
            NSUInteger expected_stride = blocks_wide * compressed_block;
            if (blob == nil || compressed_block == 0
                || level_width != expected_width
                || level_height != expected_height
                || expected == 0 || blob.length != expected
                || expected != expected_bytes
                || row_bytes != expected_stride) {
                if (error != NULL) {
                    *error = [NSError errorWithDomain:kErrorDomain code:26
                                             userInfo:@{NSLocalizedDescriptionKey:
                                                            [NSString stringWithFormat:
                                                                 @"compressed texture level %lu is incomplete or has invalid geometry",
                                                                 (unsigned long)level_index]}];
                }
                return nil;
            }
            /* The uncompressed path deliberately uploads rows unchanged:
             * captured UVs address the first uploaded row as coordinate 0 in
             * both APIs.  Compressed blocks must follow the same convention;
             * flipping only block rows would mirror BC textures relative to
             * the decoded path and would not flip intra-block texels. */
            MTLRegion region = MTLRegionMake2D(0, 0, level_width, level_height);
            [texture_handle replaceRegion:region
                             mipmapLevel:level_index
                               withBytes:blob.bytes
                             bytesPerRow:row_bytes];
            continue;
        }
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
        NSDictionary *compressed = level[@"compressed"];
        if ([compressed isKindOfClass:[NSDictionary class]]) {
            [key appendFormat:@".c%@", string_value(compressed[@"sha256"]) ?: @""];
        }
        for (NSDictionary *face in level[@"faces"] ?: @[]) {
            [key appendFormat:@".%@", string_value(face[@"sha256"]) ?: @""];
        }
    }
    return key;
}

/* ------------------------------------------------------------------------- */
/* Render targets                                                             */
/* ------------------------------------------------------------------------- */

static MTLPixelFormat pixel_format_for_name(NSString *format_name) {
    if (format_name != nil) {
        if ([format_name isEqualToString:@"RGBA8"] ||
            [format_name isEqualToString:@"other"]) {
            return MTLPixelFormatRGBA8Unorm;
        }
        if ([format_name isEqualToString:@"RGB8"] ||
            [format_name isEqualToString:@"SRGB8_ALPHA8"]) {
            return MTLPixelFormatBGRA8Unorm;
        }
        if ([format_name isEqualToString:@"R32F"]) {
            return MTLPixelFormatR32Float;
        }
    }
    return MTLPixelFormatBGRA8Unorm;
}

static id<MTLTexture> allocate_color_texture(NSUInteger width, NSUInteger height,
                                             MTLPixelFormat pixel_format) {
    MTLTextureDescriptor *descriptor = [MTLTextureDescriptor new];
    descriptor.textureType = MTLTextureType2D;
    descriptor.pixelFormat = pixel_format;
    descriptor.width = width;
    descriptor.height = height;
    descriptor.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead;
    descriptor.storageMode = MTLStorageModePrivate;
    return [g_device newTextureWithDescriptor:descriptor];
}

static id<MTLTexture> allocate_depth_texture(NSUInteger width, NSUInteger height) {
    MTLTextureDescriptor *descriptor = [MTLTextureDescriptor new];
    descriptor.textureType = MTLTextureType2D;
    descriptor.pixelFormat = MTLPixelFormatDepth32Float_Stencil8;
    descriptor.width = width;
    descriptor.height = height;
    descriptor.usage = MTLTextureUsageRenderTarget;
    descriptor.storageMode = MTLStorageModePrivate;
    return [g_device newTextureWithDescriptor:descriptor];
}

/* Look up or allocate the Metal allocation behind one captured framebuffer.
   Colour and depth/stencil are separate attachments with separate lifetimes:
   deleting a colour texture invalidates only the colour identity, while an
   independently deleted depth attachment reallocates only the depth texture.
   A request that names a different attachment identity for an existing
   framebuffer is an incompatible change and fails closed. */
static NSMutableDictionary *target_for(NSDictionary *target, NSError **error) {
    NSNumber *key = @(number(target[@"framebuffer"], 0));
    NSUInteger framebuffer = [key unsignedIntegerValue];
    NSUInteger requested_width = number(target[@"width"], 0);
    NSUInteger requested_height = number(target[@"height"], 0);
    NSDictionary *requested_attachment = target[@"color_attachment"];
    NSString *requested_format = requested_attachment
        ? string_value(requested_attachment[@"internal_format"]) : nil;
    NSString *requested_depth_name = target_depth_name(target);
    id requested_depth_field = target[@"depth_attachment"];
    /* A depth attachment may be declared without a GL name (for example a
       renderbuffer attached directly to an FBO).  It still owns a Metal
       allocation, but no lifecycle delete can address it independently. */
    BOOL has_depth_attachment =
        [requested_depth_field isKindOfClass:[NSDictionary class]];
    NSMutableDictionary *existing = g_targets[key];
    if (existing != nil) {
        NSUInteger existing_width = number(existing[@"width"], 0);
        NSUInteger existing_height = number(existing[@"height"], 0);
        BOOL incompatible =
            (requested_width != 0 && requested_width != existing_width) ||
            (requested_height != 0 && requested_height != existing_height);
        if (!incompatible && existing[@"color"] != nil &&
            requested_format.length != 0 &&
            ![requested_format isEqualToString:string_value(existing[@"format"])]) {
            incompatible = YES;
        }
        if (incompatible) {
            if (error != NULL) {
                *error = [NSError errorWithDomain:kErrorDomain code:31
                    userInfo:@{NSLocalizedDescriptionKey:
                        @"render target was recreated with incompatible "
                        @"size or format; the retained Metal identity cannot "
                        @"be reused"}];
            }
            return nil;
        }
        BOOL want_depth = has_depth_attachment || framebuffer == 0;
        if (requested_depth_name.length != 0) {
            NSMutableDictionary *owner = g_depth_targets[requested_depth_name];
            if (owner != nil && owner != existing) {
                if (error != NULL) {
                    *error = [NSError errorWithDomain:kErrorDomain code:32
                        userInfo:@{NSLocalizedDescriptionKey:
                            @"depth attachment is shared with another "
                            @"framebuffer; the replay cannot preserve both "
                            @"Metal identities"}];
                }
                return nil;
            }
            NSString *known_depth = string_value(existing[@"depth_name"]);
            if (existing[@"depth"] != nil && known_depth.length != 0 &&
                ![known_depth isEqualToString:requested_depth_name]) {
                if (error != NULL) {
                    *error = [NSError errorWithDomain:kErrorDomain code:32
                        userInfo:@{NSLocalizedDescriptionKey:
                            @"render target changed its depth attachment "
                            @"identity; the retained Metal depth contents "
                            @"cannot be reused"}];
                }
                return nil;
            }
        }
        if (want_depth && existing[@"depth"] == nil) {
            id<MTLTexture> depth =
                allocate_depth_texture(existing_width, existing_height);
            if (depth == nil) {
                if (error != NULL) {
                    *error = [NSError errorWithDomain:kErrorDomain code:30
                        userInfo:@{NSLocalizedDescriptionKey:
                            @"render target depth allocation failed"}];
                }
                return nil;
            }
            existing[@"depth"] = depth;
            existing[@"depth_generation"] = @0;
            existing[@"depth_name"] =
                requested_depth_name.length != 0 ? requested_depth_name : @"";
            if (framebuffer == 0) {
                g_default_target.depth = depth;
            }
        }
        if (requested_depth_name.length != 0) {
            /* The first explicit name for an anonymous attachment becomes its
               identity; a later different name then fails closed. */
            if (string_value(existing[@"depth_name"]).length == 0) {
                existing[@"depth_name"] = requested_depth_name;
            }
            g_depth_targets[requested_depth_name] = existing;
        }
        if (existing[@"color"] != nil) {
            return existing;
        }
        /* The colour identity was invalidated by a lifecycle delete.  Allocate
           a fresh colour texture so the recreated GL name never observes the
           deleted contents, but keep the live depth attachment in place. */
        id<MTLTexture> color = allocate_color_texture(
            existing_width, existing_height,
            pixel_format_for_name(string_value(existing[@"format"])));
        if (color == nil) {
            if (error != NULL) {
                *error = [NSError errorWithDomain:kErrorDomain code:30
                    userInfo:@{NSLocalizedDescriptionKey:
                        @"render target allocation failed"}];
            }
            return nil;
        }
        existing[@"color"] = color;
        existing[@"generation"] = @0;
        if (framebuffer == 0) {
            g_default_target.color = color;
            g_default_target.width = existing_width;
            g_default_target.height = existing_height;
        }
        return existing;
    }
    NSUInteger width = requested_width;
    NSUInteger height = requested_height;
    NSString *format_name = requested_format;
    MTLPixelFormat pixel_format = pixel_format_for_name(format_name);
    if (width == 0 || height == 0) {
        width = g_default_target.width;
        height = g_default_target.height;
    }
    id<MTLTexture> color = allocate_color_texture(width, height, pixel_format);
    id<MTLTexture> depth = nil;
    if (has_depth_attachment || framebuffer == 0) {
        depth = allocate_depth_texture(width, height);
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
        value[@"depth_generation"] = @0;
        value[@"depth_name"] = requested_depth_name.length != 0
            ? requested_depth_name : @"";
    }
    g_targets[key] = value;
    if (requested_depth_name.length != 0) {
        g_depth_targets[requested_depth_name] = value;
    }
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

/* The effective input of one native shader object is the MSL bytes that are
   actually compiled, the entry point, the stage kind and the compile options.
   A filename alone is not an identity: a diagnostic override can leave the
   name unchanged while changing the bytes, and a persistent sequence session
   can replay a later frame whose shader directory has a different file under
   the same name. */
static NSString *shader_compile_options_key(void) {
    const char *precise_env = getenv("EU4_FRAME_REPLAY_PRECISE_MATH");
    return (precise_env != NULL && strcmp(precise_env, "1") == 0)
        ? @"fastmath-off" : @"fastmath-on";
}

static NSString *shader_source_identity(NSString *path, NSString **source_out,
                                        NSError **error) {
    NSError *read_error = nil;
    NSData *data = [NSData dataWithContentsOfFile:path
                                          options:0
                                            error:&read_error];
    if (data == nil) {
        if (error != NULL) {
            *error = read_error ?: [NSError errorWithDomain:kErrorDomain
                                                       code:51
                                                   userInfo:@{NSLocalizedDescriptionKey:
                                                                  @"MSL source unreadable"}];
        }
        return nil;
    }
    unsigned char digest[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256(data.bytes, (CC_LONG)data.length, digest);
    NSMutableString *hex =
        [NSMutableString stringWithCapacity:CC_SHA256_DIGEST_LENGTH * 2];
    for (NSUInteger index = 0; index < CC_SHA256_DIGEST_LENGTH; index++) {
        [hex appendFormat:@"%02x", digest[index]];
    }
    if (source_out != NULL) {
        NSString *source = [[NSString alloc] initWithData:data
                                                 encoding:NSUTF8StringEncoding];
        if (source == nil) {
            if (error != NULL) {
                *error = [NSError errorWithDomain:kErrorDomain code:51
                                         userInfo:@{NSLocalizedDescriptionKey:
                                                        @"MSL source is not UTF-8"}];
            }
            return nil;
        }
        *source_out = source;
    }
    return hex;
}

static id<MTLFunction> function_for(NSString *shaderDir, NSDictionary *stage,
                                    NSString **identity_out, NSError **error) {
    NSString *file = string_value(stage[@"msl_file"]);
    NSString *entry = string_value(stage[@"msl_entry"]);
    NSString *kind = string_value(stage[@"stage"]) ?: @"";
    if (file.length == 0 || entry.length == 0) {
        if (error != NULL) {
            *error = [NSError errorWithDomain:kErrorDomain code:50
                                     userInfo:@{NSLocalizedDescriptionKey:
                                                    @"stage metadata incomplete"}];
        }
        return nil;
    }
    NSString *path = [shaderDir stringByAppendingPathComponent:file];
    NSString *source = nil;
    NSString *digest = shader_source_identity(path, &source, error);
    if (digest == nil) {
        return nil;
    }
    NSString *options_key = shader_compile_options_key();
    NSString *key = [NSString stringWithFormat:@"%@#%@#%@#%@", digest, entry,
                                               kind, options_key];
    if (identity_out != NULL) {
        *identity_out = key;
    }
    id<MTLFunction> cached = g_functions[key];
    if (cached != nil) {
        return cached;
    }
    MTLCompileOptions *options = [MTLCompileOptions new];
    options.languageVersion = MTLLanguageVersion2_1;
    /* Diagnostic A/B: GLSL compilation in the engine does not use Metal's
       fast-math mode.  This switch lets an offline experiment distinguish a
       fast-math translation difference from a rendering-state difference. */
    const char *precise_env = getenv("EU4_FRAME_REPLAY_PRECISE_MATH");
    if (precise_env != NULL && strcmp(precise_env, "1") == 0) {
        options.fastMathEnabled = NO;
    }
    NSError *read_error = nil;
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

/* ------------------------------------------------------------------------- */
/* Opt-in uv2 diagnostic probe                                               */
/* ------------------------------------------------------------------------- */
/* Disabled by default.  EU4_FRAME_REPLAY_UV2_PROBE=1 selects one draw event and
 * encodes a second, isolated render pass in the same command buffer: the same
 * translated vertex function, vertex buffers, vertex uniforms, viewport and
 * culling as the normal pass, with a substitute fragment function that writes
 * the interpolated uv2 varying into an RGBA32F texture.  The normal pass and
 * the translated shaders are unchanged.  The output is diagnostic evidence
 * only and never replaces the frame readback. */

typedef struct {
    int enabled;
    int event;
    int program;
    int framebuffer;
    int texture;
    char dir[1024];
    char varying[64];
} uv2_probe_config;

static uv2_probe_config g_uv2_probe;
static int g_uv2_probe_done;
static int g_uv2_probe_status; /* 0 skipped, 1 ok, 2 error */
static char g_uv2_probe_error[512];
static NSMutableArray *g_uv2_probe_records;
static NSMutableDictionary *g_uv2_probe_failure;
static NSDictionary *g_uv2_probe_capture_identity;
static id<MTLFunction> g_uv2_probe_fragment_function;

static void uv2_probe_reset(void) {
    memset(&g_uv2_probe, 0, sizeof(g_uv2_probe));
    g_uv2_probe.event = 1;
    g_uv2_probe.program = 310;
    g_uv2_probe.framebuffer = 6;
    g_uv2_probe.texture = 433;
    snprintf(g_uv2_probe.varying, sizeof(g_uv2_probe.varying), "%s",
             "v_TEXCOORD1");
    g_uv2_probe_done = 0;
    g_uv2_probe_status = 0;
    g_uv2_probe_error[0] = 0;
    g_uv2_probe_records = [NSMutableArray array];
    g_uv2_probe_failure = nil;
    g_uv2_probe_capture_identity = nil;
    g_uv2_probe_fragment_function = nil;
}

static int uv2_probe_parse_int(const char *text, int fallback) {
    if (text == NULL || text[0] == 0) {
        return fallback;
    }
    char *end = NULL;
    long value = strtol(text, &end, 0);
    if (end == text || *end != 0 || value < 0 || value > INT_MAX) {
        return -1;
    }
    return (int)value;
}

static void uv2_probe_configure_from_env(void) {
    uv2_probe_reset();
    const char *enabled = getenv("EU4_FRAME_REPLAY_UV2_PROBE");
    if (enabled == NULL || strcmp(enabled, "1") != 0) {
        return;
    }
    int value = uv2_probe_parse_int(
        getenv("EU4_FRAME_REPLAY_UV2_PROBE_EVENT"), g_uv2_probe.event);
    if (value < 0) {
        g_uv2_probe_status = 2;
        snprintf(g_uv2_probe_error, sizeof(g_uv2_probe_error),
                 "invalid probe event");
        return;
    }
    g_uv2_probe.event = value;
    value = uv2_probe_parse_int(getenv("EU4_FRAME_REPLAY_UV2_PROBE_PROGRAM"),
                                g_uv2_probe.program);
    if (value < 0) {
        g_uv2_probe_status = 2;
        snprintf(g_uv2_probe_error, sizeof(g_uv2_probe_error),
                 "invalid probe program");
        return;
    }
    g_uv2_probe.program = value;
    value = uv2_probe_parse_int(
        getenv("EU4_FRAME_REPLAY_UV2_PROBE_FRAMEBUFFER"),
        g_uv2_probe.framebuffer);
    if (value < 0) {
        g_uv2_probe_status = 2;
        snprintf(g_uv2_probe_error, sizeof(g_uv2_probe_error),
                 "invalid probe framebuffer");
        return;
    }
    g_uv2_probe.framebuffer = value;
    value = uv2_probe_parse_int(getenv("EU4_FRAME_REPLAY_UV2_PROBE_TEXTURE"),
                                g_uv2_probe.texture);
    if (value < 0) {
        g_uv2_probe_status = 2;
        snprintf(g_uv2_probe_error, sizeof(g_uv2_probe_error),
                 "invalid probe texture");
        return;
    }
    g_uv2_probe.texture = value;
    const char *varying = getenv("EU4_FRAME_REPLAY_UV2_PROBE_VARYING");
    if (varying != NULL && varying[0] != 0) {
        size_t length = strlen(varying);
        if (length >= sizeof(g_uv2_probe.varying)) {
            g_uv2_probe_status = 2;
            snprintf(g_uv2_probe_error, sizeof(g_uv2_probe_error),
                     "probe varying name is too long");
            return;
        }
        for (size_t i = 0; i < length; i++) {
            char character = varying[i];
            if (!((character >= 'a' && character <= 'z') ||
                  (character >= 'A' && character <= 'Z') ||
                  (character >= '0' && character <= '9') ||
                  character == '_')) {
                g_uv2_probe_status = 2;
                snprintf(g_uv2_probe_error, sizeof(g_uv2_probe_error),
                         "probe varying is not an identifier");
                return;
            }
        }
        snprintf(g_uv2_probe.varying, sizeof(g_uv2_probe.varying), "%s",
                 varying);
    }
    const char *dir = getenv("EU4_FRAME_REPLAY_UV2_PROBE_DIR");
    if (dir != NULL && dir[0] != 0) {
        if (dir[0] != '/') {
            g_uv2_probe_status = 2;
            snprintf(g_uv2_probe_error, sizeof(g_uv2_probe_error),
                     "probe directory is not absolute");
            return;
        }
        snprintf(g_uv2_probe.dir, sizeof(g_uv2_probe.dir), "%s", dir);
    }
    g_uv2_probe.enabled = 1;
}

static NSString *uv2_probe_sha256(NSData *data) {
    unsigned char digest[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256(data.bytes, (CC_LONG)data.length, digest);
    NSMutableString *hex =
        [NSMutableString stringWithCapacity:CC_SHA256_DIGEST_LENGTH * 2];
    for (NSUInteger index = 0; index < CC_SHA256_DIGEST_LENGTH; index++) {
        [hex appendFormat:@"%02x", digest[index]];
    }
    return hex;
}

static void uv2_probe_flip_rows(const uint8_t *source, uint8_t *destination,
                                NSUInteger width, NSUInteger height,
                                NSUInteger pixel_bytes) {
    NSUInteger stride = width * pixel_bytes;
    for (NSUInteger row = 0; row < height; row++) {
        memcpy(destination + row * stride,
               source + (height - 1 - row) * stride, stride);
    }
}

/* Builds the substitute fragment stage from the translated vertex stage's
 * declared varying location.  The location is part of the translation report,
 * so the probe reads exactly the varying the original vertex stage writes. */
static NSString *uv2_probe_fragment_source(NSDictionary *program_entry,
                                           NSDictionary *vertex_stage,
                                           NSInteger *location_out,
                                           NSError **error) {
    NSString *varying = [NSString stringWithUTF8String:g_uv2_probe.varying];
    NSDictionary *locations = program_entry[@"varying_locations"] ?: @{};
    NSNumber *location = locations[varying];
    if (location == nil) {
        if (error != NULL) {
            *error = [NSError errorWithDomain:kErrorDomain code:130
                                     userInfo:@{NSLocalizedDescriptionKey:
                                                    [NSString stringWithFormat:
                                                         @"translated program has no varying %@",
                                                         varying]}];
        }
        return nil;
    }
    NSString *type = nil;
    for (NSDictionary *item in vertex_stage[@"varyings"] ?: @[]) {
        if ([string_value(item[@"name"]) isEqualToString:varying]) {
            type = string_value(item[@"type"]);
            break;
        }
    }
    if (![type isEqualToString:@"float2"]) {
        if (error != NULL) {
            *error = [NSError errorWithDomain:kErrorDomain code:131
                                     userInfo:@{NSLocalizedDescriptionKey:
                                                    [NSString stringWithFormat:
                                                         @"varying %@ is not float2",
                                                         varying]}];
        }
        return nil;
    }
    *location_out = location.integerValue;
    return [NSString stringWithFormat:
                @"#include <metal_stdlib>\n"
                 "using namespace metal;\n"
                 "struct UV2ProbeIn { float2 value [[user(locn%ld)]]; };\n"
                 "struct UV2ProbeOut { float4 color [[color(0)]]; };\n"
                 "fragment UV2ProbeOut uv2_probe(UV2ProbeIn in [[stage_in]]) {\n"
                 "    UV2ProbeOut out; out.color = float4(in.value, 0.0, 1.0);\n"
                 "    return out;\n"
                 "}\n",
                (long)location.integerValue];
}

static id<MTLFunction> uv2_probe_compile_fragment(NSDictionary *program_entry,
                                                   NSDictionary *vertex_stage,
                                                   NSInteger *location_out,
                                                   NSError **error) {
    if (g_uv2_probe_fragment_function != nil) {
        return g_uv2_probe_fragment_function;
    }
    NSString *source = uv2_probe_fragment_source(program_entry, vertex_stage,
                                                 location_out, error);
    if (source == nil) {
        return nil;
    }
    NSError *compile_error = nil;
    id<MTLLibrary> library = [g_device newLibraryWithSource:source
                                                    options:nil
                                                      error:&compile_error];
    id<MTLFunction> function =
        library != nil ? [library newFunctionWithName:@"uv2_probe"] : nil;
    if (function == nil) {
        if (error != NULL) {
            *error = compile_error ?: [NSError errorWithDomain:kErrorDomain
                                                          code:132
                                                      userInfo:@{NSLocalizedDescriptionKey:
                                                                     @"uv2 probe fragment compile failed"}];
        }
        return nil;
    }
    g_uv2_probe_fragment_function = function;
    return function;
}

static void uv2_probe_write_descriptor(NSString *directory,
                                       NSDictionary *document) {
    [[NSFileManager defaultManager] createDirectoryAtPath:directory
                              withIntermediateDirectories:YES
                                               attributes:nil
                                                    error:NULL];
    NSData *json = [NSJSONSerialization dataWithJSONObject:document
                                                   options:NSJSONWritingPrettyPrinted
                                                     error:NULL];
    if (json != nil) {
        [json writeToFile:[directory
                              stringByAppendingPathComponent:@"uv2-probe.json"]
               atomically:YES];
    }
}

static void uv2_probe_record_failure(NSString *reason, NSDictionary *event,
                                     NSDictionary *observed) {
    if (g_uv2_probe_failure != nil) {
        return;
    }
    NSMutableDictionary *document = [@{
        @"schema": @"gfx-uv2-probe/v1",
        @"side": @"metal",
        @"status": @"error",
        @"reason": reason ?: @"unknown",
        @"event": @(g_current_event),
        @"observed": observed ?: @{},
        @"target": event[@"target"] ?: @{},
    } mutableCopy];
    g_uv2_probe_failure = document;
    g_uv2_probe_status = 2;
    snprintf(g_uv2_probe_error, sizeof(g_uv2_probe_error), "%s",
             safe_utf8(reason ?: @"unknown", ""));
}

static BOOL capture_target_to_file(NSDictionary *target_info, NSString *path,
                                   NSError **error,
                                   NSInteger *command_status);

/* Binds the diagnostic descriptor to the authenticated sealed frame.  A frame
 * without a seal is recorded as unbound; the offline comparator then refuses
 * to relate the probe to captured evidence. */
static NSDictionary *uv2_probe_read_capture_identity(NSString *frame_path) {
    NSString *path = [frame_path stringByAppendingPathComponent:
                                      @"frame-seal.json"];
    NSData *data = [NSData dataWithContentsOfFile:path];
    if (data == nil) {
        return @{@"sealed": @NO, @"reason": @"frame-seal.json is absent"};
    }
    NSDictionary *seal = [NSJSONSerialization JSONObjectWithData:data
                                                        options:0
                                                          error:NULL];
    if (![seal isKindOfClass:[NSDictionary class]] ||
        number(seal[@"sealed"], 0) != 1) {
        return @{@"sealed": @NO, @"reason": @"frame seal is not valid"};
    }
    return @{
        @"sealed": @YES,
        @"session_id": seal[@"session_id"] ?: @"",
        @"capture_frame_id": seal[@"capture_frame_id"] ?: @0,
        @"manifest_sha256": seal[@"manifest_sha256"] ?: @"",
        @"reference_sha256": seal[@"reference_sha256"] ?: @"",
    };
}

/* Reads every recorded probe target back after the frame command buffer has
 * completed and writes the raw float payload plus its descriptor. */
static NSDictionary *uv2_probe_write_outputs(NSString *output_path) {
    if (!g_uv2_probe.enabled) {
        return nil;
    }
    NSString *directory = g_uv2_probe.dir[0] != 0
        ? [NSString stringWithUTF8String:g_uv2_probe.dir]
        : [output_path stringByAppendingPathComponent:@"uv2-probe"];
    if (g_uv2_probe_failure != nil) {
        uv2_probe_write_descriptor(directory, g_uv2_probe_failure);
        return @{
            @"status": @"error",
            @"reason": g_uv2_probe_failure[@"reason"] ?: @"",
            @"descriptor": [directory stringByAppendingPathComponent:
                                         @"uv2-probe.json"],
        };
    }
    if (g_uv2_probe_records.count == 0) {
        return @{@"status": @"skipped", @"reason": @"event_not_replayed"};
    }
    NSDictionary *record = g_uv2_probe_records.firstObject;
    id<MTLTexture> texture = record[@"texture"];
    if (texture == nil) {
        return @{@"status": @"error", @"reason": @"probe_target_missing"};
    }
    NSUInteger width = texture.width;
    NSUInteger height = texture.height;
    NSUInteger stride = width * 16;
    id<MTLBuffer> buffer = [g_device newBufferWithLength:stride * height
                                                 options:MTLResourceStorageModeShared];
    if (buffer == nil) {
        return @{@"status": @"error", @"reason": @"probe_buffer_alloc"};
    }
    NSError *error = nil;
    id<MTLCommandBuffer> blit = [g_queue commandBuffer];
    id<MTLBlitCommandEncoder> encoder = [blit blitCommandEncoder];
    [encoder copyFromTexture:texture
                 sourceSlice:0
                 sourceLevel:0
                sourceOrigin:MTLOriginMake(0, 0, 0)
                  sourceSize:MTLSizeMake(width, height, 1)
                    toBuffer:buffer
           destinationOffset:0
      destinationBytesPerRow:stride
    destinationBytesPerImage:stride * height];
    [encoder endEncoding];
    [blit commit];
    if (!wait_for_transfer(blit, @"uv2 probe readback", &error)) {
        NSString *reason = failure_text(error, @"uv2 probe readback failed");
        uv2_probe_record_failure(
            reason, @{@"target": record[@"target"] ?: @{}},
            record[@"observed"] ?: @{});
        uv2_probe_write_descriptor(directory, g_uv2_probe_failure);
        return @{@"status": @"error", @"reason": reason};
    }
    uint8_t *flipped = (uint8_t *)malloc(stride * height);
    if (flipped == NULL) {
        return @{@"status": @"error", @"reason": @"probe_flip_alloc"};
    }
    uv2_probe_flip_rows((const uint8_t *)buffer.contents, flipped, width,
                        height, 16);
    NSData *data = [NSData dataWithBytes:flipped length:stride * height];
    free(flipped);
    NSString *raw_name = @"uv2-probe.rgba32f";
    NSString *raw_path = [directory stringByAppendingPathComponent:raw_name];
    [[NSFileManager defaultManager] createDirectoryAtPath:directory
                              withIntermediateDirectories:YES
                                               attributes:nil
                                                    error:NULL];
    if (![data writeToFile:raw_path atomically:YES]) {
        return @{@"status": @"error", @"reason": @"probe_raw_write"};
    }
    /* The normal event target is read at the event boundary by the existing
       checkpoint path, so its contents are not the end-of-frame state. */
    NSString *color_name = @"uv2-color.bgra";
    NSDictionary *probe_target = record[@"target"] ?: @{};
    NSString *expected_target = [NSString stringWithFormat:@"%d",
                                 g_uv2_probe.texture];
    NSString *expected_resource = [NSString stringWithFormat:@"tex:%d",
                                   g_uv2_probe.texture];
    NSUInteger expected_width = number(probe_target[@"width"], 0);
    NSUInteger expected_height = number(probe_target[@"height"], 0);
    NSDictionary *checkpoint = nil;
    for (NSDictionary *item in g_checkpoint_results) {
        if ([item[@"event"] isEqual:record[@"event"]] &&
            [item[@"file"] isEqual:color_name] &&
            [item[@"target"] isEqual:expected_target] &&
            [item[@"resource"] isEqual:expected_resource] &&
            [item[@"timing"] isEqualToString:@"post_event"] &&
            number(item[@"ok"], 0) == 1 &&
            number(item[@"width"], 0) == expected_width &&
            number(item[@"height"], 0) == expected_height) {
            checkpoint = item;
            break;
        }
    }
    NSString *color_path = [output_path stringByAppendingPathComponent:
                                         color_name];
    NSData *color_data = checkpoint != nil
        ? [NSData dataWithContentsOfFile:color_path] : nil;
    if (color_data == nil) {
        NSString *reason = [NSString stringWithFormat:
            @"no post_event checkpoint matches event %@, target %@, "
             "resource %@, file %@ and the %lux%lu event target (results: %@)",
            record[@"event"] ?: @(-1), expected_target, expected_resource,
            color_name, (unsigned long)expected_width,
            (unsigned long)expected_height, g_checkpoint_results ?: @[]];
        uv2_probe_record_failure(
            reason, @{@"target": record[@"target"] ?: @{}},
            record[@"observed"] ?: @{});
        uv2_probe_write_descriptor(directory, g_uv2_probe_failure);
        return @{@"status": @"error", @"reason": reason};
    }
    NSMutableDictionary *document = [record mutableCopy];
    [document removeObjectForKey:@"texture"];
    document[@"status"] = @"ok";
    document[@"normal_target"] = @{
        @"file": color_name,
        @"path": color_path,
        @"pixel_format": @"BGRA8",
        @"origin": @"lower_left",
        @"width": checkpoint[@"width"] ?: @0,
        @"height": checkpoint[@"height"] ?: @0,
        @"sha256": uv2_probe_sha256(color_data),
    };
    document[@"origin"] = @"lower_left";
    document[@"pixel_format"] = @"RGBA32F";
    document[@"width"] = @(width);
    document[@"height"] = @(height);
    document[@"row_stride"] = @(stride);
    document[@"raw_file"] = raw_name;
    document[@"raw_bytes"] = @(data.length);
    document[@"raw_sha256"] = uv2_probe_sha256(data);
    uv2_probe_write_descriptor(directory, document);
    g_uv2_probe_status = 1;
    return @{
        @"status": @"ok",
        @"event": document[@"event"] ?: @0,
        @"width": @(width),
        @"height": @(height),
        @"pixel_format": @"RGBA32F",
        @"raw_file": raw_name,
        @"raw_sha256": document[@"raw_sha256"],
        @"descriptor": [directory stringByAppendingPathComponent:
                                     @"uv2-probe.json"],
    };
}

typedef struct {
    MTLViewport viewport;
    NSUInteger target_width;
    NSUInteger target_height;
    NSDictionary *scissor;
    NSDictionary *cull;
    MTLCullMode cull_mode;
    BOOL cull_empty;
    BOOL no_cull;
    NSUInteger primitive_mode;
} draw_binding;

/* Applies viewport, scissor and culling.  Shared by the normal draw and the
 * isolated uv2 diagnostic so the two paths cannot drift. */
static void apply_draw_binding(id<MTLRenderCommandEncoder> encoder,
                               const draw_binding *binding) {
    [encoder setViewport:binding->viewport];
    BOOL scissor_empty = NO;
    NSDictionary *scissor = binding->scissor;
    if (number(scissor[@"enabled"], 0) != 0) {
        MTLScissorRect rect;
        if (!scissor_rect_for(scissor[@"box"], binding->target_width,
                              binding->target_height, &rect)) {
            scissor_empty = YES;
        } else {
            [encoder setScissorRect:rect];
        }
    } else {
        MTLScissorRect rect = {0, 0, binding->target_width,
                               binding->target_height};
        [encoder setScissorRect:rect];
    }
    if (binding->cull_empty || scissor_empty) {
        MTLScissorRect empty = {0, 0, 0, 0};
        [encoder setScissorRect:empty];
    }
    if (binding->cull_empty || binding->no_cull ||
        number(binding->cull[@"enabled"], 0) == 0 ||
        !topology_supports_culling(binding->primitive_mode)) {
        [encoder setCullMode:MTLCullModeNone];
        return;
    }
    [encoder setCullMode:binding->cull_mode];
    NSUInteger gl_front = (NSUInteger)strtoul(
        [string_value(binding->cull[@"front"]) ?: @"0x901" UTF8String],
        NULL, 0);
    const char *flip = getenv("EU4_FRAME_REPLAY_FLIP_WINDING");
    if (flip != NULL && flip[0] == '1') {
        gl_front = gl_front == 0x901 ? 0x900 : 0x901;
    }
    [encoder setFrontFacingWinding:gl_front == 0x901
               ? MTLWindingCounterClockwise
               : MTLWindingClockwise];
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
        NSString *vertex_identity = nil;
        NSString *fragment_identity = nil;
        id<MTLFunction> vertex_function =
            function_for(shaderDir, vs, &vertex_identity, error);
        id<MTLFunction> fragment_function =
            function_for(shaderDir, fs, &fragment_identity, error);
        NSString *debug_options = shader_compile_options_key();
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
                vertex_identity = [@"builtin:debug_attr_vertex#"
                    stringByAppendingString:debug_options];
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
                vertex_identity = [@"builtin:debug_vertex#"
                    stringByAppendingString:debug_options];
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
                fragment_identity = [@"builtin:debug_texture_fragment#"
                    stringByAppendingString:debug_options];
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
                fragment_identity = [@"builtin:debug_uv_fragment#"
                    stringByAppendingString:debug_options];
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
                fragment_identity = [@"builtin:debug_fragment#"
                    stringByAppendingString:debug_options];
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
        NSMutableDictionary *target_info = target_for(event[@"target"] ?: @{}, error);
        if (target_info == nil) {
            return NO;
        }
        target_info[@"generation"] = @(number(target_info[@"generation"], 0) + 1);
        if (depth_test && depth_write && target_info[@"depth"] != nil) {
            target_info[@"depth_generation"] =
                @(number(target_info[@"depth_generation"], 0) + 1);
        }
        NSString *draw_target_name =
            target_resource_name(event[@"target"] ?: @{});
        if (draw_target_name.length != 0) {
            target_info[@"color_name"] = draw_target_name;
            [g_frame_written addObject:draw_target_name];
            g_render_targets[draw_target_name] = target_info;
            history_note_target(draw_target_name, target_info);
        }
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
            program[@"name"], vertex_identity ?: @"", fragment_identity ?: @""];
        if (getenv("EU4_FRAME_REPLAY_DEBUG_DESCRIPTOR") != NULL) {
            fprintf(stderr, "debug descriptor program %s:\n",
                    safe_utf8([program[@"name"] description], ""));
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
        MTLViewport metal_viewport = viewport_for(
            state[@"viewport"], target_width, target_height);
        NSDictionary *scissor = state[@"scissor"] ?: @{};
        NSDictionary *draw_event = event[@"draw"] ?: @{};
        NSUInteger primitive_mode = number(draw_event[@"mode"], 0x0004);
        const char *no_cull_env = getenv("EU4_FRAME_REPLAY_NO_CULL");
        BOOL no_cull = no_cull_env != NULL && no_cull_env[0] == '1';
        BOOL cull_empty = NO;
        MTLCullMode cull_mode = MTLCullModeNone;
        if (!effective_cull(cull, primitive_mode, no_cull, &cull_mode,
                            &cull_empty)) {
            if (error != NULL) {
                *error = [NSError errorWithDomain:kErrorDomain code:67
                                         userInfo:@{NSLocalizedDescriptionKey:
                                                        [NSString stringWithFormat:
                                                             @"unsupported cull mode %@",
                                                             cull[@"mode"] ?: @"<missing>"]}];
            }
            [encoder endEncoding];
            return NO;
        }
        draw_binding binding = {metal_viewport, target_width, target_height,
                                scissor, cull, cull_mode, cull_empty, no_cull,
                                primitive_mode};
        apply_draw_binding(encoder, &binding);
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
        /* A controlled later-frame divergence is scoped to one frame and,
           optionally, one captured event.  A missing or zero selector leaves
           the fault active for every frame/event, preserving the historical
           single-frame fault behaviour. */
        const char *fault_frame_env = getenv("EU4_FRAME_REPLAY_FAULT_FRAME");
        if (fault_frame_env != NULL) {
            NSInteger fault_frame = (NSInteger)strtol(fault_frame_env, NULL, 10);
            if (fault_frame > 0 && g_frame_sequence != (uint64_t)fault_frame) {
                sampler_shift = 0;
            }
        }
        const char *fault_event_env = getenv("EU4_FRAME_REPLAY_FAULT_EVENT");
        if (fault_event_env != NULL && sampler_shift != 0) {
            NSInteger fault_event = (NSInteger)strtol(fault_event_env, NULL, 10);
            NSInteger event_index = (NSInteger)number(event[@"index"], 0);
            if (fault_event >= 0 && event_index != fault_event) {
                sampler_shift = 0;
            }
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
                NSMutableDictionary *render_target = nil;
                NSMutableDictionary *persistent_target =
                    g_render_targets[cache_name];
                NSDictionary *history = g_resource_history[cache_name];
                BOOL carried = NO;
                /* Only a write executed earlier in this frame counts as a
                   producer.  Merely being declared as a colour attachment is
                   not a producer: sampling it before its draw/clear must use
                   the carried previous-frame output or fail closed. */
                BOOL current_target =
                    [g_frame_written containsObject:cache_name];
                if (persistent_target != nil &&
                    (!g_persistent_history || current_target)) {
                    render_target = persistent_target;
                } else if (persistent_target != nil &&
                           g_persistent_history &&
                           [g_current_carried containsObject:cache_name]) {
                    if (![history[@"kind"] isEqualToString:
                            @"renderer_generated"]) {
                        g_history_status = 2;
                        g_missing_history++;
                        snprintf(g_history_error, sizeof(g_history_error),
                                 "%s is carried but was not generated by Metal",
                                 safe_utf8(cache_name, "?"));
                        [encoder endEncoding];
                        return NO;
                    }
                    NSArray *levels_for_first = texture[@"levels"];
                    NSDictionary *first =
                        [levels_for_first isKindOfClass:[NSArray class]]
                            ? levels_for_first.firstObject : nil;
                    NSUInteger width = number(texture[@"width"],
                                              number(first[@"width"], 0));
                    NSUInteger height = number(texture[@"height"],
                                               number(first[@"height"], 0));
                    NSString *captured_format =
                        string_value(texture[@"internal_format"]) ?: @"";
                    NSString *history_format =
                        string_value(history[@"format"]) ?: @"";
                    BOOL rgb_captured =
                        [captured_format isEqualToString:@"RGB8"] ||
                        [captured_format isEqualToString:@"SRGB8_ALPHA8"];
                    BOOL rgb_history =
                        [history_format isEqualToString:@"RGB8"] ||
                        [history_format isEqualToString:@"SRGB8_ALPHA8"];
                    if ((width != number(history[@"width"], 0)) ||
                        (height != number(history[@"height"], 0)) ||
                        (rgb_captured != rgb_history)) {
                        g_history_status = 2;
                        g_stale_history++;
                        snprintf(g_history_error, sizeof(g_history_error),
                                 "%s carried history is stale (size/format)",
                                 safe_utf8(cache_name, "?"));
                        [encoder endEncoding];
                        return NO;
                    }
                    render_target = persistent_target;
                    carried = YES;
                    g_carried_bindings++;
                } else if (persistent_target != nil &&
                           g_persistent_history &&
                           ![g_current_carried containsObject:cache_name] &&
                           history != nil &&
                           [history[@"kind"] isEqualToString:
                               @"renderer_generated"]) {
                    g_history_status = 2;
                    g_missing_history++;
                    snprintf(g_history_error, sizeof(g_history_error),
                             "%s has renderer history but is not carried by this frame",
                             safe_utf8(cache_name, "?"));
                    [encoder endEncoding];
                    return NO;
                } else if (persistent_target == nil &&
                           g_persistent_history &&
                           ![g_current_carried containsObject:cache_name] &&
                           history != nil &&
                           [history[@"kind"] isEqualToString:
                               @"renderer_generated"]) {
                    g_history_status = 2;
                    g_missing_history++;
                    snprintf(g_history_error, sizeof(g_history_error),
                             "%s has renderer history but no producer or carry record",
                             safe_utf8(cache_name, "?"));
                    [encoder endEncoding];
                    return NO;
                } else if (persistent_target == nil &&
                           g_persistent_history &&
                           [g_current_carried containsObject:cache_name]) {
                    snprintf(g_history_error, sizeof(g_history_error),
                             "%s is carried but no Metal producer exists",
                             safe_utf8(cache_name, "?"));
                    g_history_status = 2;
                    g_missing_history++;
                    [encoder endEncoding];
                    return NO;
                }
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
                    history_note_external(cache_name, texture);
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
                sample[@"carried"] = @(carried);
                if (carried) {
                    sample[@"history_frame"] = history[@"last_frame"] ?: @0;
                    sample[@"history_generation"] =
                        history[@"generation"] ?: @0;
                }
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
                NSString *sampler_key =
                    sampler_key_for_state(sampler_state);
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
                    if (enum_value(sampler_state[@"compare_mode"], 0) == 0x884E) {
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
        NSUInteger mode = primitive_mode;
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
        id<MTLBuffer> uv2_index_buffer = nil;
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
            uv2_index_buffer = [g_device newBufferWithBytes:rebased.bytes
                                                      length:rebased.length
                                                     options:MTLResourceStorageModeManaged];
            [encoder drawIndexedPrimitives:primitive
                               indexCount:count
                                indexType:MTLIndexTypeUInt16
                              indexBuffer:uv2_index_buffer
                        indexBufferOffset:0
                            instanceCount:instances];
        } else {
            [encoder drawPrimitives:primitive
                        vertexStart:0
                        vertexCount:count
                      instanceCount:instances];
        }
        [encoder endEncoding];
        /* Opt-in uv2 diagnostic: a second, isolated pass in the same command
           buffer, using the normal pass's compiled vertex function and
           inputs.  It never writes the normal render target. */
        if (g_uv2_probe.enabled && !g_uv2_probe_done &&
            (NSInteger)number(event[@"index"], -1) == g_uv2_probe.event) {
            NSDictionary *probe_target = event[@"target"] ?: @{};
            NSInteger observed_program =
                (NSInteger)number(program[@"name"], -1);
            NSInteger observed_framebuffer =
                (NSInteger)number(probe_target[@"framebuffer"], -1);
            NSInteger observed_texture = (NSInteger)number(
                probe_target[@"color_attachment"][@"name"], -1);
            NSDictionary *observed = @{
                @"event": event[@"index"] ?: @(-1),
                @"program": @(observed_program),
                @"framebuffer": @(observed_framebuffer),
                @"texture": @(observed_texture),
            };
            if (observed_program != g_uv2_probe.program ||
                observed_framebuffer != g_uv2_probe.framebuffer ||
                observed_texture != g_uv2_probe.texture) {
                uv2_probe_record_failure(@"identity_mismatch", event,
                                         observed);
            } else if (!indexed) {
                uv2_probe_record_failure(
                    @"selection_unsupported", event,
                    @{@"reason": @"only indexed draws are supported"});
            } else {
                NSInteger varying_location = -1;
                NSError *probe_error = nil;
                id<MTLFunction> probe_fragment =
                    uv2_probe_compile_fragment(program_entry, vs,
                                               &varying_location,
                                               &probe_error);
                if (probe_fragment == nil) {
                    uv2_probe_record_failure(
                        failure_text(probe_error,
                                     @"uv2 probe fragment unavailable"),
                        event, observed);
                } else {
                    MTLTextureDescriptor *probe_descriptor =
                        [MTLTextureDescriptor new];
                    probe_descriptor.textureType = MTLTextureType2D;
                    probe_descriptor.pixelFormat = MTLPixelFormatRGBA32Float;
                    probe_descriptor.width = target_width;
                    probe_descriptor.height = target_height;
                    probe_descriptor.usage = MTLTextureUsageRenderTarget |
                                             MTLTextureUsageShaderRead;
                    probe_descriptor.storageMode = MTLStorageModePrivate;
                    id<MTLTexture> probe_texture =
                        [g_device newTextureWithDescriptor:probe_descriptor];
                    if (probe_texture == nil) {
                        uv2_probe_record_failure(@"probe_texture_alloc",
                                                 event, observed);
                    } else {
                        NSString *probe_key = [NSString stringWithFormat:
                            @"uv2probe|%@|%ld|%lu-%lu",
                            vertex_identity ?: @"",
                            (long)varying_location,
                            (unsigned long)target_width,
                            (unsigned long)target_height];
                        id<MTLRenderPipelineState> probe_pipeline =
                            g_pipelines[probe_key];
                        if (probe_pipeline == nil) {
                            MTLRenderPipelineDescriptor *pipeline_descriptor =
                                [MTLRenderPipelineDescriptor new];
                            pipeline_descriptor.vertexFunction =
                                vertex_function;
                            pipeline_descriptor.fragmentFunction =
                                probe_fragment;
                            pipeline_descriptor.vertexDescriptor =
                                vertex_descriptor;
                            pipeline_descriptor.colorAttachments[0]
                                .pixelFormat = MTLPixelFormatRGBA32Float;
                            NSError *pipeline_error = nil;
                            probe_pipeline = [g_device
                                newRenderPipelineStateWithDescriptor:
                                    pipeline_descriptor
                                                          error:&pipeline_error];
                            if (probe_pipeline == nil) {
                                uv2_probe_record_failure(
                                    failure_text(pipeline_error,
                                                 @"uv2 probe pipeline failed"),
                                    event, observed);
                            } else {
                                g_pipelines[probe_key] = probe_pipeline;
                            }
                        }
                        if (probe_pipeline != nil) {
                            MTLRenderPassDescriptor *pass =
                                [MTLRenderPassDescriptor renderPassDescriptor];
                            pass.colorAttachments[0].texture = probe_texture;
                            pass.colorAttachments[0].loadAction =
                                MTLLoadActionClear;
                            pass.colorAttachments[0].clearColor =
                                MTLClearColorMake(0.0, 0.0, 0.0, 0.0);
                            pass.colorAttachments[0].storeAction =
                                MTLStoreActionStore;
                            id<MTLRenderCommandEncoder> probe_encoder =
                                [command renderCommandEncoderWithDescriptor:
                                             pass];
                            if (probe_encoder == nil) {
                                uv2_probe_record_failure(
                                    @"probe_encoder", event, observed);
                            } else {
                                [probe_encoder setRenderPipelineState:
                                                    probe_pipeline];
                                draw_binding probe_binding = {
                                    metal_viewport, target_width, target_height,
                                    scissor, cull, cull_mode, cull_empty,
                                    no_cull, primitive_mode};
                                apply_draw_binding(probe_encoder,
                                                   &probe_binding);
                                if (number(vs[@"msl_uniform_buffer"],
                                           NSUIntegerMax) != NSUIntegerMax &&
                                    vertex_uniforms.length > 0) {
                                    [probe_encoder
                                        setVertexBytes:vertex_uniforms.bytes
                                                length:vertex_uniforms.length
                                               atIndex:number(
                                                           vs[@"msl_uniform_buffer"],
                                                           0)];
                                }
                                for (NSNumber *location in buffers) {
                                    [probe_encoder
                                        setVertexBuffer:buffers[location]
                                                 offset:0
                                                atIndex:kVertexBufferBase +
                                                        [location
                                                            unsignedIntegerValue]];
                                }
                                [probe_encoder
                                    drawIndexedPrimitives:primitive
                                               indexCount:count
                                                indexType:MTLIndexTypeUInt16
                                              indexBuffer:uv2_index_buffer
                                        indexBufferOffset:0
                                            instanceCount:instances];
                                [probe_encoder endEncoding];
                                NSDictionary *record = @{
                                    @"schema": @"gfx-uv2-probe/v1",
                                    @"side": @"metal",
                                    @"status": @"ok",
                                    @"event": event[@"index"] ?: @(-1),
                                    @"program": program[@"name"] ?: @(-1),
                                    @"target": probe_target,
                                    @"sources": @{
                                        @"vertex_sha256":
                                            vs[@"source_sha256"] ?: @"",
                                        @"fragment_sha256":
                                            fs[@"source_sha256"] ?: @"",
                                    },
                                    @"draw": draw_event,
                                    @"capture":
                                        g_uv2_probe_capture_identity
                                            ?: @{@"sealed": @NO},
                                    @"attributes": event[@"attributes"] ?: @[],
                                    @"uniforms": program[@"uniforms"] ?: @[],
                                    @"indices": event[@"indices"] ?: @{},
                                    @"texture": probe_texture,
                                };
                                [g_uv2_probe_records addObject:record];
                                g_uv2_probe_done = 1;
                            }
                        }
                    }
                }
            }
        }
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
    if (destination.length != 0) {
        [g_frame_written addObject:destination];
        history_note_target(destination, destination_info);
    }
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
        NSString *clear_target_name =
            target_resource_name(event[@"target"] ?: @{});
        if (clear_target_name.length != 0) {
            target_info[@"color_name"] = clear_target_name;
            [g_frame_written addObject:clear_target_name];
            g_render_targets[clear_target_name] = target_info;
            history_note_target(clear_target_name, target_info);
        }
    }
    if ((clear_depth || clear_stencil) && target_info[@"depth"] != nil) {
        target_info[@"depth_generation"] =
            @(number(target_info[@"depth_generation"], 0) + 1);
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
            if (error != NULL) {
                *error = [NSError errorWithDomain:kErrorDomain code:80
                                         userInfo:@{NSLocalizedDescriptionKey:
                                                        @"frame seed staging allocation failed"}];
            }
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
        if (error != NULL) {
            *error = [NSError errorWithDomain:kErrorDomain code:80
                                     userInfo:@{NSLocalizedDescriptionKey:
                                                    @"frame seed conversion allocation failed"}];
        }
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
        if (error != NULL) {
            *error = [NSError errorWithDomain:kErrorDomain code:100
                                     userInfo:@{NSLocalizedDescriptionKey:
                                                    @"pre-image conversion allocation failed"}];
        }
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
        if (error != NULL) {
            *error = [NSError errorWithDomain:kErrorDomain code:100
                                     userInfo:@{NSLocalizedDescriptionKey:
                                                    @"pre-image staging allocation failed"}];
        }
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
static BOOL capture_target_to_file(NSDictionary *target_info, NSString *path,
                                   NSError **error,
                                   NSInteger *command_status) {
    if (command_status != NULL) {
        *command_status = 0;
    }
    id<MTLTexture> color = target_info[@"color"];
    if (color == nil) {
        if (error != NULL) {
            *error = [NSError errorWithDomain:kErrorDomain code:101
                                     userInfo:@{NSLocalizedDescriptionKey:
                                                    @"readback target has no color texture"}];
        }
        return NO;
    }
    NSUInteger width = color.width;
    NSUInteger height = color.height;
    if (width == 0 || height == 0 ||
        width > SIZE_MAX / 4 / MAX((NSUInteger)1, height)) {
        if (error != NULL) {
            *error = [NSError errorWithDomain:kErrorDomain code:101
                                     userInfo:@{NSLocalizedDescriptionKey:
                                                    @"readback target dimensions are invalid"}];
        }
        return NO;
    }
    NSUInteger bytes_per_row = width * 4;
    id<MTLBuffer> buffer = [g_device newBufferWithLength:bytes_per_row * height
                                                 options:MTLResourceStorageModeShared];
    if (buffer == nil) {
        if (error != NULL) {
            *error = [NSError errorWithDomain:kErrorDomain code:101
                                     userInfo:@{NSLocalizedDescriptionKey:
                                                    @"readback buffer allocation failed"}];
        }
        return NO;
    }
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
    BOOL completed = wait_for_transfer(blit, @"checkpoint readback", error);
    if (command_status != NULL) {
        *command_status = (NSInteger)blit.status;
    }
    if (!completed) {
        return NO;
    }
    uint8_t *flipped = (uint8_t *)malloc(bytes_per_row * height);
    if (flipped == NULL) {
        if (error != NULL) {
            *error = [NSError errorWithDomain:kErrorDomain code:101
                                     userInfo:@{NSLocalizedDescriptionKey:
                                                    @"readback conversion allocation failed"}];
        }
        return NO;
    }
    flip_rows((const uint8_t *)buffer.contents, flipped, width, height, 4);
    NSData *data = [NSData dataWithBytes:flipped length:bytes_per_row * height];
    free(flipped);
    if (![data writeToFile:path atomically:YES]) {
        if (error != NULL) {
            *error = [NSError errorWithDomain:kErrorDomain code:102
                                     userInfo:@{NSLocalizedDescriptionKey:
                                                    [NSString stringWithFormat:
                                                         @"could not write readback %@", path]}];
        }
        return NO;
    }
    return YES;
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
    if (file_value == nil && [dictionary[@"name"] isKindOfClass:[NSString class]]) {
        /* Attribute payloads use "data"; arbitrary nested data is not a blob. */
        file_value = dictionary[@"data"];
    }
    if (file_value != nil) {
        if (![file_value isKindOfClass:[NSString class]]) {
            if (error != NULL) {
                *error = [NSError errorWithDomain:kErrorDomain code:113
                                         userInfo:@{NSLocalizedDescriptionKey:
                                                        @"blob file reference is not a string"}];
            }
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
                if (error != NULL) {
                    *error = [NSError errorWithDomain:kErrorDomain code:113
                                             userInfo:@{NSLocalizedDescriptionKey:
                                                            [NSString stringWithFormat:
                                                                 @"blob %@ has an invalid size", name]}];
                }
                return NO;
            }
            expected_size = (uint64_t)[bytes_value unsignedLongLongValue];
        }
        NSString *path = [[frame_dir stringByAppendingPathComponent:@"blobs"]
            stringByAppendingPathComponent:name];
        NSDictionary *prior = seen[name];
        if (prior != nil) {
            if (![prior[@"sha256"] isEqualToString:sha]) {
                if (error != NULL) {
                    *error = [NSError errorWithDomain:kErrorDomain code:115
                                             userInfo:@{NSLocalizedDescriptionKey:
                                                            [NSString stringWithFormat:
                                                                 @"conflicting repeated blob reference %@", name]}];
                }
                return NO;
            }
            /* A reference that omits "bytes" states no size.  It is unknown,
               not zero: two identical content-addressed references may describe
               the same payload with different metadata completeness.  A
               declared size that disagrees with another declared size is still
               a conflict, and a size declared after an omitted one is verified
               against the file. */
            id prior_bytes = prior[@"bytes"];
            if (bytes_value != nil && prior_bytes != nil &&
                [prior_bytes unsignedLongLongValue] != expected_size) {
                if (error != NULL) {
                    *error = [NSError errorWithDomain:kErrorDomain code:115
                                             userInfo:@{NSLocalizedDescriptionKey:
                                                            [NSString stringWithFormat:
                                                                 @"conflicting repeated blob reference %@", name]}];
                }
                return NO;
            }
            if (bytes_value != nil && prior_bytes == nil) {
                if (!verify_file_sha(path, expected_size, sha.UTF8String,
                                     error)) {
                    return NO;
                }
                seen[name] = @{@"sha256": sha, @"bytes": @(expected_size)};
            }
        } else {
            if (!verify_file_sha(path, expected_size, sha.UTF8String, error)) {
                return NO;
            }
            NSMutableDictionary *entry =
                [NSMutableDictionary dictionaryWithObject:sha forKey:@"sha256"];
            if (bytes_value != nil) {
                entry[@"bytes"] = @(expected_size);
            }
            seen[name] = entry;
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
        if (error != NULL) {
            *error = [NSError errorWithDomain:kErrorDomain code:116
                                     userInfo:@{NSLocalizedDescriptionKey:
                                                    [@"missing event stream " stringByAppendingString:events_path]}];
        }
        return NO;
    }
    NSString *text = [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding];
    if (text == nil) {
        if (error != NULL) {
            *error = [NSError errorWithDomain:kErrorDomain code:116
                                     userInfo:@{NSLocalizedDescriptionKey:
                                                    [@"event stream is not valid UTF-8: "
                                                        stringByAppendingString:events_path]}];
        }
        return NO;
    }
    NSMutableDictionary<NSString *, NSDictionary *> *seen =
        [NSMutableDictionary dictionary];
    for (NSString *line in [text componentsSeparatedByString:@"\n"]) {
        if (line.length == 0) {
            continue;
        }
        NSData *line_data = [line dataUsingEncoding:NSUTF8StringEncoding];
        NSError *line_error = nil;
        id object = [NSJSONSerialization JSONObjectWithData:line_data
                                                    options:0
                                                      error:&line_error];
        if (object == nil) {
            if (error != NULL) {
                *error = line_error ?: [NSError errorWithDomain:kErrorDomain
                                                           code:116
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

/* ------------------------------------------------------------------------- */
/* Malformed-input validation                                                 */
/* ------------------------------------------------------------------------- */

/* Valid JSON is not necessarily a usable frame.  Type confusion in a nested
   field currently reaches Objective-C subscripts and raises; rejecting it as
   input_validation keeps the failure structured and attributable. */
static BOOL validation_error(NSInteger operation, NSString *message,
                             NSError **error) {
    if (error != NULL) {
        *error = [NSError errorWithDomain:kErrorDomain code:120
                                 userInfo:@{NSLocalizedDescriptionKey:
                                                message.length > 0
                                                    ? message
                                                    : @"invalid replay input"}];
    }
    (void)operation;
    return NO;
}

static BOOL validate_dictionary_field(NSDictionary *object, NSString *field,
                                      NSInteger operation, NSError **error) {
    id value = object[field];
    if (value == nil || [value isKindOfClass:[NSDictionary class]]) {
        return YES;
    }
    return validation_error(operation,
                            [NSString stringWithFormat:
                                 @"event %ld field %@ is not a JSON object",
                                 (long)operation, field],
                            error);
}

static BOOL validate_array_field(NSDictionary *object, NSString *field,
                                 NSInteger operation, NSError **error) {
    id value = object[field];
    if (value == nil || [value isKindOfClass:[NSArray class]]) {
        return YES;
    }
    return validation_error(operation,
                            [NSString stringWithFormat:
                                 @"event %ld field %@ is not a JSON array",
                                 (long)operation, field],
                            error);
}

static BOOL validate_dictionary_array(NSDictionary *object, NSString *field,
                                      NSInteger operation, NSError **error) {
    id value = object[field];
    if (value == nil) {
        return YES;
    }
    if (![value isKindOfClass:[NSArray class]]) {
        return validation_error(operation,
                                [NSString stringWithFormat:
                                     @"event %ld field %@ is not a JSON array",
                                     (long)operation, field],
                                error);
    }
    for (id item in (NSArray *)value) {
        if (![item isKindOfClass:[NSDictionary class]]) {
            return validation_error(operation,
                                    [NSString stringWithFormat:
                                         @"event %ld field %@ contains a non-object",
                                         (long)operation, field],
                                    error);
        }
    }
    return YES;
}

static BOOL validate_event_shape(NSDictionary *event, NSInteger index,
                                 NSError **error) {
    NSString *kind = string_value(event[@"kind"]);
    if (kind.length == 0) {
        return validation_error(index, @"frame event has no kind", error);
    }
    if (![kind isEqualToString:@"draw"] && ![kind isEqualToString:@"clear"] &&
        ![kind isEqualToString:@"resolve"] && ![kind isEqualToString:@"copy"]) {
        return validation_error(index,
                                [NSString stringWithFormat:
                                     @"unsupported frame event kind %@", kind],
                                error);
    }
    for (NSString *field in @[@"target", @"state", @"program", @"draw",
                              @"seed", @"pre_image", @"post_image",
                              @"indices"]) {
        if (!validate_dictionary_field(event, field, index, error)) {
            return NO;
        }
    }
    for (NSString *field in @[@"attributes", @"textures", @"source_rect",
                              @"destination_offset"]) {
        if (!validate_array_field(event, field, index, error)) {
            return NO;
        }
    }
    if (!validate_dictionary_array(event, @"attributes", index, error) ||
        !validate_dictionary_array(event, @"textures", index, error)) {
        return NO;
    }
    NSDictionary *state = event[@"state"];
    if ([state isKindOfClass:[NSDictionary class]]) {
        for (NSString *field in @[@"blend", @"depth", @"cull", @"stencil",
                                  @"polygon", @"scissor"]) {
            if (!validate_dictionary_field(state, field, index, error)) {
                return NO;
            }
        }
        for (NSString *field in @[@"color_mask", @"viewport"]) {
            if (!validate_array_field(state, field, index, error)) {
                return NO;
            }
        }
    }
    NSDictionary *target = event[@"target"];
    if ([target isKindOfClass:[NSDictionary class]]) {
        for (NSString *field in @[@"color_attachment", @"depth_attachment"]) {
            if (!validate_dictionary_field(target, field, index, error)) {
                return NO;
            }
        }
    }
    NSDictionary *program = event[@"program"];
    if ([program isKindOfClass:[NSDictionary class]]) {
        if (event[@"kind"] && [kind isEqualToString:@"draw"] &&
            ![program[@"name"] isKindOfClass:[NSNumber class]]) {
            return validation_error(index,
                                    @"draw event program has no numeric name",
                                    error);
        }
        if (!validate_dictionary_array(program, @"uniforms", index, error)) {
            return NO;
        }
    }
    for (id texture in (event[@"textures"] ?: @[])) {
        for (NSString *field in @[@"levels", @"faces"]) {
            if (!validate_dictionary_array(texture, field, index, error)) {
                return NO;
            }
        }
        if (!validate_dictionary_field(texture, @"sampler", index, error)) {
            return NO;
        }
    }
    for (id attribute in (event[@"attributes"] ?: @[])) {
        if (!validate_array_field(attribute, @"constant_value", index, error)) {
            return NO;
        }
    }
    return YES;
}

static BOOL validate_manifest(NSDictionary *manifest, NSError **error) {
    id frame = manifest[@"frame"];
    if (frame != nil && ![frame isKindOfClass:[NSDictionary class]]) {
        return validation_error(-1, @"manifest frame is not a JSON object",
                                error);
    }
    NSDictionary *frame_info = frame ?: @{};
    NSUInteger width = number(frame_info[@"width"], 0);
    NSUInteger height = number(frame_info[@"height"], 0);
    if (width == 0 || height == 0) {
        return validation_error(-1, @"manifest frame dimensions are missing",
                                error);
    }
    if (width > 65536 || height > 65536) {
        return validation_error(-1, @"manifest frame dimensions are invalid",
                                error);
    }
    id reference_sha = frame_info[@"reference_sha256"];
    if (reference_sha != nil &&
        ![reference_sha isKindOfClass:[NSString class]]) {
        return validation_error(-1,
                                @"manifest reference digest is not a string",
                                error);
    }
    return YES;
}

static BOOL validate_shader_report(NSDictionary *report, NSError **error) {
    id programs = report[@"programs"];
    if (programs != nil && ![programs isKindOfClass:[NSArray class]]) {
        return validation_error(-1, @"shaders.json programs is not an array",
                                error);
    }
    for (id entry in (NSArray *)programs) {
        if (![entry isKindOfClass:[NSDictionary class]]) {
            return validation_error(-1,
                                    @"shaders.json contains a non-object program",
                                    error);
        }
        if (!validate_dictionary_field(entry, @"stages", -1, error)) {
            return NO;
        }
        NSDictionary *stages = entry[@"stages"];
        if ([stages isKindOfClass:[NSDictionary class]]) {
            for (NSString *stage_name in @[@"vertex", @"pixel"]) {
                if (!validate_dictionary_field(stages, stage_name, -1, error)) {
                    return NO;
                }
                NSDictionary *stage = stages[stage_name];
                if (![stage isKindOfClass:[NSDictionary class]]) {
                    continue;
                }
                for (NSString *field in @[@"attributes", @"msl_samplers",
                                          @"uniforms"]) {
                    if (!validate_dictionary_array(stage, field, -1, error)) {
                        return NO;
                    }
                }
                if (!validate_dictionary_field(stage, @"attribute_locations",
                                               -1, error)) {
                    return NO;
                }
            }
        }
    }
    return YES;
}

static BOOL validate_checkpoints(NSDictionary *document, NSError **error) {
    id checkpoints = document[@"checkpoints"];
    if (checkpoints == nil) {
        return YES;
    }
    if (![checkpoints isKindOfClass:[NSArray class]]) {
        return validation_error(-1, @"checkpoint document is not an array",
                                error);
    }
    for (id item in (NSArray *)checkpoints) {
        if (![item isKindOfClass:[NSDictionary class]]) {
            return validation_error(-1,
                                    @"checkpoint document contains a non-object",
                                    error);
        }
        id event = item[@"event"];
        if (event != nil && ![event isKindOfClass:[NSNumber class]]) {
            return validation_error(-1,
                                    @"checkpoint event index is not a number",
                                    error);
        }
        for (NSString *field in @[@"target", @"file", @"resource",
                                  @"reference_file", @"timing"]) {
            id value = item[field];
            if (value != nil && ![value isKindOfClass:[NSString class]]) {
                return validation_error(-1,
                                        [NSString stringWithFormat:
                                             @"checkpoint %@ is not a string",
                                             field],
                                        error);
            }
        }
        id timing = item[@"timing"];
        if (timing != nil && ![timing isEqualToString:@"pre_event"] &&
            ![timing isEqualToString:@"post_event"]) {
            return validation_error(
                -1, @"checkpoint timing is not pre_event or post_event", error);
        }
    }
    return YES;
}

/* Reads every due checkpoint's target and records its exact provenance.
   The reference blob is never copied; only the Metal readback is written. */
static void run_checkpoint_readbacks(NSArray *items, NSUInteger index,
                                     NSString *output_path, BOOL post_event,
                                     NSMutableArray *failures, NSUInteger *failed,
                                     NSError **error) {
    for (NSDictionary *item in items) {
        NSString *target_name = item[@"target"];
        NSMutableDictionary *info = g_render_targets[target_name];
        NSMutableDictionary *checkpoint = [@{
            @"event": @(index),
            @"target": target_name,
            @"resource": item[@"resource"] ?: @"",
            @"timing": post_event ? @"post_event" : @"pre_event",
            @"file": item[@"file"],
            @"reference_file": item[@"reference_file"] ?: @"",
            @"reference_sha256": item[@"reference_sha256"] ?: @"",
            @"ok": @NO,
        } mutableCopy];
        if (info == nil) {
            checkpoint[@"stage"] = @"checkpoint_readback";
            checkpoint[@"error"] = @"target was not created";
            [g_checkpoint_results addObject:checkpoint];
            (*failed)++;
            [failures addObject:failure_entry(
                @"checkpoint_readback", (NSInteger)index, @"checkpoint",
                checkpoint[@"error"], NO, 0, @"")];
            continue;
        }
        checkpoint[@"width"] = info[@"width"] ?: @0;
        checkpoint[@"height"] = info[@"height"] ?: @0;
        checkpoint[@"format"] = info[@"format"] ?: @"";
        checkpoint[@"generation"] = info[@"generation"] ?: @0;
        id<MTLTexture> colour = info[@"color"];
        if (colour != nil) {
            switch (colour.pixelFormat) {
                case MTLPixelFormatBGRA8Unorm:
                    checkpoint[@"pixel_format"] = @"BGRA8";
                    break;
                case MTLPixelFormatRGBA8Unorm:
                    checkpoint[@"pixel_format"] = @"RGBA8";
                    break;
                case MTLPixelFormatRGBA32Float:
                    checkpoint[@"pixel_format"] = @"RGBA32F";
                    break;
                case MTLPixelFormatR32Float:
                    checkpoint[@"pixel_format"] = @"R32F";
                    break;
                default:
                    checkpoint[@"pixel_format"] = @"other";
                    break;
            }
        } else {
            checkpoint[@"pixel_format"] = @"other";
        }
        NSString *path = [output_path stringByAppendingPathComponent:
                              item[@"file"]];
        NSError *checkpoint_error = nil;
        NSInteger readback_status = 0;
        if (capture_target_to_file(info, path, &checkpoint_error,
                                   &readback_status)) {
            checkpoint[@"ok"] = @YES;
        } else {
            checkpoint[@"stage"] = @"checkpoint_readback";
            checkpoint[@"error"] =
                failure_text(checkpoint_error, @"checkpoint readback failed");
            (*failed)++;
            [failures addObject:failure_entry(
                @"checkpoint_readback", (NSInteger)index, @"checkpoint",
                checkpoint[@"error"], g_deadline_expired != 0, readback_status,
                @"")];
        }
        [g_checkpoint_results addObject:checkpoint];
    }
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
    uv2_probe_configure_from_env();
    g_uv2_probe_capture_identity =
        uv2_probe_read_capture_identity(frame_path);
    uint64_t started_ns = replay_now_ns();
    g_started_ns = started_ns;
    state.current_result_json = NO;
    state.current_provenance = NO;
    state.current_metal_frame = NO;
    /* Shared by the early-exit recorder and the event loop so a fatal stage
       after an encoded-event failure does not discard the earlier detail. */
    NSMutableArray *failures = [NSMutableArray array];
    NSUInteger encoded = 0;
    NSUInteger failed = 0;
    @autoreleasepool {
        /* The output directory is created first so every later failure can
           publish the same structured report the successful path uses. */
        [[NSFileManager defaultManager] createDirectoryAtPath:output_path
                                  withIntermediateDirectories:YES
                                                   attributes:nil
                                                        error:NULL];
        /* One early-exit path for failures outside the event loop.  It records
           stage, operation, underlying error, timeout classification, counters,
           duration and command status in both replay.json and the C ABI. */
        auto record_failure_status = [&](NSString *stage, NSInteger operation,
                                         NSString *kind, NSError *error,
                                         NSString *fallback, int code,
                                         NSInteger command_status) -> int {
            NSString *message = failure_text(error, fallback);
            BOOL timed_out = g_deadline_expired != 0;
            failed += 1;
            g_failed_count = failed;
            NSDictionary *entry = failure_entry(stage, operation, kind, message,
                                                timed_out, command_status, @"");
            [failures addObject:entry];
            /* The earliest meaningful failure remains the report's primary
               diagnostic; a later fatal wait or artifact failure is retained
               in the failures array instead of masking it. */
            NSDictionary *primary = failures.firstObject;
            NSString *full = failure_message(primary[@"stage"],
                                             [primary[@"operation"]
                                                 integerValue],
                                             primary[@"error"]);
            if (out_result != NULL) {
                out_result->ok = 0;
                out_result->readback_ok = 0;
                out_result->events = (uint32_t)g_events_count;
                out_result->encoded = (uint32_t)g_encoded_count;
                out_result->failed = (uint32_t)g_failed_count;
                out_result->command_status = (int32_t)command_status;
                out_result->timed_out = timed_out ? 1 : 0;
                out_result->partial =
                    (stop_after >= 0 || only_event >= 0) ? 1 : 0;
                out_result->history_status = g_history_status;
                out_result->carried_bindings =
                    (uint32_t)g_carried_bindings;
                out_result->missing_history =
                    (uint32_t)g_missing_history;
                out_result->stale_history = (uint32_t)g_stale_history;
                out_result->duration_ns = replay_now_ns() - started_ns;
                snprintf(out_result->error, sizeof(out_result->error), "%s",
                         safe_utf8(full, "unknown replay failure"));
            }
            NSMutableDictionary *report = [@{
                @"schema": @"gfx-metal-frame-replay/v1",
                @"status": timed_out ? @"timeout" : @"failed",
                @"events": @(g_events_count),
                @"encoded": @(g_encoded_count),
                @"failed": @(failed),
                @"failures": failures,
                @"failure": primary,
                @"error": full,
                @"stage": primary[@"stage"] ?: @"replay",
                @"timed_out": @(timed_out),
                @"command_status": @(command_status),
                @"partial": @((stop_after >= 0 || only_event >= 0)),
                @"duration_ns": @(replay_now_ns() - started_ns),
                @"readback": @NO,
                @"history": history_document(),
            } mutableCopy];
            NSData *json = [NSJSONSerialization dataWithJSONObject:report
                                                           options:
                                                               NSJSONWritingPrettyPrinted
                                                             error:NULL];
            BOOL written =
                json != nil &&
                [json writeToFile:[output_path stringByAppendingPathComponent:
                                            @"replay.json"]
                       atomically:YES];
            if (written) {
                g_state.current_result_json = YES;
            }
            if (out_result != NULL && written) {
                snprintf(out_result->result_json,
                         sizeof(out_result->result_json), "%s/replay.json",
                         safe_utf8(output_path, ""));
            }
            fprintf(stderr, "%s\n", safe_utf8(full, "replay failed"));
            return code;
        };
        auto record_failure = [&](NSString *stage, NSInteger operation,
                                  NSString *kind, NSError *error,
                                  NSString *fallback, int code,
                                  id<MTLCommandBuffer> command) -> int {
            return record_failure_status(
                stage, operation, kind, error, fallback, code,
                command != nil ? (NSInteger)command.status : 0);
        };
        @try {
        NSError *verify_error = nil;
        if (!verify_sealed_inputs(frame_path, shader_path, config, &verify_error)) {
            NSString *verify_detail = failure_text(
                verify_error, @"sealed input verification failed");
            NSString *message =
                [verify_detail hasPrefix:@"sealed input verification"]
                    ? verify_detail
                    : [@"sealed input verification failed: "
                          stringByAppendingString:verify_detail];
            return record_failure(@"sealed_input_verification", -1,
                                  @"frame", nil, message, 11, nil);
        }
        g_provenance = [NSMutableArray array];
        g_provenance_seen = [NSMutableDictionary dictionary];
        g_checkpoint_results = [NSMutableArray array];
        g_post_checkpoints_by_event = @{};
        g_transfer_records = [NSMutableArray array];
        g_checkpoints_by_event = nil;
        g_current_event = 0;
        g_frame_written = [NSMutableSet set];
        g_frame_targets = [NSMutableSet set];
        g_carried_bindings = 0;
        g_missing_history = 0;
        g_stale_history = 0;
        g_persistent_history = config->persistent_history != 0;
        g_frame_sequence = config->frame_sequence;
        g_history_status = g_persistent_history ? 1 : 0;
        g_current_carried = [NSSet set];
        g_pending_lifecycle = [NSMutableArray array];
        g_sequence_record = nil;
        memset(g_default_target_ptr, 0, sizeof(*g_default_target_ptr));

        NSError *error = nil;
        if (g_persistent_history || config->frame_sequence > 0) {
            if (!apply_frame_sequence(frame_path, config->frame_sequence,
                                      &error)) {
                g_history_status = 2;
                return record_failure(
                    @"history_sequence", -1, @"frame", error,
                    @"continuous frame sequence validation failed", 12, nil);
            }
        }
        NSDictionary *manifest = read_json(
            [frame_path stringByAppendingPathComponent:@"frame-trace.json"], &error);
        if (manifest == nil) {
            return record_failure(@"manifest_load", -1, @"frame", error,
                                  @"frame manifest load failed", 4, nil);
        }
        if (!validate_manifest(manifest, &error)) {
            return record_failure(@"input_validation", -1, @"frame", error,
                                  @"frame manifest validation failed", 4, nil);
        }
        NSArray *events = read_json_lines(
            [frame_path stringByAppendingPathComponent:@"frame-events.jsonl"], &error);
        if (events == nil) {
            return record_failure(@"event_stream_load", -1, @"frame", error,
                                  @"frame event stream load failed", 4, nil);
        }
        g_events_count = events.count;
        if (out_result != NULL) {
            out_result->events = (uint32_t)events.count;
        }
        for (NSUInteger index = 0; index < events.count; index++) {
            if (!validate_event_shape(events[index], (NSInteger)index, &error)) {
                return record_failure(@"input_validation", (NSInteger)index,
                                      @"frame", error,
                                      @"frame event validation failed", 4, nil);
            }
        }
        NSDictionary *shader_report = read_json(
            [shader_path stringByAppendingPathComponent:@"shaders.json"], &error);
        if (shader_report == nil) {
            return record_failure(@"shader_load", -1, @"frame", error,
                                  @"shader report load failed", 4, nil);
        }
        if (!validate_shader_report(shader_report, &error)) {
            return record_failure(@"shader_load", -1, @"frame", error,
                                  @"shader report validation failed", 4, nil);
        }
        if (checkpoint_path.length > 0) {
            NSDictionary *document = read_json(checkpoint_path, &error);
            if (document == nil) {
                return record_failure(@"checkpoint_load", -1, @"frame", error,
                                      @"checkpoint document load failed", 4,
                                      nil);
            }
            if (!validate_checkpoints(document, &error)) {
                return record_failure(@"checkpoint_load", -1, @"frame", error,
                                      @"checkpoint document validation failed",
                                      4, nil);
            }
            NSMutableDictionary *by_event = [NSMutableDictionary dictionary];
            NSMutableDictionary *post_by_event =
                [NSMutableDictionary dictionary];
            for (NSDictionary *item in document[@"checkpoints"] ?: @[]) {
                NSNumber *event_index = item[@"event"];
                NSString *target = item[@"target"];
                NSString *file = item[@"file"];
                if (event_index == nil || target.length == 0 || file.length == 0) {
                    continue;
                }
                BOOL post_event =
                    [item[@"timing"] isEqualToString:@"post_event"];
                NSMutableDictionary *store =
                    post_event ? post_by_event : by_event;
                NSMutableArray *bucket = store[event_index];
                if (bucket == nil) {
                    bucket = [NSMutableArray array];
                    store[event_index] = bucket;
                }
                [bucket addObject:item];
            }
            g_checkpoints_by_event = by_event;
            g_post_checkpoints_by_event = post_by_event;
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
        if (default_info == nil) {
            return record_failure(@"attachment_initialization", -1, @"frame",
                                  error, @"default target allocation failed", 7,
                                  nil);
        }
        /* target_for() may return the cached FB0 entry without repopulating
           the session default target (session reuse). */
        g_default_target.color = default_info[@"color"];
        g_default_target.depth = default_info[@"depth"];
        g_default_target.width = number(default_info[@"width"], 0);
        g_default_target.height = number(default_info[@"height"], 0);
        g_render_targets[@"0"] = default_info;
        [g_frame_targets addObject:@"0"];
        history_note_target(@"0", default_info);

        NSDictionary *first_draw = nil;
        NSUInteger first_draw_index = 0;
        for (NSUInteger index = 0; index < events.count; index++) {
            NSDictionary *event = events[index];
            if ([event[@"kind"] isEqualToString:@"draw"]) {
                first_draw = event;
                first_draw_index = index;
                break;
            }
        }
        if (first_draw == nil) {
            return record_failure(@"frame_structure", -1, @"frame", nil,
                                  @"frame has no draw events", 5, nil);
        }
        NSDictionary *seed = first_draw[@"seed"];
        if (seed != nil) {
            if (!upload_seed(frame_path, seed, first_draw[@"target"], &error)) {
                return record_failure(@"seed_upload",
                                      (NSInteger)first_draw_index, @"draw",
                                      error, @"frame seed upload failed", 6,
                                      nil);
            }
        }
        /* Lifecycle entries recorded before the frame's first event apply
           before targets are pre-registered, so a deleted session resource can
           be recreated with this frame's captured size/format instead of
           leaving a stale Metal identity behind. */
        if (!apply_due_lifecycle(-1)) {
            return record_failure(@"history_mutation", -1, @"frame", nil,
                                  @(g_history_error), 12, nil);
        }
        /* Register render-target textures under the GL names that sample them,
           so composition inside the frame uses the rendered contents. */
        for (NSUInteger index = 0; index < events.count; index++) {
            NSDictionary *event = events[index];
            NSDictionary *target = event[@"target"] ?: @{};
            NSDictionary *attachment = target[@"color_attachment"];
            if (![attachment isKindOfClass:[NSDictionary class]]) {
                continue;
            }
            NSMutableDictionary *info = target_for(target, &error);
            if (info == nil) {
                return record_failure(@"attachment_initialization",
                                      (NSInteger)index,
                                      string_value(event[@"kind"]) ?: @"frame",
                                      error,
                                      @"attachment allocation failed", 7, nil);
            }
            NSString *attachment_name = texture_key(attachment[@"name"]);
            if (attachment_name.length == 0) {
                continue;
            }
            if (g_render_targets[attachment_name] == nil) {
                info[@"color_name"] = attachment_name;
                g_render_targets[attachment_name] = info;
            }
            [g_frame_targets addObject:attachment_name];
        }
        /* Resolve/copy destinations are not framebuffer colour attachments in
           a draw event, so allocate their Metal targets from the captured
           destination metadata before execution. */
        for (NSUInteger index = 0; index < events.count; index++) {
            NSDictionary *event = events[index];
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
                &error);
            if (info == nil) {
                return record_failure(@"attachment_initialization",
                                      (NSInteger)index, kind, error,
                                      @"copy destination allocation failed", 7,
                                      nil);
            }
            info[@"color_name"] = destination;
            g_render_targets[destination] = info;
            [g_frame_targets addObject:destination];
            history_note_target(destination, info);
        }
        id<MTLCommandBuffer> command = [g_queue commandBuffer];
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
                return record_failure(
                    @"isolated_preparation", only_event, @"frame", nil,
                    [NSString stringWithFormat:
                         @"only-event %ld is outside the frame (%lu events)",
                         (long)only_event, (unsigned long)events.count],
                    7, nil);
            }
            isolated_event = events[(NSUInteger)only_event];
            isolated_target_info = target_for(isolated_event[@"target"] ?: @{}, &error);
            if (isolated_target_info == nil) {
                return record_failure(@"isolated_preparation", only_event,
                                      string_value(isolated_event[@"kind"])
                                          ?: @"frame",
                                      error,
                                      @"isolated target allocation failed", 7,
                                      nil);
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
            if (!apply_due_lifecycle((NSInteger)index)) {
                return record_failure(@"history_mutation", (NSInteger)index,
                                      @"frame", nil, @(g_history_error), 12,
                                      nil);
            }
            /* Execute one event per submission.  Render-target sampling may
               need a compute pass in its own command buffer; that pass must
               observe every prior event as completed or it reads stale
               contents.  Serializing also makes replay checkpoints exact. */
            if (encoded > 0) {
                [command commit];
                if (!wait_for_transfer(command, @"event boundary", &error)) {
                    return record_failure(@"event_boundary", (NSInteger)index,
                                          string_value(events[index][@"kind"])
                                              ?: @"frame",
                                          error,
                                          @"event boundary wait failed", 7,
                                          command);
                }
                command = [g_queue commandBuffer];
            }
            NSArray *due_checkpoints =
                isolated ? nil : g_checkpoints_by_event[@(index)];
            if (due_checkpoints.count > 0) {
                /* The checkpoint observes the state left by all prior events,
                   before this event executes. */
                command = [g_queue commandBuffer];
                run_checkpoint_readbacks(due_checkpoints, index, output_path,
                                         NO, failures, &failed, &error);
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
                        return record_failure(@"attachment_initialization",
                                              (NSInteger)index, @"draw", error,
                                              @"depth initialization failed", 7,
                                              command);
                    }
                }
                /* Drain every prior command before the upload: an uncommitted
                   clear encoded earlier would otherwise execute afterwards. */
                [command commit];
                if (!wait_for_transfer(command, @"attachment initialization",
                                       &error)) {
                    return record_failure(@"attachment_initialization",
                                          (NSInteger)index, @"draw", error,
                                          @"attachment wait failed", 7,
                                          command);
                }
                command = [g_queue commandBuffer];
                if (!upload_image_to_target(frame_path, event[@"pre_image"],
                                            isolated_target_info, &error)) {
                    return record_failure(@"pre_image_upload", (NSInteger)index,
                                          @"draw", error,
                                          @"pre-image upload failed", 7, nil);
                }
                if (capture_pre_images) {
                    NSString *name = [NSString stringWithFormat:
                                          @"isolated-pre-%lu.bgra",
                                          (unsigned long)index];
                    NSString *path =
                        [output_path stringByAppendingPathComponent:name];
                    NSError *pre_error = nil;
                    NSInteger pre_status = 0;
                    if (!capture_target_to_file(isolated_target_info, path,
                                                &pre_error, &pre_status)) {
                        return record_failure_status(
                            @"pre_image_readback", (NSInteger)index, @"draw",
                            pre_error, @"pre-image readback failed", 7,
                            pre_status);
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
            NSString *event_stage = @"event_encode";
            NSInteger event_command_status = (NSInteger)command.status;
            NSError *event_error = nil;
            BOOL ok = YES;
            if ([kind isEqualToString:@"clear"]) {
                ok = encode_clear(event, frame_path, command, &event_error);
            } else if ([kind isEqualToString:@"resolve"] ||
                       [kind isEqualToString:@"copy"]) {
                ok = encode_copy_event(event, frame_path, &event_error);
            } else if ([kind isEqualToString:@"draw"]) {
                NSDictionary *program_object = event[@"program"];
                NSNumber *program = [program_object isKindOfClass:
                                         [NSDictionary class]]
                                        ? program_object[@"name"] : nil;
                NSDictionary *entry = programs[program];
                if (entry == nil) {
                    ok = NO;
                    event_error = [NSError errorWithDomain:kErrorDomain code:90
                                                 userInfo:@{NSLocalizedDescriptionKey:
                                                                [NSString stringWithFormat:
                                                                     @"program %@ has no translated stages",
                                                                     program ?: @0]}];
                } else {
                    NSDictionary *target_info = target_for(event[@"target"] ?: @{},
                                                           &event_error);
                    if (ok) {
                        ok = encode_draw(event, entry, frame_path, shader_path,
                                         command, &event_error);
                        if (!ok && event_error == nil &&
                            g_history_error[0] != 0) {
                            event_error = [NSError errorWithDomain:kErrorDomain
                                code:95
                                userInfo:@{NSLocalizedDescriptionKey:
                                    @(g_history_error)}];
                            g_history_error[0] = 0;
                        }
                    }
                    if (ok && capture_post_images && event[@"post_image"] != nil &&
                        (!isolated || is_selected)) {
                        if (target_info == nil) {
                            event_stage = @"post_image_readback";
                            ok = NO;
                            if (event_error == nil) {
                                event_error = [NSError errorWithDomain:
                                                       kErrorDomain
                                                               code:101
                                                           userInfo:@{NSLocalizedDescriptionKey:
                                                                          @"post-image target is missing"}];
                            }
                        } else {
                        /* The draw was encoded into the frame command buffer; commit
                           and drain it so the isolated post-image includes this draw
                           and no later event. */
                        [command commit];
                        if (!wait_for_transfer(command, @"post-draw completion",
                                               &event_error)) {
                            event_stage = @"post_draw_completion";
                            event_command_status = (NSInteger)command.status;
                            ok = NO;
                        }
                        command = [g_queue commandBuffer];
                        if (ok) {
                            NSString *name = [NSString stringWithFormat:
                                                  @"isolated-post-%lu.bgra",
                                                  (unsigned long)index];
                            NSString *path =
                                [output_path stringByAppendingPathComponent:name];
                            NSError *post_error = nil;
                            NSInteger post_status = 0;
                            if (!capture_target_to_file(target_info, path,
                                                        &post_error,
                                                        &post_status)) {
                                ok = NO;
                                event_stage = @"post_image_readback";
                                event_command_status = post_status;
                                event_error = post_error ?:
                                    [NSError errorWithDomain:kErrorDomain
                                                        code:101
                                                    userInfo:@{NSLocalizedDescriptionKey:
                                                                   @"post readback failed"}];
                            }
                        }
                        }
                    }
                }
            }
            if (!ok) {
                failed++;
                NSString *message = failure_text(event_error,
                                                 @"event encoding failed");
                [failures addObject:failure_entry(
                    event_stage, (NSInteger)index, kind ?: @"unknown",
                    message, g_deadline_expired != 0, event_command_status,
                    string_value(event[@"draw"][@"caller_rva"]) ?: @"")];
            }
            NSArray *post_checkpoints =
                isolated ? nil : g_post_checkpoints_by_event[@(index)];
            if (ok && post_checkpoints.count > 0) {
                /* The exact-prefix checkpoint is the target state left by this
                   event and no later event.  Commit and drain first, then read
                   every target back. */
                [command commit];
                if (!wait_for_transfer(command, @"prefix checkpoint boundary",
                                       &error)) {
                    failed++;
                    [failures addObject:failure_entry(
                        @"checkpoint_boundary", (NSInteger)index,
                        kind ?: @"unknown",
                        failure_text(error, @"prefix checkpoint wait failed"),
                        g_deadline_expired != 0, command.status, @"")];
                } else {
                    command = [g_queue commandBuffer];
                    run_checkpoint_readbacks(post_checkpoints, index,
                                             output_path, YES, failures,
                                             &failed, &error);
                }
            }
            encoded++;
            g_encoded_count = encoded;
            g_failed_count = failed;
        }
        /* Drain lifecycle entries recorded at the end-of-frame event count.
           A delete or unreproduced CPU mutation after the last captured event
           must still invalidate the frame instead of being ignored forever. */
        if (!apply_due_lifecycle((NSInteger)events.count)) {
            return record_failure(@"history_mutation", (NSInteger)events.count,
                                  @"frame", nil, @(g_history_error), 12,
                                  command);
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
            [failures addObject:failure_entry(
                @"frame_completion", (NSInteger)events.count, @"frame",
                failure_text(error, @"frame did not complete"),
                g_deadline_expired != 0, command.status, @"")];
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
            BOOL valid_dimensions =
                width > 0 && height > 0 &&
                width <= SIZE_MAX / 4 / MAX((NSUInteger)1, height);
            id<MTLBuffer> buffer = valid_dimensions
                ? [g_device newBufferWithLength:bytes_per_row * height
                                        options:MTLResourceStorageModeShared]
                : nil;
            if (buffer == nil || g_default_target.color == nil) {
                failed++;
                [failures addObject:failure_entry(
                    @"frame_readback", (NSInteger)events.count, @"readback",
                    buffer == nil ? @"readback buffer allocation failed"
                                  : @"readback target is missing",
                    NO, 0, @"")];
            } else {
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
                    [failures addObject:failure_entry(
                        @"frame_readback", (NSInteger)events.count,
                        @"readback",
                        failure_text(error, @"readback did not complete"),
                        g_deadline_expired != 0, blit.status, @"")];
                }
                uint8_t *readback = readback_completed
                                        ? (uint8_t *)malloc(bytes_per_row * height)
                                        : NULL;
                if (readback_completed && readback == NULL) {
                    failed++;
                    [failures addObject:failure_entry(
                        @"frame_readback", (NSInteger)events.count,
                        @"readback",
                        @"readback conversion allocation failed",
                        g_deadline_expired != 0, blit.status, @"")];
                }
                if (readback != NULL) {
                    flip_rows((const uint8_t *)buffer.contents, readback,
                              width, height, 4);
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
                    if (!readback_ok) {
                        failed++;
                        [failures addObject:failure_entry(
                            @"frame_readback", (NSInteger)events.count,
                            @"artifact",
                            @"metal-frame.bgra write failed",
                            g_deadline_expired != 0, blit.status, @"")];
                    }
                }
            }
        }
        if (g_uv2_probe.enabled) {
            NSDictionary *probe_summary =
                uv2_probe_write_outputs(output_path);
            if (probe_summary != nil) {
                result[@"uv2_probe"] = probe_summary;
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
        result[@"history"] = history_document();
        result[@"targets"] = target_registry_document();
        result[@"readback"] = @(readback_ok);
        BOOL partial_mode = stop_after >= 0 || only_event >= 0;
        if (!provenance_written) {
            failed++;
            [failures addObject:failure_entry(
                @"artifact_write", (NSInteger)events.count, @"artifact",
                @"replay-provenance.jsonl write failed",
                g_deadline_expired != 0, command.status, @"")];
        }
        BOOL status_ok = readback_ok && failed == 0 &&
                         (partial_mode || encoded == events.count);
        result[@"failed"] = @(failed);
        result[@"failures"] = failures;
        result[@"partial"] = @(partial_mode);
        result[@"timed_out"] = @(g_deadline_expired ? YES : NO);
        if (status_ok && g_persistent_history && g_sequence_record != nil) {
            g_last_frame_identity = [@{
                @"capture_frame_id":
                    g_sequence_record[@"capture_frame_id"] ?: @0,
                @"manifest_sha256":
                    g_sequence_record[@"manifest_sha256"] ?: @"",
                @"event_stream_sha256":
                    g_sequence_record[@"event_stream_sha256"] ?: @"",
            } mutableCopy];
        }
        if (status_ok) {
            result[@"status"] = @"ok";
            if (g_persistent_history && g_history_status == 0) {
                g_history_status = 1;
            }
        } else if (g_deadline_expired) {
            result[@"status"] = @"timeout";
        } else {
            result[@"status"] = @"failed";
        }
        NSString *primary_error = nil;
        if (failures.count > 0) {
            NSDictionary *primary = failures.firstObject;
            result[@"failure"] = primary;
            result[@"stage"] = primary[@"stage"] ?: @"replay";
            primary_error = failure_message(primary[@"stage"],
                                            [primary[@"operation"] integerValue],
                                            primary[@"error"]);
            result[@"error"] = primary_error;
        } else if (!status_ok) {
            result[@"stage"] = @"replay";
            primary_error = readback_ok ? @"replay did not complete"
                                        : @"metal readback failed";
            result[@"error"] = primary_error;
        } else {
            result[@"stage"] = @"complete";
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
            NSString *artifact_error = @"replay.json write failed";
            NSDictionary *artifact_failure = failure_entry(
                @"artifact_write", (NSInteger)events.count, @"artifact",
                artifact_error, g_deadline_expired != 0, command.status, @"");
            [failures addObject:artifact_failure];
            /* The report write failure is recorded but does not displace an
               earlier shader/event/GPU failure as the primary diagnostic. */
            NSDictionary *primary = failures.firstObject;
            result[@"failed"] = @(failed);
            result[@"failures"] = failures;
            result[@"failure"] = primary;
            result[@"stage"] = primary[@"stage"] ?: @"artifact_write";
            primary_error = failure_message(primary[@"stage"],
                                            [primary[@"operation"]
                                                integerValue],
                                            primary[@"error"]);
            result[@"error"] = primary_error;
            status_ok = NO;
        }
        if (json_written) {
            g_state.current_result_json = YES;
        }
        if (provenance_written) {
            g_state.current_provenance = YES;
        }
        if (readback_ok) {
            g_state.current_metal_frame = YES;
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
            out_result->history_status = g_history_status;
            out_result->carried_bindings = (uint32_t)g_carried_bindings;
            out_result->missing_history = (uint32_t)g_missing_history;
            out_result->stale_history = (uint32_t)g_stale_history;
            out_result->ok = (status_ok && provenance_written && json_written &&
                              failed == 0 && readback_ok &&
                              (partial_mode || encoded == events.count) &&
                              command.status == MTLCommandBufferStatusCompleted)
                                 ? 1
                                 : 0;
            out_result->duration_ns = replay_now_ns() - started_ns;
            snprintf(out_result->result_json, sizeof(out_result->result_json),
                     "%s/replay.json", safe_utf8(output_path, ""));
            if (provenance_written) {
                snprintf(out_result->provenance_jsonl,
                         sizeof(out_result->provenance_jsonl), "%s/%s",
                         safe_utf8(output_path, ""), "replay-provenance.jsonl");
            }
            if (readback_ok) {
                NSString *metal_frame_path =
                    [output_path stringByAppendingPathComponent:@"metal-frame.bgra"];
                snprintf(out_result->metal_frame,
                         sizeof(out_result->metal_frame), "%s",
                         safe_utf8(metal_frame_path, ""));
            }
            NSString *manifest_reference =
                (manifest[@"frame"] ?: @{})[@"reference_sha256"] ?: @"";
            snprintf(out_result->manifest_reference_sha256,
                     sizeof(out_result->manifest_reference_sha256), "%s",
                     safe_utf8(manifest_reference, ""));
            if (readback_ok) {
                (void)replay_sha256_file(out_result->metal_frame,
                                         out_result->metal_frame_sha256);
            }
            if (!out_result->ok) {
                NSString *message = primary_error;
                if (message.length == 0) {
                    message = readback_ok ? @"replay did not complete"
                                          : @"metal readback failed";
                }
                snprintf(out_result->error, sizeof(out_result->error), "%s",
                         safe_utf8(message, "unknown replay failure"));
            }
        }
        return failed == 0 && readback_ok ? 0 : 1;
        } @catch (NSException *exception) {
            NSString *reason = exception.reason;
            if (reason.length == 0) {
                reason = exception.name;
            }
            if (reason.length == 0) {
                reason = @"Objective-C exception during replay";
            }
            return record_failure(@"internal_exception", -1, @"frame", nil,
                                  reason, 13, nil);
        }
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
            if (config == NULL) {
                snprintf(error, error_size,
                         "invalid replay config: null config");
            } else if (config->struct_size <
                           sizeof(eu4_metal_replay_config) ||
                       config->abi_version != EU4_METAL_REPLAY_ABI_VERSION) {
                snprintf(error, error_size,
                         "invalid replay config: struct_size=%u "
                         "abi_version=%u",
                         (unsigned)config->struct_size,
                         (unsigned)config->abi_version);
            } else {
                snprintf(error, error_size,
                         "invalid replay config: frame_dir=%s shader_dir=%s "
                         "output_dir=%s",
                         config->frame_dir != NULL ? "set" : "null",
                         config->shader_dir != NULL ? "set" : "null",
                         config->output_dir != NULL ? "set" : "null");
            }
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
        state.depth_targets = [NSMutableDictionary dictionary];
        state.alpha_textures = [NSMutableDictionary dictionary];
        state.orientation_textures = [NSMutableDictionary dictionary];
        state.resource_history = [NSMutableDictionary dictionary];
        state.frame_written = [NSMutableSet set];
        state.frame_targets = [NSMutableSet set];
        state.current_carried = [NSSet set];
        state.last_frame_identity = [NSMutableDictionary dictionary];
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
    if (session == NULL || config == NULL) {
        snprintf(result->error, sizeof(result->error),
                 "invalid replay session or config: %s",
                 session == NULL ? "null session" : "null config");
        return -1;
    }
    if (config->struct_size < sizeof(eu4_metal_replay_config) ||
        config->abi_version != EU4_METAL_REPLAY_ABI_VERSION) {
        snprintf(result->error, sizeof(result->error),
                 "invalid replay config: struct_size=%u abi_version=%u",
                 (unsigned)config->struct_size,
                 (unsigned)config->abi_version);
        return -1;
    }
    if (config->frame_dir == NULL || config->shader_dir == NULL ||
        config->output_dir == NULL) {
        snprintf(result->error, sizeof(result->error),
                 "invalid replay config: frame_dir=%s shader_dir=%s "
                 "output_dir=%s",
                 config->frame_dir != NULL ? "set" : "null",
                 config->shader_dir != NULL ? "set" : "null",
                 config->output_dir != NULL ? "set" : "null");
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
        if (result->failed == 0) {
            result->failed = g_failed_count > 0 ? (uint32_t)g_failed_count : 1;
        }
        result->timed_out = g_deadline_expired ? 1 : 0;
        /* Only artifacts written by this replay may be advertised.  Paths are
           pre-filled before the run, so without this check a failure could
           point at a previous successful run in the same output directory. */
        if (!state.current_result_json ||
            (result->result_json[0] != 0 &&
             access(result->result_json, R_OK) != 0)) {
            result->result_json[0] = 0;
        }
        if (!state.current_metal_frame ||
            (result->metal_frame[0] != 0 &&
             access(result->metal_frame, R_OK) != 0)) {
            result->metal_frame[0] = 0;
            result->metal_frame_sha256[0] = 0;
        }
        if (!state.current_provenance ||
            (result->provenance_jsonl[0] != 0 &&
             access(result->provenance_jsonl, R_OK) != 0)) {
            result->provenance_jsonl[0] = 0;
        }
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

/* Host-test only: run the production repeated-blob reference verifier over a
   frame's event stream without creating a Metal device or a replay session.
   Returns 0 when every reference agrees, -1 with the error on stderr. */
extern "C" int eu4_gfx_frame_replay_test_blob_references(const char *frame_dir) {
    if (frame_dir == NULL || frame_dir[0] == 0) {
        return -1;
    }
    NSString *directory = @(frame_dir);
    NSError *error = nil;
    if (!verify_blob_references(directory, &error)) {
        fprintf(stderr, "%s\n",
                safe_utf8(failure_text(error, @"blob verification failed"),
                          "blob verification failed"));
        return -1;
    }
    return 0;
}

/* Host-test only: exercise the production compressed-texture selection and
 * upload path against a synthetic texture dictionary.  Returns 0 on success
 * and -1 with an error message when the chain is rejected. */
extern "C" int eu4_gfx_frame_replay_test_compressed_texture(
    const char *frame_dir, const char *json_path, int *out_format,
    int *out_levels, char *out_error, size_t error_capacity) {
    if (frame_dir == NULL || json_path == NULL) {
        return -1;
    }
    @autoreleasepool {
        NSError *read_error = nil;
        NSData *data = [NSData dataWithContentsOfFile:@(json_path)
                                              options:0
                                                error:&read_error];
        if (data == nil) {
            if (out_error && error_capacity) {
                snprintf(out_error, error_capacity, "%s",
                         safe_utf8(failure_text(read_error, @"unreadable JSON"),
                                   "unreadable JSON"));
            }
            return -1;
        }
        id value = [NSJSONSerialization JSONObjectWithData:data
                                                   options:0
                                                     error:&read_error];
        if (![value isKindOfClass:[NSDictionary class]]) {
            if (out_error && error_capacity) {
                snprintf(out_error, error_capacity, "%s",
                         safe_utf8(failure_text(read_error, @"invalid JSON"),
                                   "invalid JSON"));
            }
            return -1;
        }
        id<MTLDevice> device = MTLCreateSystemDefaultDevice();
        if (device == nil) {
            if (out_error && error_capacity) {
                snprintf(out_error, error_capacity, "%s",
                         "no Metal device");
            }
            return -1;
        }
        EU4MetalReplayState *state = [EU4MetalReplayState new];
        state.device = device;
        EU4MetalReplayState *previous = g_state;
        g_state = state;
        NSError *build_error = nil;
        id<MTLTexture> texture =
            build_texture((NSDictionary *)value, @(frame_dir), &build_error);
        g_state = previous;
        if (texture == nil) {
            if (out_error && error_capacity) {
                snprintf(out_error, error_capacity, "%s",
                         safe_utf8(failure_text(build_error,
                                                @"texture build failed"),
                                   "texture build failed"));
            }
            return -1;
        }
        if (out_format) {
            *out_format = (int)texture.pixelFormat;
        }
        if (out_levels) {
            *out_levels = (int)texture.mipmapLevelCount;
        }
        return 0;
    }
}

extern "C" int eu4_gfx_frame_replay_test_sample_texture(
    const char *frame_dir, const char *json_path, unsigned char *out_pixels,
    int width, int height, char *out_error, size_t error_capacity) {
    if (frame_dir == NULL || json_path == NULL || out_pixels == NULL
        || width <= 0 || height <= 0) {
        return -1;
    }
    @autoreleasepool {
        NSError *read_error = nil;
        NSData *data = [NSData dataWithContentsOfFile:@(json_path)
                                              options:0
                                                error:&read_error];
        id value = data == nil ? nil
            : [NSJSONSerialization JSONObjectWithData:data options:0
                                                error:&read_error];
        if (![value isKindOfClass:[NSDictionary class]]) {
            if (out_error && error_capacity) {
                snprintf(out_error, error_capacity, "%s",
                         safe_utf8(failure_text(read_error, @"invalid JSON"),
                                   "invalid JSON"));
            }
            return -1;
        }
        id<MTLDevice> device = MTLCreateSystemDefaultDevice();
        if (device == nil) {
            if (out_error && error_capacity) {
                snprintf(out_error, error_capacity, "%s", "no Metal device");
            }
            return -1;
        }
        EU4MetalReplayState *state = [EU4MetalReplayState new];
        state.device = device;
        EU4MetalReplayState *previous = g_state;
        g_state = state;
        NSError *build_error = nil;
        id<MTLTexture> texture =
            build_texture((NSDictionary *)value, @(frame_dir), &build_error);
        g_state = previous;
        if (texture == nil) {
            if (out_error && error_capacity) {
                snprintf(out_error, error_capacity, "%s",
                         safe_utf8(failure_text(build_error,
                                                @"texture build failed"),
                                   "texture build failed"));
            }
            return -1;
        }
        NSString *source = @
            "#include <metal_stdlib>\n"
            "using namespace metal;\n"
            "kernel void sample_tex(texture2d<float, access::sample> tex\n"
            "                       [[texture(0)]],\n"
            "                       device float4 *out [[buffer(0)]],\n"
            "                       uint2 gid [[thread_position_in_grid]]) {\n"
            "  constexpr sampler s(coord::normalized, filter::nearest,\n"
            "                      address::clamp_to_edge);\n"
            "  float2 uv = (float2(gid) + 0.5) / float2(8.0, 8.0);\n"
            "  out[gid.y * 8u + gid.x] = tex.sample(s, uv);\n"
            "}\n";
        NSError *library_error = nil;
        id<MTLLibrary> library =
            [device newLibraryWithSource:source options:nil
                                   error:&library_error];
        id<MTLFunction> function = library == nil ? nil
            : [library newFunctionWithName:@"sample_tex"];
        id<MTLComputePipelineState> pipeline = function == nil ? nil
            : [device newComputePipelineStateWithFunction:function
                                                    error:&library_error];
        if (pipeline == nil) {
            if (out_error && error_capacity) {
                snprintf(out_error, error_capacity, "%s",
                         safe_utf8(failure_text(library_error,
                                                @"sampling pipeline failed"),
                                   "sampling pipeline failed"));
            }
            return -1;
        }
        id<MTLBuffer> buffer =
            [device newBufferWithLength:(NSUInteger)(width * height * 4 * sizeof(float))
                                options:MTLResourceStorageModeShared];
        id<MTLCommandQueue> queue = [device newCommandQueue];
        id<MTLCommandBuffer> command = [queue commandBuffer];
        id<MTLComputeCommandEncoder> encoder = [command computeCommandEncoder];
        [encoder setComputePipelineState:pipeline];
        [encoder setTexture:texture atIndex:0];
        [encoder setBuffer:buffer offset:0 atIndex:0];
        [encoder dispatchThreads:MTLSizeMake((NSUInteger)width,
                                             (NSUInteger)height, 1)
            threadsPerThreadgroup:MTLSizeMake(8, 8, 1)];
        [encoder endEncoding];
        [command commit];
        [command waitUntilCompleted];
        if (command.error != nil) {
            if (out_error && error_capacity) {
                snprintf(out_error, error_capacity, "%s",
                         safe_utf8(failure_text(command.error,
                                                @"sampling failed"),
                                   "sampling failed"));
            }
            return -1;
        }
        const float *pixels = (const float *)buffer.contents;
        for (int index = 0; index < width * height; index++) {
            for (int channel = 0; channel < 4; channel++) {
                float clamped = fminf(fmaxf(pixels[index * 4 + channel], 0.0f),
                                      1.0f);
                out_pixels[index * 4 + channel] =
                    (unsigned char)lroundf(clamped * 255.0f);
            }
        }
        return 0;
    }
}

extern "C" int eu4_gfx_frame_replay_test_render_state(
    const char *json_path, char *key_out, size_t key_capacity,
    int *cull_mode, int *cull_empty, int *scissor_empty,
    int *scissor_x, int *scissor_y, int *scissor_w, int *scissor_h,
    double *viewport_x, double *viewport_y, double *viewport_w,
    double *viewport_h) {
    if (json_path == NULL) {
        return -1;
    }
    @autoreleasepool {
        NSData *data = [NSData dataWithContentsOfFile:@(json_path)];
        id value = data == nil ? nil : [NSJSONSerialization
            JSONObjectWithData:data options:0 error:NULL];
        if (![value isKindOfClass:[NSDictionary class]]) {
            return -1;
        }
        NSDictionary *document = (NSDictionary *)value;
        NSDictionary *sampler = document[@"sampler"];
        if ([sampler isKindOfClass:[NSDictionary class]]) {
            NSString *key = sampler_key_for_state(sampler);
            if (key_out != NULL && key_capacity > 0) {
                snprintf(key_out, key_capacity, "%s", key.UTF8String);
            }
        }
        NSDictionary *cull = document[@"cull"] ?: @{};
        BOOL empty = NO;
        MTLCullMode mode = MTLCullModeNone;
        BOOL known = cull_mode_for(cull, &mode, &empty);
        if (!known) {
            return -1;
        }
        if (cull_mode) *cull_mode = (int)mode;
        if (cull_empty) *cull_empty = empty ? 1 : 0;
        NSUInteger width = number(document[@"width"], 0);
        NSUInteger height = number(document[@"height"], 0);
        NSDictionary *scissor = document[@"scissor"];
        BOOL has_box = NO;
        MTLScissorRect rect = {0, 0, 0, 0};
        if ([scissor isKindOfClass:[NSDictionary class]]
            && number(scissor[@"enabled"], 0) != 0) {
            has_box = scissor_rect_for(scissor[@"box"], width, height, &rect);
        }
        if (scissor_empty) *scissor_empty = has_box ? 0 : 1;
        if (scissor_x) *scissor_x = (int)rect.x;
        if (scissor_y) *scissor_y = (int)rect.y;
        if (scissor_w) *scissor_w = (int)rect.width;
        if (scissor_h) *scissor_h = (int)rect.height;
        MTLViewport vp = viewport_for(document[@"viewport"], width, height);
        if (viewport_x) *viewport_x = vp.originX;
        if (viewport_y) *viewport_y = vp.originY;
        if (viewport_w) *viewport_w = vp.width;
        if (viewport_h) *viewport_h = vp.height;
        return 0;
    }
}

/* Host-test only: render two oppositely wound triangles through the shared
 * effective-cull projection and read back which half survived. */
extern "C" int eu4_gfx_frame_replay_test_cull_render(
    int gl_cull_mode, int cull_enabled, int primitive_mode, int no_cull,
    int *left_pixels, int *right_pixels) {
    @autoreleasepool {
        id<MTLDevice> device = MTLCreateSystemDefaultDevice();
        if (device == nil) {
            return -1;
        }
        const NSUInteger size = 16;
        MTLTextureDescriptor *descriptor = [MTLTextureDescriptor
            texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm
                                         width:size height:size mipmapped:NO];
        descriptor.usage = MTLTextureUsageRenderTarget;
        descriptor.storageMode = MTLStorageModeManaged;
        id<MTLTexture> target = [device newTextureWithDescriptor:descriptor];
        NSString *source = @
            "#include <metal_stdlib>\n"
            "using namespace metal;\n"
            "vertex float4 vertex_main(uint vid [[vertex_id]]) {\n"
            "  const float2 p[6] = { float2(-1.0,-1.0), float2(-1.0,1.0),\n"
            "                        float2(0.0,0.0),\n"
            "                        float2(1.0,-1.0), float2(1.0,1.0),\n"
            "                        float2(0.0,0.0) };\n"
            "  return float4(p[vid], 0.0, 1.0);\n"
            "}\n"
            "fragment float4 fragment_main() {\n"
            "  return float4(1.0, 0.0, 0.0, 1.0);\n"
            "}\n";
        NSError *error = nil;
        id<MTLLibrary> library =
            [device newLibraryWithSource:source options:nil error:&error];
        if (library == nil) {
            return -1;
        }
        MTLRenderPipelineDescriptor *pipeline_descriptor =
            [MTLRenderPipelineDescriptor new];
        pipeline_descriptor.vertexFunction =
            [library newFunctionWithName:@"vertex_main"];
        pipeline_descriptor.fragmentFunction =
            [library newFunctionWithName:@"fragment_main"];
        pipeline_descriptor.colorAttachments[0].pixelFormat =
            MTLPixelFormatRGBA8Unorm;
        id<MTLRenderPipelineState> pipeline =
            [device newRenderPipelineStateWithDescriptor:pipeline_descriptor
                                                   error:&error];
        if (pipeline == nil) {
            return -1;
        }
        NSDictionary *cull = @{
            @"enabled": @(cull_enabled),
            @"mode": [NSString stringWithFormat:@"0x%x", gl_cull_mode],
            @"front": @"0x901",
        };
        MTLCullMode mode = MTLCullModeNone;
        BOOL empty = NO;
        BOOL known = effective_cull(cull, (NSUInteger)primitive_mode,
                                    no_cull != 0, &mode, &empty);
        if (!known) {
            return -1;
        }
        MTLRenderPassDescriptor *pass =
            [MTLRenderPassDescriptor renderPassDescriptor];
        pass.colorAttachments[0].texture = target;
        pass.colorAttachments[0].loadAction = MTLLoadActionClear;
        pass.colorAttachments[0].storeAction = MTLStoreActionStore;
        pass.colorAttachments[0].clearColor = MTLClearColorMake(0, 0, 0, 1);
        id<MTLCommandQueue> queue = [device newCommandQueue];
        id<MTLCommandBuffer> command = [queue commandBuffer];
        id<MTLRenderCommandEncoder> encoder =
            [command renderCommandEncoderWithDescriptor:pass];
        [encoder setRenderPipelineState:pipeline];
        [encoder setFrontFacingWinding:MTLWindingCounterClockwise];
        [encoder setCullMode:empty ? MTLCullModeNone : mode];
        if (empty) {
            MTLScissorRect zero = {0, 0, 0, 0};
            [encoder setScissorRect:zero];
        }
        [encoder drawPrimitives:MTLPrimitiveTypeTriangle
                    vertexStart:0 vertexCount:6];
        [encoder endEncoding];
        [command commit];
        [command waitUntilCompleted];
        if (command.error != nil) {
            return -1;
        }
        uint8_t pixels[size * size * 4];
        MTLRegion region = MTLRegionMake2D(0, 0, size, size);
        [target getBytes:pixels bytesPerRow:size * 4
           fromRegion:region mipmapLevel:0];
        int left = 0;
        int right = 0;
        for (NSUInteger y = 0; y < size; y++) {
            for (NSUInteger x = 0; x < size; x++) {
                const uint8_t *pixel = pixels + (y * size + x) * 4;
                if (pixel[0] > 128 && pixel[1] < 64) {
                    if (x < size / 2) left++;
                    else right++;
                }
            }
        }
        if (left_pixels) *left_pixels = left;
        if (right_pixels) *right_pixels = right;
        return 0;
    }
}
