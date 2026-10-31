#include "../benchmark/eu4_metal_boot_patch.h"

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

static void fail(const char *message) {
    fprintf(stderr, "%s\n", message);
    exit(1);
}

#ifndef EU4_METAL_BOOT_TEST_FAIL
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
    return original(a, b, c, d, e, flags & ~2);
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
#endif

static int four_hits;

static void hit0(void) { four_hits += 1; }
static void hit1(void) { four_hits += 2; }
static void hit2(void) { four_hits += 4; }
static void hit3(void) { four_hits += 8; }

static int place_four(uint8_t *mapping, size_t page, void *sites[4], void *stubs[4],
                      uint8_t expected[4][5]) {
    sites[0] = mapping;
    sites[1] = mapping + 8;
    sites[2] = mapping + page;
    sites[3] = mapping + page + 8;
    stubs[0] = mapping + 64;
    stubs[1] = mapping + 72;
    stubs[2] = mapping + page + 64;
    stubs[3] = mapping + page + 72;
    for (int i = 0; i < 4; i++) {
        ((uint8_t *)stubs[i])[0] = 0xc3;
        if (!eu4_metal_boot_encode_call(sites[i], stubs[i], expected[i])) {
            return 0;
        }
        memcpy(sites[i], expected[i], 5);
        ((uint8_t *)sites[i])[5] = 0xc3;
    }
    return mprotect(mapping, page * 2, PROT_READ | PROT_EXEC) == 0;
}

static void fill_four(void *sites[4], void *stubs[4], uint8_t expected[4][5],
                      eu4_metal_boot_request requests[4]) {
    void (*wraps[4])(void) = {hit0, hit1, hit2, hit3};
    for (int i = 0; i < 4; i++) {
        requests[i].callsite = sites[i];
        requests[i].expected = expected[i];
        requests[i].wrapper = (void *)wraps[i];
        requests[i].expected_target = stubs[i];
    }
}

static uint8_t *map_two_pages(size_t page) {
    uint8_t *mapping = mmap(NULL, page * 2, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
    if (mapping == MAP_FAILED) {
        fail("two-page mmap failed");
    }
    return mapping;
}

#ifdef EU4_METAL_BOOT_TEST_FAIL
static void test_rollback(void) {
    size_t page = (size_t)sysconf(_SC_PAGESIZE);
    uint8_t *mapping = map_two_pages(page);
    void *sites[4];
    void *stubs[4];
    uint8_t expected[4][5];
    if (!place_four(mapping, page, sites, stubs, expected)) {
        fail("could not place four callsites");
    }
    eu4_metal_boot_request requests[4];
    eu4_metal_boot_patch patches[4];
    fill_four(sites, stubs, expected, requests);
    if (eu4_metal_boot_install_sites(requests, 4, patches)) {
        fail("injected failure still installed");
    }
    for (int i = 0; i < 4; i++) {
        if (memcmp(sites[i], expected[i], 5) != 0) {
            fail("rollback left a callsite patched");
        }
    }
    if (eu4_metal_boot_restore()) {
        fail("failed install stayed committed");
    }
    if (munmap(mapping, page * 2) != 0) {
        fail("two-page munmap failed");
    }
}
#endif

#ifndef EU4_METAL_BOOT_TEST_FAIL
static void test_four(void) {
    size_t page = (size_t)sysconf(_SC_PAGESIZE);
    uint8_t *mapping = map_two_pages(page);
    void *sites[4];
    void *stubs[4];
    uint8_t expected[4][5];
    if (!place_four(mapping, page, sites, stubs, expected)) {
        fail("could not place four callsites");
    }
    eu4_metal_boot_request requests[4];
    eu4_metal_boot_patch patches[4];
    fill_four(sites, stubs, expected, requests);
    uint8_t bad[5];
    memcpy(bad, expected[2], 5);
    bad[1] ^= 0xff;
    requests[2].expected = bad;
    if (eu4_metal_boot_install_sites(requests, 4, patches)) {
        fail("bad third site was installed");
    }
    requests[2].expected = expected[2];
    memcpy(bad, expected[3], 5);
    bad[1] ^= 0xff;
    requests[3].expected = bad;
    if (eu4_metal_boot_install_sites(requests, 4, patches)) {
        fail("bad fourth site was installed");
    }
    requests[3].expected = expected[3];
    for (int i = 0; i < 4; i++) {
        if (memcmp(sites[i], expected[i], 5) != 0) {
            fail("rejected preflight changed a callsite");
        }
    }
    if (!eu4_metal_boot_install_sites(requests, 4, patches)) {
        fail("four-site install failed");
    }
    four_hits = 0;
    for (int i = 0; i < 4; i++) {
        ((void (*)(void))sites[i])();
    }
    if (four_hits != 15) {
        fail("four wrappers did not run");
    }
    if (!eu4_metal_boot_restore()) {
        fail("four-site restore failed");
    }
    four_hits = 0;
    for (int i = 0; i < 4; i++) {
        if (memcmp(sites[i], expected[i], 5) != 0) {
            fail("four-site restore left a callsite changed");
        }
        ((void (*)(void))sites[i])();
    }
    if (four_hits != 0) {
        fail("restored four-site still entered a wrapper");
    }
    if (munmap(mapping, page * 2) != 0) {
        fail("two-page munmap failed");
    }
}
#endif

#ifdef EU4_METAL_BOOT_TEST_FAIL
int main(void) {
    test_rollback();
    return 0;
}
#else
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
    if (target_flags != (0x6 & ~2) || seen != target_flags) {
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
    test_four();
    return 0;
}
#endif
