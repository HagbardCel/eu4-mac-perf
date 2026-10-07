// R0 frame-evolution probe: single CGLFlushDrawable owner, read-only control mmap, log ACK (F13).
#define GL_SILENCE_DEPRECATION 1
#include "eu4_r0_control.h"

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
static const volatile eu4_r0_control_snapshot_t *control;

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
                control = (const volatile eu4_r0_control_snapshot_t *)mapping;
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

static bool read_control_snapshot(eu4_r0_control_snapshot_t *out) {
    if (!control || !out) {
        return false;
    }
    out->magic = control->magic;
    out->generation = control->generation;
    out->mode = control->mode;
    out->scenario_id = control->scenario_id;
    return out->magic == EU4_R0_CONTROL_MAGIC;
}

static uint64_t crc64_ecma(const uint8_t *data, size_t len) {
    uint64_t crc = 0;
    for (size_t i = 0; i < len; i++) {
        crc ^= (uint64_t)data[i];
        for (int bit = 0; bit < 8; bit++) {
            if (crc & 1ull) {
                crc = (crc >> 1) ^ 0xc96c5795d7870f42ull;
            } else {
                crc >>= 1;
            }
        }
    }
    return crc;
}

static void invalidate_reference(void) {
    reference_size = 0;
}

static bool ensure_readback_buffer(size_t needed) {
    if (readback_capacity >= needed) {
        return true;
    }
    uint8_t *grown = realloc(readback_buffer, needed);
    uint8_t *ref_grown = realloc(reference_buffer, needed);
    if (!grown || !ref_grown) {
        free(grown);
        free(ref_grown);
        return false;
    }
    readback_buffer = grown;
    reference_buffer = ref_grown;
    readback_capacity = needed;
    return true;
}

static bool census_readback(CGLContextObj context, int *width_out, int *height_out, uint64_t *crc_out) {
    if (!context || CGLGetCurrentContext() != context) {
        return false;
    }
    GLint viewport[4] = {0, 0, 0, 0};
    glGetIntegerv(GL_VIEWPORT, viewport);
    int width = viewport[2];
    int height = viewport[3];
    if (width <= 0 || height <= 0) {
        return false;
    }
    size_t row_bytes = (size_t)width * 4u;
    size_t total = row_bytes * (size_t)height;
    if (!ensure_readback_buffer(total)) {
        return false;
    }

    GLint prev_pack_buffer = 0;
    GLint prev_pack_alignment = 4;
    GLint prev_read_buffer = GL_BACK;
    glGetIntegerv(GL_PIXEL_PACK_BUFFER_BINDING, &prev_pack_buffer);
    glGetIntegerv(GL_PACK_ALIGNMENT, &prev_pack_alignment);
    glGetIntegerv(GL_READ_BUFFER, &prev_read_buffer);

    glBindBuffer(GL_PIXEL_PACK_BUFFER, 0);
    glPixelStorei(GL_PACK_ALIGNMENT, 4);
    glReadBuffer(GL_BACK);
    glReadPixels(0, 0, width, height, GL_RGBA, GL_UNSIGNED_BYTE, readback_buffer);

    glBindBuffer(GL_PIXEL_PACK_BUFFER, (GLuint)prev_pack_buffer);
    glPixelStorei(GL_PACK_ALIGNMENT, prev_pack_alignment);
    glReadBuffer((GLenum)prev_read_buffer);

    if (width_out) {
        *width_out = width;
    }
    if (height_out) {
        *height_out = height;
    }
    if (crc_out) {
        *crc_out = crc64_ecma(readback_buffer, total);
    }
    return true;
}

static void maybe_ack_generation(uint32_t generation, uint64_t now) {
    if (generation == 0 || generation == last_acked_generation) {
        return;
    }
    char line[128];
    int n = snprintf(line, sizeof(line), "ACK,%u,%llu,%llu\n", generation,
                     (unsigned long long)frame_index, (unsigned long long)now);
    if (n > 0 && (size_t)n < sizeof(line)) {
        log_line(line);
        last_acked_generation = generation;
    }
}

static void log_frame_record(CGLContextObj context, bool census_attempted, bool census_ok, int width,
                             int height, int memcmp_equal, uint64_t crc) {
    int flush_is_main = 0;
#if defined(__APPLE__)
    flush_is_main = pthread_main_np();
#endif
    char line[256];
    int n = snprintf(line, sizeof(line),
                     "F,%llu,%u,%u,%u,%u,%llu,%d,%d,%d,%d,%llu,%d\n",
                     (unsigned long long)frame_index, (unsigned)active_mode,
                     (unsigned)active_scenario_id, (unsigned)pause_verified(),
                     (unsigned)(census_attempted && census_ok), (unsigned long long)(uintptr_t)context,
                     width, height, memcmp_equal, (unsigned)census_attempted, (unsigned long long)crc,
                     flush_is_main);
    if (n > 0 && (size_t)n < sizeof(line)) {
        log_line(line);
    }
}

static CGLError probed_flush(CGLContextObj context) {
    pthread_once(&setup_once, setup);
    uint64_t now = ns(CLOCK_UPTIME_RAW);
    bool paused = pause_verified();

    eu4_r0_control_snapshot_t snap = {0};
    if (read_control_snapshot(&snap)) {
        if (snap.generation != last_seen_generation || snap.mode != active_mode ||
            snap.scenario_id != active_scenario_id) {
            if (context != last_context || snap.scenario_id != active_scenario_id) {
                invalidate_reference();
            }
            last_seen_generation = snap.generation;
            active_mode = snap.mode;
            active_scenario_id = snap.scenario_id;
            maybe_ack_generation(snap.generation, now);
        }
    }

    bool census_attempted = false;
    bool census_ok = false;
    int width = 0;
    int height = 0;
    int memcmp_equal = -1;
    uint64_t crc = 0;

    if (active_mode == EU4_R0_MODE_CENSUS && paused && snap.generation != 0 &&
        snap.generation == last_acked_generation) {
        census_attempted = true;
        census_ok = census_readback(context, &width, &height, &crc);
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
    log_frame_record(context, census_attempted, census_ok, width, height, memcmp_equal, crc);

    CGLError result = CGLFlushDrawable(context);
    int saved_errno = errno;
    snapshot_swap_stats(now, result == kCGLNoError && paused);
    errno = saved_errno;
    return result;
}

__attribute__((used, section("__DATA,__interpose")))
static const struct {
    const void *replacement;
    const void *replacee;
} interpose_flush = {(const void *)probed_flush, (const void *)CGLFlushDrawable};
