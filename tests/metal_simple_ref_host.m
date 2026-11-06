#define GL_SILENCE_DEPRECATION
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <OpenGL/OpenGL.h>
#import <OpenGL/gl.h>
#import <stdio.h>
#import <stdlib.h>
#import <string.h>
#import <math.h>

enum { kDim = 32, kPixels = kDim * kDim, kTol = 2 };

static void die(const char *message) {
    fprintf(stderr, "%s\n", message);
    exit(1);
}

static NSString *read_text(const char *path) {
    NSError *error = nil;
    NSString *text = [NSString stringWithContentsOfFile:@(path)
                                               encoding:NSUTF8StringEncoding
                                                  error:&error];
    if (text == nil) {
        die(path);
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

/* Fullscreen triangle strip in clip space with UV and vertex color. */
static const float kVerts[] = {
    /* pos xyzw, uv, color rgba */
    -1, -1, 0, 1,  0, 0,  1, 0, 0, 1,
     1, -1, 0, 1,  1, 0,  1, 0, 0, 1,
    -1,  1, 0, 1,  0, 1,  1, 0, 0, 1,
     1,  1, 0, 1,  1, 1,  1, 0, 0, 1,
};

static void identity_matrix(float out[16]) {
    memset(out, 0, 16 * sizeof(float));
    out[0] = out[5] = out[10] = out[15] = 1.f;
}

static void draw_gl(NSString *vert, NSString *frag, uint8_t *out_rgba) {
    CGLPixelFormatAttribute attrs[] = {
        kCGLPFAOpenGLProfile, (CGLPixelFormatAttribute)kCGLOGLPVersion_Legacy,
        kCGLPFAAccelerated, kCGLPFAColorSize, (CGLPixelFormatAttribute)24,
        kCGLPFAAlphaSize, (CGLPixelFormatAttribute)8, 0};
    CGLPixelFormatObj format = NULL;
    GLint count = 0;
    if (CGLChoosePixelFormat(attrs, &format, &count) != kCGLNoError || !format) {
        die("no gl pixel format");
    }
    CGLContextObj context = NULL;
    if (CGLCreateContext(format, NULL, &context) != kCGLNoError || !context) {
        die("no gl context");
    }
    CGLDestroyPixelFormat(format);
    CGLSetCurrentContext(context);

    GLuint color = 0, fbo = 0, tex = 0, vbo = 0;
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
    glEnable(GL_BLEND);
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA);

    uint8_t texels[4] = {200, 100, 50, 255};
    glGenTextures(1, &tex);
    glActiveTexture(GL_TEXTURE0);
    glBindTexture(GL_TEXTURE_2D, tex);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_REPEAT);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_REPEAT);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, 1, 1, 0, GL_RGBA, GL_UNSIGNED_BYTE, texels);

    GLuint vs = compile_shader(GL_VERTEX_SHADER, vert);
    GLuint fs = compile_shader(GL_FRAGMENT_SHADER, frag);
    GLuint program = glCreateProgram();
    glAttachShader(program, vs);
    glAttachShader(program, fs);
    glBindAttribLocation(program, 0, "in_POSITION");
    glBindAttribLocation(program, 1, "in_TEXCOORD0");
    glBindAttribLocation(program, 2, "in_PDX_COLOR");
    glLinkProgram(program);
    GLint linked = 0;
    glGetProgramiv(program, GL_LINK_STATUS, &linked);
    if (!linked) {
        die("gl link failed");
    }
    glUseProgram(program);
    float matrix[16];
    identity_matrix(matrix);
    GLint loc = glGetUniformLocation(program, "UNIFORM0");
    glUniform4fv(loc, 4, matrix);
    glUniform1i(glGetUniformLocation(program, "SimpleTexture"), 0);

    glGenBuffers(1, &vbo);
    glBindBuffer(GL_ARRAY_BUFFER, vbo);
    glBufferData(GL_ARRAY_BUFFER, sizeof kVerts, kVerts, GL_STATIC_DRAW);
    glEnableVertexAttribArray(0);
    glVertexAttribPointer(0, 4, GL_FLOAT, GL_FALSE, 40, (void *)0);
    glEnableVertexAttribArray(1);
    glVertexAttribPointer(1, 2, GL_FLOAT, GL_FALSE, 40, (void *)16);
    glEnableVertexAttribArray(2);
    glVertexAttribPointer(2, 4, GL_FLOAT, GL_FALSE, 40, (void *)24);

    glClearColor(0, 0, 0, 1);
    glClear(GL_COLOR_BUFFER_BIT);
    glDrawArrays(GL_TRIANGLE_STRIP, 0, 4);
    glReadPixels(0, 0, kDim, kDim, GL_RGBA, GL_UNSIGNED_BYTE, out_rgba);
    CGLSetCurrentContext(NULL);
    CGLDestroyContext(context);
}

static void draw_metal(NSString *msl, uint8_t *out_rgba) {
    id<MTLDevice> device = MTLCreateSystemDefaultDevice();
    if (!device) {
        die("no metal device");
    }
    NSError *error = nil;
    id<MTLLibrary> library = [device newLibraryWithSource:msl options:nil error:&error];
    if (!library) {
        fprintf(stderr, "%s\n", error.localizedDescription.UTF8String);
        exit(1);
    }
    MTLVertexDescriptor *vd = [[MTLVertexDescriptor alloc] init];
    vd.attributes[0].format = MTLVertexFormatFloat4;
    vd.attributes[0].offset = 0;
    vd.attributes[0].bufferIndex = 0;
    vd.attributes[1].format = MTLVertexFormatFloat2;
    vd.attributes[1].offset = 16;
    vd.attributes[1].bufferIndex = 0;
    vd.attributes[2].format = MTLVertexFormatFloat4;
    vd.attributes[2].offset = 24;
    vd.attributes[2].bufferIndex = 0;
    vd.layouts[0].stride = 40;
    vd.layouts[0].stepFunction = MTLVertexStepFunctionPerVertex;

    MTLRenderPipelineDescriptor *pd = [[MTLRenderPipelineDescriptor alloc] init];
    pd.vertexFunction = [library newFunctionWithName:@"simple_vs"];
    pd.fragmentFunction = [library newFunctionWithName:@"simple_fs"];
    pd.vertexDescriptor = vd;
    pd.colorAttachments[0].pixelFormat = MTLPixelFormatRGBA8Unorm;
    pd.colorAttachments[0].blendingEnabled = YES;
    pd.colorAttachments[0].sourceRGBBlendFactor = MTLBlendFactorSourceAlpha;
    pd.colorAttachments[0].destinationRGBBlendFactor = MTLBlendFactorOneMinusSourceAlpha;
    pd.colorAttachments[0].sourceAlphaBlendFactor = MTLBlendFactorSourceAlpha;
    pd.colorAttachments[0].destinationAlphaBlendFactor = MTLBlendFactorOneMinusSourceAlpha;
    id<MTLRenderPipelineState> pipeline =
        [device newRenderPipelineStateWithDescriptor:pd error:&error];
    if (!pipeline) {
        fprintf(stderr, "%s\n", error.localizedDescription.UTF8String);
        exit(1);
    }

    MTLTextureDescriptor *td =
        [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm
                                                           width:kDim
                                                          height:kDim
                                                       mipmapped:NO];
    td.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead;
    id<MTLTexture> target = [device newTextureWithDescriptor:td];

    uint8_t texel[4] = {200, 100, 50, 255};
    MTLTextureDescriptor *sd =
        [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm
                                                           width:1
                                                          height:1
                                                       mipmapped:NO];
    sd.usage = MTLTextureUsageShaderRead;
    id<MTLTexture> sample = [device newTextureWithDescriptor:sd];
    [sample replaceRegion:MTLRegionMake2D(0, 0, 1, 1)
              mipmapLevel:0
                withBytes:texel
              bytesPerRow:4];

    id<MTLBuffer> vb =
        [device newBufferWithBytes:kVerts length:sizeof kVerts options:MTLResourceStorageModeShared];
    float matrix[16];
    identity_matrix(matrix);
    id<MTLBuffer> ub =
        [device newBufferWithBytes:matrix length:sizeof matrix options:MTLResourceStorageModeShared];

    id<MTLCommandQueue> queue = [device newCommandQueue];
    id<MTLCommandBuffer> cmd = [queue commandBuffer];
    MTLRenderPassDescriptor *pass = [MTLRenderPassDescriptor renderPassDescriptor];
    pass.colorAttachments[0].texture = target;
    pass.colorAttachments[0].loadAction = MTLLoadActionClear;
    pass.colorAttachments[0].storeAction = MTLStoreActionStore;
    pass.colorAttachments[0].clearColor = MTLClearColorMake(0, 0, 0, 1);
    id<MTLRenderCommandEncoder> enc = [cmd renderCommandEncoderWithDescriptor:pass];
    [enc setRenderPipelineState:pipeline];
    [enc setVertexBuffer:vb offset:0 atIndex:0];
    [enc setVertexBuffer:ub offset:0 atIndex:1];
    [enc setFragmentTexture:sample atIndex:0];
    MTLSamplerDescriptor *samplerDesc = [MTLSamplerDescriptor new];
    samplerDesc.minFilter = MTLSamplerMinMagFilterLinear;
    samplerDesc.magFilter = MTLSamplerMinMagFilterLinear;
    samplerDesc.sAddressMode = MTLSamplerAddressModeRepeat;
    samplerDesc.tAddressMode = MTLSamplerAddressModeRepeat;
    [enc setFragmentSamplerState:[device newSamplerStateWithDescriptor:samplerDesc] atIndex:0];
    [enc drawPrimitives:MTLPrimitiveTypeTriangleStrip vertexStart:0 vertexCount:4];
    [enc endEncoding];
    [cmd commit];
    [cmd waitUntilCompleted];

    [target getBytes:out_rgba
         bytesPerRow:kDim * 4
          fromRegion:MTLRegionMake2D(0, 0, kDim, kDim)
         mipmapLevel:0];
}

int main(int argc, char **argv) {
    if (argc != 4) {
        fprintf(stderr, "usage: %s vert.glsl frag.glsl simple.metal\n", argv[0]);
        return 2;
    }
    uint8_t gl_pixels[kPixels * 4];
    uint8_t mtl_pixels[kPixels * 4];
    draw_gl(read_text(argv[1]), read_text(argv[2]), gl_pixels);
    draw_metal(read_text(argv[3]), mtl_pixels);

    int mismatches = 0;
    int max_delta = 0;
    for (int i = 0; i < kPixels * 4; i++) {
        int d = abs((int)gl_pixels[i] - (int)mtl_pixels[i]);
        if (d > max_delta) {
            max_delta = d;
        }
        if (d > kTol) {
            mismatches++;
        }
    }
    /* Coverage: interior should be non-clear (texture*color ≈ 200,100,50). */
    int lit = 0;
    for (int i = 0; i < kPixels; i++) {
        if (gl_pixels[i * 4] > 100) {
            lit++;
        }
    }
    if (lit < kPixels / 2) {
        fprintf(stderr, "insufficient lit coverage %d\n", lit);
        return 1;
    }
    if (mismatches != 0) {
        fprintf(stderr, "mismatches=%d max_delta=%d tol=%d\n", mismatches, max_delta, kTol);
        return 1;
    }
    printf("ok simple_gl_metal max_delta=%d lit=%d\n", max_delta, lit);
    return 0;
}
