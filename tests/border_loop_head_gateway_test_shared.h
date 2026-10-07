#ifndef BORDER_LOOP_HEAD_GATEWAY_TEST_SHARED_H
#define BORDER_LOOP_HEAD_GATEWAY_TEST_SHARED_H

#include <stdint.h>

typedef struct {
    uint64_t rax;
    uint64_t rcx;
    uint64_t rdx;
    uint64_t rsi;
    uint64_t rdi;
    uint64_t r8;
    uint64_t r9;
    uint64_t r10;
    uint64_t r11;
    uint32_t mxcsr;
    uint8_t xmm0[16];
} gateway_test_machine_state_t;

extern gateway_test_machine_state_t gateway_test_seed;
extern gateway_test_machine_state_t gateway_test_capture;
extern uint32_t gateway_test_poison_mxcsr;
extern volatile int gateway_test_branch_id;

extern void *gateway_test_live_rbp;
extern void *gateway_test_live_r12;
extern void *gateway_test_live_rcx;
extern uint64_t gateway_test_live_r13;

extern uint64_t border_loop_gateway_test_captured_rsp;

void gateway_test_enter(void);
void gateway_test_continuation_draw(void);
void gateway_test_continuation_skip(void);
void gateway_test_longjmp_back(void);
void border_loop_gateway_test_observe_body(void *rbp, void *r12, uint64_t r13_full, void *rcx);

#endif
