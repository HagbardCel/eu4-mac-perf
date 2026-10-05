#include "border_loop_head_gateway_test_shared.h"

#include <setjmp.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

extern uint64_t border_loop_draw_target;
extern uint64_t border_loop_skip_target;

static jmp_buf g_gateway_return;

gateway_test_machine_state_t gateway_test_seed;
gateway_test_machine_state_t gateway_test_capture;
volatile int gateway_test_branch_id = 0;

void *gateway_test_live_rbp;
void *gateway_test_live_r12;
void *gateway_test_live_rcx;
uint64_t gateway_test_live_r13;

uint64_t border_loop_gateway_test_captured_rsp;

static void fill_seed_state(void) {
    memset(&gateway_test_seed, 0, sizeof(gateway_test_seed));
    gateway_test_seed.rax = 0xA0A0A0A0A0A0A0A0ull;
    gateway_test_seed.rcx = 0xB0B0B0B0B0B0B0B0ull;
    gateway_test_seed.rdx = 0xC0C0C0C0C0C0C0C0ull;
    gateway_test_seed.rsi = 0xD0D0D0D0D0D0D0D0ull;
    gateway_test_seed.rdi = 0xE0E0E0E0E0E0E0E0ull;
    gateway_test_seed.r8 = 0xF0F0F0F0F0F0F0F0ull;
    gateway_test_seed.r9 = 0x1212121212121212ull;
    gateway_test_seed.r10 = 0x1313131313131313ull;
    gateway_test_seed.r11 = 0xDEADBEEFCAFEBABEull;
    gateway_test_seed.mxcsr = 0x1F80u;
    for (int i = 0; i < 16; i++) {
        gateway_test_seed.xmm0[i] = (uint8_t)(0xA0 + i);
    }
}

static int states_equal(const gateway_test_machine_state_t *a, const gateway_test_machine_state_t *b) {
    if (a->rax != b->rax || a->rcx != b->rcx || a->rdx != b->rdx || a->rsi != b->rsi || a->rdi != b->rdi ||
        a->r8 != b->r8 || a->r9 != b->r9 || a->r10 != b->r10 || a->r11 != b->r11 || a->mxcsr != b->mxcsr) {
        return 0;
    }
    return memcmp(a->xmm0, b->xmm0, 16) == 0;
}

void gateway_test_longjmp_back(void) {
    longjmp(g_gateway_return, 1);
}

void border_loop_gateway_test_observe_body(void *rbp, void *r12, uint64_t r13_full, void *rcx) {
    if ((border_loop_gateway_test_captured_rsp & 15ull) != 8ull) {
        fprintf(stderr, "observe raw rsp mod16 expected 8\n");
        _exit(10);
    }
    if (rbp != gateway_test_live_rbp || r12 != gateway_test_live_r12 || rcx != gateway_test_live_rcx ||
        r13_full != gateway_test_live_r13) {
        fprintf(stderr, "observe marshalling mismatch\n");
        _exit(11);
    }
}

static int run_gateway_branch(int draw_path) {
    gateway_test_branch_id = 0;
    memset(&gateway_test_capture, 0, sizeof(gateway_test_capture));
    fill_seed_state();

    border_loop_draw_target = (uint64_t)(uintptr_t)gateway_test_continuation_draw;
    border_loop_skip_target = (uint64_t)(uintptr_t)gateway_test_continuation_skip;

    static uint8_t fake_frame[0x300];
    static uint8_t index_stream[8];
    memset(index_stream, 0, sizeof(index_stream));
    index_stream[0] = 1;
    index_stream[1] = draw_path ? (uint8_t)0xFF : (uint8_t)0x00;

    gateway_test_live_rbp = (void *)(fake_frame + 0x200);
    gateway_test_live_rcx = (void *)index_stream;
    gateway_test_live_r12 = (void *)index_stream;
    gateway_test_live_r13 = 0xffull;

    /* rcx in seed is overwritten by live rcx at enter; compare capture to live values for rcx. */
    gateway_test_seed.rcx = (uint64_t)(uintptr_t)gateway_test_live_rcx;

    if (setjmp(g_gateway_return) == 0) {
        gateway_test_enter();
        fprintf(stderr, "gateway enter fell through\n");
        return 1;
    }

    if (draw_path && gateway_test_branch_id != 1) {
        fprintf(stderr, "expected draw branch got %d\n", gateway_test_branch_id);
        return 1;
    }
    if (!draw_path && gateway_test_branch_id != 2) {
        fprintf(stderr, "expected skip branch got %d\n", gateway_test_branch_id);
        return 1;
    }
    if (!states_equal(&gateway_test_seed, &gateway_test_capture)) {
        fprintf(stderr, "gateway transparency mismatch after restore\n");
        fprintf(stderr, "seed rax=%llx cap rax=%llx\n", (unsigned long long)gateway_test_seed.rax,
                (unsigned long long)gateway_test_capture.rax);
        fprintf(stderr, "seed mxcsr=%x cap mxcsr=%x\n", gateway_test_seed.mxcsr, gateway_test_capture.mxcsr);
        return 1;
    }
    return 0;
}

int main(void) {
    if (run_gateway_branch(1) != 0) {
        return 1;
    }
    if (run_gateway_branch(0) != 0) {
        return 1;
    }
    return 0;
}
