#include "eu4_gfx_site_patch.h"

#include <libkern/OSCacheControl.h>
#include <limits.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
#include <stdatomic.h>
#include <stdint.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

static size_t page_size;
static _Atomic int g_fail_next_rx;

static size_t page_bytes(void) {
    if (page_size == 0) {
        long value = sysconf(_SC_PAGESIZE);
        if (value > 0) {
            page_size = (size_t)value;
        }
    }
    return page_size;
}

static bool set_site_protection(uintptr_t begin, vm_prot_t protection) {
    return mach_vm_protect(mach_task_self(), (mach_vm_address_t)begin,
                           (mach_vm_size_t)page_bytes(), false,
                           protection) == KERN_SUCCESS;
}

static bool set_site_rw(uintptr_t begin) {
    return set_site_protection(
        begin, VM_PROT_READ | VM_PROT_WRITE | VM_PROT_EXECUTE | VM_PROT_COPY);
}

static bool set_site_rx(uintptr_t begin) {
    if (atomic_exchange_explicit(&g_fail_next_rx, 0, memory_order_relaxed)) {
        return false;
    }
    return set_site_protection(begin, VM_PROT_READ | VM_PROT_EXECUTE);
}

void eu4_gfx_site_test_fail_next_rx(void) {
    atomic_store_explicit(&g_fail_next_rx, 1, memory_order_relaxed);
}

bool eu4_gfx_site_rel32_fits(const void *site, const void *target) {
    if (site == NULL || target == NULL) {
        return false;
    }
    int64_t delta = (int64_t)(intptr_t)target - ((int64_t)(intptr_t)site + 5);
    return delta >= INT32_MIN && delta <= INT32_MAX;
}

static void *allocate_near(uintptr_t site) {
    static const int64_t offsets[] = {
        0x01000000, -0x01000000, 0x04000000, -0x04000000,
        0x10000000, -0x10000000, 0x40000000, -0x40000000,
    };
    size_t page = page_bytes();
    if (page == 0) {
        return NULL;
    }
    for (size_t i = 0; i < sizeof(offsets) / sizeof(offsets[0]); i++) {
        int64_t candidate = (int64_t)site + offsets[i];
        if (candidate <= 0) {
            continue;
        }
        uintptr_t hint = (uintptr_t)candidate & ~((uintptr_t)page - 1);
        void *mapping = mmap((void *)hint, page, PROT_READ | PROT_WRITE,
                             MAP_PRIVATE | MAP_ANON, -1, 0);
        if (mapping == MAP_FAILED) {
            continue;
        }
        if (eu4_gfx_site_rel32_fits((const void *)site, mapping)) {
            return mapping;
        }
        (void)munmap(mapping, page);
    }
    return NULL;
}

static bool write_relay(void *relay, void *gateway) {
    uint8_t *p = (uint8_t *)relay;
    p[0] = 0xff;
    p[1] = 0x25;
    p[2] = 0x00;
    p[3] = 0x00;
    p[4] = 0x00;
    p[5] = 0x00;
    memcpy(p + 6, &gateway, sizeof(gateway));
    sys_icache_invalidate(p, 14);
    return mprotect(relay, page_bytes(), PROT_READ | PROT_EXEC) == 0;
}

static bool write_trampoline(void *trampoline, const void *site, int length) {
    uint8_t *p = (uint8_t *)trampoline;
    memcpy(p, site, (size_t)length);
    p[length] = 0xff;
    p[length + 1] = 0x25;
    p[length + 2] = 0x00;
    p[length + 3] = 0x00;
    p[length + 4] = 0x00;
    p[length + 5] = 0x00;
    uintptr_t continuation = (uintptr_t)site + (uintptr_t)length;
    memcpy(p + length + 6, &continuation, sizeof(continuation));
    sys_icache_invalidate(p, (size_t)length + 14);
    return mprotect(trampoline, page_bytes(), PROT_READ | PROT_EXEC) == 0;
}

bool eu4_gfx_site_patch(eu4_gfx_site *site, void *address,
                        const uint8_t *expected, int length, void *gateway,
                        void **trampoline_slot) {
    if (site == NULL || address == NULL || expected == NULL || gateway == NULL ||
        trampoline_slot == NULL || site->installed || length < 5 ||
        length > EU4_GFX_SITE_MAX_PATCH) {
        return false;
    }
    size_t page = page_bytes();
    if (page == 0) {
        return false;
    }
    uintptr_t target = (uintptr_t)address;
    uintptr_t begin = target & ~((uintptr_t)page - 1);
    if (target + (uintptr_t)length > begin + page) {
        return false;
    }
    if (memcmp(address, expected, (size_t)length) != 0) {
        return false;
    }

    void *relay = allocate_near(target);
    if (relay == NULL) {
        return false;
    }
    if (!write_relay(relay, gateway)) {
        (void)munmap(relay, page);
        return false;
    }

    void *trampoline = mmap(NULL, page, PROT_READ | PROT_WRITE,
                            MAP_PRIVATE | MAP_ANON, -1, 0);
    if (trampoline == MAP_FAILED) {
        (void)munmap(relay, page);
        return false;
    }
    if (!write_trampoline(trampoline, address, length)) {
        (void)munmap(trampoline, page);
        (void)munmap(relay, page);
        return false;
    }

    if (!set_site_rw(begin)) {
        (void)munmap(trampoline, page);
        (void)munmap(relay, page);
        return false;
    }

    site->address = target;
    site->length = length;
    site->relay_page = relay;
    site->trampoline_page = trampoline;
    site->trampoline_slot = trampoline_slot;
    site->gateway = gateway;
    memcpy(site->saved, address, (size_t)length);
    *trampoline_slot = trampoline;
    site->written = true;

    uint8_t *patch = (uint8_t *)address;
    patch[0] = 0xe9;
    int64_t delta = (int64_t)(intptr_t)relay - ((int64_t)target + 5);
    int32_t relative = (int32_t)delta;
    memcpy(patch + 1, &relative, sizeof(relative));
    memset(patch + 5, 0x90, (size_t)length - 5);
    sys_icache_invalidate(address, (size_t)length);

    if (!set_site_rx(begin)) {
        if (!eu4_gfx_site_restore(site)) {
            /* The patch is still live; keep the relay mapped and let the
               caller abort the session. */
            return false;
        }
        eu4_gfx_site_release(site);
        return false;
    }

    site->installed = true;
    return true;
}

bool eu4_gfx_site_restore(eu4_gfx_site *site) {
    if (site == NULL || site->address == 0 ||
        (!site->installed && !site->written)) {
        return true;
    }
    size_t page = page_bytes();
    if (page == 0) {
        return false;
    }
    uintptr_t begin = site->address & ~((uintptr_t)page - 1);
    if (!set_site_rw(begin)) {
        return false;
    }
    memcpy((void *)site->address, site->saved, (size_t)site->length);
    sys_icache_invalidate((void *)site->address, (size_t)site->length);
    if (!set_site_rx(begin)) {
        return false;
    }
    site->installed = false;
    site->written = false;
    return true;
}

void eu4_gfx_site_release(eu4_gfx_site *site) {
    if (site == NULL || site->installed || site->written) {
        return;
    }
    size_t page = page_bytes();
    if (page != 0) {
        if (site->trampoline_page != NULL) {
            (void)munmap(site->trampoline_page, page);
        }
        if (site->relay_page != NULL) {
            (void)munmap(site->relay_page, page);
        }
    }
    if (site->trampoline_slot != NULL) {
        *site->trampoline_slot = NULL;
    }
    memset(site, 0, sizeof(*site));
}
