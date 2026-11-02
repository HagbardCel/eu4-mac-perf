#include "eu4_gfx_draw_capture.h"

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

extern uint64_t eu4_gfx_shim_expect_ret;
extern int eu4_gfx_shim_return;

_Static_assert(offsetof(eu4_gfx_draw_raw, which) == 0, "which");
_Static_assert(offsetof(eu4_gfx_draw_raw, rdi) == 8, "rdi");
_Static_assert(offsetof(eu4_gfx_draw_raw, xmm) == 56, "xmm");
_Static_assert(offsetof(eu4_gfx_draw_raw, retaddr) == 184, "retaddr");
_Static_assert(offsetof(eu4_gfx_draw_raw, stack) == 192, "stack");
_Static_assert(sizeof(eu4_gfx_draw_raw) == 256, "raw size");

static void fail(const char *what) {
    fprintf(stderr, "%s\n", what);
    _exit(1);
}

int eu4_gfx_draw_capture(const eu4_gfx_draw_raw *raw) {
    if (raw == NULL) {
        fail("null");
    }
    if (raw->which != 1 || raw->pad != 0) {
        fail("which");
    }
    if (raw->rdi != 0x1111111111110001ULL || raw->rsi != 0x2222222222220002ULL ||
        raw->rdx != 0x3333333333330003ULL || raw->rcx != 0x4444444444440004ULL ||
        raw->r8 != 0x5555555555550005ULL || raw->r9 != 0x6666666666660006ULL) {
        fail("registers");
    }
    if (raw->retaddr != eu4_gfx_shim_expect_ret) {
        fail("return address");
    }
    uint8_t stack[64];
    uint8_t xmm[128];
    for (int i = 0; i < 64; i++) {
        stack[i] = (uint8_t)(0xa0 + i);
    }
    for (int i = 0; i < 128; i++) {
        xmm[i] = (uint8_t)(0x10 + i);
    }
    if (memcmp(raw->stack, stack, 64) != 0) {
        fail("stack");
    }
    if (memcmp(raw->xmm[0], xmm, 16) != 0 || memcmp(raw->xmm[1], xmm + 16, 16) != 0 ||
        memcmp(raw->xmm[2], xmm + 32, 16) != 0 || memcmp(raw->xmm[3], xmm + 48, 16) != 0 ||
        memcmp(raw->xmm[4], xmm, 16) != 0 || memcmp(raw->xmm[5], xmm + 16, 16) != 0 ||
        memcmp(raw->xmm[6], xmm + 32, 16) != 0 || memcmp(raw->xmm[7], xmm + 48, 16) != 0) {
        fail("xmm");
    }
    if (eu4_gfx_shim_return) {
        return 1;
    }
    _exit(0);
}
