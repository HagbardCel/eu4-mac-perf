#include "eu4_gfx_entry_patch.h"
#include "prefixes.h"

#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

extern void *eu4_gfx_create_index_buffer(void *, const void *, int);
extern void eu4_gfx_destroy_index_buffer(void *);
extern void eu4_gfx_update_index_buffer(void *, void *, const void *, int);
extern void eu4_gfx_set_index_buffer(void *, void *);

static const uint8_t *k_bytes[4] = {kPrefix0, kPrefix1, kPrefix2, kPrefix3};
static const int k_lens[4] = {kPrefixLen0, kPrefixLen1, kPrefixLen2, kPrefixLen3};

static void fail(const char *message) {
    fprintf(stderr, "%s\n", message);
    _exit(1);
}

static void bind(uint8_t *mapping, size_t page, eu4_gfx_entry_request *entries, void **fns, int flip) {
    /* Restore leaves the page executable. The next fill has to write it again. */
    if (mprotect(mapping, page, PROT_READ | PROT_WRITE) != 0) {
        fail("mprotect");
    }
    for (int i = 0; i < 4; i++) {
        uint8_t *site = mapping + (size_t)i * 32;
        memcpy(site, k_bytes[i], (size_t)k_lens[i]);
        if (i == flip) {
            site[k_lens[i] - 1] ^= 0xff;
        }
        site[k_lens[i]] = 0xc3;
        entries[i].site = site;
        entries[i].expected = k_bytes[i];
        entries[i].length = k_lens[i];
        entries[i].replacement = fns[i];
    }
}

static void expect_bytes(const eu4_gfx_entry_request *entries) {
    for (int i = 0; i < 4; i++) {
        if (memcmp(entries[i].site, k_bytes[i], (size_t)k_lens[i]) != 0) {
            fail("entry bytes changed");
        }
    }
}

int main(void) {
    void *fns[4] = {
        (void *)eu4_gfx_create_index_buffer,
        (void *)eu4_gfx_destroy_index_buffer,
        (void *)eu4_gfx_update_index_buffer,
        (void *)eu4_gfx_set_index_buffer,
    };
    size_t page = (size_t)sysconf(_SC_PAGESIZE);
    uint8_t *mapping = mmap(NULL, page, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
    if (mapping == MAP_FAILED) {
        fail("mmap");
    }
    eu4_gfx_entry_request entries[4];
    bind(mapping, page, entries, fns, -1);
    if (!eu4_gfx_entry_prepare(entries, 4) || !eu4_gfx_entry_commit()) {
        fail("index prefixes were rejected");
    }
    if (!eu4_gfx_entry_restore()) {
        fail("index restore failed");
    }
    expect_bytes(entries);

    bind(mapping, page, entries, fns, 3);
    if (eu4_gfx_entry_prepare(entries, 4)) {
        fail("wrong prefix was accepted");
    }
    if (((mapping + 96)[k_lens[3] - 1]) == k_bytes[3][k_lens[3] - 1]) {
        fail("wrong prefix was rewritten");
    }

    bind(mapping, page, entries, fns, -1);
    eu4_gfx_entry_test_fail_commit(2);
    if (!eu4_gfx_entry_prepare(entries, 4) || eu4_gfx_entry_commit()) {
        fail("partial commit was reported as success");
    }
    if (!eu4_gfx_entry_restore()) {
        fail("rollback restore failed");
    }
    eu4_gfx_entry_test_fail_commit(0);
    expect_bytes(entries);
    if (munmap(mapping, page) != 0) {
        fail("munmap");
    }
    return 0;
}
