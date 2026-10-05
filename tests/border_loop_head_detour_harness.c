#include <setjmp.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

extern void eu4_border_loop_head_gateway(void);
extern uint64_t border_loop_draw_target;
extern uint64_t border_loop_skip_target;

static jmp_buf g_gateway_return;
static volatile int g_resumed = 0;
static uint64_t g_r11_after = 0;

static void resume_stub(void) {
    g_resumed = 1;
    __asm__ volatile("movq %%r11, %0" : "=r"(g_r11_after));
    longjmp(g_gateway_return, 1);
}

void eu4_border_loop_head_observe_frame(void *rbp, void *r12, uint64_t r13_full, void *rcx) {
    (void)rbp;
    (void)r12;
    (void)r13_full;
    (void)rcx;
}

int main(void) {
    border_loop_draw_target = (uint64_t)(uintptr_t)resume_stub;
    border_loop_skip_target = (uint64_t)(uintptr_t)resume_stub;
    const uint64_t before = 0xDEADBEEFCAFEBABEull;
    static uint8_t index_stream[8] = {1, 0xFF, 0, 0, 0, 0, 0, 0};
    uintptr_t end_bound_cell = (uintptr_t)(index_stream + 6);
    void *const fake_rbp = (void *)((char *)&end_bound_cell + 0x198u);
    void *const fake_rcx = (void *)index_stream;
    void *const fake_r12 = (void *)index_stream;
    if (setjmp(g_gateway_return) == 0) {
        __asm__ volatile(
            "movq %[in], %%r11\n"
            "movq %[rcx], %%rcx\n"
            "movq %[rbp], %%rbp\n"
            "movq %[r12], %%r12\n"
            "movq $0xff, %%r13\n"
            "movq %%rsp, %%rax\n"
            "andq $-16, %%rsp\n"
            "jmp _eu4_border_loop_head_gateway\n"
            :
            : [in] "r"(before), [rcx] "r"(fake_rcx), [rbp] "r"(fake_rbp), [r12] "r"(fake_r12)
            : "rax", "r11", "r12", "r13", "memory");
        fprintf(stderr, "gateway jmp fell through\n");
        return 1;
    }
    if (!g_resumed) {
        fprintf(stderr, "gateway did not reach draw/skip continuation\n");
        return 1;
    }
    if (g_r11_after != before) {
        fprintf(stderr, "r11 clobbered: %llx != %llx\n", (unsigned long long)g_r11_after,
                (unsigned long long)before);
        return 1;
    }
    return 0;
}
