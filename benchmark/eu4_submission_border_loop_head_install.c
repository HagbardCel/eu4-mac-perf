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
static bool loop_head_install_fatal_sticky = false;
static uintptr_t loop_site = 0;
static unsigned char loop_saved[14];

static int test_force_rx_fail = -1;
static int test_force_rollback_fail = -1;

void eu4_border_loop_head_install_test_reset_hooks(void) {
    test_force_rx_fail = -1;
    test_force_rollback_fail = -1;
}

void eu4_border_loop_head_install_test_force_rx_fail(int attempts) {
    test_force_rx_fail = attempts;
}

void eu4_border_loop_head_install_test_force_rollback_fail(int attempts) {
    test_force_rollback_fail = attempts;
}

bool eu4_border_loop_head_install_fatal(void) {
    return loop_head_install_fatal_sticky;
}

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
    if (test_force_rx_fail > 0) {
        test_force_rx_fail--;
        return false;
    }
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
    if (test_force_rollback_fail > 0) {
        test_force_rollback_fail--;
        return false;
    }
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

static void clear_continuation_targets(void) {
    border_loop_draw_target = 0;
    border_loop_skip_target = 0;
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

eu4_border_loop_head_install_result_t eu4_border_loop_head_try_install_at(void *site, uintptr_t image_base) {
    const unsigned char expected[14] = {0x44, 0x84, 0x69, 0x01, 0x48, 0x89, 0x4d, 0x90,
                                        0x0f, 0x84, 0x5a, 0x05, 0x00, 0x00};
    if (memcmp(site, expected, sizeof(expected)) != 0) {
        return EU4_BORDER_LOOP_HEAD_INSTALL_FAILED_CLEAN;
    }
    border_loop_draw_target = image_base + 0x10cbe63u;
    border_loop_skip_target = image_base + 0x10cc3bdu;
    uintptr_t address = (uintptr_t)site;
    uintptr_t begin = 0;
    if (!page_begin(address, &begin) || !set_page_rw(begin)) {
        clear_continuation_targets();
        return EU4_BORDER_LOOP_HEAD_INSTALL_FAILED_CLEAN;
    }
    memcpy(loop_saved, site, sizeof(loop_saved));
    loop_site = address;
    write_rip_indirect_jmp14(site, (void *)eu4_border_loop_head_gateway);
    sys_icache_invalidate(site, 14);
    if (!set_page_rx(begin)) {
        if (!rollback_patched_site()) {
            loop_head_install_fatal_sticky = true;
            return EU4_BORDER_LOOP_HEAD_INSTALL_FAILED_FATAL;
        }
        clear_continuation_targets();
        return EU4_BORDER_LOOP_HEAD_INSTALL_FAILED_CLEAN;
    }
    loop_head_installed = true;
    return EU4_BORDER_LOOP_HEAD_INSTALL_OK;
}

bool eu4_border_loop_head_hook_installed(void) {
    if (loop_head_install_fatal_sticky) {
        return false;
    }
    const char *stub = getenv("EU4_SUBMISSION_TEST_STUB_HOOK");
    if (stub != NULL && stub[0] == '1') {
        return true;
    }
    return loop_head_installed;
}

eu4_border_loop_head_install_result_t eu4_border_loop_head_install_ex(void) {
    if (loop_head_install_fatal_sticky) {
        abort();
    }
    if (loop_head_installed) {
        return EU4_BORDER_LOOP_HEAD_INSTALL_OK;
    }
    const char *stub = getenv("EU4_SUBMISSION_TEST_STUB_HOOK");
    if (stub != NULL && stub[0] == '1') {
        return EU4_BORDER_LOOP_HEAD_INSTALL_OK;
    }
    void *anchor = dlsym(
        RTLD_DEFAULT,
        "_ZN18CPdxMapBorderLayer11DrawBordersEP21GfxDeferredContextGFXPK10CEU3CameraR18SBorderDrawInfoSet6CArrayIiEiifP19CWrapWorldShadowMapc");
    if (anchor == NULL) {
        return EU4_BORDER_LOOP_HEAD_INSTALL_FAILED_CLEAN;
    }
    Dl_info info;
    if (!dladdr(anchor, &info) || info.dli_fbase == NULL) {
        return EU4_BORDER_LOOP_HEAD_INSTALL_FAILED_CLEAN;
    }
    uintptr_t image_base = (uintptr_t)info.dli_fbase;
    void *site = (void *)(image_base + 0x10cbe55u);
    eu4_border_loop_head_install_result_t result = eu4_border_loop_head_try_install_at(site, image_base);
    if (result == EU4_BORDER_LOOP_HEAD_INSTALL_FAILED_CLEAN) {
        (void)rollback_patched_site();
        clear_continuation_targets();
    }
    return result;
}

bool eu4_border_loop_head_install(void) {
    return eu4_border_loop_head_install_ex() == EU4_BORDER_LOOP_HEAD_INSTALL_OK;
}
