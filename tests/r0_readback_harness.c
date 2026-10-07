#define GL_SILENCE_DEPRECATION 1
#include "../benchmark/eu4_r0_readback.h"

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

static bool buffer_grow(uint8_t **buf, size_t *cap, size_t needed) {
    if (*cap >= needed) {
        return true;
    }
    uint8_t *grown = realloc(*buf, needed);
    if (!grown) {
        return false;
    }
    *buf = grown;
    *cap = needed;
    return true;
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

    uint8_t *pixels = NULL;
    size_t capacity = 0;
    eu4_r0_readback_buffers_t buffers = {
        .pixels_out = &pixels,
        .capacity_out = &capacity,
        .grow = buffer_grow,
    };
    eu4_r0_readback_result_t result;
    if (!eu4_r0_readback_capture(context, EU4_R0_TARGET_TEST_FBO, false, &buffers, &result)) {
        fprintf(stderr, "readback capture failed\n");
        return 5;
    }
    expect_int("red channel", pixels[0], 255);

    glPixelStorei(GL_PACK_ALIGNMENT, 1);
    glPixelStorei(GL_PACK_ROW_LENGTH, width + 2);
    glClearColor(0.0f, 0.0f, 0.0f, 1.0f);
    glClear(GL_COLOR_BUFFER_BIT);
    if (!eu4_r0_readback_capture(context, EU4_R0_TARGET_TEST_FBO, false, &buffers, &result)) {
        fprintf(stderr, "second readback failed\n");
        return 6;
    }
    GLint alignment_after = 0;
    glGetIntegerv(GL_PACK_ALIGNMENT, &alignment_after);
    expect_int("pack alignment restored to prior", alignment_after, 1);

    glPixelStorei(GL_PACK_ALIGNMENT, 4);
    glClearColor(0.0f, 1.0f, 0.0f, 1.0f);
    glClear(GL_COLOR_BUFFER_BIT);
    if (!eu4_r0_readback_capture(context, EU4_R0_TARGET_TEST_FBO, false, &buffers, &result)) {
        fprintf(stderr, "third readback failed\n");
        return 7;
    }
    glGetIntegerv(GL_PACK_ALIGNMENT, &alignment_after);
    expect_int("pack alignment restored default", alignment_after, 4);

    free(pixels);
    return failures ? 1 : 0;
}
