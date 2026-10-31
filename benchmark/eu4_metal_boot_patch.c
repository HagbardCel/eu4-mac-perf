#include "eu4_metal_boot_patch.h"

#include <libkern/OSCacheControl.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
#include <stdint.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

typedef struct {
    uintptr_t address;
    uint8_t saved[5];
    uint8_t encoded[5];
    void *relay;
    int live;
} slot_state;

static slot_state slots[EU4_METAL_BOOT_MAX_SITES];
static int slot_count;
static int committed;
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

static void unmap_relays(void) {
    size_t page = page_bytes();
    for (int i = 0; i < slot_count; i++) {
        if (slots[i].relay != NULL) {
            (void)munmap(slots[i].relay, page);
            slots[i].relay = NULL;
        }
    }
}

static void forget_slots(void) {
    unmap_relays();
    memset(slots, 0, sizeof slots);
    slot_count = 0;
    committed = 0;
}

/* Restores every callsite whose bytes were already replaced. Relays are dropped either way. */
static bool rollback_live(void) {
    size_t page = page_bytes();
    uintptr_t seen[EU4_METAL_BOOT_MAX_SITES];
    int seen_count = 0;
    for (int i = 0; i < slot_count; i++) {
        if (!slots[i].live) {
            continue;
        }
        uintptr_t begin = slots[i].address & ~(page - 1);
        int found = 0;
        for (int j = 0; j < seen_count; j++) {
            if (seen[j] == begin) {
                found = 1;
                break;
            }
        }
        if (!found) {
            seen[seen_count++] = begin;
        }
    }
    for (int p = seen_count - 1; p >= 0; p--) {
        if (!protect_page(seen[p], VM_PROT_READ | VM_PROT_WRITE | VM_PROT_EXECUTE | VM_PROT_COPY)) {
            return false;
        }
        for (int i = slot_count - 1; i >= 0; i--) {
            if (!slots[i].live || (slots[i].address & ~(page - 1)) != seen[p]) {
                continue;
            }
            memcpy((void *)slots[i].address, slots[i].saved, 5);
            sys_icache_invalidate((void *)slots[i].address, 5);
            slots[i].live = 0;
        }
        if (!protect_page(seen[p], VM_PROT_READ | VM_PROT_EXECUTE)) {
            return false;
        }
    }
    forget_slots();
    return true;
}

static void rollback_or_exit(void) {
    if (!rollback_live()) {
        _exit(2);
    }
}

bool eu4_metal_boot_install_sites(const eu4_metal_boot_request *requests, int count,
                                  eu4_metal_boot_patch *out) {
    size_t page = page_bytes();
    if (committed || requests == NULL || out == NULL || count < 1 ||
        count > EU4_METAL_BOOT_MAX_SITES || page == 0) {
        return false;
    }
    memset(slots, 0, sizeof slots);
    slot_count = 0;

    uintptr_t pages[EU4_METAL_BOOT_MAX_SITES];
    int page_count = 0;
    int page_of[EU4_METAL_BOOT_MAX_SITES];
    for (int i = 0; i < count; i++) {
        if (requests[i].callsite == NULL || requests[i].expected == NULL ||
            requests[i].wrapper == NULL || requests[i].expected[0] != 0xe8) {
            return false;
        }
        uintptr_t address = (uintptr_t)requests[i].callsite;
        uintptr_t begin = address & ~(page - 1);
        if (address + 5 > begin + page) {
            return false;
        }
        for (int j = 0; j < i; j++) {
            uintptr_t other = slots[j].address;
            if (address < other + 5 && other < address + 5) {
                return false;
            }
        }
        if (memcmp(requests[i].callsite, requests[i].expected, 5) != 0) {
            return false;
        }
        void *original = decode_call(requests[i].callsite);
        if (requests[i].expected_target == NULL || original != requests[i].expected_target) {
            return false;
        }
        slots[i].address = address;
        memcpy(slots[i].saved, requests[i].callsite, 5);
        page_of[i] = -1;
        for (int p = 0; p < page_count; p++) {
            if (pages[p] == begin) {
                page_of[i] = p;
                break;
            }
        }
        if (page_of[i] < 0) {
            page_of[i] = page_count;
            pages[page_count++] = begin;
        }
        out[i].original = original;
        out[i].used_relay = false;
    }
    slot_count = count;

    for (int i = 0; i < count; i++) {
        void *dest = requests[i].wrapper;
        if (!eu4_metal_boot_rel32_fits(requests[i].callsite, dest)) {
            void *relay = allocate_near(slots[i].address);
            if (relay == NULL) {
                forget_slots();
                return false;
            }
            eu4_metal_boot_encode_relay(relay, requests[i].wrapper);
            sys_icache_invalidate(relay, 14);
            if (mprotect(relay, page, PROT_READ | PROT_EXEC) != 0) {
                (void)munmap(relay, page);
                forget_slots();
                return false;
            }
            slots[i].relay = relay;
            dest = relay;
            out[i].used_relay = true;
        }
        if (!eu4_metal_boot_encode_call(requests[i].callsite, dest, slots[i].encoded)) {
            forget_slots();
            return false;
        }
    }

    int pages_done = 0;
    for (int p = 0; p < page_count; p++) {
#ifdef EU4_METAL_BOOT_TEST_FAIL
        if (pages_done >= 1) {
            rollback_or_exit();
            return false;
        }
#endif
        if (!protect_page(pages[p], VM_PROT_READ | VM_PROT_WRITE | VM_PROT_EXECUTE | VM_PROT_COPY)) {
            if (pages_done == 0) {
                forget_slots();
                return false;
            }
            rollback_or_exit();
            return false;
        }
        for (int i = 0; i < count; i++) {
            if (page_of[i] != p) {
                continue;
            }
            memcpy((void *)slots[i].address, slots[i].encoded, 5);
            sys_icache_invalidate((void *)slots[i].address, 5);
            slots[i].live = 1;
        }
        if (!protect_page(pages[p], VM_PROT_READ | VM_PROT_EXECUTE)) {
            rollback_or_exit();
            return false;
        }
        pages_done++;
    }

    committed = 1;
    return true;
}

bool eu4_metal_boot_install(void *callsite, const uint8_t expected[5], void *expected_original,
                            void *wrapper, eu4_metal_boot_patch *out) {
    eu4_metal_boot_request request = {callsite, expected, wrapper, expected_original};
    if (expected_original == NULL) {
        return false;
    }
    return eu4_metal_boot_install_sites(&request, 1, out);
}

bool eu4_metal_boot_restore(void) {
    if (!committed) {
        return false;
    }
    if (!rollback_live()) {
        _exit(2);
    }
    return true;
}
