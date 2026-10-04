#include "eu4_detour.h"

#include <dlfcn.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>

extern void eu4_mesh_observer_gateway(void);
extern uint64_t eu4_mesh_trampoline_ptr;

static EU4Detour mesh_detour;
static bool mesh_hook_installed = false;

bool eu4_submission_try_install_mesh_hook(void) {
    if (mesh_hook_installed) {
        return true;
    }
    void *anchor =
        dlsym(RTLD_DEFAULT, "_ZN14CPdxMeshObject13RenderBucketsEP9CGraphicsP21GfxDeferredContextGFXPK7CCameraibb");
    if (!anchor) {
        return false;
    }
    Dl_info info;
    if (!dladdr(anchor, &info) || !info.dli_fbase) {
        return false;
    }
    uintptr_t image_base = (uintptr_t)info.dli_fbase;
    uintptr_t site = image_base + 0x14c81e6u;
    unsigned char expected[13] = {0x48, 0x0f, 0x45, 0xc8, 0x8b, 0x31, 0x48, 0x8b, 0xbd, 0x20, 0xff, 0xff, 0xff};
    uint8_t lengths[3] = {4, 2, 7};
    if (!eu4_detour_install(
            &mesh_detour,
            (void *)site,
            13,
            expected,
            (void *)eu4_mesh_observer_gateway,
            lengths,
            3)) {
        return false;
    }
    eu4_mesh_trampoline_ptr = (uint64_t)(uintptr_t)mesh_detour.trampoline;
    mesh_hook_installed = true;
    return true;
}

bool eu4_submission_mesh_hook_is_installed(void) {
    return mesh_hook_installed;
}
