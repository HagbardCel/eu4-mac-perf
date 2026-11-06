#define GL_SILENCE_DEPRECATION
#import <OpenGL/OpenGL.h>
#import <OpenGL/gl.h>
#import <stdio.h>
#import <stdlib.h>
#import <string.h>

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

static void clear_once(int dim, float r, float g, float b, float a) {
    GLuint color = 0;
    GLuint fbo = 0;
    glGenTextures(1, &color);
    glBindTexture(GL_TEXTURE_2D, color);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, dim, dim, 0, GL_RGBA, GL_UNSIGNED_BYTE, NULL);
    glGenFramebuffers(1, &fbo);
    glBindFramebuffer(GL_FRAMEBUFFER, fbo);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, color, 0);
    if (glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE) {
        die("fbo incomplete");
    }
    glViewport(0, 0, dim, dim);
    glColorMask(GL_TRUE, GL_TRUE, GL_TRUE, GL_TRUE);
    glDisable(GL_DITHER);
    glEnable(GL_SCISSOR_TEST);
    glScissor(0, 0, dim, dim);
    glClearColor(r, g, b, a);
    glClear(GL_COLOR_BUFFER_BIT);
    gl_ok("clear");

    size_t bytes = (size_t)dim * (size_t)dim * 4u;
    unsigned char *pixels = (unsigned char *)malloc(bytes);
    if (pixels == NULL) {
        die("alloc");
    }
    glPixelStorei(GL_PACK_ALIGNMENT, 1);
    glReadPixels(0, 0, dim, dim, GL_BGRA, GL_UNSIGNED_BYTE, pixels);
    gl_ok("read");
    fwrite(pixels, 1, bytes, stdout);
    free(pixels);
    glDeleteFramebuffers(1, &fbo);
    glDeleteTextures(1, &color);
}

int main(int argc, char **argv) {
    if (argc != 6) {
        fprintf(stderr, "usage: %s dim r g b a\n", argv[0]);
        return 2;
    }
    int dim = atoi(argv[1]);
    float r = (float)atof(argv[2]);
    float g = (float)atof(argv[3]);
    float b = (float)atof(argv[4]);
    float a = (float)atof(argv[5]);
    if (dim <= 0 || dim > 64) {
        die("bad dim");
    }

    CGLPixelFormatAttribute attrs[] = {
        kCGLPFAOpenGLProfile, (CGLPixelFormatAttribute)kCGLOGLPVersion_Legacy,
        kCGLPFAAccelerated,
        kCGLPFAColorSize, (CGLPixelFormatAttribute)24,
        kCGLPFAAlphaSize, (CGLPixelFormatAttribute)8,
        0};
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
    clear_once(dim, r, g, b, a);
    return 0;
}
