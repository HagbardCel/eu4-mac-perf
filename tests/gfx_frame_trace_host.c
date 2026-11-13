/* Focused host test for the frame-trace manifest writer. No game, no context.
 * The observer helpers the trace module calls are stubbed; only the writer is
 * exercised. */
#include "eu4_gfx_frame_trace.h"

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

bool eu4_gfx_observe_read(const void *src, void *dest, size_t n) {
    (void)src; (void)dest; (void)n;
    return false;
}

bool eu4_gfx_observe_read_bytes(const void *src, void *dest, size_t n) {
    (void)src; (void)dest; (void)n;
    return false;
}

void eu4_gfx_observe_set_kind(eu4_gfx_observe_call *call, uint32_t kind) {
    if (call != NULL) {
        call->kind = kind;
    }
}

int eu4_gfx_gl_drawable_extent(uint32_t *width, uint32_t *height) {
    (void)width; (void)height;
    return 0;
}

int main(int argc, char **argv) {
    if (argc != 2) {
        fprintf(stderr, "usage: %s <dir>\n", argv[0]);
        return 2;
    }
    if (eu4_gfx_frame_trace_test_manifest(argv[1]) != 0) {
        fprintf(stderr, "manifest writer failed\n");
        return 1;
    }
    return 0;
}
