#include "eu4_gfx_draw_indexed_observe.h"

#include <mach/mach.h>
#include <mach/mach_vm.h>
#include <stdatomic.h>
#include <string.h>

enum { SLOT_EMPTY = 0, SLOT_RESERVED = 1, SLOT_COMPLETE = 2 };

static eu4_gfx_draw_observe_sample slots[EU4_GFX_DRAW_OBSERVE_SAMPLES];
static _Atomic unsigned slot_state[EU4_GFX_DRAW_OBSERVE_SAMPLES];
static _Atomic uint64_t hits;
static _Atomic uint64_t post_ready;
static _Atomic unsigned completed;
static _Atomic unsigned refusals;
static _Atomic uint64_t *arm_word;

static bool read_bytes(void *destination, uintptr_t source, size_t length) {
    if (destination == NULL || source == 0 || length == 0) {
        return false;
    }
    if (source > UINTPTR_MAX - (length - 1)) {
        return false;
    }
    mach_vm_size_t got = 0;
    kern_return_t result = mach_vm_read_overwrite(
        mach_task_self(), (mach_vm_address_t)source, (mach_vm_size_t)length,
        (mach_vm_address_t)(uintptr_t)destination, &got);
    return result == KERN_SUCCESS && got == length;
}

void eu4_gfx_draw_indexed_observe_bind(void *word) {
    arm_word = word;
}

void eu4_gfx_draw_indexed_observe_entry(eu4_gfx_capture_frame *frame) {
    atomic_fetch_add_explicit(&hits, 1, memory_order_relaxed);
    if (arm_word == NULL ||
        atomic_load_explicit(arm_word, memory_order_acquire) == 0) {
        return;
    }
    uint64_t index =
        atomic_fetch_add_explicit(&post_ready, 1, memory_order_relaxed);
    if (index >= EU4_GFX_DRAW_OBSERVE_SAMPLES) {
        return;
    }
    atomic_store_explicit(&slot_state[index], SLOT_RESERVED, memory_order_relaxed);

    eu4_gfx_draw_observe_sample sample;
    memset(&sample, 0, sizeof sample);
    if (frame != NULL) {
        sample.rdi = frame->rdi;
        sample.rsi = frame->rsi;
        sample.rdx = frame->rdx;
        sample.rcx = frame->rcx;
    }

    uint64_t context = 0;
    uint8_t image[EU4_GFX_DRAW_OBSERVE_CONTEXT_BYTES];
    bool slot_ok = frame != NULL &&
                   read_bytes(&context, (uintptr_t)frame->rdi, sizeof context);
    bool image_ok = slot_ok && context != 0 &&
                    read_bytes(image, (uintptr_t)context, sizeof image);
    if (!image_ok) {
        sample.refusal = 1;
        atomic_fetch_add_explicit(&refusals, 1, memory_order_relaxed);
    } else {
        sample.context_ok = 1;
        memcpy(&sample.mode, image + 0x8, sizeof sample.mode);
        memcpy(&sample.stored_count, image + 0x68, sizeof sample.stored_count);
        memcpy(&sample.parent, image + 0x70, sizeof sample.parent);
        sample.uniform_flag = image[0x128];
        sample.attrib_flag = image[0x170];
    }
    slots[index] = sample;
    atomic_store_explicit(&slot_state[index], SLOT_COMPLETE, memory_order_release);
    atomic_fetch_add_explicit(&completed, 1, memory_order_release);
}

static unsigned count_flag(int which) {
    unsigned count = 0;
    for (unsigned index = 0; index < EU4_GFX_DRAW_OBSERVE_SAMPLES; index++) {
        if (atomic_load_explicit(&slot_state[index], memory_order_acquire) !=
            SLOT_COMPLETE) {
            continue;
        }
        if (!slots[index].context_ok) {
            continue;
        }
        uint8_t flag = which == 0 ? slots[index].uniform_flag : slots[index].attrib_flag;
        if (flag != 0) {
            count++;
        }
    }
    return count;
}

uint64_t eu4_gfx_draw_indexed_observe_hits(void) {
    return atomic_load_explicit(&hits, memory_order_acquire);
}

uint64_t eu4_gfx_draw_indexed_observe_post_ready(void) {
    return atomic_load_explicit(&post_ready, memory_order_acquire);
}

unsigned eu4_gfx_draw_indexed_observe_completed(void) {
    return atomic_load_explicit(&completed, memory_order_acquire);
}

unsigned eu4_gfx_draw_indexed_observe_refusals(void) {
    return atomic_load_explicit(&refusals, memory_order_acquire);
}

unsigned eu4_gfx_draw_indexed_observe_uniform_nonzero(void) {
    return count_flag(0);
}

unsigned eu4_gfx_draw_indexed_observe_attrib_nonzero(void) {
    return count_flag(1);
}

unsigned eu4_gfx_draw_indexed_observe_slot_state(unsigned index) {
    if (index >= EU4_GFX_DRAW_OBSERVE_SAMPLES) {
        return SLOT_EMPTY;
    }
    return atomic_load_explicit(&slot_state[index], memory_order_acquire);
}

bool eu4_gfx_draw_indexed_observe_copy_sample(unsigned index,
                                              eu4_gfx_draw_observe_sample *out) {
    if (out == NULL || index >= EU4_GFX_DRAW_OBSERVE_SAMPLES ||
        atomic_load_explicit(&slot_state[index], memory_order_acquire) !=
            SLOT_COMPLETE) {
        return false;
    }
    *out = slots[index];
    return true;
}

bool eu4_gfx_draw_indexed_observe_write(FILE *file) {
    if (file == NULL) {
        return false;
    }
    unsigned uniform = count_flag(0);
    unsigned attrib = count_flag(1);
    if (fprintf(file, "EU4-GFX-DRAW-OBSERVE v1\n") < 0 ||
        fprintf(file, "hits=%llu\n",
                (unsigned long long)eu4_gfx_draw_indexed_observe_hits()) < 0 ||
        fprintf(file, "post_ready=%llu\n",
                (unsigned long long)eu4_gfx_draw_indexed_observe_post_ready()) < 0 ||
        fprintf(file, "completed=%u\n", eu4_gfx_draw_indexed_observe_completed()) < 0 ||
        fprintf(file, "refusals=%u\n", eu4_gfx_draw_indexed_observe_refusals()) < 0 ||
        fprintf(file, "uniform_nonzero=%u\n", uniform) < 0 ||
        fprintf(file, "attrib_nonzero=%u\n", attrib) < 0) {
        return false;
    }
    for (unsigned index = 0; index < EU4_GFX_DRAW_OBSERVE_SAMPLES; index++) {
        if (atomic_load_explicit(&slot_state[index], memory_order_acquire) !=
            SLOT_COMPLETE) {
            continue;
        }
        const eu4_gfx_draw_observe_sample *sample = &slots[index];
        if (sample->context_ok) {
            if (fprintf(file,
                        "sample rdi=0x%llx rsi=0x%llx rdx=0x%llx rcx=0x%llx "
                        "context_ok=1 refusal=%u mode=0x%x stored_count=0x%x "
                        "parent=0x%llx uniform=0x%x attrib=0x%x\n",
                        (unsigned long long)sample->rdi,
                        (unsigned long long)sample->rsi,
                        (unsigned long long)sample->rdx,
                        (unsigned long long)sample->rcx, sample->refusal,
                        sample->mode, sample->stored_count,
                        (unsigned long long)sample->parent, sample->uniform_flag,
                        sample->attrib_flag) < 0) {
                return false;
            }
        } else if (fprintf(file,
                           "sample rdi=0x%llx rsi=0x%llx rdx=0x%llx rcx=0x%llx "
                           "context_ok=0 refusal=%u\n",
                           (unsigned long long)sample->rdi,
                           (unsigned long long)sample->rsi,
                           (unsigned long long)sample->rdx,
                           (unsigned long long)sample->rcx, sample->refusal) < 0) {
            return false;
        }
    }
    return fprintf(file, "END\n") >= 0;
}

void eu4_gfx_draw_indexed_observe_test_reset(void) {
    memset(slots, 0, sizeof slots);
    for (unsigned index = 0; index < EU4_GFX_DRAW_OBSERVE_SAMPLES; index++) {
        atomic_store_explicit(&slot_state[index], SLOT_EMPTY, memory_order_relaxed);
    }
    atomic_store_explicit(&hits, 0, memory_order_relaxed);
    atomic_store_explicit(&post_ready, 0, memory_order_relaxed);
    atomic_store_explicit(&completed, 0, memory_order_relaxed);
    atomic_store_explicit(&refusals, 0, memory_order_relaxed);
    arm_word = NULL;
}

void eu4_gfx_draw_indexed_observe_test_hold(unsigned index, uint64_t rdi_value) {
    if (index >= EU4_GFX_DRAW_OBSERVE_SAMPLES) {
        return;
    }
    memset(&slots[index], 0, sizeof slots[index]);
    slots[index].rdi = rdi_value;
    atomic_store_explicit(&slot_state[index], SLOT_RESERVED, memory_order_release);
}
