#include "eu4_submission_border_loop_head.h"

#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

void eu4_border_loop_head_gateway(void) {}

uint64_t border_loop_draw_target;
uint64_t border_loop_skip_target;

static void plant_expected_prefix(void *site) {
    const unsigned char expected[14] = {0x44, 0x84, 0x69, 0x01, 0x48, 0x89, 0x4d, 0x90,
                                        0x0f, 0x84, 0x5a, 0x05, 0x00, 0x00};
    memcpy(site, expected, sizeof(expected));
}

int main(void) {
    eu4_border_loop_head_install_test_reset_hooks();
    eu4_border_loop_head_install_test_force_rx_fail(1);
    eu4_border_loop_head_install_test_force_rollback_fail(1);
    long page = sysconf(_SC_PAGESIZE);
    void *mem = mmap(NULL, (size_t)page, PROT_READ | PROT_WRITE | PROT_EXEC, MAP_PRIVATE | MAP_ANON, -1, 0);
    if (mem == MAP_FAILED) {
        return 2;
    }
    void *site = (char *)mem + 0x40;
    plant_expected_prefix(site);
    (void)eu4_border_loop_head_try_install_at(site, 0x100000000ull);
    if (!eu4_border_loop_head_install_fatal()) {
        fprintf(stderr, "expected fatal before border install\n");
        return 3;
    }
    (void)eu4_border_loop_head_install_ex();
    fprintf(stderr, "install_ex should have aborted\n");
    return 4;
}
