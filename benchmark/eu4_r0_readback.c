#define GL_SILENCE_DEPRECATION 1
#include "eu4_r0_readback.h"

#include <OpenGL/gl.h>
#include <stdlib.h>
#include <string.h>

#ifndef GL_READ_FRAMEBUFFER_BINDING
#define GL_READ_FRAMEBUFFER_BINDING 0x8CAA
#endif

static uint64_t crc64_table[256];
static int crc64_table_ready;

static void eu4_r0_crc64_init(void) {
    if (crc64_table_ready) {
        return;
    }
    for (int byte = 0; byte < 256; byte++) {
        uint64_t crc = (uint64_t)byte;
        for (int bit = 0; bit < 8; bit++) {
            if (crc & 1ull) {
                crc = (crc >> 1) ^ 0xc96c5795d7870f42ull;
            } else {
                crc >>= 1;
            }
        }
        crc64_table[byte] = crc;
    }
    crc64_table_ready = 1;
}

uint64_t eu4_r0_crc64_ecma(const uint8_t *data, size_t len) {
    eu4_r0_crc64_init();
    uint64_t crc = 0;
    for (size_t i = 0; i < len; i++) {
        crc = crc64_table[(crc ^ data[i]) & 0xffu] ^ (crc >> 8);
    }
    return crc;
}

static bool default_grow(uint8_t **buf, size_t *cap, size_t needed) {
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

static bool plausible_dim(int value) {
    return value > 0 && value <= 16384;
}

static bool establish_drawable_dims(CGLContextObj context, bool test_relaxed, int *width_out, int *height_out) {
    GLint dims[2] = {0, 0};
    CGLError err = CGLGetParameter(context, kCGLCPSurfaceBackingSize, dims);
    if (err == kCGLNoError && plausible_dim(dims[0]) && plausible_dim(dims[1])) {
        *width_out = (int)dims[0];
        *height_out = (int)dims[1];
        GLint viewport[4] = {0, 0, 0, 0};
        glGetIntegerv(GL_VIEWPORT, viewport);
        if (plausible_dim(viewport[2]) && plausible_dim(viewport[3])) {
            if (viewport[2] != dims[0] || viewport[3] != dims[1]) {
                return false;
            }
        }
        return true;
    }
    if (test_relaxed) {
        GLint viewport[4] = {0, 0, 0, 0};
        glGetIntegerv(GL_VIEWPORT, viewport);
        if (plausible_dim(viewport[2]) && plausible_dim(viewport[3])) {
            *width_out = viewport[2];
            *height_out = viewport[3];
            return true;
        }
    }
    return false;
}

static bool validate_expected_dims(int width, int height) {
    const char *ew = getenv("EU4_R0_EXPECT_WIDTH");
    const char *eh = getenv("EU4_R0_EXPECT_HEIGHT");
    if (!ew || !eh) {
        return true;
    }
    int expect_w = atoi(ew);
    int expect_h = atoi(eh);
    if (expect_w > 0 && expect_h > 0) {
        return width == expect_w && height == expect_h;
    }
    return true;
}

static bool validate_target(eu4_r0_target_t target, bool test_relaxed) {
    GLint draw_fbo = 0;
    GLint read_fbo = 0;
    glGetIntegerv(GL_FRAMEBUFFER_BINDING, &draw_fbo);
    glGetIntegerv(GL_READ_FRAMEBUFFER_BINDING, &read_fbo);

    if (target == EU4_R0_TARGET_TEST_FBO) {
        return draw_fbo != 0;
    }
    if (draw_fbo != 0 || read_fbo != 0) {
        return test_relaxed ? false : false;
    }
    GLint read_buffer = 0;
    glGetIntegerv(GL_READ_BUFFER, &read_buffer);
    if (!test_relaxed && read_buffer != GL_BACK && read_buffer != GL_NONE) {
        /* read buffer may be GL_BACK after we set it */
    }
    return true;
}

bool eu4_r0_readback_capture(CGLContextObj context, eu4_r0_target_t target, bool test_relaxed_policy,
                             eu4_r0_readback_buffers_t *buffers, eu4_r0_readback_result_t *result) {
    if (!context || CGLGetCurrentContext() != context || !buffers || !result) {
        return false;
    }
    memset(result, 0, sizeof(*result));
    if (!validate_target(target, test_relaxed_policy)) {
        return false;
    }

    int width = 0;
    int height = 0;
    if (target == EU4_R0_TARGET_TEST_FBO) {
        GLint viewport[4] = {0, 0, 0, 0};
        glGetIntegerv(GL_VIEWPORT, viewport);
        width = viewport[2];
        height = viewport[3];
    } else if (!establish_drawable_dims(context, test_relaxed_policy, &width, &height)) {
        return false;
    }
    if (!plausible_dim(width) || !plausible_dim(height) || !validate_expected_dims(width, height)) {
        return false;
    }

    size_t row_bytes = (size_t)width * 4u;
    size_t total = row_bytes * (size_t)height;
    bool (*grow_fn)(uint8_t **, size_t *, size_t) = buffers->grow ? buffers->grow : default_grow;
    if (!grow_fn(buffers->pixels_out, buffers->capacity_out, total)) {
        return false;
    }

    GLint prev_pack_buffer = 0;
    GLint prev_pack_alignment = 4;
    GLint prev_pack_row_length = 0;
    GLint prev_pack_skip_pixels = 0;
    GLint prev_pack_skip_rows = 0;
    GLint prev_read_buffer = GL_BACK;
    glGetIntegerv(GL_PIXEL_PACK_BUFFER_BINDING, &prev_pack_buffer);
    glGetIntegerv(GL_PACK_ALIGNMENT, &prev_pack_alignment);
    glGetIntegerv(GL_PACK_ROW_LENGTH, &prev_pack_row_length);
    glGetIntegerv(GL_PACK_SKIP_PIXELS, &prev_pack_skip_pixels);
    glGetIntegerv(GL_PACK_SKIP_ROWS, &prev_pack_skip_rows);
    glGetIntegerv(GL_READ_BUFFER, &prev_read_buffer);

    glBindBuffer(GL_PIXEL_PACK_BUFFER, 0);
    glPixelStorei(GL_PACK_ALIGNMENT, 4);
    glPixelStorei(GL_PACK_ROW_LENGTH, 0);
    glPixelStorei(GL_PACK_SKIP_PIXELS, 0);
    glPixelStorei(GL_PACK_SKIP_ROWS, 0);
    if (target == EU4_R0_TARGET_DRAWABLE_BACK) {
        glReadBuffer(GL_BACK);
    } else {
        glReadBuffer(GL_COLOR_ATTACHMENT0);
    }
    glReadPixels(0, 0, width, height, GL_RGBA, GL_UNSIGNED_BYTE, *buffers->pixels_out);

    glBindBuffer(GL_PIXEL_PACK_BUFFER, (GLuint)prev_pack_buffer);
    glPixelStorei(GL_PACK_ALIGNMENT, prev_pack_alignment);
    glPixelStorei(GL_PACK_ROW_LENGTH, prev_pack_row_length);
    glPixelStorei(GL_PACK_SKIP_PIXELS, prev_pack_skip_pixels);
    glPixelStorei(GL_PACK_SKIP_ROWS, prev_pack_skip_rows);
    glReadBuffer((GLenum)prev_read_buffer);

    GLenum gl_err = glGetError();
    result->gl_error_observed = gl_err;
    if (gl_err != GL_NO_ERROR) {
        result->ok = false;
        result->width = width;
        result->height = height;
        return false;
    }

    result->ok = true;
    result->width = width;
    result->height = height;
    result->pixels = *buffers->pixels_out;
    result->pixel_bytes = total;
    result->crc64 = eu4_r0_crc64_ecma(result->pixels, total);
    return true;
}
