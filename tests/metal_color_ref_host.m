#define GL_SILENCE_DEPRECATION
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <OpenGL/OpenGL.h>
#import <OpenGL/gl.h>
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
    CGLSetCurrentContext(context);
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

int main(int argc, char **argv) {
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
