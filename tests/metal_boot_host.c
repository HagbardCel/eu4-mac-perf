#include "../benchmark/eu4_metal_boot_patch.h"

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

static int target_flags = -1;
static int target_calls;
static int wrapper_calls;
static eu4_metal_boot_patch installed;

int metal_boot_target(int a, int b, int c, int d, int e, int flags) {
    (void)a;
    (void)b;
    (void)c;
    (void)d;
    (void)e;
    target_flags = flags;
    target_calls += 1;
    return flags;
}

__asm__(".globl _metal_boot_site\n"
        "_metal_boot_site:\n"
        "call _metal_boot_target\n"
        "ret\n");

extern int metal_boot_site(int, int, int, int, int, int);

int metal_boot_wrapper(int a, int b, int c, int d, int e, int flags) {
    int (*original)(int, int, int, int, int, int) = installed.original;
    wrapper_calls += 1;
    return original(a, b, c, d, e, (flags & ~2) | 0x20000000);
}

static void fail(const char *message) {
    fprintf(stderr, "%s\n", message);
    exit(1);
}

static int relay_hits;

static void relay_target(void) { relay_hits = 1; }

static void test_relay(void) {
    long page = sysconf(_SC_PAGESIZE);
    uint8_t *mapping = mmap(NULL, (size_t)page, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
    if (mapping == MAP_FAILED) {
        fail("relay mmap failed");
    }
    eu4_metal_boot_encode_relay(mapping, relay_target);
    uint8_t *call = mapping + 32;
    if (!eu4_metal_boot_encode_call(call, mapping, call)) {
        fail("E8 to the relay did not encode");
    }
    call[5] = 0xc3;
    if (mprotect(mapping, (size_t)page, PROT_READ | PROT_EXEC) != 0) {
        fail("relay mprotect failed");
    }
    ((void (*)(void))call)();
    if (relay_hits != 1) {
        fail("relay jump did not reach the target");
    }
    if (munmap(mapping, (size_t)page) != 0) {
        fail("relay munmap failed");
    }
    uint8_t unused[5];
    if (eu4_metal_boot_encode_call((void *)(uintptr_t)0x1000, (void *)(uintptr_t)0x200000000ULL,
                                   unused)) {
        fail("out-of-range E8 was accepted");
    }
}

int main(void) {
    test_relay();
    uint8_t expected[5];
    memcpy(expected, (void *)metal_boot_site, 5);
    if (expected[0] != 0xe8) {
        fail("fixture call is not E8");
    }
    uint8_t wrong[5];
    memcpy(wrong, expected, 5);
    wrong[1] ^= 0xff;
    eu4_metal_boot_patch ignored = {0};
    if (eu4_metal_boot_install((void *)metal_boot_site, wrong, metal_boot_target, metal_boot_wrapper,
                               &ignored)) {
        fail("mismatched bytes were patched");
    }
    if (memcmp((void *)metal_boot_site, expected, 5) != 0) {
        fail("mismatched install changed the callsite");
    }
    if (eu4_metal_boot_install((void *)metal_boot_site, expected, (void *)1, metal_boot_wrapper,
                               &ignored)) {
        fail("wrong callee was patched");
    }
    if (memcmp((void *)metal_boot_site, expected, 5) != 0) {
        fail("wrong callee changed the callsite");
    }
    if (!eu4_metal_boot_install((void *)metal_boot_site, expected, metal_boot_target,
                                metal_boot_wrapper, &installed)) {
        fail("install failed");
    }
    if (installed.original != (void *)metal_boot_target) {
        fail("decoded target is not the original function");
    }
    int seen = metal_boot_site(1, 2, 3, 4, 5, 0x6);
    if (wrapper_calls != 1 || target_calls != 1) {
        fail("wrapper did not call the original");
    }
    if (target_flags != ((0x6 & ~2) | 0x20000000) || seen != target_flags) {
        fail("sixth argument was not rewritten");
    }
    if (!eu4_metal_boot_restore()) {
        fail("restore failed");
    }
    if (memcmp((void *)metal_boot_site, expected, 5) != 0) {
        fail("restore left the callsite changed");
    }
    wrapper_calls = 0;
    target_calls = 0;
    metal_boot_site(1, 2, 3, 4, 5, 0x6);
    if (wrapper_calls != 0 || target_calls != 1 || target_flags != 0x6) {
        fail("restored callsite did not reach the original unchanged");
    }
    return 0;
}
