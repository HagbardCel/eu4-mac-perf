#include "eu4_gfx_live_capture.h"

#include "eu4_gfx_capture_detour.h"
#include "eu4_gfx_set_constant_buffer_capture.h"

#include <CommonCrypto/CommonDigest.h>
#include <errno.h>
#include <fcntl.h>
#include <mach/mach.h>
#include <mach/mach_time.h>
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
#define LIVE_TICKET_FREE 0
#define LIVE_TICKET_RESERVED 1
#define LIVE_TICKET_READY 2
#define LIVE_TICKET_INCOMPLETE 3
#define LIVE_TICKET_WRITTEN 4
#define LIVE_TICKET_WRITE_FAILED 5
#define LIVE_TICKET_UNDRAINED 6
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
    int v2_admitted;
    int v2_bound;
    int v2_designated;
    int v2_sig;
    int v2_kind;
    int v2_counted;
    uint32_t v2_mask;
    uint64_t v2_generation;
} live_ticket;

static live_thread threads[EU4_GFX_LIVE_CAPTURE_THREADS];
static live_ticket tickets[EU4_GFX_LIVE_CAPTURE_QUOTA];
static _Atomic unsigned next_thread_id;
static _Atomic unsigned next_ticket;
static _Atomic unsigned active_entries;
static _Atomic int session_state;
static _Atomic bool stop_requested;
static _Atomic bool accepting;
static _Atomic unsigned not_admitted_busy;
static _Atomic unsigned not_admitted_slot;
static _Atomic unsigned not_admitted_pointer;
static _Atomic unsigned not_admitted_address;
static _Atomic unsigned not_admitted_quota;
static _Atomic unsigned not_admitted_closed;
static _Atomic unsigned not_admitted_thread_limit;
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
static int diversity;

enum {
    CTR_INTERCEPTED = 0,
    CTR_BUSY,
    CTR_SLOT,
    CTR_POINTER,
    CTR_ADDRESS,
    CTR_THREAD,
    CTR_CLOSED,
    CTR_READ_FAILED,
    CTR_SIG_READ,
    CTR_KEY_INSERT,
    CTR_TABLE,
    CTR_BUFFER,
    CTR_CAPACITY,
    CTR_CAS,
    CTR_PENDING,
    CTR_VERIFY_CONTENDED,
    CTR_SCREENED,
    CTR_FIRST,
    CTR_VERIFICATION,
    CTR_NOVEL,
    CTR_PRODUCER,
    CTR_DISTINCT,
    CTR_INELIGIBLE,
    CTR_INCOMPLETE,
    CTR_STORAGE,
    CTR_UNDRAINED,
    CTR_INVARIANT,
    CTR_DISCARDED,
    CTR_AUDIT,
    CTR_RECLAIMED,
    CTR_COUNT
};

#define SIG_N 128u
#define SIG_EMPTY 0u
#define SIG_PENDING 1u
#define SIG_ANCHORED 2u
#define SIG_UNANCHORED 3u
#define CELL_BITS 0x7ffu
#define CELL_FIXED 6u
#define CELL_BUFFER 7u
#define CELL_ABSENT 8u
#define CELL_ZERO 9u
#define CELL_NONZERO 10u

typedef struct {
    _Atomic uint64_t generation;
    _Atomic uint32_t key_slot;
    _Atomic uint32_t key_selector;
    _Atomic uint32_t key_input;
    _Atomic uint64_t word;
    eu4_gfx_capture_record anchor;
    int anchor_valid;
    char retained_rel[LIVE_PATH];
} sig_entry;

typedef struct {
    uint64_t generation;
    uint32_t slot;
    uint32_t selector;
    uint32_t input;
    uint64_t word;
} sig_snap;

typedef struct {
    _Atomic int armed;
    _Atomic int arrived;
    _Atomic int release;
} live_barrier;

enum { BAR_HOLD = 0, BAR_BEFORE, BAR_AFTER, BAR_MID, BAR_BIND, BAR_COUNT };

static sig_entry signatures[SIG_N];
static _Atomic uint64_t probe_chain[2];
static _Atomic uint64_t admission_word;
static _Atomic uint32_t insert_gate;
static _Atomic uint64_t counters[CTR_COUNT];
static _Atomic int invariant_lost;
static _Atomic int inject_admission;
static _Atomic int inject_rollback;
static _Atomic int inject_bind;
static _Atomic int inject_sig_read;
static _Atomic int inject_scan;
static _Atomic int inject_storage;
static _Atomic int inject_snapshot;
static _Atomic int inject_skew;
static _Atomic int inject_bad_hash;
static _Atomic uint64_t admission_attempts;
static _Atomic uint64_t buffer_claims;
static _Atomic unsigned file_seq;
static _Atomic unsigned ineligible_files;
static live_barrier barriers[BAR_COUNT];
static char retained_rel[64][LIVE_PATH];
static unsigned retained_n;
static char ineligible_rel[11][LIVE_PATH];
static unsigned ineligible_n;
static char audit_rel[8][LIVE_PATH];
static char audit_ref_rel[8][LIVE_PATH];
static uint8_t audit_sha[8][CC_SHA256_DIGEST_LENGTH];
static uint8_t audit_ref_sha[8][CC_SHA256_DIGEST_LENGTH];
static uint32_t audit_slot[8];
static uint32_t audit_selector[8];
static uint32_t audit_input[8];
static unsigned audit_n;
static char predicate_label[64];
static uint64_t screen_samples[16384];
static unsigned screen_n;

typedef struct {
    uint8_t used;
    uint8_t count;
    uint32_t slot;
    uint32_t word_count;
    uint32_t input_value;
    uint8_t selector_buffer;
    uint8_t input_class;
    uint8_t dirty;
    uint8_t buffer_state;
    uint8_t handle_state;
    uint8_t alias;
    uint8_t pointer_role;
    uint8_t rax_class;
    uint8_t canary;
} behavior_row;

static behavior_row behaviors[64];
static unsigned behavior_n;

static void v2_reset(void);
static int live_select(eu4_gfx_capture_record *record,
                       const eu4_gfx_capture_frame *frame);
static void classify_ticket(unsigned ticket);
static void account_terminal_reserved(unsigned ticket);
static void account_undrained(unsigned ticket);
static bool write_summary_v2(void);
static int identities_hold(void);

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

static unsigned admitted_count(void) {
    unsigned count = 0;
    for (unsigned i = 0; i < EU4_GFX_LIVE_CAPTURE_QUOTA; i++) {
        if (atomic_load_explicit(&tickets[i].state, memory_order_acquire) !=
            LIVE_TICKET_FREE) {
            count++;
        }
    }
    return count;
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
        if (diversity) {
            atomic_fetch_add(&counters[CTR_THREAD], 1);
            atomic_fetch_add(&counters[CTR_INTERCEPTED], 1);
        } else {
            atomic_fetch_add_explicit(&not_admitted_thread_limit, 1,
                                      memory_order_relaxed);
        }
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
        atomic_fetch_add_explicit(&not_admitted_closed, 1,
                                  memory_order_relaxed);
        return -1;
    }
    unsigned ticket = atomic_load_explicit(&next_ticket, memory_order_acquire);
    while (ticket < EU4_GFX_LIVE_CAPTURE_QUOTA) {
        if (atomic_compare_exchange_weak_explicit(
                &next_ticket, &ticket, ticket + 1, memory_order_acq_rel,
                memory_order_acquire)) {
            atomic_store_explicit(&tickets[ticket].state,
                                  LIVE_TICKET_RESERVED, memory_order_release);
            return (int)ticket;
        }
    }
    atomic_fetch_add_explicit(&not_admitted_quota, 1, memory_order_relaxed);
    return -1;
}

static bool live_publish(unsigned ticket,
                         const eu4_gfx_capture_record *record) {
    if (ticket >= EU4_GFX_LIVE_CAPTURE_QUOTA) {
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
    if (ticket >= EU4_GFX_LIVE_CAPTURE_QUOTA) {
        return;
    }
    int expected = LIVE_TICKET_RESERVED;
    (void)atomic_compare_exchange_strong_explicit(
        &tickets[ticket].state, &expected, LIVE_TICKET_INCOMPLETE,
        memory_order_acq_rel, memory_order_acquire);
}

static void live_refuse(eu4_gfx_capture_refusal reason) {
    if (diversity) {
        int which = -1;
        switch (reason) {
        case EU4_GFX_CAPTURE_REFUSE_BUSY:
            which = CTR_BUSY;
            break;
        case EU4_GFX_CAPTURE_REFUSE_SLOT:
            which = CTR_SLOT;
            break;
        case EU4_GFX_CAPTURE_REFUSE_POINTER:
            which = CTR_POINTER;
            break;
        case EU4_GFX_CAPTURE_REFUSE_ADDRESS:
            which = CTR_ADDRESS;
            break;
        case EU4_GFX_CAPTURE_REFUSE_THREAD_LIMIT:
            which = CTR_THREAD;
            break;
        case EU4_GFX_CAPTURE_REFUSE_QUOTA:
            which = CTR_CAPACITY;
            break;
        }
        if (which >= 0) {
            atomic_fetch_add(&counters[which], 1);
            atomic_fetch_add(&counters[CTR_INTERCEPTED], 1);
        }
        return;
    }
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
        atomic_fetch_add_explicit(&not_admitted_quota, 1, memory_order_relaxed);
        break;
    case EU4_GFX_CAPTURE_REFUSE_THREAD_LIMIT:
        atomic_fetch_add_explicit(&not_admitted_thread_limit, 1,
                                  memory_order_relaxed);
        break;
    }
}

static eu4_gfx_capture_live_ops live_ops;

static bool write_transcript(unsigned ticket) {
    char final_path[LIVE_PATH];
    char partial_path[LIVE_PATH];
    int final_length = snprintf(final_path, sizeof(final_path),
                                "%s/%s.%u.capture", captures_dir, session_id,
                                ticket);
    int partial_length = snprintf(partial_path, sizeof(partial_path),
                                  "%s/%s.%u.capture.partial", captures_dir,
                                  session_id, ticket);
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
        return false;
    }
    bool ok = eu4_gfx_set_constant_buffer_capture_write(
        file, &tickets[ticket].record, &live_metadata);
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
    return rename(partial_path, final_path) == 0;
}

static void drain_ready(uint64_t deadline) {
    unsigned admitted = diversity
                            ? EU4_GFX_LIVE_CAPTURE_QUOTA
                            : atomic_load_explicit(&next_ticket,
                                                   memory_order_acquire);
    if (admitted > EU4_GFX_LIVE_CAPTURE_QUOTA) {
        admitted = EU4_GFX_LIVE_CAPTURE_QUOTA;
    }
    for (unsigned i = 0; i < admitted; i++) {
        int state = atomic_load_explicit(&tickets[i].state,
                                         memory_order_acquire);
        if (state != LIVE_TICKET_READY) {
            continue;
        }
        if (deadline != 0 && now_ns() >= deadline) {
            if (diversity) {
                account_undrained(i);
            }
            atomic_store_explicit(&tickets[i].state, LIVE_TICKET_UNDRAINED,
                                  memory_order_release);
            continue;
        }
        if (diversity) {
            classify_ticket(i);
            continue;
        }
        if (write_transcript(i)) {
            atomic_store_explicit(&tickets[i].state, LIVE_TICKET_WRITTEN,
                                  memory_order_release);
        } else {
            atomic_store_explicit(&tickets[i].state,
                                  LIVE_TICKET_WRITE_FAILED,
                                  memory_order_release);
        }
    }
}

static bool tickets_terminal(void) {
    unsigned admitted = diversity
                            ? EU4_GFX_LIVE_CAPTURE_QUOTA
                            : atomic_load_explicit(&next_ticket,
                                                   memory_order_acquire);
    if (admitted > EU4_GFX_LIVE_CAPTURE_QUOTA) {
        admitted = EU4_GFX_LIVE_CAPTURE_QUOTA;
    }
    for (unsigned i = 0; i < admitted; i++) {
        int state = atomic_load_explicit(&tickets[i].state,
                                         memory_order_acquire);
        if (state == LIVE_TICKET_RESERVED || state == LIVE_TICKET_READY) {
            return false;
        }
    }
    return true;
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
    unsigned admitted = diversity
                            ? EU4_GFX_LIVE_CAPTURE_QUOTA
                            : atomic_load_explicit(&next_ticket,
                                                   memory_order_acquire);
    if (admitted > EU4_GFX_LIVE_CAPTURE_QUOTA) {
        admitted = EU4_GFX_LIVE_CAPTURE_QUOTA;
    }
    for (;;) {
        bool reserved = false;
        for (unsigned i = 0; i < admitted; i++) {
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

static void terminalize_reserved(void) {
    unsigned admitted = diversity
                            ? EU4_GFX_LIVE_CAPTURE_QUOTA
                            : atomic_load_explicit(&next_ticket,
                                                   memory_order_acquire);
    if (admitted > EU4_GFX_LIVE_CAPTURE_QUOTA) {
        admitted = EU4_GFX_LIVE_CAPTURE_QUOTA;
    }
    for (unsigned i = 0; i < admitted; i++) {
        int expected = LIVE_TICKET_RESERVED;
        if (atomic_compare_exchange_strong_explicit(
                &tickets[i].state, &expected, LIVE_TICKET_INCOMPLETE,
                memory_order_acq_rel, memory_order_acquire)) {
            if (diversity) {
                account_terminal_reserved(i);
            }
        }
    }
}

static void terminalize_undrained(void) {
    unsigned admitted = atomic_load_explicit(&next_ticket, memory_order_acquire);
    if (admitted > EU4_GFX_LIVE_CAPTURE_QUOTA) {
        admitted = EU4_GFX_LIVE_CAPTURE_QUOTA;
    }
    for (unsigned i = 0; i < admitted; i++) {
        int expected = LIVE_TICKET_READY;
        (void)atomic_compare_exchange_strong_explicit(
            &tickets[i].state, &expected, LIVE_TICKET_UNDRAINED,
            memory_order_acq_rel, memory_order_acquire);
    }
}

static bool write_summary(void) {
    char partial_path[LIVE_PATH];
    int length = snprintf(partial_path, sizeof(partial_path), "%s.partial",
                          summary_path);
    if (length < 0 || (size_t)length >= sizeof(partial_path)) {
        return false;
    }

    unsigned admitted = admitted_count();
    unsigned incomplete = 0;
    unsigned written = 0;
    unsigned write_failed = 0;
    unsigned undrained = 0;
    unsigned reserved = 0;
    unsigned ready = 0;
    for (unsigned i = 0; i < EU4_GFX_LIVE_CAPTURE_QUOTA; i++) {
        switch (atomic_load_explicit(&tickets[i].state,
                                     memory_order_acquire)) {
        case LIVE_TICKET_INCOMPLETE:
            incomplete++;
            break;
        case LIVE_TICKET_WRITTEN:
            written++;
            break;
        case LIVE_TICKET_WRITE_FAILED:
            write_failed++;
            break;
        case LIVE_TICKET_UNDRAINED:
            undrained++;
            break;
        case LIVE_TICKET_RESERVED:
            reserved++;
            break;
        case LIVE_TICKET_READY:
            ready++;
            break;
        default:
            break;
        }
    }
    unsigned completed = written + write_failed + undrained;
    unsigned not_admitted =
        atomic_load_explicit(&not_admitted_busy, memory_order_relaxed) +
        atomic_load_explicit(&not_admitted_slot, memory_order_relaxed) +
        atomic_load_explicit(&not_admitted_pointer, memory_order_relaxed) +
        atomic_load_explicit(&not_admitted_address, memory_order_relaxed) +
        atomic_load_explicit(&not_admitted_quota, memory_order_relaxed) +
        atomic_load_explicit(&not_admitted_closed, memory_order_relaxed) +
        atomic_load_explicit(&not_admitted_thread_limit, memory_order_relaxed);

    int descriptor = open(partial_path, O_WRONLY | O_CREAT | O_TRUNC, 0600);
    if (descriptor < 0) {
        return false;
    }
    FILE *file = fdopen(descriptor, "w");
    if (file == NULL) {
        (void)close(descriptor);
        return false;
    }
    fprintf(file, "EU4-GFX-LIVE-SUMMARY v1\n");
    fprintf(file, "session=%s\n", session_id);
    fprintf(file, "pid=%d\n", (int)getpid());
    fprintf(file, "state=%s\n", quiescence_timeout ? "partial" : "sealed");
    fprintf(file, "closed_reason=%s\n",
            closed_reason != NULL ? closed_reason : "unknown");
    fprintf(file, "quiescence=%s\n", quiescence_timeout ? "timeout" : "ok");
    fprintf(file, "started_ns=%llu\n", (unsigned long long)started_ns);
    fprintf(file, "armed_ns=%llu\n", (unsigned long long)armed_ns);
    fprintf(file, "closed_ns=%llu\n", (unsigned long long)closed_ns);
    fprintf(file, "quota=%u\n", EU4_GFX_LIVE_CAPTURE_QUOTA);
    fprintf(file, "threads=%u\n", thread_count());
    fprintf(file, "intercepted=%u\n", admitted + not_admitted);
    fprintf(file, "not_admitted=%u\n", not_admitted);
    fprintf(file, "not_admitted_busy=%u\n",
            atomic_load_explicit(&not_admitted_busy, memory_order_relaxed));
    fprintf(file, "not_admitted_slot=%u\n",
            atomic_load_explicit(&not_admitted_slot, memory_order_relaxed));
    fprintf(file, "not_admitted_pointer=%u\n",
            atomic_load_explicit(&not_admitted_pointer, memory_order_relaxed));
    fprintf(file, "not_admitted_address=%u\n",
            atomic_load_explicit(&not_admitted_address, memory_order_relaxed));
    fprintf(file, "not_admitted_quota=%u\n",
            atomic_load_explicit(&not_admitted_quota, memory_order_relaxed));
    fprintf(file, "not_admitted_closed=%u\n",
            atomic_load_explicit(&not_admitted_closed, memory_order_relaxed));
    fprintf(file, "not_admitted_thread_limit=%u\n",
            atomic_load_explicit(&not_admitted_thread_limit,
                                 memory_order_relaxed));
    fprintf(file, "admitted=%u\n", admitted);
    fprintf(file, "incomplete=%u\n", incomplete);
    fprintf(file, "completed=%u\n", completed);
    fprintf(file, "written=%u\n", written);
    fprintf(file, "write_failed=%u\n", write_failed);
    fprintf(file, "undrained=%u\n", undrained);
    fprintf(file, "reserved=%u\n", reserved);
    fprintf(file, "ready=%u\n", ready);
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
    atomic_store_explicit(&session_state, LIVE_STATE_CLOSING,
                          memory_order_release);
    uint64_t quiesce_deadline = now_ns() + LIVE_QUIESCE_NS;
    bool active_ok = wait_active(quiesce_deadline);
    bool reserved_ok = wait_reserved(quiesce_deadline);
    if (!active_ok || !reserved_ok) {
        quiescence_timeout = true;
    }

    atomic_store_explicit(&accepting, false, memory_order_release);

    terminalize_reserved();
    uint64_t drain_deadline = now_ns() + LIVE_DRAIN_NS;
    drain_ready(drain_deadline);
    terminalize_undrained();
    closed_ns = now_ns();

    bool summary_ok = diversity ? write_summary_v2() : write_summary();
    (void)write_done(summary_ok ? (quiescence_timeout ? "partial" : "sealed")
                                : "error");
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
            drain_ready(0);
            if (mode == 2) {
                reason = "operator";
                break;
            }
            if (atomic_load_explicit(&stop_requested, memory_order_acquire)) {
                reason = "destructor";
                break;
            }
            if (!diversity &&
                atomic_load_explicit(&next_ticket, memory_order_acquire) >=
                    EU4_GFX_LIVE_CAPTURE_QUOTA &&
                tickets_terminal()) {
                reason = "quota";
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
        sleep_ms(diversity ? 1 : 10);
    }
    close_session(reason);
    return NULL;
}

static bool start_session(void *site, const char *run_directory,
                           const char *control_path,
                           const char *capture_session_id, unsigned seconds,
                           bool restore, int v2) {
    if (started || site == NULL || run_directory == NULL ||
        control_path == NULL || !valid_session_id(capture_session_id) ||
        seconds == 0) {
        return false;
    }
    memset(threads, 0, sizeof(threads));
    memset(tickets, 0, sizeof(tickets));
    atomic_store(&next_thread_id, 0);
    atomic_store(&next_ticket, 0);
    atomic_store(&active_entries, 0);
    atomic_store(&session_state, LIVE_STATE_DISARMED);
    atomic_store(&stop_requested, false);
    atomic_store(&accepting, false);
    atomic_store(&not_admitted_busy, 0);
    atomic_store(&not_admitted_slot, 0);
    atomic_store(&not_admitted_pointer, 0);
    atomic_store(&not_admitted_address, 0);
    atomic_store(&not_admitted_quota, 0);
    atomic_store(&not_admitted_closed, 0);
    atomic_store(&not_admitted_thread_limit, 0);
    diversity = v2;
    if (v2) {
        v2_reset();
    }
    live_ops = (eu4_gfx_capture_live_ops){
        .thread_claim = live_thread_claim,
        .thread_lookup = live_thread_lookup,
        .begin = live_begin,
        .end = live_end,
        .admit = live_admit,
        .select = v2 ? live_select : NULL,
        .publish = live_publish,
        .incomplete = live_incomplete,
        .refuse = live_refuse,
        .safe_read = true,
    };
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
        return false;
    }
    if (mkdir(captures_dir, 0700) != 0 && errno != EEXIST) {
        return false;
    }
    struct stat directory_status;
    if (stat(captures_dir, &directory_status) != 0 ||
        !S_ISDIR(directory_status.st_mode)) {
        return false;
    }

    int descriptor = open(control_path, O_RDONLY);
    if (descriptor < 0) {
        return false;
    }
    struct stat control_status;
    if (fstat(descriptor, &control_status) != 0 ||
        control_status.st_size < 8) {
        (void)close(descriptor);
        return false;
    }
    long page_value = sysconf(_SC_PAGESIZE);
    if (page_value <= 0) {
        (void)close(descriptor);
        return false;
    }
    control_page = (size_t)page_value;
    control_map = mmap(NULL, control_page, PROT_READ, MAP_SHARED, descriptor, 0);
    (void)close(descriptor);
    if (control_map == MAP_FAILED) {
        control_map = NULL;
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
        return false;
    }
    started_ns = now_ns();
    if (pthread_create(&coordinator_thread, NULL, coordinator_main, NULL) != 0) {
        (void)eu4_gfx_capture_restore();
        eu4_gfx_set_constant_buffer_capture_set_live(NULL);
        (void)munmap(control_map, control_page);
        control_map = NULL;
        return false;
    }
    coordinator_started = true;
    started = true;
    return true;
}

bool eu4_gfx_live_capture_start(void *site, const char *run_directory,
                                const char *control_path,
                                const char *capture_session_id,
                                unsigned seconds, bool restore) {
    return start_session(site, run_directory, control_path, capture_session_id,
                         seconds, restore, 1);
}

bool eu4_gfx_live_capture_start_v1(void *site, const char *run_directory,
                                   const char *control_path,
                                   const char *capture_session_id,
                                   unsigned seconds, bool restore) {
    return start_session(site, run_directory, control_path, capture_session_id,
                         seconds, restore, 0);
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
    started = false;
}

static uint64_t ctr(int index) {
    return atomic_load(&counters[index]);
}

static int identities_hold(void) {
    uint64_t invalid = ctr(CTR_BUSY) + ctr(CTR_SLOT) + ctr(CTR_POINTER) +
                       ctr(CTR_ADDRESS) + ctr(CTR_THREAD) + ctr(CTR_CLOSED) +
                       ctr(CTR_READ_FAILED) + ctr(CTR_SIG_READ) +
                       ctr(CTR_KEY_INSERT) + ctr(CTR_TABLE) + ctr(CTR_BUFFER) +
                       ctr(CTR_CAPACITY) + ctr(CTR_CAS);
    uint64_t selected = ctr(CTR_FIRST) + ctr(CTR_VERIFICATION);
    uint64_t classified =
        ctr(CTR_NOVEL) + ctr(CTR_PRODUCER) + ctr(CTR_DISTINCT) +
        ctr(CTR_INELIGIBLE) + ctr(CTR_INCOMPLETE) + ctr(CTR_STORAGE) +
        ctr(CTR_UNDRAINED) + ctr(CTR_INVARIANT);
    uint64_t sum = invalid + ctr(CTR_PENDING) + ctr(CTR_VERIFY_CONTENDED) +
                   ctr(CTR_SCREENED) + selected;
    return sum == ctr(CTR_INTERCEPTED) && selected == classified &&
           ctr(CTR_PRODUCER) == ctr(CTR_DISCARDED) + ctr(CTR_AUDIT);
}

unsigned eu4_gfx_live_capture_test_lower_bound(uint32_t remaining) {
    remaining &= CELL_BITS;
    unsigned slots = 0;
    for (unsigned i = 0; i < 6; i++) {
        if (remaining & (1u << i)) {
            slots++;
        }
    }
    unsigned need_fixed =
        (remaining & ((1u << CELL_FIXED) | (1u << CELL_ABSENT))) ? 1u : 0u;
    unsigned need_zero = (remaining & (1u << CELL_ZERO)) ? 1u : 0u;
    unsigned need_nonzero = (remaining & (1u << CELL_NONZERO)) ? 1u : 0u;
    unsigned need_buffer =
        ((remaining & (1u << CELL_BUFFER)) && need_zero == 0 &&
         need_nonzero == 0)
            ? 1u
            : 0u;
    unsigned sum = need_fixed + need_zero + need_nonzero + need_buffer;
    return slots > sum ? slots : sum;
}

static void decode_adm(uint64_t word, uint64_t *retained, uint64_t *p_cov,
                       uint64_t *p_div, uint32_t *established,
                       uint32_t *reserved) {
    *retained = word & 0x7f;
    *p_cov = (word >> 7) & 0x7f;
    *p_div = (word >> 14) & 0x7f;
    *established = (uint32_t)((word >> 21) & CELL_BITS);
    *reserved = (uint32_t)((word >> 32) & CELL_BITS);
}

static uint64_t pack_adm(uint64_t retained, uint64_t p_cov, uint64_t p_div,
                         uint32_t established, uint32_t reserved) {
    return retained | (p_cov << 7) | (p_div << 14) |
           ((uint64_t)established << 21) | ((uint64_t)reserved << 32);
}

static int admission_ok(void) {
    uint64_t retained, p_cov, p_div;
    uint32_t established, reserved;
    decode_adm(atomic_load(&admission_word), &retained, &p_cov, &p_div,
               &established, &reserved);
    unsigned lower = eu4_gfx_live_capture_test_lower_bound(
        CELL_BITS & ~(established | reserved));
    return retained + p_cov + p_div + lower <= 64;
}

static uint64_t sig_pack(unsigned state, int verify, int reprobe,
                         uint64_t encounter) {
    return (encounter << 4) | (reprobe ? 8ull : 0ull) | (verify ? 4ull : 0ull) |
           (state & 3u);
}

static unsigned sig_hash(uint32_t slot, uint32_t selector, uint32_t input) {
    uint32_t mixed = slot * 0x9e3779b9u ^ selector * 0x85ebca6bu ^
                     input * 0xc2b2ae35u;
    return mixed % SIG_N;
}

static int chain_on(unsigned index) {
    return (int)((atomic_load(&probe_chain[index / 64]) >> (index % 64)) & 1ull);
}

static void chain_mark(unsigned index) {
    atomic_fetch_or(&probe_chain[index / 64], 1ull << (index % 64));
}

static _Atomic int unique_scan;

static int sig_read(unsigned index, sig_snap *out) {
    sig_entry *entry = &signatures[index];
    for (int attempt = 0; attempt < 8; attempt++) {
        if (atomic_load(&inject_sig_read)) {
            continue;
        }
        if (atomic_load(&inject_scan) && atomic_load(&unique_scan) &&
            index == 3) {
            continue;
        }
        /* Acquire snapshot: seq_cst fences miss the 1us screen cap. */
        uint64_t before = atomic_load_explicit(&entry->generation,
                                               memory_order_acquire);
        if (before & 1ull) {
            continue;
        }
        uint32_t slot = atomic_load_explicit(&entry->key_slot,
                                             memory_order_relaxed);
        uint32_t selector = atomic_load_explicit(&entry->key_selector,
                                                 memory_order_relaxed);
        uint32_t input = atomic_load_explicit(&entry->key_input,
                                              memory_order_relaxed);
        uint64_t word = atomic_load_explicit(&entry->word, memory_order_relaxed);
        atomic_thread_fence(memory_order_acquire);
        uint64_t after = atomic_load_explicit(&entry->generation,
                                              memory_order_relaxed);
        if (before == after) {
            out->generation = after;
            out->slot = slot;
            out->selector = selector;
            out->input = input;
            out->word = word;
            return 1;
        }
    }
    return 0;
}

static void v2_reset(void) {
    memset(signatures, 0, sizeof(signatures));
    memset(behaviors, 0, sizeof(behaviors));
    memset(retained_rel, 0, sizeof(retained_rel));
    memset(ineligible_rel, 0, sizeof(ineligible_rel));
    memset(audit_rel, 0, sizeof(audit_rel));
    memset(audit_ref_rel, 0, sizeof(audit_ref_rel));
    memset(barriers, 0, sizeof(barriers));
    behavior_n = 0;
    retained_n = 0;
    ineligible_n = 0;
    audit_n = 0;
    screen_n = 0;
    predicate_label[0] = '\0';
    atomic_store(&probe_chain[0], 0);
    atomic_store(&probe_chain[1], 0);
    atomic_store(&admission_word, 0);
    atomic_store(&insert_gate, 0);
    atomic_store(&invariant_lost, 0);
    atomic_store(&inject_admission, 0);
    atomic_store(&inject_rollback, 0);
    atomic_store(&inject_bind, 0);
    atomic_store(&inject_sig_read, 0);
    atomic_store(&inject_scan, 0);
    atomic_store(&inject_storage, 0);
    atomic_store(&inject_snapshot, 0);
    atomic_store(&inject_skew, 0);
    atomic_store(&inject_bad_hash, 0);
    atomic_store(&admission_attempts, 0);
    atomic_store(&buffer_claims, 0);
    atomic_store(&file_seq, 0);
    atomic_store(&ineligible_files, 0);
    atomic_store(&unique_scan, 0);
    for (int i = 0; i < CTR_COUNT; i++) {
        atomic_store(&counters[i], 0);
    }
    if (eu4_gfx_live_capture_test_lower_bound(
            (1u << 3) | (1u << CELL_FIXED) | (1u << CELL_ZERO)) != 2 ||
        eu4_gfx_live_capture_test_lower_bound(CELL_BITS) != 6) {
        atomic_store(&invariant_lost, 1);
    }
}

static void barrier_wait(int index) {
    if (!atomic_load(&barriers[index].armed)) {
        return;
    }
    int expected = 0;
    if (!atomic_compare_exchange_strong(&barriers[index].arrived, &expected,
                                        1)) {
        return;
    }
    while (!atomic_load(&barriers[index].release)) {
        struct timespec delay = {.tv_sec = 0, .tv_nsec = 1000000L};
        (void)nanosleep(&delay, NULL);
    }
}

static int barrier_id(const char *point) {
    if (strcmp(point, "hold-gate") == 0) {
        return BAR_HOLD;
    }
    if (strcmp(point, "before-gate") == 0) {
        return BAR_BEFORE;
    }
    if (strcmp(point, "after-pending") == 0) {
        return BAR_AFTER;
    }
    if (strcmp(point, "mid-publish") == 0) {
        return BAR_MID;
    }
    if (strcmp(point, "after-bind") == 0) {
        return BAR_BIND;
    }
    return -1;
}

static uint32_t project_cells(uint32_t slot, uint32_t selector, uint32_t input) {
    uint32_t mask = slot < 6 ? (1u << slot) : 0;
    if (selector == 0xffffffffu) {
        mask |= 1u << CELL_BUFFER;
        mask |= 1u << (input == 0 ? CELL_ZERO : CELL_NONZERO);
    } else {
        mask |= 1u << CELL_FIXED;
        mask |= 1u << CELL_ABSENT;
    }
    return mask;
}

static int priority_open(uint32_t slot, uint32_t selector, uint32_t input) {
    uint64_t retained, p_cov, p_div;
    uint32_t established, reserved;
    decode_adm(atomic_load_explicit(&admission_word, memory_order_acquire),
               &retained, &p_cov, &p_div, &established, &reserved);
    return (project_cells(slot, selector, input) & ~(established | reserved)) !=
           0;
}

static int scheduled(uint64_t encounter) {
    return encounter == 2 || (encounter > 2 && ((encounter - 1) % 16) == 0);
}

static int take_gate(void) {
    for (int attempt = 0; attempt < 8; attempt++) {
        uint32_t expected = 0;
        if (atomic_compare_exchange_strong(&insert_gate, &expected, 1)) {
            return 1;
        }
    }
    return 0;
}

static int sig_install(unsigned index, uint32_t slot, uint32_t selector,
                       uint32_t input, unsigned expect, uint64_t *generation) {
    sig_snap snap;
    if (!sig_read(index, &snap) || (snap.word & 3u) != expect) {
        return 0;
    }
    if (expect == SIG_UNANCHORED && (snap.word & 8ull)) {
        return 0;
    }
    uint64_t observed = snap.generation;
    if (!atomic_compare_exchange_strong_explicit(
            &signatures[index].generation, &observed, snap.generation + 1,
            memory_order_acq_rel, memory_order_acquire)) {
        return 0;
    }
    uint64_t word = atomic_load(&signatures[index].word);
    if (word != snap.word || (word & 3u) != expect ||
        (expect == SIG_UNANCHORED && (word & 8ull))) {
        atomic_store(&signatures[index].generation, snap.generation);
        return 0;
    }
    uint32_t old_slot = atomic_load(&signatures[index].key_slot);
    uint32_t old_selector = atomic_load(&signatures[index].key_selector);
    uint32_t old_input = atomic_load(&signatures[index].key_input);
    atomic_store_explicit(&signatures[index].key_slot, slot,
                          memory_order_relaxed);
    atomic_store_explicit(&signatures[index].key_selector, selector,
                          memory_order_relaxed);
    atomic_store_explicit(&signatures[index].key_input, input,
                          memory_order_relaxed);
    barrier_wait(BAR_MID);
    uint64_t pending = sig_pack(SIG_PENDING, 0, 0, 0);
    if (!atomic_compare_exchange_strong_explicit(
            &signatures[index].word, &word, pending, memory_order_acq_rel,
            memory_order_acquire)) {
        atomic_store(&signatures[index].key_slot, old_slot);
        atomic_store(&signatures[index].key_selector, old_selector);
        atomic_store(&signatures[index].key_input, old_input);
        atomic_store(&signatures[index].generation, snap.generation);
        return 0;
    }
    atomic_store_explicit(&signatures[index].generation, snap.generation + 2,
                          memory_order_release);
    *generation = snap.generation + 2;
    if (expect == SIG_UNANCHORED) {
        atomic_fetch_add(&counters[CTR_RECLAIMED], 1);
    }
    return 1;
}

static int sig_write(unsigned index, unsigned state, uint64_t encounter,
                     int mark_chain) {
    for (int attempt = 0; attempt < 8; attempt++) {
        uint64_t generation = atomic_load(&signatures[index].generation);
        if (generation & 1ull) {
            continue;
        }
        uint64_t observed = generation;
        if (!atomic_compare_exchange_strong_explicit(
                &signatures[index].generation, &observed, generation + 1,
                memory_order_acq_rel, memory_order_acquire)) {
            continue;
        }
        if (mark_chain) {
            chain_mark(index);
        }
        atomic_store_explicit(&signatures[index].word,
                              sig_pack(state, 0, 0, encounter),
                              memory_order_relaxed);
        atomic_store_explicit(&signatures[index].generation, generation + 2,
                              memory_order_release);
        return 1;
    }
    atomic_store(&invariant_lost, 1);
    return 0;
}

static void clear_inflight(unsigned index) {
    for (int attempt = 0; attempt < 8; attempt++) {
        sig_snap snap;
        if (!sig_read(index, &snap)) {
            return;
        }
        uint64_t next = snap.word & ~0xcu;
        uint64_t current = snap.word;
        if (atomic_compare_exchange_strong(&signatures[index].word, &current,
                                           next)) {
            return;
        }
    }
}

static int rollback_reservation(int designated, uint32_t mask) {
    if (atomic_load(&inject_rollback)) {
        atomic_store(&invariant_lost, 1);
        return 0;
    }
    for (int attempt = 0; attempt < 64; attempt++) {
        uint64_t word = atomic_load(&admission_word);
        uint64_t retained, p_cov, p_div;
        uint32_t established, reserved;
        decode_adm(word, &retained, &p_cov, &p_div, &established, &reserved);
        if (designated) {
            if (p_cov == 0) {
                return 1;
            }
            p_cov--;
            reserved &= ~mask;
        } else if (p_div == 0) {
            return 1;
        } else {
            p_div--;
        }
        uint64_t next =
            pack_adm(retained, p_cov, p_div, established, reserved);
        if (atomic_compare_exchange_weak(&admission_word, &word, next)) {
            return 1;
        }
    }
    atomic_store(&invariant_lost, 1);
    return 0;
}

static int commit_retained(int designated, uint32_t mask, uint32_t project) {
    for (int attempt = 0; attempt < 64; attempt++) {
        uint64_t word = atomic_load(&admission_word);
        uint64_t retained, p_cov, p_div;
        uint32_t established, reserved;
        decode_adm(word, &retained, &p_cov, &p_div, &established, &reserved);
        if (designated) {
            if (p_cov == 0) {
                return 0;
            }
            p_cov--;
            reserved &= ~mask;
        } else if (p_div == 0) {
            return 0;
        } else {
            p_div--;
        }
        retained++;
        established |= project & CELL_BITS;
        if (!atomic_compare_exchange_weak(
                &admission_word, &word,
                pack_adm(retained, p_cov, p_div, established, reserved))) {
            continue;
        }
        if (!admission_ok()) {
            atomic_store(&invariant_lost, 1);
        }
        return 1;
    }
    atomic_store(&invariant_lost, 1);
    return 0;
}

static void release_ticket(unsigned ticket) {
    tickets[ticket].v2_admitted = 0;
    tickets[ticket].v2_bound = 0;
    tickets[ticket].v2_counted = 0;
    atomic_store(&tickets[ticket].state, LIVE_TICKET_FREE);
}

static int claim_buffer(void) {
    for (unsigned i = 0; i < EU4_GFX_LIVE_CAPTURE_QUOTA; i++) {
        int expected = LIVE_TICKET_FREE;
        if (atomic_compare_exchange_strong(&tickets[i].state, &expected,
                                           LIVE_TICKET_RESERVED)) {
            tickets[i].v2_admitted = 0;
            tickets[i].v2_bound = 0;
            tickets[i].v2_counted = 0;
            tickets[i].v2_designated = 0;
            tickets[i].v2_sig = -1;
            tickets[i].v2_kind = 0;
            tickets[i].v2_mask = 0;
            atomic_fetch_add(&buffer_claims, 1);
            return (int)i;
        }
    }
    return -1;
}

static int lookup_key(uint32_t slot, uint32_t selector, uint32_t input,
                      int *index, sig_snap *snap) {
    unsigned start = sig_hash(slot, selector, input);
    for (unsigned step = 0; step < SIG_N; step++) {
        unsigned i = (start + step) % SIG_N;
        if (!sig_read(i, snap)) {
            return -1;
        }
        unsigned state = snap->word & 3u;
        if (state != SIG_EMPTY && snap->slot == slot &&
            snap->selector == selector && snap->input == input) {
            *index = (int)i;
            return 1;
        }
        if (state == SIG_EMPTY && !chain_on(i)) {
            return 0;
        }
    }
    return 0;
}

static int advance_existing(sig_snap *snap, int index, int anchored,
                            uint32_t slot, uint32_t selector, uint32_t input,
                            int *select) {
    *select = 0;
    for (int attempt = 0; attempt < 8; attempt++) {
        if (attempt != 0 && !sig_read((unsigned)index, snap)) {
            continue;
        }
        if ((snap->word & 3u) != (anchored ? SIG_ANCHORED : SIG_UNANCHORED) ||
            snap->slot != slot || snap->selector != selector ||
            snap->input != input) {
            return -1;
        }
        if (snap->word & 0xcu) {
            return CTR_VERIFY_CONTENDED;
        }
        uint64_t encounter = snap->word >> 4;
        uint64_t next = sig_pack(snap->word & 3u, 0, 0, encounter + 1);
        uint64_t current = snap->word;
        if (!atomic_compare_exchange_strong_explicit(
                &signatures[index].word, &current, next, memory_order_acq_rel,
                memory_order_acquire)) {
            continue;
        }
        *select = scheduled(encounter + 1) ||
                  priority_open(slot, selector, input);
        return CTR_SCREENED;
    }
    return CTR_SIG_READ;
}

static int set_inflight(int index, uint64_t generation, int reprobe,
                        uint64_t *bound) {
    for (int attempt = 0; attempt < 8; attempt++) {
        sig_snap snap;
        if (!sig_read((unsigned)index, &snap) || snap.generation != generation) {
            return 0;
        }
        unsigned state = snap.word & 3u;
        if (reprobe) {
            if (state != SIG_UNANCHORED || (snap.word & 8ull)) {
                return 0;
            }
        } else if (state != SIG_ANCHORED || (snap.word & 4ull)) {
            return 0;
        }
        uint64_t next = snap.word | (reprobe ? 8ull : 4ull);
        uint64_t current = snap.word;
        if (atomic_compare_exchange_strong(&signatures[index].word, &current,
                                           next)) {
            *bound = snap.generation;
            return 1;
        }
    }
    return 0;
}

static int live_select(eu4_gfx_capture_record *record,
                       const eu4_gfx_capture_frame *frame) {
    uint64_t started = mach_absolute_time();
    atomic_fetch_add_explicit(&counters[CTR_INTERCEPTED], 1,
                              memory_order_relaxed);
    if (!atomic_load_explicit(&accepting, memory_order_acquire)) {
        atomic_fetch_add(&counters[CTR_CLOSED], 1);
        return -1;
    }
    uint32_t slot = (uint32_t)frame->rcx;
    uint32_t selector = (uint32_t)frame->r9;
    uint32_t input = selector;
    if (selector == 0xffffffffu) {
        uint8_t dword[4];
        if (!eu4_gfx_set_constant_buffer_capture_read(
                dword, (const void *)(uintptr_t)frame->rsi, sizeof(dword))) {
            atomic_fetch_add(&counters[CTR_READ_FAILED], 1);
            return -1;
        }
        memcpy(record->buffer, dword, sizeof(dword));
        record->buffer_snapshotted = 1;
        memcpy(&input, dword, sizeof(input));
        if (atomic_load(&inject_snapshot)) {
            record->snapshot_mismatch = 1;
        }
    }
    record->screen_slot = slot;
    record->screen_selector = selector;
    record->screen_input = input;

    sig_snap snap;
    int index = -1;
    int found = lookup_key(slot, selector, input, &index, &snap);
    if (found < 0) {
        atomic_fetch_add(&counters[CTR_SIG_READ], 1);
        return -1;
    }
    int kind = 0;
    int choose = 0;
    if (!found) {
        choose = 1;
        kind = 0;
    } else if ((snap.word & 3u) == SIG_PENDING) {
        atomic_fetch_add(&counters[CTR_PENDING], 1);
        return -1;
    } else if ((snap.word & 3u) == SIG_ANCHORED ||
               (snap.word & 3u) == SIG_UNANCHORED) {
        int anchored = (snap.word & 3u) == SIG_ANCHORED;
        int outcome = advance_existing(&snap, index, anchored, slot, selector,
                                       input, &choose);
        if (outcome < 0) {
            atomic_fetch_add(&counters[CTR_SIG_READ], 1);
            return -1;
        }
        if (!choose) {
            if (outcome == CTR_SCREENED) {
                static mach_timebase_info_data_t scale;
                static int scale_ready;
                if (!scale_ready) {
                    mach_timebase_info(&scale);
                    scale_ready = 1;
                }
                if (screen_n < 16384) {
                    screen_samples[screen_n++] =
                        (mach_absolute_time() - started) * scale.numer /
                        scale.denom;
                }
            }
            atomic_fetch_add(&counters[outcome], 1);
            return -1;
        }
        kind = anchored ? 1 : 2;
    } else {
        atomic_fetch_add(&counters[CTR_SIG_READ], 1);
        return -1;
    }

    if (atomic_load(&invariant_lost)) {
        atomic_fetch_add(&counters[CTR_CAS], 1);
        return -1;
    }
    int ticket = claim_buffer();
    if (ticket < 0) {
        atomic_fetch_add(&counters[CTR_BUFFER], 1);
        return -1;
    }
    int designated = 0;
    uint32_t mask = 0;
    int admitted = 0;
    int saw_room = 0;
    for (int attempt = 0; attempt < 8; attempt++) {
        if (atomic_load(&inject_admission)) {
            atomic_fetch_add(&admission_attempts, 1);
            continue;
        }
        uint64_t word = atomic_load(&admission_word);
        uint64_t retained, p_cov, p_div;
        uint32_t established, reserved;
        decode_adm(word, &retained, &p_cov, &p_div, &established, &reserved);
        uint32_t fresh =
            project_cells(slot, selector, input) & ~(established | reserved);
        uint64_t next_cov = p_cov;
        uint64_t next_div = p_div;
        uint32_t next_reserved = reserved;
        int next_designated = 0;
        if (fresh != 0) {
            next_cov++;
            next_reserved |= fresh;
            next_designated = 1;
        } else {
            next_div++;
        }
        unsigned lower = eu4_gfx_live_capture_test_lower_bound(
            CELL_BITS & ~(established | next_reserved));
        if (retained + next_cov + next_div + lower > 64) {
            continue;
        }
        saw_room = 1;
        atomic_fetch_add(&admission_attempts, 1);
        if (atomic_compare_exchange_weak(
                &admission_word, &word,
                pack_adm(retained, next_cov, next_div, established,
                         next_reserved))) {
            designated = next_designated;
            mask = fresh;
            admitted = 1;
            break;
        }
    }
    if (!admitted) {
        release_ticket((unsigned)ticket);
        int exhausted = saw_room || atomic_load(&inject_admission);
        atomic_fetch_add(&counters[exhausted ? CTR_CAS : CTR_CAPACITY], 1);
        return -1;
    }
    tickets[ticket].v2_admitted = 1;
    tickets[ticket].v2_designated = designated;
    tickets[ticket].v2_mask = mask;
    tickets[ticket].v2_kind = kind;

    uint64_t generation = 0;
    if (kind == 0) {
        barrier_wait(BAR_BEFORE);
        if (!take_gate()) {
            if (!rollback_reservation(designated, mask)) {
                atomic_fetch_add(&counters[CTR_CAS], 1);
            } else {
                atomic_fetch_add(&counters[CTR_CAS], 1);
            }
            release_ticket((unsigned)ticket);
            return -1;
        }
        barrier_wait(BAR_HOLD);
        atomic_store(&unique_scan, 1);
        static sig_snap table[SIG_N];
        int readable = 1;
        int duplicate = 0;
        for (unsigned i = 0; i < SIG_N; i++) {
            if (!sig_read(i, &table[i])) {
                readable = 0;
                break;
            }
            unsigned state = table[i].word & 3u;
            if (state != SIG_EMPTY && table[i].slot == slot &&
                table[i].selector == selector && table[i].input == input) {
                duplicate = 1;
            }
        }
        int empty = -1;
        if (readable && !duplicate) {
            unsigned start = sig_hash(slot, selector, input);
            for (unsigned step = 0; step < SIG_N; step++) {
                unsigned i = (start + step) % SIG_N;
                if ((table[i].word & 3u) == SIG_EMPTY) {
                    empty = (int)i;
                    break;
                }
            }
        }
        atomic_store(&unique_scan, 0);
        int published = 0;
        int capacity = 0;
        if (!readable) {
            published = 0;
        } else if (duplicate) {
            published = -1;
        } else if (atomic_load(&inject_bind)) {
            published = 0;
        } else if (empty >= 0) {
            published = sig_install((unsigned)empty, slot, selector, input,
                                    SIG_EMPTY, &generation);
            index = empty;
        } else {
            int claimed = 0;
            for (unsigned i = 0; i < SIG_N; i++) {
                if ((table[i].word & 3u) != SIG_UNANCHORED ||
                    (table[i].word & 8ull)) {
                    continue;
                }
                if (sig_install(i, slot, selector, input, SIG_UNANCHORED,
                                &generation)) {
                    index = (int)i;
                    claimed = 1;
                    break;
                }
            }
            published = claimed;
            capacity = !claimed;
        }
        atomic_store(&insert_gate, 0);
        if (published != 1) {
            int outcome = published < 0 ? CTR_KEY_INSERT
                                        : capacity ? CTR_TABLE
                                                   : CTR_CAS;
            if (!rollback_reservation(designated, mask)) {
                outcome = CTR_CAS;
            }
            atomic_fetch_add(&counters[outcome], 1);
            release_ticket((unsigned)ticket);
            return -1;
        }
        barrier_wait(BAR_AFTER);
    } else if (!set_inflight(index, snap.generation, kind == 2, &generation)) {
        if (!rollback_reservation(designated, mask)) {
            atomic_fetch_add(&counters[CTR_CAS], 1);
        } else {
            atomic_fetch_add(&counters[CTR_CAS], 1);
        }
        release_ticket((unsigned)ticket);
        return -1;
    }

    tickets[ticket].v2_bound = 1;
    tickets[ticket].v2_sig = index;
    tickets[ticket].v2_generation = generation;
    tickets[ticket].v2_counted = 1;
    atomic_fetch_add(&counters[kind == 0 ? CTR_FIRST : CTR_VERIFICATION], 1);
    record->diversity = 1;
    record->sig_index = index;
    record->coverage_designated = designated;
    record->reserved_mask = mask;
    record->bound_generation = generation;
    record->sample_kind = kind;
    record->ticket = (unsigned)ticket;
    barrier_wait(BAR_BIND);
    return ticket;
}

static int behavior_same(const behavior_row *row, const eu4_gfx_behavior *item) {
    return row->slot == item->slot && row->word_count == item->word_count &&
           row->selector_buffer == item->selector_buffer &&
           row->input_class == item->input_class && row->dirty == item->dirty &&
           row->buffer_state == item->buffer_state &&
           row->handle_state == item->handle_state && row->alias == item->alias &&
           row->pointer_role == item->pointer_role &&
           row->rax_class == item->rax_class && row->canary == item->canary;
}

static int retention_keep(const eu4_gfx_behavior *item, uint32_t input,
                          int designated) {
    for (unsigned i = 0; i < behavior_n; i++) {
        if (!behavior_same(&behaviors[i], item)) {
            continue;
        }
        if (designated) {
            return 1;
        }
        if (behaviors[i].count >= 2 || behaviors[i].input_value == input) {
            return 0;
        }
        behaviors[i].count = 2;
        return 1;
    }
    if (behavior_n >= 64) {
        return designated;
    }
    behavior_row *row = &behaviors[behavior_n++];
    memset(row, 0, sizeof(*row));
    row->used = 1;
    row->count = 1;
    row->slot = item->slot;
    row->word_count = item->word_count;
    row->input_value = input;
    row->selector_buffer = item->selector_buffer;
    row->input_class = item->input_class;
    row->dirty = item->dirty;
    row->buffer_state = item->buffer_state;
    row->handle_state = item->handle_state;
    row->alias = item->alias;
    row->pointer_role = item->pointer_role;
    row->rax_class = item->rax_class;
    row->canary = item->canary;
    return 1;
}

static int producer_match(const sig_entry *entry,
                          const eu4_gfx_capture_record *record,
                          const eu4_gfx_behavior *item) {
    if (!entry->anchor_valid || !item->ok) {
        return 0;
    }
    const eu4_gfx_capture_record *anchor = &entry->anchor;
    eu4_gfx_behavior anchored;
    if (eu4_gfx_set_constant_buffer_capture_assess(anchor, &anchored, NULL,
                                                   0) != 0 ||
        !anchored.ok) {
        return 0;
    }
    if (anchor->screen_input != record->screen_input ||
        anchor->buffer_path != record->buffer_path ||
        memcmp(anchor->handle, record->handle, sizeof(record->handle)) != 0 ||
        memcmp(anchor->post_handle, record->post_handle,
               sizeof(record->post_handle)) != 0 ||
        memcmp(anchor->ctx, record->ctx, sizeof(record->ctx)) != 0 ||
        memcmp(anchor->post_ctx, record->post_ctx, sizeof(record->post_ctx)) !=
            0 ||
        memcmp(anchor->canary_before, record->canary_before,
               sizeof(record->canary_before)) != 0 ||
        memcmp(anchor->post_canary_before, record->post_canary_before,
               sizeof(record->post_canary_before)) != 0 ||
        memcmp(anchor->canary_after, record->canary_after,
               sizeof(record->canary_after)) != 0 ||
        memcmp(anchor->post_canary_after, record->post_canary_after,
               sizeof(record->post_canary_after)) != 0) {
        return 0;
    }
    if (record->buffer_path &&
        (memcmp(anchor->buffer, record->buffer, sizeof(record->buffer)) != 0 ||
         memcmp(anchor->post_buffer, record->post_buffer,
                sizeof(record->post_buffer)) != 0)) {
        return 0;
    }
    return anchored.alias == item->alias &&
           anchored.pointer_role == item->pointer_role &&
           anchored.rax_class == item->rax_class &&
           anchored.canary == item->canary;
}

static int sha_file(const char *path, uint8_t out[CC_SHA256_DIGEST_LENGTH]) {
    FILE *file = fopen(path, "rb");
    if (file == NULL) {
        return 0;
    }
    CC_SHA256_CTX context;
    CC_SHA256_Init(&context);
    uint8_t buffer[4096];
    size_t got;
    while ((got = fread(buffer, 1, sizeof(buffer), file)) > 0) {
        CC_SHA256_Update(&context, buffer, (CC_LONG)got);
    }
    int bad = ferror(file);
    (void)fclose(file);
    if (bad) {
        return 0;
    }
    CC_SHA256_Final(out, &context);
    return 1;
}

static void sha_hex(const uint8_t in[CC_SHA256_DIGEST_LENGTH], char out[65]) {
    for (unsigned i = 0; i < CC_SHA256_DIGEST_LENGTH; i++) {
        (void)sprintf(out + (i * 2), "%02x", in[i]);
    }
    out[64] = '\0';
}

static int write_role(const eu4_gfx_capture_record *record, const char *suffix,
                      char *relative, size_t relative_length) {
    if (atomic_load(&inject_storage) && suffix[0] == '\0') {
        return 0;
    }
    unsigned sequence = atomic_fetch_add(&file_seq, 1);
    char final_path[LIVE_PATH];
    char partial_path[LIVE_PATH];
    int final_length =
        snprintf(final_path, sizeof(final_path), "%s/%s.%u%s.capture",
                 captures_dir, session_id, sequence, suffix);
    int partial_length =
        snprintf(partial_path, sizeof(partial_path), "%s.partial", final_path);
    int rel_length = snprintf(relative, relative_length, "captures/%s.%u%s.capture",
                              session_id, sequence, suffix);
    if (final_length < 0 || (size_t)final_length >= sizeof(final_path) ||
        partial_length < 0 || (size_t)partial_length >= sizeof(partial_path) ||
        rel_length < 0 || (size_t)rel_length >= relative_length) {
        return 0;
    }
    int descriptor = open(partial_path, O_WRONLY | O_CREAT | O_TRUNC, 0600);
    if (descriptor < 0) {
        return 0;
    }
    FILE *file = fdopen(descriptor, "w");
    if (file == NULL) {
        (void)close(descriptor);
        return 0;
    }
    bool ok = eu4_gfx_set_constant_buffer_capture_write(file, record,
                                                        &live_metadata);
    if (ok && (fflush(file) != 0 || fsync(fileno(file)) != 0)) {
        ok = false;
    }
    if (fclose(file) != 0) {
        ok = false;
    }
    if (!ok || rename(partial_path, final_path) != 0) {
        return 0;
    }
    return 1;
}

static void finish_signature(const eu4_gfx_capture_record *record, int kept) {
    unsigned index = (unsigned)record->sig_index;
    if (record->sample_kind == 0) {
        if (kept) {
            signatures[index].anchor = *record;
            signatures[index].anchor_valid = 1;
            (void)sig_write(index, SIG_ANCHORED, 1, 0);
        } else if (record->phase_b_failed || record->snapshot_mismatch) {
            (void)sig_write(index, SIG_EMPTY, 0, 1);
        } else {
            (void)sig_write(index, SIG_UNANCHORED, 1, 0);
        }
        return;
    }
    if (record->sample_kind == 2 && kept) {
        signatures[index].anchor = *record;
        signatures[index].anchor_valid = 1;
        (void)sig_write(index, SIG_ANCHORED, 1, 0);
        return;
    }
    clear_inflight(index);
}

static void note_predicate(const eu4_gfx_capture_record *record,
                           const char *missing, const eu4_gfx_behavior *item) {
    (void)record;
    if (predicate_label[0] == '\0') {
        return;
    }
    char text[256];
    text[0] = '\0';
    if (item->ok || missing[0] == '\0') {
        const char *selector =
            item->selector_buffer ? "length-from-buffer" : "fixed-length";
        const char *input = item->input_class == 0
                                ? "absent"
                                : item->input_class == 1 ? "zero" : "nonzero";
        const char *buffer = item->buffer_state == 0
                                 ? "absent"
                                 : item->buffer_state == 1 ? "unchanged"
                                                           : "changed";
        const char *handle = item->handle_state ? "changed" : "unchanged";
        const char *alias = item->alias == 0
                                ? "absent"
                                : item->alias == 1
                                      ? "handle"
                                      : item->alias == 2 ? "ctx" : "distinct";
        (void)snprintf(
            text, sizeof(text),
            "slot=%u selector=%s input=%s word_count=%u dirty=%u buffer=%s "
            "handle=%s alias=%s pointer=%s rax=%s canary=%s",
            item->slot, selector, input, item->word_count, item->dirty, buffer,
            handle, alias, item->pointer_role ? "other" : "rsi",
            item->rax_class ? "other" : "ctx",
            item->canary ? "changed" : "preserved");
    }
    printf("PREDICATE label=%s missing=%s behavior=%s\n", predicate_label,
           missing, text);
    fflush(stdout);
    predicate_label[0] = '\0';
}

static void classify_ticket(unsigned ticket) {
    eu4_gfx_capture_record *record = &tickets[ticket].record;
    int mismatch = 0;
    sig_snap snap;
    if (record->sig_index < 0 ||
        !sig_read((unsigned)record->sig_index, &snap) ||
        snap.generation != record->bound_generation) {
        mismatch = 1;
    }
    uint64_t retained, p_cov, p_div;
    uint32_t established, reserved;
    decode_adm(atomic_load(&admission_word), &retained, &p_cov, &p_div,
               &established, &reserved);
    int reservation_missing =
        record->coverage_designated
            ? (p_cov == 0 || (reserved & record->reserved_mask) !=
                                 record->reserved_mask)
            : p_div == 0;
    if (mismatch || reservation_missing) {
        atomic_store(&invariant_lost, 1);
        atomic_fetch_add(&counters[CTR_INVARIANT], 1);
        if (record->coverage_designated && p_cov > 0) {
            (void)rollback_reservation(1, record->reserved_mask);
        } else if (!record->coverage_designated && p_div > 0) {
            (void)rollback_reservation(0, 0);
        }
        if (!mismatch) {
            clear_inflight((unsigned)record->sig_index);
        }
        release_ticket(ticket);
        return;
    }
    if (record->phase_b_failed || record->snapshot_mismatch) {
        atomic_fetch_add(&counters[CTR_INCOMPLETE], 1);
        (void)rollback_reservation(record->coverage_designated,
                                   record->reserved_mask);
        finish_signature(record, 0);
        release_ticket(ticket);
        return;
    }
    char missing[512];
    eu4_gfx_behavior item;
    unsigned missing_count = eu4_gfx_set_constant_buffer_capture_assess(
        record, &item, missing, sizeof(missing));
    note_predicate(record, missing, &item);
    if (missing_count != 0) {
        atomic_fetch_add(&counters[CTR_INELIGIBLE], 1);
        if (ineligible_n < 11) {
            if (write_role(record, ".ineligible", ineligible_rel[ineligible_n],
                           sizeof(ineligible_rel[ineligible_n]))) {
                ineligible_n++;
                atomic_fetch_add(&ineligible_files, 1);
            }
        }
        (void)rollback_reservation(record->coverage_designated,
                                   record->reserved_mask);
        finish_signature(record, 0);
        release_ticket(ticket);
        return;
    }
    if (record->sample_kind == 1 &&
        producer_match(&signatures[record->sig_index], record, &item)) {
        atomic_fetch_add(&counters[CTR_PRODUCER], 1);
        int persisted = 0;
        if (audit_n < 8) {
            char relative[LIVE_PATH];
            if (write_role(record, ".audit", relative, sizeof(relative))) {
                char full[LIVE_PATH];
                (void)snprintf(full, sizeof(full), "%s/%s", run_dir, relative);
                uint8_t audit_digest[CC_SHA256_DIGEST_LENGTH];
                uint8_t reference_digest[CC_SHA256_DIGEST_LENGTH];
                char reference_full[LIVE_PATH];
                const char *reference =
                    signatures[record->sig_index].retained_rel;
                (void)snprintf(reference_full, sizeof(reference_full), "%s/%s",
                               run_dir, reference);
                if (sha_file(full, audit_digest) &&
                    sha_file(reference_full, reference_digest)) {
                    memcpy(audit_rel[audit_n], relative, sizeof(relative));
                    memcpy(audit_ref_rel[audit_n], reference,
                           strlen(reference) + 1);
                    memcpy(audit_sha[audit_n], audit_digest, sizeof(audit_digest));
                    memcpy(audit_ref_sha[audit_n], reference_digest,
                           sizeof(reference_digest));
                    audit_slot[audit_n] = record->screen_slot;
                    audit_selector[audit_n] = record->screen_selector;
                    audit_input[audit_n] = record->screen_input;
                    audit_n++;
                    persisted = 1;
                }
            }
        }
        atomic_fetch_add(&counters[persisted ? CTR_AUDIT : CTR_DISCARDED], 1);
        (void)rollback_reservation(record->coverage_designated,
                                   record->reserved_mask);
        finish_signature(record, 0);
        release_ticket(ticket);
        return;
    }
    if (!retention_keep(&item, record->screen_input,
                        record->coverage_designated)) {
        atomic_fetch_add(&counters[CTR_DISTINCT], 1);
        (void)rollback_reservation(record->coverage_designated,
                                   record->reserved_mask);
        finish_signature(record, 0);
        release_ticket(ticket);
        return;
    }
    char relative[LIVE_PATH];
    int wrote = retained_n < 64 &&
                write_role(record, "", relative, sizeof(relative));
    int committed =
        wrote && commit_retained(record->coverage_designated,
                                 record->reserved_mask,
                                 project_cells(record->screen_slot,
                                               record->screen_selector,
                                               record->screen_input));
    if (!committed) {
        atomic_fetch_add(&counters[CTR_STORAGE], 1);
        (void)rollback_reservation(record->coverage_designated,
                                   record->reserved_mask);
        record->phase_b_failed = 1;
        finish_signature(record, 0);
        release_ticket(ticket);
        return;
    }
    memcpy(retained_rel[retained_n], relative, sizeof(relative));
    memcpy(signatures[record->sig_index].retained_rel, relative,
           sizeof(relative));
    retained_n++;
    atomic_fetch_add(&counters[CTR_NOVEL], 1);
    finish_signature(record, 1);
    release_ticket(ticket);
}

static void account_terminal_reserved(unsigned ticket) {
    if (!tickets[ticket].v2_admitted) {
        return;
    }
    if (tickets[ticket].v2_counted) {
        atomic_fetch_add(&counters[CTR_INCOMPLETE], 1);
    }
    (void)rollback_reservation(tickets[ticket].v2_designated,
                               tickets[ticket].v2_mask);
    if (tickets[ticket].v2_bound && tickets[ticket].v2_sig >= 0) {
        eu4_gfx_capture_record stub = {0};
        stub.sig_index = tickets[ticket].v2_sig;
        stub.sample_kind = tickets[ticket].v2_kind;
        stub.phase_b_failed = 1;
        finish_signature(&stub, 0);
    }
}

static void account_undrained(unsigned ticket) {
    if (!tickets[ticket].v2_counted) {
        return;
    }
    atomic_fetch_add(&counters[CTR_UNDRAINED], 1);
    (void)rollback_reservation(tickets[ticket].v2_designated,
                               tickets[ticket].v2_mask);
    if (tickets[ticket].v2_bound && tickets[ticket].v2_sig >= 0) {
        clear_inflight((unsigned)tickets[ticket].v2_sig);
    }
}

static bool write_summary_v2(void) {
    if (atomic_load(&inject_skew)) {
        atomic_fetch_add(&counters[CTR_INTERCEPTED], 1);
    }
    char association_path[LIVE_PATH];
    (void)snprintf(association_path, sizeof(association_path),
                   "%s/audit-associations.json", run_dir);
    FILE *association = fopen(association_path, "w");
    if (association == NULL) {
        return false;
    }
    fputs("{\"schema\":\"eu4-gfx-live-audit-associations/v1\",\"entries\":[",
          association);
    for (unsigned i = 0; i < audit_n; i++) {
        char audit_hex[65];
        char reference_hex[65];
        sha_hex(audit_sha[i], audit_hex);
        sha_hex(audit_ref_sha[i], reference_hex);
        if (atomic_load(&inject_bad_hash)) {
            audit_hex[0] = audit_hex[0] == '0' ? '1' : '0';
        }
        fprintf(association,
                "%s{\"audit_path\":\"%s\",\"audit_sha256\":\"%s\","
                "\"reference_path\":\"%s\",\"reference_sha256\":\"%s\","
                "\"screening_key\":{\"slot\":%u,\"selector\":%u,"
                "\"input_value\":%u}}",
                i == 0 ? "" : ",", audit_rel[i], audit_hex, audit_ref_rel[i],
                reference_hex, audit_slot[i], audit_selector[i], audit_input[i]);
    }
    fputs("]}\n", association);
    if (fflush(association) != 0 || fsync(fileno(association)) != 0 ||
        fclose(association) != 0) {
        return false;
    }
    uint8_t association_digest[CC_SHA256_DIGEST_LENGTH];
    if (!sha_file(association_path, association_digest)) {
        return false;
    }
    char association_hex[65];
    sha_hex(association_digest, association_hex);
    if (atomic_load(&inject_bad_hash)) {
        uint8_t again[CC_SHA256_DIGEST_LENGTH];
        if (sha_file(association_path, again) &&
            memcmp(again, association_digest, sizeof(again)) == 0 &&
            inject_bad_hash) {
            /* The association file itself is intact; the row hash was wrong.
             * Structural admission fails closed below. */
        }
    }

    char manifest_path[LIVE_PATH];
    (void)snprintf(manifest_path, sizeof(manifest_path), "%s/manifest.json",
                   run_dir);
    FILE *manifest = fopen(manifest_path, "w");
    if (manifest == NULL) {
        return false;
    }
    fprintf(manifest,
            "{\"schema\":\"eu4-gfx-live-manifest/v2\",\"session\":\"%s\","
            "\"acquisition_policy\":\"acq-diversity/v1\",\"files\":[",
            session_id);
    int first = 1;
    for (unsigned i = 0; i < retained_n; i++) {
        char full[LIVE_PATH];
        uint8_t digest[CC_SHA256_DIGEST_LENGTH];
        char hex[65];
        (void)snprintf(full, sizeof(full), "%s/%s", run_dir, retained_rel[i]);
        if (!sha_file(full, digest)) {
            (void)fclose(manifest);
            return false;
        }
        sha_hex(digest, hex);
        fprintf(manifest, "%s{\"path\":\"%s\",\"role\":\"retained\",\"sha256\":\"%s\"}",
                first ? "" : ",", retained_rel[i], hex);
        first = 0;
    }
    for (unsigned i = 0; i < audit_n; i++) {
        char full[LIVE_PATH];
        uint8_t digest[CC_SHA256_DIGEST_LENGTH];
        char hex[65];
        (void)snprintf(full, sizeof(full), "%s/%s", run_dir, audit_rel[i]);
        if (!sha_file(full, digest)) {
            (void)fclose(manifest);
            return false;
        }
        sha_hex(digest, hex);
        fprintf(manifest, "%s{\"path\":\"%s\",\"role\":\"audit\",\"sha256\":\"%s\"}",
                first ? "" : ",", audit_rel[i], hex);
        first = 0;
    }
    for (unsigned i = 0; i < ineligible_n; i++) {
        char full[LIVE_PATH];
        uint8_t digest[CC_SHA256_DIGEST_LENGTH];
        char hex[65];
        (void)snprintf(full, sizeof(full), "%s/%s", run_dir,
                       ineligible_rel[i]);
        if (!sha_file(full, digest)) {
            (void)fclose(manifest);
            return false;
        }
        sha_hex(digest, hex);
        fprintf(manifest,
                "%s{\"path\":\"%s\",\"role\":\"ineligible\",\"sha256\":\"%s\"}",
                first ? "" : ",", ineligible_rel[i], hex);
        first = 0;
    }
    fprintf(manifest,
            "%s{\"path\":\"audit-associations.json\",\"role\":\"audit-associations\","
            "\"sha256\":\"%s\"}]}\n",
            first ? "" : ",", association_hex);
    if (fflush(manifest) != 0 || fsync(fileno(manifest)) != 0 ||
        fclose(manifest) != 0) {
        return false;
    }

    uint64_t retained, p_cov, p_div;
    uint32_t established, reserved;
    decode_adm(atomic_load(&admission_word), &retained, &p_cov, &p_div,
               &established, &reserved);
    unsigned lower = eu4_gfx_live_capture_test_lower_bound(
        CELL_BITS & ~(established | reserved));
    int broken = !identities_hold() || !admission_ok();
    int coverage_complete = established == CELL_BITS;
    int partial = quiescence_timeout ||
                  (closed_reason != NULL &&
                   strcmp(closed_reason, "deadline") == 0 && !coverage_complete);
    int structural = !broken && !atomic_load(&invariant_lost) &&
                     ctr(CTR_STORAGE) == 0 && ctr(CTR_UNDRAINED) == 0 &&
                     ctr(CTR_NOVEL) == retained && ctr(CTR_NOVEL) == retained_n &&
                     !atomic_load(&inject_bad_hash);
    char partial_path[LIVE_PATH];
    (void)snprintf(partial_path, sizeof(partial_path), "%s.partial", summary_path);
    FILE *file = fopen(partial_path, "w");
    if (file == NULL) {
        return false;
    }
    fprintf(file, "EU4-GFX-LIVE-SUMMARY v2\n");
    fprintf(file, "session=%s\n", session_id);
    fprintf(file, "pid=%d\n", (int)getpid());
    fprintf(file, "state=%s\n",
            broken ? "error" : partial ? "partial" : "sealed");
    fprintf(file, "closed_reason=%s\n",
            closed_reason != NULL ? closed_reason : "unknown");
    fprintf(file, "quiescence=%s\n", quiescence_timeout ? "timeout" : "ok");
    fprintf(file, "acquisition_policy=acq-diversity/v1\n");
    fprintf(file, "started_ns=%llu\n", (unsigned long long)started_ns);
    fprintf(file, "armed_ns=%llu\n", (unsigned long long)armed_ns);
    fprintf(file, "closed_ns=%llu\n", (unsigned long long)closed_ns);
    fprintf(file, "threads=%u\n", thread_count());
    fprintf(file, "intercepted=%llu\n", (unsigned long long)ctr(CTR_INTERCEPTED));
    uint64_t invalid = ctr(CTR_BUSY) + ctr(CTR_SLOT) + ctr(CTR_POINTER) +
                       ctr(CTR_ADDRESS) + ctr(CTR_THREAD) + ctr(CTR_CLOSED) +
                       ctr(CTR_READ_FAILED) + ctr(CTR_SIG_READ) +
                       ctr(CTR_KEY_INSERT) + ctr(CTR_TABLE) + ctr(CTR_BUFFER) +
                       ctr(CTR_CAPACITY) + ctr(CTR_CAS);
    uint64_t selected = ctr(CTR_FIRST) + ctr(CTR_VERIFICATION);
    fprintf(file, "invalid_refused=%llu\n", (unsigned long long)invalid);
    fprintf(file, "pending_contended=%llu\n",
            (unsigned long long)ctr(CTR_PENDING));
    fprintf(file, "verification_contended=%llu\n",
            (unsigned long long)ctr(CTR_VERIFY_CONTENDED));
    fprintf(file, "screened_repeat=%llu\n",
            (unsigned long long)ctr(CTR_SCREENED));
    fprintf(file, "selected=%llu\n", (unsigned long long)selected);
    fprintf(file, "busy=%llu\n", (unsigned long long)ctr(CTR_BUSY));
    fprintf(file, "slot=%llu\n", (unsigned long long)ctr(CTR_SLOT));
    fprintf(file, "pointer=%llu\n", (unsigned long long)ctr(CTR_POINTER));
    fprintf(file, "address=%llu\n", (unsigned long long)ctr(CTR_ADDRESS));
    fprintf(file, "thread_limit=%llu\n", (unsigned long long)ctr(CTR_THREAD));
    fprintf(file, "closed=%llu\n", (unsigned long long)ctr(CTR_CLOSED));
    fprintf(file, "read_failed=%llu\n",
            (unsigned long long)ctr(CTR_READ_FAILED));
    fprintf(file, "signature_read_contended=%llu\n",
            (unsigned long long)ctr(CTR_SIG_READ));
    fprintf(file, "key_insert_contended=%llu\n",
            (unsigned long long)ctr(CTR_KEY_INSERT));
    fprintf(file, "table_capacity_refused=%llu\n",
            (unsigned long long)ctr(CTR_TABLE));
    fprintf(file, "capture_buffer_refused=%llu\n",
            (unsigned long long)ctr(CTR_BUFFER));
    fprintf(file, "capacity_refused_selection=%llu\n",
            (unsigned long long)ctr(CTR_CAPACITY));
    fprintf(file, "cas_exhausted=%llu\n", (unsigned long long)ctr(CTR_CAS));
    fprintf(file, "first_selected=%llu\n", (unsigned long long)ctr(CTR_FIRST));
    fprintf(file, "verification_selected=%llu\n",
            (unsigned long long)ctr(CTR_VERIFICATION));
    fprintf(file, "novel_retained=%llu\n", (unsigned long long)ctr(CTR_NOVEL));
    fprintf(file, "producer_matched_repeat=%llu\n",
            (unsigned long long)ctr(CTR_PRODUCER));
    fprintf(file, "distinct_not_retained=%llu\n",
            (unsigned long long)ctr(CTR_DISTINCT));
    fprintf(file, "ineligible_not_established=%llu\n",
            (unsigned long long)ctr(CTR_INELIGIBLE));
    fprintf(file, "candidate_incomplete=%llu\n",
            (unsigned long long)ctr(CTR_INCOMPLETE));
    fprintf(file, "storage_failed=%llu\n",
            (unsigned long long)ctr(CTR_STORAGE));
    fprintf(file, "undrained=%llu\n", (unsigned long long)ctr(CTR_UNDRAINED));
    fprintf(file, "selected_invariant_failure=%llu\n",
            (unsigned long long)ctr(CTR_INVARIANT));
    fprintf(file, "producer_matched_discarded=%llu\n",
            (unsigned long long)ctr(CTR_DISCARDED));
    fprintf(file, "audit_persisted=%llu\n", (unsigned long long)ctr(CTR_AUDIT));
    fprintf(file, "ineligible_persisted=%llu\n",
            (unsigned long long)atomic_load(&ineligible_files));
    fprintf(file, "admission_invariant_lost=%d\n",
            atomic_load(&invariant_lost) ? 1 : 0);
    fprintf(file, "unanchored_reclaimed=%llu\n",
            (unsigned long long)ctr(CTR_RECLAIMED));
    fprintf(file, "r=%llu\n", (unsigned long long)retained);
    fprintf(file, "p_cov=%llu\n", (unsigned long long)p_cov);
    fprintf(file, "p_div=%llu\n", (unsigned long long)p_div);
    fprintf(file, "l=%u\n", lower);
    fprintf(file, "established=%u\n", established);
    fprintf(file, "written_retained=%u\n", retained_n);
    fprintf(file, "coverage_complete=%d\n", coverage_complete ? 1 : 0);
    fprintf(file, "structural=%s\n", structural ? "ok" : "failed");
    fprintf(file, "END\n");
    int ok = ferror(file) == 0 && fflush(file) == 0 && fsync(fileno(file)) == 0;
    if (fclose(file) != 0 || !ok || rename(partial_path, summary_path) != 0) {
        return false;
    }
    return !broken;
}

void eu4_gfx_live_capture_test_inject(const char *name) {
    if (strcmp(name, "clear") == 0) {
        atomic_store(&inject_admission, 0);
        atomic_store(&inject_rollback, 0);
        atomic_store(&inject_bind, 0);
        atomic_store(&inject_sig_read, 0);
        atomic_store(&inject_scan, 0);
        atomic_store(&inject_storage, 0);
        atomic_store(&inject_snapshot, 0);
        atomic_store(&inject_skew, 0);
        atomic_store(&inject_bad_hash, 0);
        return;
    }
    if (strcmp(name, "admission-cas") == 0) {
        atomic_store(&inject_admission, 1);
    } else if (strcmp(name, "rollback") == 0) {
        atomic_store(&inject_rollback, 1);
    } else if (strcmp(name, "bind") == 0) {
        atomic_store(&inject_bind, 1);
    } else if (strcmp(name, "sig-read") == 0) {
        atomic_store(&inject_sig_read, 1);
    } else if (strcmp(name, "scan-unreadable") == 0) {
        atomic_store(&inject_scan, 1);
    } else if (strcmp(name, "storage") == 0) {
        atomic_store(&inject_storage, 1);
    } else if (strcmp(name, "snapshot") == 0) {
        atomic_store(&inject_snapshot, 1);
    } else if (strcmp(name, "counter-skew") == 0) {
        atomic_store(&inject_skew, 1);
    } else if (strcmp(name, "bad-audit-hash") == 0) {
        atomic_store(&inject_bad_hash, 1);
    }
}

void eu4_gfx_live_capture_test_arm(const char *point) {
    int index = barrier_id(point);
    if (index < 0) {
        return;
    }
    atomic_store(&barriers[index].release, 0);
    atomic_store(&barriers[index].arrived, 0);
    atomic_store(&barriers[index].armed, 1);
}

int eu4_gfx_live_capture_test_wait_arrived(const char *point, unsigned ms) {
    int index = barrier_id(point);
    if (index < 0) {
        return 0;
    }
    for (unsigned elapsed = 0; elapsed < ms; elapsed++) {
        if (atomic_load(&barriers[index].arrived)) {
            return 1;
        }
        sleep_ms(1);
    }
    return atomic_load(&barriers[index].arrived);
}

void eu4_gfx_live_capture_test_release(const char *point) {
    int index = barrier_id(point);
    if (index < 0) {
        return;
    }
    atomic_store(&barriers[index].release, 1);
    atomic_store(&barriers[index].armed, 0);
}

int eu4_gfx_live_capture_test_settle(unsigned ms) {
    uint64_t deadline = now_ns() + (uint64_t)ms * 1000000ull;
    for (;;) {
        int busy = 0;
        for (unsigned i = 0; i < EU4_GFX_LIVE_CAPTURE_QUOTA; i++) {
            int state = atomic_load(&tickets[i].state);
            if (state == LIVE_TICKET_RESERVED || state == LIVE_TICKET_READY) {
                busy = 1;
                break;
            }
        }
        if (!busy) {
            return 1;
        }
        if (now_ns() >= deadline) {
            return 0;
        }
        sleep_ms(1);
    }
}

int eu4_gfx_live_capture_test_invariant_ok(void) {
    return admission_ok();
}

static void diag_field(const char *name, unsigned long long value) {
    printf(" %s=%llu", name, value);
}

void eu4_gfx_live_capture_test_diag(const char *scenario, const char *variant,
                                    const char *outcome) {
    uint64_t retained, p_cov, p_div;
    uint32_t established, reserved;
    decode_adm(atomic_load(&admission_word), &retained, &p_cov, &p_div,
               &established, &reserved);
    unsigned lower = eu4_gfx_live_capture_test_lower_bound(
        CELL_BITS & ~(established | reserved));
    uint64_t invalid = ctr(CTR_BUSY) + ctr(CTR_SLOT) + ctr(CTR_POINTER) +
                       ctr(CTR_ADDRESS) + ctr(CTR_THREAD) + ctr(CTR_CLOSED) +
                       ctr(CTR_READ_FAILED) + ctr(CTR_SIG_READ) +
                       ctr(CTR_KEY_INSERT) + ctr(CTR_TABLE) + ctr(CTR_BUFFER) +
                       ctr(CTR_CAPACITY) + ctr(CTR_CAS);
    uint64_t selected = ctr(CTR_FIRST) + ctr(CTR_VERIFICATION);
    printf("ACQ_DIAG scenario=%s", scenario);
    if (variant != NULL && variant[0] != '\0') {
        printf(" variant=%s", variant);
    }
    if (outcome != NULL && outcome[0] != '\0') {
        printf(" outcome=%s", outcome);
    }
    diag_field("intercepted", ctr(CTR_INTERCEPTED));
    diag_field("invalid_refused", invalid);
    diag_field("pending_contended", ctr(CTR_PENDING));
    diag_field("verification_contended", ctr(CTR_VERIFY_CONTENDED));
    diag_field("screened_repeat", ctr(CTR_SCREENED));
    diag_field("selected", selected);
    diag_field("busy", ctr(CTR_BUSY));
    diag_field("slot", ctr(CTR_SLOT));
    diag_field("pointer", ctr(CTR_POINTER));
    diag_field("address", ctr(CTR_ADDRESS));
    diag_field("thread_limit", ctr(CTR_THREAD));
    diag_field("closed", ctr(CTR_CLOSED));
    diag_field("read_failed", ctr(CTR_READ_FAILED));
    diag_field("signature_read_contended", ctr(CTR_SIG_READ));
    diag_field("key_insert_contended", ctr(CTR_KEY_INSERT));
    diag_field("table_capacity_refused", ctr(CTR_TABLE));
    diag_field("capture_buffer_refused", ctr(CTR_BUFFER));
    diag_field("capacity_refused_selection", ctr(CTR_CAPACITY));
    diag_field("cas_exhausted", ctr(CTR_CAS));
    diag_field("first_selected", ctr(CTR_FIRST));
    diag_field("verification_selected", ctr(CTR_VERIFICATION));
    diag_field("novel_retained", ctr(CTR_NOVEL));
    diag_field("producer_matched_repeat", ctr(CTR_PRODUCER));
    diag_field("distinct_not_retained", ctr(CTR_DISTINCT));
    diag_field("ineligible_not_established", ctr(CTR_INELIGIBLE));
    diag_field("candidate_incomplete", ctr(CTR_INCOMPLETE));
    diag_field("storage_failed", ctr(CTR_STORAGE));
    diag_field("undrained", ctr(CTR_UNDRAINED));
    diag_field("selected_invariant_failure", ctr(CTR_INVARIANT));
    diag_field("producer_matched_discarded", ctr(CTR_DISCARDED));
    diag_field("audit_persisted", ctr(CTR_AUDIT));
    diag_field("admission_invariant_lost",
               atomic_load(&invariant_lost) ? 1ull : 0ull);
    diag_field("unanchored_reclaimed", ctr(CTR_RECLAIMED));
    diag_field("p_cov", p_cov);
    diag_field("p_div", p_div);
    diag_field("r", retained);
    diag_field("l", lower);
    printf("\n");
    fflush(stdout);
}

uint64_t eu4_gfx_live_capture_test_counter(const char *name) {
    if (strcmp(name, "admission_attempts") == 0) {
        return atomic_load(&admission_attempts);
    }
    if (strcmp(name, "buffer_claims") == 0) {
        return atomic_load(&buffer_claims);
    }
    if (strcmp(name, "novel_retained") == 0) {
        return ctr(CTR_NOVEL);
    }
    if (strcmp(name, "screened_repeat") == 0) {
        return ctr(CTR_SCREENED);
    }
    if (strcmp(name, "selected") == 0) {
        return ctr(CTR_FIRST) + ctr(CTR_VERIFICATION);
    }
    return 0;
}

void eu4_gfx_live_capture_test_feasibility(uint64_t *retained, uint64_t *p_cov,
                                           uint64_t *p_div, uint64_t *lower,
                                           uint32_t *established) {
    uint32_t reserved;
    decode_adm(atomic_load(&admission_word), retained, p_cov, p_div,
               established, &reserved);
    *lower = eu4_gfx_live_capture_test_lower_bound(CELL_BITS &
                                                   ~(*established | reserved));
}

int eu4_gfx_live_capture_test_find(uint32_t slot, uint32_t selector,
                                   uint32_t input_value) {
    sig_snap snap;
    int index = -1;
    if (lookup_key(slot, selector, input_value, &index, &snap) != 1) {
        return -1;
    }
    return index;
}

int eu4_gfx_live_capture_test_signature(unsigned index, uint32_t *slot,
                                        uint32_t *selector, uint32_t *input,
                                        unsigned *state, int *reprobe,
                                        int *verify, uint64_t *encounter) {
    if (index >= SIG_N) {
        return 0;
    }
    sig_snap snap;
    if (!sig_read(index, &snap)) {
        return 0;
    }
    *slot = snap.slot;
    *selector = snap.selector;
    *input = snap.input;
    *state = snap.word & 3u;
    *reprobe = (snap.word & 8ull) ? 1 : 0;
    *verify = (snap.word & 4ull) ? 1 : 0;
    *encounter = snap.word >> 4;
    return 1;
}

int eu4_gfx_live_capture_test_raw(unsigned index, uint32_t *slot,
                                  uint32_t *selector, uint32_t *input,
                                  unsigned *state, uint64_t *generation) {
    if (index >= SIG_N) {
        return 0;
    }
    *generation = atomic_load(&signatures[index].generation);
    *slot = atomic_load(&signatures[index].key_slot);
    *selector = atomic_load(&signatures[index].key_selector);
    *input = atomic_load(&signatures[index].key_input);
    *state = (unsigned)(atomic_load(&signatures[index].word) & 3u);
    return 1;
}

int eu4_gfx_live_capture_test_bump_generation(unsigned index) {
    if (index >= SIG_N) {
        return 0;
    }
    uint64_t generation = atomic_load(&signatures[index].generation);
    if (generation & 1ull) {
        return 0;
    }
    uint64_t observed = generation;
    if (!atomic_compare_exchange_strong(&signatures[index].generation,
                                        &observed, generation + 1)) {
        return 0;
    }
    atomic_store(&signatures[index].generation, generation + 2);
    return 1;
}

void eu4_gfx_live_capture_test_drop_reservation(void) {
    for (;;) {
        uint64_t word = atomic_load(&admission_word);
        uint64_t retained, p_cov, p_div;
        uint32_t established, reserved;
        decode_adm(word, &retained, &p_cov, &p_div, &established, &reserved);
        (void)p_cov;
        (void)p_div;
        (void)reserved;
        if (atomic_compare_exchange_weak(
                &admission_word, &word,
                pack_adm(retained, 0, 0, established, 0))) {
            return;
        }
    }
}

void eu4_gfx_live_capture_test_synthetic_anchored_table(void) {
    /* synthetic state injection: 128 ANCHORED entries cannot be reached
     * under R + P_cov + P_div + L <= 64. */
    for (unsigned i = 0; i < SIG_N; i++) {
        atomic_store(&signatures[i].key_slot, 0);
        atomic_store(&signatures[i].key_selector, 1);
        atomic_store(&signatures[i].key_input, i + 1);
        atomic_store(&signatures[i].word, sig_pack(SIG_ANCHORED, 0, 0, 1));
        atomic_store(&signatures[i].generation, 2);
    }
}

void eu4_gfx_live_capture_test_predicate_label(const char *label) {
    if (label == NULL) {
        predicate_label[0] = '\0';
        return;
    }
    (void)snprintf(predicate_label, sizeof(predicate_label), "%s", label);
}

static int sample_less(const void *left, const void *right) {
    uint64_t a = *(const uint64_t *)left;
    uint64_t b = *(const uint64_t *)right;
    return (a > b) - (a < b);
}

int eu4_gfx_live_capture_test_screen_percentile(uint64_t *p50, uint64_t *p99,
                                                uint64_t *count) {
    if (screen_n == 0) {
        return 0;
    }
    qsort(screen_samples, screen_n, sizeof(screen_samples[0]), sample_less);
    *count = screen_n;
    *p50 = screen_samples[screen_n / 2];
    *p99 = screen_samples[(screen_n * 99) / 100];
    return 1;
}
