#include "eu4_gfx_entry_patch.h"

#include <libkern/OSCacheControl.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
#include <stdint.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

typedef struct {
    uintptr_t address;
    int length;
    uint8_t saved[8];
    uint8_t encoded[8];
    void *relay;
    int live;
} entry_slot;

static entry_slot slots[EU4_GFX_ENTRY_MAX];
static int slot_count;
static int prepared;
static int written;
static size_t page_size;
static int fail_commit_after;
static int fail_relay;

static size_t page_bytes(void) {
    if (page_size == 0) {
        long value = sysconf(_SC_PAGESIZE);
        if (value > 0) {
            page_size = (size_t)value;
        }
    }
    return page_size;
}

static bool protect_page(uintptr_t begin, vm_prot_t protection) {
    return mach_vm_protect(mach_task_self(), (mach_vm_address_t)begin,
                           (mach_vm_size_t)page_bytes(), false, protection) == KERN_SUCCESS;
}

static void *allocate_near(uintptr_t site) {
    if (fail_relay) {
        return NULL;
    }
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

static void forget(void) {
    unmap_relays();
    memset(slots, 0, sizeof slots);
    slot_count = 0;
    prepared = 0;
    written = 0;
}

static bool encode_jump(const void *site, const void *dest, int length, uint8_t *out) {
    if (!eu4_metal_boot_rel32_fits(site, dest) || length < 5 || length > 8) {
        return false;
    }
    int32_t relative = (int32_t)((int64_t)(intptr_t)dest - ((int64_t)(intptr_t)site + 5));
    out[0] = 0xe9;
    memcpy(out + 1, &relative, sizeof relative);
    for (int i = 5; i < length; i++) {
        out[i] = 0x90;
    }
    return true;
}

void eu4_gfx_entry_test_fail_commit(int after_sites) { fail_commit_after = after_sites; }

void eu4_gfx_entry_test_fail_relay(int on) { fail_relay = on; }

bool eu4_gfx_entry_prepare(const eu4_gfx_entry_request *requests, int count) {
    size_t page = page_bytes();
    if (prepared || written || requests == NULL || count < 1 || count > EU4_GFX_ENTRY_MAX ||
        page == 0) {
        return false;
    }
    memset(slots, 0, sizeof slots);
    slot_count = 0;
    for (int i = 0; i < count; i++) {
        int length = requests[i].length;
        if (requests[i].site == NULL || requests[i].expected == NULL ||
            requests[i].replacement == NULL || length < 5 || length > 8) {
            return false;
        }
        uintptr_t address = (uintptr_t)requests[i].site;
        uintptr_t begin = address & ~(page - 1);
        if (address + (uintptr_t)length > begin + page) {
            return false;
        }
        for (int j = 0; j < i; j++) {
            uintptr_t other = slots[j].address;
            int other_len = slots[j].length;
            if (address < other + (uintptr_t)other_len && other < address + (uintptr_t)length) {
                return false;
            }
        }
        if (memcmp(requests[i].site, requests[i].expected, (size_t)length) != 0) {
            return false;
        }
        /* The prefix is a complete instruction run: push %rbp; mov %rsp,%rbp; one more push. */
        if (requests[i].expected[0] != 0x55 || requests[i].expected[1] != 0x48) {
            return false;
        }
        slots[i].address = address;
        slots[i].length = length;
        memcpy(slots[i].saved, requests[i].expected, (size_t)length);
    }
    slot_count = count;
    for (int i = 0; i < count; i++) {
        void *dest = requests[i].replacement;
        if (!eu4_metal_boot_rel32_fits(requests[i].site, dest)) {
            void *relay = allocate_near(slots[i].address);
            if (relay == NULL) {
                forget();
                return false;
            }
            eu4_metal_boot_encode_relay(relay, requests[i].replacement);
            sys_icache_invalidate(relay, 14);
            if (mprotect(relay, page, PROT_READ | PROT_EXEC) != 0) {
                (void)munmap(relay, page);
                slots[i].relay = NULL;
                forget();
                return false;
            }
            slots[i].relay = relay;
            dest = relay;
        }
        if (!encode_jump(requests[i].site, dest, slots[i].length, slots[i].encoded)) {
            forget();
            return false;
        }
    }
    prepared = 1;
    return true;
}

bool eu4_gfx_entry_commit(void) {
    size_t page = page_bytes();
    if (!prepared || written || page == 0) {
        return false;
    }
    uintptr_t pages[EU4_GFX_ENTRY_MAX];
    int page_of[EU4_GFX_ENTRY_MAX];
    int page_count = 0;
    for (int i = 0; i < slot_count; i++) {
        uintptr_t begin = slots[i].address & ~(page - 1);
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
    }
    for (int p = 0; p < page_count; p++) {
        if (!protect_page(pages[p], VM_PROT_READ | VM_PROT_WRITE | VM_PROT_EXECUTE | VM_PROT_COPY)) {
            return false;
        }
        for (int i = 0; i < slot_count; i++) {
            if (page_of[i] != p) {
                continue;
            }
            if (fail_commit_after > 0 && written >= fail_commit_after) {
                (void)protect_page(pages[p], VM_PROT_READ | VM_PROT_EXECUTE);
                return false;
            }
            memcpy((void *)slots[i].address, slots[i].encoded, (size_t)slots[i].length);
            sys_icache_invalidate((void *)slots[i].address, (size_t)slots[i].length);
            slots[i].live = 1;
            written += 1;
        }
        if (!protect_page(pages[p], VM_PROT_READ | VM_PROT_EXECUTE)) {
            return false;
        }
    }
    prepared = 0;
    return true;
}

bool eu4_gfx_entry_restore(void) {
    if (!prepared && written == 0) {
        return false;
    }
    if (written == 0) {
        forget();
        return true;
    }
    size_t page = page_bytes();
    uintptr_t seen[EU4_GFX_ENTRY_MAX];
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
            _exit(2);
        }
        for (int i = slot_count - 1; i >= 0; i--) {
            if (!slots[i].live || (slots[i].address & ~(page - 1)) != seen[p]) {
                continue;
            }
            memcpy((void *)slots[i].address, slots[i].saved, (size_t)slots[i].length);
            sys_icache_invalidate((void *)slots[i].address, (size_t)slots[i].length);
            slots[i].live = 0;
        }
        if (!protect_page(seen[p], VM_PROT_READ | VM_PROT_EXECUTE)) {
            _exit(2);
        }
    }
    forget();
    return true;
}

static int ranges_match(const eu4_gfx_entry_request *entries, int entry_count,
                        const eu4_metal_boot_request *calls, int call_count) {
    if (entries == NULL || calls == NULL || entry_count < 1 || call_count < 1) {
        return 0;
    }
    for (int i = 0; i < entry_count; i++) {
        if (entries[i].site == NULL || entries[i].expected == NULL || entries[i].length < 5) {
            return 0;
        }
        if (memcmp(entries[i].site, entries[i].expected, (size_t)entries[i].length) != 0) {
            return 0;
        }
    }
    for (int i = 0; i < call_count; i++) {
        if (calls[i].callsite == NULL || calls[i].expected == NULL || calls[i].expected[0] != 0xe8) {
            return 0;
        }
        if (memcmp(calls[i].callsite, calls[i].expected, 5) != 0) {
            return 0;
        }
    }
    return 1;
}

bool eu4_gfx_install_all(const eu4_gfx_entry_request *entries, int entry_count,
                         const eu4_metal_boot_request *calls, int call_count,
                         eu4_metal_boot_patch *call_out) {
    if (!ranges_match(entries, entry_count, calls, call_count) || call_out == NULL) {
        return false;
    }
    if (!eu4_gfx_entry_prepare(entries, entry_count)) {
        return false;
    }
    if (!eu4_metal_boot_install_sites(calls, call_count, call_out)) {
        (void)eu4_gfx_entry_restore();
        return false;
    }
    if (!eu4_gfx_entry_commit()) {
        if (!eu4_gfx_entry_restore()) {
            _exit(2);
        }
        if (!eu4_metal_boot_restore()) {
            _exit(2);
        }
        return false;
    }
    return true;
}
