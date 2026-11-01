#import "eu4_b2_draw.h"
#import "eu4_gfx_init.h"

#import <Metal/Metal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static const char *json_find(const char *json, const char *key) {
    char pattern[64];
    snprintf(pattern, sizeof pattern, "\"%s\"", key);
    const char *found = strstr(json, pattern);
    if (found == NULL) return NULL;
    found = strchr(found + strlen(pattern), ':');
    if (found == NULL) return NULL;
    found++;
    while (*found == ' ' || *found == '\n') found++;
    return found;
}

static int json_int(const char *json, const char *key, int *out) {
    const char *value = json_find(json, key);
    if (value == NULL) return -1;
    *out = atoi(value);
    return 0;
}

static int json_string(const char *json, const char *key, char *out, int cap) {
    const char *value = json_find(json, key);
    if (value == NULL || *value != '"') return -1;
    value++;
    int n = 0;
    while (value[n] != '"' && value[n] != 0 && n + 1 < cap) {
        out[n] = value[n];
        n++;
    }
    out[n] = 0;
    return value[n] == '"' ? 0 : -1;
}

static uint8_t *read_file(const char *path, int *out_size) {
    FILE *in = fopen(path, "rb");
    if (in == NULL) return NULL;
    if (fseek(in, 0, SEEK_END) != 0) {
        fclose(in);
        return NULL;
    }
    long size = ftell(in);
    if (size < 0 || size > (64 << 20)) {
        fclose(in);
        return NULL;
    }
    rewind(in);
    uint8_t *bytes = (uint8_t *)malloc((size_t)size + 1);
    if (bytes == NULL || (size > 0 && fread(bytes, 1, (size_t)size, in) != (size_t)size)) {
        free(bytes);
        fclose(in);
        return NULL;
    }
    bytes[size] = 0;
    fclose(in);
    *out_size = (int)size;
    return bytes;
}

static MTLVertexFormat format_for(const char *name) {
    if (strcmp(name, "float2") == 0) return MTLVertexFormatFloat2;
    if (strcmp(name, "float3") == 0) return MTLVertexFormatFloat3;
    if (strcmp(name, "float4") == 0) return MTLVertexFormatFloat4;
    if (strcmp(name, "uchar4_norm") == 0) return MTLVertexFormatUChar4Normalized;
    return MTLVertexFormatInvalid;
}

static id<MTLDepthStencilState> depth_state(id<MTLDevice> device, int use_b1, int depth_test, int depth_write,
                                            int depth_func) {
    if (use_b1) {
        uint8_t desc[EU4_GFX_DEPTH_DESC_SIZE];
        memset(desc, 0, sizeof desc);
        eu4_gfx_write_default_depth_stencil(desc);
        void *raw = eu4_gfx_default_depth_stencil((__bridge void *)device, desc);
        if (raw == NULL) return nil;
        return (__bridge_transfer id<MTLDepthStencilState>)raw;
    }
    if (!depth_test) return nil;
    MTLDepthStencilDescriptor *descriptor = [MTLDepthStencilDescriptor new];
    int compare = depth_func - 0x200;
    if (compare < 0 || compare > 7) return nil;
    descriptor.depthCompareFunction = (MTLCompareFunction)compare;
    descriptor.depthWriteEnabled = depth_write ? YES : NO;
    return [device newDepthStencilStateWithDescriptor:descriptor];
}

int eu4_b2_draw_fixture(const char *dir, uint8_t *pixels, int capacity) {
    @autoreleasepool {
        char path[1100];
        snprintf(path, sizeof path, "%s/fixture.json", dir);
        int json_size = 0;
        char *json = (char *)read_file(path, &json_size);
        if (json == NULL) return -1;
        int width = 0, height = 0, elements = 0, first = 0, count = 0, index_offset = 0, stride = 0;
        int attrib_count = 0, texture_count = 0, stencil_enable = 0, depth_test = 0, depth_write = 0;
        int depth_func = 0, depth_matches_b1 = 0, blend = 0, cull = 0, front_ccw = 1, scissor = 0;
        int tex_w = 0, tex_h = 0, tex_linear = 0, box[4] = {0, 0, 0, 0};
        if (json_int(json, "width", &width) || json_int(json, "height", &height) ||
            json_int(json, "elements", &elements) || json_int(json, "first", &first) ||
            json_int(json, "count", &count) || json_int(json, "index_offset", &index_offset) ||
            json_int(json, "stride", &stride) || json_int(json, "attrib_count", &attrib_count) ||
            json_int(json, "texture_count", &texture_count) || json_int(json, "stencil_enable", &stencil_enable) ||
            json_int(json, "depth_test", &depth_test) || json_int(json, "depth_write", &depth_write) ||
            json_int(json, "depth_func", &depth_func) || json_int(json, "depth_matches_b1", &depth_matches_b1) ||
            json_int(json, "blend", &blend) || json_int(json, "cull", &cull) ||
            json_int(json, "front_ccw", &front_ccw) || json_int(json, "scissor", &scissor) ||
            json_int(json, "tex0_width", &tex_w) || json_int(json, "tex0_height", &tex_h) ||
            json_int(json, "tex0_linear", &tex_linear)) {
            free(json);
            return -1;
        }
        const char *box_text = json_find(json, "scissor_box");
        if (box_text == NULL || sscanf(box_text, "[%d, %d, %d, %d]", &box[0], &box[1], &box[2], &box[3]) != 4) {
            free(json);
            return -1;
        }
        char mode[32] = {0}, index_type[32] = {0}, evidence[16] = {0};
        if (json_string(json, "mode", mode, sizeof mode) || json_string(json, "index_type", index_type, sizeof index_type) ||
            json_string(json, "depth_evidence", evidence, sizeof evidence)) {
            free(json);
            return -1;
        }
        if (stencil_enable) {
            free(json);
            return -1;
        }
        int use_b1 = depth_matches_b1 && strcmp(evidence, "engine") != 0 && strcmp(evidence, "gl") == 0;
        if (depth_matches_b1 && strcmp(evidence, "engine") == 0) use_b1 = 1;
        int pixels_needed = width * height * 4;
        if (width < 1 || height < 1 || pixels == NULL || capacity < pixels_needed || attrib_count < 1 ||
            attrib_count > 4 || texture_count > 1) {
            free(json);
            return -1;
        }
        snprintf(path, sizeof path, "%s/draw.vert.metal", dir);
        int vert_size = 0, frag_size = 0, vert_bytes = 0;
        char *vert_src = (char *)read_file(path, &vert_size);
        snprintf(path, sizeof path, "%s/draw.frag.metal", dir);
        char *frag_src = (char *)read_file(path, &frag_size);
        snprintf(path, sizeof path, "%s/vertices.bin", dir);
        uint8_t *vertices = read_file(path, &vert_bytes);
        if (vert_src == NULL || frag_src == NULL || vertices == NULL) {
            free(json);
            free(vert_src);
            free(frag_src);
            free(vertices);
            return -1;
        }
        id<MTLDevice> device = MTLCreateSystemDefaultDevice();
        NSError *error = nil;
        id<MTLLibrary> vert_lib = [device newLibraryWithSource:@(vert_src) options:nil error:&error];
        id<MTLLibrary> frag_lib = [device newLibraryWithSource:@(frag_src) options:nil error:&error];
        if (vert_lib == nil || frag_lib == nil) {
            free(json);
            free(vert_src);
            free(frag_src);
            free(vertices);
            return -1;
        }
        MTLVertexDescriptor *layout = [MTLVertexDescriptor vertexDescriptor];
        for (int i = 0; i < attrib_count; i++) {
            char key[32], format_name[32];
            int offset = 0, buffer = 0;
            snprintf(key, sizeof key, "attrib%d_format", i);
            if (json_string(json, key, format_name, sizeof format_name)) {
                free(json);
                return -1;
            }
            snprintf(key, sizeof key, "attrib%d_offset", i);
            if (json_int(json, key, &offset)) return -1;
            snprintf(key, sizeof key, "attrib%d_buffer", i);
            if (json_int(json, key, &buffer)) return -1;
            MTLVertexFormat format = format_for(format_name);
            if (format == MTLVertexFormatInvalid) return -1;
            layout.attributes[i].format = format;
            layout.attributes[i].offset = (NSUInteger)offset;
            layout.attributes[i].bufferIndex = (NSUInteger)buffer;
            layout.layouts[buffer].stride = (NSUInteger)stride;
        }
        free(json);
        MTLTextureDescriptor *color_desc =
            [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm
                                                               width:(NSUInteger)width
                                                              height:(NSUInteger)height
                                                           mipmapped:NO];
        color_desc.usage = MTLTextureUsageRenderTarget;
        color_desc.storageMode = MTLStorageModePrivate;
        id<MTLTexture> color = [device newTextureWithDescriptor:color_desc];
        id<MTLTexture> depth_tex = nil;
        if (depth_test) {
            MTLTextureDescriptor *depth_desc =
                [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatDepth32Float
                                                                   width:(NSUInteger)width
                                                                  height:(NSUInteger)height
                                                               mipmapped:NO];
            depth_desc.usage = MTLTextureUsageRenderTarget;
            depth_desc.storageMode = MTLStorageModePrivate;
            depth_tex = [device newTextureWithDescriptor:depth_desc];
        }
        MTLRenderPipelineDescriptor *pipeline = [MTLRenderPipelineDescriptor new];
        pipeline.vertexFunction = [vert_lib newFunctionWithName:@"main0"];
        pipeline.fragmentFunction = [frag_lib newFunctionWithName:@"main0"];
        pipeline.vertexDescriptor = layout;
        pipeline.colorAttachments[0].pixelFormat = MTLPixelFormatRGBA8Unorm;
        if (blend) {
            pipeline.colorAttachments[0].blendingEnabled = YES;
            pipeline.colorAttachments[0].sourceRGBBlendFactor = MTLBlendFactorSourceAlpha;
            pipeline.colorAttachments[0].destinationRGBBlendFactor = MTLBlendFactorOneMinusSourceAlpha;
            pipeline.colorAttachments[0].rgbBlendOperation = MTLBlendOperationAdd;
            pipeline.colorAttachments[0].sourceAlphaBlendFactor = MTLBlendFactorSourceAlpha;
            pipeline.colorAttachments[0].destinationAlphaBlendFactor = MTLBlendFactorOneMinusSourceAlpha;
            pipeline.colorAttachments[0].alphaBlendOperation = MTLBlendOperationAdd;
        }
        if (depth_test) pipeline.depthAttachmentPixelFormat = MTLPixelFormatDepth32Float;
        id<MTLRenderPipelineState> state = [device newRenderPipelineStateWithDescriptor:pipeline error:&error];
        id<MTLBuffer> vertex_buffer =
            [device newBufferWithBytes:vertices length:(NSUInteger)vert_bytes options:MTLResourceStorageModeShared];
        id<MTLTexture> texture = nil;
        if (texture_count == 1) {
            snprintf(path, sizeof path, "%s/texture0.bin", dir);
            int tex_bytes = 0;
            uint8_t *tex_pixels = read_file(path, &tex_bytes);
            if (tex_pixels == NULL || tex_bytes != tex_w * tex_h * 4) {
                free(tex_pixels);
                return -1;
            }
            MTLTextureDescriptor *tex_desc =
                [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm
                                                                   width:(NSUInteger)tex_w
                                                                  height:(NSUInteger)tex_h
                                                               mipmapped:NO];
            tex_desc.usage = MTLTextureUsageShaderRead;
            texture = [device newTextureWithDescriptor:tex_desc];
            [texture replaceRegion:MTLRegionMake2D(0, 0, (NSUInteger)tex_w, (NSUInteger)tex_h)
                       mipmapLevel:0
                         withBytes:tex_pixels
                       bytesPerRow:(NSUInteger)tex_w * 4];
            free(tex_pixels);
        }
        int index_bytes = 0;
        uint8_t *indices = NULL;
        if (elements) {
            snprintf(path, sizeof path, "%s/indices.bin", dir);
            indices = read_file(path, &index_bytes);
            if (indices == NULL) return -1;
        }
        id<MTLBuffer> index_buffer = nil;
        if (indices != NULL) {
            index_buffer = [device newBufferWithBytes:indices length:(NSUInteger)index_bytes
                                              options:MTLResourceStorageModeShared];
        }
        id<MTLBuffer> readback =
            [device newBufferWithLength:(NSUInteger)pixels_needed options:MTLResourceStorageModeShared];
        id<MTLDepthStencilState> depth = depth_state(device, use_b1, depth_test, depth_write, depth_func);
        if (state == nil || color == nil || vertex_buffer == nil || readback == nil) return -1;
        if (depth_test && depth == nil) return -1;
        id<MTLCommandBuffer> buffer = [[device newCommandQueue] commandBuffer];
        MTLRenderPassDescriptor *pass = [MTLRenderPassDescriptor renderPassDescriptor];
        pass.colorAttachments[0].texture = color;
        pass.colorAttachments[0].loadAction = MTLLoadActionClear;
        pass.colorAttachments[0].storeAction = MTLStoreActionStore;
        pass.colorAttachments[0].clearColor = MTLClearColorMake(0, 0, 0, 0);
        if (depth_tex != nil) {
            pass.depthAttachment.texture = depth_tex;
            pass.depthAttachment.loadAction = MTLLoadActionClear;
            pass.depthAttachment.storeAction = MTLStoreActionDontCare;
            pass.depthAttachment.clearDepth = 1.0;
        }
        id<MTLRenderCommandEncoder> encoder = [buffer renderCommandEncoderWithDescriptor:pass];
        [encoder setRenderPipelineState:state];
        if (depth != nil) [encoder setDepthStencilState:depth];
        /* Negative height matches glReadPixels' bottom-left origin. */
        [encoder setViewport:(MTLViewport){0, (double)height, (double)width, -(double)height, 0, 1}];
        [encoder setFrontFacingWinding:front_ccw ? MTLWindingCounterClockwise : MTLWindingClockwise];
        [encoder setCullMode:cull == 0 ? MTLCullModeNone : cull == 2 ? MTLCullModeFront : MTLCullModeBack];
        if (scissor && box[2] > 0 && box[3] > 0) {
            int metal_y = height - (box[1] + box[3]);
            if (metal_y < 0) metal_y = 0;
            MTLScissorRect rect = {(NSUInteger)box[0], (NSUInteger)metal_y, (NSUInteger)box[2], (NSUInteger)box[3]};
            [encoder setScissorRect:rect];
        }
        [encoder setVertexBuffer:vertex_buffer offset:0 atIndex:0];
        if (texture != nil) {
            [encoder setFragmentTexture:texture atIndex:0];
        }
        if (elements && index_buffer != nil) {
            MTLIndexType type = strcmp(index_type, "uint16") == 0 ? MTLIndexTypeUInt16 : MTLIndexTypeUInt32;
            [encoder drawIndexedPrimitives:strcmp(mode, "triangle_strip") == 0 ? MTLPrimitiveTypeTriangleStrip
                                                                               : MTLPrimitiveTypeTriangle
                                indexCount:(NSUInteger)count
                                 indexType:type
                               indexBuffer:index_buffer
                         indexBufferOffset:(NSUInteger)index_offset];
        } else {
            [encoder drawPrimitives:strcmp(mode, "triangle_strip") == 0 ? MTLPrimitiveTypeTriangleStrip
                                                                        : MTLPrimitiveTypeTriangle
                        vertexStart:(NSUInteger)first
                        vertexCount:(NSUInteger)count];
        }
        [encoder endEncoding];
        id<MTLBlitCommandEncoder> blit = [buffer blitCommandEncoder];
        [blit copyFromTexture:color sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0, 0, 0)
                   sourceSize:MTLSizeMake((NSUInteger)width, (NSUInteger)height, 1)
                     toBuffer:readback
            destinationOffset:0
       destinationBytesPerRow:(NSUInteger)width * 4
     destinationBytesPerImage:(NSUInteger)pixels_needed];
        [blit endEncoding];
        [buffer commit];
        [buffer waitUntilCompleted];
        free(vert_src);
        free(frag_src);
        free(vertices);
        free(indices);
        if (buffer.status != MTLCommandBufferStatusCompleted) return -1;
        memcpy(pixels, readback.contents, (size_t)pixels_needed);
        return 0;
    }
}
