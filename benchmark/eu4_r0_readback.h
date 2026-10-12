#ifndef EU4_R0_READBACK_H
#define EU4_R0_READBACK_H

#include <OpenGL/OpenGL.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef enum {
    EU4_R0_READBACK_OK = 0,
    EU4_R0_READBACK_UNSUPPORTED_DRAWABLE = 1,
    EU4_R0_READBACK_FRAMEBUFFER_MISMATCH = 2,
    EU4_R0_READBACK_DIMENSION_MISMATCH = 3,
    EU4_R0_READBACK_ALLOCATION_FAILED = 4,
    EU4_R0_READBACK_GL_ERROR = 5,
    EU4_R0_READBACK_UNKNOWN_FAILURE = 6,
} eu4_r0_readback_failure_t;

typedef enum {
    EU4_R0_TARGET_DRAWABLE_BACK,
    EU4_R0_TARGET_TEST_FBO
} eu4_r0_target_t;

typedef struct {
    bool ok;
    int width;
    int height;
    GLenum gl_error_observed;
    const uint8_t *pixels;
    size_t pixel_bytes;
    uint64_t crc64;
    eu4_r0_readback_failure_t failure_code;
} eu4_r0_readback_result_t;

typedef struct {
    uint8_t **pixels_out;
    size_t *capacity_out;
    bool (*grow)(uint8_t **buf, size_t *cap, size_t needed);
} eu4_r0_readback_buffers_t;

bool eu4_r0_readback_capture(CGLContextObj context, eu4_r0_target_t target, bool test_relaxed_policy,
                             eu4_r0_readback_buffers_t *buffers, eu4_r0_readback_result_t *result);

uint64_t eu4_r0_crc64_ecma(const uint8_t *data, size_t len);

#endif
