#define GL_SILENCE_DEPRECATION 1
#include <OpenGL/OpenGL.h>
#include <OpenGL/gl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int failures;

static void expect_int(const char *label, int got, int want) {
    if (got != want) {
        fprintf(stderr, "%s: got %d want %d\n", label, got, want);
        failures++;
    }
}

static GLuint fbo_with_texture(int width, int height, GLuint *texture_out) {
    GLuint texture = 0;
    glGenTextures(1, &texture);
    glBindTexture(GL_TEXTURE_2D, texture);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, width, height, 0, GL_RGBA, GL_UNSIGNED_BYTE, NULL);
    GLuint fbo = 0;
    glGenFramebuffers(1, &fbo);
    glBindFramebuffer(GL_FRAMEBUFFER, fbo);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, texture, 0);
    if (glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE) {
        fprintf(stderr, "framebuffer incomplete\n");
        exit(2);
    }
    *texture_out = texture;
    return fbo;
}

static void read_rgba(int x, int y, int w, int h, void *dst) {
    GLint pack_buffer = 0;
    GLint pack_alignment = 0;
    GLint pack_row_length = 0;
    GLint pack_skip_pixels = 0;
    GLint pack_skip_rows = 0;
    glGetIntegerv(GL_PIXEL_PACK_BUFFER_BINDING, &pack_buffer);
    glGetIntegerv(GL_PACK_ALIGNMENT, &pack_alignment);
    glGetIntegerv(GL_PACK_ROW_LENGTH, &pack_row_length);
    glGetIntegerv(GL_PACK_SKIP_PIXELS, &pack_skip_pixels);
    glGetIntegerv(GL_PACK_SKIP_ROWS, &pack_skip_rows);

    glBindBuffer(GL_PIXEL_PACK_BUFFER, 0);
    glPixelStorei(GL_PACK_ALIGNMENT, 1);
    glPixelStorei(GL_PACK_ROW_LENGTH, 0);
    glPixelStorei(GL_PACK_SKIP_PIXELS, 0);
    glPixelStorei(GL_PACK_SKIP_ROWS, 0);
    glReadPixels(x, y, w, h, GL_RGBA, GL_UNSIGNED_BYTE, dst);

    glBindBuffer(GL_PIXEL_PACK_BUFFER, (GLuint)pack_buffer);
    glPixelStorei(GL_PACK_ALIGNMENT, pack_alignment);
    glPixelStorei(GL_PACK_ROW_LENGTH, pack_row_length);
    glPixelStorei(GL_PACK_SKIP_PIXELS, pack_skip_pixels);
    glPixelStorei(GL_PACK_SKIP_ROWS, pack_skip_rows);
}

int main(void) {
    CGLPixelFormatAttribute attrs[] = {kCGLPFAAccelerated, (CGLPixelFormatAttribute)0};
    CGLPixelFormatObj format = NULL;
    CGLContextObj context = NULL;
    GLint count = 0;
    if (CGLChoosePixelFormat(attrs, &format, &count) != kCGLNoError || !format) {
        return 2;
    }
    if (CGLCreateContext(format, NULL, &context) != kCGLNoError || !context) {
        return 3;
    }
    if (CGLSetCurrentContext(context) != kCGLNoError) {
        return 4;
    }

    const int width = 8;
    const int height = 8;
    GLuint texture = 0;
    GLuint fbo = fbo_with_texture(width, height, &texture);
    glBindFramebuffer(GL_FRAMEBUFFER, fbo);
    glViewport(0, 0, width, height);
    glClearColor(1.0f, 0.0f, 0.0f, 1.0f);
    glClear(GL_COLOR_BUFFER_BIT);

    unsigned char pixels[8 * 8 * 4];
    memset(pixels, 0, sizeof(pixels));
    read_rgba(0, 0, width, height, pixels);
    expect_int("red channel", pixels[0], 255);

    GLint alignment_after = 0;
    glGetIntegerv(GL_PACK_ALIGNMENT, &alignment_after);
    expect_int("pack alignment restored", alignment_after, 4);

    glPixelStorei(GL_PACK_ALIGNMENT, 1);
    glPixelStorei(GL_PACK_ROW_LENGTH, width + 2);
    unsigned char packed[10 * 8 * 4];
    memset(packed, 0, sizeof(packed));
    for (int y = 0; y < height; y++) {
        for (int x = 0; x < width; x++) {
            packed[y * 10 * 4 + x * 4] = (unsigned char)(x + y);
        }
    }
    glClearColor(0.0f, 0.0f, 0.0f, 1.0f);
    glClear(GL_COLOR_BUFFER_BIT);
    glReadPixels(0, 0, width, height, GL_RGBA, GL_UNSIGNED_BYTE, packed);
    expect_int("row length sample", packed[4], 1);

    GLuint pbo = 0;
    glGenBuffers(1, &pbo);
    glBindBuffer(GL_PIXEL_PACK_BUFFER, pbo);
    glBufferData(GL_PIXEL_PACK_BUFFER, 16, NULL, GL_STREAM_READ);
    glBindFramebuffer(GL_FRAMEBUFFER, fbo);
    glClearColor(0.0f, 1.0f, 0.0f, 1.0f);
    glClear(GL_COLOR_BUFFER_BIT);
    unsigned char cpu_buf[16];
    memset(cpu_buf, 0xAA, sizeof(cpu_buf));
    glBindBuffer(GL_PIXEL_PACK_BUFFER, 0);
    read_rgba(0, 0, 1, 1, cpu_buf);
    expect_int("pbo unbound read green", cpu_buf[1], 255);

    return failures ? 1 : 0;
}
