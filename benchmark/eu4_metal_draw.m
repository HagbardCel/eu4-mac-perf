// One EU4 draw into an offscreen texture. The GL draw is not skipped.
// Loaded only when EU4_METAL_DRAW_DIR is an absolute path.
#define GL_SILENCE_DEPRECATION 1
#import <Metal/Metal.h>
#import <OpenGL/OpenGL.h>
#import <OpenGL/gl.h>
#include <dlfcn.h>
#include <pthread.h>
#include <stdint.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

enum { kMaxPrograms = 16, kMaxBuffers = 32, kMaxBytes = 262144, kMaxDim = 512 };

static char g_dir[1024];
static char g_vert_path[1024];
static char g_frag_path[1024];
static pthread_mutex_t g_lock = PTHREAD_MUTEX_INITIALIZER;
static int g_done;
static GLuint g_shaders[kMaxPrograms];
static int g_shader_count;
static GLuint g_programs[kMaxPrograms];
static int g_program_count;
static GLuint g_current_program;
static GLuint g_array_buffer;
static GLuint g_element_buffer;
typedef struct {
    GLuint id;
    GLenum target;
    void *bytes;
    int size;
} BufferCopy;
static BufferCopy g_buffers[kMaxBuffers];
static float g_uniform[16];
static int g_uniform_ready;
static GLint g_attrib_size[2];
static GLenum g_attrib_type[2];
static GLsizei g_attrib_stride[2];
static uintptr_t g_attrib_offset[2];

static int contains_id(const GLuint *ids, int count, GLuint id) {
    for (int i = 0; i < count; i++) {
        if (ids[i] == id) {
            return 1;
        }
    }
    return 0;
}

static void remember(GLuint *ids, int *count, GLuint id) {
    if (id == 0 || contains_id(ids, *count, id) || *count >= kMaxPrograms) {
        return;
    }
    ids[(*count)++] = id;
}

static BufferCopy *find_buffer(GLuint id, GLenum target) {
    for (int i = 0; i < kMaxBuffers; i++) {
        if (g_buffers[i].id == id && g_buffers[i].target == target) {
            return &g_buffers[i];
        }
    }
    return NULL;
}

static void store_bytes(GLuint id, GLenum target, const void *data, int size) {
    if (id == 0 || size <= 0 || size > kMaxBytes) {
        return;
    }
    BufferCopy *slot = find_buffer(id, target);
    if (slot == NULL) {
        for (int i = 0; i < kMaxBuffers; i++) {
            if (g_buffers[i].id == 0) {
                slot = &g_buffers[i];
                break;
            }
        }
    }
    if (slot == NULL) {
        return;
    }
    void *copy = malloc((size_t)size);
    if (copy == NULL) {
        return;
    }
    if (data == NULL) {
        free(copy);
        return;
    }
    memcpy(copy, data, (size_t)size);
    free(slot->bytes);
    slot->id = id;
    slot->target = target;
    slot->bytes = copy;
    slot->size = size;
}

static NSString *read_source(const char *path) {
    return [NSString stringWithContentsOfFile:@(path) encoding:NSUTF8StringEncoding error:nil];
}

// Returns the number of non-black pixels, or -1 if the pipeline could not be built.
int eu4_metal_draw_render(const void *vertices, int vertex_bytes, int stride, int color_offset,
                          int color_is_byte, const float *uniform, int vertex_count, int width,
                          int height, int as_strip, uint8_t *pixels_out) {
    @autoreleasepool {
        if (vertices == NULL || uniform == NULL || stride < 8 || vertex_count < 3 ||
            vertex_bytes < stride * vertex_count) {
            return -1;
        }
        if (width < 2) {
            width = 2;
        }
        if (height < 2) {
            height = 2;
        }
        if (width > kMaxDim) {
            width = kMaxDim;
        }
        if (height > kMaxDim) {
            height = kMaxDim;
        }
        id<MTLDevice> device = MTLCreateSystemDefaultDevice();
        NSString *vert = read_source(g_vert_path);
        NSString *frag = read_source(g_frag_path);
        NSError *error = nil;
        id<MTLLibrary> vert_lib = vert ? [device newLibraryWithSource:vert options:nil error:&error] : nil;
        id<MTLLibrary> frag_lib = frag ? [device newLibraryWithSource:frag options:nil error:&error] : nil;
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

static void write_result(int colored, int vertex_count, int stride, int color_is_byte,
                         float min_x, float min_y, float max_x, float max_y, int has_bounds,
                         const char *cause) {
    char path[1100];
    snprintf(path, sizeof path, "%s/result.json", g_dir);
    FILE *out = fopen(path, "w");
    if (out == NULL) {
        return;
    }
    fprintf(out,
            "{\n"
            "  \"nonzero_pixels\": %d,\n"
            "  \"vertex_count\": %d,\n"
            "  \"stride\": %d,\n"
            "  \"color_is_byte\": %s,\n"
            "  \"depth_test\": %s,\n"
            "  \"blend\": %s,\n"
            "  \"cull_face\": %s,\n"
            "  \"position_min\": [%g, %g],\n"
            "  \"position_max\": [%g, %g],\n"
            "  \"has_position_bounds\": %s,\n"
            "  \"cause\": %s%s%s\n"
            "}\n",
            colored, vertex_count, stride, color_is_byte ? "true" : "false",
            glIsEnabled(GL_DEPTH_TEST) ? "true" : "false",
            glIsEnabled(GL_BLEND) ? "true" : "false",
            glIsEnabled(GL_CULL_FACE) ? "true" : "false",
            min_x, min_y, max_x, max_y, has_bounds ? "true" : "false",
            cause ? "\"" : "null", cause ? cause : "", cause ? "\"" : "");
    fclose(out);
}

static int g_misses;
static int g_shader_calls;
static int g_draw_calls;

static void note(const char *line) {
    if (g_dir[0] != '/') {
        return;
    }
    char path[1100];
    snprintf(path, sizeof path, "%s/hooks.log", g_dir);
    FILE *out = fopen(path, "a");
    if (out == NULL) {
        return;
    }
    fprintf(out, "%s\n", line);
    fclose(out);
}

static void try_draw(GLint first, GLsizei count, int as_strip, int as_quad,
                       const void *linear, int linear_bytes) {
    if (g_done || !contains_id(g_programs, g_program_count, g_current_program)) {
        return;
    }
    int stride = g_attrib_stride[0] ? (int)g_attrib_stride[0] : (g_attrib_type[1] == GL_UNSIGNED_BYTE ? 12 : 24);
    int color_is_byte = g_attrib_type[1] == GL_UNSIGNED_BYTE;
    int color_offset = g_attrib_type[1] == 0 ? 8 : (int)g_attrib_offset[1];
    if (color_offset < 0 || color_offset > 64) {
        color_offset = 8;
    }
    if (g_attrib_type[0] != 0 && g_attrib_type[0] != GL_FLOAT) {
        write_result(0, count, stride, color_is_byte, 0, 0, 0, 0, 0, "position attribute is not float");
        g_done = 1;
        return;
    }
    if (g_attrib_type[1] != 0 && g_attrib_type[1] != GL_FLOAT && g_attrib_type[1] != GL_UNSIGNED_BYTE) {
        write_result(0, count, stride, color_is_byte, 0, 0, 0, 0, 0, "color attribute is not float4 or ubyte4");
        g_done = 1;
        return;
    }
    const void *bytes = NULL;
    int bytes_size = 0;
    void *owned = NULL;
    BufferCopy *slot = find_buffer(g_array_buffer, GL_ARRAY_BUFFER);
    int needed = stride * (first + count);
    if (linear != NULL && linear_bytes >= stride * count) {
        bytes = linear;
        bytes_size = linear_bytes;
        first = 0;
    } else if (slot != NULL && needed <= slot->size) {
        bytes = slot->bytes;
        bytes_size = slot->size;
    } else if (g_array_buffer == 0 && g_attrib_offset[0] != 0 && needed > 0 && needed <= kMaxBytes) {
        owned = malloc((size_t)needed);
        if (owned != NULL) {
            memcpy(owned, (const void *)g_attrib_offset[0], (size_t)needed);
            bytes = owned;
            bytes_size = needed;
        }
    }
    if (bytes == NULL || !g_uniform_ready || count < 3) {
        if (g_misses < 6) {
            char line[220];
            snprintf(line, sizeof line,
                     "miss bytes=%d uniform=%d buffer=%u stride=%d count=%d type0=%u type1=%u",
                     bytes != NULL, g_uniform_ready, g_array_buffer, stride, count,
                     g_attrib_type[0], g_attrib_type[1]);
            note(line);
        }
        free(owned);
        if (++g_misses >= 32) {
            write_result(0, count, stride, color_is_byte, 0, 0, 0, 0, 0,
                         "no CPU vertex snapshot for the color shader");
            g_done = 1;
        }
        return;
    }
    GLint viewport[4] = {0, 0, 256, 256};
    glGetIntegerv(GL_VIEWPORT, viewport);
    const char *start = (const char *)bytes + (size_t)first * (size_t)stride;
    void *quads = NULL;
    int render_count = count;
    const char *render_start = start;
    int render_bytes = bytes_size - first * stride;
    if (as_quad) {
        if (count < 4 || (count % 4) != 0) {
            free(owned);
            return;
        }
        render_count = (count / 4) * 6;
        quads = malloc((size_t)render_count * (size_t)stride);
        if (quads == NULL) {
            free(owned);
            return;
        }
        const int order[6] = {0, 1, 2, 0, 2, 3};
        for (int quad = 0; quad < count / 4; quad++) {
            for (int corner = 0; corner < 6; corner++) {
                memcpy((char *)quads + ((size_t)quad * 6 + (size_t)corner) * (size_t)stride,
                       start + ((size_t)quad * 4 + (size_t)order[corner]) * (size_t)stride,
                       (size_t)stride);
            }
        }
        render_start = quads;
        render_bytes = render_count * stride;
        as_strip = 0;
    }
    float min_x = 0, min_y = 0, max_x = 0, max_y = 0;
    for (int i = 0; i < render_count; i++) {
        float x = *(const float *)(render_start + (size_t)i * (size_t)stride);
        float y = *(const float *)(render_start + (size_t)i * (size_t)stride + 4);
        if (i == 0 || x < min_x) min_x = x;
        if (i == 0 || y < min_y) min_y = y;
        if (i == 0 || x > max_x) max_x = x;
        if (i == 0 || y > max_y) max_y = y;
    }
    int colored = eu4_metal_draw_render(render_start, render_bytes, stride, color_offset,
                                        color_is_byte, g_uniform, render_count, viewport[2], viewport[3],
                                        as_strip, NULL);
    free(quads);
    free(owned);
    if (colored <= 0) {
        if (++g_misses < 8) {
            note(colored < 0 ? "pipeline failed" : "draw produced no pixels");
            return;
        }
    }
    write_result(colored, count, stride, color_is_byte, min_x, min_y, max_x, max_y, 1,
                 colored < 0 ? "metal pipeline failed" : (colored == 0 ? "draw produced no pixels" : NULL));
    g_done = 1;
}

static void draw_elements(GLenum mode, GLsizei count, GLenum type, const void *indices);
static void shader_source(GLuint shader, GLsizei count, const GLchar *const *string, const GLint *length);
static void attach_shader(GLuint program, GLuint shader);
static void use_program(GLuint program);
static void bind_buffer(GLenum target, GLuint buffer);
static void buffer_data(GLenum target, GLsizeiptr size, const void *data, GLenum usage);
static void uniform4fv(GLint location, GLsizei count, const GLfloat *value);
static void attrib_pointer(GLuint index, GLint size, GLenum type, GLboolean normalized,
                                    GLsizei stride, const void *pointer);

static void *hooked_dlsym(void *handle, const char *symbol) {
    void *found = dlsym(handle, symbol);
    if (symbol == NULL) {
        return found;
    }
    if (strncmp(symbol, "gl", 2) == 0 && g_shader_calls == 0 && strstr(symbol, "Shader") != NULL) {
        char line[160];
        snprintf(line, sizeof line, "dlsym %s", symbol);
        note(line);
    }
    if (strcmp(symbol, "glShaderSource") == 0 || strcmp(symbol, "glShaderSourceARB") == 0) {
        return (void *)shader_source;
    }
    if (strcmp(symbol, "glAttachShader") == 0 || strcmp(symbol, "glAttachObjectARB") == 0) {
        return (void *)attach_shader;
    }
    if (strcmp(symbol, "glUseProgram") == 0 || strcmp(symbol, "glUseProgramARB") == 0 ||
        strcmp(symbol, "glUseProgramObjectARB") == 0) {
        return (void *)use_program;
    }
    if (strcmp(symbol, "glDrawElements") == 0 || strcmp(symbol, "glDrawElementsARB") == 0) {
        return (void *)draw_elements;
    }
    if (strcmp(symbol, "glBindBuffer") == 0 || strcmp(symbol, "glBindBufferARB") == 0) {
        return (void *)bind_buffer;
    }
    if (strcmp(symbol, "glBufferData") == 0 || strcmp(symbol, "glBufferDataARB") == 0) {
        return (void *)buffer_data;
    }
    if (strcmp(symbol, "glUniform4fv") == 0 || strcmp(symbol, "glUniform4fvARB") == 0) {
        return (void *)uniform4fv;
    }
    if (strcmp(symbol, "glVertexAttribPointer") == 0 || strcmp(symbol, "glVertexAttribPointerARB") == 0) {
        return (void *)attrib_pointer;
    }
    return found;
}

static void draw_arrays(GLenum mode, GLint first, GLsizei count) {
    glDrawArrays(mode, first, count);
    if (g_dir[0] == 0) {
        return;
    }
    int as_strip = mode == GL_TRIANGLE_STRIP;
    int as_quad = mode == GL_QUADS;
    if (g_draw_calls < 8 || (g_draw_calls < 40 && contains_id(g_programs, g_program_count, g_current_program))) {
        char line[80];
        snprintf(line, sizeof line, "draw_arrays mode=%u first=%d count=%d", mode, first, count);
        note(line);
    }
    g_draw_calls++;
    if (mode != GL_TRIANGLES && mode != GL_TRIANGLE_STRIP && mode != GL_QUADS) {
        return;
    }
    int stride = g_attrib_stride[0] ? (int)g_attrib_stride[0] : 24;
    if (first < 0 || stride <= 0) {
        return;
    }
    pthread_mutex_lock(&g_lock);
    try_draw(first, count, as_strip, as_quad, NULL, 0);
    pthread_mutex_unlock(&g_lock);
}

static void *read_bound_buffer(GLenum target, int *out_size) {
    GLint size = 0;
    glGetBufferParameteriv(target, GL_BUFFER_SIZE, &size);
    *out_size = size;
    if (size <= 0 || size > 4 * 1024 * 1024) {
        return NULL;
    }
    void *data = malloc((size_t)size);
    if (data != NULL) {
        glGetBufferSubData(target, 0, size, data);
    }
    return data;
}

static void draw_elements(GLenum mode, GLsizei count, GLenum type, const void *indices) {
    glDrawElements(mode, count, type, indices);
    if (g_dir[0] == 0) {
        return;
    }
    pthread_mutex_lock(&g_lock);
    if (!contains_id(g_programs, g_program_count, g_current_program)) {
        pthread_mutex_unlock(&g_lock);
        return;
    }
    int index_size = type == GL_UNSIGNED_SHORT ? 2 : type == GL_UNSIGNED_INT ? 4 : 0;
    if ((mode != GL_TRIANGLES && mode != GL_TRIANGLE_STRIP) || count < 3 || count > 16384 || index_size == 0) {
        static int rejected;
        if (rejected < 4) {
            char line[120];
            snprintf(line, sizeof line, "elements reject mode=%u count=%d type=%u", mode, count, type);
            note(line);
            rejected++;
        }
        pthread_mutex_unlock(&g_lock);
        return;
    }
    int stride = g_attrib_stride[0] ? (int)g_attrib_stride[0] : 24;
    int vertex_size = 0;
    int element_size = 0;
    void *vertex_owned = read_bound_buffer(GL_ARRAY_BUFFER, &vertex_size);
    void *element_owned = g_element_buffer != 0 ? read_bound_buffer(GL_ELEMENT_ARRAY_BUFFER, &element_size) : NULL;
    const unsigned char *index_bytes = NULL;
    void *index_copy = NULL;
    size_t index_offset = (size_t)(uintptr_t)indices;
    if (element_owned != NULL && index_offset + (size_t)index_size * (size_t)count <= (size_t)element_size) {
        index_bytes = (const unsigned char *)element_owned + index_offset;
    } else if (g_element_buffer == 0 && indices != NULL) {
        index_copy = malloc((size_t)index_size * (size_t)count);
        if (index_copy != NULL) {
            memcpy(index_copy, indices, (size_t)index_size * (size_t)count);
            index_bytes = index_copy;
        }
    }
    void *gathered = NULL;
    if (index_bytes != NULL && vertex_owned != NULL && stride > 0) {
        gathered = malloc((size_t)count * (size_t)stride);
    }
    if (gathered != NULL) {
        for (GLsizei i = 0; i < count; i++) {
            uint32_t index = index_size == 2 ? ((const uint16_t *)index_bytes)[i]
                                             : ((const uint32_t *)index_bytes)[i];
            if ((size_t)(index + 1) * (size_t)stride > (size_t)vertex_size) {
                free(gathered);
                gathered = NULL;
                break;
            }
            memcpy((char *)gathered + (size_t)i * (size_t)stride,
                   (const char *)vertex_owned + (size_t)index * (size_t)stride, (size_t)stride);
        }
    }
    if (gathered != NULL) {
        try_draw(0, count, mode == GL_TRIANGLE_STRIP, 0, gathered, count * stride);
    } else {
        static int element_notes;
        if (element_notes < 6) {
            char line[180];
            snprintf(line, sizeof line, "elements miss vert_bytes=%d index_bytes=%d count=%d stride=%d",
                     vertex_size, element_size, count, stride);
            note(line);
            element_notes++;
        }
    }
    free(gathered);
    free(vertex_owned);
    free(element_owned);
    free(index_copy);
    pthread_mutex_unlock(&g_lock);
}

static void shader_source(GLuint shader, GLsizei count, const GLchar *const *string, const GLint *length) {
    glShaderSource(shader, count, string, length);
    if (g_dir[0] == 0 || string == NULL) {
        return;
    }
    size_t total = 0;
    for (GLsizei i = 0; i < count; i++) {
        total += string[i] ? strlen(string[i]) : 0;
    }
    if (total == 0 || total > 200000) {
        return;
    }
    char *text = malloc(total + 1);
    if (text == NULL) {
        return;
    }
    size_t used = 0;
    for (GLsizei i = 0; i < count; i++) {
        if (string[i] == NULL) {
            continue;
        }
        size_t n = length && length[i] > 0 ? (size_t)length[i] : strlen(string[i]);
        memcpy(text + used, string[i], n);
        used += n;
    }
    text[used] = 0;
    int match = strstr(text, "float2 vPosition") != NULL && strstr(text, "in_PDX_COLOR") != NULL;
    if (match || g_shader_calls < 4) {
        char line[120];
        snprintf(line, sizeof line, "shader_source bytes=%zu match=%d", used, match);
        note(line);
    }
    if (match && g_dir[0] == '/') {
        char path[1100];
        snprintf(path, sizeof path, "%s/matched.glsl", g_dir);
        FILE *dump = fopen(path, "w");
        if (dump != NULL) {
            fwrite(text, 1, used, dump);
            fclose(dump);
        }
    }
    g_shader_calls++;
    free(text);
    if (!match) {
        return;
    }
    pthread_mutex_lock(&g_lock);
    remember(g_shaders, &g_shader_count, shader);
    pthread_mutex_unlock(&g_lock);
}

static void attach_shader(GLuint program, GLuint shader) {
    glAttachShader(program, shader);
    pthread_mutex_lock(&g_lock);
    if (contains_id(g_shaders, g_shader_count, shader)) {
        remember(g_programs, &g_program_count, program);
    }
    pthread_mutex_unlock(&g_lock);
}

static int g_use_notes;

static void use_program(GLuint program) {
    glUseProgram(program);
    g_current_program = program;
    if (g_use_notes < 15 && contains_id(g_programs, g_program_count, program)) {
        char line[64];
        snprintf(line, sizeof line, "use_program %u", program);
        note(line);
        g_use_notes++;
    }
}

static void bind_buffer(GLenum target, GLuint buffer) {
    glBindBuffer(target, buffer);
    if (target == GL_ARRAY_BUFFER) {
        g_array_buffer = buffer;
    } else if (target == GL_ELEMENT_ARRAY_BUFFER) {
        g_element_buffer = buffer;
    }
}

static void buffer_data(GLenum target, GLsizeiptr size, const void *data, GLenum usage) {
    glBufferData(target, size, data, usage);
    (void)usage;
    GLuint id = target == GL_ARRAY_BUFFER ? g_array_buffer : g_element_buffer;
    if (target != GL_ARRAY_BUFFER && target != GL_ELEMENT_ARRAY_BUFFER) {
        return;
    }
    pthread_mutex_lock(&g_lock);
    store_bytes(id, target, data, (int)size);
    pthread_mutex_unlock(&g_lock);
}

static void uniform4fv(GLint location, GLsizei count, const GLfloat *value) {
    glUniform4fv(location, count, value);
    if (count >= 4 && value != NULL) {
        pthread_mutex_lock(&g_lock);
        memcpy(g_uniform, value, sizeof g_uniform);
        g_uniform_ready = 1;
        pthread_mutex_unlock(&g_lock);
    }
}

static void attrib_pointer(GLuint index, GLint size, GLenum type, GLboolean normalized,
                                    GLsizei stride, const void *pointer) {
    glVertexAttribPointer(index, size, type, normalized, stride, pointer);
    if (index < 2) {
        g_attrib_size[index] = size;
        g_attrib_type[index] = type;
        g_attrib_stride[index] = stride;
        g_attrib_offset[index] = (uintptr_t)pointer;
    }
}

#define INTERPOSE(repl, orig) \
    __attribute__((used, section("__DATA,__interpose"))) \
    static const struct { const void *replacement; const void *replacee; } \
        interpose_##orig = {(const void *)(repl), (const void *)(orig)}

static atomic_int g_flushes;

static CGLError flush_drawable(CGLContextObj context) {
    CGLError result = CGLFlushDrawable(context);
    int count = atomic_fetch_add(&g_flushes, 1) + 1;
    if (count == 1 && g_dir[0] == '/') {
        char side[1100];
        snprintf(side, sizeof side, "%s/gl_flushes", g_dir);
        FILE *out = fopen(side, "w");
        if (out != NULL) {
            fprintf(out, "%d\n", count);
            fclose(out);
        }
    }
    return result;
}

INTERPOSE(flush_drawable, CGLFlushDrawable);
INTERPOSE(hooked_dlsym, dlsym);
INTERPOSE(draw_arrays, glDrawArrays);
INTERPOSE(draw_elements, glDrawElements);
INTERPOSE(shader_source, glShaderSource);
INTERPOSE(attach_shader, glAttachShader);
INTERPOSE(use_program, glUseProgram);
INTERPOSE(bind_buffer, glBindBuffer);
INTERPOSE(buffer_data, glBufferData);
INTERPOSE(uniform4fv, glUniform4fv);
INTERPOSE(attrib_pointer, glVertexAttribPointer);

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
