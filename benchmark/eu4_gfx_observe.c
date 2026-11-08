#include "eu4_gfx_observe.h"

#include "eu4_gfx_observe_buffers.h"
#include "eu4_gfx_observe_draw.h"
#include "eu4_gfx_observe_viewport.h"
#include "eu4_gfx_site_patch.h"
#include "eu4_gfx_startup_trace.h"

#include <errno.h>
#include <fcntl.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

static const eu4_gfx_observe_spec kSpecs[] = {
#define OBS_ENTRY(symbol, rva, prefix, reloc, entry_cb, exit_cb) \
    {symbol, rva, (const uint8_t *)(prefix), sizeof(prefix) - 1, reloc, \
     entry_cb, exit_cb},
#include "eu4_gfx_observe_entries.def"
#undef OBS_ENTRY
};

static _Atomic uint32_t g_enabled;
static _Atomic uint32_t g_installed;
static _Atomic uint32_t g_max_events = EU4_GFX_OBSERVE_DEFAULT_MAX_EVENTS;
static _Atomic uint32_t g_duration_ms = EU4_GFX_OBSERVE_DEFAULT_DURATION_MS;
static _Atomic uint32_t g_capacity = EU4_GFX_OBSERVE_RECORD_CAP;
static _Atomic uint32_t g_admitted;
static _Atomic uint32_t g_committed;
static _Atomic uint32_t g_active;
static _Atomic uint32_t g_overlaps;
static _Atomic uint32_t g_refused;
static _Atomic uint32_t g_unmatched;
static _Atomic uint32_t g_unavailable;
static _Atomic uint32_t g_overflow;
static _Atomic uint64_t g_first_event_ns;
static _Atomic uint64_t g_last_event_ns;
static _Atomic int g_init_seen;
static _Atomic uint64_t g_init_call_id;
static _Atomic int g_test_unpaired;
static _Atomic int g_test_fail_output;
static _Atomic int g_test_overlap;
static _Atomic int g_test_fail_install_after = -1;
static _Atomic int g_finalized;
static _Atomic int g_finalized_ok;
static _Atomic uint32_t g_retention = EU4_GFX_OBSERVE_RETENTION_LEGACY;
static _Atomic uint32_t g_stop_policy = EU4_GFX_OBSERVE_STOP_QUOTA;
static _Atomic uint64_t g_target_caller;
static _Atomic uint64_t g_target_draw_call_id;
static _Atomic uint32_t g_target_hit;
static _Atomic uint32_t g_observed;
static _Atomic uint32_t g_draw_observed;
static _Atomic uint32_t g_draw_at_caller;
static _Atomic uint32_t g_draw_qualified;
static _Atomic uint32_t g_draw_mode_counts[EU4_GFX_OBSERVE_MODE_SLOTS];
static _Atomic uint32_t g_draw_count_buckets[4];
static _Atomic uint64_t g_draw_caller_rva[EU4_GFX_OBSERVE_CALLER_SLOTS];
static _Atomic uint32_t g_draw_caller_count[EU4_GFX_OBSERVE_CALLER_SLOTS];
static _Atomic uint32_t g_draw_caller_overflow;
static _Atomic uint32_t g_primitive_sets;
static _Atomic uint32_t g_matching_retained;
static _Atomic uint32_t g_retention_budget;
static _Atomic uint32_t g_payload_gaps;
static eu4_gfx_observe_record g_records[EU4_GFX_OBSERVE_RECORD_CAP];
static uint8_t g_payload[EU4_GFX_OBSERVE_PAYLOAD_CAP];
static _Atomic uint32_t g_payload_used;
static const eu4_gfx_observe_spec *g_selected[EU4_GFX_OBSERVE_MAX_SPECS];
static size_t g_selected_count;
static eu4_gfx_site g_sites[EU4_GFX_OBSERVE_MAX_SPECS];
static uintptr_t g_image_base;
static uintptr_t g_text_begin;
static uintptr_t g_text_end;

typedef struct {
    uint32_t slot;
    int admitted;
    int abandoned;
    eu4_gfx_observe_call call;
} tls_frame;

typedef struct {
    tls_frame frames[EU4_GFX_OBSERVE_MAX_SPECS];
    unsigned depth;
    unsigned refuse_depth[EU4_GFX_OBSERVE_MAX_SPECS];
} tls_state;

static _Thread_local tls_state g_tls;

static eu4_gfx_observe_hook *const g_entry_hooks[EU4_GFX_OBSERVE_MAX_SPECS] = {
    &eu4_gfx_observe_entry_0, &eu4_gfx_observe_entry_1,
    &eu4_gfx_observe_entry_2, &eu4_gfx_observe_entry_3,
    &eu4_gfx_observe_entry_4, &eu4_gfx_observe_entry_5,
    &eu4_gfx_observe_entry_6, &eu4_gfx_observe_entry_7,
};
static eu4_gfx_observe_hook *const g_exit_hooks[EU4_GFX_OBSERVE_MAX_SPECS] = {
    &eu4_gfx_observe_exit_0, &eu4_gfx_observe_exit_1,
    &eu4_gfx_observe_exit_2, &eu4_gfx_observe_exit_3,
    &eu4_gfx_observe_exit_4, &eu4_gfx_observe_exit_5,
    &eu4_gfx_observe_exit_6, &eu4_gfx_observe_exit_7,
};
static void *const g_gateways[EU4_GFX_OBSERVE_MAX_SPECS] = {
    (void *)eu4_gfx_observe_gateway_0, (void *)eu4_gfx_observe_gateway_1,
    (void *)eu4_gfx_observe_gateway_2, (void *)eu4_gfx_observe_gateway_3,
    (void *)eu4_gfx_observe_gateway_4, (void *)eu4_gfx_observe_gateway_5,
    (void *)eu4_gfx_observe_gateway_6, (void *)eu4_gfx_observe_gateway_7,
};
static void **const g_trampoline_slots[EU4_GFX_OBSERVE_MAX_SPECS] = {
    &eu4_gfx_observe_trampoline_0, &eu4_gfx_observe_trampoline_1,
    &eu4_gfx_observe_trampoline_2, &eu4_gfx_observe_trampoline_3,
    &eu4_gfx_observe_trampoline_4, &eu4_gfx_observe_trampoline_5,
    &eu4_gfx_observe_trampoline_6, &eu4_gfx_observe_trampoline_7,
};

static uint64_t now_ns(void) {
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0) {
        return 0;
    }
    return (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
}

static uint64_t thread_id(void) {
    uint64_t value = 0;
    (void)pthread_threadid_np(NULL, &value);
    return value;
}

static bool read_range(const void *src, void *dest, size_t n, size_t limit) {
    if (src == NULL || dest == NULL || n == 0 || n > limit) {
        return false;
    }
    uintptr_t address = (uintptr_t)src;
    if (address > UINTPTR_MAX - (n - 1)) {
        return false;
    }
    mach_vm_size_t got = 0;
    kern_return_t result = mach_vm_read_overwrite(
        mach_task_self(), (mach_vm_address_t)address, (mach_vm_size_t)n,
        (mach_vm_address_t)(uintptr_t)dest, &got);
    return result == KERN_SUCCESS && got == n;
}

bool eu4_gfx_observe_read(const void *src, void *dest, size_t n) {
    return read_range(src, dest, n, EU4_GFX_OBSERVE_BLOB_SIZE);
}

bool eu4_gfx_observe_read_bytes(const void *src, void *dest, size_t n) {
    return read_range(src, dest, n, EU4_GFX_OBSERVE_READ_MAX);
}

void eu4_gfx_observe_set_output_base(eu4_gfx_observe_call *call, void *base) {
    if (call != NULL) {
        call->output_base = base;
    }
}

void eu4_gfx_observe_set_input(eu4_gfx_observe_call *call, const void *src,
                               size_t n) {
    if (call == NULL) {
        return;
    }
    if (n > EU4_GFX_OBSERVE_BLOB_SIZE ||
        !eu4_gfx_observe_read(src, call->input, n)) {
        call->input_ok = 0;
        call->input_size = 0;
        return;
    }
    call->input_size = n;
}

void eu4_gfx_observe_set_output(eu4_gfx_observe_call *call, const void *src,
                                size_t n) {
    if (call == NULL) {
        return;
    }
    if (atomic_exchange_explicit(&g_test_fail_output, 0,
                                 memory_order_relaxed)) {
        call->output_ok = 0;
        call->output_size = 0;
        return;
    }
    if (n > EU4_GFX_OBSERVE_BLOB_SIZE ||
        !eu4_gfx_observe_read(src, call->output, n)) {
        call->output_ok = 0;
        call->output_size = 0;
        return;
    }
    call->output_size = n;
}

void eu4_gfx_observe_set_return(eu4_gfx_observe_call *call, int64_t value) {
    if (call != NULL) {
        call->return_valid = 1;
        call->return_value = value;
    }
}

void eu4_gfx_observe_set_kind(eu4_gfx_observe_call *call, uint32_t kind) {
    if (call != NULL) {
        call->kind = kind;
    }
}

void eu4_gfx_observe_set_resource(eu4_gfx_observe_call *call, const char *id,
                                  int contents_known) {
    if (call == NULL) {
        return;
    }
    if (id != NULL) {
        (void)snprintf(call->resource_id, sizeof(call->resource_id), "%s", id);
    }
    call->contents_known = contents_known ? 1 : 0;
}

void eu4_gfx_observe_set_meta(eu4_gfx_observe_call *call, const char *json) {
    if (call != NULL && json != NULL) {
        (void)snprintf(call->meta, sizeof(call->meta), "%s", json);
    }
}

static bool read_payload_range(const void *src, void *dest, size_t n) {
    if (src == NULL || dest == NULL || n == 0) {
        return false;
    }
    uintptr_t address = (uintptr_t)src;
    if (address > UINTPTR_MAX - (n - 1)) {
        return false;
    }
    mach_vm_size_t got = 0;
    kern_return_t result = mach_vm_read_overwrite(
        mach_task_self(), (mach_vm_address_t)address, (mach_vm_size_t)n,
        (mach_vm_address_t)(uintptr_t)dest, &got);
    return result == KERN_SUCCESS && got == n;
}

void eu4_gfx_observe_set_payload(eu4_gfx_observe_call *call, const void *src,
                                 size_t n) {
    if (call == NULL) {
        return;
    }
    if (n == 0) {
        call->payload_size = 0;
        call->payload_ok = 1;
        return;
    }
    if (src == NULL || n > EU4_GFX_OBSERVE_PAYLOAD_MAX) {
        call->payload_ok = 0;
        call->fields_ok = 0;
        return;
    }
    uint32_t aligned = (uint32_t)((n + 15u) & ~(size_t)15u);
    uint32_t offset = atomic_fetch_add_explicit(&g_payload_used, aligned,
                                                memory_order_relaxed);
    if (offset > EU4_GFX_OBSERVE_PAYLOAD_CAP - aligned) {
        call->payload_ok = 0;
        call->fields_ok = 0;
        return;
    }
    if (!read_payload_range(src, g_payload + offset, n)) {
        call->payload_ok = 0;
        call->fields_ok = 0;
        return;
    }
    call->payload_offset = offset;
    call->payload_size = (uint32_t)n;
    call->payload_ok = 1;
}

void eu4_gfx_observe_fail_fields(eu4_gfx_observe_call *call) {
    if (call != NULL) {
        call->fields_ok = 0;
    }
}

void eu4_gfx_observe_note_payload_gap(void) {
    atomic_fetch_add_explicit(&g_payload_gaps, 1, memory_order_relaxed);
}

/* Optional resource payload. A gap is coverage, not record corruption: the
   observation stays structurally valid so the packet can report the missing
   bytes instead of rejecting the whole capture. */
bool eu4_gfx_observe_try_payload(eu4_gfx_observe_call *call, const void *src,
                                 size_t n) {
    if (call == NULL) {
        return false;
    }
    if (n == 0) {
        call->payload_size = 0;
        call->payload_ok = 1;
        return true;
    }
    if (src == NULL || n > EU4_GFX_OBSERVE_PAYLOAD_MAX) {
        call->payload_ok = 1;
        call->payload_size = 0;
        call->contents_known = 0;
        eu4_gfx_observe_note_payload_gap();
        return false;
    }
    uint32_t aligned = (uint32_t)((n + 15u) & ~(size_t)15u);
    uint32_t offset = atomic_fetch_add_explicit(&g_payload_used, aligned,
                                                memory_order_relaxed);
    if (offset > EU4_GFX_OBSERVE_PAYLOAD_CAP - aligned) {
        call->payload_ok = 1;
        call->payload_size = 0;
        call->contents_known = 0;
        eu4_gfx_observe_note_payload_gap();
        return false;
    }
    if (!read_payload_range(src, g_payload + offset, n)) {
        call->payload_ok = 1;
        call->payload_size = 0;
        call->contents_known = 0;
        eu4_gfx_observe_note_payload_gap();
        return false;
    }
    call->payload_offset = offset;
    call->payload_size = (uint32_t)n;
    call->payload_ok = 1;
    return true;
}

void eu4_gfx_observe_count_primitive(void) {
    atomic_fetch_add_explicit(&g_primitive_sets, 1, memory_order_relaxed);
}

static void note_caller(uint64_t rva) {
    if (rva == 0) {
        return;
    }
    for (uint32_t i = 0; i < EU4_GFX_OBSERVE_CALLER_SLOTS; i++) {
        uint64_t slot =
            atomic_load_explicit(&g_draw_caller_rva[i], memory_order_relaxed);
        if (slot == rva) {
            atomic_fetch_add_explicit(&g_draw_caller_count[i], 1,
                                      memory_order_relaxed);
            return;
        }
        if (slot == 0) {
            uint64_t expected = 0;
            if (atomic_compare_exchange_strong_explicit(
                    &g_draw_caller_rva[i], &expected, rva,
                    memory_order_relaxed, memory_order_relaxed)) {
                atomic_fetch_add_explicit(&g_draw_caller_count[i], 1,
                                          memory_order_relaxed);
                return;
            }
            if (expected == rva) {
                atomic_fetch_add_explicit(&g_draw_caller_count[i], 1,
                                          memory_order_relaxed);
                return;
            }
        }
    }
    atomic_fetch_add_explicit(&g_draw_caller_overflow, 1, memory_order_relaxed);
}

void eu4_gfx_observe_count_draw(uint64_t caller_rva, int caller_known,
                                int32_t mode, int32_t effective_count) {
    atomic_fetch_add_explicit(&g_draw_observed, 1, memory_order_relaxed);
    uint64_t target = atomic_load_explicit(&g_target_caller, memory_order_relaxed);
    if (caller_known && target != 0 && caller_rva == target) {
        atomic_fetch_add_explicit(&g_draw_at_caller, 1, memory_order_relaxed);
    }
    uint32_t mode_index =
        mode >= 0 && mode < (int32_t)EU4_GFX_OBSERVE_MODE_SLOTS
            ? (uint32_t)mode
            : (uint32_t)EU4_GFX_OBSERVE_MODE_SLOTS - 1;
    atomic_fetch_add_explicit(&g_draw_mode_counts[mode_index], 1,
                              memory_order_relaxed);
    uint32_t bucket = effective_count <= 0
                          ? 0
                          : effective_count == 1
                                ? 1
                                : effective_count == 2 ? 2 : 3;
    atomic_fetch_add_explicit(&g_draw_count_buckets[bucket], 1,
                              memory_order_relaxed);
    if (caller_known) {
        note_caller(caller_rva);
    }
}

static void refuse(uint32_t slot) {
    if (slot < EU4_GFX_OBSERVE_MAX_SPECS) {
        g_tls.refuse_depth[slot] += 1;
    }
    atomic_fetch_add_explicit(&g_refused, 1, memory_order_relaxed);
}

static void read_caller(eu4_gfx_capture_frame *frame,
                        eu4_gfx_observe_call *call) {
    uint64_t ret = 0;
    const uint8_t *above = (const uint8_t *)frame + sizeof(*frame);
    if (!eu4_gfx_observe_read(above, &ret, sizeof(ret))) {
        return;
    }
    if (ret >= g_text_begin && ret < g_text_end) {
        call->caller_known = 1;
        call->caller_rva = g_image_base != 0 ? ret - g_image_base : ret;
    }
}

static void on_entry(eu4_gfx_capture_frame *frame, uint64_t slot64) {
    uint32_t slot = (uint32_t)slot64;
    if (!atomic_load_explicit(&g_enabled, memory_order_relaxed) ||
        slot >= g_selected_count) {
        refuse(slot);
        return;
    }
    if (!atomic_load_explicit(&g_init_seen, memory_order_acquire)) {
        refuse(slot);
        return;
    }
    if (g_tls.depth >= EU4_GFX_OBSERVE_MAX_SPECS) {
        refuse(slot);
        return;
    }
    if (!eu4_gfx_startup_admit_hook()) {
        refuse(slot);
        return;
    }
    uint32_t retention =
        atomic_load_explicit(&g_retention, memory_order_relaxed);
    if (retention == EU4_GFX_OBSERVE_RETENTION_LEGACY) {
        uint32_t admitted =
            atomic_fetch_add_explicit(&g_admitted, 1, memory_order_relaxed) + 1;
        if (admitted > atomic_load_explicit(&g_max_events, memory_order_relaxed) ||
            admitted > atomic_load_explicit(&g_capacity, memory_order_relaxed)) {
            atomic_fetch_sub_explicit(&g_admitted, 1, memory_order_relaxed);
            eu4_gfx_startup_withdraw_hook();
            refuse(slot);
            return;
        }
    } else {
        atomic_fetch_add_explicit(&g_observed, 1, memory_order_relaxed);
    }
    uint32_t active =
        atomic_fetch_add_explicit(&g_active, 1, memory_order_relaxed) + 1;
    if (atomic_exchange_explicit(&g_test_overlap, 0, memory_order_relaxed)) {
        active = 2;
    }
    if (active > 1) {
        atomic_fetch_add_explicit(&g_overlaps, 1, memory_order_relaxed);
    }

    tls_frame *entry = &g_tls.frames[g_tls.depth++];
    memset(entry, 0, sizeof(*entry));
    entry->slot = slot;
    entry->admitted = 1;
    entry->call.call_id = eu4_gfx_startup_next_call_id();
    entry->call.thread_id = thread_id();
    entry->call.entry_time_ns = now_ns();
    entry->call.input_ok = 1;
    entry->call.output_ok = 1;
    entry->call.fields_ok = 1;
    entry->call.payload_ok = 1;
    entry->call.overlap = active > 1;
    if (atomic_exchange_explicit(&g_test_unpaired, 0, memory_order_relaxed)) {
        entry->abandoned = 1;
    }
    read_caller(frame, &entry->call);
    if (atomic_load_explicit(&g_first_event_ns, memory_order_relaxed) == 0) {
        atomic_store_explicit(&g_first_event_ns, entry->call.entry_time_ns,
                              memory_order_relaxed);
    }
    const eu4_gfx_observe_spec *spec = g_selected[slot];
    if (spec->on_entry != NULL) {
        spec->on_entry(frame, &entry->call);
    }
}

static void on_exit(eu4_gfx_capture_frame *frame, uint64_t slot64) {
    uint32_t slot = (uint32_t)slot64;
    if (slot < EU4_GFX_OBSERVE_MAX_SPECS && g_tls.refuse_depth[slot] > 0) {
        g_tls.refuse_depth[slot] -= 1;
        return;
    }
    if (g_tls.depth == 0) {
        atomic_fetch_add_explicit(&g_unmatched, 1, memory_order_relaxed);
        return;
    }
    tls_frame *entry = &g_tls.frames[g_tls.depth - 1];
    if (entry->slot != slot || !entry->admitted) {
        atomic_fetch_add_explicit(&g_unmatched, 1, memory_order_relaxed);
        return;
    }
    g_tls.depth -= 1;
    if (entry->abandoned) {
        atomic_fetch_sub_explicit(&g_active, 1, memory_order_relaxed);
        atomic_fetch_add_explicit(&g_unmatched, 1, memory_order_relaxed);
        eu4_gfx_startup_finish_hook();
        return;
    }

    eu4_gfx_observe_record record;
    memset(&record, 0, sizeof(record));
    const eu4_gfx_observe_spec *spec = g_selected[slot];
    if (spec->on_exit != NULL) {
        spec->on_exit(frame, &entry->call, &record);
    }
    record.slot = slot;
    record.call_id = entry->call.call_id;
    record.thread_id = entry->call.thread_id;
    record.caller_rva = entry->call.caller_rva;
    record.caller_known = entry->call.caller_known;
    record.overlap = entry->call.overlap;
    record.fields_ok = entry->call.fields_ok && entry->call.input_ok &&
                       entry->call.output_ok && entry->call.payload_ok;
    record.kind = entry->call.kind;
    record.contents_known = entry->call.contents_known;
    record.payload_offset = entry->call.payload_offset;
    record.payload_size = entry->call.payload_size;
    record.payload_ok = entry->call.payload_ok;
    (void)snprintf(record.resource_id, sizeof(record.resource_id), "%s",
                   entry->call.resource_id);
    (void)snprintf(record.meta, sizeof(record.meta), "%s", entry->call.meta);
    record.return_valid = entry->call.return_valid;
    record.return_value = entry->call.return_value;
    record.output_base_non_null = entry->call.output_base != NULL;
    record.input_size = entry->call.input_size;
    record.output_size = entry->call.output_size;
    if (record.input_size <= EU4_GFX_OBSERVE_BLOB_SIZE) {
        memcpy(record.input, entry->call.input, record.input_size);
    }
    if (record.output_size <= EU4_GFX_OBSERVE_BLOB_SIZE) {
        memcpy(record.output, entry->call.output, record.output_size);
    }
    if (!record.fields_ok) {
        atomic_fetch_add_explicit(&g_unavailable, 1, memory_order_relaxed);
    }

    uint32_t retention =
        atomic_load_explicit(&g_retention, memory_order_relaxed);
    int retain = retention == EU4_GFX_OBSERVE_RETENTION_LEGACY ||
                 entry->call.retain || entry->call.target_hit;
    uint32_t capacity = atomic_load_explicit(&g_capacity, memory_order_relaxed);
    int stored = 0;
    if (!retain) {
        atomic_store_explicit(&g_last_event_ns, now_ns(), memory_order_relaxed);
        atomic_fetch_sub_explicit(&g_active, 1, memory_order_relaxed);
        eu4_gfx_startup_finish_hook();
        return;
    }
    if (retention == EU4_GFX_OBSERVE_RETENTION_STATE &&
        entry->call.caller_known && entry->call.caller_rva ==
                                        atomic_load_explicit(&g_target_caller,
                                                             memory_order_relaxed)) {
        uint32_t matching =
            atomic_fetch_add_explicit(&g_matching_retained, 1,
                                      memory_order_relaxed) + 1;
        if (matching > EU4_GFX_OBSERVE_MATCHING_DRAW_CAP) {
            atomic_store_explicit(&g_retention_budget, 1, memory_order_relaxed);
        }
    }
    uint32_t index = atomic_fetch_add_explicit(&g_committed, 1,
                                               memory_order_relaxed);
    if (index < capacity) {
        record.completion_seq = (uint64_t)index + 1;
        g_records[index] = record;
        stored = 1;
    } else if (retention == EU4_GFX_OBSERVE_RETENTION_STATE) {
        atomic_store_explicit(&g_retention_budget, 1, memory_order_relaxed);
    } else {
        atomic_fetch_add_explicit(&g_overflow, 1, memory_order_relaxed);
    }
    atomic_store_explicit(&g_last_event_ns, now_ns(), memory_order_relaxed);
    atomic_fetch_sub_explicit(&g_active, 1, memory_order_relaxed);
    if (entry->call.target_hit) {
        if (stored) {
            atomic_store_explicit(&g_target_draw_call_id, entry->call.call_id,
                                  memory_order_relaxed);
            atomic_fetch_add_explicit(&g_draw_qualified, 1, memory_order_relaxed);
            atomic_store_explicit(&g_target_hit, 1, memory_order_release);
        } else {
            atomic_store_explicit(&g_retention_budget, 1, memory_order_relaxed);
        }
    }
    eu4_gfx_startup_finish_hook();
}

bool eu4_gfx_observe_configure_from_env(void) {
    const char *targets = getenv("EU4_GFX_OPS_TARGETS");
    if (targets == NULL || targets[0] == '\0') {
        atomic_store_explicit(&g_enabled, 0, memory_order_relaxed);
        return true;
    }
    char copy[512];
    if (snprintf(copy, sizeof(copy), "%s", targets) < 0) {
        return false;
    }
    size_t count = 0;
    char *save = NULL;
    for (char *token = strtok_r(copy, ",", &save); token != NULL;
         token = strtok_r(NULL, ",", &save)) {
        while (*token == ' ' || *token == '\t') {
            token++;
        }
        size_t end = strlen(token);
        while (end > 0 && (token[end - 1] == ' ' || token[end - 1] == '\t')) {
            token[--end] = '\0';
        }
        if (count >= EU4_GFX_OBSERVE_MAX_SPECS) {
            return false;
        }
        const eu4_gfx_observe_spec *found = NULL;
        for (size_t i = 0; i < sizeof(kSpecs) / sizeof(kSpecs[0]); i++) {
            if (strcmp(kSpecs[i].symbol, token) == 0) {
                found = &kSpecs[i];
                break;
            }
        }
        if (found == NULL) {
            return false;
        }
        g_selected[count++] = found;
    }
    if (count == 0) {
        atomic_store_explicit(&g_enabled, 0, memory_order_relaxed);
        return true;
    }
    uint32_t max_events = EU4_GFX_OBSERVE_DEFAULT_MAX_EVENTS;
    const char *max_text = getenv("EU4_GFX_OPS_MAX_EVENTS");
    if (max_text != NULL) {
        char *end = NULL;
        unsigned long value = strtoul(max_text, &end, 10);
        if (end == NULL || *end != '\0' || value == 0 ||
            value > EU4_GFX_OBSERVE_RECORD_CAP) {
            return false;
        }
        max_events = (uint32_t)value;
    }
    uint32_t duration_ms = EU4_GFX_OBSERVE_DEFAULT_DURATION_MS;
    const char *duration_text = getenv("EU4_GFX_OPS_DURATION_MS");
    if (duration_text != NULL) {
        char *end = NULL;
        unsigned long value = strtoul(duration_text, &end, 10);
        if (end == NULL || *end != '\0' || value > 600000ul) {
            return false;
        }
        duration_ms = (uint32_t)value;
    }
    uint32_t retention = EU4_GFX_OBSERVE_RETENTION_LEGACY;
    const char *retention_text = getenv("EU4_GFX_OPS_RETENTION");
    if (retention_text != NULL) {
        if (strcmp(retention_text, "state") == 0) {
            retention = EU4_GFX_OBSERVE_RETENTION_STATE;
        } else if (strcmp(retention_text, "legacy") != 0) {
            return false;
        }
    }
    uint32_t stop_policy = EU4_GFX_OBSERVE_STOP_QUOTA;
    const char *stop_text = getenv("EU4_GFX_OPS_STOP_POLICY");
    if (stop_text != NULL) {
        if (strcmp(stop_text, "target_draw") == 0) {
            stop_policy = EU4_GFX_OBSERVE_STOP_TARGET_DRAW;
        } else if (strcmp(stop_text, "quota") != 0) {
            return false;
        }
    }
    uint64_t target_caller = 0;
    const char *caller_text = getenv("EU4_GFX_OPS_TARGET_CALLER");
    if (caller_text != NULL) {
        char *end = NULL;
        unsigned long long value = strtoull(caller_text, &end, 0);
        if (end == NULL || *end != '\0' || value == 0 ||
            value > 0xffffffffull) {
            return false;
        }
        target_caller = (uint64_t)value;
    }
    if (stop_policy == EU4_GFX_OBSERVE_STOP_TARGET_DRAW && target_caller == 0) {
        return false;
    }
    g_selected_count = count;
    atomic_store_explicit(&g_max_events, max_events, memory_order_relaxed);
    atomic_store_explicit(&g_duration_ms, duration_ms, memory_order_relaxed);
    if (!eu4_gfx_observe_configure_policy(stop_policy, target_caller,
                                          retention)) {
        return false;
    }
    atomic_store_explicit(&g_enabled, 1, memory_order_relaxed);
    return true;
}

bool eu4_gfx_observe_configure_policy(uint32_t stop_policy, uint64_t target_caller,
                                      uint32_t retention) {
    if (stop_policy != EU4_GFX_OBSERVE_STOP_QUOTA &&
        stop_policy != EU4_GFX_OBSERVE_STOP_TARGET_DRAW) {
        return false;
    }
    if (retention != EU4_GFX_OBSERVE_RETENTION_LEGACY &&
        retention != EU4_GFX_OBSERVE_RETENTION_STATE) {
        return false;
    }
    if (stop_policy == EU4_GFX_OBSERVE_STOP_TARGET_DRAW) {
        if (target_caller == 0) {
            return false;
        }
        retention = EU4_GFX_OBSERVE_RETENTION_STATE;
    }
    atomic_store_explicit(&g_stop_policy, stop_policy, memory_order_relaxed);
    atomic_store_explicit(&g_target_caller, target_caller, memory_order_relaxed);
    atomic_store_explicit(&g_retention, retention, memory_order_relaxed);
    eu4_gfx_observe_draw_configure(target_caller);
    return true;
}

void eu4_gfx_observe_mark_target_draw(uint64_t call_id) {
    atomic_store_explicit(&g_target_draw_call_id, call_id, memory_order_relaxed);
    atomic_store_explicit(&g_target_hit, 1, memory_order_release);
}

bool eu4_gfx_observe_target_draw_hit(void) {
    return atomic_load_explicit(&g_target_hit, memory_order_acquire) != 0;
}

uint32_t eu4_gfx_observe_retention(void) {
    return atomic_load_explicit(&g_retention, memory_order_relaxed);
}

uint64_t eu4_gfx_observe_target_caller(void) {
    return atomic_load_explicit(&g_target_caller, memory_order_relaxed);
}

static bool validate_specs(const eu4_gfx_observe_spec *specs, size_t count) {
    if (specs == NULL || count == 0 || count > EU4_GFX_OBSERVE_MAX_SPECS) {
        return false;
    }
    for (size_t i = 0; i < count; i++) {
        const eu4_gfx_observe_spec *spec = &specs[i];
        if (spec->symbol == NULL || spec->prefix == NULL ||
            spec->length < 5 || spec->length > EU4_GFX_SITE_MAX_PATCH ||
            spec->relocation != EU4_GFX_OBSERVE_RELOC_SAFE) {
            return false;
        }
        uintptr_t site = g_image_base + (uintptr_t)spec->rva;
        if (site < g_text_begin ||
            site + (uintptr_t)spec->length > g_text_end) {
            return false;
        }
        if (memcmp((const void *)site, spec->prefix, spec->length) != 0) {
            return false;
        }
    }
    return true;
}

static void rollback_sites(size_t patched) {
    while (patched > 0) {
        patched -= 1;
        if (g_sites[patched].installed || g_sites[patched].written) {
            (void)eu4_gfx_site_restore(&g_sites[patched]);
        }
        eu4_gfx_site_release(&g_sites[patched]);
    }
}

static bool install_selected(uintptr_t image_base, uintptr_t text_begin,
                             uintptr_t text_end) {
    if (atomic_load_explicit(&g_installed, memory_order_relaxed)) {
        return false;
    }
    g_image_base = image_base;
    g_text_begin = text_begin;
    g_text_end = text_end;
    /* validate_specs needs a contiguous array; copy the pointers into a local
       spec array so the same validator applies to env and test selections. */
    eu4_gfx_observe_spec local[EU4_GFX_OBSERVE_MAX_SPECS];
    for (size_t i = 0; i < g_selected_count; i++) {
        local[i] = *g_selected[i];
    }
    if (!validate_specs(local, g_selected_count)) {
        return false;
    }
    memset(g_sites, 0, sizeof(g_sites));
    for (size_t i = 0; i < g_selected_count; i++) {
        int fail_after = atomic_load_explicit(&g_test_fail_install_after,
                                              memory_order_relaxed);
        if (fail_after >= 0 && (size_t)fail_after == i) {
            atomic_store_explicit(&g_test_fail_install_after, -1,
                                  memory_order_relaxed);
            rollback_sites(i);
            return false;
        }
        *g_entry_hooks[i] = on_entry;
        *g_exit_hooks[i] = on_exit;
        uintptr_t site = image_base + (uintptr_t)local[i].rva;
        if (!eu4_gfx_site_patch(&g_sites[i], (void *)site, local[i].prefix,
                                (int)local[i].length, g_gateways[i],
                                g_trampoline_slots[i])) {
            rollback_sites(i);
            return false;
        }
    }
    atomic_store_explicit(&g_installed, 1, memory_order_relaxed);
    return true;
}

bool eu4_gfx_observe_install(uintptr_t image_base, uintptr_t text_begin,
                             uintptr_t text_end) {
    if (!atomic_load_explicit(&g_enabled, memory_order_relaxed) ||
        g_selected_count == 0) {
        return false;
    }
    return install_selected(image_base, text_begin, text_end);
}

bool eu4_gfx_observe_install_specs(const eu4_gfx_observe_spec *specs,
                                   size_t count, uintptr_t image_base,
                                   uintptr_t text_begin, uintptr_t text_end) {
    if (specs == NULL || count == 0 || count > EU4_GFX_OBSERVE_MAX_SPECS) {
        return false;
    }
    if (atomic_load_explicit(&g_installed, memory_order_relaxed)) {
        return false;
    }
    if (atomic_load_explicit(&g_max_events, memory_order_relaxed) == 0) {
        atomic_store_explicit(&g_max_events, EU4_GFX_OBSERVE_DEFAULT_MAX_EVENTS,
                              memory_order_relaxed);
    }
    if (atomic_load_explicit(&g_duration_ms, memory_order_relaxed) == 0) {
        atomic_store_explicit(&g_duration_ms,
                              EU4_GFX_OBSERVE_DEFAULT_DURATION_MS,
                              memory_order_relaxed);
    }
    for (size_t i = 0; i < count; i++) {
        g_selected[i] = &specs[i];
    }
    g_selected_count = count;
    atomic_store_explicit(&g_enabled, 1, memory_order_relaxed);
    g_image_base = image_base;
    g_text_begin = text_begin;
    g_text_end = text_end;
    return install_selected(image_base, text_begin, text_end);
}

void eu4_gfx_observe_poll(void) {}

bool eu4_gfx_observe_should_stop(void) {
    if (!atomic_load_explicit(&g_enabled, memory_order_relaxed)) {
        return false;
    }
    if (atomic_load_explicit(&g_stop_policy, memory_order_relaxed) ==
        EU4_GFX_OBSERVE_STOP_TARGET_DRAW) {
        return atomic_load_explicit(&g_target_hit, memory_order_acquire) != 0 ||
               atomic_load_explicit(&g_retention_budget, memory_order_relaxed) !=
                   0;
    }
    if (atomic_load_explicit(&g_admitted, memory_order_relaxed) >=
        atomic_load_explicit(&g_max_events, memory_order_relaxed)) {
        return true;
    }
    uint64_t first = atomic_load_explicit(&g_first_event_ns, memory_order_relaxed);
    if (first == 0) {
        return false;
    }
    uint32_t duration = atomic_load_explicit(&g_duration_ms, memory_order_relaxed);
    if (duration == 0) {
        return false;
    }
    return now_ns() - first >= (uint64_t)duration * 1000000ull;
}

bool eu4_gfx_observe_enabled(void) {
    return atomic_load_explicit(&g_enabled, memory_order_relaxed) != 0;
}

void eu4_gfx_observe_mark_init(uint64_t call_id) {
    atomic_store_explicit(&g_init_call_id, call_id, memory_order_relaxed);
    atomic_store_explicit(&g_init_seen, 1, memory_order_release);
}

uint32_t eu4_gfx_observe_in_flight(void) {
    return atomic_load_explicit(&g_active, memory_order_relaxed);
}

static int evaluate_integrity(const eu4_gfx_observe_snapshot *out) {
    if (out->state != EU4_GFX_STARTUP_SEALED || out->in_flight != 0 ||
        out->unmatched != 0 || out->unavailable != 0 || out->overflow != 0) {
        return 0;
    }
    for (uint32_t i = 0; i < out->record_count; i++) {
        if (!out->records[i].fields_ok) {
            return 0;
        }
    }
    return 1;
}

void eu4_gfx_observe_copy_snapshot(eu4_gfx_observe_snapshot *out) {
    if (out == NULL) {
        return;
    }
    memset(out, 0, sizeof(*out));
    out->state = eu4_gfx_startup_state();
    out->enabled = atomic_load_explicit(&g_enabled, memory_order_relaxed);
    out->capacity = atomic_load_explicit(&g_capacity, memory_order_relaxed);
    out->max_events = atomic_load_explicit(&g_max_events, memory_order_relaxed);
    out->duration_ms = atomic_load_explicit(&g_duration_ms, memory_order_relaxed);
    out->overlaps = atomic_load_explicit(&g_overlaps, memory_order_relaxed);
    out->refused = atomic_load_explicit(&g_refused, memory_order_relaxed);
    out->unmatched = atomic_load_explicit(&g_unmatched, memory_order_relaxed);
    out->unavailable = atomic_load_explicit(&g_unavailable, memory_order_relaxed);
    out->overflow = atomic_load_explicit(&g_overflow, memory_order_relaxed);
    out->in_flight = atomic_load_explicit(&g_active, memory_order_relaxed);
    out->init_seen = atomic_load_explicit(&g_init_seen, memory_order_relaxed);
    out->init_call_id = atomic_load_explicit(&g_init_call_id, memory_order_relaxed);
    out->first_event_ns = atomic_load_explicit(&g_first_event_ns, memory_order_relaxed);
    out->last_event_ns = atomic_load_explicit(&g_last_event_ns, memory_order_relaxed);
    uint32_t committed = atomic_load_explicit(&g_committed, memory_order_relaxed);
    if (committed > out->capacity) {
        committed = out->capacity;
    }
    out->events = committed;
    out->record_count = committed;
    out->records = g_records;
    out->retention = atomic_load_explicit(&g_retention, memory_order_relaxed);
    out->observed = atomic_load_explicit(&g_observed, memory_order_relaxed);
    out->stop_policy = atomic_load_explicit(&g_stop_policy, memory_order_relaxed);
    out->target_draw_caller =
        atomic_load_explicit(&g_target_caller, memory_order_relaxed);
    out->target_draw_qualified =
        atomic_load_explicit(&g_target_hit, memory_order_acquire) != 0;
    out->target_draw_call_id =
        atomic_load_explicit(&g_target_draw_call_id, memory_order_relaxed);
    out->draw_observed = atomic_load_explicit(&g_draw_observed, memory_order_relaxed);
    out->draw_at_caller =
        atomic_load_explicit(&g_draw_at_caller, memory_order_relaxed);
    out->draw_qualified =
        atomic_load_explicit(&g_draw_qualified, memory_order_relaxed);
    for (uint32_t i = 0; i < EU4_GFX_OBSERVE_MODE_SLOTS; i++) {
        out->draw_mode_counts[i] =
            atomic_load_explicit(&g_draw_mode_counts[i], memory_order_relaxed);
    }
    for (uint32_t i = 0; i < 4; i++) {
        out->draw_count_buckets[i] =
            atomic_load_explicit(&g_draw_count_buckets[i], memory_order_relaxed);
    }
    out->draw_caller_overflow =
        atomic_load_explicit(&g_draw_caller_overflow, memory_order_relaxed);
    for (uint32_t i = 0; i < EU4_GFX_OBSERVE_CALLER_SLOTS; i++) {
        out->draw_callers[i].rva =
            atomic_load_explicit(&g_draw_caller_rva[i], memory_order_relaxed);
        out->draw_callers[i].count =
            atomic_load_explicit(&g_draw_caller_count[i], memory_order_relaxed);
    }
    out->primitive_sets =
        atomic_load_explicit(&g_primitive_sets, memory_order_relaxed);
    out->retention_budget_exhausted =
        atomic_load_explicit(&g_retention_budget, memory_order_relaxed) != 0;
    out->payload_gaps = atomic_load_explicit(&g_payload_gaps, memory_order_relaxed);
    out->integrity_ok = evaluate_integrity(out);
    out->records_copied = out->integrity_ok;
    if (out->state != EU4_GFX_STARTUP_SEALED) {
        snprintf(out->reason, sizeof(out->reason), "not_sealed");
    } else if (out->in_flight != 0) {
        snprintf(out->reason, sizeof(out->reason), "active_hooks");
    } else if (out->unmatched != 0) {
        snprintf(out->reason, sizeof(out->reason), "unmatched");
    } else if (out->unavailable != 0) {
        snprintf(out->reason, sizeof(out->reason), "unavailable");
    } else if (out->overflow != 0) {
        snprintf(out->reason, sizeof(out->reason), "overflow");
    } else if (out->retention_budget_exhausted) {
        snprintf(out->reason, sizeof(out->reason), "retention_budget");
    }
}

static const char *state_name(uint32_t state) {
    if (state == EU4_GFX_STARTUP_ARMED) {
        return "armed";
    }
    if (state == EU4_GFX_STARTUP_DRAINING) {
        return "draining";
    }
    if (state == EU4_GFX_STARTUP_SEALED) {
        return "sealed";
    }
    return "unknown";
}

static void emit_hex(FILE *file, const char *name, const uint8_t *bytes,
                     size_t size) {
    fprintf(file, ",\"%s\":\"", name);
    for (size_t i = 0; i < size; i++) {
        fprintf(file, "%02x", bytes[i]);
    }
    fputc('"', file);
}

static const char *kind_name(uint32_t kind) {
    switch (kind) {
    case EU4_GFX_OBSERVE_KIND_VERTEX_CREATE:
        return "vertex_buffer_create";
    case EU4_GFX_OBSERVE_KIND_VERTEX_UPDATE:
        return "vertex_buffer_update";
    case EU4_GFX_OBSERVE_KIND_VERTEX_BIND:
        return "vertex_buffer_bind";
    case EU4_GFX_OBSERVE_KIND_DRAW:
        return "draw";
    case EU4_GFX_OBSERVE_KIND_PRIMITIVE_SET:
        return "primitive_set";
    default:
        return "operation";
    }
}

typedef struct {
    const eu4_gfx_observe_snapshot *snap;
    const char *session;
    const char *executable_sha;
    int pid;
    uintptr_t image_base;
    int partial;
    const char *run_dir;
    const char *payload_prefix;
    int payload_failed;
} write_ctx;

static bool write_payload_file(const write_ctx *ctx,
                               const eu4_gfx_observe_record *record,
                               char *relative, size_t relative_size) {
    if (!record->payload_ok || record->payload_size == 0) {
        return true;
    }
    if (ctx->run_dir == NULL || ctx->payload_prefix == NULL ||
        record->payload_offset > EU4_GFX_OBSERVE_PAYLOAD_CAP ||
        record->payload_size >
            EU4_GFX_OBSERVE_PAYLOAD_CAP - record->payload_offset) {
        return false;
    }
    char directory[1024];
    char path[1024];
    if (snprintf(directory, sizeof(directory), "%s/%s", ctx->run_dir,
                 ctx->payload_prefix) >= (int)sizeof(directory) ||
        snprintf(relative, relative_size, "%s/%llu-%u.bin",
                 ctx->payload_prefix, (unsigned long long)record->call_id,
                 record->kind) >= (int)relative_size ||
        snprintf(path, sizeof(path), "%s/%s", ctx->run_dir, relative) >=
            (int)sizeof(path)) {
        return false;
    }
    if (mkdir(directory, 0755) != 0 && errno != EEXIST) {
        return false;
    }
    FILE *file = fopen(path, "wb");
    if (file == NULL) {
        return false;
    }
    bool ok = fwrite(g_payload + record->payload_offset, 1,
                     record->payload_size, file) == record->payload_size;
    if (ok && fflush(file) != 0) {
        ok = false;
    }
    if (ok && fsync(fileno(file)) != 0) {
        ok = false;
    }
    if (fclose(file) != 0) {
        ok = false;
    }
    return ok;
}

static void emit_record(FILE *file, const eu4_gfx_observe_record *record,
                        int partial, write_ctx *ctx) {
    const eu4_gfx_observe_spec *spec =
        record->slot < g_selected_count ? g_selected[record->slot] : NULL;
    const char *symbol =
        spec != NULL && spec->symbol != NULL ? spec->symbol : "";
    uint64_t rva = spec != NULL ? spec->rva : 0;
    fprintf(file,
            "{\"schema\":\"gfx-op/v0\",\"call_id\":%llu,\"completion_seq\":%llu,"
            "\"symbol\":\"%s\",\"rva\":\"0x%llx\",\"thread_id\":%llu",
            (unsigned long long)record->call_id,
            (unsigned long long)record->completion_seq, symbol,
            (unsigned long long)rva,
            (unsigned long long)record->thread_id);
    if (record->caller_known) {
        fprintf(file, ",\"caller_rva\":%llu",
                (unsigned long long)record->caller_rva);
    } else {
        fputs(",\"caller_rva\":null", file);
    }
    fprintf(file,
            ",\"caller_known\":%s,\"overlap\":%s,\"fields_ok\":%s,"
            "\"output_base_non_null\":%s,\"input_size\":%zu,\"output_size\":%zu",
            record->caller_known ? "true" : "false",
            record->overlap ? "true" : "false",
            record->fields_ok ? "true" : "false",
            record->output_base_non_null ? "true" : "false", record->input_size,
            record->output_size);
    emit_hex(file, "input_hex", record->input, record->input_size);
    emit_hex(file, "output_hex", record->output, record->output_size);
    if (record->return_valid) {
        fprintf(file, ",\"return\":%lld",
                (long long)record->return_value);
    } else {
        fputs(",\"return\":null", file);
    }
    fprintf(file, ",\"kind\":\"%s\"", kind_name(record->kind));
    if (record->resource_id[0] != 0) {
        fprintf(file, ",\"resource_id\":\"%s\"", record->resource_id);
    }
    fprintf(file, ",\"contents_known\":%s",
            record->contents_known ? "true" : "false");
    if (record->meta[0] != 0) {
        fputs(record->meta, file);
    }
    char relative[512];
    if (record->payload_ok && record->payload_size > 0) {
        if (write_payload_file(ctx, record, relative, sizeof(relative))) {
            fprintf(file, ",\"payload\":{\"size\":%u,\"file\":\"%s\"}",
                    record->payload_size, relative);
        } else {
            ctx->payload_failed = 1;
            fputs(",\"payload\":null", file);
        }
    }
    if (partial) {
        fputs(",\"partial_record\":true", file);
    }
    fputs("}\n", file);
}

static int write_records_body(FILE *file, void *opaque) {
    write_ctx *ctx = opaque;
    for (uint32_t i = 0; i < ctx->snap->record_count; i++) {
        emit_record(file, &ctx->snap->records[i], ctx->partial, ctx);
    }
    return 0;
}

static int write_summary_body(FILE *file, void *opaque) {
    write_ctx *ctx = opaque;
    const eu4_gfx_observe_snapshot *snap = ctx->snap;
    fprintf(file,
            "{\"schema\":\"gfx-op-summary/v0\",\"recorder_version\":\"1\","
            "\"session\":\"%s\",\"pid\":%d,\"executable_sha256\":\"%s\","
            "\"image_base\":\"0x%llx\",\"enabled\":%s,\"targets\":[",
            ctx->session != NULL ? ctx->session : "", ctx->pid,
            ctx->executable_sha != NULL ? ctx->executable_sha : "",
            (unsigned long long)ctx->image_base,
            snap->enabled ? "true" : "false");
    for (size_t i = 0; i < g_selected_count; i++) {
        const eu4_gfx_observe_spec *spec = g_selected[i];
        if (i != 0) {
            fputc(',', file);
        }
        fprintf(file,
                "{\"symbol\":\"%s\",\"rva\":\"0x%llx\",\"length\":%zu,"
                "\"relocation\":\"safe\"}",
                spec->symbol, (unsigned long long)spec->rva, spec->length);
    }
    fprintf(file,
            "],\"max_events\":%u,\"capacity\":%u,\"duration_ms\":%u,"
            "\"retention\":\"%s\",\"stop_policy\":\"%s\",\"observed\":%u,"
            "\"state\":\"%s\",\"events\":%u,\"integrity_ok\":%s,"
            "\"records_copied\":%s,\"overlaps\":%u,\"refused\":%u,"
            "\"unmatched\":%u,\"unavailable\":%u,\"overflow\":%u,"
            "\"in_flight\":%u,\"init_seen\":%s,\"init_call_id\":%llu,"
            "\"target_draw_caller\":%llu,\"target_draw_qualified\":%s,"
            "\"target_draw_call_id\":%llu,\"draw_observed\":%u,"
            "\"draw_at_caller\":%u,\"draw_qualified\":%u,"
            "\"retention_budget_exhausted\":%s,\"payload_gaps\":%u,"
            "\"primitive_sets\":%u,\"draw_caller_overflow\":%u,"
            "\"first_event_ms\":%llu,\"last_event_ms\":%llu,\"draw_modes\":[",
            snap->max_events, snap->capacity, snap->duration_ms,
            snap->retention == EU4_GFX_OBSERVE_RETENTION_STATE ? "state"
                                                               : "legacy",
            snap->stop_policy == EU4_GFX_OBSERVE_STOP_TARGET_DRAW
                ? "target_draw"
                : "quota",
            snap->observed, state_name(snap->state), snap->events,
            snap->integrity_ok ? "true" : "false",
            snap->records_copied ? "true" : "false", snap->overlaps,
            snap->refused, snap->unmatched, snap->unavailable, snap->overflow,
            snap->in_flight, snap->init_seen ? "true" : "false",
            (unsigned long long)snap->init_call_id,
            (unsigned long long)snap->target_draw_caller,
            snap->target_draw_qualified ? "true" : "false",
            (unsigned long long)snap->target_draw_call_id, snap->draw_observed,
            snap->draw_at_caller, snap->draw_qualified,
            snap->retention_budget_exhausted ? "true" : "false",
            snap->payload_gaps, snap->primitive_sets, snap->draw_caller_overflow,
            0ull,
            (unsigned long long)(snap->first_event_ns == 0 ||
                                         snap->last_event_ns < snap->first_event_ns
                                     ? 0
                                     : (snap->last_event_ns -
                                        snap->first_event_ns) /
                                           1000000ull));
    for (uint32_t i = 0; i < EU4_GFX_OBSERVE_MODE_SLOTS; i++) {
        fprintf(file, "%s%u", i == 0 ? "" : ",", snap->draw_mode_counts[i]);
    }
    fprintf(file,
            "],\"draw_counts\":{\"zero\":%u,\"one\":%u,\"two\":%u,"
            "\"at_least_three\":%u},\"draw_callers\":[",
            snap->draw_count_buckets[0], snap->draw_count_buckets[1],
            snap->draw_count_buckets[2], snap->draw_count_buckets[3]);
    int first_caller = 1;
    for (uint32_t i = 0; i < EU4_GFX_OBSERVE_CALLER_SLOTS; i++) {
        if (snap->draw_callers[i].rva == 0) {
            continue;
        }
        fprintf(file, "%s{\"rva\":%llu,\"count\":%u}",
                first_caller ? "" : ",",
                (unsigned long long)snap->draw_callers[i].rva,
                snap->draw_callers[i].count);
        first_caller = 0;
    }
    fprintf(file,
            "],\"coverage\":{\"events\":%u,\"observed\":%u,\"overlaps\":%u},"
            "\"reason\":\"%s\"}\n",
            snap->events, snap->observed, snap->overlaps, snap->reason);
    return 0;
}

typedef int (*write_body)(FILE *, void *);

static bool write_text_file(const char *path, write_body body, void *opaque,
                            int atomic_rename) {
    char partial[1024];
    if (atomic_rename) {
        if (snprintf(partial, sizeof(partial), "%s.partial", path) >=
            (int)sizeof(partial)) {
            return false;
        }
    } else {
        if (snprintf(partial, sizeof(partial), "%s", path) >=
            (int)sizeof(partial)) {
            return false;
        }
    }
    FILE *file = fopen(partial, "w");
    if (file == NULL) {
        return false;
    }
    bool ok = body(file, opaque) == 0;
    if (ok && fflush(file) != 0) {
        ok = false;
    }
    if (ok && fsync(fileno(file)) != 0) {
        ok = false;
    }
    if (fclose(file) != 0) {
        ok = false;
    }
    if (!ok) {
        (void)unlink(partial);
        return false;
    }
    if (atomic_rename && rename(partial, path) != 0) {
        (void)unlink(partial);
        return false;
    }
    return true;
}

bool eu4_gfx_observe_finalize(const char *run_dir, const char *session,
                              const char *executable_sha, int pid,
                              uintptr_t image_base) {
    if (!atomic_load_explicit(&g_enabled, memory_order_relaxed)) {
        return true;
    }
    int expected = 0;
    if (!atomic_compare_exchange_strong_explicit(
            &g_finalized, &expected, 1, memory_order_seq_cst,
            memory_order_seq_cst)) {
        while (atomic_load_explicit(&g_finalized, memory_order_acquire) != 2) {
            usleep(1000);
        }
        return atomic_load_explicit(&g_finalized_ok, memory_order_acquire) != 0;
    }
    eu4_gfx_observe_snapshot snap;
    eu4_gfx_observe_copy_snapshot(&snap);
    write_ctx ctx = {
        .snap = &snap,
        .session = session,
        .executable_sha = executable_sha,
        .pid = pid,
        .image_base = image_base,
        .partial = 0,
        .run_dir = run_dir,
        .payload_prefix = "payloads",
        .payload_failed = 0,
    };
    char summary_path[1024];
    char records_path[1024];
    char partial_path[1024];
    bool ok = true;
    if (snprintf(summary_path, sizeof(summary_path), "%s/operations-summary.json",
                 run_dir) >= (int)sizeof(summary_path) ||
        snprintf(records_path, sizeof(records_path), "%s/operations.jsonl",
                 run_dir) >= (int)sizeof(records_path) ||
        snprintf(partial_path, sizeof(partial_path), "%s/operations.partial.jsonl",
                 run_dir) >= (int)sizeof(partial_path)) {
        ok = false;
    }
    if (ok) {
        if (snap.records_copied) {
            ok = write_text_file(records_path, write_records_body, &ctx, 1);
        } else {
            /* Publish an empty completed stream and keep the mutable records
               in the explicitly partial sidecar for diagnosis. */
            eu4_gfx_observe_snapshot empty = snap;
            empty.record_count = 0;
            write_ctx empty_ctx = ctx;
            empty_ctx.snap = &empty;
            ok = write_text_file(records_path, write_records_body, &empty_ctx,
                                 1);
        }
    }
    if (ok && !snap.records_copied && snap.record_count > 0) {
        write_ctx partial_ctx = ctx;
        partial_ctx.partial = 1;
        partial_ctx.payload_prefix = "payloads.partial";
        partial_ctx.payload_failed = 0;
        (void)write_text_file(partial_path, write_records_body, &partial_ctx, 1);
    }
    eu4_gfx_observe_snapshot summary_snap = snap;
    write_ctx summary_ctx = ctx;
    if (ctx.payload_failed) {
        summary_snap.integrity_ok = 0;
        summary_snap.records_copied = 0;
        (void)snprintf(summary_snap.reason, sizeof(summary_snap.reason),
                       "payload_write");
        summary_ctx.snap = &summary_snap;
        ok = false;
    } else if (!ok) {
        summary_snap.integrity_ok = 0;
        summary_snap.records_copied = 0;
        (void)snprintf(summary_snap.reason, sizeof(summary_snap.reason),
                       "write_failed");
        summary_ctx.snap = &summary_snap;
    }
    if (write_text_file(summary_path, write_summary_body, &summary_ctx, 1) == false) {
        ok = false;
    }
    atomic_store_explicit(&g_finalized_ok, ok ? 1 : 0, memory_order_release);
    atomic_store_explicit(&g_finalized, 2, memory_order_release);
    return ok;
}

void eu4_gfx_observe_test_reset(void) {
    for (size_t i = 0; i < EU4_GFX_OBSERVE_MAX_SPECS; i++) {
        if (g_sites[i].installed || g_sites[i].written) {
            (void)eu4_gfx_site_restore(&g_sites[i]);
        }
        eu4_gfx_site_release(&g_sites[i]);
    }
    atomic_store(&g_enabled, 0);
    atomic_store(&g_installed, 0);
    atomic_store(&g_max_events, EU4_GFX_OBSERVE_DEFAULT_MAX_EVENTS);
    atomic_store(&g_duration_ms, EU4_GFX_OBSERVE_DEFAULT_DURATION_MS);
    atomic_store(&g_capacity, EU4_GFX_OBSERVE_RECORD_CAP);
    atomic_store(&g_admitted, 0);
    atomic_store(&g_committed, 0);
    atomic_store(&g_active, 0);
    atomic_store(&g_overlaps, 0);
    atomic_store(&g_refused, 0);
    atomic_store(&g_unmatched, 0);
    atomic_store(&g_unavailable, 0);
    atomic_store(&g_overflow, 0);
    atomic_store(&g_first_event_ns, 0);
    atomic_store(&g_last_event_ns, 0);
    atomic_store(&g_init_seen, 0);
    atomic_store(&g_init_call_id, 0);
    atomic_store(&g_test_unpaired, 0);
    atomic_store(&g_test_fail_output, 0);
    atomic_store(&g_test_overlap, 0);
    atomic_store(&g_payload_used, 0);
    atomic_store(&g_retention, EU4_GFX_OBSERVE_RETENTION_LEGACY);
    atomic_store(&g_stop_policy, EU4_GFX_OBSERVE_STOP_QUOTA);
    atomic_store(&g_target_caller, 0);
    atomic_store(&g_target_draw_call_id, 0);
    atomic_store(&g_target_hit, 0);
    atomic_store(&g_observed, 0);
    atomic_store(&g_draw_observed, 0);
    atomic_store(&g_draw_at_caller, 0);
    atomic_store(&g_draw_qualified, 0);
    for (uint32_t i = 0; i < EU4_GFX_OBSERVE_MODE_SLOTS; i++) {
        atomic_store(&g_draw_mode_counts[i], 0);
    }
    for (uint32_t i = 0; i < 4; i++) {
        atomic_store(&g_draw_count_buckets[i], 0);
    }
    for (uint32_t i = 0; i < EU4_GFX_OBSERVE_CALLER_SLOTS; i++) {
        atomic_store(&g_draw_caller_rva[i], 0);
        atomic_store(&g_draw_caller_count[i], 0);
    }
    atomic_store(&g_draw_caller_overflow, 0);
    atomic_store(&g_primitive_sets, 0);
    atomic_store(&g_matching_retained, 0);
    atomic_store(&g_retention_budget, 0);
    atomic_store(&g_payload_gaps, 0);
    eu4_gfx_observe_buffers_reset();
    eu4_gfx_observe_draw_reset();
    atomic_store(&g_test_fail_install_after, -1);
    atomic_store(&g_finalized, 0);
    atomic_store(&g_finalized_ok, 0);
    memset(g_records, 0, sizeof(g_records));
    memset(g_selected, 0, sizeof(g_selected));
    g_selected_count = 0;
    memset(&g_tls, 0, sizeof(g_tls));
    g_image_base = 0;
    g_text_begin = 0;
    g_text_end = 0;
}

void eu4_gfx_observe_test_force_unpaired(void) {
    atomic_store(&g_test_unpaired, 1);
}

void eu4_gfx_observe_test_fail_next_output_read(void) {
    atomic_store(&g_test_fail_output, 1);
}

void eu4_gfx_observe_test_force_install_failure(int after_sites) {
    atomic_store(&g_test_fail_install_after, after_sites);
}

void eu4_gfx_observe_test_set_init_seen(int on) {
    atomic_store(&g_init_seen, on ? 1 : 0);
}

void eu4_gfx_observe_test_reset_resources(void) {
    eu4_gfx_observe_buffers_reset();
    atomic_store(&g_payload_used, 0);
}

void eu4_gfx_observe_test_set_limits(uint32_t max_events,
                                     uint32_t duration_ms) {
    if (max_events == 0 || max_events > EU4_GFX_OBSERVE_RECORD_CAP) {
        max_events = EU4_GFX_OBSERVE_RECORD_CAP;
    }
    atomic_store(&g_max_events, max_events);
    atomic_store(&g_duration_ms, duration_ms);
}

void eu4_gfx_observe_test_force_overlap(void) {
    atomic_store(&g_test_overlap, 1);
}

uint32_t eu4_gfx_observe_test_events(void) {
    uint32_t committed = atomic_load_explicit(&g_committed, memory_order_relaxed);
    uint32_t capacity = atomic_load_explicit(&g_capacity, memory_order_relaxed);
    return committed > capacity ? capacity : committed;
}

void eu4_gfx_observe_test_set_policy(uint32_t stop_policy, uint64_t target_caller,
                                     uint32_t retention) {
    (void)eu4_gfx_observe_configure_policy(stop_policy, target_caller, retention);
}

uint32_t eu4_gfx_observe_test_observed(void) {
    return atomic_load_explicit(&g_observed, memory_order_relaxed);
}

bool eu4_gfx_observe_test_budget_exhausted(void) {
    return atomic_load_explicit(&g_retention_budget, memory_order_relaxed) != 0;
}
