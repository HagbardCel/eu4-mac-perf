#include <immintrin.h>
#include <setjmp.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

extern void eu4_border_loop_head_gateway(void);
extern uint64_t border_loop_draw_target;
extern uint64_t border_loop_skip_target;
extern uint64_t border_loop_gateway_test_captured_rsp;

void border_loop_gateway_test_observe_body(void *rbp, void *r12, uint64_t r13_full, void *rcx);

static jmp_buf g_gateway_return;
static volatile int g_branch = 0;

static void *g_expected_rbp;
static void *g_expected_r12;
static void *g_expected_rcx;
static uint64_t g_expected_r13;

static uint64_t g_seed_r11;
static uint64_t g_cont_r11;

static void resume_draw_stub(void) {
    __asm__ volatile("movq %%r11, %0" : "=m"(g_cont_r11));
    g_branch = 1;
    longjmp(g_gateway_return, 1);
}

static void resume_skip_stub(void) {
    __asm__ volatile("movq %%r11, %0" : "=m"(g_cont_r11));
    g_branch = 2;
    longjmp(g_gateway_return, 1);
}

void border_loop_gateway_test_observe_body(void *rbp, void *r12, uint64_t r13_full, void *rcx) {
    if ((border_loop_gateway_test_captured_rsp & 15ull) != 8ull) {
        fprintf(stderr, "observe raw rsp mod16 expected 8 got %llu\n",
                (unsigned long long)(border_loop_gateway_test_captured_rsp & 15ull));
        _exit(10);
    }
    if (rbp != g_expected_rbp || r12 != g_expected_r12 || rcx != g_expected_rcx || r13_full != g_expected_r13) {
        fprintf(stderr, "observe marshalling mismatch\n");
        _exit(11);
    }
}

static int run_gateway_branch(int draw_path) {
    g_branch = 0;
    g_cont_r11 = 0;
    border_loop_draw_target = (uint64_t)(uintptr_t)resume_draw_stub;
    border_loop_skip_target = (uint64_t)(uintptr_t)resume_skip_stub;

    static uint8_t fake_frame[0x300];
    static uint8_t index_stream[8];
    memset(index_stream, 0, sizeof(index_stream));
    index_stream[0] = 1;
    index_stream[1] = draw_path ? (uint8_t)0xFF : (uint8_t)0x00;

    void *const fake_rbp = (void *)(fake_frame + 0x200);
    void *const fake_rcx = (void *)index_stream;
    void *const fake_r12 = (void *)index_stream;

    g_expected_rbp = fake_rbp;
    g_expected_r12 = fake_r12;
    g_expected_rcx = fake_rcx;
    g_expected_r13 = 0xffull;

    g_seed_r11 = 0xDEADBEEFCAFEBABEull;

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
            : [in] "r"(g_seed_r11), [rcx] "r"(fake_rcx), [rbp] "r"(fake_rbp), [r12] "r"(fake_r12)
            : "rax", "r11", "r12", "r13", "memory");
        fprintf(stderr, "gateway jmp fell through\n");
        return 1;
    }
    if (draw_path && g_branch != 1) {
        fprintf(stderr, "expected draw branch got %d\n", g_branch);
        return 1;
    }
    if (!draw_path && g_branch != 2) {
        fprintf(stderr, "expected skip branch got %d\n", g_branch);
        return 1;
    }
    if (g_cont_r11 != g_seed_r11) {
        fprintf(stderr, "r11 clobbered: %llx != %llx\n", (unsigned long long)g_cont_r11,
                (unsigned long long)g_seed_r11);
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
