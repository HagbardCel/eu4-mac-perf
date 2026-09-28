// GOG EU IV 1.37.5: paused-state probe and narrowly scoped sampler-uniform test.
#define GL_SILENCE_DEPRECATION 1
#include <OpenGL/OpenGL.h>
#include <OpenGL/gl.h>
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-W#warnings"
#include <OpenGL/gl3.h>
#pragma clang diagnostic pop
#include <mach-o/dyld.h>
#include <dlfcn.h>
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

enum { TABLE_SIZE=16384, SET_ALL_RETURN=0x15ebc7a };
enum { UNSAFE_THREAD=2, UNSAFE_TABLE=4 };
typedef struct {
    uintptr_t program;
    GLint location, value;
    bool occupied, known;
} Entry;
static Entry table[TABLE_SIZE];
static volatile unsigned char *control;
static int log_fd=-1;
static uintptr_t image_base, current_program;
static _Atomic(CGLContextObj) render_context;
static _Atomic uint64_t render_thread;
static uint64_t bucket_start, swaps, paused_swaps;
static uint64_t attempted, forwarded, suppressed, context_switches;
static _Atomic unsigned current_mode;
static _Atomic unsigned unsafe_flags;
static void *(*app_instance)(void);
static bool (*actually_paused)(void *);
static void *idler_vtable;
static pthread_once_t pause_once=PTHREAD_ONCE_INIT;
static pthread_mutex_t lock=PTHREAD_MUTEX_INITIALIZER;

static uint64_t clock_ns(clockid_t id) {
    struct timespec t;
    return clock_gettime(id,&t) ? 0 : (uint64_t)t.tv_sec*1000000000ull+t.tv_nsec;
}
static uint64_t thread_id(void) {
    uint64_t value=0;
    (void)pthread_threadid_np(NULL,&value);
    return value;
}
static void setup(void) {
    image_base=(uintptr_t)_dyld_get_image_header(0);
    const char *path=getenv("EU4_SAMPLER_LOG");
    if (path && *path=='/') log_fd=open(path,O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC,0600);
    path=getenv("EU4_SAMPLER_CONTROL");
    if (path && *path=='/') {
        int fd=open(path,O_RDONLY);
        if (fd>=0) {
            void *memory=mmap(NULL,4096,PROT_READ,MAP_SHARED,fd,0);
            if (memory!=MAP_FAILED) control=memory;
            close(fd);
        }
    }
}
__attribute__((constructor)) static void initialize(void) { setup(); }
static void resolve_pause(void) {
    app_instance=dlsym(RTLD_DEFAULT,"_ZN12CApplication14AccessInstanceEv");
    actually_paused=dlsym(RTLD_DEFAULT,"_ZNK10CGameSpeed16IsActuallyPausedEv");
    idler_vtable=dlsym(RTLD_DEFAULT,"_ZTV12CInGameIdler");
}
static bool paused_in_game(void) {
    pthread_once(&pause_once,resolve_pause);
    if (!app_instance || !actually_paused || !idler_vtable) return false;
    char *app=app_instance();
    if (!app) return false;
    char *idler=*(char **)(app+0x40);
    if (!idler || *(void **)idler!=(char *)idler_vtable+16) return false;
    void *speed=*(void **)(idler+0x408);
    return speed && actually_paused(speed);
}
static unsigned requested_mode(void) { return control && *control==1 && log_fd>=0 ? 1u : 0u; }
static void change_mode(unsigned mode) {
    if (mode==atomic_load(&current_mode)) return;
    memset(table,0,sizeof(table));
    current_program=0;
    atomic_store(&current_mode,mode);
    atomic_store(&unsafe_flags,0);
    if (mode) atomic_store(&render_context,CGLGetCurrentContext());
    attempted=forwarded=suppressed=context_switches=0;
    bucket_start=swaps=paused_swaps=0;
}
static Entry *entry(uintptr_t program,GLint location) {
    uint32_t hash=(uint32_t)program*2654435761u ^ (uint32_t)(program>>32)*2246822519u ^
                  (uint32_t)location*3266489917u;
    for (unsigned i=0;i<TABLE_SIZE;i++) {
        Entry *item=&table[(hash+i)&(TABLE_SIZE-1)];
        if (!item->occupied || (item->program==program && item->location==location)) {
            if (!item->occupied) {
                item->program=program; item->location=location; item->occupied=true;
            }
            return item;
        }
    }
    atomic_fetch_or(&unsafe_flags,UNSAFE_TABLE);
    return NULL;
}
static void invalidate(GLint location) {
    if (atomic_load(&current_mode) && current_program && location>=0) {
        Entry *item=entry(current_program,location);
        if (item) item->known=false;
    }
}
static bool safe_render_thread(void) {
    uint64_t owner=atomic_load(&render_thread);
    if (!owner) return false;
    if (thread_id()!=owner) {
        if (atomic_load(&current_mode)) atomic_fetch_or(&unsafe_flags,UNSAFE_THREAD);
        return false;
    }
    return !atomic_load(&current_mode) || !atomic_load(&unsafe_flags);
}
static void tracked_use_program(GLhandleARB program) {
    if (safe_render_thread()) current_program=(uintptr_t)program;
    glUseProgramObjectARB(program);
}
static void tracked_uniform1i(GLint location,GLint value) {
    bool safe=safe_render_thread();
    if (!safe) {glUniform1i(location,value);return;}
    unsigned mode=requested_mode();
    change_mode(mode);
#ifdef EU4_SAMPLER_TEST_CALLSITE
    bool target=true;
#else
    bool target=(uintptr_t)__builtin_return_address(0)==image_base+SET_ALL_RETURN;
#endif
    if (!target || location<0 || value<0 || value>15) {
        invalidate(location);
        glUniform1i(location,value);
        return;
    }
    attempted++;
    if (mode && current_program) {
        Entry *item=entry(current_program,location);
        if (item && item->known && item->value==value) {
            suppressed++;
            return;
        }
        if (item) {item->value=value;item->known=true;}
    }
    forwarded++;
    glUniform1i(location,value);
}
static void tracked_uniform4fv(GLint location,GLsizei count,const GLfloat *values) {
    if (safe_render_thread() && count>0) {
        if (count>64 || location>INT32_MAX-count) memset(table,0,sizeof(table));
        else for (GLsizei i=0;i<count;i++) invalidate(location+i);
    }
    glUniform4fvARB(location,count,values);
}
static void tracked_link(GLhandleARB program) {
    glLinkProgramARB(program);
    if (safe_render_thread()) memset(table,0,sizeof(table));
}
static void tracked_delete(GLhandleARB object) {
    glDeleteObjectARB(object);
    if (safe_render_thread()) {
        memset(table,0,sizeof(table));
        if (current_program==(uintptr_t)object) current_program=0;
    }
}
static CGLError tracked_set_context(CGLContextObj context) {
    CGLError result=CGLSetCurrentContext(context);
    CGLContextObj owner=atomic_load(&render_context);
    if (result==kCGLNoError && owner && context && context!=owner) {
        uint64_t thread=atomic_load(&render_thread);
        if (thread && thread!=thread_id()) {
            if (atomic_load(&current_mode)) atomic_fetch_or(&unsafe_flags,UNSAFE_THREAD);
        }
        else {
            memset(table,0,sizeof(table));
            current_program=0;
            context_switches++;
            atomic_store(&render_context,context);
        }
    }
    return result;
}
static CGLError tracked_flush(CGLContextObj context) {
    CGLError result=CGLFlushDrawable(context);
    uint64_t tid=thread_id();
    uint64_t empty=0;
    if (atomic_compare_exchange_strong(&render_thread,&empty,tid))
        atomic_store(&render_context,context);
    unsigned mode=requested_mode();
    if (!mode) {
        atomic_store(&render_thread,tid);
        atomic_store(&render_context,context);
    } else if (atomic_load(&render_thread)!=tid) {
        atomic_fetch_or(&unsafe_flags,UNSAFE_THREAD);
        return result;
    }
    CGLContextObj owner=atomic_load(&render_context);
    if (owner && owner!=context) {
        memset(table,0,sizeof(table));
        current_program=0;
        context_switches++;
        atomic_store(&render_context,context);
    }
    uint64_t now=clock_ns(CLOCK_UPTIME_RAW);
    pthread_mutex_lock(&lock);
    change_mode(mode);
    if (!bucket_start) bucket_start=now;
    swaps++;
    if (result==kCGLNoError && paused_in_game()) paused_swaps++;
    if (log_fd>=0 && now-bucket_start>=1000000000ull) {
        char line[256];
        uint64_t wall=clock_ns(CLOCK_REALTIME);
        int n=snprintf(line,sizeof(line),"S,%llu,%llu,%llu,%llu,%llu\n"
            "U,%llu,%llu,%llu,%u,%llu,%llu,%llu,%u,%llu\n",
            (unsigned long long)now,(unsigned long long)wall,
            (unsigned long long)(now-bucket_start),(unsigned long long)swaps,
            (unsigned long long)paused_swaps,
            (unsigned long long)now,(unsigned long long)wall,
            (unsigned long long)(now-bucket_start),mode,
            (unsigned long long)attempted,(unsigned long long)forwarded,
            (unsigned long long)suppressed,atomic_load(&unsafe_flags),
            (unsigned long long)context_switches);
        if (n>0 && (size_t)n<sizeof(line)) (void)write(log_fd,line,(size_t)n);
        bucket_start=now;swaps=paused_swaps=attempted=forwarded=suppressed=context_switches=0;
    }
    pthread_mutex_unlock(&lock);
    return result;
}
static void *tracked_dlsym(void *handle,const char *symbol) {
    void *original=dlsym(handle,symbol);
    if (!original || !symbol) return original;
    if (!strcmp(symbol,"glUseProgramObjectARB")) return (void *)tracked_use_program;
    if (!strcmp(symbol,"glUniform1i")) return (void *)tracked_uniform1i;
    if (!strcmp(symbol,"glUniform4fvARB")) return (void *)tracked_uniform4fv;
    if (!strcmp(symbol,"glLinkProgramARB")) return (void *)tracked_link;
    if (!strcmp(symbol,"glDeleteObjectARB")) return (void *)tracked_delete;
    return original;
}
#define INTERPOSE(name,replacement) {(const void *)(replacement),(const void *)(name)}
__attribute__((used,section("__DATA,__interpose")))
static const struct {const void *replacement,*replacee;} interposes[]={
    INTERPOSE(dlsym,tracked_dlsym),
    INTERPOSE(CGLFlushDrawable,tracked_flush),
    INTERPOSE(CGLSetCurrentContext,tracked_set_context),
};
