/* Focused native test for the original-GL readback's pixel-pack handling.
 * Creates a real CGL context; no game launch, no mocked GL driver. */

#define GL_SILENCE_DEPRECATION
#include "eu4_gfx_observe_framebuffer.h"

#include <OpenGL/OpenGL.h>
#include <OpenGL/gl.h>
#include <stdbool.h>
#include <stdio.h>

bool eu4_gfx_observe_read(const void *src, void *dest, size_t n) {
    (void)src;
    (void)dest;
    (void)n;
    return false;
}

bool eu4_gfx_observe_read_bytes(const void *src, void *dest, size_t n) {
    (void)src;
    (void)dest;
    (void)n;
    return false;
}

static int make_context(void) {
    CGLPixelFormatAttribute attributes[] = {
        kCGLPFAOpenGLProfile, (CGLPixelFormatAttribute)kCGLOGLPVersion_Legacy,
        kCGLPFAAccelerated, kCGLPFAColorSize, (CGLPixelFormatAttribute)24, 0};
    CGLPixelFormatObj format = NULL;
    GLint count = 0;
    if (CGLChoosePixelFormat(attributes, &format, &count) != kCGLNoError ||
        format == NULL) {
        return 0;
    }
    CGLContextObj context = NULL;
    int ok = CGLCreateContext(format, NULL, &context) == kCGLNoError &&
             context != NULL;
    CGLDestroyPixelFormat(format);
    if (ok) {
        CGLSetCurrentContext(context);
    }
    return ok;
}

static void set_custom_pack(GLuint buffer) {
    glBindBuffer(GL_PIXEL_PACK_BUFFER, buffer);
    glPixelStorei(GL_PACK_ALIGNMENT, 8);
    glPixelStorei(GL_PACK_ROW_LENGTH, 7);
    glPixelStorei(GL_PACK_SKIP_ROWS, 2);
    glPixelStorei(GL_PACK_SKIP_PIXELS, 3);
}

static void dump_pack(const char *label) {
    GLint alignment = 0, row = 0, skip_rows = 0, skip_pixels = 0, bound = 0;
    glGetIntegerv(GL_PACK_ALIGNMENT, &alignment);
    glGetIntegerv(GL_PACK_ROW_LENGTH, &row);
    glGetIntegerv(GL_PACK_SKIP_ROWS, &skip_rows);
    glGetIntegerv(GL_PACK_SKIP_PIXELS, &skip_pixels);
    glGetIntegerv(GL_PIXEL_PACK_BUFFER_BINDING, &bound);
    printf("%s alignment=%d row=%d skip_rows=%d skip_pixels=%d bound=%d error=0x%x\n",
           label, alignment, row, skip_rows, skip_pixels, bound, glGetError());
}

static int custom_pack_intact(GLuint buffer) {
    GLint alignment = 0, row = 0, skip_rows = 0, skip_pixels = 0, bound = 0;
    glGetIntegerv(GL_PACK_ALIGNMENT, &alignment);
    glGetIntegerv(GL_PACK_ROW_LENGTH, &row);
    glGetIntegerv(GL_PACK_SKIP_ROWS, &skip_rows);
    glGetIntegerv(GL_PACK_SKIP_PIXELS, &skip_pixels);
    glGetIntegerv(GL_PIXEL_PACK_BUFFER_BINDING, &bound);
    return alignment == 8 && row == 7 && skip_rows == 2 && skip_pixels == 3 &&
           bound == (GLint)buffer;
}

int main(void) {
    if (!make_context()) {
        fprintf(stderr, "no GL context\n");
        return 2;
    }
    GLuint buffer = 0;
    glGenBuffers(1, &buffer);
    if (buffer == 0) {
        fprintf(stderr, "no PBO\n");
        return 2;
    }
    set_custom_pack(buffer);
    dump_pack("before");

    int roundtrip = eu4_gfx_observe_framebuffer_test_pack_roundtrip();
    dump_pack("after-roundtrip");
    int restored = custom_pack_intact(buffer);
    printf("roundtrip=%d restored=%d\n", roundtrip, restored);

    int failure = eu4_gfx_observe_framebuffer_test_pack_failure();
    int failure_restored = custom_pack_intact(buffer);
    printf("failure=%d failure_restored=%d\n", failure, failure_restored);

    int default_ok = eu4_gfx_observe_framebuffer_test_framebuffer(
                         0, 0, GL_BACK, GL_BACK) == 1;
    int draw_front = eu4_gfx_observe_framebuffer_test_framebuffer(
                         0, 0, GL_BACK, GL_FRONT) == 0;
    int separate_fbo = eu4_gfx_observe_framebuffer_test_framebuffer(
                           1, 1, GL_BACK, GL_BACK) == 0;
    int mismatch = eu4_gfx_observe_framebuffer_test_framebuffer(
                       0, 1, GL_BACK, GL_BACK) == 0;
    int read_front = eu4_gfx_observe_framebuffer_test_framebuffer(
                         0, 0, GL_FRONT, GL_BACK) == 0;
    printf("default_ok=%d draw_front=%d separate_fbo=%d mismatch=%d read_front=%d\n",
           default_ok, draw_front, separate_fbo, mismatch, read_front);

    return roundtrip == 0 && restored && failure == 0 && failure_restored &&
                   default_ok && draw_front && separate_fbo && mismatch &&
                   read_front
               ? 0
               : 1;
}
