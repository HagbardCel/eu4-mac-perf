#ifndef EU4_GFX_SITE_PATCH_H
#define EU4_GFX_SITE_PATCH_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* One intercepted entry. `length` is the complete-instruction prefix that is
   replaced by e9 rel32 plus nop padding. The trampoline replays exactly those
   bytes and jumps back to address + length. 13 is the historical startup-trace
   window; the graphics operation observer also uses shorter windows. */
#define EU4_GFX_SITE_MAX_PATCH 13

typedef struct {
    uintptr_t address;
    int length;
    uint8_t saved[EU4_GFX_SITE_MAX_PATCH];
    void *relay_page;
    void *trampoline_page;
    void **trampoline_slot;
    void *gateway;
    bool installed;
} eu4_gfx_site;

bool eu4_gfx_site_rel32_fits(const void *site, const void *target);
bool eu4_gfx_site_patch(eu4_gfx_site *site, void *address,
                        const uint8_t *expected, int length, void *gateway,
                        void **trampoline_slot);
bool eu4_gfx_site_restore(eu4_gfx_site *site);
void eu4_gfx_site_release(eu4_gfx_site *site);

#endif
