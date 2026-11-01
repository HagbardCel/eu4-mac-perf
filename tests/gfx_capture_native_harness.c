#include "eu4_gfx_capture_detour.h"
#include "gfx_capture_test_state.h"

#include <stdint.h>
#include <stdio.h>
#include <string.h>

extern void gfx_capture_native_leaf(void);
extern uint64_t gfx_capture_native_leaf_calls;
extern void eu4_gfx_set_constant_buffer_capture_entry(eu4_gfx_capture_frame *frame);
extern void eu4_gfx_set_constant_buffer_capture_exit(eu4_gfx_capture_frame *frame);
extern uint64_t gfx_capture_native_entry_frame;
extern uint64_t gfx_capture_native_exit_frame;
extern uint64_t gfx_capture_native_entry_calls;
extern uint64_t gfx_capture_native_exit_calls;
extern eu4_gfx_capture_frame gfx_capture_native_entry_snapshot;
extern eu4_gfx_capture_frame gfx_capture_native_exit_snapshot;

gfx_capture_test_state gfx_capture_test_seed;
gfx_capture_test_state gfx_capture_test_capture;
uint64_t gfx_capture_test_pre_call_rsp;
uint64_t gfx_capture_test_capture_rsp;
void *gfx_capture_test_target;

static uint64_t leaf_value = 0x123456789abcdef0ull;

static const uint8_t expected[EU4_GFX_CAPTURE_PATCH_SIZE] = {
    0x55, 0x48, 0x89, 0xe5, 0x48, 0x8b, 0x07,
    0x89, 0xc9, 0x41, 0x83, 0xf9, 0xff,
};

static int fail(const char *message) {
    fprintf(stderr, "%s\n", message);
    return 1;
}

static void fill_seed(void) {
    memset(&gfx_capture_test_seed, 0, sizeof(gfx_capture_test_seed));
    gfx_capture_test_seed.rax = 0xA0A0A0A0A0A0A0A0ull;
    gfx_capture_test_seed.rbx = 0xB0B0B0B0B0B0B0B0ull;
    gfx_capture_test_seed.rcx = 0xC0C0C0C0C0C0C0C0ull;
    gfx_capture_test_seed.rdx = 0xD0D0D0D0D0D0D0D0ull;
    gfx_capture_test_seed.rsi = 0xE0E0E0E0E0E0E0E0ull;
    gfx_capture_test_seed.rdi = (uint64_t)(uintptr_t)&leaf_value;
    gfx_capture_test_seed.rbp = 0x5151515151515151ull;
    gfx_capture_test_seed.r8 = 0xF0F0F0F0F0F0F0F0ull;
    gfx_capture_test_seed.r9 = 0x1212121212121212ull;
    gfx_capture_test_seed.r10 = 0x1313131313131313ull;
    gfx_capture_test_seed.r11 = 0x1414141414141414ull;
    gfx_capture_test_seed.r12 = 0x1515151515151515ull;
    gfx_capture_test_seed.r13 = 0x1616161616161616ull;
    gfx_capture_test_seed.r14 = 0x1717171717171717ull;
    gfx_capture_test_seed.r15 = 0x1818181818181818ull;
    gfx_capture_test_seed.flags = 0xAD7ull;
    gfx_capture_test_seed.mxcsr = 0x1F80u;
    for (int i = 0; i < 16; i++) {
        for (int j = 0; j < 16; j++) {
            gfx_capture_test_seed.xmm[i][j] = (uint8_t)(0x40 + i * 16 + j);
        }
    }
}

static void run_target(void) {
    memset(&gfx_capture_test_capture, 0, sizeof(gfx_capture_test_capture));
    gfx_capture_test_pre_call_rsp = 0;
    gfx_capture_test_capture_rsp = 0;
    gfx_capture_test_target = (void *)(uintptr_t)gfx_capture_native_leaf;
    gfx_capture_test_call_target();
}

static int test_encodings(void) {
    const uint8_t *site =
        (const uint8_t *)(uintptr_t)gfx_capture_native_leaf;
    if (memcmp(site, expected, sizeof(expected)) != 0) {
        return fail("test leaf does not begin with the frozen 13 bytes");
    }
    if (!eu4_gfx_capture_rel32_fits(site, site + 0x100)) {
        return fail("near rel32 was rejected");
    }
    if (eu4_gfx_capture_rel32_fits(
            site, (const void *)((uintptr_t)site + 0x100000000ull))) {
        return fail("far rel32 was accepted");
    }
    return 0;
}

static int test_bad_bytes(void) {
    void *site = (void *)(uintptr_t)gfx_capture_native_leaf;
    uint8_t wrong[EU4_GFX_CAPTURE_PATCH_SIZE];
    memcpy(wrong, site, sizeof(wrong));
    wrong[0] ^= 0xff;
    if (eu4_gfx_capture_install(site, wrong)) {
        return fail("install accepted incorrect site bytes");
    }
    if (memcmp(site, expected, sizeof(expected)) != 0) {
        return fail("failed install changed the site");
    }
    return 0;
}

static int test_install_and_restore(void) {
    void *site = (void *)(uintptr_t)gfx_capture_native_leaf;
    if (eu4_gfx_capture_install(site, expected) == false) {
        return fail("install failed");
    }
    if (!eu4_gfx_capture_installed()) {
        return fail("install did not set the installed state");
    }

    const uint8_t *p = (const uint8_t *)site;
    if (p[0] != 0xe9) {
        return fail("site is not a rel32 jump");
    }
    int32_t relative = 0;
    memcpy(&relative, p + 1, sizeof(relative));
    const void *jump_target =
        (const void *)((uintptr_t)site + 5 + (intptr_t)relative);
    if (jump_target != eu4_gfx_capture_relay()) {
        return fail("rel32 does not reach the relay");
    }
    for (int i = 5; i < EU4_GFX_CAPTURE_PATCH_SIZE; i++) {
        if (p[i] != 0x90) {
            return fail("site tail is not NOP-filled");
        }
    }
    if (memcmp(p, "\x49\xbb", 2) == 0) {
        return fail("site uses the R11 clobbering jump");
    }

    const uint8_t *relay = (const uint8_t *)eu4_gfx_capture_relay();
    if (relay == NULL || relay[0] != 0xff || relay[1] != 0x25 ||
        relay[2] != 0 || relay[3] != 0 || relay[4] != 0 || relay[5] != 0) {
        return fail("relay is not a RIP-indirect jump");
    }
    uint64_t gateway = 0;
    memcpy(&gateway, relay + 6, sizeof(gateway));
    if (gateway != (uint64_t)(uintptr_t)eu4_gfx_capture_gateway) {
        return fail("relay does not point at the gateway");
    }

    const uint8_t *trampoline = (const uint8_t *)eu4_gfx_capture_trampoline();
    if (trampoline == NULL ||
        memcmp(trampoline, expected, sizeof(expected)) != 0 ||
        trampoline[13] != 0xff || trampoline[14] != 0x25 ||
        trampoline[15] != 0 || trampoline[16] != 0 ||
        trampoline[17] != 0 || trampoline[18] != 0) {
        return fail("trampoline prologue or tail is wrong");
    }
    uint64_t continuation = 0;
    memcpy(&continuation, trampoline + 19, sizeof(continuation));
    if (continuation != (uint64_t)((uintptr_t)site + EU4_GFX_CAPTURE_PATCH_SIZE)) {
        return fail("trampoline does not continue after the patch");
    }
    if (!eu4_gfx_capture_rel32_fits(site, relay)) {
        return fail("installed relay is outside rel32 range");
    }

    if (!eu4_gfx_capture_restore()) {
        return fail("restore failed");
    }
    if (eu4_gfx_capture_installed() ||
        memcmp(site, expected, sizeof(expected)) != 0 ||
        eu4_gfx_capture_relay() != NULL ||
        eu4_gfx_capture_trampoline() != NULL) {
        return fail("restore did not return the site and mappings");
    }

    if (!eu4_gfx_capture_install(site, expected) ||
        !eu4_gfx_capture_restore()) {
        return fail("second install/restore cycle failed");
    }
    return 0;
}

static int test_transparency(void) {
    fill_seed();
    run_target();
    gfx_capture_test_state unhooked = gfx_capture_test_capture;
    uint64_t unhooked_pre = gfx_capture_test_pre_call_rsp;
    uint64_t unhooked_post = gfx_capture_test_capture_rsp;

    gfx_capture_native_entry_frame = 0;
    gfx_capture_native_exit_frame = 0;
    gfx_capture_native_entry_calls = 0;
    gfx_capture_native_exit_calls = 0;

    fill_seed();
    eu4_gfx_capture_entry_ptr = eu4_gfx_set_constant_buffer_capture_entry;
    eu4_gfx_capture_exit_ptr = eu4_gfx_set_constant_buffer_capture_exit;
    uint64_t leaf_before = gfx_capture_native_leaf_calls;
    if (!eu4_gfx_capture_install(
            (void *)(uintptr_t)gfx_capture_native_leaf, expected)) {
        return fail("transparency install failed");
    }
    run_target();
    gfx_capture_test_state hooked = gfx_capture_test_capture;
    uint64_t hooked_pre = gfx_capture_test_pre_call_rsp;
    uint64_t hooked_post = gfx_capture_test_capture_rsp;
    if (!eu4_gfx_capture_restore()) {
        return fail("transparency restore failed");
    }

    if (gfx_capture_native_leaf_calls != leaf_before + 1) {
        return fail("original target did not run exactly once");
    }
    if (gfx_capture_native_entry_calls != 1 ||
        gfx_capture_native_exit_calls != 1) {
        return fail("gateway did not call entry and exit once");
    }
    if ((gfx_capture_native_entry_frame & 15ull) != 0 ||
        (gfx_capture_native_exit_frame & 15ull) != 0) {
        return fail("gateway frame is not 16-byte aligned");
    }
    if (gfx_capture_native_entry_frame != gfx_capture_native_exit_frame) {
        return fail("entry and exit hooks saw different frames");
    }

    const eu4_gfx_capture_frame *entry = &gfx_capture_native_entry_snapshot;
    const eu4_gfx_capture_frame *exit = &gfx_capture_native_exit_snapshot;
    if (entry->rax != gfx_capture_test_seed.rax ||
        entry->rcx != gfx_capture_test_seed.rcx ||
        entry->rdi != gfx_capture_test_seed.rdi ||
        entry->flags != gfx_capture_test_seed.flags) {
        fprintf(stderr, "entry rax=%llx/%llx rcx=%llx/%llx rdi=%llx/%llx flags=%llx/%llx\n",
                (unsigned long long)entry->rax,
                (unsigned long long)gfx_capture_test_seed.rax,
                (unsigned long long)entry->rcx,
                (unsigned long long)gfx_capture_test_seed.rcx,
                (unsigned long long)entry->rdi,
                (unsigned long long)gfx_capture_test_seed.rdi,
                (unsigned long long)entry->flags,
                (unsigned long long)gfx_capture_test_seed.flags);
        return fail("entry hook did not see the declared entry state");
    }
    if (exit->rax != unhooked.rax || exit->rcx != unhooked.rcx ||
        exit->rdx != unhooked.rdx || exit->r9 != unhooked.r9 ||
        exit->flags != unhooked.flags) {
        return fail("exit hook did not see the post-original state");
    }

    if (!gfx_capture_test_state_equal(&unhooked, &hooked)) {
        return fail("hooked and unhooked register state differ");
    }
    if (unhooked_pre != hooked_pre || unhooked_post != hooked_post ||
        unhooked_pre != unhooked_post ||
        (hooked_pre & 15ull) != 0) {
        return fail("external RSP changed across the hook");
    }
    if (unhooked.rax == gfx_capture_test_seed.rax ||
        unhooked.rcx == gfx_capture_test_seed.rcx ||
        unhooked.rdx == gfx_capture_test_seed.rdx ||
        unhooked.r9 == gfx_capture_test_seed.r9 ||
        unhooked.flags == gfx_capture_test_seed.flags) {
        return fail("test leaf did not exercise the compared state");
    }
    return 0;
}

static int test_callbacks(eu4_gfx_capture_hook entry, eu4_gfx_capture_hook exit_hook,
                          uint64_t expect_entry, uint64_t expect_exit,
                          const char *name) {
    fill_seed();
    run_target();
    gfx_capture_test_state unhooked = gfx_capture_test_capture;

    gfx_capture_native_entry_calls = 0;
    gfx_capture_native_exit_calls = 0;
    eu4_gfx_capture_entry_ptr = entry;
    eu4_gfx_capture_exit_ptr = exit_hook;
    fill_seed();
    uint64_t leaf_before = gfx_capture_native_leaf_calls;
    if (!eu4_gfx_capture_install(
            (void *)(uintptr_t)gfx_capture_native_leaf, expected)) {
        fprintf(stderr, "%s install failed\n", name);
        return 1;
    }
    run_target();
    gfx_capture_test_state hooked = gfx_capture_test_capture;
    if (!eu4_gfx_capture_restore()) {
        fprintf(stderr, "%s restore failed\n", name);
        return 1;
    }
    if (gfx_capture_native_leaf_calls != leaf_before + 1) {
        fprintf(stderr, "%s original target count\n", name);
        return 1;
    }
    if (gfx_capture_native_entry_calls != expect_entry ||
        gfx_capture_native_exit_calls != expect_exit) {
        fprintf(stderr, "%s callback counts entry=%llu exit=%llu\n", name,
                (unsigned long long)gfx_capture_native_entry_calls,
                (unsigned long long)gfx_capture_native_exit_calls);
        return 1;
    }
    if (!gfx_capture_test_state_equal(&unhooked, &hooked)) {
        fprintf(stderr, "%s register state differs flags %llx/%llx mxcsr %x/%x rax %llx/%llx\n",
                name, (unsigned long long)unhooked.flags,
                (unsigned long long)hooked.flags, unhooked.mxcsr, hooked.mxcsr,
                (unsigned long long)unhooked.rax, (unsigned long long)hooked.rax);
        return 1;
    }
    return 0;
}

static int test_nullable_callbacks(void) {
    if (test_callbacks(NULL, NULL, 0, 0, "null callbacks") != 0) {
        return 1;
    }
    if (test_callbacks(eu4_gfx_set_constant_buffer_capture_entry, NULL, 1, 0,
                       "entry only") != 0) {
        return 1;
    }
    return 0;
}

int main(void) {
    if (test_encodings() != 0) {
        return 1;
    }
    if (test_bad_bytes() != 0) {
        return 1;
    }
    if (test_install_and_restore() != 0) {
        return 1;
    }
    if (test_transparency() != 0) {
        return 1;
    }
    if (test_nullable_callbacks() != 0) {
        return 1;
    }
    puts("gfx capture native harness: ok");
    return 0;
}
