#include "eu4_gfx_live_capture.h"
#include "eu4_gfx_set_constant_buffer_capture.h"
#include "gfx_capture_test_state.h"

#include <errno.h>
#include <fcntl.h>
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
    eu4_gfx_set_constant_buffer_capture_thread_init();
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
    eu4_gfx_set_constant_buffer_capture_thread_init();
    eu4_gfx_set_constant_buffer_capture_entry(&frame);
    eu4_gfx_set_constant_buffer_capture_exit(&frame);
}

static void *worker(void *argument) {
    fixture *value = (fixture *)argument;
    direct_capture(value, 3, 0);
    return NULL;
}

static int run_mode(const char *mode, const char *run_dir,
                    const char *control_path) {
    if (open_control(control_path) != 0) {
        return 1;
    }
    if (!eu4_gfx_live_capture_start(
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
        eu4_gfx_set_constant_buffer_capture_thread_init();
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
    } else {
        return fail("unknown mode");
    }

    set_control(2);
    sleep_ms(50);
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
    return run_mode(argv[1], argv[2], argv[3]);
}
