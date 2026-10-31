// One EU4 color draw into an offscreen texture. The GL draw is not skipped.
// Loaded only when EU4_METAL_DRAW_DIR is an absolute path.
#define GL_SILENCE_DEPRECATION 1
#import <ImageIO/ImageIO.h>
#import <Metal/Metal.h>
#import <OpenGL/OpenGL.h>
#import <OpenGL/gl.h>
#include <dlfcn.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

enum { kMaxDim = 512, kMaxShader = 200 * 1024, kMaxPrograms = 1024, kMaxVerts = 8192 };

static char g_dir[1024];
static char g_vert_path[1024];
static char g_frag_path[1024];
static pthread_mutex_t g_lock = PTHREAD_MUTEX_INITIALIZER;
static int g_done;
static GLuint g_color_program;
static GLuint g_rejected[kMaxPrograms];
static int g_rejected_count;
static atomic_int g_flushes;
static int g_draw_invoked;

// A CCW clip-space triangle with back-face culling survives only this winding
// under the viewport below. The host test locks that mapping in.
static MTLWinding winding_for_front(int front_ccw) {
    return front_ccw ? MTLWindingCounterClockwise : MTLWindingClockwise;
}

static NSString *read_source(const char *path) {
    return [NSString stringWithContentsOfFile:@(path) encoding:NSUTF8StringEncoding error:nil];
}

int eu4_metal_draw_render(const void *vertices, int vertex_bytes, int stride, int color_offset,
                          int color_is_byte, const float *uniform, int vertex_count, int width,
                          int height, int as_strip, int cull_mode, int front_ccw, uint8_t *pixels_out) {
    @autoreleasepool {
        if (vertices == NULL || uniform == NULL || stride < 12 || vertex_count < 3 ||
            vertex_bytes < stride * vertex_count) {
            return -1;
        }
        if (width < 2) width = 2;
        if (height < 2) height = 2;
        if (width > kMaxDim) width = kMaxDim;
        if (height > kMaxDim) height = kMaxDim;
        id<MTLDevice> device = MTLCreateSystemDefaultDevice();
        NSError *error = nil;
        id<MTLLibrary> vert_lib = [device newLibraryWithSource:read_source(g_vert_path) options:nil error:&error];
        id<MTLLibrary> frag_lib = [device newLibraryWithSource:read_source(g_frag_path) options:nil error:&error];
        if (device == nil || vert_lib == nil || frag_lib == nil) {
            return -1;
        }
        MTLVertexDescriptor *layout = [MTLVertexDescriptor vertexDescriptor];
        layout.attributes[0].format = MTLVertexFormatFloat2;
        layout.attributes[0].offset = 0;
        layout.attributes[0].bufferIndex = 1;
        layout.attributes[1].format = color_is_byte ? MTLVertexFormatUChar4Normalized : MTLVertexFormatFloat4;
        layout.attributes[1].offset = (NSUInteger)color_offset;
        layout.attributes[1].bufferIndex = 1;
        layout.layouts[1].stride = (NSUInteger)stride;
        MTLTextureDescriptor *desc =
            [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm
                                                               width:(NSUInteger)width
                                                              height:(NSUInteger)height
                                                           mipmapped:NO];
        desc.usage = MTLTextureUsageRenderTarget;
        desc.storageMode = MTLStorageModePrivate;
        id<MTLTexture> texture = [device newTextureWithDescriptor:desc];
        MTLRenderPipelineDescriptor *pipeline = [MTLRenderPipelineDescriptor new];
        pipeline.vertexFunction = [vert_lib newFunctionWithName:@"main0"];
        pipeline.fragmentFunction = [frag_lib newFunctionWithName:@"main0"];
        pipeline.vertexDescriptor = layout;
        pipeline.colorAttachments[0].pixelFormat = MTLPixelFormatBGRA8Unorm;
        id<MTLRenderPipelineState> state = [device newRenderPipelineStateWithDescriptor:pipeline error:&error];
        id<MTLBuffer> vertex_buffer =
            [device newBufferWithBytes:vertices length:(NSUInteger)vertex_bytes options:MTLResourceStorageModeShared];
        id<MTLBuffer> uniform_buffer =
            [device newBufferWithBytes:uniform length:sizeof(float) * 16 options:MTLResourceStorageModeShared];
        id<MTLBuffer> readback =
            [device newBufferWithLength:(NSUInteger)(width * height * 4) options:MTLResourceStorageModeShared];
        if (state == nil || texture == nil || vertex_buffer == nil || uniform_buffer == nil || readback == nil) {
            return -1;
        }
        id<MTLCommandBuffer> buffer = [[device newCommandQueue] commandBuffer];
        MTLRenderPassDescriptor *pass = [MTLRenderPassDescriptor renderPassDescriptor];
        pass.colorAttachments[0].texture = texture;
        pass.colorAttachments[0].loadAction = MTLLoadActionClear;
        pass.colorAttachments[0].storeAction = MTLStoreActionStore;
        pass.colorAttachments[0].clearColor = MTLClearColorMake(0, 0, 0, 1);
        id<MTLRenderCommandEncoder> encoder = [buffer renderCommandEncoderWithDescriptor:pass];
        [encoder setRenderPipelineState:state];
        [encoder setViewport:(MTLViewport){0, 0, width, height, 0, 1}];
        [encoder setCullMode:cull_mode == 0 ? MTLCullModeNone : (cull_mode == 1 ? MTLCullModeBack : MTLCullModeFront)];
        [encoder setFrontFacingWinding:winding_for_front(front_ccw)];
        [encoder setVertexBuffer:uniform_buffer offset:0 atIndex:0];
        [encoder setVertexBuffer:vertex_buffer offset:0 atIndex:1];
        [encoder drawPrimitives:as_strip ? MTLPrimitiveTypeTriangleStrip : MTLPrimitiveTypeTriangle
                       vertexStart:0 vertexCount:(NSUInteger)vertex_count];
        [encoder endEncoding];
        id<MTLBlitCommandEncoder> blit = [buffer blitCommandEncoder];
        [blit copyFromTexture:texture sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0, 0, 0)
                   sourceSize:MTLSizeMake(width, height, 1) toBuffer:readback destinationOffset:0
          destinationBytesPerRow:width * 4 destinationBytesPerImage:width * height * 4];
        [blit endEncoding];
        [buffer commit];
        [buffer waitUntilCompleted];
        if (buffer.status != MTLCommandBufferStatusCompleted) {
            return -1;
        }
        if (pixels_out != NULL) {
            memcpy(pixels_out, readback.contents, (size_t)(width * height * 4));
        }
        const uint8_t *bytes = readback.contents;
        int colored = 0;
        for (int i = 0; i < width * height * 4; i += 4) {
            if (bytes[i] | bytes[i + 1] | bytes[i + 2]) {
                colored++;
            }
        }
        return colored;
    }
}

// GL_SHADER_SOURCE_LENGTH includes the NUL. Refuse before allocating.
int eu4_metal_draw_copy_shader_source(GLuint shader, char **text, int *length) {
    *text = NULL;
    *length = 0;
    if (shader == 0 || !glIsShader(shader)) {
        return -1;
    }
    GLint size = 0;
    glGetShaderiv(shader, GL_SHADER_SOURCE_LENGTH, &size);
    if (size < 2 || size > kMaxShader) {
        return -1;
    }
    char *buf = malloc((size_t)size);
    if (buf == NULL) {
        return -1;
    }
    GLsizei written = 0;
    glGetShaderSource(shader, size, &written, buf);
    if (written < 1 || written >= size) {
        free(buf);
        return -1;
    }
    buf[written] = 0;
    *text = buf;
    *length = (int)written;
    return 0;
}

static int rejected(GLuint program) {
    for (int i = 0; i < g_rejected_count; i++) {
        if (g_rejected[i] == program) {
            return 1;
        }
    }
    return 0;
}

static void reject(GLuint program) {
    if (program == 0 || rejected(program) || g_rejected_count >= kMaxPrograms) {
        return;
    }
    g_rejected[g_rejected_count++] = program;
}

static void write_png(const uint8_t *bgra, int width, int height, const char *path) {
    uint8_t *rgba = malloc((size_t)width * (size_t)height * 4);
    if (rgba == NULL) {
        return;
    }
    for (int i = 0; i < width * height; i++) {
        rgba[i * 4] = bgra[i * 4 + 2];
        rgba[i * 4 + 1] = bgra[i * 4 + 1];
        rgba[i * 4 + 2] = bgra[i * 4];
        rgba[i * 4 + 3] = bgra[i * 4 + 3];
    }
    CGDataProviderRef provider = CGDataProviderCreateWithData(NULL, rgba, (size_t)width * (size_t)height * 4, NULL);
    CGColorSpaceRef space = CGColorSpaceCreateDeviceRGB();
    CGImageRef image = CGImageCreate((size_t)width, (size_t)height, 8, 32, (size_t)width * 4, space,
                                     kCGImageAlphaLast | kCGBitmapByteOrder32Big, provider, NULL, false,
                                     kCGRenderingIntentDefault);
    CFURLRef url = CFURLCreateFromFileSystemRepresentation(NULL, (const UInt8 *)path, (CFIndex)strlen(path), false);
    CGImageDestinationRef dest = CGImageDestinationCreateWithURL(url, CFSTR("public.png"), 1, NULL);
    if (dest != NULL) {
        CGImageDestinationAddImage(dest, image, NULL);
        CGImageDestinationFinalize(dest);
        CFRelease(dest);
    }
    CFRelease(url);
    CGImageRelease(image);
    CGColorSpaceRelease(space);
    CGDataProviderRelease(provider);
    free(rgba);
}

static const char *json_string(const char *text) {
    return text ? text : "";
}

static void write_result(int pixels, int invoked, const char *cause, GLuint program, const char *mode,
                         int stride, int shared, int indexed, int vertex_count, float min_x, float min_y,
                         float max_x, float max_y, int pos_loc, GLenum pos_type, int pos_size, int pos_off,
                         GLuint pos_buf, int col_loc, GLenum col_type, int col_size, int col_norm, int col_off,
                         GLuint col_buf, int cull, GLenum cull_mode, GLenum front, const char *metal_winding) {
    char path[1100];
    snprintf(path, sizeof path, "%s/result.json", g_dir);
    FILE *out = fopen(path, "w");
    if (out == NULL) {
        return;
    }
    fprintf(out,
            "{\n"
            "  \"original_gl_draw_invoked\": %s,\n"
            "  \"gl_flush_success_count\": %d,\n"
            "  \"metal_nonzero_pixels\": %d,\n"
            "  \"cause\": %s%s%s,\n"
            "  \"program\": %u,\n"
            "  \"mode\": \"%s\",\n"
            "  \"stride\": %d,\n"
            "  \"shared_attribute_buffer\": %s,\n"
            "  \"indexed\": %s,\n"
            "  \"vertex_count\": %d,\n"
            "  \"position_min\": [%g, %g],\n"
            "  \"position_max\": [%g, %g],\n"
            "  \"position_location\": %d,\n"
            "  \"position_type\": %u,\n"
            "  \"position_size\": %d,\n"
            "  \"position_offset\": %d,\n"
            "  \"position_buffer\": %u,\n"
            "  \"color_location\": %d,\n"
            "  \"color_type\": %u,\n"
            "  \"color_size\": %d,\n"
            "  \"color_normalized\": %s,\n"
            "  \"color_offset\": %d,\n"
            "  \"color_buffer\": %u,\n"
            "  \"cull_face\": %s,\n"
            "  \"cull_mode\": %u,\n"
            "  \"front_face\": %u,\n"
            "  \"metal_winding\": \"%s\",\n"
            "  \"depth_test\": %s,\n"
            "  \"blend\": %s,\n"
            "  \"png\": \"readback.png\"\n"
            "}\n",
            invoked ? "true" : "false", atomic_load(&g_flushes), pixels,
            cause ? "\"" : "null", json_string(cause), cause ? "\"" : "",
            program, mode, stride, shared ? "true" : "false", indexed ? "true" : "false", vertex_count,
            min_x, min_y, max_x, max_y, pos_loc, pos_type, pos_size, pos_off, pos_buf, col_loc, col_type,
            col_size, col_norm ? "true" : "false", col_off, col_buf, cull ? "true" : "false", cull_mode, front,
            metal_winding, glIsEnabled(GL_DEPTH_TEST) ? "true" : "false",
            glIsEnabled(GL_BLEND) ? "true" : "false");
    fclose(out);
}

static void *read_buffer(GLenum target, GLuint binding, GLenum binding_query, int *out_size) {
    GLint previous = 0;
    glGetIntegerv(binding_query, &previous);
    glBindBuffer(target, binding);
    GLint size = 0;
    glGetBufferParameteriv(target, GL_BUFFER_SIZE, &size);
    void *data = NULL;
    if (size > 0 && size <= 4 * 1024 * 1024) {
        data = malloc((size_t)size);
        if (data != NULL) {
            glGetBufferSubData(target, 0, size, data);
        }
    }
    glBindBuffer(target, (GLuint)previous);
    *out_size = data ? size : 0;
    return data;
}

static int in_range(int buffer_size, int offset, int stride, int index, int bytes) {
    if (stride <= 0 || index < 0 || bytes <= 0 || offset < 0) {
        return 0;
    }
    long long start = (long long)offset + (long long)index * stride;
    return start >= 0 && start + bytes <= buffer_size;
}

static void finish_draw(GLenum mode, GLint first, GLsizei count, int indexed, GLenum index_type,
                        const void *indices, int base_vertex) {
    if (g_done || count < 3 || count > kMaxVerts) {
        return;
    }
    GLint program = 0;
    glGetIntegerv(GL_CURRENT_PROGRAM, &program);
    if (program == 0 || rejected((GLuint)program)) {
        return;
    }
    if ((GLuint)program != g_color_program) {
        GLint attached = 0;
        glGetProgramiv((GLuint)program, GL_ATTACHED_SHADERS, &attached);
        if (attached <= 0) {
            reject((GLuint)program);
            return;
        }
        if (attached > 8) {
            reject((GLuint)program);
            return;
        }
        GLuint shaders[8];
        GLsizei shader_count = 0;
        glGetAttachedShaders((GLuint)program, 8, &shader_count, shaders);
        int vertex = 0, fragment = 0, unreadable = 0;
        for (GLsizei i = 0; i < shader_count; i++) {
            char *text = NULL;
            int length = 0;
            if (eu4_metal_draw_copy_shader_source(shaders[i], &text, &length) != 0) {
                unreadable++;
                continue;
            }
            GLint type = 0;
            glGetShaderiv(shaders[i], GL_SHADER_TYPE, &type);
            if (type == GL_VERTEX_SHADER && strstr(text, "float2 vPosition") && strstr(text, "in_PDX_COLOR")) {
                vertex = 1;
            }
            // The shared header names sampler types. Color declares no sampler uniform.
            if (type == GL_FRAGMENT_SHADER && strstr(text, "v_TEXCOORD1") && strstr(text, "uniform sampler") == NULL) {
                fragment = 1;
            }
            free(text);
        }
        if (shader_count > 0 && unreadable == shader_count) {
            write_result(0, g_draw_invoked, "shaders detached after link", (GLuint)program, "none", 0, 0,
                         indexed, count, 0, 0, 0, 0, -1, 0, 0, 0, 0, -1, 0, 0, 0, 0, 0, 0, 0, 0, "none");
            g_done = 1;
            return;
        }
        if (!vertex || !fragment) {
            reject((GLuint)program);
            return;
        }
        g_color_program = (GLuint)program;
    }
    if (mode != GL_TRIANGLES && mode != GL_TRIANGLE_STRIP) {
        return; /* shortcut: quads are skipped, upgrade if Color is only drawn as quads */
    }
    const char *failure = NULL;
    float uniform[16];
    GLint pos = -1, col = -1;
    GLint pos_buf = 0, pos_size = 0, pos_type = 0, pos_stride = 0;
    GLint col_buf = 0, col_size = 0, col_type = 0, col_norm = 0, col_stride = 0;
    void *pos_ptr = NULL, *col_ptr = NULL;
    for (int i = 0; i < 4; i++) {
        char name[32];
        snprintf(name, sizeof name, "UNIFORM0[%d]", i);
        GLint location = glGetUniformLocation(g_color_program, name);
        if (location < 0) {
            failure = "UNIFORM0 element is missing";
            break;
        }
        glGetUniformfv(g_color_program, location, uniform + i * 4);
    }
    if (failure == NULL) {
        pos = glGetAttribLocation(g_color_program, "in_POSITION");
        col = glGetAttribLocation(g_color_program, "in_PDX_COLOR");
    }
    if (failure == NULL && (pos < 0 || col < 0)) {
        failure = "color attributes are missing";
    }
    if (failure == NULL) {
        glGetVertexAttribiv(pos, GL_VERTEX_ATTRIB_ARRAY_BUFFER_BINDING, &pos_buf);
        glGetVertexAttribiv(pos, GL_VERTEX_ATTRIB_ARRAY_SIZE, &pos_size);
        glGetVertexAttribiv(pos, GL_VERTEX_ATTRIB_ARRAY_TYPE, &pos_type);
        glGetVertexAttribiv(pos, GL_VERTEX_ATTRIB_ARRAY_STRIDE, &pos_stride);
        glGetVertexAttribPointerv(pos, GL_VERTEX_ATTRIB_ARRAY_POINTER, &pos_ptr);
        glGetVertexAttribiv(col, GL_VERTEX_ATTRIB_ARRAY_BUFFER_BINDING, &col_buf);
        glGetVertexAttribiv(col, GL_VERTEX_ATTRIB_ARRAY_SIZE, &col_size);
        glGetVertexAttribiv(col, GL_VERTEX_ATTRIB_ARRAY_TYPE, &col_type);
        glGetVertexAttribiv(col, GL_VERTEX_ATTRIB_ARRAY_NORMALIZED, &col_norm);
        glGetVertexAttribiv(col, GL_VERTEX_ATTRIB_ARRAY_STRIDE, &col_stride);
        glGetVertexAttribPointerv(col, GL_VERTEX_ATTRIB_ARRAY_POINTER, &col_ptr);
        if (pos_buf == 0 || pos_buf != col_buf || pos_type != GL_FLOAT || pos_size != 2 || col_size != 4 ||
            pos_stride <= 0 || pos_stride != col_stride ||
            (col_type != GL_FLOAT && col_type != GL_UNSIGNED_BYTE) ||
            (col_type == GL_UNSIGNED_BYTE && !col_norm)) {
            failure = "unsupported color attribute layout";
        }
    }
    int color_is_byte = col_type == GL_UNSIGNED_BYTE;
    int color_bytes = color_is_byte ? 4 : 16;
    int pos_off = (int)(uintptr_t)pos_ptr;
    int col_off = (int)(uintptr_t)col_ptr;
    int vertex_size = 0;
    void *vertex_data = failure ? NULL : read_buffer(GL_ARRAY_BUFFER, (GLuint)pos_buf, GL_ARRAY_BUFFER_BINDING, &vertex_size);
    if (failure == NULL && vertex_data == NULL) {
        failure = "vertex buffer could not be read";
    }
    int element_size = 0;
    void *element_data = NULL;
    GLenum element_type = index_type;
    int index_offset = (int)(uintptr_t)indices;
    if (failure == NULL && indexed) {
        if (element_type != GL_UNSIGNED_SHORT && element_type != GL_UNSIGNED_INT) {
            failure = "unsupported index type";
        } else {
            GLint element_binding = 0;
            glGetIntegerv(GL_ELEMENT_ARRAY_BUFFER_BINDING, &element_binding);
            if (element_binding == 0) {
                failure = "client index pointer is not a buffer";
            } else {
                element_data = read_buffer(GL_ELEMENT_ARRAY_BUFFER, (GLuint)element_binding,
                                           GL_ELEMENT_ARRAY_BUFFER_BINDING, &element_size);
                int index_size = element_type == GL_UNSIGNED_SHORT ? 2 : 4;
                if (element_data == NULL || index_offset < 0 ||
                    (long long)index_offset + (long long)count * index_size > element_size) {
                    failure = "index read is outside the element buffer";
                }
            }
        }
    }
    int packed = 8 + color_bytes;
    void *gathered = NULL;
    if (failure == NULL) {
        gathered = malloc((size_t)count * (size_t)packed);
        if (gathered == NULL) {
            failure = "vertex buffer could not be read";
        }
    }
    if (failure == NULL) {
        for (GLsizei i = 0; i < count; i++) {
            int vertex = first + (int)i;
            if (indexed) {
                int index_size = element_type == GL_UNSIGNED_SHORT ? 2 : 4;
                const unsigned char *entry = (const unsigned char *)element_data + index_offset + (int)i * index_size;
                vertex = index_size == 2 ? ((const uint16_t *)entry)[0] : (int)((const uint32_t *)entry)[0];
                vertex += base_vertex;
            }
            if (!in_range(vertex_size, pos_off, pos_stride, vertex, 8) ||
                !in_range(vertex_size, col_off, pos_stride, vertex, color_bytes)) {
                failure = "attribute read is outside the vertex buffer";
                break;
            }
            char *dst = (char *)gathered + (size_t)i * (size_t)packed;
            memcpy(dst, (char *)vertex_data + pos_off + (size_t)vertex * (size_t)pos_stride, 8);
            memcpy(dst + 8, (char *)vertex_data + col_off + (size_t)vertex * (size_t)pos_stride, (size_t)color_bytes);
        }
    }
    float min_x = 0, min_y = 0, max_x = 0, max_y = 0;
    if (failure == NULL) {
        for (GLsizei i = 0; i < count; i++) {
            float x = *(float *)((char *)gathered + (size_t)i * (size_t)packed);
            float y = *(float *)((char *)gathered + (size_t)i * (size_t)packed + 4);
            if (i == 0 || x < min_x) min_x = x;
            if (i == 0 || y < min_y) min_y = y;
            if (i == 0 || x > max_x) max_x = x;
            if (i == 0 || y > max_y) max_y = y;
        }
    }
    GLint cull_on = glIsEnabled(GL_CULL_FACE);
    GLint cull_mode = GL_BACK, front = GL_CCW;
    glGetIntegerv(GL_CULL_FACE_MODE, &cull_mode);
    glGetIntegerv(GL_FRONT_FACE, &front);
    int metal_cull = !cull_on ? 0 : (cull_mode == GL_FRONT ? 2 : 1);
    const char *winding_name = front == GL_CCW ? "counter_clockwise" : "clockwise";
    GLint viewport[4] = {0, 0, 256, 256};
    glGetIntegerv(GL_VIEWPORT, viewport);
    int pixels = -1;
    uint8_t *image = NULL;
    if (failure == NULL) {
        int width = viewport[2] > 0 ? viewport[2] : 2;
        int height = viewport[3] > 0 ? viewport[3] : 2;
        if (width > kMaxDim) width = kMaxDim;
        if (height > kMaxDim) height = kMaxDim;
        image = malloc((size_t)width * (size_t)height * 4);
        pixels = eu4_metal_draw_render(gathered, count * packed, packed, 8, color_is_byte, uniform, count,
                                       width, height, mode == GL_TRIANGLE_STRIP, metal_cull, front == GL_CCW, image);
        if (pixels < 0) {
            failure = "metal pipeline failed";
        } else if (pixels == 0) {
            free(image);
            free(gathered);
            free(vertex_data);
            free(element_data);
            return;
        }
    }
    if (image != NULL && pixels > 0) {
        char png[1100];
        snprintf(png, sizeof png, "%s/readback.png", g_dir);
        int width = viewport[2] > kMaxDim ? kMaxDim : viewport[2];
        int height = viewport[3] > kMaxDim ? kMaxDim : viewport[3];
        write_png(image, width < 2 ? 2 : width, height < 2 ? 2 : height, png);
    }
    write_result(pixels < 0 ? 0 : pixels, g_draw_invoked, failure, g_color_program,
                 mode == GL_TRIANGLE_STRIP ? "triangle_strip" : "triangles", pos_stride, pos_buf == col_buf,
                 indexed, count, min_x, min_y, max_x, max_y, pos, (GLenum)pos_type, pos_size, pos_off,
                 (GLuint)pos_buf, col, (GLenum)col_type, col_size, col_norm, col_off, (GLuint)col_buf, cull_on,
                 (GLenum)cull_mode, (GLenum)front, winding_name);
    g_done = 1;
    free(image);
    free(gathered);
    free(vertex_data);
    free(element_data);
}

static void draw_arrays(GLenum mode, GLint first, GLsizei count) {
    glDrawArrays(mode, first, count);
    g_draw_invoked = 1;
    if (g_dir[0] != '/' || g_done) {
        return;
    }
    pthread_mutex_lock(&g_lock);
    finish_draw(mode, first, count, 0, 0, NULL, 0);
    pthread_mutex_unlock(&g_lock);
}

static void draw_elements(GLenum mode, GLsizei count, GLenum type, const void *indices) {
    glDrawElements(mode, count, type, indices);
    g_draw_invoked = 1;
    if (g_dir[0] != '/' || g_done) {
        return;
    }
    pthread_mutex_lock(&g_lock);
    finish_draw(mode, 0, count, 1, type, indices, 0);
    pthread_mutex_unlock(&g_lock);
}

static void *hooked_dlsym(void *handle, const char *symbol) {
    void *found = dlsym(handle, symbol);
    if (symbol == NULL) {
        return found;
    }
    if (strcmp(symbol, "glDrawArrays") == 0 || strcmp(symbol, "glDrawArraysARB") == 0) {
        return (void *)draw_arrays;
    }
    if (strcmp(symbol, "glDrawElements") == 0 || strcmp(symbol, "glDrawElementsARB") == 0) {
        return (void *)draw_elements;
    }
    return found;
}

static CGLError flush_drawable(CGLContextObj context) {
    CGLError result = CGLFlushDrawable(context);
    if (result != kCGLNoError || g_dir[0] != '/') {
        return result;
    }
    int count = atomic_fetch_add(&g_flushes, 1) + 1;
    if (count == 1) {
        char path[1100];
        snprintf(path, sizeof path, "%s/gl_flushes", g_dir);
        FILE *out = fopen(path, "w");
        if (out != NULL) {
            fprintf(out, "%d\n", count);
            fclose(out);
        }
    }
    return result;
}

#define INTERPOSE(repl, orig) \
    __attribute__((used, section("__DATA,__interpose"))) \
    static const struct { const void *replacement; const void *replacee; } \
        interpose_##orig = {(const void *)(repl), (const void *)(orig)}

INTERPOSE(flush_drawable, CGLFlushDrawable);
INTERPOSE(hooked_dlsym, dlsym);
INTERPOSE(draw_arrays, glDrawArrays);
INTERPOSE(draw_elements, glDrawElements);

__attribute__((constructor)) static void eu4_metal_draw_init(void) {
    const char *dir = getenv("EU4_METAL_DRAW_DIR");
    const char *vert = getenv("EU4_METAL_DRAW_VERT");
    const char *frag = getenv("EU4_METAL_DRAW_FRAG");
    if (dir == NULL || dir[0] != '/' || vert == NULL || frag == NULL) {
        return;
    }
    snprintf(g_dir, sizeof g_dir, "%s", dir);
    snprintf(g_vert_path, sizeof g_vert_path, "%s", vert);
    snprintf(g_frag_path, sizeof g_frag_path, "%s", frag);
}
