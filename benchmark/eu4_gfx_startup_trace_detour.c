#include "eu4_gfx_startup_trace.h"
#include "eu4_gfx_site_patch.h"

#include <stdint.h>

static eu4_gfx_site sites[EU4_GFX_STARTUP_SITE_COUNT];
static bool both_installed;

bool eu4_gfx_startup_install(void *settings_site, void *init_site,
                             const uint8_t settings_expected[EU4_GFX_CAPTURE_PATCH_SIZE],
                             const uint8_t init_expected[EU4_GFX_CAPTURE_PATCH_SIZE]) {
    if (both_installed) {
        return false;
    }
    if (!eu4_gfx_site_patch(&sites[EU4_GFX_STARTUP_SITE_SETTINGS], settings_site,
                            settings_expected, EU4_GFX_CAPTURE_PATCH_SIZE,
                            (void *)eu4_gfx_startup_gateway_settings,
                            &eu4_gfx_startup_trampoline_settings)) {
        return false;
    }
    if (!eu4_gfx_site_patch(&sites[EU4_GFX_STARTUP_SITE_INIT], init_site,
                            init_expected, EU4_GFX_CAPTURE_PATCH_SIZE,
                            (void *)eu4_gfx_startup_gateway_init,
                            &eu4_gfx_startup_trampoline_init)) {
        if (eu4_gfx_site_restore(&sites[EU4_GFX_STARTUP_SITE_SETTINGS])) {
            eu4_gfx_site_release(&sites[EU4_GFX_STARTUP_SITE_SETTINGS]);
        }
        return false;
    }
    both_installed = true;
    return true;
}

bool eu4_gfx_startup_installed(void) {
    return both_installed;
}
