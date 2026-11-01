#ifndef EU4_GFX_ENTRY_PATCH_H
#define EU4_GFX_ENTRY_PATCH_H

#include "eu4_metal_boot_patch.h"

#include <stdbool.h>
#include <stdint.h>

/* Six function entries. The prefix is the Stage A complete-instruction run
   (5 or 6 bytes). The patch is e9 rel32 inside that prefix, then nop. */

#define EU4_GFX_ENTRY_MAX 6

typedef struct {
    void *site;
    const uint8_t *expected;
    int length;
    void *replacement;
} eu4_gfx_entry_request;

bool eu4_gfx_entry_prepare(const eu4_gfx_entry_request *requests, int count);
bool eu4_gfx_entry_commit(void);
/* After a commit that wrote at least one site, put those bytes back.
   On a prepared-only set, unmap the relays. */
bool eu4_gfx_entry_restore(void);

/* Verify eight sites, prepare six entries, install the CALL sites, then commit.
   A false return from eu4_metal_boot_install_sites is not restored again. */
bool eu4_gfx_install_all(const eu4_gfx_entry_request *entries, int entry_count,
                         const eu4_metal_boot_request *calls, int call_count,
                         eu4_metal_boot_patch *call_out);

void eu4_gfx_entry_test_fail_commit(int after_sites);
void eu4_gfx_entry_test_fail_relay(int on);

#endif
