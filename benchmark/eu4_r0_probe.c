// R0 frame-evolution probe: single CGLFlushDrawable owner, seqlock control mmap, census readback.
#define GL_SILENCE_DEPRECATION 1
#include "eu4_r0_control.h"
#include "eu4_r0_readback.h"

#include <OpenGL/OpenGL.h>
#include <OpenGL/gl.h>
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

static pthread_once_t setup_once = PTHREAD_ONCE_INIT;
static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
static int log_fd = -1;
static const volatile eu4_r0_control_page_t *control;

static void *(*app_instance)(void);
static bool (*actually_paused)(void *);
static void *idler_vtable;

static uint64_t swap_bucket_start_ns;
static uint64_t swaps;
static uint64_t paused_swaps;

static uint64_t frame_index;
static uint32_t last_acked_generation;
static uint32_t last_seen_generation;
static uint32_t active_scenario_id;
static uint32_t active_mode;

static CGLContextObj last_context;
static int last_width;
static int last_height;

static uint8_t *readback_buffer;
static size_t readback_capacity;
static uint8_t *reference_buffer;
static size_t reference_size;

static uint64_t ns(clockid_t clock_id) {
    struct timespec t;
    if (clock_gettime(clock_id, &t)) {
        return 0;
    }
    return (uint64_t)t.tv_sec * 1000000000ull + (uint64_t)t.tv_nsec;
}

static void setup(void) {
    const char *path = getenv("EU4_R0_LOG");
    if (path && path[0] == '/') {
        log_fd = open(path, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
    }
    const char *control_path = getenv("EU4_R0_CONTROL");
    if (control_path && control_path[0] == '/') {
        int fd = open(control_path, O_RDONLY);
        if (fd >= 0) {
            void *mapping = mmap(NULL, EU4_R0_CONTROL_PAGE_SIZE, PROT_READ, MAP_SHARED, fd, 0);
            if (mapping != MAP_FAILED) {
                control = (const volatile eu4_r0_control_page_t *)mapping;
            }
            close(fd);
        }
    }
    app_instance = dlsym(RTLD_DEFAULT, "_ZN12CApplication14AccessInstanceEv");
    actually_paused = dlsym(RTLD_DEFAULT, "_ZNK10CGameSpeed16IsActuallyPausedEv");
    idler_vtable = dlsym(RTLD_DEFAULT, "_ZTV12CInGameIdler");
}

static bool test_census_enabled(void) {
    const char *flag = getenv("EU4_R0_TEST_CENSUS");
    return flag && flag[0] && strcmp(flag, "0") != 0;
}

static bool pause_verified(void) {
    if (test_census_enabled()) {
        return true;
    }
    if (!app_instance || !actually_paused || !idler_vtable) {
        return false;
    }
    char *app = app_instance();
    if (!app) {
        return false;
    }
    char *idler = *(char **)(app + 0x40);
    if (!idler || *(void **)idler != (char *)idler_vtable + 16) {
        return false;
    }
    void *speed = *(void **)(idler + 0x408);
    return speed && actually_paused(speed);
}

static void log_line(const char *line) {
    if (log_fd < 0 || !line) {
        return;
    }
    size_t len = strlen(line);
    if (len && write(log_fd, line, len) != (ssize_t)len) {
        close(log_fd);
        log_fd = -1;
    }
}

static void snapshot_swap_stats(uint64_t now, bool paused) {
    if (log_fd < 0) {
        return;
    }
    pthread_mutex_lock(&lock);
    if (!swap_bucket_start_ns) {
        swap_bucket_start_ns = now;
    }
    swaps++;
    if (paused) {
        paused_swaps++;
    }
    if (now - swap_bucket_start_ns >= 1000000000ull) {
        char line[192];
        int n = snprintf(line, sizeof(line), "S,%llu,%llu,%u,%llu,%llu,%llu\n",
                         (unsigned long long)now, (unsigned long long)ns(CLOCK_REALTIME),
                         (unsigned)active_mode, (unsigned long long)(now - swap_bucket_start_ns),
                         (unsigned long long)swaps, (unsigned long long)paused_swaps);
        if (n > 0 && (size_t)n < sizeof(line)) {
            log_line(line);
        }
        swap_bucket_start_ns = now;
        swaps = paused_swaps = 0;
    }
    pthread_mutex_unlock(&lock);
}

static bool buffer_grow(uint8_t **buf, size_t *cap, size_t needed) {
    if (*cap >= needed) {
        return true;
    }
    uint8_t *grown = realloc(*buf, needed);
    uint8_t *ref_grown = realloc(reference_buffer, needed);
    if (!grown || !ref_grown) {
        free(grown);
        free(ref_grown);
        return false;
    }
    *buf = grown;
    reference_buffer = ref_grown;
    readback_capacity = needed;
    *cap = needed;
    return true;
}

static void invalidate_reference(void) {
    reference_size = 0;
}

static bool census_readback(CGLContextObj context, int *width_out, int *height_out, uint64_t *crc_out,
                            GLenum *gl_error_out) {
    eu4_r0_readback_buffers_t buffers = {
        .pixels_out = &readback_buffer,
        .capacity_out = &readback_capacity,
        .grow = buffer_grow,
    };
    eu4_r0_readback_result_t result;
    bool relaxed = test_census_enabled();
    if (!eu4_r0_readback_capture(context, EU4_R0_TARGET_DRAWABLE_BACK, relaxed, &buffers, &result)) {
        if (gl_error_out) {
            *gl_error_out = result.gl_error_observed;
        }
        return false;
    }
    if (width_out) {
        *width_out = result.width;
    }
    if (height_out) {
        *height_out = result.height;
    }
    if (crc_out) {
        *crc_out = result.crc64;
    }
    if (gl_error_out) {
        *gl_error_out = result.gl_error_observed;
    }
    return true;
}

static void maybe_ack_generation(const eu4_r0_control_snapshot_t *snap, uint64_t now) {
    if (!snap || snap->generation == 0 || snap->generation == last_acked_generation) {
        return;
    }
    char line[160];
    int n = snprintf(line, sizeof(line), "ACK,%u,%llu,%llu,%u,%u\n", snap->generation,
                     (unsigned long long)frame_index, (unsigned long long)now, (unsigned)snap->mode,
                     (unsigned)snap->scenario_id);
    if (n > 0 && (size_t)n < sizeof(line)) {
        log_line(line);
        last_acked_generation = snap->generation;
    }
}

static uint64_t pthread_tid_self(void) {
#if defined(__APPLE__)
    uint64_t tid = 0;
    pthread_threadid_np(NULL, &tid);
    return tid;
#else
    return 0;
#endif
}

static void log_frame_record(CGLContextObj context, bool census_attempted, bool census_ok, int width,
                             int height, int memcmp_equal, uint64_t crc, uint64_t uptime_ns,
                             GLenum gl_error_observed, bool flush_ok, int flush_cgl_error) {
    int flush_is_main = 0;
#if defined(__APPLE__)
    flush_is_main = pthread_main_np();
#endif
    char line[320];
    int n = snprintf(
        line, sizeof(line),
        "F,%llu,%u,%u,%u,%u,%llu,%d,%d,%d,%d,%llu,%d,%llu,%u,%u,%d,%llu\n",
        (unsigned long long)frame_index, (unsigned)active_mode, (unsigned)active_scenario_id,
        (unsigned)pause_verified(), (unsigned)(census_attempted && census_ok),
        (unsigned long long)(uintptr_t)context, width, height, memcmp_equal,
        (unsigned)census_attempted, (unsigned long long)crc, flush_is_main,
        (unsigned long long)uptime_ns, (unsigned)gl_error_observed, (unsigned)flush_ok,
        flush_cgl_error, (unsigned long long)pthread_tid_self());
    if (n > 0 && (size_t)n < sizeof(line)) {
        log_line(line);
    }
}

static CGLError probed_flush(CGLContextObj context) {
    pthread_once(&setup_once, setup);
    uint64_t now = ns(CLOCK_UPTIME_RAW);
    bool paused = pause_verified();

    eu4_r0_control_snapshot_t snap = {0};
    bool have_snap = control && eu4_r0_control_consume(control, &snap);
    if (have_snap) {
        if (snap.generation != last_seen_generation || snap.mode != active_mode ||
            snap.scenario_id != active_scenario_id) {
            if (context != last_context || snap.scenario_id != active_scenario_id) {
                invalidate_reference();
            }
            last_seen_generation = snap.generation;
            active_mode = snap.mode;
            active_scenario_id = snap.scenario_id;
            maybe_ack_generation(&snap, now);
        }
    }

    bool census_attempted = false;
    bool census_ok = false;
    int width = 0;
    int height = 0;
    int memcmp_equal = -1;
    uint64_t crc = 0;
    GLenum gl_error_observed = GL_NO_ERROR;

    if (have_snap && active_mode == EU4_R0_MODE_CENSUS && paused && snap.generation != 0 &&
        snap.generation == last_acked_generation) {
        census_attempted = true;
        census_ok = census_readback(context, &width, &height, &crc, &gl_error_observed);
        if (census_ok) {
            size_t total = (size_t)width * 4u * (size_t)height;
            if (context != last_context || width != last_width || height != last_height) {
                invalidate_reference();
            }
            last_context = context;
            last_width = width;
            last_height = height;
            if (reference_size == total) {
                memcmp_equal = memcmp(readback_buffer, reference_buffer, total) == 0 ? 1 : 0;
            } else {
                memcmp_equal = 0;
            }
            memcpy(reference_buffer, readback_buffer, total);
            reference_size = total;
        }
    }

    frame_index++;
    CGLError flush_result = CGLFlushDrawable(context);
    int saved_errno = errno;
    bool flush_ok = flush_result == kCGLNoError;
    log_frame_record(context, census_attempted, census_ok, width, height, memcmp_equal, crc, now,
                     gl_error_observed, flush_ok, (int)flush_result);
    snapshot_swap_stats(now, flush_ok && paused);
    errno = saved_errno;
    return flush_result;
}

__attribute__((used, section("__DATA,__interpose")))
static const struct {
    const void *replacement;
    const void *replacee;
} interpose_flush = {(const void *)probed_flush, (const void *)CGLFlushDrawable};
