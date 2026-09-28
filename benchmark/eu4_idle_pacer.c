// Version-pinned, opt-in paused/idle frame pacing for the x86_64 GOG EU IV.
// OFF always forwards CGLFlushDrawable unchanged. ON delays only after a
// successful swap, with the game paused and no input for three seconds.
#define GL_SILENCE_DEPRECATION 1
#include <OpenGL/OpenGL.h>
#include <CoreGraphics/CoreGraphics.h>
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

static pthread_once_t setup_once = PTHREAD_ONCE_INIT;
static volatile unsigned char *mode;
static int log_fd = -1;
static void *(*app_instance)(void);
static bool (*actually_paused)(void *);
static void *idler_vtable;
static uint64_t bucket_start, frames, eligible, slept_ns, rejected;
static uint64_t next_deadline;

static uint64_t clock_ns(clockid_t clock_id) {
    struct timespec value;
    if (clock_gettime(clock_id, &value)) return 0;
    return (uint64_t)value.tv_sec * 1000000000ull + (uint64_t)value.tv_nsec;
}

static void setup(void) {
    const char *path = getenv("EU4_PACE_LOG");
    if (path && path[0] == '/')
        log_fd = open(path, O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC, 0600);
    const char *control = getenv("EU4_PACE_CONTROL");
    if (control && control[0] == '/') {
        int fd = open(control, O_RDONLY);
        if (fd >= 0) {
            void *mapping = mmap(NULL, 4096, PROT_READ, MAP_SHARED, fd, 0);
            if (mapping != MAP_FAILED) mode = mapping;
            close(fd);
        }
    }
    app_instance = dlsym(RTLD_DEFAULT, "_ZN12CApplication14AccessInstanceEv");
    actually_paused = dlsym(RTLD_DEFAULT, "_ZNK10CGameSpeed16IsActuallyPausedEv");
    idler_vtable = dlsym(RTLD_DEFAULT, "_ZTV12CInGameIdler");
}

static bool pause_verified(void) {
    if (!app_instance || !actually_paused || !idler_vtable) return false;
    char *app = app_instance();
    if (!app) return false;
    char *idler = *(char **)(app + 0x40);
    if (!idler || *(void **)idler != (char *)idler_vtable + 16) return false;
    void *speed = *(void **)(idler + 0x408);
    return speed && actually_paused(speed);
}

static void snapshot(uint64_t now, unsigned selected_mode) {
    if (log_fd < 0) return;
    if (!bucket_start) bucket_start = now;
    if (now-bucket_start < 1000000000ull) return;
    char line[192];
    int n = snprintf(line, sizeof(line), "S,%llu,%llu,%u,%llu,%llu,%llu,%llu,%llu\n",
        (unsigned long long)now,
        (unsigned long long)clock_ns(CLOCK_REALTIME), selected_mode,
        (unsigned long long)(now-bucket_start),
        (unsigned long long)frames, (unsigned long long)eligible,
        (unsigned long long)slept_ns, (unsigned long long)rejected);
    if (n > 0 && (size_t)n < sizeof(line) && write(log_fd,line,(size_t)n) != n) {
        close(log_fd);
        log_fd=-1;
    }
    bucket_start=now; frames=eligible=slept_ns=rejected=0;
}

static CGLError paced_flush(CGLContextObj context) {
    CGLError result = CGLFlushDrawable(context);
    int saved_errno = errno;
    pthread_once(&setup_once,setup);
    unsigned selected_mode = log_fd >= 0 && mode ? *mode : 0;
    uint64_t now = clock_ns(CLOCK_UPTIME_RAW);
    if (selected_mode == 1 && result == kCGLNoError && pause_verified()) {
        const char *test_idle = getenv("EU4_PACE_TEST_IDLE");
        double idle = test_idle ? (strcmp(test_idle,"0") == 0 ? 0.0 : 10.0) :
            CGEventSourceSecondsSinceLastEventType(
                kCGEventSourceStateCombinedSessionState,kCGAnyInputEventType);
        if (isfinite(idle) && idle >= 3.0) {
            eligible++;
            if (next_deadline > now && next_deadline-now <= 16666667ull) {
                uint64_t remaining=next_deadline-now;
                struct timespec delay={(time_t)(remaining/1000000000ull),
                                       (long)(remaining%1000000000ull)};
                uint64_t before=now;
                (void)nanosleep(&delay,NULL);
                now=clock_ns(CLOCK_UPTIME_RAW);
                if (now>before) slept_ns+=now-before;
            }
            next_deadline=now+16666667ull;
        } else {
            next_deadline=0;
            rejected++;
        }
    } else {
        next_deadline=0;
        if (selected_mode == 1) rejected++;
    }
    frames++;
    snapshot(now,selected_mode);
    errno=saved_errno;
    return result;
}

__attribute__((used,section("__DATA,__interpose")))
static const struct { const void *replacement,*replacee; } interpose_flush = {
    (const void *)paced_flush,(const void *)CGLFlushDrawable
};
