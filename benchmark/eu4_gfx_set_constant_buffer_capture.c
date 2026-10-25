#include "eu4_gfx_set_constant_buffer_capture.h"

#include <assert.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

_Static_assert(EU4_GFX_CAPTURE_CANARY_BEFORE_START +
                       EU4_GFX_CAPTURE_CANARY_LENGTH ==
                   EU4_GFX_CAPTURE_OBSERVATION_START,
               "context observation layout");
_Static_assert(EU4_GFX_CAPTURE_OBSERVATION_START +
                       EU4_GFX_CAPTURE_OBSERVATION_LENGTH ==
                   EU4_GFX_CAPTURE_CANARY_AFTER_START,
               "context observation layout");
_Static_assert(EU4_GFX_CAPTURE_EXTENT_START +
                       EU4_GFX_CAPTURE_EXTENT_LENGTH ==
                   EU4_GFX_CAPTURE_CANARY_AFTER_START +
                       EU4_GFX_CAPTURE_CANARY_LENGTH,
               "context extent layout");

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

static const eu4_gfx_capture_metadata prb_metadata = {
    "prb-offline",
    "s03:length-from-buffer",
    "s10:explicit-length",
    "prb-offline",
    "unavailable",
    true,
};

static _Thread_local eu4_gfx_capture_record tls_record;
static _Thread_local unsigned tls_thread_number;
static _Thread_local int tls_thread_ready;
static _Atomic unsigned next_thread_number = 0;
static const eu4_gfx_capture_live_ops *live;

static bool read_bytes(void *destination, const void *source, size_t length) {
    if (destination == NULL || source == NULL || length == 0) {
        return false;
    }
    uintptr_t address = (uintptr_t)source;
    if (address > UINTPTR_MAX - (length - 1)) {
        return false;
    }
    if (live == NULL || !live->safe_read) {
        memcpy(destination, source, length);
        return true;
    }
    mach_vm_size_t got = 0;
    kern_return_t result = mach_vm_read_overwrite(
        mach_task_self(), (mach_vm_address_t)address, (mach_vm_size_t)length,
        (mach_vm_address_t)(uintptr_t)destination, &got);
    return result == KERN_SUCCESS && got == length;
}

static void refuse_capture(eu4_gfx_capture_record *record,
                           eu4_gfx_capture_refusal reason) {
    if (record != NULL) {
        record->refusals++;
    }
    if (live != NULL && live->refuse != NULL) {
        live->refuse(reason);
    }
}

static void incomplete_capture(unsigned ticket) {
    if (live != NULL && live->incomplete != NULL) {
        live->incomplete(ticket);
    }
}

static void fail_capture(eu4_gfx_capture_record *record, unsigned ticket) {
    if (record != NULL && record->diversity && live != NULL &&
        live->publish != NULL) {
        record->phase_b_failed = 1;
        (void)live->publish(ticket, record);
        return;
    }
    incomplete_capture(ticket);
    if (record != NULL) {
        record->refusals++;
    }
}

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

bool eu4_gfx_set_constant_buffer_capture_write(
    FILE *file, const eu4_gfx_capture_record *record,
    const eu4_gfx_capture_metadata *metadata) {
    if (file == NULL || record == NULL || metadata == NULL) {
        return false;
    }

    fprintf(file, "FUNCTION-CAPTURE v1\n");
    fprintf(file,
            "CAPTURE capture=%s scenario=%s epoch=%s thread=%u sequence=%lu "
            "function=%s callsite=%s module=%s policy=%s fidelity=%s "
            "policy_version=%s synthetic=%d\n",
            metadata->capture,
            record->buffer_path ? metadata->scenario_buffer
                                : metadata->scenario_explicit,
            metadata->epoch, record->thread, record->sequence, function_id,
            metadata->callsite, module_sha, policy_sha, fidelity,
            policy_version, metadata->synthetic ? 1 : 0);

    emit_region(file, "ENTRY", "handle", record->handle_address, record->handle,
                EU4_GFX_CAPTURE_HANDLE_LENGTH);
    emit_region(file, "ENTRY", "ctx",
                record->ctx_address + EU4_GFX_CAPTURE_OBSERVATION_START,
                record->ctx, EU4_GFX_CAPTURE_OBSERVATION_LENGTH);
    emit_region(file, "ENTRY", "canary-before",
                record->ctx_address + EU4_GFX_CAPTURE_CANARY_BEFORE_START,
                record->canary_before, EU4_GFX_CAPTURE_CANARY_LENGTH);
    emit_region(file, "ENTRY", "canary-after",
                record->ctx_address + EU4_GFX_CAPTURE_CANARY_AFTER_START,
                record->canary_after, EU4_GFX_CAPTURE_CANARY_LENGTH);
    if (record->buffer_path) {
        emit_region(file, "ENTRY", "buffer", record->buffer_address,
                    record->buffer, EU4_GFX_CAPTURE_BUFFER_LENGTH);
    }

    emit_region(file, "EXIT", "handle", record->handle_address,
                record->post_handle, EU4_GFX_CAPTURE_HANDLE_LENGTH);
    emit_region(file, "EXIT", "ctx",
                record->ctx_address + EU4_GFX_CAPTURE_OBSERVATION_START,
                record->post_ctx, EU4_GFX_CAPTURE_OBSERVATION_LENGTH);
    emit_region(file, "EXIT", "canary-before",
                record->ctx_address + EU4_GFX_CAPTURE_CANARY_BEFORE_START,
                record->post_canary_before, EU4_GFX_CAPTURE_CANARY_LENGTH);
    emit_region(file, "EXIT", "canary-after",
                record->ctx_address + EU4_GFX_CAPTURE_CANARY_AFTER_START,
                record->post_canary_after, EU4_GFX_CAPTURE_CANARY_LENGTH);
    if (record->buffer_path) {
        emit_region(file, "EXIT", "buffer", record->buffer_address,
                    record->post_buffer, EU4_GFX_CAPTURE_BUFFER_LENGTH);
    }

    emit_carrier(file, "RDI", record->rdi);
    emit_carrier(file, "RSI", record->rsi);
    emit_carrier(file, "RCX", record->rcx);
    emit_carrier(file, "R9", record->r9);
    fprintf(file, "OUTCOME register=RAX value=0x%llx confidence=observed\n",
            (unsigned long long)record->outcome);
    fprintf(file, "END regions=%u carriers=4 outcomes=1 losses=0\n",
            record->buffer_path ? 10u : 8u);
    return ferror(file) == 0;
}

static bool write_capture(void) {
    const char *base = getenv("EU4_GFX_CAPTURE_PATH");
    if (base == NULL || base[0] == '\0') {
        return false;
    }
    char path[1024];
    int written = snprintf(path, sizeof(path), "%s.%u", base, tls_record.thread);
    if (written < 0 || (size_t)written >= sizeof(path)) {
        return false;
    }
    FILE *file = fopen(path, "w");
    if (file == NULL) {
        return false;
    }
    bool ok = eu4_gfx_set_constant_buffer_capture_write(
        file, &tls_record, &prb_metadata);
    int closed = fclose(file);
    return ok && closed == 0;
}

void eu4_gfx_set_constant_buffer_capture_thread_init(void) {
    if (live != NULL && live->thread_claim != NULL) {
        (void)live->thread_claim();
        return;
    }
    if (tls_thread_ready) {
        return;
    }
    tls_thread_number = atomic_fetch_add_explicit(
        &next_thread_number, 1, memory_order_relaxed);
    tls_thread_ready = 1;
}

void eu4_gfx_set_constant_buffer_capture_set_live(
    const eu4_gfx_capture_live_ops *ops) {
    live = ops;
}

void eu4_gfx_set_constant_buffer_capture_entry(eu4_gfx_capture_frame *frame) {
    assert(frame != NULL);
    assert(((uintptr_t)frame & 15u) == 0);

    eu4_gfx_capture_record *record = NULL;
    bool guarded = false;
    if (live != NULL) {
        if (live->begin == NULL) {
            return;
        }
        if (!live->begin()) {
            /* Preserve nesting for an outstanding capture after CLOSING. */
            record = live->thread_lookup != NULL ? live->thread_lookup() : NULL;
            if (record != NULL) {
                record->depth++;
            }
            return;
        }
        guarded = true;
        record = live->thread_claim != NULL ? live->thread_claim() : NULL;
        if (record == NULL) {
            live->end();
            return;
        }
    } else {
        record = &tls_record;
        if (!tls_thread_ready) {
            record->refusals++;
            return;
        }
    }
    record->depth++;

    if (record->state != EU4_GFX_CAPTURE_SLOT_IDLE) {
        refuse_capture(record, EU4_GFX_CAPTURE_REFUSE_BUSY);
        goto done;
    }
    record->buffer_snapshotted = 0;
    record->diversity = 0;
    record->phase_b_failed = 0;
    record->snapshot_mismatch = 0;
    record->coverage_designated = 0;
    record->sig_index = -1;

    uint32_t slot = (uint32_t)frame->rcx;
    if (slot >= EU4_GFX_CAPTURE_SLOT_MAX) {
        refuse_capture(record, EU4_GFX_CAPTURE_REFUSE_SLOT);
        goto done;
    }

    uintptr_t handle = (uintptr_t)frame->rdi;
    if (handle == 0) {
        refuse_capture(record, EU4_GFX_CAPTURE_REFUSE_POINTER);
        goto done;
    }
    if (handle > UINTPTR_MAX - EU4_GFX_CAPTURE_HANDLE_LENGTH) {
        refuse_capture(record, EU4_GFX_CAPTURE_REFUSE_ADDRESS);
        goto done;
    }
    int buffer_path = ((uint32_t)frame->r9 == 0xffffffffu);
    uintptr_t buffer_address = (uintptr_t)frame->rsi;
    if (buffer_path && buffer_address == 0) {
        refuse_capture(record, EU4_GFX_CAPTURE_REFUSE_POINTER);
        goto done;
    }
    if (buffer_path &&
        buffer_address > UINTPTR_MAX - EU4_GFX_CAPTURE_BUFFER_LENGTH) {
        refuse_capture(record, EU4_GFX_CAPTURE_REFUSE_ADDRESS);
        goto done;
    }

    unsigned ticket = 0;
    if (live != NULL && live->select != NULL) {
        int selected = live->select(record, frame);
        if (selected < 0) {
            goto done;
        }
        ticket = (unsigned)selected;
    } else if (live != NULL && live->admit != NULL) {
        int admitted = live->admit(frame);
        if (admitted < 0) {
            goto done;
        }
        ticket = (unsigned)admitted;
    }

    record->ticket = ticket;
    if (live == NULL) {
        record->thread = tls_thread_number;
    }
    record->sequence++;
    record->rdi = frame->rdi;
    record->rsi = frame->rsi;
    record->rcx = frame->rcx;
    record->r9 = frame->r9;
    record->handle_address = handle;
    record->buffer_path = buffer_path;
    record->buffer_address = buffer_address;

    if (!read_bytes(record->handle, (const void *)handle,
                    EU4_GFX_CAPTURE_HANDLE_LENGTH)) {
        fail_capture(record, ticket);
        goto done;
    }
    uint64_t ctx_address = 0;
    memcpy(&ctx_address, record->handle, sizeof(ctx_address));
    if (ctx_address == 0 ||
        ctx_address > UINTPTR_MAX -
                          (EU4_GFX_CAPTURE_CANARY_AFTER_START +
                           EU4_GFX_CAPTURE_CANARY_LENGTH)) {
        fail_capture(record, ticket);
        goto done;
    }
    record->ctx_address = (uintptr_t)ctx_address;

    uint8_t extent[EU4_GFX_CAPTURE_EXTENT_LENGTH];
    if (!read_bytes(extent,
                    (const void *)(record->ctx_address +
                                   EU4_GFX_CAPTURE_EXTENT_START),
                    sizeof(extent))) {
        fail_capture(record, ticket);
        goto done;
    }
    memcpy(record->canary_before, extent,
           EU4_GFX_CAPTURE_CANARY_LENGTH);
    memcpy(record->ctx, extent + EU4_GFX_CAPTURE_CANARY_LENGTH,
           EU4_GFX_CAPTURE_OBSERVATION_LENGTH);
    memcpy(record->canary_after,
           extent + EU4_GFX_CAPTURE_CANARY_LENGTH +
               EU4_GFX_CAPTURE_OBSERVATION_LENGTH,
           EU4_GFX_CAPTURE_CANARY_LENGTH);

    if (buffer_path && !record->buffer_snapshotted &&
        !read_bytes(record->buffer, (const void *)buffer_address,
                    EU4_GFX_CAPTURE_BUFFER_LENGTH)) {
        fail_capture(record, ticket);
        goto done;
    }

    record->capture_depth = record->depth;
    record->state = EU4_GFX_CAPTURE_SLOT_ACTIVE;

done:
    if (guarded) {
        live->end();
    }
}

static bool read_exit(eu4_gfx_capture_record *record) {
    if (!read_bytes(record->post_handle,
                    (const void *)record->handle_address,
                    EU4_GFX_CAPTURE_HANDLE_LENGTH)) {
        return false;
    }
    uint8_t extent[EU4_GFX_CAPTURE_EXTENT_LENGTH];
    if (!read_bytes(extent,
                    (const void *)(record->ctx_address +
                                   EU4_GFX_CAPTURE_EXTENT_START),
                    sizeof(extent))) {
        return false;
    }
    memcpy(record->post_canary_before, extent,
           EU4_GFX_CAPTURE_CANARY_LENGTH);
    memcpy(record->post_ctx, extent + EU4_GFX_CAPTURE_CANARY_LENGTH,
           EU4_GFX_CAPTURE_OBSERVATION_LENGTH);
    memcpy(record->post_canary_after,
           extent + EU4_GFX_CAPTURE_CANARY_LENGTH +
               EU4_GFX_CAPTURE_OBSERVATION_LENGTH,
           EU4_GFX_CAPTURE_CANARY_LENGTH);
    if (record->buffer_path &&
        !read_bytes(record->post_buffer,
                    (const void *)record->buffer_address,
                    EU4_GFX_CAPTURE_BUFFER_LENGTH)) {
        return false;
    }
    return true;
}

void eu4_gfx_set_constant_buffer_capture_exit(eu4_gfx_capture_frame *frame) {
    assert(frame != NULL);
    assert(((uintptr_t)frame & 15u) == 0);

    eu4_gfx_capture_record *record =
        live != NULL
            ? (live->thread_lookup != NULL ? live->thread_lookup() : NULL)
            : &tls_record;
    if (record == NULL || record->depth == 0) {
        return;
    }

    unsigned exiting_depth = record->depth;
    record->depth = exiting_depth - 1;
    if (record->state != EU4_GFX_CAPTURE_SLOT_ACTIVE ||
        exiting_depth != record->capture_depth) {
        return;
    }

    if (!read_exit(record)) {
        fail_capture(record, record->ticket);
        record->state = EU4_GFX_CAPTURE_SLOT_IDLE;
        return;
    }
    record->outcome = frame->rax;
    record->state = EU4_GFX_CAPTURE_SLOT_COMPLETE;

    if (live != NULL) {
        if (live->publish != NULL) {
            (void)live->publish(record->ticket, record);
        }
        record->state = EU4_GFX_CAPTURE_SLOT_IDLE;
    }
}

bool eu4_gfx_set_constant_buffer_capture_read(void *destination,
                                              const void *source,
                                              size_t length) {
    return read_bytes(destination, source, length);
}

static uint32_t load_u32(const uint8_t *bytes) {
    uint32_t value;
    memcpy(&value, bytes, sizeof(value));
    return value;
}

static uint64_t load_u64(const uint8_t *bytes) {
    uint64_t value;
    memcpy(&value, bytes, sizeof(value));
    return value;
}

static int interval_less(const void *left, const void *right) {
    const uintptr_t *a = left;
    const uintptr_t *b = right;
    if (a[0] < b[0]) {
        return -1;
    }
    if (a[0] > b[0]) {
        return 1;
    }
    return 0;
}

static void missing_add(const char **names, unsigned *count, const char *name) {
    if (*count < 24) {
        names[(*count)++] = name;
    }
}

static int name_less(const void *left, const void *right) {
    return strcmp(*(const char *const *)left, *(const char *const *)right);
}

unsigned eu4_gfx_set_constant_buffer_capture_assess(
    const eu4_gfx_capture_record *record, eu4_gfx_behavior *behavior,
    char *missing_text, size_t missing_length) {
    const char *names[24];
    unsigned count = 0;
    unsigned i;
    int buffer_path = record->buffer_path;
    uint32_t slot = (uint32_t)record->rcx;
    uint64_t ctx_base = load_u64(record->handle);

    if (record->handle_address == 0 ||
        record->handle_address != (uintptr_t)record->rdi) {
        missing_add(names, &count, "read:handle");
    }
    if (ctx_base == 0 || record->ctx_address != (uintptr_t)ctx_base) {
        missing_add(names, &count, "read:ctx");
        missing_add(names, &count, "read:canary-before");
        missing_add(names, &count, "read:canary-after");
    }
    if (buffer_path && record->buffer_address != (uintptr_t)record->rsi) {
        missing_add(names, &count, "read:buffer");
    }

    {
        int fields_ok = slot < EU4_GFX_CAPTURE_SLOT_MAX && ctx_base != 0 &&
                        record->ctx_address == (uintptr_t)ctx_base;
        uint32_t word_rel = slot * 16u;
        uint32_t pointer_rel = 8u + slot * 16u;
        int word_ok = 0;
        int pointer_ok = 0;
        int dirty_ok = 0;
        if (fields_ok && word_rel + 4u <= EU4_GFX_CAPTURE_OBSERVATION_LENGTH &&
            pointer_rel + 8u <= EU4_GFX_CAPTURE_OBSERVATION_LENGTH) {
            word_ok = 1;
            pointer_ok = load_u64(record->post_ctx + pointer_rel) == record->rsi;
            dirty_ok = record->post_ctx[96] == 1;
        }
        if (!word_ok) {
            missing_add(names, &count, "post:word_count");
        }
        if (!pointer_ok) {
            missing_add(names, &count, "post:buffer");
        }
        if (!dirty_ok) {
            missing_add(names, &count, "post:dirty");
        }
    }

    if (!(load_u64(record->handle) == load_u64(record->post_handle) &&
          record->handle_address != 0)) {
        missing_add(names, &count, "preserve:handle");
    }
    if (memcmp(record->canary_before, record->post_canary_before,
               EU4_GFX_CAPTURE_CANARY_LENGTH) != 0) {
        missing_add(names, &count, "preserve:canary-before");
    }
    if (memcmp(record->canary_after, record->post_canary_after,
               EU4_GFX_CAPTURE_CANARY_LENGTH) != 0) {
        missing_add(names, &count, "preserve:canary-after");
    }
    if (slot < EU4_GFX_CAPTURE_SLOT_MAX) {
        uint8_t allowed[EU4_GFX_CAPTURE_OBSERVATION_LENGTH];
        memset(allowed, 0, sizeof(allowed));
        uint32_t word_rel = slot * 16u;
        uint32_t pointer_rel = 8u + slot * 16u;
        if (word_rel + 4u <= EU4_GFX_CAPTURE_OBSERVATION_LENGTH) {
            memset(allowed + word_rel, 1, 4);
        }
        if (pointer_rel + 8u <= EU4_GFX_CAPTURE_OBSERVATION_LENGTH) {
            memset(allowed + pointer_rel, 1, 8);
        }
        allowed[96] = 1;
        for (i = 0; i < EU4_GFX_CAPTURE_OBSERVATION_LENGTH; i++) {
            if (record->ctx[i] != record->post_ctx[i] && !allowed[i]) {
                missing_add(names, &count, "preserve:ctx");
                break;
            }
        }
    } else {
        missing_add(names, &count, "preserve:ctx");
    }
    if (buffer_path && memcmp(record->buffer, record->post_buffer,
                              EU4_GFX_CAPTURE_BUFFER_LENGTH) != 0) {
        missing_add(names, &count, "preserve:buffer");
    }
    if (ctx_base == 0 || record->outcome != ctx_base) {
        missing_add(names, &count, "outcome:RAX");
    }
    if (slot >= EU4_GFX_CAPTURE_SLOT_MAX) {
        missing_add(names, &count, "domain:slot");
    }
    {
        uintptr_t spans[5][2];
        unsigned n = 0;
        spans[n][0] = record->handle_address;
        spans[n][1] = record->handle_address + EU4_GFX_CAPTURE_HANDLE_LENGTH;
        n++;
        if (record->ctx_address != 0) {
            spans[n][0] = record->ctx_address + EU4_GFX_CAPTURE_CANARY_BEFORE_START;
            spans[n][1] = spans[n][0] + EU4_GFX_CAPTURE_CANARY_LENGTH;
            n++;
            spans[n][0] = record->ctx_address + EU4_GFX_CAPTURE_OBSERVATION_START;
            spans[n][1] = spans[n][0] + EU4_GFX_CAPTURE_OBSERVATION_LENGTH;
            n++;
            spans[n][0] = record->ctx_address + EU4_GFX_CAPTURE_CANARY_AFTER_START;
            spans[n][1] = spans[n][0] + EU4_GFX_CAPTURE_CANARY_LENGTH;
            n++;
        }
        if (buffer_path) {
            spans[n][0] = record->buffer_address;
            spans[n][1] = record->buffer_address + EU4_GFX_CAPTURE_BUFFER_LENGTH;
            n++;
        }
        qsort(spans, n, sizeof(spans[0]), interval_less);
        for (i = 0; i + 1 < n; i++) {
            if (spans[i][1] > spans[i + 1][0]) {
                missing_add(names, &count, "domain:disjoint");
                break;
            }
        }
    }

    if (count > 1) {
        qsort(names, count, sizeof(names[0]), name_less);
    }
    if (missing_text != NULL && missing_length > 0) {
        size_t used = 0;
        missing_text[0] = '\0';
        for (i = 0; i < count; i++) {
            int wrote = snprintf(missing_text + used, missing_length - used,
                                 "%s%s", i == 0 ? "" : ",", names[i]);
            if (wrote < 0 || (size_t)wrote >= missing_length - used) {
                break;
            }
            used += (size_t)wrote;
        }
    }

    if (behavior != NULL) {
        memset(behavior, 0, sizeof(*behavior));
        behavior->slot = slot;
        behavior->input_value = record->screen_input;
        behavior->ok = count == 0;
        if (slot < EU4_GFX_CAPTURE_SLOT_MAX &&
            slot * 16u + 4u <= EU4_GFX_CAPTURE_OBSERVATION_LENGTH) {
            behavior->word_count = load_u32(record->post_ctx + slot * 16u);
            behavior->dirty = record->post_ctx[96];
        }
        if (buffer_path) {
            uint32_t dword = load_u32(record->buffer);
            behavior->selector_buffer = 1;
            behavior->input_class = dword == 0 ? 1 : 2;
            behavior->buffer_state =
                memcmp(record->buffer, record->post_buffer,
                       EU4_GFX_CAPTURE_BUFFER_LENGTH) == 0
                    ? 1
                    : 2;
            if (record->buffer_address == record->handle_address) {
                behavior->alias = 1;
            } else if (record->buffer_address == (uintptr_t)ctx_base) {
                behavior->alias = 2;
            } else {
                behavior->alias = 3;
            }
        } else {
            behavior->input_class = 0;
            behavior->buffer_state = 0;
            behavior->alias = 0;
        }
        behavior->handle_state =
            memcmp(record->handle, record->post_handle,
                   EU4_GFX_CAPTURE_HANDLE_LENGTH) == 0
                ? 0
                : 1;
        if (slot < EU4_GFX_CAPTURE_SLOT_MAX &&
            8u + slot * 16u + 8u <= EU4_GFX_CAPTURE_OBSERVATION_LENGTH) {
            behavior->pointer_role =
                load_u64(record->post_ctx + 8u + slot * 16u) == record->rsi ? 0
                                                                           : 1;
        } else {
            behavior->pointer_role = 1;
            behavior->ok = 0;
        }
        behavior->rax_class =
            record->outcome == ctx_base && ctx_base != 0 ? 0 : 1;
        behavior->canary =
            memcmp(record->canary_before, record->post_canary_before,
                   EU4_GFX_CAPTURE_CANARY_LENGTH) == 0 &&
                    memcmp(record->canary_after, record->post_canary_after,
                           EU4_GFX_CAPTURE_CANARY_LENGTH) == 0
                ? 0
                : 1;
    }
    return count;
}

bool eu4_gfx_set_constant_buffer_capture_flush(void) {
    if (live != NULL || tls_record.state != EU4_GFX_CAPTURE_SLOT_COMPLETE) {
        return false;
    }
    if (!write_capture()) {
        return false;
    }
    tls_record.state = EU4_GFX_CAPTURE_SLOT_IDLE;
    return true;
}

unsigned eu4_gfx_set_constant_buffer_capture_refusals(void) {
    eu4_gfx_capture_record *record =
        live != NULL
            ? (live->thread_lookup != NULL ? live->thread_lookup() : NULL)
            : &tls_record;
    return record != NULL ? record->refusals : 0;
}

bool eu4_gfx_set_constant_buffer_capture_install(void *site) {
    return eu4_gfx_capture_install(site, capture_site);
}
