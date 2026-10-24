#ifndef GFX_CAPTURE_TEST_STATE_H
#define GFX_CAPTURE_TEST_STATE_H

#include <stddef.h>
#include <stdint.h>

/* Offsets are used by tests/gfx_capture_observation.S. */
typedef struct {
    uint64_t rax;
    uint64_t rbx;
    uint64_t rcx;
    uint64_t rdx;
    uint64_t rsi;
    uint64_t rdi;
    uint64_t rbp;
    uint64_t r8;
    uint64_t r9;
    uint64_t r10;
    uint64_t r11;
    uint64_t r12;
    uint64_t r13;
    uint64_t r14;
    uint64_t r15;
    uint64_t flags;
    uint32_t mxcsr;
    uint32_t pad;
    uint8_t xmm[16][16];
} gfx_capture_test_state;

_Static_assert(offsetof(gfx_capture_test_state, flags) == 120,
               "observation flags offset");
_Static_assert(offsetof(gfx_capture_test_state, mxcsr) == 128,
               "observation mxcsr offset");
_Static_assert(offsetof(gfx_capture_test_state, xmm) == 136,
               "observation xmm offset");
_Static_assert(sizeof(gfx_capture_test_state) == 392,
               "observation state size");

extern gfx_capture_test_state gfx_capture_test_seed;
extern gfx_capture_test_state gfx_capture_test_capture;
extern uint64_t gfx_capture_test_pre_call_rsp;
extern uint64_t gfx_capture_test_capture_rsp;
extern void *gfx_capture_test_target;
extern uint64_t gfx_capture_test_saved_c_stack;

void gfx_capture_test_call_target(void);

#endif
