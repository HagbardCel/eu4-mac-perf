#include "eu4_metal_boot_patch.h"

#include <libkern/OSCacheControl.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
#include <stdint.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

static bool installed;
static uintptr_t site_address;
static uint8_t saved[5];
static void *relay_page;
static size_t page_size;

static size_t page_bytes(void) {
    if (page_size == 0) {
        long value = sysconf(_SC_PAGESIZE);
        if (value > 0) {
            page_size = (size_t)value;
        }
    }
    return page_size;
}

bool eu4_metal_boot_rel32_fits(const void *callsite, const void *dest) {
    if (callsite == NULL || dest == NULL) {
        return false;
    }
    int64_t delta = (int64_t)(intptr_t)dest - ((int64_t)(intptr_t)callsite + 5);
    return delta >= INT32_MIN && delta <= INT32_MAX;
}

bool eu4_metal_boot_encode_call(const void *callsite, const void *dest, uint8_t out[5]) {
    if (!eu4_metal_boot_rel32_fits(callsite, dest) || out == NULL) {
        return false;
    }
    int32_t relative = (int32_t)((int64_t)(intptr_t)dest - ((int64_t)(intptr_t)callsite + 5));
    out[0] = 0xe8;
    memcpy(out + 1, &relative, sizeof relative);
    return true;
}

void eu4_metal_boot_encode_relay(void *dst, void *target) {
    uint8_t *bytes = dst;
    bytes[0] = 0xff;
    bytes[1] = 0x25;
    bytes[2] = 0;
    bytes[3] = 0;
    bytes[4] = 0;
    bytes[5] = 0;
    memcpy(bytes + 6, &target, sizeof target);
}

static void *decode_call(const void *callsite) {
    int32_t relative;
    memcpy(&relative, (const uint8_t *)callsite + 1, sizeof relative);
    return (void *)((uintptr_t)callsite + 5 + (intptr_t)relative);
}

static bool protect_page(uintptr_t begin, vm_prot_t protection) {
    return mach_vm_protect(mach_task_self(), (mach_vm_address_t)begin,
                           (mach_vm_size_t)page_bytes(), false, protection) == KERN_SUCCESS;
}

static void *allocate_near(uintptr_t site) {
    static const int64_t offsets[] = {
        0x01000000, -0x01000000, 0x04000000, -0x04000000,
        0x10000000, -0x10000000, 0x40000000, -0x40000000,
    };
    size_t page = page_bytes();
    for (size_t i = 0; i < sizeof offsets / sizeof offsets[0]; i++) {
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
        if (eu4_metal_boot_rel32_fits((const void *)site, mapping)) {
            return mapping;
        }
        (void)munmap(mapping, page);
    }
    return NULL;
}

bool eu4_metal_boot_install(void *callsite, const uint8_t expected[5], void *wrapper,
                            eu4_metal_boot_patch *out) {
    size_t page = page_bytes();
    if (installed || callsite == NULL || expected == NULL || wrapper == NULL || out == NULL ||
        page == 0 || expected[0] != 0xe8) {
        return false;
    }
    uintptr_t address = (uintptr_t)callsite;
    uintptr_t begin = address & ~((uintptr_t)page - 1);
    if (address + 5 > begin + page) {
        return false;
    }
    if (memcmp(callsite, expected, 5) != 0) {
        return false;
    }

    void *dest = wrapper;
    void *relay = NULL;
    if (!eu4_metal_boot_rel32_fits(callsite, wrapper)) {
        relay = allocate_near(address);
        if (relay == NULL) {
            return false;
        }
        eu4_metal_boot_encode_relay(relay, wrapper);
        sys_icache_invalidate(relay, 14);
        if (mprotect(relay, page, PROT_READ | PROT_EXEC) != 0) {
            (void)munmap(relay, page);
            return false;
        }
        dest = relay;
    }

    if (!protect_page(begin, VM_PROT_READ | VM_PROT_WRITE | VM_PROT_EXECUTE | VM_PROT_COPY)) {
        if (relay != NULL) {
            (void)munmap(relay, page);
        }
        return false;
    }
    void *original = decode_call(callsite);
    memcpy(saved, callsite, 5);
    uint8_t encoded[5];
    if (!eu4_metal_boot_encode_call(callsite, dest, encoded)) {
        (void)protect_page(begin, VM_PROT_READ | VM_PROT_EXECUTE);
        if (relay != NULL) {
            (void)munmap(relay, page);
        }
        return false;
    }
    memcpy(callsite, encoded, 5);
    sys_icache_invalidate(callsite, 5);
    if (!protect_page(begin, VM_PROT_READ | VM_PROT_EXECUTE)) {
        memcpy(callsite, saved, 5);
        sys_icache_invalidate(callsite, 5);
        (void)protect_page(begin, VM_PROT_READ | VM_PROT_EXECUTE);
        if (relay != NULL) {
            (void)munmap(relay, page);
        }
        return false;
    }

    site_address = address;
    relay_page = relay;
    installed = true;
    out->original = original;
    out->used_relay = relay != NULL;
    return true;
}

bool eu4_metal_boot_restore(void) {
    if (!installed) {
        return false;
    }
    size_t page = page_bytes();
    uintptr_t begin = site_address & ~((uintptr_t)page - 1);
    if (!protect_page(begin, VM_PROT_READ | VM_PROT_WRITE | VM_PROT_EXECUTE | VM_PROT_COPY)) {
        return false;
    }
    memcpy((void *)site_address, saved, 5);
    sys_icache_invalidate((void *)site_address, 5);
    if (!protect_page(begin, VM_PROT_READ | VM_PROT_EXECUTE)) {
        return false;
    }
    if (relay_page != NULL) {
        (void)munmap(relay_page, page);
        relay_page = NULL;
    }
    installed = false;
    site_address = 0;
    return true;
}
