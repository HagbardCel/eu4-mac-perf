#include "eu4_gfx_live_capture.h"
#include "eu4_gfx_set_constant_buffer_capture.h"
#include "gfx_capture_test_state.h"

#include <errno.h>
#include <fcntl.h>
#include <mach/mach.h>
#include <mach/mach_time.h>
#include <mach/mach_vm.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

#define CTX_SPAN 0x200u
#define BUFFER_LENGTH 4u

extern void gfx_set_constant_buffer_contract_stub(void);

gfx_capture_test_state gfx_capture_test_seed;
gfx_capture_test_state gfx_capture_test_capture;
uint64_t gfx_capture_test_pre_call_rsp;
uint64_t gfx_capture_test_capture_rsp;
void *gfx_capture_test_target;

typedef struct {
    _Alignas(16) uint8_t ctx[CTX_SPAN];
    uint64_t handle;
    uint8_t buffer[BUFFER_LENGTH];
} fixture;

typedef struct {
    fixture value;
    _Alignas(16) eu4_gfx_capture_frame frame;
    _Atomic int entered;
    _Atomic int release;
} held_capture;

typedef struct {
    fixture value;
    _Alignas(16) eu4_gfx_capture_frame frame;
    _Atomic int ready;
    _Atomic int release;
} held_refusal;

static int control_fd = -1;
static _Atomic uint64_t *control;
static size_t control_size;

static int fail(const char *message) {
    fprintf(stderr, "%s\n", message);
    return 1;
}

static void sleep_ms(unsigned milliseconds) {
    struct timespec value = {
        .tv_sec = 0,
        .tv_nsec = (long)milliseconds * 1000000L,
    };
    (void)nanosleep(&value, NULL);
}

static int open_control(const char *path) {
    long page = sysconf(_SC_PAGESIZE);
    if (page <= 0) {
        return fail("page size is unavailable");
    }
    control_size = (size_t)page;
    control_fd = open(path, O_RDWR | O_CREAT | O_TRUNC, 0600);
    if (control_fd < 0 || ftruncate(control_fd, (off_t)control_size) != 0) {
        return fail("cannot create control page");
    }
    void *mapping = mmap(NULL, control_size, PROT_READ | PROT_WRITE,
                         MAP_SHARED, control_fd, 0);
    if (mapping == MAP_FAILED) {
        return fail("cannot map control page");
    }
    control = (_Atomic uint64_t *)mapping;
    atomic_store(control, 0);
    return 0;
}

static void set_control(uint64_t value) {
    atomic_store_explicit(control, value, memory_order_release);
}

static void wait_flag(_Atomic int *flag) {
    while (!atomic_load_explicit(flag, memory_order_acquire)) {
        sleep_ms(1);
    }
}

static void open_gate(_Atomic int *gate) {
    atomic_store_explicit(gate, 1, memory_order_release);
}

static bool wait_done_marker(const char *run_dir, unsigned timeout_ms) {
    char path[1024];
    int length = snprintf(path, sizeof(path), "%s/session.done", run_dir);
    if (length < 0 || (size_t)length >= sizeof(path)) {
        return false;
    }
    for (unsigned elapsed = 0; elapsed < timeout_ms; elapsed += 10) {
        if (access(path, F_OK) == 0) {
            return true;
        }
        sleep_ms(10);
    }
    return access(path, F_OK) == 0;
}

static void fill_fixture(fixture *value) {
    memset(value, 0, sizeof(*value));
    for (size_t i = 0; i < CTX_SPAN; i++) {
        value->ctx[i] = (uint8_t)((i * 55u + 17u) & 0xffu);
    }
    value->handle = (uint64_t)(uintptr_t)value->ctx;
    uint32_t length = 13;
    memcpy(value->buffer, &length, sizeof(length));
}

static void frame_for(eu4_gfx_capture_frame *frame, fixture *value,
                      unsigned slot, int buffer_path) {
    memset(frame, 0, sizeof(*frame));
    frame->rdi = (uint64_t)(uintptr_t)&value->handle;
    frame->rsi = (uint64_t)(uintptr_t)value->buffer;
    frame->rcx = 0xdeadbeef00000000ull | slot;
    frame->r9 = 0xfeedface00000000ull |
                (buffer_path ? 0xffffffffu : 13u);
    frame->rax = (uint64_t)(uintptr_t)value->ctx;
}

static void direct_capture(fixture *value, unsigned slot, int buffer_path) {
    _Alignas(16) eu4_gfx_capture_frame frame;
    frame_for(&frame, value, slot, buffer_path);
    eu4_gfx_set_constant_buffer_capture_entry(&frame);
    eu4_gfx_set_constant_buffer_capture_exit(&frame);
}

static void direct_failure(fixture *value, uintptr_t handle_address,
                           uint64_t ctx_address) {
    _Alignas(16) eu4_gfx_capture_frame frame;
    memset(&frame, 0, sizeof(frame));
    frame.rdi = (uint64_t)handle_address;
    frame.rsi = (uint64_t)(uintptr_t)value->buffer;
    frame.rcx = 0xdeadbeef00000000ull | 3u;
    frame.r9 = 0xfeedface00000000ull | 13u;
    frame.rax = ctx_address;
    eu4_gfx_set_constant_buffer_capture_entry(&frame);
    eu4_gfx_set_constant_buffer_capture_exit(&frame);
}

static void *worker(void *argument) {
    fixture *value = (fixture *)argument;
    direct_capture(value, 3, 0);
    return NULL;
}

static void *held_capture_worker(void *argument) {
    held_capture *held = (held_capture *)argument;
    eu4_gfx_set_constant_buffer_capture_entry(&held->frame);
    atomic_store_explicit(&held->entered, 1, memory_order_release);
    wait_flag(&held->release);
    eu4_gfx_set_constant_buffer_capture_exit(&held->frame);
    return NULL;
}

static void *thread_fill_worker(void *argument) {
    held_refusal *held = (held_refusal *)argument;
    eu4_gfx_set_constant_buffer_capture_entry(&held->frame);
    eu4_gfx_set_constant_buffer_capture_exit(&held->frame);
    atomic_store_explicit(&held->ready, 1, memory_order_release);
    wait_flag(&held->release);
    return NULL;
}

static int run_mode(const char *mode, const char *run_dir,
                    const char *control_path) {
    if (open_control(control_path) != 0) {
        return 1;
    }
    if (!eu4_gfx_live_capture_start_v1(
            (void *)(uintptr_t)gfx_set_constant_buffer_contract_stub, run_dir,
            control_path, "live-test", 5, true)) {
        return fail("live capture start failed");
    }

    if (strcmp(mode, "accept") == 0) {
        set_control(1);
        sleep_ms(100);
        fixture first;
        fixture second;
        fill_fixture(&first);
        fill_fixture(&second);
        direct_capture(&first, 2, 1);
        direct_capture(&second, 3, 0);
        direct_capture(&first, 6, 0);
        direct_capture(&second, 3, 0);
        _Alignas(16) eu4_gfx_capture_frame null_frame;
        memset(&null_frame, 0, sizeof(null_frame));
        null_frame.rcx = 3;
        null_frame.r9 = 13;
        eu4_gfx_set_constant_buffer_capture_entry(&null_frame);
        eu4_gfx_set_constant_buffer_capture_exit(&null_frame);

        fixture outer;
        fixture inner;
        fill_fixture(&outer);
        fill_fixture(&inner);
        _Alignas(16) eu4_gfx_capture_frame outer_frame;
        _Alignas(16) eu4_gfx_capture_frame inner_frame;
        frame_for(&outer_frame, &outer, 2, 1);
        frame_for(&inner_frame, &inner, 3, 0);
        eu4_gfx_set_constant_buffer_capture_entry(&outer_frame);
        eu4_gfx_set_constant_buffer_capture_entry(&inner_frame);
        eu4_gfx_set_constant_buffer_capture_exit(&inner_frame);
        eu4_gfx_set_constant_buffer_capture_exit(&outer_frame);
    } else if (strcmp(mode, "hook") == 0) {
        set_control(1);
        sleep_ms(100);
        fixture values[2];
        fill_fixture(&values[0]);
        fill_fixture(&values[1]);
        for (unsigned i = 0; i < 2; i++) {
            memset(&gfx_capture_test_seed, 0, sizeof(gfx_capture_test_seed));
            gfx_capture_test_seed.rdi =
                (uint64_t)(uintptr_t)&values[i].handle;
            gfx_capture_test_seed.rsi =
                (uint64_t)(uintptr_t)values[i].buffer;
            gfx_capture_test_seed.rcx =
                0xdeadbeef00000000ull | (i == 0 ? 2u : 3u);
            gfx_capture_test_seed.r9 =
                0xfeedface00000000ull | (i == 0 ? 0xffffffffu : 13u);
            gfx_capture_test_seed.rax =
                (uint64_t)(uintptr_t)values[i].ctx;
            gfx_capture_test_target =
                (void *)(uintptr_t)gfx_set_constant_buffer_contract_stub;
            gfx_capture_test_call_target();
        }
    } else if (strcmp(mode, "guard") == 0) {
        set_control(1);
        sleep_ms(100);
        long page = sysconf(_SC_PAGESIZE);
        uint8_t *guard = mmap(NULL, (size_t)page, PROT_NONE,
                              MAP_PRIVATE | MAP_ANON, -1, 0);
        if (guard == MAP_FAILED) {
            return fail("cannot map guard page");
        }
        fixture value;
        fill_fixture(&value);
        value.handle = (uint64_t)(uintptr_t)guard;
        direct_failure(&value, (uintptr_t)&value.handle,
                       (uint64_t)(uintptr_t)value.ctx);
        direct_failure(&value, (uintptr_t)guard,
                       (uint64_t)(uintptr_t)value.ctx);
        (void)munmap(guard, (size_t)page);
    } else if (strcmp(mode, "nested") == 0) {
        set_control(1);
        sleep_ms(100);
        fixture first;
        fixture second;
        fill_fixture(&first);
        fill_fixture(&second);
        _Alignas(16) eu4_gfx_capture_frame accepted;
        _Alignas(16) eu4_gfx_capture_frame refused;
        frame_for(&accepted, &first, 2, 1);
        frame_for(&refused, &second, 3, 0);
        eu4_gfx_set_constant_buffer_capture_entry(&accepted);
        set_control(2);
        sleep_ms(100);
        eu4_gfx_set_constant_buffer_capture_entry(&refused);
        eu4_gfx_set_constant_buffer_capture_exit(&refused);
        eu4_gfx_set_constant_buffer_capture_exit(&accepted);
    } else if (strcmp(mode, "quota") == 0) {
        set_control(1);
        sleep_ms(100);
        fixture value;
        fill_fixture(&value);
        for (unsigned i = 0; i < 64; i++) {
            direct_capture(&value, 3, 0);
        }
    } else if (strcmp(mode, "threads") == 0) {
        set_control(1);
        sleep_ms(100);
        fixture values[2];
        pthread_t workers[2];
        fill_fixture(&values[0]);
        fill_fixture(&values[1]);
        for (unsigned i = 0; i < 2; i++) {
            if (pthread_create(&workers[i], NULL, worker, &values[i]) != 0) {
                return fail("cannot create worker");
            }
        }
        for (unsigned i = 0; i < 2; i++) {
            (void)pthread_join(workers[i], NULL);
        }
    } else if (strcmp(mode, "extent-cross") == 0) {
        set_control(1);
        sleep_ms(100);
        long page = sysconf(_SC_PAGESIZE);
        if (page <= 0) {
            return fail("page size is unavailable");
        }
        uint8_t *pages = mmap(NULL, (size_t)page * 2,
                              PROT_READ | PROT_WRITE,
                              MAP_PRIVATE | MAP_ANON, -1, 0);
        if (pages == MAP_FAILED) {
            return fail("cannot map crossing extent");
        }
        if (mprotect(pages + page, (size_t)page, PROT_NONE) != 0) {
            return fail("cannot protect crossing extent");
        }
        uint8_t *ctx = pages + page - 0x100;
        memset(ctx, 0x5a, 0x100);
        fixture value;
        fill_fixture(&value);
        value.handle = (uint64_t)(uintptr_t)ctx;
        direct_failure(&value, (uintptr_t)&value.handle,
                       (uint64_t)(uintptr_t)ctx);
        (void)munmap(pages, (size_t)page * 2);
    } else if (strcmp(mode, "buffer-guard") == 0) {
        set_control(1);
        sleep_ms(100);
        long page = sysconf(_SC_PAGESIZE);
        if (page <= 0) {
            return fail("page size is unavailable");
        }
        uint8_t *guard = mmap(NULL, (size_t)page, PROT_NONE,
                              MAP_PRIVATE | MAP_ANON, -1, 0);
        if (guard == MAP_FAILED) {
            return fail("cannot map buffer guard");
        }
        fixture value;
        fill_fixture(&value);
        _Alignas(16) eu4_gfx_capture_frame frame;
        memset(&frame, 0, sizeof(frame));
        frame.rdi = (uint64_t)(uintptr_t)&value.handle;
        frame.rsi = (uint64_t)(uintptr_t)guard;
        frame.rcx = 0xdeadbeef00000000ull | 3u;
        frame.r9 = 0xfeedface00000000ull | 0xffffffffu;
        frame.rax = (uint64_t)(uintptr_t)value.ctx;
        eu4_gfx_set_constant_buffer_capture_entry(&frame);
        eu4_gfx_set_constant_buffer_capture_exit(&frame);
        (void)munmap(guard, (size_t)page);
    } else if (strcmp(mode, "exit-guard") == 0) {
        set_control(1);
        sleep_ms(100);
        long page = sysconf(_SC_PAGESIZE);
        if (page <= 0) {
            return fail("page size is unavailable");
        }
        uint8_t *ctx = mmap(NULL, (size_t)page, PROT_READ | PROT_WRITE,
                            MAP_PRIVATE | MAP_ANON, -1, 0);
        if (ctx == MAP_FAILED) {
            return fail("cannot map exit context");
        }
        memset(ctx, 0x5a, (size_t)page);
        fixture value;
        fill_fixture(&value);
        value.handle = (uint64_t)(uintptr_t)ctx;
        _Alignas(16) eu4_gfx_capture_frame frame;
        frame_for(&frame, &value, 3, 0);
        eu4_gfx_set_constant_buffer_capture_entry(&frame);
        if (mprotect(ctx, (size_t)page, PROT_NONE) != 0) {
            return fail("cannot protect exit context");
        }
        eu4_gfx_set_constant_buffer_capture_exit(&frame);
        (void)munmap(ctx, (size_t)page);
    } else if (strcmp(mode, "thread-full") == 0) {
        set_control(1);
        sleep_ms(100);
        enum { FILLERS = 63 };
        held_refusal *fillers = calloc(FILLERS, sizeof(*fillers));
        if (fillers == NULL) {
            return fail("cannot allocate thread fillers");
        }
        pthread_t threads[FILLERS];
        for (unsigned i = 0; i < FILLERS; i++) {
            fill_fixture(&fillers[i].value);
            frame_for(&fillers[i].frame, &fillers[i].value, 7, 0);
            if (pthread_create(&threads[i], NULL, thread_fill_worker,
                               &fillers[i]) != 0) {
                return fail("cannot create thread filler");
            }
        }
        for (unsigned i = 0; i < FILLERS; i++) {
            wait_flag(&fillers[i].ready);
        }
        fixture extra;
        fill_fixture(&extra);
        pthread_t refused;
        if (pthread_create(&refused, NULL, worker, &extra) != 0) {
            return fail("cannot create refused worker");
        }
        (void)pthread_join(refused, NULL);
        for (unsigned i = 0; i < FILLERS; i++) {
            open_gate(&fillers[i].release);
        }
        for (unsigned i = 0; i < FILLERS; i++) {
            (void)pthread_join(threads[i], NULL);
        }
        free(fillers);
    } else if (strcmp(mode, "quota-overflow") == 0) {
        set_control(1);
        sleep_ms(100);
        held_capture *held = calloc(1, sizeof(*held));
        if (held == NULL) {
            return fail("cannot allocate held capture");
        }
        fill_fixture(&held->value);
        frame_for(&held->frame, &held->value, 3, 0);
        pthread_t producer;
        if (pthread_create(&producer, NULL, held_capture_worker, held) != 0) {
            return fail("cannot create held capture");
        }
        wait_flag(&held->entered);
        fixture value;
        fill_fixture(&value);
        for (unsigned i = 0; i < 63; i++) {
            direct_capture(&value, 3, 0);
        }
        direct_capture(&value, 3, 0);
        open_gate(&held->release);
        (void)pthread_join(producer, NULL);
        free(held);
    } else if (strcmp(mode, "late-publish") == 0) {
        set_control(1);
        sleep_ms(100);
        held_capture *held = calloc(1, sizeof(*held));
        if (held == NULL) {
            return fail("cannot allocate held capture");
        }
        fill_fixture(&held->value);
        frame_for(&held->frame, &held->value, 3, 0);
        pthread_t producer;
        if (pthread_create(&producer, NULL, held_capture_worker, held) != 0) {
            return fail("cannot create held capture");
        }
        wait_flag(&held->entered);
        set_control(2);
        bool sealed = wait_done_marker(run_dir, 12000);
        open_gate(&held->release);
        (void)pthread_join(producer, NULL);
        free(held);
        if (!sealed) {
            return fail("late publish session did not seal");
        }
    } else {
        return fail("unknown mode");
    }

    set_control(2);
    sleep_ms(50);
    eu4_gfx_live_capture_stop();
    (void)close(control_fd);
    return 0;
}

static void fill_qualified(fixture *value) {
    fill_fixture(value);
}

static void capture_call(fixture *value, unsigned slot, int buffer_path,
                         uint32_t selector, int mutate_pre, int override_post,
                         uint32_t post_word, int dirty) {
    fill_qualified(value);
    if (buffer_path) {
        memcpy(value->buffer, &selector, sizeof(selector));
    }
    if (mutate_pre) {
        uint32_t pre_word = 0x11111111u;
        memcpy(value->ctx + 0xc8 + slot * 16u, &pre_word, sizeof(pre_word));
    }
    _Alignas(16) eu4_gfx_capture_frame frame;
    frame_for(&frame, value, slot, buffer_path);
    if (!buffer_path) {
        frame.r9 = (frame.r9 & 0xffffffff00000000ull) | selector;
    }
    eu4_gfx_set_constant_buffer_capture_entry(&frame);
    uint32_t words;
    if (buffer_path) {
        uint32_t dword;
        memcpy(&dword, value->buffer, sizeof(dword));
        words = (dword + 3u) >> 2;
    } else {
        int32_t r9 = (int32_t)selector;
        int32_t edx = r9 + 3;
        r9 += 6;
        if (edx >= 0) {
            r9 = edx;
        }
        words = (uint32_t)(r9 >> 2);
    }
    memcpy(value->ctx + 0xc8 + (size_t)slot * 16u, &words, sizeof(words));
    uint64_t rsi = (uint64_t)(uintptr_t)value->buffer;
    memcpy(value->ctx + 0xd0 + (size_t)slot * 16u, &rsi, sizeof(rsi));
    value->ctx[0x128] = 1;
    if (override_post) {
        memcpy(value->ctx + 0xc8 + (size_t)slot * 16u, &post_word,
               sizeof(post_word));
    }
    if (!dirty) {
        value->ctx[0x128] = 0;
    }
    eu4_gfx_set_constant_buffer_capture_exit(&frame);
}

static int settle_ok(void);

static int fill_ineligible(fixture *value, unsigned count) {
    for (unsigned i = 0; i < count; i++) {
        capture_call(value, i % 6u, 1, i + 1u, 0, 0, 0, 0);
        if (settle_ok() != 0) {
            return 1;
        }
    }
    return 0;
}

static int wait_published(uint32_t slot, uint32_t selector, uint32_t input,
                          int *index) {
    for (int attempt = 0; attempt < 3000; attempt++) {
        int found = eu4_gfx_live_capture_test_find(slot, selector, input);
        if (found >= 0) {
            *index = found;
            return 0;
        }
        sleep_ms(1);
    }
    return fail("published key did not appear");
}

static int settle_ok(void) {
    if (!eu4_gfx_live_capture_test_settle(5000) ||
        !eu4_gfx_live_capture_test_invariant_ok()) {
        return fail("diversity settle failed");
    }
    return 0;
}

static int s03(fixture *value, int mutate_pre, int override_post,
               uint32_t post_word, int dirty) {
    capture_call(value, 2, 1, 13, mutate_pre, override_post, post_word, dirty);
    return 0;
}

static int sample_point(unsigned n) {
    return n == 1 || n == 2 || (n > 2 && ((n - 1) % 16u) == 0);
}

typedef struct {
    fixture value;
    unsigned slot;
    int buffer_path;
    uint32_t selector;
    int mutate_pre;
    int override_post;
    uint32_t post_word;
    int dirty;
} acq_job;

static void *acq_worker(void *argument) {
    acq_job *job = argument;
    capture_call(&job->value, job->slot, job->buffer_path, job->selector,
                 job->mutate_pre, job->override_post, job->post_word,
                 job->dirty);
    return NULL;
}

static uint64_t ticks_to_ns(uint64_t ticks) {
    static mach_timebase_info_data_t scale;
    static int ready;
    if (!ready) {
        mach_timebase_info(&scale);
        ready = 1;
    }
    return ticks * scale.numer / scale.denom;
}

static int cmp_u64(const void *left, const void *right) {
    uint64_t a = *(const uint64_t *)left;
    uint64_t b = *(const uint64_t *)right;
    return (a > b) - (a < b);
}

static void percentile(uint64_t *samples, unsigned count, uint64_t *p50,
                       uint64_t *p99) {
    qsort(samples, count, sizeof(samples[0]), cmp_u64);
    *p50 = samples[count / 2];
    *p99 = samples[(count * 99u) / 100u];
}

static int run_diversity(const char *mode, const char *run_dir,
                         const char *control_path) {
    unsigned deadline = 30;
    if (strcmp(mode, "acq-overhead") == 0 || strcmp(mode, "overhead") == 0) {
        deadline = 120;
    }
    if (strcmp(mode, "acq-deadline") == 0) {
        deadline = 1;
    }
    if (open_control(control_path) != 0) {
        return 1;
    }
    int v1_overhead = strcmp(mode, "acq-overhead") == 0 ||
                      strcmp(mode, "overhead") == 0;
    if (v1_overhead) {
        /* Measured below in its own sessions. */
    }
    if (!v1_overhead &&
        !eu4_gfx_live_capture_start((void *)(uintptr_t)gfx_set_constant_buffer_contract_stub,
                                    run_dir, control_path, "live-test", deadline,
                                    true)) {
        return fail("diversity start failed");
    }
    if (!v1_overhead) {
        set_control(1);
        sleep_ms(30);
    }
    fixture value;
    int failed = 0;

    if (strcmp(mode, "acq-identical") == 0) {
        for (unsigned n = 1; n <= 100; n++) {
            s03(&value, 0, 0, 0, 1);
            if (sample_point(n) && settle_ok() != 0) {
                return 1;
            }
        }
        eu4_gfx_live_capture_test_diag("identical_calls_100", "", "");
    } else if (strcmp(mode, "acq-dword") == 0) {
        s03(&value, 0, 0, 0, 1);
        if (settle_ok() != 0) {
            return 1;
        }
        capture_call(&value, 2, 1, 1, 0, 0, 0, 1);
        if (settle_ok() != 0) {
            return 1;
        }
        eu4_gfx_live_capture_test_diag("same_slot_different_dword", "", "");
    } else if (strcmp(mode, "acq-sched-cas") == 0) {
        s03(&value, 0, 0, 0, 1);
        if (settle_ok() != 0) {
            return 1;
        }
        eu4_gfx_live_capture_test_inject("admission-cas");
        s03(&value, 0, 0, 0, 1);
        eu4_gfx_live_capture_test_inject("clear");
        int sample = eu4_gfx_live_capture_test_find(2, 0xffffffffu, 13);
        uint64_t encounter = 0;
        unsigned state = 0;
        int reprobe = 0;
        int verify = 0;
        uint32_t found_slot = 0;
        uint32_t found_selector = 0;
        uint32_t found_input = 0;
        if (sample < 0 ||
            !eu4_gfx_live_capture_test_signature(
                (unsigned)sample, &found_slot, &found_selector, &found_input,
                &state, &reprobe, &verify, &encounter) ||
            encounter != 2 ||
            eu4_gfx_live_capture_test_counter("verification_selected") != 0) {
            return fail("refused sample did not keep the advanced encounter");
        }
        eu4_gfx_live_capture_test_diag(
            "scheduled_sample_phase_b_refused", "cas_exhausted", "cas_exhausted");
    } else if (strcmp(mode, "acq-unanchored-pcov") == 0) {
        capture_call(&value, 4, 1, 13, 0, 0, 0, 0);
        if (settle_ok() != 0) {
            return 1;
        }
        uint64_t before_r = 0, before_cov = 0, before_div = 0, before_l = 0;
        uint32_t before_est = 0;
        eu4_gfx_live_capture_test_feasibility(&before_r, &before_cov, &before_div,
                                              &before_l, &before_est);
        if (before_est != 0 || before_r != 0) {
            return fail("ineligible established a cell");
        }
        eu4_gfx_live_capture_test_arm("after-bind");
        acq_job job = {.slot = 4, .buffer_path = 1, .selector = 13, .dirty = 1};
        pthread_t thread;
        if (pthread_create(&thread, NULL, acq_worker, &job) != 0) {
            return fail("cannot start reprobe");
        }
        if (!eu4_gfx_live_capture_test_wait_arrived("after-bind", 2000)) {
            return fail("reprobe did not bind");
        }
        eu4_gfx_live_capture_test_feasibility(&before_r, &before_cov, &before_div,
                                              &before_l, &before_est);
        if (before_cov != 1 || before_est != 0) {
            return fail("reprobe did not reserve P_cov");
        }
        eu4_gfx_live_capture_test_release("after-bind");
        (void)pthread_join(thread, NULL);
        if (settle_ok() != 0) {
            return 1;
        }
        eu4_gfx_live_capture_test_feasibility(&before_r, &before_cov, &before_div,
                                              &before_l, &before_est);
        if (before_r != 1 || (before_est & (1u << 4)) == 0) {
            return fail("qualified retain did not establish the cell");
        }
        eu4_gfx_live_capture_test_diag("unanchored_missing_cell_pcov", "", "");
    } else if (strcmp(mode, "acq-threads-holds") == 0) {
        eu4_gfx_live_capture_test_arm("hold-gate");
        acq_job first = {.slot = 2, .buffer_path = 1, .selector = 13, .dirty = 1};
        acq_job second = first;
        pthread_t alpha;
        pthread_t beta;
        if (pthread_create(&alpha, NULL, acq_worker, &first) != 0) {
            return fail("cannot start holder");
        }
        if (!eu4_gfx_live_capture_test_wait_arrived("hold-gate", 2000)) {
            return fail("holder did not take the gate");
        }
        if (pthread_create(&beta, NULL, acq_worker, &second) != 0) {
            return fail("cannot start loser");
        }
        (void)pthread_join(beta, NULL);
        eu4_gfx_live_capture_test_release("hold-gate");
        (void)pthread_join(alpha, NULL);
        if (settle_ok() != 0) {
            return 1;
        }
        eu4_gfx_live_capture_test_diag("two_threads_same_unseen_key", "holds_gate",
                                       "cas_exhausted");
    } else if (strcmp(mode, "acq-threads-pending") == 0) {
        eu4_gfx_live_capture_test_arm("after-pending");
        acq_job first = {.slot = 2, .buffer_path = 1, .selector = 13, .dirty = 1};
        acq_job second = first;
        pthread_t alpha;
        pthread_t beta;
        if (pthread_create(&alpha, NULL, acq_worker, &first) != 0) {
            return fail("cannot start owner");
        }
        if (!eu4_gfx_live_capture_test_wait_arrived("after-pending", 2000)) {
            return fail("owner did not publish");
        }
        if (pthread_create(&beta, NULL, acq_worker, &second) != 0) {
            return fail("cannot start waiter");
        }
        (void)pthread_join(beta, NULL);
        eu4_gfx_live_capture_test_release("after-pending");
        (void)pthread_join(alpha, NULL);
        if (settle_ok() != 0) {
            return 1;
        }
        eu4_gfx_live_capture_test_diag("two_threads_same_unseen_key", "sees_pending",
                                       "pending_contended");
    } else if (strcmp(mode, "acq-threads-insert") == 0) {
        eu4_gfx_live_capture_test_arm("before-gate");
        eu4_gfx_live_capture_test_arm("after-pending");
        acq_job first = {.slot = 2, .buffer_path = 1, .selector = 13, .dirty = 1};
        acq_job second = first;
        pthread_t alpha;
        pthread_t beta;
        if (pthread_create(&beta, NULL, acq_worker, &second) != 0) {
            return fail("cannot start scanner");
        }
        if (!eu4_gfx_live_capture_test_wait_arrived("before-gate", 2000)) {
            return fail("scanner did not reach the gate");
        }
        if (pthread_create(&alpha, NULL, acq_worker, &first) != 0) {
            return fail("cannot start owner");
        }
        if (!eu4_gfx_live_capture_test_wait_arrived("after-pending", 2000)) {
            return fail("owner did not publish");
        }
        eu4_gfx_live_capture_test_release("before-gate");
        (void)pthread_join(beta, NULL);
        eu4_gfx_live_capture_test_release("after-pending");
        (void)pthread_join(alpha, NULL);
        if (settle_ok() != 0) {
            return 1;
        }
        eu4_gfx_live_capture_test_diag("two_threads_same_unseen_key",
                                       "key_insert_contended",
                                       "key_insert_contended");
    } else if (strcmp(mode, "acq-anchor") == 0) {
        s03(&value, 0, 0, 0, 1);
        if (settle_ok() != 0) {
            return 1;
        }
        s03(&value, 1, 0, 0, 1);
        if (settle_ok() != 0) {
            return 1;
        }
        eu4_gfx_live_capture_test_diag("anchor_survives_nonretained", "encounter_2",
                                       "distinct_not_retained");
        for (unsigned n = 3; n <= 16; n++) {
            s03(&value, 0, 0, 0, 1);
        }
        s03(&value, 0, 0, 0, 0);
        if (settle_ok() != 0) {
            return 1;
        }
        eu4_gfx_live_capture_test_diag("anchor_survives_nonretained", "encounter_17",
                                       "ineligible_not_established");
        for (unsigned n = 18; n <= 32; n++) {
            s03(&value, 0, 0, 0, 1);
        }
        eu4_gfx_live_capture_test_inject("storage");
        s03(&value, 0, 1, 9, 1);
        if (settle_ok() != 0) {
            return 1;
        }
        eu4_gfx_live_capture_test_inject("clear");
        eu4_gfx_live_capture_test_diag("anchor_survives_nonretained", "encounter_33",
                                       "storage_failed");
        unsigned state = 0;
        int reprobe = 0;
        int verify = 0;
        uint32_t slot = 0, selector = 0, input = 0;
        uint64_t encounter = 0;
        int index = eu4_gfx_live_capture_test_find(2, 0xffffffffu, 13);
        if (index < 0 ||
            !eu4_gfx_live_capture_test_signature((unsigned)index, &slot, &selector,
                                                 &input, &state, &reprobe, &verify,
                                                 &encounter) ||
            state != 2 || encounter != 33) {
            return fail("anchor did not survive");
        }
    } else if (strcmp(mode, "acq-rollback") == 0) {
        eu4_gfx_live_capture_test_inject("bind");
        eu4_gfx_live_capture_test_inject("rollback");
        s03(&value, 0, 0, 0, 1);
        eu4_gfx_live_capture_test_diag("rollback_fail_after_bind_fail", "",
                                       "cas_exhausted");
    } else if (strcmp(mode, "acq-pre") == 0) {
        s03(&value, 0, 0, 0, 1);
        if (settle_ok() != 0) {
            return 1;
        }
        s03(&value, 1, 0, 0, 1);
        if (settle_ok() != 0) {
            return 1;
        }
        s03(&value, 0, 0, 0, 1);
        if (eu4_gfx_live_capture_test_counter("screened_repeat") != 1 ||
            eu4_gfx_live_capture_test_counter("novel_retained") != 1) {
            return fail("pre-state sample was not a single declined verification");
        }
        printf("ACQ_CHECK pre-state ok\n");
    } else if (strcmp(mode, "acq-post") == 0) {
        s03(&value, 0, 0, 0, 1);
        if (settle_ok() != 0) {
            return 1;
        }
        s03(&value, 0, 1, 9, 1);
        if (settle_ok() != 0) {
            return 1;
        }
        for (unsigned n = 3; n <= 16; n++) {
            s03(&value, 0, 0, 0, 1);
        }
        s03(&value, 0, 0, 0, 1);
        if (settle_ok() != 0) {
            return 1;
        }
        if (eu4_gfx_live_capture_test_counter("novel_retained") != 2) {
            return fail("post-state verification was not distinct");
        }
        printf("ACQ_CHECK post-state ok\n");
    } else if (strcmp(mode, "acq-zero") == 0) {
        for (unsigned n = 1; n <= 32; n++) {
            capture_call(&value, 1, 1, 8, 0, 0, 0, 1);
            if (sample_point(n) && settle_ok() != 0) {
                return 1;
            }
        }
        capture_call(&value, 1, 1, 0, 0, 0, 0, 1);
        if (settle_ok() != 0) {
            return 1;
        }
        if (eu4_gfx_live_capture_test_counter("novel_retained") != 2) {
            return fail("zero input was not retained");
        }
        printf("ACQ_CHECK zero-input ok\n");
    } else if (strcmp(mode, "acq-sig-read") == 0) {
        eu4_gfx_live_capture_test_inject("sig-read");
        s03(&value, 0, 0, 0, 1);
        eu4_gfx_live_capture_test_diag("signature_read_contended", "",
                                       "signature_read_contended");
    } else if (strcmp(mode, "acq-scan") == 0) {
        eu4_gfx_live_capture_test_inject("scan-unreadable");
        s03(&value, 0, 0, 0, 1);
        if (eu4_gfx_live_capture_test_find(2, 0xffffffffu, 13) >= 0) {
            return fail("unreadable scan published a key");
        }
        eu4_gfx_live_capture_test_diag("uniqueness_scan_unreadable", "",
                                       "cas_exhausted");
    } else if (strcmp(mode, "acq-pending-retry") == 0) {
        eu4_gfx_live_capture_test_inject("snapshot");
        s03(&value, 0, 0, 0, 1);
        if (settle_ok() != 0) {
            return 1;
        }
        eu4_gfx_live_capture_test_inject("clear");
        if (eu4_gfx_live_capture_test_find(2, 0xffffffffu, 13) >= 0) {
            return fail("failed pending capture stayed live");
        }
        s03(&value, 0, 0, 0, 1);
        if (settle_ok() != 0) {
            return 1;
        }
        if (eu4_gfx_live_capture_test_counter("novel_retained") != 1) {
            return fail("retry after empty was not retained");
        }
        printf("ACQ_CHECK pending-retry ok\n");
    } else if (strcmp(mode, "acq-mismatch") == 0) {
        s03(&value, 0, 0, 0, 1);
        if (settle_ok() != 0) {
            return 1;
        }
        eu4_gfx_live_capture_test_arm("after-bind");
        acq_job job = {.slot = 2, .buffer_path = 1, .selector = 13, .dirty = 1};
        pthread_t thread;
        if (pthread_create(&thread, NULL, acq_worker, &job) != 0) {
            return fail("cannot start mismatched verification");
        }
        if (!eu4_gfx_live_capture_test_wait_arrived("after-bind", 2000)) {
            return fail("verification did not bind");
        }
        int index = eu4_gfx_live_capture_test_find(2, 0xffffffffu, 13);
        if (index < 0 ||
            !eu4_gfx_live_capture_test_bump_generation((unsigned)index)) {
            return fail("cannot bump generation");
        }
        eu4_gfx_live_capture_test_release("after-bind");
        (void)pthread_join(thread, NULL);
        if (settle_ok() != 0) {
            return 1;
        }
        printf("ACQ_CHECK post-bind-mismatch ok\n");
    } else if (strcmp(mode, "acq-drop-reservation") == 0) {
        eu4_gfx_live_capture_test_arm("after-bind");
        acq_job job = {.slot = 2, .buffer_path = 1, .selector = 13, .dirty = 1};
        pthread_t thread;
        if (pthread_create(&thread, NULL, acq_worker, &job) != 0) {
            return fail("cannot start reservation drop");
        }
        if (!eu4_gfx_live_capture_test_wait_arrived("after-bind", 2000)) {
            return fail("capture did not bind");
        }
        eu4_gfx_live_capture_test_drop_reservation();
        eu4_gfx_live_capture_test_release("after-bind");
        (void)pthread_join(thread, NULL);
        if (settle_ok() != 0) {
            return 1;
        }
        printf("ACQ_CHECK missing-reservation ok\n");
    } else if (strcmp(mode, "acq-reader") == 0) {
        eu4_gfx_live_capture_test_arm("mid-publish");
        acq_job job = {.slot = 2, .buffer_path = 1, .selector = 13, .dirty = 1};
        pthread_t thread;
        if (pthread_create(&thread, NULL, acq_worker, &job) != 0) {
            return fail("cannot start publisher");
        }
        if (!eu4_gfx_live_capture_test_wait_arrived("mid-publish", 2000)) {
            return fail("publisher did not pause");
        }
        int saw_odd = 0;
        for (unsigned i = 0; i < 128; i++) {
            uint32_t slot, selector, input;
            unsigned state;
            uint64_t generation;
            if (!eu4_gfx_live_capture_test_raw(i, &slot, &selector, &input,
                                               &state, &generation)) {
                return fail("raw read failed");
            }
            if (generation & 1ull) {
                saw_odd = 1;
                if (state == 1 &&
                    !(slot == 2 && selector == 0xffffffffu && input == 13)) {
                    return fail("reader saw a new state with the old key");
                }
            }
        }
        if (!saw_odd) {
            return fail("publish window was not visible");
        }
        eu4_gfx_live_capture_test_release("mid-publish");
        (void)pthread_join(thread, NULL);
        if (settle_ok() != 0) {
            return 1;
        }
        printf("ACQ_CHECK reader ok\n");
    } else if (strcmp(mode, "acq-tight-screen") == 0) {
        s03(&value, 0, 0, 0, 1);
        if (settle_ok() != 0) {
            return 1;
        }
        s03(&value, 0, 0, 0, 1);
        if (settle_ok() != 0) {
            return 1;
        }
        uint64_t attempts = eu4_gfx_live_capture_test_counter("admission_attempts");
        uint64_t claims = eu4_gfx_live_capture_test_counter("buffer_claims");
        s03(&value, 0, 0, 0, 1);
        if (eu4_gfx_live_capture_test_counter("admission_attempts") != attempts ||
            eu4_gfx_live_capture_test_counter("buffer_claims") != claims) {
            return fail("screened repeat touched admission or an extra buffer");
        }
        printf("ACQ_CHECK tight-screen ok\n");
    } else if (strcmp(mode, "acq-false-coverage") == 0) {
        capture_call(&value, 0, 1, 4, 0, 0, 0, 0);
        if (settle_ok() != 0) {
            return 1;
        }
        uint64_t retained, p_cov, p_div, lower;
        uint32_t established;
        eu4_gfx_live_capture_test_feasibility(&retained, &p_cov, &p_div, &lower,
                                              &established);
        if (established != 0 || retained != 0) {
            return fail("ineligible write established coverage");
        }
        printf("ACQ_CHECK false-coverage ok\n");
    } else if (strcmp(mode, "acq-capacity") == 0) {
        for (unsigned i = 0; i < 64; i++) {
            unsigned slot = i % 6u;
            int buffer_path = i != 1;
            uint32_t selector = buffer_path ? (i == 0 ? 0u : i + 1u) : 13u;
            capture_call(&value, slot, buffer_path, selector, 0, 1, i + 1u, 1);
            if (settle_ok() != 0) {
                return 1;
            }
        }
        uint64_t before = eu4_gfx_live_capture_test_counter("novel_retained");
        capture_call(&value, 2, 1, 400u, 0, 1, 80, 1);
        if (before != 64 ||
            eu4_gfx_live_capture_test_counter("novel_retained") != 64) {
            return fail("65th capture overwrote the retained set");
        }
        printf("ACQ_CHECK capacity-refused ok\n");
    } else if (strcmp(mode, "acq-reclaim") == 0) {
        for (unsigned i = 0; i < 128; i++) {
            capture_call(&value, i % 6u, 1, i + 1u, 0, 0, 0, 0);
            if (settle_ok() != 0) {
                return 1;
            }
        }
        capture_call(&value, 0, 1, 40000u, 0, 0, 0, 1);
        if (settle_ok() != 0) {
            return 1;
        }
        if (eu4_gfx_live_capture_test_find(0, 0xffffffffu, 40000u) != 0) {
            return fail("new diversity key did not reclaim the lowest victim");
        }
        printf("ACQ_CHECK reclaim ok\n");
    } else if (strcmp(mode, "acq-table-synthetic") == 0) {
        printf("SYNTHETIC_STATE_INJECTION table_capacity_refused\n");
        eu4_gfx_live_capture_test_synthetic_anchored_table();
        s03(&value, 0, 0, 0, 1);
        eu4_gfx_live_capture_test_diag("table_capacity_refused", "synthetic",
                                       "table_capacity_refused");
    } else if (strcmp(mode, "acq-victim-cov") == 0 ||
               strcmp(mode, "acq-victim-div") == 0) {
        int coverage_first = strcmp(mode, "acq-victim-cov") == 0;
        s03(&value, 0, 0, 0, 1);
        if (settle_ok() != 0 || fill_ineligible(&value, 127) != 0) {
            return 1;
        }
        eu4_gfx_live_capture_test_arm("after-pending");
        acq_job coverage = {.slot = 0, .buffer_path = 1, .selector = 0, .dirty = 1};
        acq_job diversity = {.slot = 2, .buffer_path = 1, .selector = 7, .dirty = 1};
        pthread_t first_thread;
        pthread_t second_thread;
        acq_job *first_job = coverage_first ? &coverage : &diversity;
        acq_job *second_job = coverage_first ? &diversity : &coverage;
        if (pthread_create(&first_thread, NULL, acq_worker, first_job) != 0) {
            return fail("cannot start the first victim claimant");
        }
        if (!eu4_gfx_live_capture_test_wait_arrived("after-pending", 2000)) {
            return fail("first claimant did not publish");
        }
        int first_index = eu4_gfx_live_capture_test_find(
            first_job->slot, 0xffffffffu, first_job->selector);
        if (pthread_create(&second_thread, NULL, acq_worker, second_job) != 0) {
            return fail("cannot start the second victim claimant");
        }
        (void)pthread_join(second_thread, NULL);
        int second_index = -1;
        if (wait_published(second_job->slot, 0xffffffffu, second_job->selector,
                           &second_index) != 0) {
            return 1;
        }
        int still = eu4_gfx_live_capture_test_find(
            first_job->slot, 0xffffffffu, first_job->selector);
        if (first_index < 0 || second_index < 0 || first_index == second_index ||
            still != first_index) {
            return fail("victim claim was overwritten");
        }
        eu4_gfx_live_capture_test_release("after-pending");
        (void)pthread_join(first_thread, NULL);
        if (settle_ok() != 0) {
            return 1;
        }
        printf("ACQ_CHECK %s ok\n", mode);
    } else if (strcmp(mode, "acq-reprobe-held") == 0) {
        if (fill_ineligible(&value, 128) != 0) {
            return 1;
        }
        uint32_t held_slot = 0;
        uint32_t held_selector = 0;
        uint32_t held_input = 0;
        unsigned state = 0;
        int reprobe = 0;
        int verify = 0;
        uint64_t encounter = 0;
        if (!eu4_gfx_live_capture_test_signature(
                0, &held_slot, &held_selector, &held_input, &state, &reprobe,
                &verify, &encounter) ||
            state != 3) {
            return fail("index 0 is not an idle unanchored entry");
        }
        eu4_gfx_live_capture_test_arm("after-bind");
        acq_job held = {.slot = held_slot,
                        .buffer_path = held_selector == 0xffffffffu,
                        .selector = held_input,
                        .dirty = 1};
        pthread_t thread;
        if (pthread_create(&thread, NULL, acq_worker, &held) != 0) {
            return fail("cannot start the held reprobe");
        }
        if (!eu4_gfx_live_capture_test_wait_arrived("after-bind", 2000)) {
            return fail("reprobe did not bind");
        }
        capture_call(&value, 4, 1, 60000u, 0, 0, 0, 1);
        int reclaimed = -1;
        if (wait_published(4, 0xffffffffu, 60000u, &reclaimed) != 0) {
            return 1;
        }
        uint32_t now_slot = 0;
        uint32_t now_selector = 0;
        uint32_t now_input = 0;
        if (reclaimed == 0 ||
            !eu4_gfx_live_capture_test_signature(
                0, &now_slot, &now_selector, &now_input, &state, &reprobe,
                &verify, &encounter) ||
            now_slot != held_slot || now_input != held_input || !reprobe) {
            return fail("reprobe in flight was reclaimed");
        }
        eu4_gfx_live_capture_test_release("after-bind");
        (void)pthread_join(thread, NULL);
        if (settle_ok() != 0) {
            return 1;
        }
        printf("ACQ_CHECK reprobe-held ok\n");
    } else if (strcmp(mode, "acq-skew") == 0) {
        s03(&value, 0, 0, 0, 1);
        if (settle_ok() != 0) {
            return 1;
        }
        eu4_gfx_live_capture_test_inject("counter-skew");
        printf("ACQ_CHECK skew armed\n");
    } else if (strcmp(mode, "acq-bad-hash") == 0) {
        s03(&value, 0, 0, 0, 1);
        if (settle_ok() != 0) {
            return 1;
        }
        s03(&value, 0, 0, 0, 1);
        if (settle_ok() != 0) {
            return 1;
        }
        eu4_gfx_live_capture_test_inject("bad-audit-hash");
        printf("ACQ_CHECK bad-hash armed\n");
    } else if (strcmp(mode, "acq-audit-empty") == 0) {
        printf("ACQ_CHECK audit-empty ok\n");
    } else if (strcmp(mode, "acq-predicate") == 0) {
        eu4_gfx_live_capture_test_predicate_label("s03");
        s03(&value, 0, 0, 0, 1);
        if (settle_ok() != 0) {
            return 1;
        }
        eu4_gfx_live_capture_test_predicate_label("dword1");
        capture_call(&value, 2, 1, 1, 0, 0, 0, 1);
        if (settle_ok() != 0) {
            return 1;
        }
        eu4_gfx_live_capture_test_predicate_label("fixed");
        capture_call(&value, 3, 0, 13, 0, 0, 0, 1);
        if (settle_ok() != 0) {
            return 1;
        }
        eu4_gfx_live_capture_test_predicate_label("zero");
        capture_call(&value, 5, 1, 0, 0, 0, 0, 1);
        if (settle_ok() != 0) {
            return 1;
        }
        eu4_gfx_live_capture_test_predicate_label("ineligible");
        capture_call(&value, 4, 1, 9, 0, 0, 0, 0);
        if (settle_ok() != 0) {
            return 1;
        }
    } else if (strcmp(mode, "acq-deadline") == 0) {
        s03(&value, 0, 0, 0, 1);
        if (settle_ok() != 0) {
            return 1;
        }
        if (!wait_done_marker(run_dir, 5000)) {
            return fail("deadline did not seal");
        }
        printf("ACQ_CHECK deadline ok\n");
    } else if (v1_overhead) {
        enum { N = 10000 };
        uint64_t *mach_samples = calloc(N, sizeof(uint64_t));
        uint64_t *leaf_samples = calloc(N, sizeof(uint64_t));
        uint64_t *v1_samples = calloc(N, sizeof(uint64_t));
        uint64_t *refuse_samples = calloc(N, sizeof(uint64_t));
        uint64_t *v2_samples = calloc(N, sizeof(uint64_t));
        if (mach_samples == NULL || leaf_samples == NULL || v1_samples == NULL ||
            refuse_samples == NULL || v2_samples == NULL) {
            return fail("cannot allocate timing samples");
        }
        volatile uint32_t source = 13;
        uint32_t destination = 0;
        for (unsigned i = 0; i < 1000; i++) {
            mach_vm_size_t got = 0;
            (void)mach_vm_read_overwrite(
                mach_task_self(), (mach_vm_address_t)(uintptr_t)&source, 4,
                (mach_vm_address_t)(uintptr_t)&destination, &got);
        }
        for (unsigned i = 0; i < N; i++) {
            mach_vm_size_t got = 0;
            uint64_t t0 = mach_absolute_time();
            kern_return_t result = mach_vm_read_overwrite(
                mach_task_self(), (mach_vm_address_t)(uintptr_t)&source, 4,
                (mach_vm_address_t)(uintptr_t)&destination, &got);
            mach_samples[i] = ticks_to_ns(mach_absolute_time() - t0);
            if (result != KERN_SUCCESS || got != 4) {
                return fail("mach read failed");
            }
        }
        for (unsigned i = 0; i < N; i++) {
            uint64_t t0 = mach_absolute_time();
            asm volatile("" ::: "memory");
            leaf_samples[i] = ticks_to_ns(mach_absolute_time() - t0);
        }
        unsigned v1_count = 0;
        while (v1_count < N) {
            if (!eu4_gfx_live_capture_start_v1(
                    (void *)(uintptr_t)gfx_set_constant_buffer_contract_stub,
                    run_dir, control_path, "live-test", 30, true)) {
                return fail("v1 timing session failed");
            }
            set_control(1);
            sleep_ms(20);
            for (unsigned i = 0; i < 64 && v1_count < N; i++) {
                uint64_t t0 = mach_absolute_time();
                s03(&value, 0, 0, 0, 1);
                v1_samples[v1_count++] = ticks_to_ns(mach_absolute_time() - t0);
            }
            set_control(2);
            eu4_gfx_live_capture_stop();
        }
        if (!eu4_gfx_live_capture_start_v1(
                (void *)(uintptr_t)gfx_set_constant_buffer_contract_stub, run_dir,
                control_path, "live-test", 30, true)) {
            return fail("v1 refuse session failed");
        }
        set_control(1);
        sleep_ms(20);
        held_capture *held = calloc(1, sizeof(*held));
        if (held == NULL) {
            return fail("cannot hold a v1 ticket");
        }
        fill_fixture(&held->value);
        frame_for(&held->frame, &held->value, 3, 0);
        pthread_t producer;
        if (pthread_create(&producer, NULL, held_capture_worker, held) != 0) {
            return fail("cannot create held capture");
        }
        wait_flag(&held->entered);
        for (unsigned i = 0; i < 63; i++) {
            direct_capture(&held->value, 3, 0);
        }
        for (unsigned i = 0; i < N; i++) {
            uint64_t t0 = mach_absolute_time();
            direct_capture(&held->value, 3, 0);
            refuse_samples[i] = ticks_to_ns(mach_absolute_time() - t0);
        }
        open_gate(&held->release);
        (void)pthread_join(producer, NULL);
        free(held);
        set_control(2);
        eu4_gfx_live_capture_stop();

        if (!eu4_gfx_live_capture_start(
                (void *)(uintptr_t)gfx_set_constant_buffer_contract_stub, run_dir,
                control_path, "live-test", 120, true)) {
            return fail("v2 timing session failed");
        }
        set_control(1);
        sleep_ms(20);
        s03(&value, 0, 0, 0, 1);
        if (settle_ok() != 0) {
            return 1;
        }
        unsigned v2_count = 0;
        for (unsigned n = 2; n <= 160001; n++) {
            uint64_t before = eu4_gfx_live_capture_test_counter("selected");
            uint64_t t0 = mach_absolute_time();
            s03(&value, 0, 0, 0, 1);
            uint64_t elapsed = ticks_to_ns(mach_absolute_time() - t0);
            if (eu4_gfx_live_capture_test_counter("selected") != before) {
                if (settle_ok() != 0) {
                    return 1;
                }
                if (v2_count < N) {
                    v2_samples[v2_count++] = elapsed;
                }
            }
        }
        uint64_t screen_p50 = 0, screen_p99 = 0, screen_count = 0;
        if (v2_count < N ||
            !eu4_gfx_live_capture_test_screen_percentile(&screen_p50, &screen_p99,
                                                         &screen_count) ||
            screen_count < N) {
            return fail("screen sample budget was not met");
        }
        uint64_t mach_p50, mach_p99, leaf_p50, leaf_p99, v1_p50, v1_p99,
            refuse_p50, refuse_p99, v2_p50, v2_p99;
        percentile(mach_samples, N, &mach_p50, &mach_p99);
        percentile(leaf_samples, N, &leaf_p50, &leaf_p99);
        percentile(v1_samples, N, &v1_p50, &v1_p99);
        percentile(refuse_samples, N, &refuse_p50, &refuse_p99);
        percentile(v2_samples, N, &v2_p50, &v2_p99);
        printf("ACQ_OVERHEAD mach_p50=%llu mach_p99=%llu screen_p50=%llu "
               "screen_p99=%llu v1_full_p50=%llu v2_full_p50=%llu "
               "unhooked_p50=%llu v1_refuse_p50=%llu screen_count=%llu "
               "v2_count=%u\n",
               (unsigned long long)mach_p50, (unsigned long long)mach_p99,
               (unsigned long long)screen_p50, (unsigned long long)screen_p99,
               (unsigned long long)v1_p50, (unsigned long long)v2_p50,
               (unsigned long long)leaf_p50, (unsigned long long)refuse_p50,
               (unsigned long long)screen_count, v2_count);
        fflush(stdout);
        free(mach_samples);
        free(leaf_samples);
        free(v1_samples);
        free(refuse_samples);
        free(v2_samples);
        if (mach_p50 > 1000 || mach_p99 > 2500) {
            fprintf(stderr, "mach read exceeds the screen cap\n");
            set_control(2);
            eu4_gfx_live_capture_stop();
            (void)close(control_fd);
            return 2;
        }
        set_control(2);
        eu4_gfx_live_capture_stop();
        (void)close(control_fd);
        return 0;
    } else {
        return fail("unknown diversity mode");
    }

    if (failed) {
        return 1;
    }
    set_control(2);
    sleep_ms(20);
    eu4_gfx_live_capture_stop();
    (void)close(control_fd);
    return 0;
}

static int run_start_failure(const char *run_dir, const char *control_path) {
    if (open_control(control_path) != 0) {
        return 1;
    }
    long page = sysconf(_SC_PAGESIZE);
    if (page <= 0) {
        return fail("page size is unavailable");
    }
    uint8_t *site = mmap(NULL, (size_t)page, PROT_READ | PROT_WRITE,
                         MAP_PRIVATE | MAP_ANON, -1, 0);
    if (site == MAP_FAILED) {
        return fail("cannot map fake site");
    }
    memset(site, 0x90, (size_t)page);
    if (mprotect(site, (size_t)page, PROT_READ | PROT_EXEC) != 0) {
        return fail("cannot protect fake site");
    }
    bool started = eu4_gfx_live_capture_start(site, run_dir, control_path,
                                              "live-start-fail", 5, true);
    printf("LIVE-START-FAIL started=%d\n", started ? 1 : 0);
    (void)munmap(site, (size_t)page);
    (void)close(control_fd);
    return started ? 1 : 0;
}

int main(int argc, char **argv) {
    if (argc != 4) {
        fprintf(stderr, "usage: %s <mode> <run-dir> <control>\n", argv[0]);
        return 2;
    }
    if (strcmp(argv[1], "start-fail") == 0) {
        return run_start_failure(argv[2], argv[3]);
    }
    if (strncmp(argv[1], "acq-", 4) == 0 || strcmp(argv[1], "overhead") == 0) {
        return run_diversity(argv[1], argv[2], argv[3]);
    }
    return run_mode(argv[1], argv[2], argv[3]);
}
