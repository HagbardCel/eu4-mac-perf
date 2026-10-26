#include "eu4_gfx_live_capture.h"

#include "eu4_gfx_capture_detour.h"
#include "eu4_gfx_set_constant_buffer_capture.h"

#include <CommonCrypto/CommonDigest.h>
#include <errno.h>
#include <fcntl.h>
#include <mach/mach.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#define LIVE_PATH 1024u
#define LIVE_SESSION 128u
#define LIVE_CANON_MAX 1536u
#define LIVE_TICKET_FREE 0
#define LIVE_TICKET_RESERVED 1
#define LIVE_TICKET_READY 2
#define LIVE_TICKET_PROCESSING 3
#define LIVE_TICKET_QUARANTINE 4
#define LIVE_STATE_DISARMED 0
#define LIVE_STATE_ARMED 1
#define LIVE_STATE_CLOSING 2
#define LIVE_STATE_SEALED 3
#define LIVE_QUIESCE_NS 2000000000ull
#define LIVE_DRAIN_NS 5000000000ull

typedef struct {
    _Atomic uintptr_t key;
    _Atomic bool ready;
    eu4_gfx_capture_record record;
} live_thread;

typedef struct {
    _Atomic int state;
    eu4_gfx_capture_record record;
} live_ticket;

typedef struct {
    uint8_t hash[CC_SHA256_DIGEST_LENGTH];
    uint16_t length;
    uint8_t bytes[LIVE_CANON_MAX];
} retained_observation;

static live_thread threads[EU4_GFX_LIVE_CAPTURE_THREADS];
static live_ticket tickets[EU4_GFX_LIVE_CAPTURE_POOL];
static retained_observation *retained;
static unsigned max_unique;
static unsigned unique_retained;
static _Atomic unsigned next_thread_id;
static _Atomic unsigned active_entries;
static _Atomic int session_state;
static _Atomic bool stop_requested;
static _Atomic bool accepting;
static _Atomic unsigned not_admitted_busy;
static _Atomic unsigned not_admitted_slot;
static _Atomic unsigned not_admitted_pointer;
static _Atomic unsigned not_admitted_address;
static _Atomic unsigned not_admitted_closed;
static _Atomic unsigned not_admitted_thread_limit;
static _Atomic unsigned buffer_refused;
static _Atomic unsigned duplicates_discarded;
static _Atomic unsigned unique_limit_discarded;
static _Atomic unsigned storage_failed;
static _Atomic unsigned incomplete_observations;
static _Atomic unsigned undrained_observations;
static unsigned fail_writes_remaining;
static int force_identical_hash;
static int dump_canonical;
static _Atomic int pause_drain;
static pthread_t coordinator_thread;
static bool coordinator_started;
static bool started;
static bool restore_on_stop;
static void *control_map;
static size_t control_page;
static _Atomic uint64_t *control_word;
static uint64_t started_ns;
static uint64_t armed_ns;
static uint64_t closed_ns;
static unsigned deadline_seconds;
static bool quiescence_timeout;
static const char *closed_reason;
static char run_dir[LIVE_PATH];
static char captures_dir[LIVE_PATH];
static char summary_path[LIVE_PATH];
static char done_path[LIVE_PATH];
static char session_id[LIVE_SESSION];
static eu4_gfx_capture_metadata live_metadata;

static uint64_t now_ns(void) {
    struct timespec value;
    if (clock_gettime(CLOCK_MONOTONIC, &value) != 0) {
        return 0;
    }
    return (uint64_t)value.tv_sec * 1000000000ull + (uint64_t)value.tv_nsec;
}

static void sleep_ms(unsigned milliseconds) {
    struct timespec value = {
        .tv_sec = 0,
        .tv_nsec = (long)milliseconds * 1000000L,
    };
    (void)nanosleep(&value, NULL);
}

static bool slot_outstanding(int state) {
    return state == LIVE_TICKET_RESERVED || state == LIVE_TICKET_READY ||
           state == LIVE_TICKET_PROCESSING;
}

static bool slots_outstanding(void) {
    for (unsigned i = 0; i < EU4_GFX_LIVE_CAPTURE_POOL; i++) {
        int state = atomic_load_explicit(&tickets[i].state, memory_order_acquire);
        if (slot_outstanding(state)) {
            return true;
        }
    }
    return false;
}

void eu4_gfx_live_capture_test_force_identical_hash(int enabled) {
    force_identical_hash = enabled;
}

void eu4_gfx_live_capture_test_fail_next_writes(unsigned count) {
    fail_writes_remaining = count;
}

void eu4_gfx_live_capture_test_dump_canonical(int enabled) {
    dump_canonical = enabled;
}

void eu4_gfx_live_capture_test_pause_drain(int enabled) {
    atomic_store_explicit(&pause_drain, enabled, memory_order_release);
}

bool eu4_gfx_live_capture_wait_idle(unsigned timeout_ms) {
    for (unsigned elapsed = 0; elapsed <= timeout_ms; elapsed++) {
        if (!slots_outstanding()) {
            return true;
        }
        sleep_ms(1);
    }
    return !slots_outstanding();
}

static unsigned thread_count(void) {
    unsigned count = 0;
    for (unsigned i = 0; i < EU4_GFX_LIVE_CAPTURE_THREADS; i++) {
        if (atomic_load_explicit(&threads[i].key, memory_order_acquire) != 0) {
            count++;
        }
    }
    return count;
}

static bool valid_session_id(const char *value) {
    if (value == NULL || value[0] == '\0') {
        return false;
    }
    size_t length = strlen(value);
    if (length >= LIVE_SESSION) {
        return false;
    }
    for (size_t i = 0; i < length; i++) {
        char c = value[i];
        if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
              (c >= '0' && c <= '9') || c == '-' || c == '_' || c == '.')) {
            return false;
        }
    }
    return true;
}

static void count_thread_limit_refusal(void) {
    if (atomic_load_explicit(&active_entries, memory_order_acquire) != 0) {
        atomic_fetch_add_explicit(&not_admitted_thread_limit, 1,
                                  memory_order_relaxed);
    }
}

/* Registration is allowed only before arming (thread_init) or after a
 * successful epoch guard (entry). The ready flag is published before a
 * caller can observe a usable record; a claimed-but-not-ready match is a
 * stale slot and is refused, never waited on. */
static eu4_gfx_capture_record *live_thread_claim(void) {
    uintptr_t key = (uintptr_t)pthread_self();
    if (key == 0) {
        return NULL;
    }
    if (atomic_load_explicit(&session_state, memory_order_acquire) !=
            LIVE_STATE_DISARMED &&
        atomic_load_explicit(&active_entries, memory_order_acquire) == 0) {
        return NULL;
    }
    unsigned start = (unsigned)((key >> 4) % EU4_GFX_LIVE_CAPTURE_THREADS);
    for (unsigned step = 0; step < EU4_GFX_LIVE_CAPTURE_THREADS; step++) {
        unsigned index =
            (start + step) % EU4_GFX_LIVE_CAPTURE_THREADS;
        live_thread *slot = &threads[index];
        uintptr_t current =
            atomic_load_explicit(&slot->key, memory_order_acquire);
        if (current == key) {
            if (!atomic_load_explicit(&slot->ready, memory_order_acquire)) {
                count_thread_limit_refusal();
                return NULL;
            }
            return &slot->record;
        }
        if (current != 0) {
            continue;
        }
        uintptr_t expected = 0;
        if (atomic_compare_exchange_strong_explicit(
                &slot->key, &expected, key, memory_order_acq_rel,
                memory_order_acquire)) {
            memset(&slot->record, 0, sizeof(slot->record));
            slot->record.state = EU4_GFX_CAPTURE_SLOT_IDLE;
            slot->record.thread = atomic_fetch_add_explicit(
                &next_thread_id, 1, memory_order_relaxed);
            atomic_store_explicit(&slot->ready, true, memory_order_release);
            return &slot->record;
        }
        if (expected == key) {
            if (!atomic_load_explicit(&slot->ready, memory_order_acquire)) {
                count_thread_limit_refusal();
                return NULL;
            }
            return &slot->record;
        }
    }
    count_thread_limit_refusal();
    return NULL;
}

static eu4_gfx_capture_record *live_thread_lookup(void) {
    uintptr_t key = (uintptr_t)pthread_self();
    if (key == 0) {
        return NULL;
    }
    for (unsigned i = 0; i < EU4_GFX_LIVE_CAPTURE_THREADS; i++) {
        live_thread *slot = &threads[i];
        if (atomic_load_explicit(&slot->key, memory_order_acquire) == key) {
            if (!atomic_load_explicit(&slot->ready, memory_order_acquire)) {
                return NULL;
            }
            return &slot->record;
        }
    }
    return NULL;
}

static bool live_begin(void) {
    if (atomic_load_explicit(&session_state, memory_order_acquire) !=
        LIVE_STATE_ARMED) {
        return false;
    }
    atomic_fetch_add_explicit(&active_entries, 1, memory_order_acq_rel);
    if (atomic_load_explicit(&session_state, memory_order_acquire) !=
        LIVE_STATE_ARMED) {
        atomic_fetch_sub_explicit(&active_entries, 1, memory_order_acq_rel);
        return false;
    }
    return true;
}

static void live_end(void) {
    atomic_fetch_sub_explicit(&active_entries, 1, memory_order_acq_rel);
}

static int live_admit(const eu4_gfx_capture_frame *frame) {
    (void)frame;
    if (!atomic_load_explicit(&accepting, memory_order_acquire)) {
        atomic_fetch_add_explicit(&not_admitted_closed, 1, memory_order_relaxed);
        return -1;
    }
    for (unsigned ticket = 0; ticket < EU4_GFX_LIVE_CAPTURE_POOL; ticket++) {
        int expected = LIVE_TICKET_FREE;
        if (!atomic_compare_exchange_strong_explicit(
                &tickets[ticket].state, &expected, LIVE_TICKET_RESERVED,
                memory_order_acq_rel, memory_order_acquire)) {
            continue;
        }
        if (!atomic_load_explicit(&accepting, memory_order_acquire)) {
            atomic_store_explicit(&tickets[ticket].state, LIVE_TICKET_FREE,
                                  memory_order_release);
            atomic_fetch_add_explicit(&not_admitted_closed, 1,
                                      memory_order_relaxed);
            return -1;
        }
        return (int)ticket;
    }
    atomic_fetch_add_explicit(&buffer_refused, 1, memory_order_relaxed);
    return -1;
}

static bool live_publish(unsigned ticket,
                         const eu4_gfx_capture_record *record) {
    if (ticket >= EU4_GFX_LIVE_CAPTURE_POOL || record == NULL) {
        return false;
    }
    if (atomic_load_explicit(&tickets[ticket].state, memory_order_acquire) !=
        LIVE_TICKET_RESERVED) {
        return false;
    }
    memcpy(&tickets[ticket].record, record, sizeof(*record));
    int expected = LIVE_TICKET_RESERVED;
    return atomic_compare_exchange_strong_explicit(
        &tickets[ticket].state, &expected, LIVE_TICKET_READY,
        memory_order_acq_rel, memory_order_acquire);
}

static void live_incomplete(unsigned ticket) {
    if (ticket >= EU4_GFX_LIVE_CAPTURE_POOL) {
        return;
    }
    int expected = LIVE_TICKET_RESERVED;
    if (!atomic_compare_exchange_strong_explicit(
            &tickets[ticket].state, &expected, LIVE_TICKET_PROCESSING,
            memory_order_acq_rel, memory_order_acquire)) {
        return;
    }
    atomic_fetch_add_explicit(&incomplete_observations, 1, memory_order_relaxed);
    atomic_store_explicit(&tickets[ticket].state, LIVE_TICKET_FREE,
                          memory_order_release);
}

static void live_refuse(eu4_gfx_capture_refusal reason) {
    switch (reason) {
    case EU4_GFX_CAPTURE_REFUSE_BUSY:
        atomic_fetch_add_explicit(&not_admitted_busy, 1, memory_order_relaxed);
        break;
    case EU4_GFX_CAPTURE_REFUSE_SLOT:
        atomic_fetch_add_explicit(&not_admitted_slot, 1, memory_order_relaxed);
        break;
    case EU4_GFX_CAPTURE_REFUSE_POINTER:
        atomic_fetch_add_explicit(&not_admitted_pointer, 1,
                                  memory_order_relaxed);
        break;
    case EU4_GFX_CAPTURE_REFUSE_ADDRESS:
        atomic_fetch_add_explicit(&not_admitted_address, 1,
                                  memory_order_relaxed);
        break;
    case EU4_GFX_CAPTURE_REFUSE_QUOTA:
        atomic_fetch_add_explicit(&buffer_refused, 1, memory_order_relaxed);
        break;
    case EU4_GFX_CAPTURE_REFUSE_THREAD_LIMIT:
        atomic_fetch_add_explicit(&not_admitted_thread_limit, 1,
                                  memory_order_relaxed);
        break;
    }
}

static const eu4_gfx_capture_live_ops live_ops = {
    .thread_claim = live_thread_claim,
    .thread_lookup = live_thread_lookup,
    .begin = live_begin,
    .end = live_end,
    .admit = live_admit,
    .publish = live_publish,
    .incomplete = live_incomplete,
    .refuse = live_refuse,
    .safe_read = true,
};

static bool put_bytes(uint8_t *out, size_t cap, size_t *used,
                      const void *src, size_t length) {
    if (*used > cap || length > cap - *used) {
        return false;
    }
    memcpy(out + *used, src, length);
    *used += length;
    return true;
}

static bool put_u8(uint8_t *out, size_t cap, size_t *used, uint8_t value) {
    return put_bytes(out, cap, used, &value, 1);
}

static bool put_u16(uint8_t *out, size_t cap, size_t *used, uint16_t value) {
    uint8_t bytes[2] = {(uint8_t)value, (uint8_t)(value >> 8)};
    return put_bytes(out, cap, used, bytes, 2);
}

static bool put_u64(uint8_t *out, size_t cap, size_t *used, uint64_t value) {
    uint8_t bytes[8];
    for (int shift = 0; shift < 8; shift++) {
        bytes[shift] = (uint8_t)(value >> (8 * shift));
    }
    return put_bytes(out, cap, used, bytes, 8);
}

static bool put_region(uint8_t *out, size_t cap, size_t *used, int present,
                       uint64_t address, const uint8_t *pre,
                       const uint8_t *post, uint16_t length) {
    if (!put_u8(out, cap, used, present ? 1u : 0u)) {
        return false;
    }
    if (!present) {
        return true;
    }
    return put_u64(out, cap, used, address) &&
           put_u16(out, cap, used, length) &&
           put_bytes(out, cap, used, pre, length) &&
           put_bytes(out, cap, used, post, length);
}

/* eu4-exact-dedup-canon/v1. Field order is the contract, not struct layout. */
static bool canonical_bytes(const eu4_gfx_capture_record *record,
                            uint8_t *out, size_t cap, size_t *length) {
    const char *function_id = eu4_gfx_set_constant_buffer_function_id();
    size_t name_length = strlen(function_id);
    size_t used = 0;
    if (name_length > 65535u ||
        !put_bytes(out, cap, &used, "EXDEDUP1", 8) ||
        !put_u16(out, cap, &used, (uint16_t)name_length) ||
        !put_bytes(out, cap, &used, function_id, name_length) ||
        !put_u64(out, cap, &used, record->rdi) ||
        !put_u64(out, cap, &used, record->rsi) ||
        !put_u64(out, cap, &used, record->rcx) ||
        !put_u64(out, cap, &used, record->r9) ||
        !put_u8(out, cap, &used, record->buffer_path ? 1u : 0u) ||
        !put_region(out, cap, &used, 1, (uint64_t)record->handle_address,
                    record->handle, record->post_handle,
                    EU4_GFX_CAPTURE_HANDLE_LENGTH) ||
        !put_region(out, cap, &used, 1,
                    (uint64_t)record->ctx_address +
                        EU4_GFX_CAPTURE_OBSERVATION_START,
                    record->ctx, record->post_ctx,
                    EU4_GFX_CAPTURE_OBSERVATION_LENGTH) ||
        !put_region(out, cap, &used, 1,
                    (uint64_t)record->ctx_address +
                        EU4_GFX_CAPTURE_CANARY_BEFORE_START,
                    record->canary_before, record->post_canary_before,
                    EU4_GFX_CAPTURE_CANARY_LENGTH) ||
        !put_region(out, cap, &used, 1,
                    (uint64_t)record->ctx_address +
                        EU4_GFX_CAPTURE_CANARY_AFTER_START,
                    record->canary_after, record->post_canary_after,
                    EU4_GFX_CAPTURE_CANARY_LENGTH) ||
        !put_region(out, cap, &used, record->buffer_path,
                    (uint64_t)record->buffer_address, record->buffer,
                    record->post_buffer, EU4_GFX_CAPTURE_BUFFER_LENGTH) ||
        !put_u64(out, cap, &used, record->outcome)) {
        return false;
    }
    *length = used;
    return true;
}

static void fingerprint(const uint8_t *bytes, size_t length, uint8_t out[32]) {
    if (force_identical_hash) {
        memset(out, 0, 32);
        return;
    }
    CC_SHA256(bytes, (CC_LONG)length, out);
}

static bool retained_match(const uint8_t hash[32], const uint8_t *bytes,
                           size_t length) {
    for (unsigned i = 0; i < unique_retained; i++) {
        if (memcmp(retained[i].hash, hash, 32) != 0) {
            continue;
        }
        if (retained[i].length == length &&
            memcmp(retained[i].bytes, bytes, length) == 0) {
            return true;
        }
    }
    return false;
}

static void discard_partial(const char *path) {
    if (path != NULL && path[0] != '\0') {
        (void)unlink(path);
    }
}

static bool write_transcript(const eu4_gfx_capture_record *record,
                             unsigned index) {
    char final_path[LIVE_PATH];
    char partial_path[LIVE_PATH];
    int final_length = snprintf(final_path, sizeof(final_path),
                                "%s/%s.%u.capture", captures_dir, session_id,
                                index);
    int partial_length = snprintf(partial_path, sizeof(partial_path),
                                  "%s/%s.%u.capture.partial", captures_dir,
                                  session_id, index);
    if (final_length < 0 || (size_t)final_length >= sizeof(final_path) ||
        partial_length < 0 ||
        (size_t)partial_length >= sizeof(partial_path)) {
        return false;
    }

    int descriptor = open(partial_path, O_WRONLY | O_CREAT | O_TRUNC, 0600);
    if (descriptor < 0) {
        return false;
    }
    FILE *file = fdopen(descriptor, "w");
    if (file == NULL) {
        (void)close(descriptor);
        discard_partial(partial_path);
        return false;
    }
    bool injected_failure = false;
    if (fail_writes_remaining > 0) {
        fail_writes_remaining--;
        injected_failure = true;
    }
    bool ok = !injected_failure &&
              eu4_gfx_set_constant_buffer_capture_write(
                  file, record, &live_metadata);
    if (ok && fflush(file) != 0) {
        ok = false;
    }
    if (ok && fsync(fileno(file)) != 0) {
        ok = false;
    }
    if (fclose(file) != 0) {
        ok = false;
    }
    if (!ok || rename(partial_path, final_path) != 0) {
        discard_partial(partial_path);
        discard_partial(final_path);
        return false;
    }
    return true;
}

static bool write_canonical_sidecar(unsigned index, const uint8_t *bytes,
                                    size_t length) {
    char path[LIVE_PATH];
    int written = snprintf(path, sizeof(path), "%s/%s.%u.canon", captures_dir,
                           session_id, index);
    if (written < 0 || (size_t)written >= sizeof(path)) {
        return false;
    }
    int descriptor = open(path, O_WRONLY | O_CREAT | O_TRUNC, 0600);
    if (descriptor < 0) {
        return false;
    }
    size_t offset = 0;
    while (offset < length) {
        ssize_t chunk = write(descriptor, bytes + offset, length - offset);
        if (chunk < 0) {
            (void)close(descriptor);
            (void)unlink(path);
            return false;
        }
        offset += (size_t)chunk;
    }
    if (fsync(descriptor) != 0 || close(descriptor) != 0) {
        (void)unlink(path);
        return false;
    }
    return true;
}

static void classify(const eu4_gfx_capture_record *record) {
    uint8_t canon[LIVE_CANON_MAX];
    size_t length = 0;
    uint8_t hash[CC_SHA256_DIGEST_LENGTH];
    if (retained == NULL ||
        !canonical_bytes(record, canon, sizeof(canon), &length)) {
        atomic_fetch_add_explicit(&storage_failed, 1, memory_order_relaxed);
        return;
    }
    fingerprint(canon, length, hash);
    if (retained_match(hash, canon, length)) {
        atomic_fetch_add_explicit(&duplicates_discarded, 1,
                                  memory_order_relaxed);
        return;
    }
    if (unique_retained >= max_unique) {
        atomic_fetch_add_explicit(&unique_limit_discarded, 1,
                                  memory_order_relaxed);
        atomic_store_explicit(&accepting, false, memory_order_release);
        return;
    }
    if (!write_transcript(record, unique_retained)) {
        atomic_fetch_add_explicit(&storage_failed, 1, memory_order_relaxed);
        return;
    }
    if (dump_canonical &&
        !write_canonical_sidecar(unique_retained, canon, length)) {
        char published[LIVE_PATH];
        int written = snprintf(published, sizeof(published),
                               "%s/%s.%u.capture", captures_dir, session_id,
                               unique_retained);
        if (written > 0 && (size_t)written < sizeof(published)) {
            (void)unlink(published);
        }
        atomic_fetch_add_explicit(&storage_failed, 1, memory_order_relaxed);
        return;
    }
    memcpy(retained[unique_retained].hash, hash, sizeof(hash));
    retained[unique_retained].length = (uint16_t)length;
    memcpy(retained[unique_retained].bytes, canon, length);
    unique_retained++;
    if (unique_retained >= max_unique) {
        atomic_store_explicit(&accepting, false, memory_order_release);
    }
}

static bool drain_one(uint64_t deadline) {
    if (atomic_load_explicit(&pause_drain, memory_order_acquire)) {
        return false;
    }
    if (deadline != 0 && now_ns() >= deadline) {
        return false;
    }
    for (unsigned i = 0; i < EU4_GFX_LIVE_CAPTURE_POOL; i++) {
        int expected = LIVE_TICKET_READY;
        if (!atomic_compare_exchange_strong_explicit(
                &tickets[i].state, &expected, LIVE_TICKET_PROCESSING,
                memory_order_acq_rel, memory_order_acquire)) {
            continue;
        }
        eu4_gfx_capture_record record = tickets[i].record;
        classify(&record);
        atomic_store_explicit(&tickets[i].state, LIVE_TICKET_FREE,
                              memory_order_release);
        return true;
    }
    return false;
}

static void quarantine(int from_state) {
    for (unsigned i = 0; i < EU4_GFX_LIVE_CAPTURE_POOL; i++) {
        int expected = from_state;
        if (atomic_compare_exchange_strong_explicit(
                &tickets[i].state, &expected, LIVE_TICKET_QUARANTINE,
                memory_order_acq_rel, memory_order_acquire)) {
            atomic_fetch_add_explicit(&undrained_observations, 1,
                                      memory_order_relaxed);
        }
    }
}

static bool wait_active(uint64_t deadline) {
    while (atomic_load_explicit(&active_entries, memory_order_acquire) != 0) {
        if (now_ns() >= deadline) {
            return false;
        }
        sleep_ms(1);
    }
    return true;
}

static bool wait_reserved(uint64_t deadline) {
    for (;;) {
        bool reserved = false;
        for (unsigned i = 0; i < EU4_GFX_LIVE_CAPTURE_POOL; i++) {
            if (atomic_load_explicit(&tickets[i].state,
                                     memory_order_acquire) ==
                LIVE_TICKET_RESERVED) {
                reserved = true;
                break;
            }
        }
        if (!reserved) {
            return true;
        }
        if (now_ns() >= deadline) {
            return false;
        }
        sleep_ms(1);
    }
}

static bool write_summary(void) {
    char partial_path[LIVE_PATH];
    int length = snprintf(partial_path, sizeof(partial_path), "%s.partial",
                          summary_path);
    if (length < 0 || (size_t)length >= sizeof(partial_path)) {
        return false;
    }

    unsigned busy =
        atomic_load_explicit(&not_admitted_busy, memory_order_relaxed);
    unsigned slot =
        atomic_load_explicit(&not_admitted_slot, memory_order_relaxed);
    unsigned pointer =
        atomic_load_explicit(&not_admitted_pointer, memory_order_relaxed);
    unsigned address =
        atomic_load_explicit(&not_admitted_address, memory_order_relaxed);
    unsigned closed =
        atomic_load_explicit(&not_admitted_closed, memory_order_relaxed);
    unsigned thread_limit =
        atomic_load_explicit(&not_admitted_thread_limit, memory_order_relaxed);
    unsigned refused_buffer =
        atomic_load_explicit(&buffer_refused, memory_order_relaxed);
    unsigned duplicates =
        atomic_load_explicit(&duplicates_discarded, memory_order_relaxed);
    unsigned past_limit =
        atomic_load_explicit(&unique_limit_discarded, memory_order_relaxed);
    unsigned failed =
        atomic_load_explicit(&storage_failed, memory_order_relaxed);
    unsigned incomplete =
        atomic_load_explicit(&incomplete_observations, memory_order_relaxed);
    unsigned undrained =
        atomic_load_explicit(&undrained_observations, memory_order_relaxed);
    unsigned not_admitted =
        busy + slot + pointer + address + closed + thread_limit;
    unsigned complete = unique_retained + duplicates + past_limit + failed;
    unsigned intercepted =
        not_admitted + refused_buffer + incomplete + complete + undrained;
    int clean = !quiescence_timeout && undrained == 0;

    int descriptor = open(partial_path, O_WRONLY | O_CREAT | O_TRUNC, 0600);
    if (descriptor < 0) {
        return false;
    }
    FILE *file = fdopen(descriptor, "w");
    if (file == NULL) {
        (void)close(descriptor);
        return false;
    }
    fprintf(file, "EU4-GFX-LIVE-SUMMARY exact-dedup/v1\n");
    fprintf(file, "session=%s\n", session_id);
    fprintf(file, "pid=%d\n", (int)getpid());
    fprintf(file, "state=%s\n", clean ? "sealed" : "partial");
    fprintf(file, "closed_reason=%s\n",
            closed_reason != NULL ? closed_reason : "unknown");
    fprintf(file, "quiescence=%s\n", quiescence_timeout ? "timeout" : "ok");
    fprintf(file, "started_ns=%llu\n", (unsigned long long)started_ns);
    fprintf(file, "armed_ns=%llu\n", (unsigned long long)armed_ns);
    fprintf(file, "closed_ns=%llu\n", (unsigned long long)closed_ns);
    fprintf(file, "max_unique=%u\n", max_unique);
    fprintf(file, "threads=%u\n", thread_count());
    fprintf(file, "intercepted=%u\n", intercepted);
    fprintf(file, "not_admitted=%u\n", not_admitted);
    fprintf(file, "not_admitted_busy=%u\n", busy);
    fprintf(file, "not_admitted_slot=%u\n", slot);
    fprintf(file, "not_admitted_pointer=%u\n", pointer);
    fprintf(file, "not_admitted_address=%u\n", address);
    fprintf(file, "not_admitted_closed=%u\n", closed);
    fprintf(file, "not_admitted_thread_limit=%u\n", thread_limit);
    fprintf(file, "buffer_refused=%u\n", refused_buffer);
    fprintf(file, "complete_observations=%u\n", complete);
    fprintf(file, "unique_retained=%u\n", unique_retained);
    fprintf(file, "duplicates_discarded=%u\n", duplicates);
    fprintf(file, "unique_limit_discarded=%u\n", past_limit);
    fprintf(file, "storage_failed=%u\n", failed);
    fprintf(file, "incomplete_observations=%u\n", incomplete);
    fprintf(file, "undrained_observations=%u\n", undrained);
    fprintf(file, "END\n");

    bool ok = ferror(file) == 0;
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
        return false;
    }
    return rename(partial_path, summary_path) == 0;
}

static bool write_done(const char *state) {
    char partial_path[LIVE_PATH];
    int length = snprintf(partial_path, sizeof(partial_path), "%s.partial",
                          done_path);
    if (length < 0 || (size_t)length >= sizeof(partial_path)) {
        return false;
    }
    int descriptor = open(partial_path, O_WRONLY | O_CREAT | O_TRUNC, 0600);
    if (descriptor < 0) {
        return false;
    }
    FILE *file = fdopen(descriptor, "w");
    if (file == NULL) {
        (void)close(descriptor);
        return false;
    }
    fprintf(file, "EU4-GFX-LIVE-DONE v1\nsession=%s\nstate=%s\nEND\n",
            session_id, state);
    bool ok = ferror(file) == 0;
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
        return false;
    }
    return rename(partial_path, done_path) == 0;
}

static void close_session(const char *reason) {
    closed_reason = reason;
    atomic_store_explicit(&accepting, false, memory_order_release);
    atomic_store_explicit(&session_state, LIVE_STATE_CLOSING,
                          memory_order_release);
    uint64_t quiesce_deadline = now_ns() + LIVE_QUIESCE_NS;
    bool active_ok = wait_active(quiesce_deadline);
    bool reserved_ok = wait_reserved(quiesce_deadline);
    if (!active_ok || !reserved_ok) {
        quiescence_timeout = true;
    }

    quarantine(LIVE_TICKET_RESERVED);
    uint64_t drain_deadline = now_ns() + LIVE_DRAIN_NS;
    while (drain_one(drain_deadline)) {
    }
    quarantine(LIVE_TICKET_READY);
    quarantine(LIVE_TICKET_PROCESSING);
    closed_ns = now_ns();

    unsigned undrained =
        atomic_load_explicit(&undrained_observations, memory_order_relaxed);
    bool clean = !quiescence_timeout && undrained == 0;
    bool summary_ok = write_summary();
    (void)write_done(summary_ok ? (clean ? "sealed" : "partial") : "error");
    atomic_store_explicit(&session_state, LIVE_STATE_SEALED,
                          memory_order_release);
}

static void *coordinator_main(void *argument) {
    (void)argument;
    uint64_t arm_time = 0;
    const char *reason = "destructor";
    for (;;) {
        uint64_t mode = atomic_load_explicit(control_word, memory_order_acquire);
        int state = atomic_load_explicit(&session_state, memory_order_acquire);
        if (mode > 2) {
            reason = "control";
            break;
        }
        if (state == LIVE_STATE_DISARMED && mode == 1) {
            arm_time = now_ns();
            armed_ns = arm_time;
            atomic_store_explicit(&accepting, true, memory_order_release);
            atomic_store_explicit(&session_state, LIVE_STATE_ARMED,
                                  memory_order_release);
            state = LIVE_STATE_ARMED;
        }
        if (state == LIVE_STATE_ARMED) {
            while (drain_one(0)) {
            }
            if (mode == 2) {
                reason = "operator";
                break;
            }
            if (atomic_load_explicit(&stop_requested, memory_order_acquire)) {
                reason = "destructor";
                break;
            }
            if (unique_retained >= max_unique && !slots_outstanding()) {
                reason = "unique_limit";
                break;
            }
            if (arm_time != 0 &&
                now_ns() >= arm_time +
                                (uint64_t)deadline_seconds * 1000000000ull) {
                reason = "deadline";
                break;
            }
        } else if (state == LIVE_STATE_DISARMED) {
            if (atomic_load_explicit(&stop_requested, memory_order_acquire)) {
                reason = "destructor";
                break;
            }
            if (mode == 2) {
                reason = "operator";
                break;
            }
        }
        sleep_ms(10);
    }
    close_session(reason);
    return NULL;
}

bool eu4_gfx_live_capture_start(void *site, const char *run_directory,
                                const char *control_path,
                                const char *capture_session_id,
                                unsigned seconds, unsigned unique_limit,
                                bool restore) {
    if (started || site == NULL || run_directory == NULL ||
        control_path == NULL || !valid_session_id(capture_session_id) ||
        seconds == 0 || unique_limit == 0 ||
        unique_limit > EU4_GFX_LIVE_CAPTURE_MAX_UNIQUE) {
        return false;
    }
    retained_observation *table =
        calloc(unique_limit, sizeof(*table));
    if (table == NULL) {
        return false;
    }
    memset(threads, 0, sizeof(threads));
    memset(tickets, 0, sizeof(tickets));
    retained = table;
    max_unique = unique_limit;
    unique_retained = 0;
    atomic_store(&next_thread_id, 0);
    atomic_store(&active_entries, 0);
    atomic_store(&session_state, LIVE_STATE_DISARMED);
    atomic_store(&stop_requested, false);
    atomic_store(&accepting, false);
    atomic_store(&not_admitted_busy, 0);
    atomic_store(&not_admitted_slot, 0);
    atomic_store(&not_admitted_pointer, 0);
    atomic_store(&not_admitted_address, 0);
    atomic_store(&not_admitted_closed, 0);
    atomic_store(&not_admitted_thread_limit, 0);
    atomic_store(&buffer_refused, 0);
    atomic_store(&duplicates_discarded, 0);
    atomic_store(&unique_limit_discarded, 0);
    atomic_store(&storage_failed, 0);
    atomic_store(&incomplete_observations, 0);
    atomic_store(&undrained_observations, 0);
    quiescence_timeout = false;
    closed_reason = NULL;
    armed_ns = 0;
    closed_ns = 0;
    deadline_seconds = seconds;
    restore_on_stop = restore;

    int run_length = snprintf(run_dir, sizeof(run_dir), "%s", run_directory);
    int captures_length = snprintf(captures_dir, sizeof(captures_dir),
                                   "%s/captures", run_directory);
    int summary_length = snprintf(summary_path, sizeof(summary_path),
                                  "%s/summary.native", run_directory);
    int done_length = snprintf(done_path, sizeof(done_path), "%s/session.done",
                               run_directory);
    int session_length = snprintf(session_id, sizeof(session_id), "%s",
                                  capture_session_id);
    if (run_length < 0 || (size_t)run_length >= sizeof(run_dir) ||
        captures_length < 0 ||
        (size_t)captures_length >= sizeof(captures_dir) ||
        summary_length < 0 ||
        (size_t)summary_length >= sizeof(summary_path) || done_length < 0 ||
        (size_t)done_length >= sizeof(done_path) || session_length < 0 ||
        (size_t)session_length >= sizeof(session_id)) {
        free(retained);
        retained = NULL;
        return false;
    }
    if (mkdir(captures_dir, 0700) != 0 && errno != EEXIST) {
        free(retained);
        retained = NULL;
        return false;
    }
    struct stat directory_status;
    if (stat(captures_dir, &directory_status) != 0 ||
        !S_ISDIR(directory_status.st_mode)) {
        free(retained);
        retained = NULL;
        return false;
    }

    int descriptor = open(control_path, O_RDONLY);
    if (descriptor < 0) {
        free(retained);
        retained = NULL;
        return false;
    }
    struct stat control_status;
    if (fstat(descriptor, &control_status) != 0 ||
        control_status.st_size < 8) {
        (void)close(descriptor);
        free(retained);
        retained = NULL;
        return false;
    }
    long page_value = sysconf(_SC_PAGESIZE);
    if (page_value <= 0) {
        (void)close(descriptor);
        free(retained);
        retained = NULL;
        return false;
    }
    control_page = (size_t)page_value;
    control_map = mmap(NULL, control_page, PROT_READ, MAP_SHARED, descriptor, 0);
    (void)close(descriptor);
    if (control_map == MAP_FAILED) {
        control_map = NULL;
        free(retained);
        retained = NULL;
        return false;
    }
    control_word = (_Atomic uint64_t *)control_map;

    live_metadata.capture = session_id;
    live_metadata.scenario_buffer = "live:length-from-buffer";
    live_metadata.scenario_explicit = "live:explicit-length";
    live_metadata.epoch = "p5a-live";
    live_metadata.callsite = "unavailable";
    live_metadata.synthetic = false;

    eu4_gfx_set_constant_buffer_capture_set_live(&live_ops);
    eu4_gfx_set_constant_buffer_capture_thread_init();
    if (!eu4_gfx_set_constant_buffer_capture_install(site)) {
        eu4_gfx_set_constant_buffer_capture_set_live(NULL);
        (void)munmap(control_map, control_page);
        control_map = NULL;
        free(retained);
        retained = NULL;
        return false;
    }
    started_ns = now_ns();
    if (pthread_create(&coordinator_thread, NULL, coordinator_main, NULL) != 0) {
        (void)eu4_gfx_capture_restore();
        eu4_gfx_set_constant_buffer_capture_set_live(NULL);
        (void)munmap(control_map, control_page);
        control_map = NULL;
        free(retained);
        retained = NULL;
        return false;
    }
    coordinator_started = true;
    started = true;
    return true;
}

void eu4_gfx_live_capture_stop(void) {
    if (!started) {
        return;
    }
    atomic_store_explicit(&stop_requested, true, memory_order_release);
    if (coordinator_started) {
        (void)pthread_join(coordinator_thread, NULL);
        coordinator_started = false;
    }
    if (restore_on_stop) {
        (void)eu4_gfx_capture_restore();
        eu4_gfx_set_constant_buffer_capture_set_live(NULL);
    }
    free(retained);
    retained = NULL;
    started = false;
}
