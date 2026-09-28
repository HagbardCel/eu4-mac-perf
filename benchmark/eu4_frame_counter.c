// Passive OpenGL swap-call counter for the x86_64 GOG EU IV process.
// Loaded only when EU4_FRAME_LOG and DYLD_INSERT_LIBRARIES are set at launch.
#define GL_SILENCE_DEPRECATION 1
#include <OpenGL/OpenGL.h>
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

static pthread_once_t log_once = PTHREAD_ONCE_INIT;
static pthread_mutex_t log_lock = PTHREAD_MUTEX_INITIALIZER;
static int log_fd = -1;
static int status_fd = -1;
static int mp_requested = 0;
static CGLContextObj last_mp_context = NULL;
static uint64_t window_start_ns = 0;
static uint64_t swaps = 0;

static uint64_t monotonic_ns(void) {
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now) != 0) {
        return 0;
    }
    return (uint64_t)now.tv_sec * 1000000000ull + (uint64_t)now.tv_nsec;
}

static void open_log(void) {
    const char *path = getenv("EU4_FRAME_LOG");
    if (path == NULL || path[0] != '/') {
        return;
    }
    log_fd = open(path, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
    if (log_fd >= 0) {
        const char header[] = "interval_ns,swap_calls\n";
        (void)write(log_fd, header, sizeof(header) - 1);
    }
    const char *mp = getenv("EU4_GL_MULTITHREADED");
    const char *status = getenv("EU4_GL_STATUS_LOG");
    if (log_fd >= 0 && mp != NULL && strcmp(mp, "1") == 0 &&
            status != NULL && status[0] == '/') {
        status_fd = open(status, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
        if (status_fd >= 0) {
            mp_requested = 1;
            const char header[] = "query_before,before,enable_error,query_after,after\n";
            (void)write(status_fd, header, sizeof(header) - 1);
        }
    }
}

static void maybe_enable_multithreaded(CGLContextObj context) {
    if (!mp_requested || context == NULL) {
        return;
    }
    pthread_mutex_lock(&log_lock);
    if (context == last_mp_context) {
        pthread_mutex_unlock(&log_lock);
        return;
    }
    last_mp_context = context;
    pthread_mutex_unlock(&log_lock);

    GLint before = -1, after = -1;
    CGLError query_before = CGLIsEnabled(context, kCGLCEMPEngine, &before);
    CGLError enable_error = query_before;
    if (query_before == kCGLNoError && before == 0) {
        enable_error = CGLEnable(context, kCGLCEMPEngine);
    }
    CGLError query_after = CGLIsEnabled(context, kCGLCEMPEngine, &after);
    char row[128];
    int length = snprintf(row, sizeof(row), "%d,%d,%d,%d,%d\n",
                          (int)query_before, (int)before, (int)enable_error,
                          (int)query_after, (int)after);
    if (length > 0 && (size_t)length < sizeof(row)) {
        (void)write(status_fd, row, (size_t)length);
    }
}

static CGLError counted_flush(CGLContextObj context) {
    CGLError result = CGLFlushDrawable(context);
    int saved_errno = errno;
    pthread_once(&log_once, open_log);
    maybe_enable_multithreaded(context);
    if (log_fd >= 0) {
        uint64_t now = monotonic_ns();
        if (now != 0) {
            pthread_mutex_lock(&log_lock);
            if (window_start_ns == 0) {
                window_start_ns = now;
            }
            swaps++;
            uint64_t interval = now - window_start_ns;
            if (interval >= 1000000000ull) {
                char row[96];
                int length = snprintf(row, sizeof(row), "%llu,%llu\n",
                                      (unsigned long long)interval,
                                      (unsigned long long)swaps);
                if (length > 0 && (size_t)length < sizeof(row)) {
                    (void)write(log_fd, row, (size_t)length);
                }
                window_start_ns = now;
                swaps = 0;
            }
            pthread_mutex_unlock(&log_lock);
        }
    }
    errno = saved_errno;
    return result;
}

__attribute__((used, section("__DATA,__interpose")))
static const struct {
    const void *replacement;
    const void *replacee;
} interpose_flush = {(const void *)counted_flush, (const void *)CGLFlushDrawable};
