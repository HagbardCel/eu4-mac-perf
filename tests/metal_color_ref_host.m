#define GL_SILENCE_DEPRECATION
#import <CommonCrypto/CommonDigest.h>
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <OpenGL/OpenGL.h>
#import <OpenGL/gl.h>
#import <OpenGL/glext.h>
#import <math.h>
#import <stdio.h>
#import <stdlib.h>
#import <string.h>

enum { kDim = 32, kPixels = kDim * kDim, kRow = kDim * 4 };

static const uint8_t kClear[4] = {16, 32, 48, 255};
static const uint8_t kFill[4] = {128, 64, 192, 255};

static GLuint g_program;
static GLint g_uniform;
static GLuint g_vbo;
static id<MTLDevice> g_device;
static id<MTLCommandQueue> g_queue;
static id<MTLRenderPipelineState> g_pipeline;

static void die(const char *message) {
    fprintf(stderr, "%s\n", message);
    exit(1);
}

static void gl_ok(const char *where) {
    GLenum error = glGetError();
    if (error != GL_NO_ERROR) {
        fprintf(stderr, "gl error 0x%x at %s\n", error, where);
        exit(1);
    }
}

static NSString *read_text(const char *path) {
    NSError *error = nil;
    NSString *text = [NSString stringWithContentsOfFile:@(path) encoding:NSUTF8StringEncoding error:&error];
    if (text == nil) {
        fprintf(stderr, "cannot read %s\n", path);
        exit(1);
    }
    return text;
}

static GLuint compile_shader(GLenum type, NSString *source) {
    const char *text = source.UTF8String;
    GLuint shader = glCreateShader(type);
    glShaderSource(shader, 1, &text, NULL);
    glCompileShader(shader);
    GLint ok = 0;
    glGetShaderiv(shader, GL_COMPILE_STATUS, &ok);
    if (!ok) {
        char log[4096];
        glGetShaderInfoLog(shader, (GLsizei)sizeof log, NULL, log);
        fprintf(stderr, "%s\n", log);
        exit(1);
    }
    return shader;
}

static void init_gl(NSString *vert, NSString *frag) {
    CGLPixelFormatAttribute attrs[] = {
        kCGLPFAOpenGLProfile, (CGLPixelFormatAttribute)kCGLOGLPVersion_Legacy,
        kCGLPFAAccelerated,
        kCGLPFAColorSize, (CGLPixelFormatAttribute)24,
        kCGLPFAAlphaSize, (CGLPixelFormatAttribute)8,
        0
    };
    CGLPixelFormatObj format = NULL;
    GLint count = 0;
    if (CGLChoosePixelFormat(attrs, &format, &count) != kCGLNoError || format == NULL) {
        die("no legacy opengl pixel format");
    }
    CGLContextObj context = NULL;
    if (CGLCreateContext(format, NULL, &context) != kCGLNoError || context == NULL) {
        die("no legacy opengl context");
    }
    CGLDestroyPixelFormat(format);
    if (CGLSetCurrentContext(context) != kCGLNoError) {
        die("no current GL context");
    }
    while (glGetError() != GL_NO_ERROR) {
    }

    GLuint color = 0;
    GLuint fbo = 0;
    glGenTextures(1, &color);
    glBindTexture(GL_TEXTURE_2D, color);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, kDim, kDim, 0, GL_RGBA, GL_UNSIGNED_BYTE, NULL);
    glGenFramebuffers(1, &fbo);
    glBindFramebuffer(GL_FRAMEBUFFER, fbo);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, color, 0);
    if (glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE) {
        die("fbo incomplete");
    }
    glViewport(0, 0, kDim, kDim);
    glDisable(GL_DEPTH_TEST);
    glDisable(GL_BLEND);
    glDisable(GL_STENCIL_TEST);
    glDisable(GL_SCISSOR_TEST);
    glDisable(GL_DITHER);
    glEnable(GL_CULL_FACE);
    glCullFace(GL_BACK);
    gl_ok("framebuffer");

    GLuint vs = compile_shader(GL_VERTEX_SHADER, vert);
    GLuint fs = compile_shader(GL_FRAGMENT_SHADER, frag);
    g_program = glCreateProgram();
    glAttachShader(g_program, vs);
    glAttachShader(g_program, fs);
    glLinkProgram(g_program);
    GLint linked = 0;
    glGetProgramiv(g_program, GL_LINK_STATUS, &linked);
    if (!linked) {
        char log[4096];
        glGetProgramInfoLog(g_program, (GLsizei)sizeof log, NULL, log);
        fprintf(stderr, "%s\n", log);
        exit(1);
    }
    glUseProgram(g_program);
    g_uniform = glGetUniformLocation(g_program, "UNIFORM0");
    GLint pos = glGetAttribLocation(g_program, "in_POSITION");
    GLint color_attr = glGetAttribLocation(g_program, "in_PDX_COLOR");
    if (g_uniform < 0 || pos < 0 || color_attr < 0) {
        die("shader interface missing");
    }
    glGenBuffers(1, &g_vbo);
    glBindBuffer(GL_ARRAY_BUFFER, g_vbo);
    glVertexAttribPointer((GLuint)pos, 2, GL_FLOAT, GL_FALSE, 12, (void *)0);
    glEnableVertexAttribArray((GLuint)pos);
    glVertexAttribPointer((GLuint)color_attr, 4, GL_UNSIGNED_BYTE, GL_TRUE, 12, (void *)8);
    glEnableVertexAttribArray((GLuint)color_attr);
    gl_ok("program");
}

static void init_metal(NSString *vert, NSString *frag) {
    g_device = MTLCreateSystemDefaultDevice();
    if (g_device == nil) {
        die("no metal device");
    }
    NSError *error = nil;
    id<MTLLibrary> vert_lib = [g_device newLibraryWithSource:vert options:nil error:&error];
    id<MTLLibrary> frag_lib = vert_lib ? [g_device newLibraryWithSource:frag options:nil error:&error] : nil;
    if (vert_lib == nil || frag_lib == nil) {
        fprintf(stderr, "%s\n", error.localizedDescription.UTF8String);
        exit(1);
    }
    MTLVertexDescriptor *layout = [MTLVertexDescriptor vertexDescriptor];
    layout.attributes[0].format = MTLVertexFormatFloat2;
    layout.attributes[0].offset = 0;
    layout.attributes[0].bufferIndex = 1;
    layout.attributes[1].format = MTLVertexFormatUChar4Normalized;
    layout.attributes[1].offset = 8;
    layout.attributes[1].bufferIndex = 1;
    layout.layouts[1].stride = 12;
    MTLRenderPipelineDescriptor *pipeline = [MTLRenderPipelineDescriptor new];
    pipeline.vertexFunction = [vert_lib newFunctionWithName:@"main0"];
    pipeline.fragmentFunction = [frag_lib newFunctionWithName:@"main0"];
    pipeline.vertexDescriptor = layout;
    pipeline.colorAttachments[0].pixelFormat = MTLPixelFormatBGRA8Unorm;
    pipeline.colorAttachments[0].blendingEnabled = NO;
    g_pipeline = [g_device newRenderPipelineStateWithDescriptor:pipeline error:&error];
    g_queue = [g_device newCommandQueue];
    if (g_pipeline == nil || g_queue == nil) {
        fprintf(stderr, "%s\n", error.localizedDescription.UTF8String ?: "metal pipeline failed");
        exit(1);
    }
}

static void render_gl(const uint8_t *verts, const float *matrix, int front_cw, uint8_t *top_down) {
    glFrontFace(front_cw ? GL_CW : GL_CCW);
    glUniform4fv(g_uniform, 4, matrix);
    glBufferData(GL_ARRAY_BUFFER, 36, verts, GL_STATIC_DRAW);
    glClearColor(kClear[0] / 255.f, kClear[1] / 255.f, kClear[2] / 255.f, 1.f);
    glClear(GL_COLOR_BUFFER_BIT);
    glDrawArrays(GL_TRIANGLES, 0, 3);
    uint8_t bottom_up[kPixels * 4];
    glReadPixels(0, 0, kDim, kDim, GL_RGBA, GL_UNSIGNED_BYTE, bottom_up);
    gl_ok("draw");
    for (int y = 0; y < kDim; y++) {
        memcpy(top_down + (kDim - 1 - y) * kRow, bottom_up + y * kRow, kRow);
    }
}

static void render_metal(const uint8_t *verts, const float *matrix, int front_cw, uint8_t *rgba) {
    @autoreleasepool {
        id<MTLBuffer> vertices =
            [g_device newBufferWithBytes:verts length:36 options:MTLResourceStorageModeShared];
        id<MTLBuffer> uniforms =
            [g_device newBufferWithBytes:matrix length:sizeof(float) * 16 options:MTLResourceStorageModeShared];
        MTLTextureDescriptor *desc =
            [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm
                                                               width:kDim
                                                              height:kDim
                                                           mipmapped:NO];
        desc.usage = MTLTextureUsageRenderTarget;
        desc.storageMode = MTLStorageModePrivate;
        id<MTLTexture> texture = [g_device newTextureWithDescriptor:desc];
        id<MTLBuffer> readback =
            [g_device newBufferWithLength:kPixels * 4 options:MTLResourceStorageModeShared];
        if (vertices == nil || uniforms == nil || texture == nil || readback == nil) {
            die("metal resource failed");
        }
        id<MTLCommandBuffer> buffer = [g_queue commandBuffer];
        MTLRenderPassDescriptor *pass = [MTLRenderPassDescriptor renderPassDescriptor];
        pass.colorAttachments[0].texture = texture;
        pass.colorAttachments[0].loadAction = MTLLoadActionClear;
        pass.colorAttachments[0].storeAction = MTLStoreActionStore;
        pass.colorAttachments[0].clearColor =
            MTLClearColorMake(kClear[0] / 255.0, kClear[1] / 255.0, kClear[2] / 255.0, 1);
        id<MTLRenderCommandEncoder> encoder = [buffer renderCommandEncoderWithDescriptor:pass];
        if (encoder == nil || buffer == nil) {
            die("metal encoder failed");
        }
        [encoder setRenderPipelineState:g_pipeline];
        [encoder setViewport:(MTLViewport){0, 0, kDim, kDim, 0, 1}];
        [encoder setCullMode:MTLCullModeBack];
        [encoder setFrontFacingWinding:front_cw ? MTLWindingClockwise : MTLWindingCounterClockwise];
        [encoder setVertexBuffer:uniforms offset:0 atIndex:0];
        [encoder setVertexBuffer:vertices offset:0 atIndex:1];
        [encoder drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
        [encoder endEncoding];
        id<MTLBlitCommandEncoder> blit = [buffer blitCommandEncoder];
        if (blit == nil) {
            die("metal blit failed");
        }
        [blit copyFromTexture:texture sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0, 0, 0)
                   sourceSize:MTLSizeMake(kDim, kDim, 1) toBuffer:readback destinationOffset:0
          destinationBytesPerRow:kRow destinationBytesPerImage:kPixels * 4];
        [blit endEncoding];
        [buffer commit];
        [buffer waitUntilCompleted];
        if (buffer.status != MTLCommandBufferStatusCompleted) {
            die("metal command buffer failed");
        }
        const uint8_t *bgra = readback.contents;
        for (int i = 0; i < kPixels; i++) {
            rgba[i * 4 + 0] = bgra[i * 4 + 2];
            rgba[i * 4 + 1] = bgra[i * 4 + 1];
            rgba[i * 4 + 2] = bgra[i * 4 + 0];
            rgba[i * 4 + 3] = bgra[i * 4 + 3];
        }
    }
}

static int pixel_is(const uint8_t *image, int index, const uint8_t *rgba) {
    return memcmp(image + index * 4, rgba, 4) == 0;
}

static int count_matching(const uint8_t *image, const uint8_t *rgba) {
    int count = 0;
    for (int i = 0; i < kPixels; i++) {
        count += pixel_is(image, i, rgba);
    }
    return count;
}

static void report_mismatch(const char *label, const uint8_t *expected, const uint8_t *actual) {
    int diffs = 0;
    int first = -1;
    for (int i = 0; i < kPixels; i++) {
        if (memcmp(expected + i * 4, actual + i * 4, 4) != 0) {
            if (first < 0) {
                first = i;
            }
            diffs++;
        }
    }
    if (diffs == 0) {
        return;
    }
    const uint8_t *want = expected + first * 4;
    const uint8_t *got = actual + first * 4;
    int channel = 0;
    for (int c = 0; c < 4; c++) {
        if (want[c] != got[c]) {
            channel = c;
            break;
        }
    }
    int want_clear = memcmp(want, kClear, 4) == 0;
    int got_clear = memcmp(got, kClear, 4) == 0;
    int want_color = memcmp(want, kFill, 4) == 0;
    int got_color = memcmp(got, kFill, 4) == 0;
    const char *kind = ((want_clear && got_color) || (got_clear && want_color)) ? "coverage" : "color";
    fprintf(stderr,
            "%s: first difference (%d, %d, channel %d) expected %u,%u,%u,%u actual %u,%u,%u,%u, "
            "differing pixels %d, %s\n",
            label, first % kDim, first / kDim, channel, want[0], want[1], want[2], want[3], got[0], got[1],
            got[2], got[3], diffs, kind);
    exit(1);
}

static void require_both(const char *label, const uint8_t *image) {
    if (count_matching(image, kFill) == 0 || count_matching(image, kClear) == 0) {
        fprintf(stderr, "%s: expected both the clear color and the triangle color\n", label);
        exit(1);
    }
}

static void require_clear(const char *label, const uint8_t *image) {
    if (count_matching(image, kClear) != kPixels) {
        fprintf(stderr, "%s: expected a fully cleared target\n", label);
        exit(1);
    }
}

static void put_vert(uint8_t *dst, float x, float y) {
    memcpy(dst, &x, 4);
    memcpy(dst + 4, &y, 4);
    memcpy(dst + 8, kFill, 4);
}

static void fill_triangle(uint8_t *verts, int clockwise) {
    float xy[3][2] = {{6.f, 6.f}, {26.5f, 6.f}, {6.f, 26.5f}};
    int order[3] = {0, 1, 2};
    if (clockwise) {
        order[1] = 2;
        order[2] = 1;
    }
    for (int i = 0; i < 3; i++) {
        put_vert(verts + i * 12, xy[order[i]][0], xy[order[i]][1]);
    }
}

static void ortho(float m[16], float clip_z) {
    memset(m, 0, sizeof(float) * 16);
    m[0] = 2.f / kDim;
    m[5] = 2.f / kDim;
    m[10] = 1.f;
    m[12] = -1.f;
    m[13] = -1.f;
    m[14] = clip_z;
    m[15] = 1.f;
}

// Column vectors: new Z row = (Z + W) / 2, so clip z=-0.5, w=1 becomes 0.25.
static void remap_clip_z(float m[16]) {
    for (int col = 0; col < 4; col++) {
        int z = col * 4 + 2;
        int w = col * 4 + 3;
        m[z] = 0.5f * m[z] + 0.5f * m[w];
    }
}

static void match_draw(const char *label, const uint8_t *verts, const float *matrix, int front_cw,
                       int expect_triangle) {
    uint8_t gl_image[kPixels * 4];
    uint8_t metal_image[kPixels * 4];
    render_gl(verts, matrix, front_cw, gl_image);
    render_metal(verts, matrix, front_cw, metal_image);
    report_mismatch(label, gl_image, metal_image);
    if (expect_triangle) {
        require_both(label, gl_image);
    } else {
        require_clear(label, gl_image);
    }
}

static int compare_cap(const uint8_t *expected, const uint8_t *actual, int pixels, int max_delta, int *coverage,
                       int *worst) {
    *coverage = 0;
    *worst = 0;
    for (int i = 0; i < pixels; i++) {
        int a_clear = memcmp(expected + i * 4, kClear, 4) == 0;
        int b_clear = memcmp(actual + i * 4, kClear, 4) == 0;
        if (a_clear != b_clear) {
            (*coverage)++;
            continue;
        }
        for (int c = 0; c < 4; c++) {
            int delta = (int)expected[i * 4 + c] - (int)actual[i * 4 + c];
            if (delta < 0) {
                delta = -delta;
            }
            if (delta > *worst) {
                *worst = delta;
            }
        }
    }
    return *coverage == 0 && *worst <= max_delta;
}

static void flip_rgba(const uint8_t *bottom_up, uint8_t *top_down, int width, int height) {
    int row = width * 4;
    for (int y = 0; y < height; y++) {
        memcpy(top_down + (height - 1 - y) * row, bottom_up + y * row, (size_t)row);
    }
}

static void init_indexed_gl(NSString *vert, NSString *frag, int width, int height, GLuint pos_loc, GLuint color_loc) {
    CGLPixelFormatAttribute attrs[] = {
        kCGLPFAOpenGLProfile, (CGLPixelFormatAttribute)kCGLOGLPVersion_Legacy,
        kCGLPFAAccelerated,
        kCGLPFAColorSize, (CGLPixelFormatAttribute)24,
        kCGLPFAAlphaSize, (CGLPixelFormatAttribute)8,
        0
    };
    CGLPixelFormatObj format = NULL;
    GLint count = 0;
    if (CGLChoosePixelFormat(attrs, &format, &count) != kCGLNoError || format == NULL) {
        die("no legacy opengl pixel format");
    }
    CGLContextObj context = NULL;
    if (CGLCreateContext(format, NULL, &context) != kCGLNoError || context == NULL) {
        die("no legacy opengl context");
    }
    CGLDestroyPixelFormat(format);
    if (CGLSetCurrentContext(context) != kCGLNoError) {
        die("no current GL context");
    }
    while (glGetError() != GL_NO_ERROR) {
    }
    GLuint color = 0;
    GLuint fbo = 0;
    glGenTextures(1, &color);
    glBindTexture(GL_TEXTURE_2D, color);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, width, height, 0, GL_RGBA, GL_UNSIGNED_BYTE, NULL);
    glGenFramebuffers(1, &fbo);
    glBindFramebuffer(GL_FRAMEBUFFER, fbo);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, color, 0);
    if (glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE) {
        die("fbo incomplete");
    }
    GLuint vao = 0;
    glGenVertexArraysAPPLE(1, &vao);
    glBindVertexArrayAPPLE(vao);
    if (vao == 0) {
        die("legacy context did not provide a vertex array object");
    }
    glViewport(0, 0, width, height);
    glDisable(GL_DEPTH_TEST);
    glDisable(GL_BLEND);
    glDisable(GL_STENCIL_TEST);
    glDisable(GL_SCISSOR_TEST);
    glDisable(GL_DITHER);
    glEnable(GL_CULL_FACE);
    glCullFace(GL_BACK);
    gl_ok("indexed framebuffer");

    GLuint vs = compile_shader(GL_VERTEX_SHADER, vert);
    GLuint fs = compile_shader(GL_FRAGMENT_SHADER, frag);
    g_program = glCreateProgram();
    glBindAttribLocation(g_program, pos_loc, "in_POSITION");
    glBindAttribLocation(g_program, color_loc, "in_PDX_COLOR");
    glAttachShader(g_program, vs);
    glAttachShader(g_program, fs);
    glLinkProgram(g_program);
    GLint linked = 0;
    glGetProgramiv(g_program, GL_LINK_STATUS, &linked);
    if (!linked) {
        char log[4096];
        glGetProgramInfoLog(g_program, (GLsizei)sizeof log, NULL, log);
        fprintf(stderr, "%s\n", log);
        exit(1);
    }
    glUseProgram(g_program);
    g_uniform = glGetUniformLocation(g_program, "UNIFORM0");
    if (g_uniform < 0 || glGetAttribLocation(g_program, "in_POSITION") != (GLint)pos_loc ||
        glGetAttribLocation(g_program, "in_PDX_COLOR") != (GLint)color_loc) {
        die("linked attribute locations do not match the fixture");
    }
    glGenBuffers(1, &g_vbo);
    glBindBuffer(GL_ARRAY_BUFFER, g_vbo);
    glVertexAttribPointer(pos_loc, 2, GL_FLOAT, GL_FALSE, 12, (void *)0);
    glEnableVertexAttribArray(pos_loc);
    glVertexAttribPointer(color_loc, 4, GL_UNSIGNED_BYTE, GL_TRUE, 12, (void *)8);
    glEnableVertexAttribArray(color_loc);
    // This legacy driver drops a draw unless array 0 is enabled, even with a VAO.
    // The shader locations stay the captured ones; array 0 is not a shader input.
    if (pos_loc != 0 && color_loc != 0) {
        glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, 12, (void *)0);
        glEnableVertexAttribArray(0);
    }
    gl_ok("indexed program");
}

static void read_top_down(int width, int height, uint8_t *top_down) {
    int bytes = width * height * 4;
    uint8_t *bottom = malloc((size_t)bytes);
    if (bottom == NULL) {
        die("readback alloc failed");
    }
    glReadPixels(0, 0, width, height, GL_RGBA, GL_UNSIGNED_BYTE, bottom);
    gl_ok("read");
    flip_rgba(bottom, top_down, width, height);
    free(bottom);
}

static void render_gl_indexed(const uint8_t *verts, int vertex_bytes, const void *index_bytes, int index_bytes_len,
                              GLenum index_type, int count, uintptr_t index_offset, const float *matrix, int front_cw,
                              int dither, int width, int height, int draw, uint8_t *top_down) {
    glFrontFace(front_cw ? GL_CW : GL_CCW);
    if (dither) {
        glEnable(GL_DITHER);
    } else {
        glDisable(GL_DITHER);
    }
    glUniform4fv(g_uniform, 4, matrix);
    glBindBuffer(GL_ARRAY_BUFFER, g_vbo);
    glBufferData(GL_ARRAY_BUFFER, vertex_bytes, verts, GL_STATIC_DRAW);
    GLuint ibo = 0;
    glGenBuffers(1, &ibo);
    glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, ibo);
    glBufferData(GL_ELEMENT_ARRAY_BUFFER, index_bytes_len, index_bytes, GL_STATIC_DRAW);
    glClearColor(kClear[0] / 255.f, kClear[1] / 255.f, kClear[2] / 255.f, 1.f);
    glClear(GL_COLOR_BUFFER_BIT);
    if (draw) {
        glDrawElements(GL_TRIANGLES, count, index_type, (void *)index_offset);
    }
    read_top_down(width, height, top_down);
    glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, 0);
    glDeleteBuffers(1, &ibo);
}

static void render_metal_indexed(const uint8_t *verts, int vertex_bytes, const void *index_bytes, int index_bytes_len,
                                 int draw_count, MTLIndexType index_type, NSUInteger index_offset, const float *matrix,
                                 int front_cw, int width, int height, uint8_t *rgba) {
    @autoreleasepool {
        int pixels = width * height;
        int row = width * 4;
        id<MTLBuffer> vertices =
            [g_device newBufferWithBytes:verts length:(NSUInteger)vertex_bytes options:MTLResourceStorageModeShared];
        id<MTLBuffer> indices =
            [g_device newBufferWithBytes:index_bytes length:(NSUInteger)index_bytes_len options:MTLResourceStorageModeShared];
        id<MTLBuffer> uniforms =
            [g_device newBufferWithBytes:matrix length:sizeof(float) * 16 options:MTLResourceStorageModeShared];
        MTLTextureDescriptor *desc =
            [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm
                                                               width:(NSUInteger)width
                                                              height:(NSUInteger)height
                                                           mipmapped:NO];
        desc.usage = MTLTextureUsageRenderTarget;
        desc.storageMode = MTLStorageModePrivate;
        id<MTLTexture> texture = [g_device newTextureWithDescriptor:desc];
        id<MTLBuffer> readback = [g_device newBufferWithLength:(NSUInteger)pixels * 4 options:MTLResourceStorageModeShared];
        if (vertices == nil || indices == nil || uniforms == nil || texture == nil || readback == nil) {
            die("metal resource failed");
        }
        id<MTLCommandBuffer> buffer = [g_queue commandBuffer];
        MTLRenderPassDescriptor *pass = [MTLRenderPassDescriptor renderPassDescriptor];
        pass.colorAttachments[0].texture = texture;
        pass.colorAttachments[0].loadAction = MTLLoadActionClear;
        pass.colorAttachments[0].storeAction = MTLStoreActionStore;
        pass.colorAttachments[0].clearColor =
            MTLClearColorMake(kClear[0] / 255.0, kClear[1] / 255.0, kClear[2] / 255.0, 1);
        id<MTLRenderCommandEncoder> encoder = [buffer renderCommandEncoderWithDescriptor:pass];
        if (encoder == nil || buffer == nil) {
            die("metal encoder failed");
        }
        [encoder setRenderPipelineState:g_pipeline];
        [encoder setViewport:(MTLViewport){0, 0, width, height, 0, 1}];
        [encoder setCullMode:MTLCullModeBack];
        [encoder setFrontFacingWinding:front_cw ? MTLWindingClockwise : MTLWindingCounterClockwise];
        [encoder setVertexBuffer:uniforms offset:0 atIndex:0];
        [encoder setVertexBuffer:vertices offset:0 atIndex:1];
        [encoder drawIndexedPrimitives:MTLPrimitiveTypeTriangle
                            indexCount:(NSUInteger)draw_count
                             indexType:index_type
                           indexBuffer:indices
                     indexBufferOffset:index_offset];
        [encoder endEncoding];
        id<MTLBlitCommandEncoder> blit = [buffer blitCommandEncoder];
        if (blit == nil) {
            die("metal blit failed");
        }
        [blit copyFromTexture:texture sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0, 0, 0)
                   sourceSize:MTLSizeMake((NSUInteger)width, (NSUInteger)height, 1)
                     toBuffer:readback
            destinationOffset:0
          destinationBytesPerRow:(NSUInteger)row
        destinationBytesPerImage:(NSUInteger)pixels * 4];
        [blit endEncoding];
        [buffer commit];
        [buffer waitUntilCompleted];
        if (buffer.status != MTLCommandBufferStatusCompleted) {
            die("metal command buffer failed");
        }
        const uint8_t *bgra = readback.contents;
        for (int i = 0; i < pixels; i++) {
            rgba[i * 4 + 0] = bgra[i * 4 + 2];
            rgba[i * 4 + 1] = bgra[i * 4 + 1];
            rgba[i * 4 + 2] = bgra[i * 4 + 0];
            rgba[i * 4 + 3] = bgra[i * 4 + 3];
        }
    }
}

static void require_cap(const char *label, const uint8_t *expected, const uint8_t *actual, int max_delta,
                        int expect_triangle) {
    int coverage = 0;
    int worst = 0;
    if (!compare_cap(expected, actual, kPixels, max_delta, &coverage, &worst)) {
        fprintf(stderr, "%s: coverage %d worst %d\n", label, coverage, worst);
        exit(1);
    }
    if (expect_triangle) {
        require_both(label, expected);
    }
}

static int selftest(const char *vert_path, const char *frag_path, const char *msl_vert_path, const char *msl_frag_path) {
    @autoreleasepool {
        NSString *gl_vert = read_text(vert_path);
        NSString *gl_frag = read_text(frag_path);
        NSString *msl_vert = read_text(msl_vert_path);
        NSString *msl_frag = read_text(msl_frag_path);
        init_indexed_gl(gl_vert, gl_frag, kDim, kDim, 3, 5);
        init_metal(msl_vert, msl_frag);
        uint8_t verts[36];
        uint16_t shorts[3] = {0, 1, 2};
        uint32_t ints[3] = {0, 1, 2};
        fill_triangle(verts, 1);
        float matrix[16];
        ortho(matrix, 0.f);
        uint8_t gl_image[kPixels * 4];
        uint8_t metal_image[kPixels * 4];
        render_gl_indexed(verts, 36, shorts, sizeof shorts, GL_UNSIGNED_SHORT, 3, 0, matrix, 1, 0, kDim, kDim, 1,
                          gl_image);
        float metal_matrix[16];
        memcpy(metal_matrix, matrix, sizeof metal_matrix);
        remap_clip_z(metal_matrix);
        render_metal_indexed(verts, 36, shorts, (int)sizeof shorts, 3, MTLIndexTypeUInt16, 0, metal_matrix, 1, kDim, kDim,
                             metal_image);
        require_cap("indexed-cw", gl_image, metal_image, 0, 1);

        fill_triangle(verts, 0);
        render_gl_indexed(verts, 36, ints, (int)sizeof ints, GL_UNSIGNED_INT, 3, 0, matrix, 0, 0, kDim, kDim, 1,
                          gl_image);
        render_metal_indexed(verts, 36, ints, (int)sizeof ints, 3, MTLIndexTypeUInt32, 0, metal_matrix, 0, kDim, kDim,
                             metal_image);
        require_cap("indexed-ccw", gl_image, metal_image, 0, 1);

        fill_triangle(verts, 1);
        render_gl_indexed(verts, 36, shorts, sizeof shorts, GL_UNSIGNED_SHORT, 3, 0, matrix, 1, 1, kDim, kDim, 1,
                          gl_image);
        render_metal_indexed(verts, 36, shorts, (int)sizeof shorts, 3, MTLIndexTypeUInt16, 0, metal_matrix, 1, kDim, kDim,
                             metal_image);
        require_cap("indexed-dither", gl_image, metal_image, 1, 1);

        float negative[16];
        ortho(negative, -0.5f);
        render_gl_indexed(verts, 36, shorts, sizeof shorts, GL_UNSIGNED_SHORT, 3, 0, negative, 1, 0, kDim, kDim, 1,
                          gl_image);
        require_both("indexed-clip-gl", gl_image);
        render_metal_indexed(verts, 36, shorts, (int)sizeof shorts, 3, MTLIndexTypeUInt16, 0, negative, 1, kDim, kDim,
                             metal_image);
        require_clear("indexed-clip-metal", metal_image);
        remap_clip_z(negative);
        render_metal_indexed(verts, 36, shorts, (int)sizeof shorts, 3, MTLIndexTypeUInt16, 0, negative, 1, kDim, kDim,
                             metal_image);
        require_cap("indexed-clip", gl_image, metal_image, 0, 1);
    }
    printf("ok\n");
    return 0;
}

static NSData *file_data(NSString *path) {
    NSData *data = [NSData dataWithContentsOfFile:path];
    if (data == nil) {
        fprintf(stderr, "cannot read %s\n", path.UTF8String);
        exit(1);
    }
    return data;
}

static void sha256_hex(NSData *data, char out[65]) {
    unsigned char dig[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256(data.bytes, (CC_LONG)data.length, dig);
    for (int i = 0; i < CC_SHA256_DIGEST_LENGTH; i++) {
        sprintf(out + i * 2, "%02x", dig[i]);
    }
    out[64] = 0;
}

static long long json_int(NSDictionary *object, NSString *key) {
    id value = object[key];
    if (![value isKindOfClass:[NSNumber class]]) {
        fprintf(stderr, "fixture missing %s\n", key.UTF8String);
        exit(1);
    }
    return [(NSNumber *)value longLongValue];
}

static int json_bool(NSDictionary *object, NSString *key) {
    id value = object[key];
    if (![value isKindOfClass:[NSNumber class]]) {
        fprintf(stderr, "fixture missing %s\n", key.UTF8String);
        exit(1);
    }
    return [(NSNumber *)value boolValue];
}

static NSString *json_string(NSDictionary *object, NSString *key) {
    id value = object[key];
    if (![value isKindOfClass:[NSString class]]) {
        fprintf(stderr, "fixture missing %s\n", key.UTF8String);
        exit(1);
    }
    return value;
}

static int range_ok(uint64_t offset, uint64_t count, uint64_t index_size, uint64_t buffer_bytes) {
    if (count == 0 || offset > buffer_bytes || count > UINT64_MAX / index_size) {
        return 0;
    }
    return count * index_size <= buffer_bytes - offset;
}

static int fixture_main(const char *dir) {
    @autoreleasepool {
        NSString *root = [NSString stringWithUTF8String:dir];
        NSData *json_data = file_data([root stringByAppendingPathComponent:@"fixture.json"]);
        NSDictionary *fixture = [NSJSONSerialization JSONObjectWithData:json_data options:0 error:nil];
        if (![fixture isKindOfClass:[NSDictionary class]]) {
            die("fixture json is not an object");
        }
        if (json_int(fixture, @"schema_version") != 1 || json_int(fixture, @"capture_version") != 1) {
            die("fixture schema is not version 1");
        }
        if (![json_string(fixture, @"executable_sha256") isEqualToString:
                @"b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d"]) {
            die("fixture executable hash is not the pinned build");
        }
        if (![json_string(fixture, @"primitive") isEqualToString:@"GL_TRIANGLES"] ||
            json_int(fixture, @"stride") != 12 || !json_bool(fixture, @"cull") ||
            ![json_string(fixture, @"cull_face") isEqualToString:@"GL_BACK"] ||
            ![json_string(fixture, @"front_face") isEqualToString:@"GL_CW"] ||
            json_bool(fixture, @"depth_test") || json_bool(fixture, @"blend") || json_bool(fixture, @"stencil_test") ||
            json_bool(fixture, @"scissor_test") || json_bool(fixture, @"alpha_test") ||
            json_bool(fixture, @"color_logic_op") || json_bool(fixture, @"framebuffer_srgb") ||
            json_bool(fixture, @"polygon_smooth") || json_bool(fixture, @"clip_planes") ||
            json_bool(fixture, @"primitive_restart") || json_int(fixture, @"samples") != 0 ||
            ![json_string(fixture, @"polygon_mode") isEqualToString:@"GL_FILL"] ||
            ![json_string(fixture, @"gl_format") isEqualToString:@"RGBA8"] ||
            ![json_string(fixture, @"metal_format") isEqualToString:@"BGRA8Unorm"] ||
            ![json_string(fixture, @"comparison") isEqualToString:@"RGBA_top_down"]) {
            die("fixture state is outside the replay contract");
        }
        NSDictionary *acceptance = fixture[@"acceptance"];
        if (json_int(acceptance, @"coverage_mismatches") != 0 || json_int(acceptance, @"max_channel_delta") != 1) {
            die("fixture acceptance cap is not the frozen contract");
        }
        NSArray *mask = fixture[@"color_mask"];
        NSArray *clear = fixture[@"clear"];
        if (![mask isKindOfClass:[NSArray class]] || mask.count != 4 || ![clear isKindOfClass:[NSArray class]] ||
            clear.count != 4) {
            die("fixture mask or clear is incomplete");
        }
        for (NSUInteger i = 0; i < 4; i++) {
            if (![mask[i] boolValue] || [clear[i] intValue] != kClear[i]) {
                die("fixture mask or clear is outside the replay contract");
            }
        }
        NSDictionary *viewport = fixture[@"viewport"];
        long long width = json_int(viewport, @"width");
        long long height = json_int(viewport, @"height");
        if (json_int(viewport, @"x") != 0 || json_int(viewport, @"y") != 0 || width < 1 || width > 2048 || height < 1 ||
            height > 2048) {
            die("fixture viewport is outside the replay contract");
        }
        NSDictionary *hashes = fixture[@"hashes"];
        NSData *vertices = file_data([root stringByAppendingPathComponent:@"vertices.bin"]);
        NSData *indices = file_data([root stringByAppendingPathComponent:@"indices.bin"]);
        NSData *uniform = file_data([root stringByAppendingPathComponent:@"uniform0.bin"]);
        NSData *vert = file_data([root stringByAppendingPathComponent:@"draw.vert.glsl"]);
        NSData *frag = file_data([root stringByAppendingPathComponent:@"draw.frag.glsl"]);
        NSData *files[] = {vertices, indices, uniform, vert, frag};
        NSString *names[] = {@"vertices.bin", @"indices.bin", @"uniform0.bin", @"draw.vert.glsl", @"draw.frag.glsl"};
        if (vertices.length != (NSUInteger)json_int(fixture, @"vertex_bytes") ||
            indices.length != (NSUInteger)json_int(fixture, @"index_bytes") || uniform.length != 64) {
            die("fixture byte lengths do not match");
        }
        for (int i = 0; i < 5; i++) {
            char hex[65];
            sha256_hex(files[i], hex);
            if (![json_string(hashes, names[i]) isEqualToString:[NSString stringWithUTF8String:hex]]) {
                die("fixture hash mismatch");
            }
        }
        NSString *index_name = json_string(fixture, @"index_type");
        GLenum index_type = 0;
        MTLIndexType metal_index = MTLIndexTypeUInt16;
        uint64_t index_size = 0;
        if ([index_name isEqualToString:@"GL_UNSIGNED_SHORT"]) {
            index_type = GL_UNSIGNED_SHORT;
            metal_index = MTLIndexTypeUInt16;
            index_size = 2;
        } else if ([index_name isEqualToString:@"GL_UNSIGNED_INT"]) {
            index_type = GL_UNSIGNED_INT;
            metal_index = MTLIndexTypeUInt32;
            index_size = 4;
        } else {
            die("fixture index type is unsupported");
        }
        long long count = json_int(fixture, @"count");
        long long offset = json_int(fixture, @"index_offset");
        if (count < 3 || (count % 3) != 0 || offset < 0 ||
            !range_ok((uint64_t)offset, (uint64_t)count, index_size, indices.length)) {
            die("fixture index range does not fit");
        }
        float matrix[16];
        memcpy(matrix, uniform.bytes, sizeof matrix);
        for (int i = 0; i < 16; i++) {
            if (!isfinite(matrix[i])) {
                die("fixture matrix is nonfinite");
            }
        }
        NSArray *attributes = fixture[@"attributes"];
        if (![attributes isKindOfClass:[NSArray class]] || attributes.count != 2) {
            die("fixture attributes are incomplete");
        }
        long long pos_loc = -1;
        long long color_loc = -1;
        for (NSDictionary *attr in attributes) {
            NSString *name = json_string(attr, @"name");
            if ([name isEqualToString:@"in_POSITION"] &&
                [json_string(attr, @"shader_type") isEqualToString:@"GL_FLOAT_VEC2"] && json_int(attr, @"size") == 2 &&
                [json_string(attr, @"component_type") isEqualToString:@"GL_FLOAT"] && !json_bool(attr, @"normalized") &&
                json_int(attr, @"offset") == 0) {
                pos_loc = json_int(attr, @"location");
            } else if ([name isEqualToString:@"in_PDX_COLOR"] &&
                       [json_string(attr, @"shader_type") isEqualToString:@"GL_FLOAT_VEC4"] &&
                       json_int(attr, @"size") == 4 &&
                       [json_string(attr, @"component_type") isEqualToString:@"GL_UNSIGNED_BYTE"] &&
                       json_bool(attr, @"normalized") && json_int(attr, @"offset") == 8) {
                color_loc = json_int(attr, @"location");
            }
        }
        if (pos_loc < 0 || color_loc < 0 || pos_loc == color_loc || pos_loc > 15 || color_loc > 15) {
            die("fixture attribute locations are unusable");
        }
        int pixels = (int)(width * height);
        uint8_t *clear_image = calloc((size_t)pixels, 4);
        uint8_t *reference = calloc((size_t)pixels, 4);
        uint8_t *metal_image = calloc((size_t)pixels, 4);
        if (clear_image == NULL || reference == NULL || metal_image == NULL) {
            die("image alloc failed");
        }
        NSString *vert_text = [[NSString alloc] initWithData:vert encoding:NSUTF8StringEncoding];
        NSString *frag_text = [[NSString alloc] initWithData:frag encoding:NSUTF8StringEncoding];
        if (vert_text == nil || frag_text == nil) {
            die("captured glsl is not utf-8");
        }
        const char *msl_vert_path = getenv("EU4_B2_COLOR_MSL_VERT");
        const char *msl_frag_path = getenv("EU4_B2_COLOR_MSL_FRAG");
        if (msl_vert_path == NULL || msl_frag_path == NULL) {
            die("fixture replay needs EU4_B2_COLOR_MSL_VERT and EU4_B2_COLOR_MSL_FRAG");
        }
        init_indexed_gl(vert_text, frag_text, (int)width, (int)height, (GLuint)pos_loc, (GLuint)color_loc);
        init_metal(read_text(msl_vert_path), read_text(msl_frag_path));
        int dither = json_bool(fixture, @"dither");
        render_gl_indexed(vertices.bytes, (int)vertices.length, indices.bytes, (int)indices.length, index_type,
                          (int)count, (uintptr_t)offset, matrix, 1, dither, (int)width, (int)height, 0, clear_image);
        render_gl_indexed(vertices.bytes, (int)vertices.length, indices.bytes, (int)indices.length, index_type,
                          (int)count, (uintptr_t)offset, matrix, 1, dither, (int)width, (int)height, 1, reference);
        if (memcmp(clear_image, reference, (size_t)pixels * 4) == 0) {
            die("gl replay matches the clear image");
        }
        NSData *clear_data = [NSData dataWithBytes:clear_image length:(NSUInteger)pixels * 4];
        NSData *reference_data = [NSData dataWithBytes:reference length:(NSUInteger)pixels * 4];
        if (![clear_data writeToFile:[root stringByAppendingPathComponent:@"clear.rgba"] atomically:YES] ||
            ![reference_data writeToFile:[root stringByAppendingPathComponent:@"reference.rgba"] atomically:YES]) {
            die("could not write replay images");
        }
        float metal_matrix[16];
        memcpy(metal_matrix, matrix, sizeof metal_matrix);
        remap_clip_z(metal_matrix);
        render_metal_indexed(vertices.bytes, (int)vertices.length, indices.bytes, (int)indices.length, (int)count,
                             metal_index, (NSUInteger)offset, metal_matrix, 1, (int)width, (int)height, metal_image);
        int coverage = 0;
        int worst = 0;
        if (!compare_cap(reference, metal_image, pixels, 1, &coverage, &worst)) {
            fprintf(stderr, "fixture: coverage %d worst %d\n", coverage, worst);
            exit(1);
        }
        free(clear_image);
        free(reference);
        free(metal_image);
        printf("ok worst=%d coverage=%d\n", worst, coverage);
    }
    return 0;
}

int main(int argc, char **argv) {
    if (argc == 6 && strcmp(argv[1], "--selftest") == 0) {
        return selftest(argv[2], argv[3], argv[4], argv[5]);
    }
    if (argc == 3 && strcmp(argv[1], "--fixture") == 0) {
        return fixture_main(argv[2]);
    }
    if (argc != 5) {
        die("usage: metal_color_ref_host color.vert color.frag color.vert.metal color.frag.metal");
    }
    @autoreleasepool {
        NSString *gl_vert = read_text(argv[1]);
        NSString *gl_frag = read_text(argv[2]);
        NSString *msl_vert = read_text(argv[3]);
        NSString *msl_frag = read_text(argv[4]);
        init_gl(gl_vert, gl_frag);
        init_metal(msl_vert, msl_frag);

        uint8_t ccw[36];
        uint8_t cw[36];
        fill_triangle(ccw, 0);
        fill_triangle(cw, 1);
        float matrix[16];
        ortho(matrix, 0.f);
        match_draw("ccw-front", ccw, matrix, 0, 1);
        match_draw("ccw-back", cw, matrix, 0, 0);
        match_draw("cw-front", cw, matrix, 1, 1);
        match_draw("cw-back", ccw, matrix, 1, 0);

        float negative_z[16];
        ortho(negative_z, -0.5f);
        uint8_t gl_image[kPixels * 4];
        uint8_t metal_image[kPixels * 4];
        render_gl(ccw, negative_z, 0, gl_image);
        require_both("clip-z-gl", gl_image);
        render_metal(ccw, negative_z, 0, metal_image);
        require_clear("clip-z-metal", metal_image);
        float corrected[16];
        memcpy(corrected, negative_z, sizeof corrected);
        remap_clip_z(corrected);
        render_metal(ccw, corrected, 0, metal_image);
        report_mismatch("clip-z-corrected", gl_image, metal_image);
    }
    printf("ok\n");
    return 0;
}
