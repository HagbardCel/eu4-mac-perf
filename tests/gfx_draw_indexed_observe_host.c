#include "eu4_gfx_draw_indexed_observe.h"

#include <stdatomic.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

extern void eu4_gfx_draw_indexed_observe_test_reset(void);
extern void eu4_gfx_draw_indexed_observe_test_hold(unsigned index, uint64_t rdi_value);

static _Atomic uint64_t armed;

static int fail(const char *message) {
    fprintf(stderr, "%s\n", message);
    return 1;
}

static void fill_image(uint8_t *image, uint32_t mode, uint32_t stored, uint64_t parent,
                       uint8_t uniform, uint8_t attrib) {
    memset(image, 0, EU4_GFX_DRAW_OBSERVE_CONTEXT_BYTES);
    memcpy(image + 0x8, &mode, sizeof mode);
    memcpy(image + 0x68, &stored, sizeof stored);
    memcpy(image + 0x70, &parent, sizeof parent);
    image[0x128] = uniform;
    image[0x170] = attrib;
}

static eu4_gfx_capture_frame frame_for(uint64_t rdi, uint64_t rsi, uint64_t rdx,
                                       uint64_t rcx) {
    eu4_gfx_capture_frame frame;
    memset(&frame, 0, sizeof frame);
    frame.rdi = rdi;
    frame.rsi = rsi;
    frame.rdx = rdx;
    frame.rcx = rcx;
    return frame;
}

static int test_unarmed(void) {
    eu4_gfx_draw_indexed_observe_test_reset();
    atomic_store_explicit(&armed, 0, memory_order_release);
    eu4_gfx_draw_indexed_observe_bind(&armed);
    eu4_gfx_capture_frame frame = frame_for(0x10, 3, 1, 2);
    eu4_gfx_draw_indexed_observe_entry(&frame);
    if (eu4_gfx_draw_indexed_observe_hits() != 1 ||
        eu4_gfx_draw_indexed_observe_post_ready() != 0 ||
        eu4_gfx_draw_indexed_observe_completed() != 0) {
        return fail("unarmed call recorded a sample");
    }
    return 0;
}

static int test_readable(FILE *earlier) {
    uint8_t image[EU4_GFX_DRAW_OBSERVE_CONTEXT_BYTES];
    uint64_t parent = 0x1000;
    fill_image(image, 4, 9, parent, 1, 0);
    uint64_t slot = (uint64_t)(uintptr_t)image;
    atomic_store_explicit(&armed, 1, memory_order_release);
    eu4_gfx_draw_indexed_observe_test_hold(63, 0x1111111111111111ull);
    eu4_gfx_capture_frame frame = frame_for((uint64_t)(uintptr_t)&slot, 3, 0x111,
                                            0xffffffffull);
    if (eu4_gfx_draw_indexed_observe_slot_state(0) != 0) {
        return fail("slot published before the call");
    }
    eu4_gfx_draw_indexed_observe_entry(&frame);
    if (eu4_gfx_draw_indexed_observe_slot_state(0) != 2 ||
        eu4_gfx_draw_indexed_observe_slot_state(63) != 1) {
        return fail("publication marker is wrong");
    }
    eu4_gfx_draw_observe_sample sample;
    if (!eu4_gfx_draw_indexed_observe_copy_sample(0, &sample) ||
        sample.rdi != (uint64_t)(uintptr_t)&slot || sample.rsi != 3 ||
        sample.rdx != 0x111 || sample.rcx != 0xffffffffull || sample.context_ok != 1 ||
        sample.refusal != 0 || sample.mode != 4 || sample.stored_count != 9 ||
        sample.parent != parent || sample.uniform_flag != 1 || sample.attrib_flag != 0) {
        return fail("raw sample fields do not match the context");
    }
    if (eu4_gfx_draw_indexed_observe_copy_sample(63, &sample)) {
        return fail("reserved slot was readable as a complete sample");
    }
    if (!eu4_gfx_draw_indexed_observe_write(earlier)) {
        return fail("write failed");
    }
    return 0;
}

static int test_refused(void) {
    long page = sysconf(_SC_PAGESIZE);
    void *guard = mmap(NULL, (size_t)page, PROT_NONE, MAP_PRIVATE | MAP_ANON, -1, 0);
    if (guard == MAP_FAILED) {
        return fail("guard page");
    }
    uint64_t slot = (uint64_t)(uintptr_t)guard;
    eu4_gfx_capture_frame frame = frame_for((uint64_t)(uintptr_t)&slot, 8, 9, 10);
    eu4_gfx_draw_indexed_observe_entry(&frame);
    eu4_gfx_draw_observe_sample sample;
    if (eu4_gfx_draw_indexed_observe_completed() != 2 ||
        eu4_gfx_draw_indexed_observe_refusals() != 1 ||
        !eu4_gfx_draw_indexed_observe_copy_sample(1, &sample) || sample.context_ok != 0 ||
        sample.refusal != 1 || sample.rsi != 8 || sample.rdx != 9 || sample.rcx != 10) {
        return fail("failed read was not a complete refusal");
    }
    return 0;
}

static int test_quota(void) {
    eu4_gfx_draw_indexed_observe_test_reset();
    atomic_store_explicit(&armed, 1, memory_order_release);
    eu4_gfx_draw_indexed_observe_bind(&armed);
    uint8_t image[EU4_GFX_DRAW_OBSERVE_CONTEXT_BYTES];
    fill_image(image, 5, 2, 0x20, 0, 1);
    uint64_t slot = (uint64_t)(uintptr_t)image;
    for (int i = 0; i < 64; i++) {
        eu4_gfx_capture_frame frame =
            frame_for((uint64_t)(uintptr_t)&slot, (uint64_t)(i + 1), 0, 0);
        eu4_gfx_draw_indexed_observe_entry(&frame);
    }
    eu4_gfx_draw_observe_sample first;
    if (!eu4_gfx_draw_indexed_observe_copy_sample(0, &first)) {
        return fail("first sample missing");
    }
    eu4_gfx_capture_frame extra = frame_for((uint64_t)(uintptr_t)&slot, 99, 0, 0);
    eu4_gfx_draw_indexed_observe_entry(&extra);
    eu4_gfx_draw_observe_sample again;
    if (eu4_gfx_draw_indexed_observe_completed() != 64 ||
        eu4_gfx_draw_indexed_observe_post_ready() != 65 ||
        !eu4_gfx_draw_indexed_observe_copy_sample(0, &again) || again.rsi != first.rsi) {
        return fail("quota did not stop recording");
    }
    return 0;
}

int main(int argc, char **argv) {
    if (argc != 2) {
        return fail("usage: observe_host samples.txt");
    }
    if (test_unarmed() != 0) {
        return 1;
    }
    FILE *file = fopen(argv[1], "w");
    if (file == NULL) {
        return fail("open");
    }
    if (test_readable(file) != 0 || test_refused() != 0) {
        fclose(file);
        return 1;
    }
    if (fclose(file) != 0) {
        return fail("close");
    }
    if (test_quota() != 0) {
        return 1;
    }
    puts("gfx draw indexed observe: ok");
    return 0;
}
