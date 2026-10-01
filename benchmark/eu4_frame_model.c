// Runtime profiler for the pinned GOG EU IV 1.37.5 x86-64 executable.
// Hooks are installed only after the generated inventory and prologues match.
#define GL_SILENCE_DEPRECATION 1
#include <OpenGL/OpenGL.h>
#include <OpenGL/gl3.h>
#include <CoreGraphics/CoreGraphics.h>
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <libkern/OSCacheControl.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
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
#include "eu4_frame_model_sites.h"
#ifndef GL_QUAD_STRIP
#define GL_QUAD_STRIP 0x0008
#endif
#ifndef GL_POLYGON
#define GL_POLYGON 0x0009
#endif
#ifndef GL_LINES_ADJACENCY
#define GL_LINES_ADJACENCY 0x000A
#endif
#ifndef GL_LINE_STRIP_ADJACENCY
#define GL_LINE_STRIP_ADJACENCY 0x000B
#endif
#ifndef GL_TRIANGLES_ADJACENCY
#define GL_TRIANGLES_ADJACENCY 0x000C
#endif
#ifndef GL_TRIANGLE_STRIP_ADJACENCY
#define GL_TRIANGLE_STRIP_ADJACENCY 0x000D
#endif

enum { OFF, PROFILE, SKIP_RENDER, DROP_DRAWS, RASTER_SUPPRESS, CADENCE_30, CADENCE_15 };
enum { HOOK_UPDATE, HOOK_IDLE, HOOK_RENDER, HOOK_MAP, HOOK_PRESENT_SCENE,
       HOOK_ADD_BUCKET, HOOK_APPEND, HOOK_GL_DRAW, HOOK_GL_UPLOAD,
       HOOK_GL_UNIFORM, HOOK_SWAP, HOOK_GL_STATE, HOOK_COUNT };
typedef struct {
    uint32_t version, mode, phase, flags, detail_frames, reserved;
    uint64_t cadence_ns, generation;
    _Atomic uint64_t ack_generation, hook_failures;
} Control;
typedef struct {
    uint64_t update_id, render_id, present_id, phase, thread_id;
    uint64_t wall_ns, cpu_ns, update_wall_ns, update_cpu_ns;
    uint64_t draws, indices, triangles, draw_wall_ns, draw_cpu_ns;
    uint64_t draw_timed_samples;
    uint64_t buffer_calls, buffer_bytes, buffer_storage_bytes, buffer_upload_bytes;
    uint64_t texture_calls, texture_bytes, state_calls, texture_binds, buffer_binds;
    uint64_t program_switches;
    uint64_t uniform_calls, uniform_bytes, bucket_calls, append_calls;
    uint64_t idle_wall_ns, idle_cpu_ns, render_wall_ns, render_cpu_ns;
    uint64_t map_wall_ns, map_cpu_ns, present_wall_ns, present_cpu_ns;
    uint64_t wait_ns, wait_cpu_ns, sleep_ns, flags, forwarded_draws, suppressed_draws;
} Frame;
typedef struct { _Atomic uint64_t ready; Frame frame; } FrameSlot;
typedef struct { _Atomic uint64_t ready; char kind; uint64_t a,b,c,d,e,f,g; } DetailSlot;
#define FRAME_CAPACITY 32768u
#define DETAIL_CAPACITY 262144u
static FrameSlot frames[FRAME_CAPACITY];
static DetailSlot details[DETAIL_CAPACITY];
static _Atomic uint64_t frame_head, frame_tail;
static _Atomic uint64_t detail_head, detail_tail;
static _Atomic uint64_t call_counts[HOOK_COUNT];
static _Atomic uint64_t call_wall_ns[HOOK_COUNT], call_cpu_ns[HOOK_COUNT];
static _Thread_local Frame current_frame;
static _Thread_local GLuint active_program;
static _Thread_local GLuint bound_array_buffer, bound_element_buffer;
static _Thread_local bool detail_active;
static _Thread_local uint32_t draw_sample_seq;
typedef struct { bool used; uint64_t site,render_id; GLuint program; GLint location;
                 size_t bytes; unsigned char data[256]; } UniformSnapshot;
static _Thread_local UniformSnapshot uniform_snapshots[256];
static _Thread_local unsigned uniform_snapshot_evict;
typedef struct { CGLContextObj context; GLboolean enabled_before; } RasterContext;
static _Thread_local RasterContext raster_contexts[64];
static _Thread_local unsigned raster_context_count;
static _Thread_local bool raster_active;
typedef struct { GLuint q[4]; bool pending,has_map; uint64_t render_id,phase; } GpuSlot;
static _Thread_local CGLContextObj gpu_context;
static _Thread_local GpuSlot gpu_slots[8];
static _Thread_local unsigned gpu_cursor;
static _Thread_local int gpu_active_slot=-1;
static _Thread_local bool gpu_ready;
static _Thread_local bool gpu_attempted;
static _Thread_local bool gpu_map_started;
static _Thread_local bool gpu_context_changed;
static bool extension_has(const char *extensions,const char *name) {
    if(!extensions || !name) return false;
    size_t length=strlen(name);
    for(const char *p=extensions;(p=strstr(p,name))!=NULL;p+=length)
        if((p==extensions || p[-1]==' ') && (p[length]==' ' || p[length]=='\0')) return true;
    return false;
}
static void (*gpu_gen_queries)(GLsizei,GLuint *);
static void (*gpu_query_counter)(GLuint,GLenum);
static void (*gpu_get_query_iv)(GLuint,GLenum,GLint *);
static void (*gpu_get_query_u64)(GLuint,GLenum,GLuint64 *);
enum { GL_TIMESTAMP_MODEL=0x8E28, GL_QUERY_RESULT_MODEL=0x8866,
       GL_QUERY_AVAILABLE_MODEL=0x8867 };
static Control *control;
static int log_fd=-1;
static int auto_log_fd=-1;
static pthread_mutex_t auto_probe_lock=PTHREAD_MUTEX_INITIALIZER;
static uint64_t auto_probe_start,auto_probe_swaps,auto_probe_paused_swaps;
static void *(*auto_app_instance)(void);
static bool (*auto_actually_paused)(void *);
static void *auto_idler_vtable;
static uintptr_t image_base;
static uint64_t next_deadline;
static pthread_t writer_thread;
static _Atomic int writer_running;
static bool writer_started;

static uint64_t now_ns(clockid_t id) {
    struct timespec t;
    if (clock_gettime(id,&t)) return 0;
    return (uint64_t)t.tv_sec*1000000000ull+(uint64_t)t.tv_nsec;
}
static uint64_t tid(void) { uint64_t v=0; (void)pthread_threadid_np(NULL,&v); return v; }
static bool auto_paused_in_game(void) {
    if(!auto_app_instance || !auto_actually_paused || !auto_idler_vtable) return false;
    char *app=auto_app_instance(); if(!app) return false;
    char *idler=*(char **)(app+0x40);
    if(!idler || *(void **)idler!=(char *)auto_idler_vtable+16) return false;
    void *speed=*(void **)(idler+0x408);
    return speed && auto_actually_paused(speed);
}
static void auto_probe_swap(CGLError result) {
    if(auto_log_fd<0) return;
    int saved_errno=errno;
    uint64_t now=now_ns(CLOCK_UPTIME_RAW); bool paused=result==kCGLNoError && auto_paused_in_game();
    pthread_mutex_lock(&auto_probe_lock);
    if(!auto_probe_start) auto_probe_start=now;
    auto_probe_swaps++; if(paused) auto_probe_paused_swaps++;
    if(now-auto_probe_start>=1000000000ull) {
        char line[160]; uint64_t wall=now_ns(CLOCK_REALTIME);
        int n=snprintf(line,sizeof(line),"S,%llu,%llu,%llu,%llu,%llu\n",
            (unsigned long long)now,(unsigned long long)wall,
            (unsigned long long)(now-auto_probe_start),
            (unsigned long long)auto_probe_swaps,(unsigned long long)auto_probe_paused_swaps);
        if(n>0 && (size_t)n<sizeof(line)) (void)write(auto_log_fd,line,(size_t)n);
        auto_probe_start=now; auto_probe_swaps=auto_probe_paused_swaps=0;
    }
    pthread_mutex_unlock(&auto_probe_lock); errno=saved_errno;
}
static uint64_t hash_bytes(const void *data,size_t n) {
    const unsigned char *p=data; uint64_t h=1469598103934665603ull;
    for(size_t i=0;i<n;i++) h=(h^p[i])*1099511628211ull;
    return h;
}
static uint64_t estimated_triangles(GLenum mode,GLsizei count,uint64_t instances) {
    uint64_t n=count>0?(uint64_t)count:0,per=0;
    switch(mode) {
        case GL_TRIANGLES: per=n/3; break;
        case GL_TRIANGLE_STRIP: case GL_TRIANGLE_FAN: per=n>2?n-2:0; break;
        case GL_POLYGON: per=n>2?n-2:0; break;
        case GL_QUADS: per=(n/4)*2; break;
        case GL_QUAD_STRIP: per=n>=4?((n/2)-1)*2:0; break;
        case GL_TRIANGLES_ADJACENCY: per=n/6; break;
        case GL_TRIANGLE_STRIP_ADJACENCY: per=n>=6?(n-4)/2:0; break;
        case GL_POINTS: case GL_LINES: case GL_LINE_STRIP: case GL_LINE_LOOP:
        case GL_LINES_ADJACENCY: case GL_LINE_STRIP_ADJACENCY: return 0;
        default: current_frame.flags|=128; return 0;
    }
    return per*instances;
}
static void event(unsigned id,uint64_t wall,uint64_t cpu) {
    atomic_fetch_add_explicit(&call_counts[id],1,memory_order_relaxed);
    atomic_fetch_add_explicit(&call_wall_ns[id],wall,memory_order_relaxed);
    atomic_fetch_add_explicit(&call_cpu_ns[id],cpu,memory_order_relaxed);
}
static void publish_frame(Frame *f) {
    uint64_t h=atomic_load_explicit(&frame_head,memory_order_relaxed);
    uint64_t t=atomic_load_explicit(&frame_tail,memory_order_acquire);
    if(h-t>=FRAME_CAPACITY) { f->flags|=1; return; }
    FrameSlot *s=&frames[h%FRAME_CAPACITY]; s->frame=*f;
    atomic_store_explicit(&s->ready,h+1,memory_order_release);
    atomic_store_explicit(&frame_head,h+1,memory_order_release);
}
static void *writer(void *unused) {
    (void)unused;
    while(atomic_load(&writer_running) ||
          atomic_load_explicit(&frame_tail,memory_order_relaxed)<
          atomic_load_explicit(&frame_head,memory_order_acquire) ||
          atomic_load_explicit(&detail_tail,memory_order_relaxed)<
          atomic_load_explicit(&detail_head,memory_order_acquire)) {
        uint64_t t=atomic_load_explicit(&frame_tail,memory_order_relaxed);
        uint64_t h=atomic_load_explicit(&frame_head,memory_order_acquire);
        while(t<h) {
            FrameSlot *s=&frames[t%FRAME_CAPACITY];
            if(atomic_load_explicit(&s->ready,memory_order_acquire)!=t+1) break;
            Frame f=s->frame;
            char line[768];
            unsigned long long values[]={f.update_id,f.render_id,f.present_id,f.phase,
                f.thread_id,f.wall_ns,f.cpu_ns,f.update_wall_ns,f.update_cpu_ns,
                f.draws,f.indices,f.triangles,f.draw_wall_ns,f.draw_cpu_ns,f.draw_timed_samples,
                f.buffer_calls,f.buffer_bytes,f.buffer_storage_bytes,f.buffer_upload_bytes,
                f.texture_calls,f.texture_bytes,f.state_calls,f.texture_binds,
                f.buffer_binds,f.program_switches,f.uniform_calls,f.uniform_bytes,
                f.bucket_calls,f.append_calls,f.idle_wall_ns,f.idle_cpu_ns,
                f.render_wall_ns,f.render_cpu_ns,f.map_wall_ns,f.map_cpu_ns,
                f.present_wall_ns,f.present_cpu_ns,f.wait_ns,f.wait_cpu_ns,f.sleep_ns,f.flags,
                f.forwarded_draws,f.suppressed_draws};
            size_t used=(size_t)snprintf(line,sizeof(line),"F");
            for(size_t i=0;i<sizeof(values)/sizeof(values[0]) && used<sizeof(line);i++)
                used+=(size_t)snprintf(line+used,sizeof(line)-used,",%llu",values[i]);
            int n=(int)used;
            if(used<sizeof(line)) { line[used++]='\n'; line[used]='\0'; n=(int)used; }
            if(n>0 && (size_t)n<sizeof(line)) (void)write(log_fd,line,(size_t)n);
            atomic_store_explicit(&s->ready,0,memory_order_relaxed); t++;
            atomic_store_explicit(&frame_tail,t,memory_order_release);
        }
        uint64_t dt=atomic_load_explicit(&detail_tail,memory_order_relaxed);
        uint64_t dh=atomic_load_explicit(&detail_head,memory_order_acquire);
        while(dt<dh) {
            DetailSlot *s=&details[dt%DETAIL_CAPACITY];
            if(atomic_load_explicit(&s->ready,memory_order_acquire)!=dt+1) break;
            char line[256];
            int n=snprintf(line,sizeof(line),"%c,%llu,%llu,%llu,%llu,%llu,%llu,%llu\n",s->kind,
                (unsigned long long)s->a,(unsigned long long)s->b,
                (unsigned long long)s->c,(unsigned long long)s->d,
                (unsigned long long)s->e,(unsigned long long)s->f,
                (unsigned long long)s->g);
            if(n>0 && (size_t)n<sizeof(line)) (void)write(log_fd,line,(size_t)n);
            atomic_store_explicit(&s->ready,0,memory_order_relaxed); dt++;
            atomic_store_explicit(&detail_tail,dt,memory_order_release);
        }
        struct timespec nap={0,1000000}; (void)nanosleep(&nap,NULL);
    }
    return NULL;
}
static void count_line(const char *kind,uint64_t a,uint64_t b,uint64_t c) {
    if(log_fd<0) return;
    char line[160]; int n=snprintf(line,sizeof(line),"%s,%llu,%llu,%llu\n",kind,
        (unsigned long long)a,(unsigned long long)b,(unsigned long long)c);
    if(n>0 && (size_t)n<sizeof(line)) (void)write(log_fd,line,(size_t)n);
}
static void hook_line(unsigned id) {
    if(log_fd<0) return;
    char line[160];
    int n=snprintf(line,sizeof(line),"C,%u,%llu,%llu,%llu\n",id,
        (unsigned long long)atomic_load(&call_counts[id]),
        (unsigned long long)atomic_load(&call_wall_ns[id]),
        (unsigned long long)atomic_load(&call_cpu_ns[id]));
    if(n>0 && (size_t)n<sizeof(line)) (void)write(log_fd,line,(size_t)n);
}
static void detail_line(const char *kind,uint64_t a,uint64_t b,uint64_t c,uint64_t d) {
    if(log_fd<0 || !kind || !kind[0]) return;
    uint64_t h=atomic_load_explicit(&detail_head,memory_order_relaxed);
    uint64_t t=atomic_load_explicit(&detail_tail,memory_order_acquire);
    if(h-t>=DETAIL_CAPACITY) { atomic_fetch_add(&control->hook_failures,1); return; }
    DetailSlot *s=&details[h%DETAIL_CAPACITY]; s->kind=kind[0]; s->a=a;s->b=b;s->c=c;s->d=d;s->e=0;s->f=0;s->g=0;
    atomic_store_explicit(&s->ready,h+1,memory_order_release);
    atomic_store_explicit(&detail_head,h+1,memory_order_release);
}
static void detail_line5(const char *kind,uint64_t a,uint64_t b,uint64_t c,uint64_t d,uint64_t e) {
    if(log_fd<0 || !kind || !kind[0]) return;
    uint64_t h=atomic_load_explicit(&detail_head,memory_order_relaxed);
    uint64_t t=atomic_load_explicit(&detail_tail,memory_order_acquire);
    if(h-t>=DETAIL_CAPACITY) { atomic_fetch_add(&control->hook_failures,1); return; }
    DetailSlot *s=&details[h%DETAIL_CAPACITY]; s->kind=kind[0]; s->a=a;s->b=b;s->c=c;s->d=d;s->e=e;s->f=0;s->g=0;
    atomic_store_explicit(&s->ready,h+1,memory_order_release);
    atomic_store_explicit(&detail_head,h+1,memory_order_release);
}
static void detail_line7(const char *kind,uint64_t a,uint64_t b,uint64_t c,uint64_t d,
                         uint64_t e,uint64_t f,uint64_t g) {
    if(log_fd<0 || !kind || !kind[0]) return;
    uint64_t h=atomic_load_explicit(&detail_head,memory_order_relaxed);
    uint64_t t=atomic_load_explicit(&detail_tail,memory_order_acquire);
    if(h-t>=DETAIL_CAPACITY) { atomic_fetch_add(&control->hook_failures,1); return; }
    DetailSlot *s=&details[h%DETAIL_CAPACITY]; s->kind=kind[0];
    s->a=a;s->b=b;s->c=c;s->d=d;s->e=e;s->f=f;s->g=g;
    atomic_store_explicit(&s->ready,h+1,memory_order_release);
    atomic_store_explicit(&detail_head,h+1,memory_order_release);
}
static void gpu_initialize(void) {
    if(gpu_context_changed) return;
    CGLContextObj ctx=CGLGetCurrentContext();
    if(!ctx) return;
    if(gpu_context && gpu_context!=ctx) { gpu_ready=false; gpu_active_slot=-1; return; }
    if(gpu_ready || gpu_attempted) return;
    gpu_attempted=true;
    const GLubyte *version=glGetString(GL_VERSION);
    int major=0,minor=0;
    if(version) (void)sscanf((const char *)version,"%d.%d",&major,&minor);
    const char *extensions=NULL;
    if(major<3 || (major==3 && minor<3))
        extensions=(const char *)glGetString(GL_EXTENSIONS);
    if((major<3 || (major==3 && minor<3)) &&
       !extension_has(extensions,"GL_ARB_timer_query") &&
       !extension_has(extensions,"GL_EXT_timer_query")) return;
    gpu_context=ctx;
    gpu_gen_queries=dlsym(RTLD_DEFAULT,"glGenQueries");
    gpu_query_counter=dlsym(RTLD_DEFAULT,"glQueryCounter");
    gpu_get_query_iv=dlsym(RTLD_DEFAULT,"glGetQueryObjectiv");
    gpu_get_query_u64=dlsym(RTLD_DEFAULT,"glGetQueryObjectui64v");
    if(!gpu_query_counter) gpu_query_counter=dlsym(RTLD_DEFAULT,"glQueryCounterEXT");
    if(!gpu_get_query_iv) gpu_get_query_iv=dlsym(RTLD_DEFAULT,"glGetQueryObjectivEXT");
    if(!gpu_get_query_u64) gpu_get_query_u64=dlsym(RTLD_DEFAULT,"glGetQueryObjectui64vEXT");
    if(!gpu_gen_queries || !gpu_query_counter || !gpu_get_query_iv || !gpu_get_query_u64) return;
    GLuint ids[32]={0}; gpu_gen_queries(32,ids);
    for(unsigned i=0;i<32;i++) if(!ids[i]) return;
    for(unsigned i=0;i<8;i++) memcpy(gpu_slots[i].q,ids+i*4,4*sizeof(GLuint));
    gpu_ready=true;
}
static bool gpu_available(GpuSlot *slot) {
    GLint ready=0;
    gpu_get_query_iv(slot->q[0],GL_QUERY_AVAILABLE_MODEL,&ready); if(!ready) return false;
    gpu_get_query_iv(slot->q[3],GL_QUERY_AVAILABLE_MODEL,&ready); if(!ready) return false;
    if(slot->has_map) {
        gpu_get_query_iv(slot->q[1],GL_QUERY_AVAILABLE_MODEL,&ready); if(!ready) return false;
        gpu_get_query_iv(slot->q[2],GL_QUERY_AVAILABLE_MODEL,&ready); if(!ready) return false;
    }
    return true;
}
static void gpu_poll(void) {
    if(!gpu_ready) return;
    for(unsigned i=0;i<8;i++) {
        GpuSlot *slot=&gpu_slots[i]; if(!slot->pending || !gpu_available(slot)) continue;
        GLuint64 start=0,end=0,map_start=0,map_end=0;
        gpu_get_query_u64(slot->q[0],GL_QUERY_RESULT_MODEL,&start);
        gpu_get_query_u64(slot->q[3],GL_QUERY_RESULT_MODEL,&end);
        if(slot->has_map) {
            gpu_get_query_u64(slot->q[1],GL_QUERY_RESULT_MODEL,&map_start);
            gpu_get_query_u64(slot->q[2],GL_QUERY_RESULT_MODEL,&map_end);
        }
        if(end>=start && (!slot->has_map || map_end>=map_start))
            detail_line5("G",slot->phase,slot->render_id,end-start,
                         slot->has_map?map_end-map_start:0,0);
        slot->pending=false;
    }
}
static void gpu_begin_render(void) {
    gpu_initialize(); if(!gpu_ready) return;
    gpu_poll();
    for(unsigned n=0;n<8;n++) {
        unsigned i=(gpu_cursor+n)%8; GpuSlot *slot=&gpu_slots[i];
        if(slot->pending) continue;
        slot->pending=true; slot->has_map=false; slot->render_id=current_frame.render_id;
        slot->phase=current_frame.phase; gpu_active_slot=(int)i;
        gpu_map_started=false;
        gpu_query_counter(slot->q[0],GL_TIMESTAMP_MODEL);
        gpu_cursor=(i+1)%8; return;
    }
    current_frame.flags|=64; gpu_active_slot=-1;
}
static void gpu_map_marker(unsigned marker) {
    if(gpu_active_slot<0 || !gpu_ready || marker>1) return;
    GpuSlot *slot=&gpu_slots[(unsigned)gpu_active_slot];
    if(marker==0 && !gpu_map_started) {
        gpu_query_counter(slot->q[1],GL_TIMESTAMP_MODEL); gpu_map_started=true;
    } else if(marker==1 && gpu_map_started && !slot->has_map) {
        gpu_query_counter(slot->q[2],GL_TIMESTAMP_MODEL); slot->has_map=true;
    }
}
static void gpu_end_render(void) {
    if(gpu_active_slot<0 || !gpu_ready) return;
    gpu_query_counter(gpu_slots[(unsigned)gpu_active_slot].q[3],GL_TIMESTAMP_MODEL);
    gpu_active_slot=-1;
}
static void uniform_snapshot(uint64_t site,GLuint program,GLint location,
                             const GLfloat *payload,size_t bytes) {
    if(bytes>256) {
        detail_line7("W",current_frame.render_id,site,program,(uint32_t)location,bytes,0,1);
        return;
    }
    uint64_t hash=site^((uint64_t)program<<17)^(uint32_t)location;
    unsigned first=(unsigned)(hash%256),chosen=256;
    for(unsigned n=0;n<256;n++) {
        unsigned i=(first+n)%256; UniformSnapshot *item=&uniform_snapshots[i];
        if(item->used && item->site==site && item->program==program && item->location==location) {
            chosen=i; break;
        }
        if(!item->used) { chosen=i; break; }
    }
    if(chosen==256) { chosen=uniform_snapshot_evict++%256; }
    UniformSnapshot *item=&uniform_snapshots[chosen];
    if(item->used && item->site==site && item->program==program &&
       item->location==location && item->render_id+1==current_frame.render_id && item->bytes==bytes) {
        uint32_t changed=0,emitted=0;
        for(size_t offset=0;offset+sizeof(uint32_t)<=bytes;offset+=sizeof(uint32_t)) {
            uint32_t before=0,after=0;
            memcpy(&before,item->data+offset,sizeof(before));
            memcpy(&after,(const unsigned char *)payload+offset,sizeof(after));
            if(before==after) continue;
            changed++;
            if(emitted++<32) detail_line7("V",current_frame.render_id,site,program,
                (uint32_t)location,offset,before,after);
        }
        detail_line7("W",current_frame.render_id,site,program,(uint32_t)location,
                     bytes,changed,changed>32?changed-32:0);
    }
    item->used=true; item->site=site; item->program=program; item->location=location;
    item->render_id=current_frame.render_id; item->bytes=bytes;
    memcpy(item->data,payload,bytes);
}

static void jump_stub(unsigned char *at,uintptr_t target) {
    at[0]=0x49; at[1]=0xbb; memcpy(at+2,&target,8); at[10]=0x41; at[11]=0xff; at[12]=0xe3;
}
typedef struct { unsigned char bytes[32]; void *trampoline; uintptr_t site; size_t patch; } Detour;
static Detour detours[EU4_HOOK_COUNT];
static void restore_detours(void) {
    for(unsigned i=0;i<EU4_HOOK_COUNT;i++) {
        Detour *d=&detours[i]; if(!d->site || !d->trampoline || d->trampoline==MAP_FAILED) continue;
        long page=sysconf(_SC_PAGESIZE); uintptr_t begin=d->site&~((uintptr_t)page-1);
        bool restored=false;
        if(mach_vm_protect(mach_task_self(),begin,(mach_vm_size_t)page,false,
                           VM_PROT_READ|VM_PROT_WRITE|VM_PROT_COPY)==KERN_SUCCESS) {
            memcpy((void *)d->site,d->bytes,d->patch); sys_icache_invalidate((void *)d->site,d->patch);
            restored=mach_vm_protect(mach_task_self(),begin,(mach_vm_size_t)page,false,
                                     VM_PROT_READ|VM_PROT_EXECUTE)==KERN_SUCCESS;
        }
        if(restored) { (void)munmap(d->trampoline,4096); d->trampoline=NULL; }
        else atomic_fetch_add(&control->hook_failures,1);
    }
}
typedef void (*UpdateFn)(void *,bool); typedef void (*IdleFn)(void *,bool);
typedef void (*RenderFn)(void *); typedef void (*MapFn)(void *,void *,const void *,float,bool);
typedef void (*PresentFn)(void *); typedef void (*AddFn)(const void *,int,const void *);
typedef void (*AppendFn)(void *,const void *);
static UpdateFn real_update; static IdleFn real_idle; static RenderFn real_render;
static MapFn real_map; static PresentFn real_present;
static AddFn real_add; static AppendFn real_append;
static _Atomic uint64_t updates, renders, presents;

static void hook_update(void *self,bool force) {
    uint64_t id=atomic_fetch_add(&updates,1)+1;
    if(!control || control->mode==OFF) {
        real_update(self,force);
        if(control) atomic_store(&control->ack_generation,control->generation);
        return;
    }
    uint64_t start=now_ns(CLOCK_UPTIME_RAW), cpu=now_ns(CLOCK_THREAD_CPUTIME_ID);
    current_frame=(Frame){.update_id=id,.phase=control?control->phase:0,.thread_id=tid()};
    real_update(self,force);
    uint64_t end=now_ns(CLOCK_UPTIME_RAW), end_cpu=now_ns(CLOCK_THREAD_CPUTIME_ID);
    current_frame.update_wall_ns=end-start; current_frame.update_cpu_ns=end_cpu-cpu;
    event(HOOK_UPDATE,end-start,end_cpu-cpu);
    uint64_t cadence=control?control->cadence_ns:0;
    if(cadence && control->mode!=OFF) {
        if(!next_deadline) next_deadline=end+cadence;
        else if(end<next_deadline) {
            uint64_t deadline=next_deadline;
            uint64_t remain=deadline-end;
            struct timespec ts={(time_t)(remain/1000000000ull),(long)(remain%1000000000ull)};
            (void)nanosleep(&ts,NULL);
            current_frame.sleep_ns=now_ns(CLOCK_UPTIME_RAW)-end;
            next_deadline=deadline+cadence;
        } else { current_frame.flags|=2; next_deadline=end+cadence; }
    } else next_deadline=0;
    current_frame.wall_ns=now_ns(CLOCK_UPTIME_RAW)-start;
    current_frame.cpu_ns=now_ns(CLOCK_THREAD_CPUTIME_ID)-cpu;
    if(control && control->mode!=OFF) publish_frame(&current_frame);
    if(control) atomic_store(&control->ack_generation,control->generation);
}
static void hook_idle(void *self,bool force) {
    if(!control || control->mode==OFF) { real_idle(self,force); return; }
    uint64_t w=now_ns(CLOCK_UPTIME_RAW),c=now_ns(CLOCK_THREAD_CPUTIME_ID);
    real_idle(self,force);
    current_frame.idle_wall_ns=now_ns(CLOCK_UPTIME_RAW)-w;
    current_frame.idle_cpu_ns=now_ns(CLOCK_THREAD_CPUTIME_ID)-c;
    event(HOOK_IDLE,current_frame.idle_wall_ns,current_frame.idle_cpu_ns);
}
static void hook_render(void *self) {
    uint64_t id=atomic_fetch_add(&renders,1)+1;
    if(!control || control->mode==OFF) { real_render(self); return; }
    current_frame.render_id=id;
    detail_active=control && control->detail_frames>0;
    if(detail_active) control->detail_frames--;
    uint32_t mode=control?control->mode:OFF;
    bool skip=mode==SKIP_RENDER;
    if(skip) return;
    uint64_t w=now_ns(CLOCK_UPTIME_RAW),c=now_ns(CLOCK_THREAD_CPUTIME_ID);
    gpu_begin_render();
    CGLContextObj render_context=NULL;
    static void (*enable)(GLenum); static void (*disable)(GLenum);
    static CGLError (*set_context)(CGLContextObj);
    if(mode==RASTER_SUPPRESS) {
        if(!enable) enable=dlsym(RTLD_DEFAULT,"glEnable");
        if(!disable) disable=dlsym(RTLD_DEFAULT,"glDisable");
        if(!set_context) set_context=dlsym(RTLD_NEXT,"CGLSetCurrentContext");
        render_context=CGLGetCurrentContext();
        raster_active=true; raster_context_count=0;
        if(render_context && enable) {
            GLboolean (*is_enabled)(GLenum)=dlsym(RTLD_DEFAULT,"glIsEnabled");
            if(is_enabled) {
                raster_contexts[0]=(RasterContext){render_context,is_enabled(GL_RASTERIZER_DISCARD)};
                raster_context_count=1;
                if(!raster_contexts[0].enabled_before) {
                    enable(GL_RASTERIZER_DISCARD);
                    if(!is_enabled(GL_RASTERIZER_DISCARD)) current_frame.flags|=8;
                }
            } else current_frame.flags|=8;
        } else current_frame.flags|=8;
    }
    real_render(self);
    gpu_end_render();
    if(mode==RASTER_SUPPRESS) {
        CGLContextObj restore=CGLGetCurrentContext();
        if(render_context && set_context && disable) {
            for(unsigned i=0;i<raster_context_count;i++) {
                if(set_context(raster_contexts[i].context)!=kCGLNoError) { current_frame.flags|=16; continue; }
                if(!raster_contexts[i].enabled_before) disable(GL_RASTERIZER_DISCARD);
            }
            if(set_context(restore)!=kCGLNoError) current_frame.flags|=16;
        } else current_frame.flags|=16;
        raster_active=false; raster_context_count=0;
    }
    current_frame.render_wall_ns=now_ns(CLOCK_UPTIME_RAW)-w;
    current_frame.render_cpu_ns=now_ns(CLOCK_THREAD_CPUTIME_ID)-c;
    event(HOOK_RENDER,current_frame.render_wall_ns,current_frame.render_cpu_ns);
}
static void hook_map(void *self,void *ctx,const void *camera,float alpha,bool flag) {
    if(!control || control->mode==OFF) { real_map(self,ctx,camera,alpha,flag); return; }
    uint64_t w=now_ns(CLOCK_UPTIME_RAW),c=now_ns(CLOCK_THREAD_CPUTIME_ID);
    gpu_map_marker(0);
    real_map(self,ctx,camera,alpha,flag);
    gpu_map_marker(1);
    current_frame.map_wall_ns=now_ns(CLOCK_UPTIME_RAW)-w;
    current_frame.map_cpu_ns=now_ns(CLOCK_THREAD_CPUTIME_ID)-c;
    event(HOOK_MAP,current_frame.map_wall_ns,current_frame.map_cpu_ns);
}
static void hook_present(void *self) {
    if(!control || control->mode==OFF) { real_present(self); return; }
    uint64_t w=now_ns(CLOCK_UPTIME_RAW),c=now_ns(CLOCK_THREAD_CPUTIME_ID);
    real_present(self);
    current_frame.present_wall_ns=now_ns(CLOCK_UPTIME_RAW)-w;
    current_frame.present_cpu_ns=now_ns(CLOCK_THREAD_CPUTIME_ID)-c;
    event(HOOK_PRESENT_SCENE,current_frame.present_wall_ns,current_frame.present_cpu_ns);
}
static void hook_add(const void *self,int layer,const void *camera) {
    if(!control || control->mode==OFF) { real_add(self,layer,camera); return; }
    current_frame.bucket_calls++; uint64_t w=now_ns(CLOCK_UPTIME_RAW),c=now_ns(CLOCK_THREAD_CPUTIME_ID);
    real_add(self,layer,camera); event(HOOK_ADD_BUCKET,now_ns(CLOCK_UPTIME_RAW)-w,
                                      now_ns(CLOCK_THREAD_CPUTIME_ID)-c);
}
static void hook_append(void *self,const void *record) {
    if(!control || control->mode==OFF) { real_append(self,record); return; }
    current_frame.append_calls++; real_append(self,record);
    event(HOOK_APPEND,0,0);
}

static void install_one(unsigned i,void *replacement,void **original) {
    const EU4HookSite *site=&eu4_hook_sites[i];
    uintptr_t at=image_base+site->offset;
    *original=(void *)at;
    // Avoid instruction decoding at runtime: all sites must match the generated
    // 13-byte instruction-boundary prefix before any text page is changed.
    unsigned char expected[24];
    size_t patch=site->patch;
    if(patch<13 || patch>sizeof(expected)) { atomic_fetch_add(&control->hook_failures,1); return; }
    for(size_t j=0;j<patch;j++) {
        char pair[3]={site->prefix[2*j],site->prefix[2*j+1],0};
        expected[j]=(unsigned char)strtoul(pair,NULL,16);
    }
    if(memcmp((void *)at,expected,patch)!=0) { atomic_fetch_add(&control->hook_failures,1); return; }
    long page=sysconf(_SC_PAGESIZE); uintptr_t begin=at&~((uintptr_t)page-1);
    if(mach_vm_protect(mach_task_self(),begin,(mach_vm_size_t)page,false,
                       VM_PROT_READ|VM_PROT_WRITE|VM_PROT_COPY)!=KERN_SUCCESS) {
        atomic_fetch_add(&control->hook_failures,1); return;
    }
    Detour *d=&detours[i]; d->site=at; d->patch=patch; memcpy(d->bytes,(void *)at,patch);
    d->trampoline=mmap(NULL,4096,PROT_READ|PROT_WRITE|PROT_EXEC,
                       MAP_PRIVATE|MAP_ANON,-1,0);
    if(d->trampoline==MAP_FAILED) {
        atomic_fetch_add(&control->hook_failures,1);
        (void)mach_vm_protect(mach_task_self(),begin,(mach_vm_size_t)page,false,
                              VM_PROT_READ|VM_PROT_EXECUTE);
        return;
    }
    memcpy(d->trampoline,d->bytes,patch);
    jump_stub((unsigned char *)d->trampoline+patch,at+patch);
    sys_icache_invalidate(d->trampoline,patch+13);
    jump_stub((unsigned char *)at,(uintptr_t)replacement);
    for(size_t j=13;j<patch;j++) *((unsigned char *)at+j)=0x90;
    sys_icache_invalidate((void *)at,patch);
    if(mach_vm_protect(mach_task_self(),begin,(mach_vm_size_t)page,false,
                       VM_PROT_READ|VM_PROT_EXECUTE)!=KERN_SUCCESS)
        atomic_fetch_add(&control->hook_failures,1);
    *original=d->trampoline;
}

static CGLError hooked_flush(CGLContextObj ctx) {
    uint64_t id=atomic_fetch_add(&presents,1)+1; current_frame.present_id=id;
    uint64_t w=now_ns(CLOCK_UPTIME_RAW),c=now_ns(CLOCK_THREAD_CPUTIME_ID);
    static CGLError (*real_flush)(CGLContextObj);
    if(!real_flush) real_flush=dlsym(RTLD_NEXT,"CGLFlushDrawable");
    CGLError result=real_flush?real_flush(ctx):kCGLBadContext;
    if(!control || control->mode==OFF) { auto_probe_swap(result); return result; }
    uint64_t dw=now_ns(CLOCK_UPTIME_RAW)-w,dc=now_ns(CLOCK_THREAD_CPUTIME_ID)-c;
    current_frame.wait_ns+=dw; current_frame.wait_cpu_ns+=dc; event(HOOK_SWAP,dw,dc);
    if(control && control->mode!=OFF) count_line("P",id,control->phase,dw);
    auto_probe_swap(result);
    return result;
}
static void gl_draw_elements(GLenum mode,GLsizei count,GLenum type,const void *indices) {
    static void (*real_fn)(GLenum,GLsizei,GLenum,const void *);
    if(!real_fn) real_fn=dlsym(RTLD_NEXT,"glDrawElements");
    if(!control || control->mode==OFF) { if(real_fn) real_fn(mode,count,type,indices); return; }
    current_frame.draws++; if(count>0) current_frame.indices+=(uint64_t)count;
    current_frame.triangles+=estimated_triangles(mode,count,1);
    if(detail_active) {
        uintptr_t caller=(uintptr_t)__builtin_return_address(0);
        detail_line5("D",current_frame.render_id,caller>=image_base?caller-image_base:0,
                   mode,(uint32_t)count,type);
    }
    bool timed=(++draw_sample_seq&255u)==1u;
    uint64_t w=timed?now_ns(CLOCK_UPTIME_RAW):0,c=timed?now_ns(CLOCK_THREAD_CPUTIME_ID):0;
    if(control && control->mode==DROP_DRAWS) current_frame.suppressed_draws++;
    else { if(real_fn) real_fn(mode,count,type,indices); current_frame.forwarded_draws++; }
    uint64_t dw=timed?now_ns(CLOCK_UPTIME_RAW)-w:0,dc=timed?now_ns(CLOCK_THREAD_CPUTIME_ID)-c:0;
    current_frame.draw_wall_ns+=dw*256; current_frame.draw_cpu_ns+=dc*256;
    if(timed) current_frame.draw_timed_samples++;
    event(HOOK_GL_DRAW,dw,dc);
}
static void gl_draw_base(GLenum mode,GLsizei count,GLenum type,const void *indices,GLint base) {
    static void (*real_fn)(GLenum,GLsizei,GLenum,const void *,GLint);
    if(!real_fn) real_fn=dlsym(RTLD_NEXT,"glDrawElementsBaseVertex");
    if(!control || control->mode==OFF) { if(real_fn) real_fn(mode,count,type,indices,base); return; }
    current_frame.draws++; if(count>0) current_frame.indices+=(uint64_t)count;
    current_frame.triangles+=estimated_triangles(mode,count,1);
    if(detail_active) {
        uintptr_t caller=(uintptr_t)__builtin_return_address(0);
        detail_line5("D",current_frame.render_id,caller>=image_base?caller-image_base:0,
                   mode,(uint32_t)count,((uint64_t)(uint32_t)base<<32)|type);
    }
    bool timed=(++draw_sample_seq&255u)==1u;
    uint64_t w=timed?now_ns(CLOCK_UPTIME_RAW):0,c=timed?now_ns(CLOCK_THREAD_CPUTIME_ID):0;
    uint32_t m=control?control->mode:OFF;
    if(m==DROP_DRAWS) current_frame.suppressed_draws++;
    else { if(real_fn) real_fn(mode,count,type,indices,base); current_frame.forwarded_draws++; }
    uint64_t dw=timed?now_ns(CLOCK_UPTIME_RAW)-w:0,dc=timed?now_ns(CLOCK_THREAD_CPUTIME_ID)-c:0;
    current_frame.draw_wall_ns+=dw*256; current_frame.draw_cpu_ns+=dc*256;
    if(timed) current_frame.draw_timed_samples++;
    event(HOOK_GL_DRAW,dw,dc);
}
static void gl_draw_arrays(GLenum mode,GLint first,GLsizei count) {
    static void (*real_fn)(GLenum,GLint,GLsizei);
    if(!real_fn) real_fn=dlsym(RTLD_NEXT,"glDrawArrays");
    if(!control || control->mode==OFF) { if(real_fn) real_fn(mode,first,count); return; }
    current_frame.draws++; if(count>0) current_frame.indices+=(uint64_t)count;
    current_frame.triangles+=estimated_triangles(mode,count,1);
    if(detail_active) {
        uintptr_t caller=(uintptr_t)__builtin_return_address(0);
        detail_line5("D",current_frame.render_id,caller>=image_base?caller-image_base:0,
                   mode,(uint32_t)first,(uint32_t)count);
    }
    bool timed=(++draw_sample_seq&255u)==1u;
    uint64_t w=timed?now_ns(CLOCK_UPTIME_RAW):0,c=timed?now_ns(CLOCK_THREAD_CPUTIME_ID):0;
    uint32_t m=control?control->mode:OFF;
    if(m==DROP_DRAWS) current_frame.suppressed_draws++;
    else { if(real_fn) real_fn(mode,first,count); current_frame.forwarded_draws++; }
    uint64_t dw=timed?now_ns(CLOCK_UPTIME_RAW)-w:0,dc=timed?now_ns(CLOCK_THREAD_CPUTIME_ID)-c:0;
    current_frame.draw_wall_ns+=dw*256; current_frame.draw_cpu_ns+=dc*256;
    if(timed) current_frame.draw_timed_samples++;
    event(HOOK_GL_DRAW,dw,dc);
}
static void gl_draw_elements_instanced(GLenum mode,GLsizei count,GLenum type,
        const void *indices,GLsizei instances) {
    static void (*real_fn)(GLenum,GLsizei,GLenum,const void *,GLsizei);
    if(!real_fn) real_fn=dlsym(RTLD_NEXT,"glDrawElementsInstanced");
    if(!control || control->mode==OFF) { if(real_fn) real_fn(mode,count,type,indices,instances); return; }
    current_frame.draws++; if(count>0 && instances>0) current_frame.indices+=(uint64_t)count*(uint64_t)instances;
    current_frame.triangles+=estimated_triangles(mode,count,instances>0?(uint64_t)instances:0);
    if(detail_active) {
        uintptr_t caller=(uintptr_t)__builtin_return_address(0);
        detail_line5("D",current_frame.render_id,caller>=image_base?caller-image_base:0,
                     mode,(uint32_t)count,((uint64_t)(uint32_t)instances<<32)|type);
    }
    bool timed=(++draw_sample_seq&255u)==1u;
    uint64_t w=timed?now_ns(CLOCK_UPTIME_RAW):0,c=timed?now_ns(CLOCK_THREAD_CPUTIME_ID):0;
    uint32_t m=control->mode;
    if(m==DROP_DRAWS) current_frame.suppressed_draws++;
    else { if(real_fn) real_fn(mode,count,type,indices,instances); current_frame.forwarded_draws++; }
    uint64_t dw=timed?now_ns(CLOCK_UPTIME_RAW)-w:0,dc=timed?now_ns(CLOCK_THREAD_CPUTIME_ID)-c:0;
    current_frame.draw_wall_ns+=dw*256;current_frame.draw_cpu_ns+=dc*256;
    if(timed) current_frame.draw_timed_samples++; event(HOOK_GL_DRAW,dw,dc);
}
static void gl_draw_arrays_instanced(GLenum mode,GLint first,GLsizei count,GLsizei instances) {
    static void (*real_fn)(GLenum,GLint,GLsizei,GLsizei);
    if(!real_fn) real_fn=dlsym(RTLD_NEXT,"glDrawArraysInstanced");
    if(!control || control->mode==OFF) { if(real_fn) real_fn(mode,first,count,instances); return; }
    current_frame.draws++; if(count>0 && instances>0) current_frame.indices+=(uint64_t)count*(uint64_t)instances;
    current_frame.triangles+=estimated_triangles(mode,count,instances>0?(uint64_t)instances:0);
    if(detail_active) {
        uintptr_t caller=(uintptr_t)__builtin_return_address(0);
        detail_line5("D",current_frame.render_id,caller>=image_base?caller-image_base:0,
                     mode,(uint32_t)first,((uint64_t)(uint32_t)count<<32)|(uint32_t)instances);
    }
    bool timed=(++draw_sample_seq&255u)==1u;
    uint64_t w=timed?now_ns(CLOCK_UPTIME_RAW):0,c=timed?now_ns(CLOCK_THREAD_CPUTIME_ID):0;
    uint32_t m=control->mode;
    if(m==DROP_DRAWS) current_frame.suppressed_draws++;
    else { if(real_fn) real_fn(mode,first,count,instances); current_frame.forwarded_draws++; }
    uint64_t dw=timed?now_ns(CLOCK_UPTIME_RAW)-w:0,dc=timed?now_ns(CLOCK_THREAD_CPUTIME_ID)-c:0;
    current_frame.draw_wall_ns+=dw*256;current_frame.draw_cpu_ns+=dc*256;
    if(timed) current_frame.draw_timed_samples++; event(HOOK_GL_DRAW,dw,dc);
}
static void gl_draw_elements_instanced_base(GLenum mode,GLsizei count,GLenum type,
        const void *indices,GLsizei instances,GLint base) {
    static void (*real_fn)(GLenum,GLsizei,GLenum,const void *,GLsizei,GLint);
    if(!real_fn) real_fn=dlsym(RTLD_NEXT,"glDrawElementsInstancedBaseVertex");
    if(!control || control->mode==OFF) { if(real_fn) real_fn(mode,count,type,indices,instances,base); return; }
    current_frame.draws++; if(count>0 && instances>0) current_frame.indices+=(uint64_t)count*(uint64_t)instances;
    current_frame.triangles+=estimated_triangles(mode,count,instances>0?(uint64_t)instances:0);
    if(detail_active) {
        uintptr_t caller=(uintptr_t)__builtin_return_address(0);
        detail_line5("D",current_frame.render_id,caller>=image_base?caller-image_base:0,
                     mode,(uint32_t)count,((uint64_t)(uint32_t)instances<<32)|
                     ((uint64_t)(uint16_t)base<<16)|type);
    }
    bool timed=(++draw_sample_seq&255u)==1u;
    uint64_t w=timed?now_ns(CLOCK_UPTIME_RAW):0,c=timed?now_ns(CLOCK_THREAD_CPUTIME_ID):0;
    uint32_t m=control->mode;
    if(m==DROP_DRAWS) current_frame.suppressed_draws++;
    else { if(real_fn) real_fn(mode,count,type,indices,instances,base); current_frame.forwarded_draws++; }
    uint64_t dw=timed?now_ns(CLOCK_UPTIME_RAW)-w:0,dc=timed?now_ns(CLOCK_THREAD_CPUTIME_ID)-c:0;
    current_frame.draw_wall_ns+=dw*256;current_frame.draw_cpu_ns+=dc*256;
    if(timed) current_frame.draw_timed_samples++; event(HOOK_GL_DRAW,dw,dc);
}
static void gl_buffer_data(GLenum target,GLsizeiptr size,const void *data,GLenum usage) {
    static void (*real_fn)(GLenum,GLsizeiptr,const void *,GLenum);
    if(!real_fn) real_fn=dlsym(RTLD_NEXT,"glBufferData");
    if(!control || control->mode==OFF) { if(real_fn) real_fn(target,size,data,usage); return; }
    event(HOOK_GL_UPLOAD,0,0);
    current_frame.buffer_calls++; if(size>0) {
        current_frame.buffer_bytes+=(uint64_t)size;
        current_frame.buffer_storage_bytes+=(uint64_t)size;
        if(data) current_frame.buffer_upload_bytes+=(uint64_t)size;
    }
    if(detail_active && data && size>0) detail_line5("B",current_frame.render_id,
        ((uint64_t)target<<32)|(target==GL_ELEMENT_ARRAY_BUFFER?bound_element_buffer:bound_array_buffer),
        (uint64_t)size,hash_bytes(data,(size_t)size),usage);
    if(real_fn) real_fn(target,size,data,usage);
}
static void gl_buffer_subdata(GLenum target,GLintptr offset,GLsizeiptr size,const void *data) {
    static void (*real_fn)(GLenum,GLintptr,GLsizeiptr,const void *);
    if(!real_fn) real_fn=dlsym(RTLD_NEXT,"glBufferSubData");
    if(!control || control->mode==OFF) { if(real_fn) real_fn(target,offset,size,data); return; }
    event(HOOK_GL_UPLOAD,0,0);
    current_frame.buffer_calls++; if(size>0) {
        current_frame.buffer_bytes+=(uint64_t)size;
        current_frame.buffer_upload_bytes+=(uint64_t)size;
    }
    if(detail_active && data && size>0) detail_line5("b",current_frame.render_id,
        ((uint64_t)target<<32)|(target==GL_ELEMENT_ARRAY_BUFFER?bound_element_buffer:bound_array_buffer),
        (uint64_t)size,(uint64_t)offset,hash_bytes(data,(size_t)size));
    if(real_fn) real_fn(target,offset,size,data);
}
static void gl_uniform4fv(GLint location,GLsizei count,const GLfloat *data) {
    static void (*real_fn)(GLint,GLsizei,const GLfloat *);
    if(!real_fn) real_fn=dlsym(RTLD_NEXT,"glUniform4fv");
    if(!control || control->mode==OFF) { if(real_fn) real_fn(location,count,data); return; }
    event(HOOK_GL_UNIFORM,0,0);
    current_frame.uniform_calls++; if(count>0) current_frame.uniform_bytes+=(uint64_t)count*16;
    if(detail_active && data && count>0) {
        uintptr_t caller=(uintptr_t)__builtin_return_address(0);
        detail_line5("U",current_frame.render_id,caller>=image_base?caller-image_base:0,
                   active_program,(uint32_t)location,
                   ((uint64_t)(uint32_t)count<<32)|(uint32_t)hash_bytes(data,(size_t)count*16));
        uniform_snapshot(caller>=image_base?caller-image_base:0,active_program,location,
                         data,(size_t)count*16);
    }
    if(real_fn) real_fn(location,count,data);
}
static void gl_use_program(GLuint program) {
    static void (*real_fn)(GLuint);
    if(!real_fn) real_fn=dlsym(RTLD_NEXT,"glUseProgram");
    if(control && control->mode!=OFF) {
        event(HOOK_GL_STATE,0,0);
        current_frame.state_calls++;
        if(active_program!=program) current_frame.program_switches++;
        active_program=program;
        if(detail_active) {
            uintptr_t caller=(uintptr_t)__builtin_return_address(0);
            detail_line("S",current_frame.render_id,caller>=image_base?caller-image_base:0,1,program);
        }
    }
    if(real_fn) real_fn(program);
}
static void gl_bind_buffer(GLenum target,GLuint buffer) {
    static void (*real_fn)(GLenum,GLuint);
    if(!real_fn) real_fn=dlsym(RTLD_NEXT,"glBindBuffer");
    if(control && control->mode!=OFF) {
        event(HOOK_GL_STATE,0,0);
        current_frame.state_calls++; current_frame.buffer_binds++;
        if(target==GL_ARRAY_BUFFER) bound_array_buffer=buffer;
        if(target==GL_ELEMENT_ARRAY_BUFFER) bound_element_buffer=buffer;
        if(detail_active) {
            uintptr_t caller=(uintptr_t)__builtin_return_address(0);
            detail_line("S",current_frame.render_id,caller>=image_base?caller-image_base:0,2,
                        ((uint64_t)target<<32)|buffer);
        }
    }
    if(real_fn) real_fn(target,buffer);
}
static void gl_tex_image_2d(GLenum target,GLint level,GLint internal,GLsizei width,
        GLsizei height,GLint border,GLenum format,GLenum type,const void *pixels) {
    static void (*real_fn)(GLenum,GLint,GLint,GLsizei,GLsizei,GLint,GLenum,GLenum,const void *);
    if(!real_fn) real_fn=dlsym(RTLD_NEXT,"glTexImage2D");
    if(!control || control->mode==OFF) {
        if(real_fn) real_fn(target,level,internal,width,height,border,format,type,pixels); return;
    }
    event(HOOK_GL_UPLOAD,0,0);
    current_frame.texture_calls++;
    if(width>0 && height>0) {
        unsigned bpp=(format==GL_RGBA?4:format==GL_RGB?3:format==0x190A?2:format==0x1909||format==GL_ALPHA?1:0);
        unsigned unit=(type==GL_UNSIGNED_BYTE||type==GL_BYTE)?1:
                      (type==GL_UNSIGNED_SHORT||type==GL_SHORT||type==GL_HALF_FLOAT)?2:
                      (type==GL_UNSIGNED_INT||type==GL_INT||type==GL_FLOAT)?4:0;
        if(bpp && unit) current_frame.texture_bytes+=(uint64_t)width*height*bpp*unit;
        else current_frame.flags|=4;
    }
    if(detail_active && pixels && width>0 && height>0) {
        uintptr_t caller=(uintptr_t)__builtin_return_address(0);
        detail_line("T",current_frame.render_id,caller>=image_base?caller-image_base:0,
                    ((uint64_t)(uint32_t)width<<32)|(uint32_t)height,0);
    }
    if(real_fn) real_fn(target,level,internal,width,height,border,format,type,pixels);
}
static void gl_tex_sub_image_2d(GLenum target,GLint level,GLint x,GLint y,GLsizei width,
        GLsizei height,GLenum format,GLenum type,const void *pixels) {
    static void (*real_fn)(GLenum,GLint,GLint,GLint,GLsizei,GLsizei,GLenum,GLenum,const void *);
    if(!real_fn) real_fn=dlsym(RTLD_NEXT,"glTexSubImage2D");
    if(!control || control->mode==OFF) {
        if(real_fn) real_fn(target,level,x,y,width,height,format,type,pixels); return;
    }
    event(HOOK_GL_UPLOAD,0,0);
    current_frame.texture_calls++;
    if(width>0 && height>0) {
        unsigned bpp=(format==GL_RGBA?4:format==GL_RGB?3:format==0x190A?2:format==0x1909||format==GL_ALPHA?1:0);
        unsigned unit=(type==GL_UNSIGNED_BYTE||type==GL_BYTE)?1:
                      (type==GL_UNSIGNED_SHORT||type==GL_SHORT||type==GL_HALF_FLOAT)?2:
                      (type==GL_UNSIGNED_INT||type==GL_INT||type==GL_FLOAT)?4:0;
        if(bpp && unit) current_frame.texture_bytes+=(uint64_t)width*height*bpp*unit;
        else current_frame.flags|=4;
    }
    if(detail_active && pixels && width>0 && height>0) {
        uintptr_t caller=(uintptr_t)__builtin_return_address(0);
        detail_line("t",current_frame.render_id,caller>=image_base?caller-image_base:0,
                    ((uint64_t)(uint32_t)width<<32)|(uint32_t)height,0);
    }
    if(real_fn) real_fn(target,level,x,y,width,height,format,type,pixels);
}
static void gl_bind_texture(GLenum target,GLuint texture) {
    static void (*real_fn)(GLenum,GLuint);
    if(!real_fn) real_fn=dlsym(RTLD_NEXT,"glBindTexture");
    if(control && control->mode!=OFF) {
        event(HOOK_GL_STATE,0,0);
        current_frame.state_calls++; current_frame.texture_binds++;
        if(detail_active) {
            uintptr_t caller=(uintptr_t)__builtin_return_address(0);
            detail_line("S",current_frame.render_id,caller>=image_base?caller-image_base:0,3,
                        ((uint64_t)target<<32)|texture);
        }
    }
    if(real_fn) real_fn(target,texture);
}
static void gl_enable(GLenum cap) {
    static void (*real_fn)(GLenum);
    if(!real_fn) real_fn=dlsym(RTLD_NEXT,"glEnable");
    if(control && control->mode!=OFF) {
        event(HOOK_GL_STATE,0,0);
        current_frame.state_calls++;
        if(detail_active) {
            uintptr_t caller=(uintptr_t)__builtin_return_address(0);
            detail_line("S",current_frame.render_id,caller>=image_base?caller-image_base:0,4,
                        ((uint64_t)cap<<1)|1);
        }
    }
    if(real_fn) real_fn(cap);
}
static void gl_disable(GLenum cap) {
    static void (*real_fn)(GLenum);
    if(!real_fn) real_fn=dlsym(RTLD_NEXT,"glDisable");
    if(control && control->mode!=OFF) {
        event(HOOK_GL_STATE,0,0);
        current_frame.state_calls++;
        if(detail_active) {
            uintptr_t caller=(uintptr_t)__builtin_return_address(0);
            detail_line("S",current_frame.render_id,caller>=image_base?caller-image_base:0,4,
                        (uint64_t)cap<<1);
        }
    }
    if(real_fn) real_fn(cap);
}
static void gl_active_texture(GLenum unit) {
    static void (*real_fn)(GLenum);
    if(!real_fn) real_fn=dlsym(RTLD_NEXT,"glActiveTexture");
    if(control && control->mode!=OFF) {
        event(HOOK_GL_STATE,0,0);
        current_frame.state_calls++;
        if(detail_active) {
            uintptr_t caller=(uintptr_t)__builtin_return_address(0);
            detail_line("S",current_frame.render_id,caller>=image_base?caller-image_base:0,5,unit);
        }
    }
    if(real_fn) real_fn(unit);
}
static CGLError tracked_set_context(CGLContextObj ctx) {
    static CGLError (*real_fn)(CGLContextObj);
    if(!real_fn) real_fn=dlsym(RTLD_NEXT,"CGLSetCurrentContext");
    CGLError result=real_fn?real_fn(ctx):kCGLBadContext;
    if(result==kCGLNoError && gpu_context && ctx!=gpu_context) {
        gpu_context_changed=true; gpu_ready=false; gpu_active_slot=-1;
        current_frame.flags|=256;
    }
    if(result==kCGLNoError && raster_active && ctx) {
        bool found=false;
        for(unsigned i=0;i<raster_context_count;i++)
            if(raster_contexts[i].context==ctx) { found=true; break; }
        if(!found) {
            if(raster_context_count>=64) current_frame.flags|=32;
            else {
                GLboolean (*is_enabled)(GLenum)=dlsym(RTLD_DEFAULT,"glIsEnabled");
                void (*enable)(GLenum)=dlsym(RTLD_DEFAULT,"glEnable");
                if(!is_enabled || !enable) current_frame.flags|=8;
                else {
                    GLboolean previous=is_enabled(GL_RASTERIZER_DISCARD);
                    raster_contexts[raster_context_count++]=(RasterContext){ctx,previous};
                    if(!previous) {
                        enable(GL_RASTERIZER_DISCARD);
                        if(!is_enabled(GL_RASTERIZER_DISCARD)) current_frame.flags|=8;
                    }
                }
            }
        }
    }
    return result;
}

static void *tracked_dlsym(void *handle,const char *name) {
    void *original=dlsym(handle,name);
    if(!name || !original) return original;
    if(!strcmp(name,"glDrawElementsBaseVertex") || !strcmp(name,"glDrawElementsBaseVertexARB")) return gl_draw_base;
    if(!strcmp(name,"glDrawElements") || !strcmp(name,"glDrawElementsEXT")) return gl_draw_elements;
    if(!strcmp(name,"glDrawArrays")) return gl_draw_arrays;
    if(!strcmp(name,"glDrawElementsInstanced") || !strcmp(name,"glDrawElementsInstancedARB")) return gl_draw_elements_instanced;
    if(!strcmp(name,"glDrawArraysInstanced") || !strcmp(name,"glDrawArraysInstancedARB")) return gl_draw_arrays_instanced;
    if(!strcmp(name,"glDrawElementsInstancedBaseVertex")) return gl_draw_elements_instanced_base;
    if(!strcmp(name,"glBufferData") || !strcmp(name,"glBufferDataARB")) return gl_buffer_data;
    if(!strcmp(name,"glBufferSubData") || !strcmp(name,"glBufferSubDataARB")) return gl_buffer_subdata;
    if(!strcmp(name,"glUniform4fv") || !strcmp(name,"glUniform4fvARB")) return gl_uniform4fv;
    if(!strcmp(name,"glUseProgram") || !strcmp(name,"glUseProgramObjectARB")) return gl_use_program;
    if(!strcmp(name,"glBindBuffer") || !strcmp(name,"glBindBufferARB")) return gl_bind_buffer;
    if(!strcmp(name,"glTexImage2D") || !strcmp(name,"glTexImage2DEXT")) return gl_tex_image_2d;
    if(!strcmp(name,"glTexSubImage2D") || !strcmp(name,"glTexSubImage2DEXT")) return gl_tex_sub_image_2d;
    if(!strcmp(name,"glBindTexture")) return gl_bind_texture;
    if(!strcmp(name,"glEnable")) return gl_enable;
    if(!strcmp(name,"glDisable")) return gl_disable;
    if(!strcmp(name,"glActiveTexture") || !strcmp(name,"glActiveTextureARB")) return gl_active_texture;
    return original;
}

static void initialize(void) __attribute__((constructor));
static void initialize(void) {
    const char *path=getenv("EU4_FRAME_MODEL_CONTROL");
    if(path) { int fd=open(path,O_RDWR); if(fd>=0) { control=mmap(NULL,4096,PROT_READ|PROT_WRITE,MAP_SHARED,fd,0); close(fd); } }
    const char *log=getenv("EU4_FRAME_MODEL_LOG");
    if(log) log_fd=open(log,O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC,0600);
    if(!control || control==MAP_FAILED || log_fd<0) return;
    const char *auto_path=getenv("EU4_AUTO_PROBE_LOG");
    if(auto_path) auto_log_fd=open(auto_path,O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC,0600);
    if(auto_path && auto_log_fd<0) atomic_fetch_add(&control->hook_failures,1);
    auto_app_instance=dlsym(RTLD_DEFAULT,"_ZN12CApplication14AccessInstanceEv");
    auto_actually_paused=dlsym(RTLD_DEFAULT,"_ZNK10CGameSpeed16IsActuallyPausedEv");
    auto_idler_vtable=dlsym(RTLD_DEFAULT,"_ZTV12CInGameIdler");
    Dl_info info={0}; (void)dladdr((void *)initialize,&info);
    // The library is separate from EU IV. Resolve a public executable symbol
    // to obtain the image base used by the generated offsets.
    void *anchor=dlsym(RTLD_DEFAULT,"_ZN12CApplication14UpdateOneFrameEb");
    if(anchor) { Dl_info game={0}; if(dladdr(anchor,&game)) image_base=(uintptr_t)game.dli_fbase; }
    if(!image_base) { atomic_fetch_add(&control->hook_failures,1); return; }
    install_one(0,hook_update,(void **)&real_update);
    install_one(1,hook_idle,(void **)&real_idle);
    install_one(2,hook_render,(void **)&real_render);
    install_one(3,hook_map,(void **)&real_map);
    install_one(4,hook_present,(void **)&real_present);
    install_one(5,hook_add,(void **)&real_add);
    install_one(6,hook_append,(void **)&real_append);
    if(atomic_load(&control->hook_failures)) { restore_detours(); return; }
    count_line("Z",atomic_load(&control->hook_failures),EU4_HOOK_COUNT,0);
    atomic_store(&writer_running,1);
    writer_started=pthread_create(&writer_thread,NULL,writer,NULL)==0;
    if(!writer_started) { atomic_fetch_add(&control->hook_failures,1); restore_detours(); }
    // Imported calls are covered by __interpose; dlsym results are covered by
    // the wrapper below. OpenGL exports are not modified in the game image.
}
static void shutdown_probe(void) __attribute__((destructor));
static void shutdown_probe(void) {
    if(!control || control==MAP_FAILED) return;
    atomic_store(&writer_running,0); if(writer_started) (void)pthread_join(writer_thread,NULL);
    restore_detours();
    for(unsigned i=0;i<HOOK_COUNT;i++) hook_line(i);
    count_line("X",atomic_load(&updates),atomic_load(&renders),atomic_load(&presents));
    count_line("Z",atomic_load(&control->hook_failures),EU4_HOOK_COUNT,0);
    if(auto_log_fd>=0) close(auto_log_fd);
}

__attribute__((used,section("__DATA,__interpose")))
static const struct { const void *replacement,*replacee; } interposes[]={
    {(const void *)tracked_dlsym,(const void *)dlsym},
    {(const void *)hooked_flush,(const void *)CGLFlushDrawable},
    {(const void *)gl_draw_elements,(const void *)glDrawElements},
    {(const void *)gl_draw_base,(const void *)glDrawElementsBaseVertex},
    {(const void *)gl_draw_arrays,(const void *)glDrawArrays},
    {(const void *)gl_draw_elements_instanced,(const void *)glDrawElementsInstanced},
    {(const void *)gl_draw_arrays_instanced,(const void *)glDrawArraysInstanced},
    {(const void *)gl_draw_elements_instanced_base,(const void *)glDrawElementsInstancedBaseVertex},
    {(const void *)gl_buffer_data,(const void *)glBufferData},
    {(const void *)gl_buffer_subdata,(const void *)glBufferSubData},
    {(const void *)gl_uniform4fv,(const void *)glUniform4fv},
    {(const void *)gl_use_program,(const void *)glUseProgram},
    {(const void *)gl_bind_buffer,(const void *)glBindBuffer},
    {(const void *)gl_tex_image_2d,(const void *)glTexImage2D},
    {(const void *)gl_tex_sub_image_2d,(const void *)glTexSubImage2D},
    {(const void *)gl_bind_texture,(const void *)glBindTexture},
    {(const void *)gl_enable,(const void *)glEnable},
    {(const void *)gl_disable,(const void *)glDisable},
    {(const void *)gl_active_texture,(const void *)glActiveTexture},
    {(const void *)tracked_set_context,(const void *)CGLSetCurrentContext}
};
