#include <stdint.h>

int g_observer_calls = 0;
int g_bad_stack_alignment = 0;

void eu4_submission_on_renderbuckets_entry(void) {
    uintptr_t rsp = 0;
#if defined(__x86_64__)
    __asm__ volatile("mov %%rsp, %0" : "=r"(rsp));
#endif
    if (rsp % 16u != 0u) {
        g_bad_stack_alignment = 1;
    }
    g_observer_calls++;
}
