#include "eu4_gfx_startup_trace.h"

#include <libkern/OSCacheControl.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
#include <limits.h>
#include <stdint.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

typedef struct {
    bool installed;
    uint8_t saved[EU4_GFX_CAPTURE_PATCH_SIZE];
    uintptr_t site_address;
    void *relay_page;
    void *trampoline_page;
    void **trampoline_slot;
    void *gateway;
} startup_site;

static startup_site sites[EU4_GFX_STARTUP_SITE_COUNT];
static size_t page_size;
static bool both_installed;

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
    return set_site_protection(begin, VM_PROT_READ | VM_PROT_EXECUTE);
}

static bool restore_one(startup_site *site) {
    if (site->site_address == 0) {
        return true;
    }
    uintptr_t begin = site->site_address & ~((uintptr_t)page_bytes() - 1);
    if (!set_site_rw(begin)) {
        return false;
    }
    memcpy((void *)site->site_address, site->saved, sizeof(site->saved));
    sys_icache_invalidate((void *)site->site_address, sizeof(site->saved));
    return set_site_rx(begin);
}

static void release_one(startup_site *site) {
    if (site->trampoline_page != NULL) {
        (void)munmap(site->trampoline_page, page_bytes());
        site->trampoline_page = NULL;
    }
    if (site->relay_page != NULL) {
        (void)munmap(site->relay_page, page_bytes());
        site->relay_page = NULL;
    }
    if (site->trampoline_slot != NULL) {
        *site->trampoline_slot = NULL;
    }
    site->site_address = 0;
    site->installed = false;
}

static bool rel32_fits(const void *site, const void *target) {
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
        if (rel32_fits((const void *)site, mapping)) {
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

static bool write_trampoline(void *trampoline, const void *site) {
    uint8_t *p = (uint8_t *)trampoline;
    memcpy(p, site, EU4_GFX_CAPTURE_PATCH_SIZE);
    p[13] = 0xff;
    p[14] = 0x25;
    p[15] = 0x00;
    p[16] = 0x00;
    p[17] = 0x00;
    p[18] = 0x00;
    uintptr_t continuation = (uintptr_t)site + EU4_GFX_CAPTURE_PATCH_SIZE;
    memcpy(p + 19, &continuation, sizeof(continuation));
    sys_icache_invalidate(p, 27);
    return mprotect(trampoline, page_bytes(), PROT_READ | PROT_EXEC) == 0;
}

static bool install_one(startup_site *slot, void *site,
                        const uint8_t expected[EU4_GFX_CAPTURE_PATCH_SIZE],
                        void *gateway, void **trampoline_slot) {
    if (site == NULL || expected == NULL || slot->installed || gateway == NULL ||
        trampoline_slot == NULL) {
        return false;
    }
    size_t page = page_bytes();
    if (page == 0) {
        return false;
    }
    uintptr_t address = (uintptr_t)site;
    uintptr_t begin = address & ~((uintptr_t)page - 1);
    if (address + EU4_GFX_CAPTURE_PATCH_SIZE > begin + page) {
        return false;
    }
    if (memcmp(site, expected, EU4_GFX_CAPTURE_PATCH_SIZE) != 0) {
        return false;
    }

    void *relay = allocate_near(address);
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
    if (!write_trampoline(trampoline, site)) {
        (void)munmap(trampoline, page);
        (void)munmap(relay, page);
        return false;
    }

    if (!set_site_rw(begin)) {
        (void)munmap(trampoline, page);
        (void)munmap(relay, page);
        return false;
    }

    memcpy(slot->saved, site, sizeof(slot->saved));
    slot->site_address = address;
    slot->relay_page = relay;
    slot->trampoline_page = trampoline;
    slot->trampoline_slot = trampoline_slot;
    slot->gateway = gateway;
    *trampoline_slot = trampoline;

    uint8_t *patch = (uint8_t *)site;
    patch[0] = 0xe9;
    int64_t delta = (int64_t)(intptr_t)relay - ((int64_t)address + 5);
    int32_t relative = (int32_t)delta;
    memcpy(patch + 1, &relative, sizeof(relative));
    memset(patch + 5, 0x90, 8);
    sys_icache_invalidate(site, EU4_GFX_CAPTURE_PATCH_SIZE);

    if (!set_site_rx(begin)) {
        if (!restore_one(slot)) {
            slot->installed = true;
            return false;
        }
        release_one(slot);
        return false;
    }

    slot->installed = true;
    return true;
}

bool eu4_gfx_startup_install(void *settings_site, void *init_site,
                             const uint8_t settings_expected[EU4_GFX_CAPTURE_PATCH_SIZE],
                             const uint8_t init_expected[EU4_GFX_CAPTURE_PATCH_SIZE]) {
    if (both_installed) {
        return false;
    }
    if (!install_one(&sites[EU4_GFX_STARTUP_SITE_SETTINGS], settings_site,
                     settings_expected, (void *)eu4_gfx_startup_gateway_settings,
                     &eu4_gfx_startup_trampoline_settings)) {
        return false;
    }
    if (!install_one(&sites[EU4_GFX_STARTUP_SITE_INIT], init_site, init_expected,
                     (void *)eu4_gfx_startup_gateway_init,
                     &eu4_gfx_startup_trampoline_init)) {
        if (restore_one(&sites[EU4_GFX_STARTUP_SITE_SETTINGS])) {
            release_one(&sites[EU4_GFX_STARTUP_SITE_SETTINGS]);
        }
        return false;
    }
    both_installed = true;
    return true;
}

bool eu4_gfx_startup_installed(void) {
    return both_installed;
}
