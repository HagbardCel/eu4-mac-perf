#include "eu4_gfx_set_constant_buffer_capture.h"

#include <assert.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define HANDLE_LENGTH 8u
#define OBSERVATION_START 200u
#define OBSERVATION_LENGTH 97u
#define CANARY_BEFORE_START 184u
#define CANARY_AFTER_START 297u
#define CANARY_LENGTH 16u
#define BUFFER_LENGTH 4u

_Static_assert(CANARY_BEFORE_START + CANARY_LENGTH == OBSERVATION_START,
               "context observation layout");
_Static_assert(OBSERVATION_START + OBSERVATION_LENGTH == CANARY_AFTER_START,
               "context observation layout");

static const uint8_t capture_site[EU4_GFX_CAPTURE_PATCH_SIZE] = {
    0x55, 0x48, 0x89, 0xe5, 0x48, 0x8b, 0x07,
    0x89, 0xc9, 0x41, 0x83, 0xf9, 0xff,
};

static const char module_sha[] =
    "b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d";
static const char policy_sha[] =
    "ea68e8e75bc8e293e77e3683a9b543d3e9ad61f6624de1dd718d284ba0c2cf31";
static const char function_id[] = "gfx:set-constant-buffer";
static const char fidelity[] = "faithful";
static const char policy_version[] = "p4-fid-v1";

enum {
    SLOT_IDLE = 0,
    SLOT_ACTIVE,
    SLOT_COMPLETE,
};

typedef struct {
    int state;
    unsigned depth;
    unsigned capture_depth;
    unsigned refusals;
    unsigned thread;
    unsigned long sequence;
    uint64_t rdi;
    uint64_t rsi;
    uint64_t rcx;
    uint64_t r9;
    uintptr_t handle_address;
    uint8_t handle[HANDLE_LENGTH];
    uintptr_t ctx_address;
    uint8_t ctx[OBSERVATION_LENGTH];
    uint8_t canary_before[CANARY_LENGTH];
    uint8_t canary_after[CANARY_LENGTH];
    int buffer_path;
    uintptr_t buffer_address;
    uint8_t buffer[BUFFER_LENGTH];
    uint8_t post_handle[HANDLE_LENGTH];
    uint8_t post_ctx[OBSERVATION_LENGTH];
    uint8_t post_canary_before[CANARY_LENGTH];
    uint8_t post_canary_after[CANARY_LENGTH];
    uint8_t post_buffer[BUFFER_LENGTH];
    uint64_t outcome;
} capture_slot;

static _Thread_local capture_slot slot;
static _Thread_local unsigned thread_number;
static _Thread_local int thread_ready;
static _Atomic unsigned next_thread_number = 0;

static void emit_region(FILE *file, const char *tag, const char *name,
                        uintptr_t address, const uint8_t *bytes,
                        size_t length) {
    fprintf(file, "%s name=%s address=0x%llx bytes=", tag, name,
            (unsigned long long)address);
    for (size_t i = 0; i < length; i++) {
        fprintf(file, "%02x", bytes[i]);
    }
    fputc('\n', file);
}

static void emit_carrier(FILE *file, const char *name, uint64_t value) {
    fprintf(file,
            "CARRIER name=%s kind=int bits=64 value=0x%llx "
            "confidence=observed\n",
            name, (unsigned long long)value);
}

static bool write_capture(void) {
    const char *base = getenv("EU4_GFX_CAPTURE_PATH");
    if (base == NULL || base[0] == '\0') {
        return false;
    }
    char path[1024];
    int written = snprintf(path, sizeof(path), "%s.%u", base, slot.thread);
    if (written < 0 || (size_t)written >= sizeof(path)) {
        return false;
    }
    FILE *file = fopen(path, "w");
    if (file == NULL) {
        return false;
    }

    fprintf(file, "FUNCTION-CAPTURE v1\n");
    fprintf(file,
            "CAPTURE capture=prb-offline scenario=%s epoch=prb-offline "
            "thread=%u sequence=%lu function=%s callsite=unavailable "
            "module=%s policy=%s fidelity=%s policy_version=%s synthetic=1\n",
            slot.buffer_path ? "s03:length-from-buffer"
                             : "s10:explicit-length",
            slot.thread, slot.sequence, function_id, module_sha, policy_sha,
            fidelity, policy_version);

    emit_region(file, "ENTRY", "handle", slot.handle_address, slot.handle,
                HANDLE_LENGTH);
    emit_region(file, "ENTRY", "ctx", slot.ctx_address + OBSERVATION_START,
                slot.ctx, OBSERVATION_LENGTH);
    emit_region(file, "ENTRY", "canary-before",
                slot.ctx_address + CANARY_BEFORE_START, slot.canary_before,
                CANARY_LENGTH);
    emit_region(file, "ENTRY", "canary-after",
                slot.ctx_address + CANARY_AFTER_START, slot.canary_after,
                CANARY_LENGTH);
    if (slot.buffer_path) {
        emit_region(file, "ENTRY", "buffer", slot.buffer_address, slot.buffer,
                    BUFFER_LENGTH);
    }

    emit_region(file, "EXIT", "handle", slot.handle_address, slot.post_handle,
                HANDLE_LENGTH);
    emit_region(file, "EXIT", "ctx", slot.ctx_address + OBSERVATION_START,
                slot.post_ctx, OBSERVATION_LENGTH);
    emit_region(file, "EXIT", "canary-before",
                slot.ctx_address + CANARY_BEFORE_START,
                slot.post_canary_before, CANARY_LENGTH);
    emit_region(file, "EXIT", "canary-after",
                slot.ctx_address + CANARY_AFTER_START, slot.post_canary_after,
                CANARY_LENGTH);
    if (slot.buffer_path) {
        emit_region(file, "EXIT", "buffer", slot.buffer_address,
                    slot.post_buffer, BUFFER_LENGTH);
    }

    emit_carrier(file, "RDI", slot.rdi);
    emit_carrier(file, "RSI", slot.rsi);
    emit_carrier(file, "RCX", slot.rcx);
    emit_carrier(file, "R9", slot.r9);
    fprintf(file, "OUTCOME register=RAX value=0x%llx confidence=observed\n",
            (unsigned long long)slot.outcome);
    fprintf(file, "END regions=%u carriers=4 outcomes=1 losses=0\n",
            slot.buffer_path ? 10u : 8u);
    if (ferror(file)) {
        (void)fclose(file);
        return false;
    }
    return fclose(file) == 0;
}

void eu4_gfx_set_constant_buffer_capture_thread_init(void) {
    if (thread_ready) {
        return;
    }
    thread_number = atomic_fetch_add_explicit(
        &next_thread_number, 1, memory_order_relaxed);
    thread_ready = 1;
}

void eu4_gfx_set_constant_buffer_capture_entry(eu4_gfx_capture_frame *frame) {
    assert(frame != NULL);
    assert(((uintptr_t)frame & 15u) == 0);
    slot.depth++;
    if (!thread_ready) {
        slot.refusals++;
        return;
    }
    if (slot.state != SLOT_IDLE) {
        slot.refusals++;
        return;
    }

    uintptr_t handle = (uintptr_t)frame->rdi;
    if (handle == 0) {
        slot.refusals++;
        return;
    }
    uint64_t ctx_address = 0;
    memcpy(&ctx_address, (const void *)handle, sizeof(ctx_address));
    int buffer_path = ((uint32_t)frame->r9 == 0xffffffffu);
    uintptr_t buffer_address = (uintptr_t)frame->rsi;
    if (ctx_address == 0 || (buffer_path && buffer_address == 0)) {
        slot.refusals++;
        return;
    }

    slot.state = SLOT_ACTIVE;
    slot.capture_depth = slot.depth;
    slot.thread = thread_number;
    slot.sequence++;
    slot.rdi = frame->rdi;
    slot.rsi = frame->rsi;
    slot.rcx = frame->rcx;
    slot.r9 = frame->r9;
    slot.handle_address = handle;
    slot.ctx_address = (uintptr_t)ctx_address;
    slot.buffer_path = buffer_path;
    slot.buffer_address = buffer_address;

    memcpy(slot.handle, (const void *)handle, HANDLE_LENGTH);
    memcpy(slot.ctx, (const void *)(slot.ctx_address + OBSERVATION_START),
           OBSERVATION_LENGTH);
    memcpy(slot.canary_before,
           (const void *)(slot.ctx_address + CANARY_BEFORE_START),
           CANARY_LENGTH);
    memcpy(slot.canary_after,
           (const void *)(slot.ctx_address + CANARY_AFTER_START),
           CANARY_LENGTH);
    if (buffer_path) {
        memcpy(slot.buffer, (const void *)buffer_address, BUFFER_LENGTH);
    }
}

void eu4_gfx_set_constant_buffer_capture_exit(eu4_gfx_capture_frame *frame) {
    assert(frame != NULL);
    assert(((uintptr_t)frame & 15u) == 0);
    if (slot.depth == 0) {
        return;
    }

    unsigned exiting_depth = slot.depth;
    slot.depth = exiting_depth - 1;
    if (slot.state != SLOT_ACTIVE || exiting_depth != slot.capture_depth) {
        return;
    }

    memcpy(slot.post_handle, (const void *)slot.handle_address, HANDLE_LENGTH);
    memcpy(slot.post_ctx,
           (const void *)(slot.ctx_address + OBSERVATION_START),
           OBSERVATION_LENGTH);
    memcpy(slot.post_canary_before,
           (const void *)(slot.ctx_address + CANARY_BEFORE_START),
           CANARY_LENGTH);
    memcpy(slot.post_canary_after,
           (const void *)(slot.ctx_address + CANARY_AFTER_START),
           CANARY_LENGTH);
    if (slot.buffer_path) {
        memcpy(slot.post_buffer, (const void *)slot.buffer_address,
               BUFFER_LENGTH);
    }
    slot.outcome = frame->rax;
    slot.state = SLOT_COMPLETE;
}

bool eu4_gfx_set_constant_buffer_capture_flush(void) {
    if (slot.state != SLOT_COMPLETE) {
        return false;
    }
    if (!write_capture()) {
        return false;
    }
    slot.state = SLOT_IDLE;
    return true;
}

unsigned eu4_gfx_set_constant_buffer_capture_refusals(void) {
    return slot.refusals;
}

bool eu4_gfx_set_constant_buffer_capture_install(void *site) {
    return eu4_gfx_capture_install(site, capture_site);
}
