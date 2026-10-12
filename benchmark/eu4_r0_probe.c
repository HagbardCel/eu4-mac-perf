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
#include <stdatomic.h>
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

static uint32_t capture_track_generation;
static bool capture_attempted_for_generation;
static bool capture_succeeded_for_generation;

static _Atomic uint64_t flush_owner_tid;
static _Atomic bool flush_thread_violation;
static bool violation_logged;

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
    const char *force_fail = getenv("EU4_R0_TEST_BUFFER_GROW_FAIL");
    if (force_fail && force_fail[0] && strcmp(force_fail, "0") != 0) {
        return false;
    }
    if (*cap >= needed) {
        return true;
    }
    uint8_t *new_readback = (uint8_t *)malloc(needed);
    uint8_t *new_reference = (uint8_t *)malloc(needed);
    if (!new_readback || !new_reference) {
        free(new_readback);
        free(new_reference);
        return false;
    }
    size_t preserve = readback_capacity < needed ? readback_capacity : needed;
    if (preserve && readback_buffer) {
        memcpy(new_readback, readback_buffer, preserve);
    }
    if (preserve && reference_buffer) {
        memcpy(new_reference, reference_buffer, preserve);
    }
    free(readback_buffer);
    free(reference_buffer);
    readback_buffer = new_readback;
    reference_buffer = new_reference;
    readback_capacity = needed;
    *buf = readback_buffer;
    *cap = needed;
    return true;
}

static void invalidate_reference(void) {
    reference_size = 0;
}

static bool census_readback(CGLContextObj context, eu4_r0_readback_result_t *result_out) {
    eu4_r0_readback_buffers_t buffers = {
        .pixels_out = &readback_buffer,
        .capacity_out = &readback_capacity,
        .grow = buffer_grow,
    };
    eu4_r0_readback_result_t result;
    bool relaxed = test_census_enabled();
    eu4_r0_target_t target =
        test_census_enabled() ? EU4_R0_TARGET_TEST_FBO : EU4_R0_TARGET_DRAWABLE_BACK;
    bool ok = eu4_r0_readback_capture(context, target, relaxed, &buffers, &result);
    if (result_out) {
        *result_out = result;
    }
    return ok;
}

static bool is_census_mode(uint32_t mode) {
    return mode == EU4_R0_MODE_CENSUS || mode == EU4_R0_MODE_CENSUS_CAPTURE;
}

static bool write_all(int fd, const void *buffer, size_t length) {
    const uint8_t *cursor = (const uint8_t *)buffer;
    size_t remaining = length;
    while (remaining > 0) {
        ssize_t chunk = write(fd, cursor, remaining);
        if (chunk <= 0) {
            return false;
        }
        cursor += (size_t)chunk;
        remaining -= (size_t)chunk;
    }
    return true;
}

static bool capture_success_on_disk(uint32_t generation) {
    const char *dir = getenv("EU4_R0_CAPTURE_DIR");
    if (!dir || !dir[0]) {
        return false;
    }
    char path[512];
    int n = snprintf(path, sizeof(path), "%s/capture_%u.json", dir, (unsigned)generation);
    if (n <= 0 || (size_t)n >= sizeof(path)) {
        return false;
    }
    return access(path, F_OK) == 0;
}

static bool capture_failure_on_disk(uint32_t generation) {
    const char *dir = getenv("EU4_R0_CAPTURE_DIR");
    if (!dir || !dir[0]) {
        return false;
    }
    char path[512];
    int n = snprintf(path, sizeof(path), "%s/capture_%u.failed.json", dir, (unsigned)generation);
    if (n <= 0 || (size_t)n >= sizeof(path)) {
        return false;
    }
    return access(path, F_OK) == 0;
}

static void sync_capture_flags_for_generation(uint32_t generation) {
    capture_track_generation = generation;
    capture_succeeded_for_generation = capture_success_on_disk(generation);
    capture_attempted_for_generation =
        capture_succeeded_for_generation || capture_failure_on_disk(generation);
}

static bool test_capture_fail_once_active(void) {
    const char *flag = getenv("EU4_R0_TEST_CAPTURE_FAIL_ONCE");
    return flag && flag[0] && strcmp(flag, "0") != 0;
}

static bool test_capture_fail_once_should_reject(void) {
    static bool armed = true;
    if (!test_capture_fail_once_active()) {
        return false;
    }
    if (!armed) {
        return false;
    }
    armed = false;
    return true;
}

static bool write_capture_failure_sidecar(uint32_t generation, int error_code, const char *reason) {
    const char *dir = getenv("EU4_R0_CAPTURE_DIR");
    if (!dir || !dir[0]) {
        return false;
    }
    char json_tmp[512];
    char json_final[512];
    int n = snprintf(json_tmp, sizeof(json_tmp), "%s/capture_%u.failed.json.tmp", dir, (unsigned)generation);
    if (n <= 0 || (size_t)n >= sizeof(json_tmp)) {
        return false;
    }
    snprintf(json_final, sizeof(json_final), "%s/capture_%u.failed.json", dir, (unsigned)generation);
    int jfd = open(json_tmp, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
    if (jfd < 0) {
        return false;
    }
    char meta[512];
    int mlen = snprintf(meta, sizeof(meta),
                        "{\"artifact_kind\":\"capture_failure\",\"control_generation\":%u,"
                        "\"write_ok\":false,\"capture_error\":\"%s\",\"errno\":%d}\n",
                        (unsigned)generation, reason ? reason : "write_failed", error_code);
    if (mlen <= 0 || !write_all(jfd, meta, (size_t)mlen)) {
        close(jfd);
        unlink(json_tmp);
        return false;
    }
    close(jfd);
    if (rename(json_tmp, json_final) != 0) {
        unlink(json_tmp);
        return false;
    }
    return true;
}

static bool write_capture_artifact(uint32_t generation, uint32_t scenario_id, uint64_t record_frame_index,
                                   uint64_t uptime_ns, const eu4_r0_readback_result_t *result) {
    const char *dir = getenv("EU4_R0_CAPTURE_DIR");
    if (!dir || !dir[0] || !result || !result->ok || capture_success_on_disk(generation)) {
        return false;
    }
    if (test_capture_fail_once_should_reject()) {
        errno = EIO;
        return false;
    }
    char rgba_tmp[512];
    char json_tmp[512];
    char rgba_final[512];
    char json_final[512];
    int n = snprintf(rgba_tmp, sizeof(rgba_tmp), "%s/capture_%u.rgba.tmp", dir, (unsigned)generation);
    if (n <= 0 || (size_t)n >= sizeof(rgba_tmp)) {
        return false;
    }
    snprintf(json_tmp, sizeof(json_tmp), "%s/capture_%u.json.tmp", dir, (unsigned)generation);
    snprintf(rgba_final, sizeof(rgba_final), "%s/capture_%u.rgba", dir, (unsigned)generation);
    snprintf(json_final, sizeof(json_final), "%s/capture_%u.json", dir, (unsigned)generation);

    int fd = open(rgba_tmp, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
    if (fd < 0) {
        return false;
    }
    bool rgba_ok = write_all(fd, result->pixels, result->pixel_bytes);
    close(fd);
    if (!rgba_ok) {
        unlink(rgba_tmp);
        return false;
    }
    if (rename(rgba_tmp, rgba_final) != 0) {
        unlink(rgba_tmp);
        return false;
    }

    int jfd = open(json_tmp, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
    if (jfd < 0) {
        unlink(rgba_final);
        return false;
    }
    char meta[640];
    int mlen = snprintf(meta, sizeof(meta),
                        "{\"control_generation\":%u,\"scenario_id\":%u,\"frame_index\":%llu,"
                        "\"width\":%d,\"height\":%d,\"crc64\":%llu,\"uptime_ns\":%llu,"
                        "\"write_ok\":true,\"readback_failure_code\":0,\"pixel_bytes\":%zu}\n",
                        (unsigned)generation, (unsigned)scenario_id,
                        (unsigned long long)record_frame_index, result->width, result->height,
                        (unsigned long long)result->crc64, (unsigned long long)uptime_ns,
                        result->pixel_bytes);
    if (mlen <= 0 || !write_all(jfd, meta, (size_t)mlen)) {
        close(jfd);
        unlink(json_tmp);
        unlink(rgba_final);
        return false;
    }
    close(jfd);
    if (rename(json_tmp, json_final) != 0) {
        unlink(json_tmp);
        unlink(rgba_final);
        return false;
    }
    return true;
}

static void attempt_generation_capture(uint32_t generation, uint32_t scenario_id, uint64_t record_frame_index,
                                       uint64_t uptime_ns, const eu4_r0_readback_result_t *rb) {
    if (capture_attempted_for_generation) {
        return;
    }
    capture_attempted_for_generation = true;
    if (write_capture_artifact(generation, scenario_id, record_frame_index, uptime_ns, rb)) {
        capture_succeeded_for_generation = true;
        return;
    }
    write_capture_failure_sidecar(generation, errno, "rgba_or_sidecar_write_failed");
}

static void maybe_ack_generation(const eu4_r0_control_snapshot_t *snap, uint64_t now) {
    if (!snap || !snap->valid || snap->generation == 0 || snap->generation == last_acked_generation) {
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

static void log_frame_record(CGLContextObj context, uint64_t record_frame_index, bool census_attempted,
                             bool census_ok, int width, int height, int memcmp_equal, uint64_t crc,
                             uint64_t uptime_ns, GLenum gl_error_observed, bool flush_ok, int flush_cgl_error,
                             uint32_t control_generation, unsigned readback_failure_code) {
    int flush_is_main = 0;
#if defined(__APPLE__)
    flush_is_main = pthread_main_np();
#endif
    char line[384];
    int n = snprintf(
        line, sizeof(line),
        "F,%llu,%u,%u,%u,%u,%llu,%d,%d,%d,%d,%llu,%d,%llu,%u,%u,%d,%llu,%u,%u\n",
        (unsigned long long)record_frame_index, (unsigned)active_mode, (unsigned)active_scenario_id,
        (unsigned)pause_verified(), (unsigned)(census_attempted && census_ok),
        (unsigned long long)(uintptr_t)context, width, height, memcmp_equal,
        (unsigned)census_attempted, (unsigned long long)crc, flush_is_main,
        (unsigned long long)uptime_ns, (unsigned)gl_error_observed, (unsigned)flush_ok,
        flush_cgl_error, (unsigned long long)pthread_tid_self(), (unsigned)control_generation,
        readback_failure_code);
    if (n > 0 && (size_t)n < sizeof(line)) {
        log_line(line);
    }
}

static CGLError forward_flush_only(CGLContextObj context) {
    return CGLFlushDrawable(context);
}

static void log_thread_violation(uint64_t now, uint64_t owner_tid, uint64_t offender_tid) {
    atomic_store_explicit(&flush_thread_violation, true, memory_order_release);
    pthread_mutex_lock(&lock);
    if (!violation_logged) {
        violation_logged = true;
        char line[160];
        int n = snprintf(line, sizeof(line), "T,%llu,%llu,%llu\n", (unsigned long long)now,
                         (unsigned long long)owner_tid, (unsigned long long)offender_tid);
        if (n > 0 && (size_t)n < sizeof(line)) {
            log_line(line);
        }
    }
    pthread_mutex_unlock(&lock);
}

static bool ensure_flush_owner(uint64_t self_tid, uint64_t now) {
    if (self_tid == 0) {
        return false;
    }
    uint64_t owner = atomic_load_explicit(&flush_owner_tid, memory_order_acquire);
    if (owner == 0) {
        uint64_t expected = 0;
        if (atomic_compare_exchange_strong_explicit(&flush_owner_tid, &expected, self_tid,
                                                    memory_order_acq_rel, memory_order_acquire)) {
            return true;
        }
        owner = atomic_load_explicit(&flush_owner_tid, memory_order_acquire);
    }
    if (owner == self_tid) {
        return true;
    }
    log_thread_violation(now, owner, self_tid);
    return false;
}

static CGLError probed_flush(CGLContextObj context) {
    pthread_once(&setup_once, setup);
    uint64_t now = ns(CLOCK_UPTIME_RAW);
    uint64_t self_tid = pthread_tid_self();
    if (!ensure_flush_owner(self_tid, now)) {
        return forward_flush_only(context);
    }
    bool paused = pause_verified();

    eu4_r0_control_snapshot_t snap = {0};
    bool have_snap = control && eu4_r0_control_consume(control, &snap) && snap.valid;
    if (have_snap) {
        if (snap.generation != last_seen_generation || snap.mode != active_mode ||
            snap.scenario_id != active_scenario_id) {
            if (context != last_context || snap.scenario_id != active_scenario_id) {
                invalidate_reference();
            }
            last_seen_generation = snap.generation;
            active_mode = snap.mode;
            active_scenario_id = snap.scenario_id;
            if (snap.generation != capture_track_generation) {
                sync_capture_flags_for_generation(snap.generation);
            }
            maybe_ack_generation(&snap, now);
        }
    }

    uint64_t record_frame_index = frame_index + 1;
    bool census_attempted = false;
    bool census_ok = false;
    int width = 0;
    int height = 0;
    int memcmp_equal = -1;
    uint64_t crc = 0;
    GLenum gl_error_observed = GL_NO_ERROR;
    unsigned readback_failure_code = (unsigned)EU4_R0_READBACK_UNKNOWN_FAILURE;
    uint32_t control_generation = have_snap ? snap.generation : 0;

    if (have_snap && is_census_mode(active_mode) && paused && snap.generation != 0 &&
        snap.generation == last_acked_generation) {
        census_attempted = true;
        eu4_r0_readback_result_t rb = {0};
        census_ok = census_readback(context, &rb);
        width = rb.width;
        height = rb.height;
        crc = rb.crc64;
        gl_error_observed = rb.gl_error_observed;
        readback_failure_code = (unsigned)rb.failure_code;
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
            if (active_mode == EU4_R0_MODE_CENSUS_CAPTURE && !capture_succeeded_for_generation) {
                attempt_generation_capture(snap.generation, snap.scenario_id, record_frame_index, now, &rb);
            }
        }
    }

    frame_index = record_frame_index;
    CGLError flush_result = CGLFlushDrawable(context);
    int saved_errno = errno;
    bool flush_ok = flush_result == kCGLNoError;
    log_frame_record(context, record_frame_index, census_attempted, census_ok, width, height, memcmp_equal,
                     crc, now, gl_error_observed, flush_ok, (int)flush_result, control_generation,
                     readback_failure_code);
    snapshot_swap_stats(now, flush_ok && paused);
    errno = saved_errno;
    return flush_result;
}

__attribute__((used, section("__DATA,__interpose")))
static const struct {
    const void *replacement;
    const void *replacee;
} interpose_flush = {(const void *)probed_flush, (const void *)CGLFlushDrawable};
