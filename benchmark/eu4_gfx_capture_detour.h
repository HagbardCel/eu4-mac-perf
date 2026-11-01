#ifndef EU4_GFX_CAPTURE_DETOUR_H
#define EU4_GFX_CAPTURE_DETOUR_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define EU4_GFX_CAPTURE_PATCH_SIZE 13

/* One gateway frame. The assembly saves FXSAVE first, then the GPRs, then flags. */
typedef struct {
    uint8_t fxsave[512];
    uint64_t rax;
    uint64_t rbx;
    uint64_t rcx;
    uint64_t rdx;
    uint64_t rsi;
    uint64_t rdi;
    uint64_t rbp;
    uint64_t r8;
    uint64_t r9;
    uint64_t r10;
    uint64_t r11;
    uint64_t r12;
    uint64_t r13;
    uint64_t r14;
    uint64_t r15;
    uint64_t pad;
    uint64_t flags;
} eu4_gfx_capture_frame;

_Static_assert(offsetof(eu4_gfx_capture_frame, rax) == 512, "gateway GPR offset");
_Static_assert(offsetof(eu4_gfx_capture_frame, r15) == 624, "gateway GPR offset");
_Static_assert(offsetof(eu4_gfx_capture_frame, flags) == 640, "gateway flags offset");
_Static_assert(sizeof(eu4_gfx_capture_frame) == 648, "gateway frame size");

extern void eu4_gfx_capture_gateway(void);
extern void *eu4_gfx_capture_trampoline_ptr;

/* Set once before eu4_gfx_capture_install. A null hook is skipped. Do not
 * change either pointer while an intercepted call can still be in the gateway. */
typedef void (*eu4_gfx_capture_hook)(eu4_gfx_capture_frame *frame);
extern eu4_gfx_capture_hook eu4_gfx_capture_entry_ptr;
extern eu4_gfx_capture_hook eu4_gfx_capture_exit_ptr;

bool eu4_gfx_capture_rel32_fits(const void *site, const void *target);
bool eu4_gfx_capture_install(void *site,
                             const uint8_t expected[EU4_GFX_CAPTURE_PATCH_SIZE]);
bool eu4_gfx_capture_restore(void);
bool eu4_gfx_capture_installed(void);
void *eu4_gfx_capture_trampoline(void);
void *eu4_gfx_capture_relay(void);
uintptr_t eu4_gfx_capture_site(void);

#endif
