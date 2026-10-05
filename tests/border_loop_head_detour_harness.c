#include <stdint.h>
#include <stdio.h>

extern void eu4_border_loop_head_gateway(void);
extern uint64_t border_loop_draw_target;
extern uint64_t border_loop_skip_target;

static void resume_stub(void) {}

void eu4_border_loop_head_observe_frame(void *rbp, void *r12, uint64_t r13_full, void *rcx) {
    (void)rbp;
    (void)r12;
    (void)r13_full;
    (void)rcx;
}

int main(void) {
    border_loop_draw_target = (uint64_t)(uintptr_t)resume_stub;
    border_loop_skip_target = (uint64_t)(uintptr_t)resume_stub;
    uint64_t before = 0xDEADBEEFCAFEBABEull;
    uint64_t after = 0;
    __asm__ volatile(
        "movq %[in], %%r11\n"
        "call _eu4_border_loop_head_gateway\n"
        "movq %%r11, %[out]\n"
        : [out] "=r"(after)
        : [in] "r"(before)
        : "r11", "memory");
    if (after != before) {
        fprintf(stderr, "r11 clobbered: %llx != %llx\n", (unsigned long long)after, (unsigned long long)before);
        return 1;
    }
    return 0;
}
