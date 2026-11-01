#include "../benchmark/eu4_gfx_entry_patch.h"
#include "../benchmark/eu4_metal_boot_patch.h"

#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/wait.h>
#include <unistd.h>

#define GL_SILENCE_DEPRECATION
#include <OpenGL/OpenGL.h>
#include <OpenGL/gl.h>

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
    if (munmap(mapping, page * 2) != 0) {
        fail("two-page munmap failed");
    }
}
#endif

static int entry_hits;
static int call_hits;

static void entry_hit(void) { entry_hits += 1; }
static void call_hit(void) { call_hits += 1; }

int eu4_metal_gl_should_forward(int mode, int phase);
void eu4_metal_gl_test_set_mode(int mode);
void eu4_metal_gl_test_set_phase(int phase);
void (*eu4_metal_gl_test_clear(void))(GLbitfield);
void eu4_metal_events_set_dir(const char *dir);
void eu4_metal_events_set_image(uintptr_t base, uintptr_t end);

extern char gl_clear_ret[];

__attribute__((noinline)) static void clear_callsite(void (*fn)(GLbitfield)) {
    asm volatile("movq %0, %%r11\n\t"
                 "movl $0x4000, %%edi\n\t"
                 "call *%%r11\n\t"
                 ".globl _gl_clear_ret\n\t"
                 "_gl_clear_ret:\n\t"
                 :
                 : "r"(fn)
                 : "r11", "rdi", "memory");
}

static void test_gl_forward(void) {
    if (!eu4_metal_gl_should_forward(0, 0) || !eu4_metal_gl_should_forward(1, 1) ||
        !eu4_metal_gl_should_forward(2, 2)) {
        fail("unset, a1, or bootstrap did not forward");
    }
    if (!eu4_metal_gl_should_forward(3, 0)) {
        fail("before_init did not forward");
    }
    if (eu4_metal_gl_should_forward(3, 1) || eu4_metal_gl_should_forward(3, 2)) {
        fail("metal_init forwarded during or after init");
    }
    void (*wrap)(GLbitfield) = eu4_metal_gl_test_clear();
    void (*real)(GLbitfield) = dlsym(RTLD_NEXT, "glClear");
    Dl_info info;
    memset(&info, 0, sizeof info);
    if (wrap == NULL || real == NULL || real == wrap || dladdr((void *)real, &info) == 0 ||
        info.dli_fname == NULL || strstr(info.dli_fname, "metal_boot_host") != NULL) {
        fail("glClear did not resolve past the wrapper");
    }
    CGLPixelFormatAttribute attrs[] = {kCGLPFAAccelerated, 0};
    CGLPixelFormatObj format = NULL;
    GLint count = 0;
    CGLContextObj context = NULL;
    if (CGLChoosePixelFormat(attrs, &format, &count) != kCGLNoError || format == NULL ||
        CGLCreateContext(format, NULL, &context) != kCGLNoError ||
        CGLSetCurrentContext(context) != kCGLNoError) {
        fail("could not make a GL context");
    }
    real(0);
    char dir[] = "/tmp/eu4-gl-XXXXXX";
    if (mkdtemp(dir) == NULL) {
        fail("gl temp dir");
    }
    eu4_metal_events_set_dir(dir);
    Dl_info self;
    if (dladdr((void *)clear_callsite, &self) == 0 || self.dli_fbase == NULL) {
        fail("callsite image");
    }
    eu4_metal_events_set_image((uintptr_t)self.dli_fbase, (uintptr_t)self.dli_fbase + 0x10000000);
    const int modes[] = {0, 1, 2};
    for (int i = 0; i < 3; i++) {
        eu4_metal_gl_test_set_mode(modes[i]);
        wrap(0);
    }
    char stop[64];
    snprintf(stop, sizeof stop, "%s/stop.json", dir);
    if (access(stop, F_OK) == 0) {
        fail("forwarding wrote stop.json");
    }
    eu4_metal_gl_test_set_phase(0);
    eu4_metal_gl_test_set_mode(3);
    wrap(0);
    if (access(stop, F_OK) == 0) {
        fail("before_init wrote stop.json");
    }
    pid_t child = fork();
    if (child < 0) {
        fail("fork");
    }
    if (child == 0) {
        eu4_metal_gl_test_set_mode(3);
        eu4_metal_gl_test_set_phase(2);
        clear_callsite(wrap);
        _exit(99);
    }
    int status = 0;
    if (waitpid(child, &status, 0) < 0 || !WIFEXITED(status) || WEXITSTATUS(status) != 2) {
        fail("diagnostic child did not exit 2");
    }
    FILE *file = fopen(stop, "r");
    char body[1024];
    if (file == NULL || fgets(body, sizeof body, file) == NULL) {
        fail("stop.json missing");
    }
    fclose(file);
    char expected[64];
    snprintf(expected, sizeof expected, "\"address\":\"0x%llx\"", (unsigned long long)(uintptr_t)gl_clear_ret);
    if (strstr(body, expected) == NULL || strstr(body, "\"rva\":null") != NULL) {
        fail("stop.json did not record the host callsite");
    }
}

static uint8_t *map_entries(size_t page, eu4_gfx_entry_request entries[6],
                            eu4_metal_boot_request calls[2], uint8_t prefixes[6][6],
                            uint8_t call_expected[2][5]) {
    uint8_t *mapping = mmap(NULL, page * 2, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
    if (mapping == MAP_FAILED) {
        fail("entry mmap failed");
    }
    static const uint8_t prefix6[6] = {0x55, 0x48, 0x89, 0xe5, 0x41, 0x57};
    static const uint8_t prefix5[5] = {0x55, 0x48, 0x89, 0xe5, 0x53};
    for (int i = 0; i < 6; i++) {
        int length = i == 5 ? 5 : 6;
        memset(prefixes[i], 0, 6);
        memcpy(prefixes[i], i == 5 ? prefix5 : prefix6, (size_t)length);
        memcpy(mapping + (size_t)i * 16, prefixes[i], (size_t)length);
        mapping[(size_t)i * 16 + (size_t)length] = 0xc3;
        entries[i].site = mapping + (size_t)i * 16;
        entries[i].expected = prefixes[i];
        entries[i].length = length;
        entries[i].replacement = (void *)entry_hit;
    }
    void *sites[2] = {mapping + 256, mapping + page};
    uint8_t *stubs[2] = {mapping + 320, mapping + page + 64};
    for (int i = 0; i < 2; i++) {
        stubs[i][0] = 0xc3;
        if (!eu4_metal_boot_encode_call(sites[i], stubs[i], call_expected[i])) {
            fail("could not encode a call fixture");
        }
        memcpy(sites[i], call_expected[i], 5);
        ((uint8_t *)sites[i])[5] = 0xc3;
        calls[i].callsite = sites[i];
        calls[i].expected = call_expected[i];
        calls[i].wrapper = (void *)call_hit;
        calls[i].expected_target = stubs[i];
    }
    if (mprotect(mapping, page * 2, PROT_READ | PROT_EXEC) != 0) {
        fail("entry mprotect failed");
    }
    return mapping;
}

static void test_entry_transaction(void) {
    size_t page = (size_t)sysconf(_SC_PAGESIZE);
    eu4_gfx_entry_request entries[6];
    eu4_metal_boot_request calls[2];
    uint8_t prefixes[6][6];
    uint8_t call_expected[2][5];
    uint8_t *mapping = map_entries(page, entries, calls, prefixes, call_expected);
    uint8_t bad[6];
    memcpy(bad, prefixes[0], 6);
    bad[0] = 0;
    entries[0].expected = bad;
    eu4_metal_boot_patch patches[2] = {0};
    if (eu4_gfx_install_all(entries, 6, calls, 2, patches)) {
        fail("bad entry bytes were patched");
    }
    if (memcmp(entries[0].site, prefixes[0], 6) != 0) {
        fail("rejected bytes changed an entry");
    }
    entries[0].expected = prefixes[0];
    if (!eu4_gfx_entry_prepare(entries, 6)) {
        fail("prepare failed");
    }
    if (memcmp(entries[3].site, prefixes[3], 6) != 0) {
        fail("prepare wrote an entry");
    }
    if (!eu4_gfx_entry_restore()) {
        fail("prepared-only restore failed");
    }
    if (eu4_gfx_entry_commit()) {
        fail("commit ran without a prepare");
    }
    eu4_gfx_entry_test_fail_relay(1);
    eu4_gfx_entry_request far = entries[0];
    far.replacement = (void *)((uintptr_t)far.site + 0x100000000ULL);
    if (eu4_gfx_entry_prepare(&far, 1)) {
        fail("missing relay was prepared");
    }
    eu4_gfx_entry_test_fail_relay(0);
    if (memcmp(entries[0].site, prefixes[0], 6) != 0) {
        fail("missing relay wrote an entry");
    }
    if (!eu4_gfx_install_all(entries, 6, calls, 2, patches)) {
        fail("five-step install failed");
    }
    entry_hits = 0;
    call_hits = 0;
    for (int i = 0; i < 6; i++) {
        ((void (*)(void))entries[i].site)();
    }
    ((void (*)(void))calls[0].callsite)();
    ((void (*)(void))calls[1].callsite)();
    if (entry_hits != 6 || call_hits != 2) {
        fail("installed sites did not reach the replacements");
    }
    if (!eu4_gfx_entry_restore() || !eu4_metal_boot_restore()) {
        fail("could not restore a successful install");
    }
    eu4_gfx_entry_test_fail_commit(1);
    if (eu4_gfx_install_all(entries, 6, calls, 2, patches)) {
        fail("failed commit reported success");
    }
    eu4_gfx_entry_test_fail_commit(0);
    for (int i = 0; i < 6; i++) {
        if (memcmp(entries[i].site, prefixes[i], (size_t)entries[i].length) != 0) {
            fail("failed commit left an entry patched");
        }
    }
    if (memcmp(calls[0].callsite, calls[0].expected, 5) != 0 ||
        memcmp(calls[1].callsite, calls[1].expected, 5) != 0) {
        fail("failed commit left a call patched");
    }
    if (eu4_metal_boot_restore()) {
        fail("failed commit left the call install committed");
    }
    if (munmap(mapping, page * 2) != 0) {
        fail("entry munmap failed");
    }
}

#ifdef EU4_METAL_BOOT_TEST_FAIL
static void test_failed_call_once(void) {
    size_t page = (size_t)sysconf(_SC_PAGESIZE);
    eu4_gfx_entry_request entries[6];
    eu4_metal_boot_request calls[2];
    uint8_t prefixes[6][6];
    uint8_t call_expected[2][5];
    uint8_t *mapping = map_entries(page, entries, calls, prefixes, call_expected);
    eu4_metal_boot_patch patches[2] = {0};
    if (eu4_gfx_install_all(entries, 6, calls, 2, patches)) {
        fail("injected call failure still installed");
    }
    for (int i = 0; i < 6; i++) {
        if (memcmp(entries[i].site, prefixes[i], (size_t)entries[i].length) != 0) {
            fail("call failure changed an entry");
        }
    }
    if (memcmp(calls[0].callsite, calls[0].expected, 5) != 0 ||
        memcmp(calls[1].callsite, calls[1].expected, 5) != 0) {
        fail("call failure left a call patched");
    }
    if (eu4_metal_boot_restore()) {
        fail("rolled-back call install was restored again");
    }
    if (munmap(mapping, page * 2) != 0) {
        fail("entry munmap failed");
    }
}
#endif

#ifdef EU4_METAL_BOOT_TEST_FAIL
int main(void) {
    test_rollback();
    test_failed_call_once();
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
    test_gl_forward();
    test_entry_transaction();
    return 0;
}
#endif
