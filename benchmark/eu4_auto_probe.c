// Passive, version-pinned readiness and swap probe for GOG EU IV 1.37.5.
#define GL_SILENCE_DEPRECATION 1
#include <OpenGL/OpenGL.h>
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

static pthread_once_t setup_once=PTHREAD_ONCE_INIT;
static pthread_mutex_t lock=PTHREAD_MUTEX_INITIALIZER;
static int log_fd=-1;
static void *(*app_instance)(void);
static bool (*actually_paused)(void *);
static void *idler_vtable;
static uint64_t start_ns,swaps,paused_swaps;

static uint64_t ns(clockid_t clock_id) {
    struct timespec t;
    return clock_gettime(clock_id,&t) ? 0 : (uint64_t)t.tv_sec*1000000000ull+t.tv_nsec;
}
static void setup(void) {
    const char *path=getenv("EU4_AUTO_PROBE_LOG");
    if (path && path[0]=='/') log_fd=open(path,O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC,0600);
    app_instance=dlsym(RTLD_DEFAULT,"_ZN12CApplication14AccessInstanceEv");
    actually_paused=dlsym(RTLD_DEFAULT,"_ZNK10CGameSpeed16IsActuallyPausedEv");
    idler_vtable=dlsym(RTLD_DEFAULT,"_ZTV12CInGameIdler");
}
static bool paused_in_game(void) {
    if (!app_instance || !actually_paused || !idler_vtable) return false;
    char *app=app_instance();
    if (!app) return false;
    char *idler=*(char **)(app+0x40);
    if (!idler || *(void **)idler!=(char *)idler_vtable+16) return false;
    void *speed=*(void **)(idler+0x408);
    return speed && actually_paused(speed);
}
static CGLError probed_flush(CGLContextObj context) {
    CGLError result=CGLFlushDrawable(context);
    int saved_errno=errno;
    pthread_once(&setup_once,setup);
    if (log_fd>=0) {
        uint64_t now=ns(CLOCK_UPTIME_RAW);
        bool paused=result==kCGLNoError && paused_in_game();
        pthread_mutex_lock(&lock);
        if (!start_ns) start_ns=now;
        swaps++;
        if (paused) paused_swaps++;
        if (now-start_ns>=1000000000ull) {
            char line[160];
            int n=snprintf(line,sizeof(line),"S,%llu,%llu,%llu,%llu,%llu\n",
                (unsigned long long)now,(unsigned long long)ns(CLOCK_REALTIME),
                (unsigned long long)(now-start_ns),
                (unsigned long long)swaps,(unsigned long long)paused_swaps);
            if (n>0 && (size_t)n<sizeof(line)) (void)write(log_fd,line,(size_t)n);
            start_ns=now;swaps=paused_swaps=0;
        }
        pthread_mutex_unlock(&lock);
    }
    errno=saved_errno;
    return result;
}
__attribute__((used,section("__DATA,__interpose")))
static const struct {const void *replacement,*replacee;} interpose={
    (const void *)probed_flush,(const void *)CGLFlushDrawable
};
