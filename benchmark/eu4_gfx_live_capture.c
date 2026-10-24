#include "eu4_gfx_live_capture.h"

#include "eu4_gfx_capture_detour.h"
#include "eu4_gfx_set_constant_buffer_capture.h"

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
    unsigned admitted = atomic_load_explicit(&next_ticket, memory_order_acquire);
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
            atomic_store_explicit(&tickets[i].state, LIVE_TICKET_UNDRAINED,
                                  memory_order_release);
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
    unsigned admitted = atomic_load_explicit(&next_ticket, memory_order_acquire);
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
    unsigned admitted = atomic_load_explicit(&next_ticket, memory_order_acquire);
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
    unsigned admitted = atomic_load_explicit(&next_ticket, memory_order_acquire);
    if (admitted > EU4_GFX_LIVE_CAPTURE_QUOTA) {
        admitted = EU4_GFX_LIVE_CAPTURE_QUOTA;
    }
    for (unsigned i = 0; i < admitted; i++) {
        int expected = LIVE_TICKET_RESERVED;
        (void)atomic_compare_exchange_strong_explicit(
            &tickets[i].state, &expected, LIVE_TICKET_INCOMPLETE,
            memory_order_acq_rel, memory_order_acquire);
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

    bool summary_ok = write_summary();
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
            if (atomic_load_explicit(&next_ticket, memory_order_acquire) >=
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
        sleep_ms(10);
    }
    close_session(reason);
    return NULL;
}

bool eu4_gfx_live_capture_start(void *site, const char *run_directory,
                                const char *control_path,
                                const char *capture_session_id,
                                unsigned seconds, bool restore) {
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
