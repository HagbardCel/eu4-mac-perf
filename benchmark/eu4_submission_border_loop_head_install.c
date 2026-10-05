#include "eu4_submission_border_loop_head.h"

#include <dlfcn.h>
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
extern uint64_t border_loop_draw_target;
extern uint64_t border_loop_skip_target;

#define RX_RESTORE_MAX_ATTEMPTS 3

static bool loop_head_installed = false;
static uintptr_t loop_site = 0;
static unsigned char loop_saved[14];

static bool page_begin(uintptr_t address, uintptr_t *begin_out) {
    long page = sysconf(_SC_PAGESIZE);
    if (page <= 0) {
        return false;
    }
    *begin_out = address & ~((uintptr_t)page - 1);
    return true;
}

static bool set_page_rw(uintptr_t begin) {
    long page = sysconf(_SC_PAGESIZE);
    return mach_vm_protect(
               mach_task_self(), begin, (mach_vm_size_t)page, false, VM_PROT_READ | VM_PROT_WRITE | VM_PROT_COPY) ==
           KERN_SUCCESS;
}

static bool set_page_rx(uintptr_t begin) {
    long page = sysconf(_SC_PAGESIZE);
    return mach_vm_protect(mach_task_self(), begin, (mach_vm_size_t)page, false, VM_PROT_READ | VM_PROT_EXECUTE) ==
           KERN_SUCCESS;
}

static bool restore_page_rx_bounded(uintptr_t begin) {
    for (int attempt = 0; attempt < RX_RESTORE_MAX_ATTEMPTS; attempt++) {
        if (set_page_rx(begin)) {
            return true;
        }
    }
    return false;
}

static bool rollback_patched_site(void) {
    if (loop_site == 0) {
        return true;
    }
    uintptr_t begin = 0;
    if (!page_begin(loop_site, &begin)) {
        return false;
    }
    if (!set_page_rw(begin)) {
        return false;
    }
    memcpy((void *)loop_site, loop_saved, sizeof(loop_saved));
    sys_icache_invalidate((void *)loop_site, sizeof(loop_saved));
    if (!restore_page_rx_bounded(begin)) {
        return false;
    }
    loop_site = 0;
    loop_head_installed = false;
    return true;
}

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

static bool install_loop_head_at(void *site, uintptr_t image_base) {
    const unsigned char expected[14] = {0x44, 0x84, 0x69, 0x01, 0x48, 0x89, 0x4d, 0x90,
                                        0x0f, 0x84, 0x5a, 0x05, 0x00, 0x00};
    if (memcmp(site, expected, sizeof(expected)) != 0) {
        return false;
    }
    border_loop_draw_target = image_base + 0x10cbe63u;
    border_loop_skip_target = image_base + 0x10cc3bdu;
    uintptr_t address = (uintptr_t)site;
    uintptr_t begin = 0;
    if (!page_begin(address, &begin) || !set_page_rw(begin)) {
        return false;
    }
    memcpy(loop_saved, site, sizeof(loop_saved));
    loop_site = address;
    write_rip_indirect_jmp14(site, (void *)eu4_border_loop_head_gateway);
    sys_icache_invalidate(site, 14);
    if (!set_page_rx(begin)) {
        (void)rollback_patched_site();
        border_loop_draw_target = 0;
        border_loop_skip_target = 0;
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
    if (!install_loop_head_at(site, image_base)) {
        (void)rollback_patched_site();
        border_loop_draw_target = 0;
        border_loop_skip_target = 0;
        return false;
    }
    return true;
}
