#include "eu4_submission_border_loop_head.h"

#include <mach/mach.h>
#include <mach/mach_vm.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

void eu4_border_loop_head_gateway(void) {}

uint64_t border_loop_draw_target;
uint64_t border_loop_skip_target;

static void *map_executable_page(void) {
    long page = sysconf(_SC_PAGESIZE);
    void *mem = mmap(NULL, (size_t)page, PROT_READ | PROT_WRITE | PROT_EXEC, MAP_PRIVATE | MAP_ANON, -1, 0);
    return mem == MAP_FAILED ? NULL : mem;
}

static void plant_expected_prefix(void *site) {
    const unsigned char expected[14] = {0x44, 0x84, 0x69, 0x01, 0x48, 0x89, 0x4d, 0x90,
                                        0x0f, 0x84, 0x5a, 0x05, 0x00, 0x00};
    memcpy(site, expected, sizeof(expected));
}

static int test_successful_install(void) {
    eu4_border_loop_head_install_test_reset_hooks();
    void *page = map_executable_page();
    if (page == NULL) {
        return 1;
    }
    void *site = (char *)page + 0x40;
    plant_expected_prefix(site);
    eu4_border_loop_head_install_result_t r = eu4_border_loop_head_try_install_at(site, 0x100000000ull);
    if (r != EU4_BORDER_LOOP_HEAD_INSTALL_OK) {
        fprintf(stderr, "expected OK got %d\n", (int)r);
        return 1;
    }
    if (!eu4_border_loop_head_hook_installed()) {
        fprintf(stderr, "hook not installed\n");
        return 1;
    }
    return 0;
}

static int test_rx_fail_clean_rollback(void) {
    eu4_border_loop_head_install_test_reset_hooks();
    eu4_border_loop_head_install_test_force_rx_fail(1);
    void *page = map_executable_page();
    if (page == NULL) {
        return 1;
    }
    void *site = (char *)page + 0x40;
    plant_expected_prefix(site);
    unsigned char saved[14];
    memcpy(saved, site, sizeof(saved));
    eu4_border_loop_head_install_result_t r = eu4_border_loop_head_try_install_at(site, 0x100000000ull);
    if (r != EU4_BORDER_LOOP_HEAD_INSTALL_FAILED_CLEAN) {
        fprintf(stderr, "expected CLEAN fail got %d\n", (int)r);
        return 1;
    }
    if (memcmp(site, saved, sizeof(saved)) != 0) {
        fprintf(stderr, "site bytes not restored\n");
        return 1;
    }
    if (eu4_border_loop_head_install_fatal()) {
        fprintf(stderr, "unexpected fatal\n");
        return 1;
    }
    return 0;
}

static int test_fatal_rollback(void) {
    eu4_border_loop_head_install_test_reset_hooks();
    eu4_border_loop_head_install_test_force_rx_fail(1);
    eu4_border_loop_head_install_test_force_rollback_fail(1);
    void *page = map_executable_page();
    if (page == NULL) {
        return 1;
    }
    void *site = (char *)page + 0x40;
    plant_expected_prefix(site);
    eu4_border_loop_head_install_result_t r = eu4_border_loop_head_try_install_at(site, 0x100000000ull);
    if (r != EU4_BORDER_LOOP_HEAD_INSTALL_FAILED_FATAL) {
        fprintf(stderr, "expected FATAL got %d\n", (int)r);
        return 1;
    }
    if (!eu4_border_loop_head_install_fatal()) {
        fprintf(stderr, "expected fatal sticky\n");
        return 1;
    }
    return 0;
}

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: harness <success|rx_fail|fatal>\n");
        return 1;
    }
    if (strcmp(argv[1], "success") == 0) {
        return test_successful_install();
    }
    if (strcmp(argv[1], "rx_fail") == 0) {
        return test_rx_fail_clean_rollback();
    }
    if (strcmp(argv[1], "fatal") == 0) {
        return test_fatal_rollback();
    }
    return 1;
}
