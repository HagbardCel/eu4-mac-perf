#include <stdint.h>
#include <stdio.h>

extern void eu4_border_loop_head_gateway(void);
extern uint64_t eu4_border_loop_head_trampoline_ptr;
static void resume_stub(void) {}

void eu4_border_loop_head_gateway_from_regs(uint8_t *index_cursor, uint8_t visibility_mask) {
    (void)index_cursor;
    (void)visibility_mask;
}

int main(void) {
    eu4_border_loop_head_trampoline_ptr = (uint64_t)(uintptr_t)resume_stub;
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
