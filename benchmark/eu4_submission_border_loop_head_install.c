#include "eu4_submission_border_loop_head.h"

#include <dlfcn.h>
#include <stdlib.h>
#include <libkern/OSCacheControl.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

extern void eu4_border_loop_head_gateway(void);
extern uint64_t eu4_border_loop_head_trampoline_ptr;

static bool loop_head_installed = false;
static void *loop_trampoline = NULL;
static uintptr_t loop_site = 0;
static unsigned char loop_saved[14];

static bool write_rip_indirect_jmp14(void *site, void *target) {
    unsigned char *p = (unsigned char *)site;
    p[0] = 0xff;
    p[1] = 0x25;
    p[2] = 0x00;
    p[3] = 0x00;
    p[4] = 0x00;
    p[5] = 0x00;
    memcpy(p + 6, &target, sizeof(void *));
    return true;
}

static bool install_loop_head_at(void *site) {
    const unsigned char expected[14] = {0x44, 0x84, 0x69, 0x01, 0x48, 0x89, 0x4d, 0x90,
                                        0x0f, 0x84, 0x5a, 0x05, 0x00, 0x00};
    if (memcmp(site, expected, sizeof(expected)) != 0) {
        return false;
    }
    long page = sysconf(_SC_PAGESIZE);
    uintptr_t address = (uintptr_t)site;
    uintptr_t begin = address & ~((uintptr_t)page - 1);
    if (mach_vm_protect(
            mach_task_self(), begin, (mach_vm_size_t)page, false, VM_PROT_READ | VM_PROT_WRITE | VM_PROT_COPY) !=
        KERN_SUCCESS) {
        return false;
    }
    loop_trampoline = mmap(NULL, 4096, PROT_READ | PROT_WRITE | PROT_EXEC, MAP_PRIVATE | MAP_ANON, -1, 0);
    if (loop_trampoline == MAP_FAILED) {
        (void)mach_vm_protect(mach_task_self(), begin, (mach_vm_size_t)page, false, VM_PROT_READ | VM_PROT_EXECUTE);
        return false;
    }
    memcpy(loop_saved, site, sizeof(loop_saved));
    memcpy(loop_trampoline, site, sizeof(loop_saved));
    const uintptr_t resume = address + sizeof(loop_saved);
    unsigned char *tramp_tail = (unsigned char *)loop_trampoline + sizeof(loop_saved);
    tramp_tail[0] = 0xff;
    tramp_tail[1] = 0x25;
    tramp_tail[2] = 0x00;
    tramp_tail[3] = 0x00;
    tramp_tail[4] = 0x00;
    tramp_tail[5] = 0x00;
    memcpy(tramp_tail + 6, &resume, sizeof(void *));
    sys_icache_invalidate(loop_trampoline, sizeof(loop_saved) + 14);

    eu4_border_loop_head_trampoline_ptr = (uint64_t)(uintptr_t)loop_trampoline;
    loop_site = address;
    write_rip_indirect_jmp14(site, (void *)eu4_border_loop_head_gateway);
    sys_icache_invalidate(site, 14);
    if (mach_vm_protect(mach_task_self(), begin, (mach_vm_size_t)page, false, VM_PROT_READ | VM_PROT_EXECUTE) !=
        KERN_SUCCESS) {
        return false;
    }
    loop_head_installed = true;
    return true;
}

bool eu4_border_loop_head_hook_installed(void) {
    const char *stub = getenv("EU4_SUBMISSION_TEST_STUB_HOOK");
    if (stub != NULL && stub[0] == '1') {
        return true;
    }
    return loop_head_installed;
}

bool eu4_border_loop_head_install(void) {
    if (loop_head_installed) {
        return true;
    }
    const char *stub = getenv("EU4_SUBMISSION_TEST_STUB_HOOK");
    if (stub != NULL && stub[0] == '1') {
        return true;
    }
    void *anchor = dlsym(
        RTLD_DEFAULT,
        "_ZN18CPdxMapBorderLayer11DrawBordersEP21GfxDeferredContextGFXPK10CEU3CameraR18SBorderDrawInfoSet6CArrayIiEiifP19CWrapWorldShadowMapc");
    if (anchor == NULL) {
        return false;
    }
    Dl_info info;
    if (!dladdr(anchor, &info) || info.dli_fbase == NULL) {
        return false;
    }
    uintptr_t image_base = (uintptr_t)info.dli_fbase;
    void *site = (void *)(image_base + 0x10cbe55u);
    return install_loop_head_at(site);
}
