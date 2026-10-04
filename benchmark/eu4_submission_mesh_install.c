#include "eu4_detour.h"

#include <dlfcn.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>

extern void eu4_mesh_observer_gateway(void);
extern void eu4_renderbuckets_entry_gateway(void);
extern uint64_t eu4_mesh_trampoline_ptr;
extern uint64_t eu4_renderbuckets_trampoline_ptr;

static EU4Detour mesh_detour;
static EU4Detour entry_detour;
static bool mesh_hook_installed = false;
static bool entry_hook_installed = false;

static bool rollback_entry(void) {
    if (!entry_hook_installed) {
        return true;
    }
    if (!eu4_detour_restore(&entry_detour)) {
        return false;
    }
    entry_hook_installed = false;
    return true;
}

static bool rollback_mesh(void) {
    if (!mesh_hook_installed) {
        return true;
    }
    if (!eu4_detour_restore(&mesh_detour)) {
        return false;
    }
    mesh_hook_installed = false;
    return true;
}

static void rollback_partial_install(void) {
    (void)rollback_mesh();
    (void)rollback_entry();
}

bool eu4_submission_try_install_mesh_hook(void) {
    return mesh_hook_installed;
}

bool eu4_submission_mesh_hook_is_installed(void) {
    return mesh_hook_installed;
}

bool eu4_submission_try_install_all_hooks(void) {
    if (mesh_hook_installed && entry_hook_installed) {
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

    if (!entry_hook_installed) {
        uintptr_t entry_site = image_base + 0x14c7da4u;
        unsigned char entry_expected[13] = {0x55, 0x48, 0x89, 0xe5, 0x41, 0x57, 0x41, 0x56, 0x41, 0x55, 0x41, 0x54, 0x53};
        uint8_t entry_lengths[7] = {1, 3, 2, 2, 2, 2, 1};
        if (!eu4_detour_install(
                &entry_detour,
                (void *)entry_site,
                13,
                entry_expected,
                (void *)eu4_renderbuckets_entry_gateway,
                entry_lengths,
                7)) {
            return false;
        }
        eu4_renderbuckets_trampoline_ptr = (uint64_t)(uintptr_t)entry_detour.trampoline;
        entry_hook_installed = true;
    }

    if (!mesh_hook_installed) {
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
            rollback_partial_install();
            return false;
        }
        eu4_mesh_trampoline_ptr = (uint64_t)(uintptr_t)mesh_detour.trampoline;
        mesh_hook_installed = true;
    }
    return true;
}

bool eu4_submission_hooks_are_installed(void) {
    return mesh_hook_installed && entry_hook_installed;
}
