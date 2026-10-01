// Runtime profiler for the pinned GOG EU IV 1.37.5 x86-64 executable.
// Hooks are installed only after the generated inventory and prologues match.
#define GL_SILENCE_DEPRECATION 1
#include <OpenGL/OpenGL.h>
#include <OpenGL/gl3.h>
extern void glUseProgramObjectARB(GLhandleARB);
#include <CoreGraphics/CoreGraphics.h>
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <libkern/OSCacheControl.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stddef.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>
#include "eu4_frame_model_sites.h"
#include "eu4_detour.h"
#include "eu4_render_gate.h"
#include "eu4_scope_tree.h"
#include "eu4_spsc.h"
#include "eu4_raster_policy.h"
#include "eu4_gpu_segments.h"
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

enum { OFF, PROFILE, SKIP_RENDER, DROP_DRAWS, RASTER_SUPPRESS, CADENCE_30, CADENCE_15, REFERENCE };
enum { INTERVENTION_ACTIVE=1u, MEASURE_ENABLED=2u };
enum { SCOPE_UPDATE, SCOPE_IDLE, SCOPE_RENDER, SCOPE_MAP,
       SCOPE_PRESENT, SCOPE_ADD_BUCKET, SCOPE_APPEND, SCOPE_FLUSH,
       SCOPE_LOOP, SCOPE_CADENCE_SLEEP, SCOPE_COUNT };
enum { HOOK_UPDATE, HOOK_IDLE, HOOK_RENDER, HOOK_MAP, HOOK_PRESENT_SCENE,
       HOOK_ADD_BUCKET, HOOK_APPEND, HOOK_GL_DRAW, HOOK_GL_UPLOAD,
       HOOK_GL_UNIFORM, HOOK_SWAP, HOOK_GL_STATE, HOOK_COUNT };
typedef struct {
    uint32_t version;
    _Atomic uint32_t command_seq;
    uint32_t mode, phase, flags, detail_frames;
    uint64_t update_period_ns, render_period_ns, generation, measurement_epoch;
    _Atomic uint64_t ack_generation, hook_failures, dropped_records, ack_time_ns;
} Control;
_Static_assert(offsetof(Control,ack_generation)==56,"control v3 ack layout");
_Static_assert(offsetof(Control,ack_time_ns)==80,"control v3 clock layout");
typedef struct {
    uint32_t mode, phase, flags, detail_frames;
    uint64_t update_period_ns, render_period_ns, generation, measurement_epoch;
} ControlSnapshot;
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
    uint64_t render_attempts, render_executed, present_scene_calls, present_calls;
    uint64_t measurement_epoch,start_ns,end_ns,render_start_ns,render_end_ns,present_time_ns;
    uint64_t generation,sample_window,render_deadline_ns,render_lateness_ns,render_missed_deadlines,render_overruns,raster_challenges;
    Eu4ScopeSnapshot scopes;
} Frame;
typedef struct { Frame frame; } FrameSlot;
typedef struct { char kind; uint64_t a,b,c,d,e,f,g,h,i,j,k,l,epoch,update,phase,thread,generation,window,context; } DetailSlot;
#define FRAME_CAPACITY 128u
#define DETAIL_CAPACITY 32768u
#define PRODUCER_CAPACITY 16u
typedef struct {
    FrameSlot frames[FRAME_CAPACITY]; DetailSlot details[DETAIL_CAPACITY];
    Eu4Spsc frame_queue,detail_queue;
    uint64_t counts[HOOK_COUNT],wall[HOOK_COUNT],cpu[HOOK_COUNT];
} Producer;
static Producer *producers[PRODUCER_CAPACITY];
static _Atomic unsigned producer_count;
static pthread_mutex_t producer_lock=PTHREAD_MUTEX_INITIALIZER;
static _Thread_local Producer *local_producer;
static _Atomic uint64_t call_counts[HOOK_COUNT];
static _Atomic uint64_t call_wall_ns[HOOK_COUNT], call_cpu_ns[HOOK_COUNT];
static _Thread_local Frame current_frame;
static _Thread_local Eu4ScopeTree scope_tree;
static _Thread_local ControlSnapshot frame_control;
static _Thread_local uintptr_t active_program;
static _Thread_local GLuint bound_array_buffer, bound_element_buffer;
static _Thread_local CGLContextObj state_context;
static _Thread_local bool state_seeded;
static _Thread_local bool was_measurement_active;
typedef struct { GLuint buffer; GLint size; GLenum type; GLboolean normalized,enabled;
                 GLsizei stride; uintptr_t pointer; GLuint divisor; } VertexAttribute;
typedef struct { bool used; CGLContextObj context; GLuint vao,element_buffer;
                 VertexAttribute attributes[32]; } VertexArrayState;
static _Thread_local VertexArrayState vertex_arrays[64];
static _Thread_local unsigned vertex_array_count;
static _Thread_local VertexArrayState *vertex_array_state;
static _Thread_local bool detail_active;
static _Thread_local uint32_t draw_sample_seq;
static _Thread_local uint64_t detail_generation;
static _Thread_local uint32_t detail_remaining;
typedef struct { bool used; uint64_t site,render_id,program,epoch,window; GLint location;
                 size_t bytes; unsigned char data[256]; } UniformSnapshot;
static _Thread_local UniformSnapshot uniform_snapshots[256];
static _Thread_local unsigned uniform_snapshot_evict;
typedef Eu4RasterContext RasterContext;
static _Thread_local RasterContext raster_contexts[64];
static _Thread_local unsigned raster_context_count;
static _Thread_local bool raster_active;
static _Thread_local Eu4GpuManager gpu_manager;
static Eu4GpuRegistry gpu_registry;
static pthread_mutex_t gpu_lock=PTHREAD_MUTEX_INITIALIZER;
static _Thread_local const Eu4GpuIdentity *detail_gpu_origin;
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
static _Thread_local uint64_t next_deadline;
static _Thread_local uint64_t next_render_deadline;
static pthread_t writer_thread;
static _Atomic int writer_running;
static bool writer_started;
static uint64_t now_ns(clockid_t id);

static bool snapshot_control(ControlSnapshot *out) {
    if(!control || control==MAP_FAILED || control->version!=3) return false;
    for(unsigned attempt=0;attempt<100;attempt++) {
        uint32_t before=atomic_load_explicit(&control->command_seq,memory_order_acquire);
        if(before&1u) continue;
        out->mode=control->mode; out->phase=control->phase;
        out->flags=control->flags; out->detail_frames=control->detail_frames;
        out->update_period_ns=control->update_period_ns;
        out->render_period_ns=control->render_period_ns;
        out->generation=control->generation;
        out->measurement_epoch=control->measurement_epoch;
        atomic_thread_fence(memory_order_acquire);
        if(before==atomic_load_explicit(&control->command_seq,memory_order_relaxed)) return true;
    }
    return false;
}
static bool intervention_active(void) {
    if(control && frame_control.generation==0) (void)snapshot_control(&frame_control);
    return control && frame_control.mode!=OFF && frame_control.mode!=REFERENCE &&
           (frame_control.flags&INTERVENTION_ACTIVE)!=0;
}
static bool measurement_active(void) {
    if(control && frame_control.generation==0) (void)snapshot_control(&frame_control);
    return control && frame_control.mode!=OFF && (frame_control.flags&MEASURE_ENABLED)!=0;
}
static int scope_begin(unsigned id) {
    if(!measurement_active() || (frame_control.mode==REFERENCE && id!=SCOPE_UPDATE &&
        id!=SCOPE_LOOP && id!=SCOPE_RENDER && id!=SCOPE_PRESENT)) return 0;
    return eu4_scope_begin(&scope_tree,id,true,now_ns(CLOCK_UPTIME_RAW),
                           now_ns(CLOCK_THREAD_CPUTIME_ID));
}
static void scope_end(unsigned id,int entered) {
    if(!entered) return;
    eu4_scope_end(&scope_tree,id,entered,now_ns(CLOCK_UPTIME_RAW),
                  now_ns(CLOCK_THREAD_CPUTIME_ID));
    current_frame.flags|=scope_tree.flags;
}

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
static Producer *producer(void) {
    if(local_producer) return local_producer;
    pthread_mutex_lock(&producer_lock);
    unsigned count=atomic_load_explicit(&producer_count,memory_order_relaxed);
    if(count<PRODUCER_CAPACITY) {
        local_producer=calloc(1,sizeof(Producer));
        if(local_producer) {
            producers[count]=local_producer;
            atomic_store_explicit(&producer_count,count+1,memory_order_release);
        }
    }
    pthread_mutex_unlock(&producer_lock);
    if(!local_producer && control) atomic_fetch_add(&control->dropped_records,1);
    return local_producer;
}
static void event(unsigned id,uint64_t wall,uint64_t cpu) {
    Producer *p=producer(); if(!p) return;
    p->counts[id]++; p->wall[id]+=wall; p->cpu[id]+=cpu;
}
static void flush_counters(void) {
    Producer *p=local_producer; if(!p) return;
    for(unsigned i=0;i<HOOK_COUNT;i++) if(p->counts[i]) {
        atomic_fetch_add_explicit(&call_counts[i],p->counts[i],memory_order_relaxed);
        atomic_fetch_add_explicit(&call_wall_ns[i],p->wall[i],memory_order_relaxed);
        atomic_fetch_add_explicit(&call_cpu_ns[i],p->cpu[i],memory_order_relaxed);
        p->counts[i]=p->wall[i]=p->cpu[i]=0;
    }
}
static void publish_frame(Frame *f) {
    Producer *p=producer(); uint64_t h;
    flush_counters();
    if(!p || !eu4_spsc_reserve(&p->frame_queue,FRAME_CAPACITY,&h)) {
        f->flags|=1; atomic_fetch_add(&control->dropped_records,1); return;
    }
    p->frames[h%FRAME_CAPACITY].frame=*f;
    eu4_spsc_publish(&p->frame_queue,h);
}
static void write_record(const char *line,size_t size) {
    while(size) {
        ssize_t written=write(log_fd,line,size);
        if(written<0 && errno==EINTR) continue;
        if(written<=0) { atomic_fetch_add(&control->hook_failures,1); return; }
        line+=written; size-=(size_t)written;
    }
}
static void *writer(void *unused) {
    (void)unused;
    bool pending=true;
    while(atomic_load(&writer_running) || pending) {
        pending=false;
        unsigned count=atomic_load_explicit(&producer_count,memory_order_acquire);
        for(unsigned index=0;index<count;index++) {
        Producer *p=producers[index];
        uint64_t t=atomic_load_explicit(&p->frame_queue.tail,memory_order_relaxed);
        uint64_t h=atomic_load_explicit(&p->frame_queue.head,memory_order_acquire);
        pending|=t<h;
        while(t<h) {
            FrameSlot *s=&p->frames[t%FRAME_CAPACITY];
            Frame f=s->frame;
            char line[1536];
            unsigned long long values[]={f.update_id,f.render_id,f.present_id,f.phase,
                f.thread_id,f.wall_ns,f.cpu_ns,f.update_wall_ns,f.update_cpu_ns,
                f.draws,f.indices,f.triangles,f.draw_wall_ns,f.draw_cpu_ns,f.draw_timed_samples,
                f.buffer_calls,f.buffer_bytes,f.buffer_storage_bytes,f.buffer_upload_bytes,
                f.texture_calls,f.texture_bytes,f.state_calls,f.texture_binds,
                f.buffer_binds,f.program_switches,f.uniform_calls,f.uniform_bytes,
                f.bucket_calls,f.append_calls,f.idle_wall_ns,f.idle_cpu_ns,
                f.render_wall_ns,f.render_cpu_ns,f.map_wall_ns,f.map_cpu_ns,
                f.present_wall_ns,f.present_cpu_ns,f.wait_ns,f.wait_cpu_ns,f.sleep_ns,f.flags,
                f.forwarded_draws,f.suppressed_draws,f.render_attempts,f.render_executed,
                f.present_scene_calls,f.present_calls,f.measurement_epoch,f.start_ns,f.end_ns,
                f.render_start_ns,f.render_end_ns,f.present_time_ns,f.generation,f.sample_window,
                f.render_deadline_ns,f.render_lateness_ns,f.render_missed_deadlines,f.render_overruns,f.raster_challenges};
            size_t used=(size_t)snprintf(line,sizeof(line),"F");
            for(size_t i=0;i<sizeof(values)/sizeof(values[0]) && used<sizeof(line);i++)
                used+=(size_t)snprintf(line+used,sizeof(line)-used,",%llu",values[i]);
            int n=(int)used;
            if(used<sizeof(line)) { line[used++]='\n'; line[used]='\0'; n=(int)used; }
            if(n>0 && (size_t)n<sizeof(line)) write_record(line,(size_t)n);
            Eu4ScopeTree tree={.count=f.scopes.count,.flags=f.scopes.flags};
            memcpy(tree.nodes,f.scopes.nodes,f.scopes.count*sizeof(tree.nodes[0]));
            for(unsigned node=0;node<f.scopes.count;node++) {
                int m=eu4_scope_record(line,sizeof(line),&tree,node,f.update_id,f.phase,f.measurement_epoch);
                if(m>0 && (size_t)m<sizeof(line)) m+=snprintf(line+m-1,sizeof(line)-(size_t)m+1,",%llu,%llu,%llu\n",
                    (unsigned long long)f.thread_id,(unsigned long long)f.generation,(unsigned long long)f.sample_window)-1;
                if(m>0 && (size_t)m<sizeof(line)) write_record(line,(size_t)m);
            } t++;
            atomic_store_explicit(&p->frame_queue.tail,t,memory_order_release);
        }
        uint64_t dt=atomic_load_explicit(&p->detail_queue.tail,memory_order_relaxed);
        uint64_t dh=atomic_load_explicit(&p->detail_queue.head,memory_order_acquire);
        pending|=dt<dh;
        while(dt<dh) {
            DetailSlot *s=&p->details[dt%DETAIL_CAPACITY];
            char line[512];
            int n=snprintf(line,sizeof(line),"%c,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%llu\n",s->kind,
                (unsigned long long)s->a,(unsigned long long)s->b,
                (unsigned long long)s->c,(unsigned long long)s->d,
                (unsigned long long)s->e,(unsigned long long)s->f,
                (unsigned long long)s->g,(unsigned long long)s->h,
                (unsigned long long)s->i,(unsigned long long)s->j,
                (unsigned long long)s->k,(unsigned long long)s->l,
                (unsigned long long)s->epoch,(unsigned long long)s->update,(unsigned long long)s->phase,(unsigned long long)s->thread,
                (unsigned long long)s->generation,(unsigned long long)s->window,(unsigned long long)s->context);
            if(n>0 && (size_t)n<sizeof(line)) write_record(line,(size_t)n); dt++;
            atomic_store_explicit(&p->detail_queue.tail,dt,memory_order_release);
        }
        }
        struct timespec nap={0,1000000}; (void)nanosleep(&nap,NULL);
    }
    return NULL;
}
static void count_line(const char *kind,uint64_t a,uint64_t b,uint64_t c) {
    if(log_fd<0) return;
    char line[160]; int n=snprintf(line,sizeof(line),"%s,%llu,%llu,%llu\n",kind,
        (unsigned long long)a,(unsigned long long)b,(unsigned long long)c);
    if(n>0 && (size_t)n<sizeof(line)) write_record(line,(size_t)n);
}
static void hook_line(unsigned id) {
    if(log_fd<0) return;
    char line[160];
    int n=snprintf(line,sizeof(line),"C,%u,%llu,%llu,%llu\n",id,
        (unsigned long long)atomic_load(&call_counts[id]),
        (unsigned long long)atomic_load(&call_wall_ns[id]),
        (unsigned long long)atomic_load(&call_cpu_ns[id]));
    if(n>0 && (size_t)n<sizeof(line)) write_record(line,(size_t)n);
}
static void detail_line(const char *kind,uint64_t a,uint64_t b,uint64_t c,uint64_t d) {
    if(log_fd<0 || !kind || !kind[0]) return;
    Producer *p=producer(); uint64_t h;
    if(!p || !eu4_spsc_reserve(&p->detail_queue,DETAIL_CAPACITY,&h)) { atomic_fetch_add(&control->dropped_records,1); return; }
    DetailSlot *s=&p->details[h%DETAIL_CAPACITY]; s->kind=kind[0]; s->a=a;s->b=b;s->c=c;s->d=d;
    s->e=s->f=s->g=s->h=s->i=s->j=s->k=s->l=0;
    s->epoch=current_frame.measurement_epoch; s->update=current_frame.update_id; s->phase=current_frame.phase; s->thread=tid(); s->generation=frame_control.generation;
    s->window=current_frame.sample_window; s->context=(uintptr_t)CGLGetCurrentContext();
    if(detail_gpu_origin) {
        s->epoch=detail_gpu_origin->epoch; s->update=detail_gpu_origin->update; s->phase=detail_gpu_origin->phase;
        s->thread=detail_gpu_origin->thread; s->generation=detail_gpu_origin->generation; s->window=detail_gpu_origin->window;
    }
    eu4_spsc_publish(&p->detail_queue,h);
}
static void detail_line5(const char *kind,uint64_t a,uint64_t b,uint64_t c,uint64_t d,uint64_t e) {
    if(log_fd<0 || !kind || !kind[0]) return;
    Producer *p=producer(); uint64_t h;
    if(!p || !eu4_spsc_reserve(&p->detail_queue,DETAIL_CAPACITY,&h)) { atomic_fetch_add(&control->dropped_records,1); return; }
    DetailSlot *s=&p->details[h%DETAIL_CAPACITY]; s->kind=kind[0]; s->a=a;s->b=b;s->c=c;s->d=d;s->e=e;
    s->f=s->g=s->h=s->i=s->j=s->k=s->l=0;
    s->epoch=current_frame.measurement_epoch; s->update=current_frame.update_id; s->phase=current_frame.phase; s->thread=tid(); s->generation=frame_control.generation;
    s->window=current_frame.sample_window; s->context=(uintptr_t)CGLGetCurrentContext();
    if(detail_gpu_origin) {
        s->epoch=detail_gpu_origin->epoch; s->update=detail_gpu_origin->update; s->phase=detail_gpu_origin->phase;
        s->thread=detail_gpu_origin->thread; s->generation=detail_gpu_origin->generation; s->window=detail_gpu_origin->window;
    }
    eu4_spsc_publish(&p->detail_queue,h);
}
static void detail_line7(const char *kind,uint64_t a,uint64_t b,uint64_t c,uint64_t d,
                         uint64_t e,uint64_t f,uint64_t g) {
    if(log_fd<0 || !kind || !kind[0]) return;
    Producer *p=producer(); uint64_t h;
    if(!p || !eu4_spsc_reserve(&p->detail_queue,DETAIL_CAPACITY,&h)) { atomic_fetch_add(&control->dropped_records,1); return; }
    DetailSlot *s=&p->details[h%DETAIL_CAPACITY]; s->kind=kind[0];
    s->a=a;s->b=b;s->c=c;s->d=d;s->e=e;s->f=f;s->g=g;
    s->h=s->i=s->j=s->k=s->l=0;
    s->epoch=current_frame.measurement_epoch; s->update=current_frame.update_id; s->phase=current_frame.phase; s->thread=tid(); s->generation=frame_control.generation;
    s->window=current_frame.sample_window; s->context=(uintptr_t)CGLGetCurrentContext();
    if(detail_gpu_origin) {
        s->epoch=detail_gpu_origin->epoch; s->update=detail_gpu_origin->update; s->phase=detail_gpu_origin->phase;
        s->thread=detail_gpu_origin->thread; s->generation=detail_gpu_origin->generation; s->window=detail_gpu_origin->window;
    }
    eu4_spsc_publish(&p->detail_queue,h);
}
static void detail_line12(const char *kind,uint64_t a,uint64_t b,uint64_t c,uint64_t d,
                          uint64_t e,uint64_t f,uint64_t g,uint64_t h,uint64_t i,
                          uint64_t j,uint64_t k,uint64_t l) {
    if(log_fd<0 || !kind || !kind[0]) return;
    Producer *p=producer(); uint64_t head;
    if(!p || !eu4_spsc_reserve(&p->detail_queue,DETAIL_CAPACITY,&head)) { atomic_fetch_add(&control->dropped_records,1); return; }
    DetailSlot *s=&p->details[head%DETAIL_CAPACITY]; s->kind=kind[0];
    s->a=a;s->b=b;s->c=c;s->d=d;s->e=e;s->f=f;s->g=g;s->h=h;
    s->i=i;s->j=j;s->k=k;s->l=l;
    s->epoch=current_frame.measurement_epoch; s->update=current_frame.update_id; s->phase=current_frame.phase; s->thread=tid(); s->generation=frame_control.generation;
    s->window=current_frame.sample_window; s->context=(uintptr_t)CGLGetCurrentContext();
    if(detail_gpu_origin) {
        s->epoch=detail_gpu_origin->epoch; s->update=detail_gpu_origin->update; s->phase=detail_gpu_origin->phase;
        s->thread=detail_gpu_origin->thread; s->generation=detail_gpu_origin->generation; s->window=detail_gpu_origin->window;
    }
    eu4_spsc_publish(&p->detail_queue,head);
}
static void timestamp_event(unsigned kind,uint64_t timestamp) {
    if(!measurement_active()) return;
    detail_line12("E",current_frame.measurement_epoch,current_frame.update_id,
        current_frame.phase,kind,timestamp,current_frame.render_id,current_frame.present_id,0,0,0,0,0);
}
static VertexArrayState *vertex_state_for(CGLContextObj ctx,GLuint vao,bool create) {
    for(unsigned i=0;i<vertex_array_count;i++)
        if(vertex_arrays[i].used && vertex_arrays[i].context==ctx && vertex_arrays[i].vao==vao)
            return &vertex_arrays[i];
    if(!create || vertex_array_count>=64) return NULL;
    VertexArrayState *state=&vertex_arrays[vertex_array_count++];
    memset(state,0,sizeof(*state));state->used=true;state->context=ctx;state->vao=vao;
    return state;
}
static void seed_gl_state(void) {
    CGLContextObj ctx=CGLGetCurrentContext();
    if(!ctx || (state_seeded && state_context==ctx)) return;
    void (*get_integer)(GLenum,GLint *)=dlsym(RTLD_NEXT,"glGetIntegerv");
    if(!get_integer) { current_frame.flags|=2048; return; }
    GLint value=0,vao=0;
    get_integer(0x8B8D,&value); active_program=(GLuint)value;
    get_integer(0x8894,&value); bound_array_buffer=(GLuint)value;
    get_integer(0x8895,&value); bound_element_buffer=(GLuint)value;
    get_integer(0x85B5,&vao);
    vertex_array_state=vertex_state_for(ctx,(GLuint)vao,true);
    if(!vertex_array_state) {current_frame.flags|=2048;return;}
    vertex_array_state->element_buffer=bound_element_buffer;
    void (*get_attrib)(GLuint,GLenum,GLint *)=dlsym(RTLD_NEXT,"glGetVertexAttribiv");
    void (*get_pointer)(GLuint,GLenum,void **)=dlsym(RTLD_NEXT,"glGetVertexAttribPointerv");
    if(!get_attrib || !get_pointer) {current_frame.flags|=2048;return;}
    get_integer(0x8869,&value); if(value>32) value=32;
    for(GLuint i=0;i<(GLuint)value;i++) {
        VertexAttribute *a=&vertex_array_state->attributes[i]; GLint field=0;
        get_attrib(i,0x8622,&field);a->enabled=(GLboolean)field;
        get_attrib(i,0x8623,&a->size);get_attrib(i,0x8625,&field);a->type=(GLenum)field;
        get_attrib(i,0x886A,&field);a->normalized=(GLboolean)field;
        get_attrib(i,0x8624,&a->stride);get_attrib(i,0x889F,&field);a->buffer=(GLuint)field;
        get_attrib(i,0x88FE,&field);a->divisor=(GLuint)field;
        void *pointer=NULL;get_pointer(i,0x8645,&pointer);a->pointer=(uintptr_t)pointer;
    }
    bound_element_buffer=vertex_array_state->element_buffer;
    state_context=ctx; state_seeded=true;
}
static uint64_t vertex_source_signature(void) {
    if(!vertex_array_state) return 0;
    uint64_t hash=1469598103934665603ull;
    uint64_t identity=((uint64_t)(uintptr_t)vertex_array_state->context<<32)|vertex_array_state->vao;
    hash=(hash^identity)*1099511628211ull;
    for(unsigned i=0;i<32;i++) {
        VertexAttribute *a=&vertex_array_state->attributes[i];
        if(!a->enabled) continue;
        uint64_t fields[]={i,a->buffer,(uint32_t)a->size,a->type,a->normalized,
                           (uint32_t)a->stride,a->pointer,a->divisor};
        for(size_t j=0;j<sizeof(fields)/sizeof(fields[0]);j++)
            hash=(hash^fields[j])*1099511628211ull;
    }
    return hash;
}
static void draw_detail(uintptr_t caller,uint64_t api,GLenum mode,GLsizei count,
        GLenum type,uint64_t index_offset,uint64_t first,GLint base,GLsizei instances) {
    seed_gl_state();
    detail_line12("D",current_frame.render_id,
        caller>=image_base?caller-image_base:0,api,(uintptr_t)active_program,
        vertex_source_signature(),bound_element_buffer,mode,(uint32_t)count,type,
        index_offset?index_offset:first,(uint32_t)base,(uint32_t)instances);
}
static uintptr_t gpu_context(void) { return (uintptr_t)CGLGetCurrentContext(); }
static bool gpu_initialize_queries(unsigned *ids,unsigned count) {
    const GLubyte *version=glGetString(GL_VERSION);
    int major=0,minor=0;
    if(version) (void)sscanf((const char *)version,"%d.%d",&major,&minor);
    const char *extensions=NULL;
    if(major<3 || (major==3 && minor<3))
        extensions=(const char *)glGetString(GL_EXTENSIONS);
    if((major<3 || (major==3 && minor<3)) &&
       !extension_has(extensions,"GL_ARB_timer_query") &&
       !extension_has(extensions,"GL_EXT_timer_query")) return false;
    gpu_gen_queries=dlsym(RTLD_DEFAULT,"glGenQueries");
    gpu_query_counter=dlsym(RTLD_DEFAULT,"glQueryCounter");
    gpu_get_query_iv=dlsym(RTLD_DEFAULT,"glGetQueryObjectiv");
    gpu_get_query_u64=dlsym(RTLD_DEFAULT,"glGetQueryObjectui64v");
    if(!gpu_query_counter) gpu_query_counter=dlsym(RTLD_DEFAULT,"glQueryCounterEXT");
    if(!gpu_get_query_iv) gpu_get_query_iv=dlsym(RTLD_DEFAULT,"glGetQueryObjectivEXT");
    if(!gpu_get_query_u64) gpu_get_query_u64=dlsym(RTLD_DEFAULT,"glGetQueryObjectui64vEXT");
    if(!gpu_gen_queries || !gpu_query_counter || !gpu_get_query_iv || !gpu_get_query_u64) return false;
    gpu_gen_queries((GLsizei)count,ids);
    for(unsigned i=0;i<count;i++) if(!ids[i]) return false;
    return true;
}
static void gpu_stamp(unsigned query) { gpu_query_counter(query,GL_TIMESTAMP_MODEL); }
static bool gpu_available(unsigned query) {
    GLint ready=0; gpu_get_query_iv(query,GL_QUERY_AVAILABLE_MODEL,&ready); return ready!=0;
}
static uint64_t gpu_result(unsigned query) {
    GLuint64 value=0; gpu_get_query_u64(query,GL_QUERY_RESULT_MODEL,&value); return value;
}
static void gpu_emit(Eu4GpuIdentity id,uint64_t start,uint64_t end,unsigned missing) {
    detail_gpu_origin=&id;
    detail_line12("G",id.phase,id.render,start,end,id.lifetime,id.pass,id.sequence,
                  id.epoch,id.update,missing,0,0);
    detail_gpu_origin=NULL;
}
static Eu4GpuApi gpu_api={gpu_context,gpu_initialize_queries,gpu_stamp,gpu_available,gpu_result,gpu_emit};
static void gpu_begin_render(void) {
    pthread_mutex_lock(&gpu_lock); gpu_manager.registry=&gpu_registry;
    eu4_gpu_begin(&gpu_manager,&gpu_api,(Eu4GpuIdentity){.phase=current_frame.phase,
        .render=current_frame.render_id,.epoch=current_frame.measurement_epoch,.update=current_frame.update_id,.thread=tid(),.generation=frame_control.generation,.window=current_frame.sample_window});
    pthread_mutex_unlock(&gpu_lock);
}
static void gpu_map_marker(unsigned marker) {
    if(!gpu_manager.rendering) return;
    pthread_mutex_lock(&gpu_lock); eu4_gpu_pass(&gpu_manager,&gpu_api,marker?0:1); pthread_mutex_unlock(&gpu_lock);
}
static void gpu_end_render(void) {
    if(!gpu_manager.rendering) return;
    pthread_mutex_lock(&gpu_lock); eu4_gpu_end(&gpu_manager,&gpu_api); pthread_mutex_unlock(&gpu_lock);
}
static void uniform_snapshot(uint64_t site,uintptr_t program,GLint location,
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
       item->location==location && item->render_id+1==current_frame.render_id && item->bytes==bytes &&
       item->epoch==current_frame.measurement_epoch && item->window==current_frame.sample_window) {
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
    item->render_id=current_frame.render_id; item->epoch=current_frame.measurement_epoch; item->window=current_frame.sample_window; item->bytes=bytes;
    memcpy(item->data,payload,bytes);
}

typedef EU4Detour Detour;
static Detour detours[EU4_HOOK_COUNT];
static void restore_detours(void) {
    if(!eu4_detour_rollback(detours,EU4_HOOK_COUNT)) atomic_fetch_add(&control->hook_failures,1);
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
    if(scope_tree.depth) {
        /* Recursive updates stay on the same physical frame and scope path. */
        atomic_fetch_add(&updates,1);
        int nested=scope_begin(SCOPE_UPDATE);
        real_update(self,force); scope_end(SCOPE_UPDATE,nested);
        event(HOOK_UPDATE,0,0); return;
    }
    uint64_t previous_period=frame_control.update_period_ns;
    uint32_t previous_mode=frame_control.mode;
    uint64_t id=atomic_fetch_add(&updates,1)+1;
    if(!snapshot_control(&frame_control)) {
        real_update(self,force);
        return;
    }
    if(previous_period!=frame_control.update_period_ns || previous_mode!=frame_control.mode) {
        next_deadline=0; next_render_deadline=0;
    }
    bool measuring=measurement_active();
    scope_tree=(Eu4ScopeTree){0};
    if(measuring && !was_measurement_active) {
        state_seeded=false;vertex_array_state=NULL;
    }
    if(!measuring && was_measurement_active) {
        pthread_mutex_lock(&gpu_lock); eu4_gpu_drain(&gpu_manager,&gpu_api); pthread_mutex_unlock(&gpu_lock);
    }
    was_measurement_active=measuring;
    if(!measuring) current_frame=(Frame){0};
    uint64_t start=now_ns(CLOCK_UPTIME_RAW), cpu=now_ns(CLOCK_THREAD_CPUTIME_ID);
    if(measuring) current_frame=(Frame){.update_id=id,.phase=frame_control.phase,
                                       .thread_id=tid(),.measurement_epoch=frame_control.measurement_epoch,.start_ns=start,.generation=frame_control.generation};

    if(measuring) timestamp_event(1,start);
    int loop_scope=scope_begin(SCOPE_LOOP);
    int update_scope=scope_begin(SCOPE_UPDATE);
    real_update(self,force);
    scope_end(SCOPE_UPDATE,update_scope);
    uint64_t end=now_ns(CLOCK_UPTIME_RAW), end_cpu=now_ns(CLOCK_THREAD_CPUTIME_ID);
    if(measuring) {
        current_frame.update_wall_ns=end-start; current_frame.update_cpu_ns=end_cpu-cpu;
        event(HOOK_UPDATE,end-start,end_cpu-cpu);
    }
    uint64_t period=intervention_active()?frame_control.update_period_ns:0;
    if(period) {
        if(!next_deadline) next_deadline=end+period;
        else if(end<next_deadline) {
            uint64_t deadline=next_deadline,remain=deadline-end;
            struct timespec ts={(time_t)(remain/1000000000ull),(long)(remain%1000000000ull)};
            int sleep_scope=scope_begin(SCOPE_CADENCE_SLEEP);
            (void)nanosleep(&ts,NULL);
            scope_end(SCOPE_CADENCE_SLEEP,sleep_scope);
            if(measuring) current_frame.sleep_ns=now_ns(CLOCK_UPTIME_RAW)-end;
            next_deadline=deadline+period;
        } else {
            if(measuring) current_frame.flags|=2;
            next_deadline=end+period;
        }
    } else next_deadline=0;
    scope_end(SCOPE_LOOP,loop_scope);
    if(measuring) {
        current_frame.wall_ns=now_ns(CLOCK_UPTIME_RAW)-start;
        current_frame.cpu_ns=now_ns(CLOCK_THREAD_CPUTIME_ID)-cpu;
        current_frame.end_ns=now_ns(CLOCK_UPTIME_RAW);
        eu4_scope_snapshot(&current_frame.scopes,&scope_tree);
        ControlSnapshot after={0};
        if(snapshot_control(&after) && after.measurement_epoch==frame_control.measurement_epoch && after.mode==frame_control.mode &&
           (after.flags&MEASURE_ENABLED)) publish_frame(&current_frame);
        else { current_frame.flags|=4096; publish_frame(&current_frame); }
    }
    if(control && atomic_load(&control->ack_generation)!=frame_control.generation) {
        atomic_store(&control->ack_time_ns,now_ns(CLOCK_UPTIME_RAW));
        atomic_store_explicit(&control->ack_generation,frame_control.generation,memory_order_release);
    }
}
static void hook_idle(void *self,bool force) {
    if(!control || !(frame_control.flags&MEASURE_ENABLED) || (frame_control.mode==OFF || frame_control.mode==REFERENCE)) {
        real_idle(self,force); return;
    }
    uint64_t w=now_ns(CLOCK_UPTIME_RAW),c=now_ns(CLOCK_THREAD_CPUTIME_ID);
    int entered=scope_begin(SCOPE_IDLE);
    real_idle(self,force);
    scope_end(SCOPE_IDLE,entered);
    current_frame.idle_wall_ns=now_ns(CLOCK_UPTIME_RAW)-w;
    current_frame.idle_cpu_ns=now_ns(CLOCK_THREAD_CPUTIME_ID)-c;
    event(HOOK_IDLE,current_frame.idle_wall_ns,current_frame.idle_cpu_ns);
}
static void hook_render(void *self) {
    uint64_t id=atomic_fetch_add(&renders,1)+1;
    if(!control) { real_render(self); return; }
    if(measurement_active())
        { current_frame.render_id=id; current_frame.render_attempts++; current_frame.render_start_ns=now_ns(CLOCK_UPTIME_RAW); timestamp_event(2,current_frame.render_start_ns); }
    uint32_t mode=frame_control.mode;
    uint64_t now=now_ns(CLOCK_UPTIME_RAW);
    uint64_t render_period=frame_control.render_period_ns;
    if(render_period && (mode==CADENCE_30 || mode==CADENCE_15)) {
        Eu4RenderDecision decision=eu4_render_decide(now,&next_render_deadline,render_period);
        current_frame.render_deadline_ns=decision.scheduled;
        current_frame.render_lateness_ns=decision.lateness;
        current_frame.render_missed_deadlines+=decision.missed;
        if(!decision.due) {
            detail_active=false; return;
        }
    } else next_render_deadline=0;
    if(mode==OFF || !(frame_control.flags&INTERVENTION_ACTIVE)) { real_render(self); return; }
    if(detail_generation!=frame_control.generation) {
        detail_generation=frame_control.generation;
        detail_remaining=frame_control.detail_frames;
    }
    detail_active=detail_remaining>0;
    if(detail_active) { detail_remaining--; current_frame.sample_window=detail_generation; }
    bool skip=mode==SKIP_RENDER;
    if(skip) { detail_active=false; return; }
    if(measurement_active()) { current_frame.render_executed++; current_frame.render_start_ns=now_ns(CLOCK_UPTIME_RAW); timestamp_event(3,current_frame.render_start_ns); }
    uint64_t w=now_ns(CLOCK_UPTIME_RAW),c=now_ns(CLOCK_THREAD_CPUTIME_ID);
    if(detail_active && measurement_active()) gpu_begin_render();
    CGLContextObj render_context=NULL;
    static void (*enable)(GLenum)=glEnable; static void (*disable)(GLenum)=glDisable;
    static CGLError (*set_context)(CGLContextObj)=CGLSetCurrentContext;
    if(mode==RASTER_SUPPRESS) {
        render_context=CGLGetCurrentContext();
        raster_active=true; raster_context_count=0;
        if(render_context && enable) {
            GLboolean (*is_enabled)(GLenum)=glIsEnabled;
            if(is_enabled) {
                raster_contexts[0]=(RasterContext){(uintptr_t)render_context,is_enabled(GL_RASTERIZER_DISCARD)};
                raster_context_count=1;
                if(!raster_contexts[0].requested) {
                    enable(GL_RASTERIZER_DISCARD);
                    if(!is_enabled(GL_RASTERIZER_DISCARD)) current_frame.flags|=8;
                }
            } else current_frame.flags|=8;
        } else current_frame.flags|=8;
    }
    int render_scope=scope_begin(SCOPE_RENDER);
    real_render(self);
    scope_end(SCOPE_RENDER,render_scope);
    if(measurement_active()) { gpu_end_render(); current_frame.render_end_ns=now_ns(CLOCK_UPTIME_RAW);
        if(render_period && current_frame.render_end_ns-current_frame.render_start_ns>render_period) current_frame.render_overruns++; }
    if(mode==RASTER_SUPPRESS) {
        CGLContextObj restore=CGLGetCurrentContext();
        if(render_context && set_context && disable) {
            for(unsigned i=0;i<raster_context_count;i++) {
                if(set_context((CGLContextObj)raster_contexts[i].context)!=kCGLNoError) { current_frame.flags|=16; continue; }
                if(raster_contexts[i].requested) enable(GL_RASTERIZER_DISCARD);
                else disable(GL_RASTERIZER_DISCARD);
                if(glIsEnabled(GL_RASTERIZER_DISCARD)!=raster_contexts[i].requested) current_frame.flags|=32;
            }
            if(set_context(restore)!=kCGLNoError) current_frame.flags|=16;
        } else current_frame.flags|=16;
        raster_active=false; raster_context_count=0;
    }
    current_frame.render_wall_ns=now_ns(CLOCK_UPTIME_RAW)-w;
    current_frame.render_cpu_ns=now_ns(CLOCK_THREAD_CPUTIME_ID)-c;
    event(HOOK_RENDER,current_frame.render_wall_ns,current_frame.render_cpu_ns);
    detail_active=false;
}
static void hook_map(void *self,void *ctx,const void *camera,float alpha,bool flag) {
    if(!intervention_active()) { real_map(self,ctx,camera,alpha,flag); return; }
    uint64_t w=now_ns(CLOCK_UPTIME_RAW),c=now_ns(CLOCK_THREAD_CPUTIME_ID);
    int entered=scope_begin(SCOPE_MAP);
    gpu_map_marker(0);
    real_map(self,ctx,camera,alpha,flag);
    gpu_map_marker(1);
    scope_end(SCOPE_MAP,entered);
    current_frame.map_wall_ns=now_ns(CLOCK_UPTIME_RAW)-w;
    current_frame.map_cpu_ns=now_ns(CLOCK_THREAD_CPUTIME_ID)-c;
    event(HOOK_MAP,current_frame.map_wall_ns,current_frame.map_cpu_ns);
}
static void hook_present(void *self) {
    if(!measurement_active()) { real_present(self); return; }
    if(measurement_active()) current_frame.present_scene_calls++;
    uint64_t w=now_ns(CLOCK_UPTIME_RAW),c=now_ns(CLOCK_THREAD_CPUTIME_ID);
    int entered=scope_begin(SCOPE_PRESENT);
    real_present(self);
    scope_end(SCOPE_PRESENT,entered);
    current_frame.present_wall_ns=now_ns(CLOCK_UPTIME_RAW)-w;
    current_frame.present_cpu_ns=now_ns(CLOCK_THREAD_CPUTIME_ID)-c;
    event(HOOK_PRESENT_SCENE,current_frame.present_wall_ns,current_frame.present_cpu_ns);
}
static void hook_add(const void *self,int layer,const void *camera) {
    if(!intervention_active()) { real_add(self,layer,camera); return; }
    current_frame.bucket_calls++; uint64_t w=now_ns(CLOCK_UPTIME_RAW),c=now_ns(CLOCK_THREAD_CPUTIME_ID);
    int entered=scope_begin(SCOPE_ADD_BUCKET);
    real_add(self,layer,camera); scope_end(SCOPE_ADD_BUCKET,entered);
    event(HOOK_ADD_BUCKET,now_ns(CLOCK_UPTIME_RAW)-w,
                                      now_ns(CLOCK_THREAD_CPUTIME_ID)-c);
}
static void hook_append(void *self,const void *record) {
    if(!intervention_active()) { real_append(self,record); return; }
    current_frame.append_calls++; int entered=scope_begin(SCOPE_APPEND);
    real_append(self,record); scope_end(SCOPE_APPEND,entered);
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
    Detour *d=&detours[i];
    if(!eu4_detour_install(d,(void *)at,patch,expected,replacement,site->lengths,site->instruction_count)) {
        atomic_fetch_add(&control->hook_failures,1); return;
    }
    *original=d->trampoline;
}

static CGLError hooked_flush(CGLContextObj ctx) {
    uint64_t id=atomic_fetch_add(&presents,1)+1; current_frame.present_id=id;
    if(measurement_active()) { current_frame.present_calls++; current_frame.present_time_ns=now_ns(CLOCK_UPTIME_RAW); timestamp_event(4,current_frame.present_time_ns); }
    uint64_t w=now_ns(CLOCK_UPTIME_RAW),c=now_ns(CLOCK_THREAD_CPUTIME_ID);
    static CGLError (*real_flush)(CGLContextObj);
    if(!real_flush) real_flush=CGLFlushDrawable;
    int flush_scope=scope_begin(SCOPE_FLUSH);
    CGLError result=real_flush?real_flush(ctx):kCGLBadContext;
    scope_end(SCOPE_FLUSH,flush_scope);
    if(!intervention_active()) { auto_probe_swap(result); return result; }
    uint64_t dw=now_ns(CLOCK_UPTIME_RAW)-w,dc=now_ns(CLOCK_THREAD_CPUTIME_ID)-c;
    current_frame.wait_ns+=dw; current_frame.wait_cpu_ns+=dc; event(HOOK_SWAP,dw,dc);
    if(measurement_active()) detail_line12("P",id,frame_control.phase,dw,0,0,0,0,0,0,0,0,0);
    auto_probe_swap(result);
    return result;
}
static void gl_draw_elements(GLenum mode,GLsizei count,GLenum type,const void *indices) {
    static void (*real_fn)(GLenum,GLsizei,GLenum,const void *)=glDrawElements;
    if(!intervention_active()) { if(real_fn) real_fn(mode,count,type,indices); return; }
    current_frame.draws++; if(count>0) current_frame.indices+=(uint64_t)count;
    current_frame.triangles+=estimated_triangles(mode,count,1);
    if(detail_active) {
        uintptr_t caller=(uintptr_t)__builtin_return_address(0);
        draw_detail(caller,1,mode,count,type,(uintptr_t)indices,0,0,1);
    }
    bool measuring=measurement_active();
    bool timed=measuring && detail_active && (++draw_sample_seq&255u)==1u;
    uint64_t w=timed?now_ns(CLOCK_UPTIME_RAW):0,c=timed?now_ns(CLOCK_THREAD_CPUTIME_ID):0;
    if(control && frame_control.mode==DROP_DRAWS) current_frame.suppressed_draws++;
    else { if(real_fn) real_fn(mode,count,type,indices); current_frame.forwarded_draws++; }
    uint64_t dw=timed?now_ns(CLOCK_UPTIME_RAW)-w:0,dc=timed?now_ns(CLOCK_THREAD_CPUTIME_ID)-c:0;
    current_frame.draw_wall_ns+=dw*256; current_frame.draw_cpu_ns+=dc*256;
    if(timed) current_frame.draw_timed_samples++;
    if(measuring) event(HOOK_GL_DRAW,dw,dc);
}
static void gl_draw_base(GLenum mode,GLsizei count,GLenum type,const void *indices,GLint base) {
    static void (*real_fn)(GLenum,GLsizei,GLenum,const void *,GLint)=glDrawElementsBaseVertex;
    if(!intervention_active()) { if(real_fn) real_fn(mode,count,type,indices,base); return; }
    current_frame.draws++; if(count>0) current_frame.indices+=(uint64_t)count;
    current_frame.triangles+=estimated_triangles(mode,count,1);
    if(detail_active) {
        uintptr_t caller=(uintptr_t)__builtin_return_address(0);
        draw_detail(caller,2,mode,count,type,(uintptr_t)indices,0,base,1);
    }
    bool measuring=measurement_active();
    bool timed=measuring && detail_active && (++draw_sample_seq&255u)==1u;
    uint64_t w=timed?now_ns(CLOCK_UPTIME_RAW):0,c=timed?now_ns(CLOCK_THREAD_CPUTIME_ID):0;
    uint32_t m=control?frame_control.mode:OFF;
    if(m==DROP_DRAWS) current_frame.suppressed_draws++;
    else { if(real_fn) real_fn(mode,count,type,indices,base); current_frame.forwarded_draws++; }
    uint64_t dw=timed?now_ns(CLOCK_UPTIME_RAW)-w:0,dc=timed?now_ns(CLOCK_THREAD_CPUTIME_ID)-c:0;
    current_frame.draw_wall_ns+=dw*256; current_frame.draw_cpu_ns+=dc*256;
    if(timed) current_frame.draw_timed_samples++;
    if(measuring) event(HOOK_GL_DRAW,dw,dc);
}
static void gl_draw_arrays(GLenum mode,GLint first,GLsizei count) {
    static void (*real_fn)(GLenum,GLint,GLsizei)=glDrawArrays;
    if(!intervention_active()) { if(real_fn) real_fn(mode,first,count); return; }
    current_frame.draws++; if(count>0) current_frame.indices+=(uint64_t)count;
    current_frame.triangles+=estimated_triangles(mode,count,1);
    if(detail_active) {
        uintptr_t caller=(uintptr_t)__builtin_return_address(0);
        draw_detail(caller,3,mode,count,0,0,(uint32_t)first,0,1);
    }
    bool measuring=measurement_active();
    bool timed=measuring && detail_active && (++draw_sample_seq&255u)==1u;
    uint64_t w=timed?now_ns(CLOCK_UPTIME_RAW):0,c=timed?now_ns(CLOCK_THREAD_CPUTIME_ID):0;
    uint32_t m=control?frame_control.mode:OFF;
    if(m==DROP_DRAWS) current_frame.suppressed_draws++;
    else { if(real_fn) real_fn(mode,first,count); current_frame.forwarded_draws++; }
    uint64_t dw=timed?now_ns(CLOCK_UPTIME_RAW)-w:0,dc=timed?now_ns(CLOCK_THREAD_CPUTIME_ID)-c:0;
    current_frame.draw_wall_ns+=dw*256; current_frame.draw_cpu_ns+=dc*256;
    if(timed) current_frame.draw_timed_samples++;
    if(measuring) event(HOOK_GL_DRAW,dw,dc);
}
static void gl_draw_elements_instanced(GLenum mode,GLsizei count,GLenum type,
        const void *indices,GLsizei instances) {
    static void (*real_fn)(GLenum,GLsizei,GLenum,const void *,GLsizei)=glDrawElementsInstanced;
    if(!intervention_active()) { if(real_fn) real_fn(mode,count,type,indices,instances); return; }
    current_frame.draws++; if(count>0 && instances>0) current_frame.indices+=(uint64_t)count*(uint64_t)instances;
    current_frame.triangles+=estimated_triangles(mode,count,instances>0?(uint64_t)instances:0);
    if(detail_active) {
        uintptr_t caller=(uintptr_t)__builtin_return_address(0);
        draw_detail(caller,4,mode,count,type,(uintptr_t)indices,0,0,instances);
    }
    bool measuring=measurement_active();
    bool timed=measuring && detail_active && (++draw_sample_seq&255u)==1u;
    uint64_t w=timed?now_ns(CLOCK_UPTIME_RAW):0,c=timed?now_ns(CLOCK_THREAD_CPUTIME_ID):0;
    uint32_t m=frame_control.mode;
    if(m==DROP_DRAWS) current_frame.suppressed_draws++;
    else { if(real_fn) real_fn(mode,count,type,indices,instances); current_frame.forwarded_draws++; }
    uint64_t dw=timed?now_ns(CLOCK_UPTIME_RAW)-w:0,dc=timed?now_ns(CLOCK_THREAD_CPUTIME_ID)-c:0;
    current_frame.draw_wall_ns+=dw*256;current_frame.draw_cpu_ns+=dc*256;
    if(timed) current_frame.draw_timed_samples++;
    if(measuring) event(HOOK_GL_DRAW,dw,dc);
}
static void gl_draw_arrays_instanced(GLenum mode,GLint first,GLsizei count,GLsizei instances) {
    static void (*real_fn)(GLenum,GLint,GLsizei,GLsizei)=glDrawArraysInstanced;
    if(!intervention_active()) { if(real_fn) real_fn(mode,first,count,instances); return; }
    current_frame.draws++; if(count>0 && instances>0) current_frame.indices+=(uint64_t)count*(uint64_t)instances;
    current_frame.triangles+=estimated_triangles(mode,count,instances>0?(uint64_t)instances:0);
    if(detail_active) {
        uintptr_t caller=(uintptr_t)__builtin_return_address(0);
        draw_detail(caller,5,mode,count,0,0,(uint32_t)first,0,instances);
    }
    bool measuring=measurement_active();
    bool timed=measuring && detail_active && (++draw_sample_seq&255u)==1u;
    uint64_t w=timed?now_ns(CLOCK_UPTIME_RAW):0,c=timed?now_ns(CLOCK_THREAD_CPUTIME_ID):0;
    uint32_t m=frame_control.mode;
    if(m==DROP_DRAWS) current_frame.suppressed_draws++;
    else { if(real_fn) real_fn(mode,first,count,instances); current_frame.forwarded_draws++; }
    uint64_t dw=timed?now_ns(CLOCK_UPTIME_RAW)-w:0,dc=timed?now_ns(CLOCK_THREAD_CPUTIME_ID)-c:0;
    current_frame.draw_wall_ns+=dw*256;current_frame.draw_cpu_ns+=dc*256;
    if(timed) current_frame.draw_timed_samples++;
    if(measuring) event(HOOK_GL_DRAW,dw,dc);
}
static void gl_draw_elements_instanced_base(GLenum mode,GLsizei count,GLenum type,
        const void *indices,GLsizei instances,GLint base) {
    static void (*real_fn)(GLenum,GLsizei,GLenum,const void *,GLsizei,GLint)=glDrawElementsInstancedBaseVertex;
    if(!intervention_active()) { if(real_fn) real_fn(mode,count,type,indices,instances,base); return; }
    current_frame.draws++; if(count>0 && instances>0) current_frame.indices+=(uint64_t)count*(uint64_t)instances;
    current_frame.triangles+=estimated_triangles(mode,count,instances>0?(uint64_t)instances:0);
    if(detail_active) {
        uintptr_t caller=(uintptr_t)__builtin_return_address(0);
        draw_detail(caller,6,mode,count,type,(uintptr_t)indices,0,base,instances);
    }
    bool measuring=measurement_active();
    bool timed=measuring && detail_active && (++draw_sample_seq&255u)==1u;
    uint64_t w=timed?now_ns(CLOCK_UPTIME_RAW):0,c=timed?now_ns(CLOCK_THREAD_CPUTIME_ID):0;
    uint32_t m=frame_control.mode;
    if(m==DROP_DRAWS) current_frame.suppressed_draws++;
    else { if(real_fn) real_fn(mode,count,type,indices,instances,base); current_frame.forwarded_draws++; }
    uint64_t dw=timed?now_ns(CLOCK_UPTIME_RAW)-w:0,dc=timed?now_ns(CLOCK_THREAD_CPUTIME_ID)-c:0;
    current_frame.draw_wall_ns+=dw*256;current_frame.draw_cpu_ns+=dc*256;
    if(timed) current_frame.draw_timed_samples++;
    if(measuring) event(HOOK_GL_DRAW,dw,dc);
}
static void gl_buffer_data(GLenum target,GLsizeiptr size,const void *data,GLenum usage) {
    static void (*real_fn)(GLenum,GLsizeiptr,const void *,GLenum)=glBufferData;
    if(!intervention_active()) { if(real_fn) real_fn(target,size,data,usage); return; }
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
    static void (*real_fn)(GLenum,GLintptr,GLsizeiptr,const void *)=glBufferSubData;
    if(!intervention_active()) { if(real_fn) real_fn(target,offset,size,data); return; }
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
static void uniform_record(uintptr_t site,GLint location,const void *data,size_t bytes) {
    if(!intervention_active()) return;
    if(frame_control.mode!=REFERENCE && measurement_active()) {
        event(HOOK_GL_UNIFORM,0,0);
        current_frame.uniform_calls++;
        current_frame.uniform_bytes+=bytes;
    }
    if(frame_control.mode!=REFERENCE && measurement_active() && detail_active && data && bytes>0 && bytes<=1048576) {
        detail_line5("U",current_frame.render_id,site,
            active_program,(uint32_t)location,((uint64_t)bytes<<32)|
            (uint32_t)hash_bytes(data,bytes));
    }
}
static void gl_uniform4fv(GLint location,GLsizei count,const GLfloat *data) {
    static void (*real_fn)(GLint,GLsizei,const GLfloat *)=glUniform4fv;
    if(!intervention_active()) { if(real_fn) real_fn(location,count,data); return; }
    size_t bytes=count>0?(size_t)count*16:0;
    uintptr_t caller=(uintptr_t)__builtin_return_address(0);
    uniform_record(caller>=image_base?caller-image_base:0,location,data,bytes);
    if(frame_control.mode!=REFERENCE && measurement_active() && detail_active && data && count>0 && bytes<=256) {
        uniform_snapshot(caller>=image_base?caller-image_base:0,active_program,location,data,bytes);
    }
    if(real_fn) real_fn(location,count,data);
}
#define DEFINE_UNIFORM_VECTOR(name,type,components) \
static void gl_##name(GLint location,GLsizei count,const type *data) { \
    typedef void (*Fn)(GLint,GLsizei,const type *); static Fn real_fn; \
    if(!real_fn) real_fn=name; \
    if(!intervention_active()) { if(real_fn) real_fn(location,count,data); return; } \
    size_t bytes=count>0?(size_t)count*(components)*sizeof(type):0; \
    uintptr_t caller=(uintptr_t)__builtin_return_address(0); \
    uniform_record(caller>=image_base?caller-image_base:0,location,data,bytes); \
    if(real_fn) real_fn(location,count,data); \
}
#define DEFINE_UNIFORM_SCALAR(name,type) \
static void gl_##name(GLint location,type value) { \
    typedef void (*Fn)(GLint,type); static Fn real_fn; \
    if(!real_fn) real_fn=name; \
    if(!intervention_active()) { if(real_fn) real_fn(location,value); return; } \
    uintptr_t caller=(uintptr_t)__builtin_return_address(0); \
    uniform_record(caller>=image_base?caller-image_base:0,location,&value,sizeof(value)); \
    if(real_fn) real_fn(location,value); \
}
#define DEFINE_UNIFORM_MATRIX(name,dim) \
static void gl_##name(GLint location,GLsizei count,GLboolean transpose,const GLfloat *data) { \
    typedef void (*Fn)(GLint,GLsizei,GLboolean,const GLfloat *); static Fn real_fn; \
    if(!real_fn) real_fn=name; \
    if(!intervention_active()) { if(real_fn) real_fn(location,count,transpose,data); return; } \
    size_t bytes=count>0?(size_t)count*(dim)*(dim)*sizeof(GLfloat):0; \
    uintptr_t caller=(uintptr_t)__builtin_return_address(0); \
    uniform_record(caller>=image_base?caller-image_base:0,location,data,bytes); \
    if(real_fn) real_fn(location,count,transpose,data); \
}
#define DEFINE_UNIFORM_COMPONENTS_3(name,type) \
static void gl_##name(GLint location,type v0,type v1,type v2) { \
    typedef void (*Fn)(GLint,type,type,type); static Fn real_fn; \
    if(!real_fn) real_fn=name; \
    if(!intervention_active()) { if(real_fn) real_fn(location,v0,v1,v2); return; } \
    type values[3]={v0,v1,v2}; uintptr_t caller=(uintptr_t)__builtin_return_address(0); \
    uniform_record(caller>=image_base?caller-image_base:0,location,values,3*sizeof(type)); \
    if(real_fn) real_fn(location,v0,v1,v2); \
}
#define DEFINE_UNIFORM_COMPONENTS_4(name,type) \
static void gl_##name(GLint location,type v0,type v1,type v2,type v3) { \
    typedef void (*Fn)(GLint,type,type,type,type); static Fn real_fn; \
    if(!real_fn) real_fn=name; \
    if(!intervention_active()) { if(real_fn) real_fn(location,v0,v1,v2,v3); return; } \
    type values[4]={v0,v1,v2,v3}; uintptr_t caller=(uintptr_t)__builtin_return_address(0); \
    uniform_record(caller>=image_base?caller-image_base:0,location,values,4*sizeof(type)); \
    if(real_fn) real_fn(location,v0,v1,v2,v3); \
}
#define DEFINE_UNIFORM_COMPONENTS_2(name,type) \
static void gl_##name(GLint location,type v0,type v1) { \
    typedef void (*Fn)(GLint,type,type); static Fn real_fn; \
    if(!real_fn) real_fn=name; \
    if(!intervention_active()) { if(real_fn) real_fn(location,v0,v1); return; } \
    type values[2]={v0,v1}; uintptr_t caller=(uintptr_t)__builtin_return_address(0); \
    uniform_record(caller>=image_base?caller-image_base:0,location,values,2*sizeof(type)); \
    if(real_fn) real_fn(location,v0,v1); \
}
DEFINE_UNIFORM_VECTOR(glUniform1fv,GLfloat,1)
DEFINE_UNIFORM_VECTOR(glUniform2fv,GLfloat,2)
DEFINE_UNIFORM_VECTOR(glUniform3fv,GLfloat,3)
DEFINE_UNIFORM_VECTOR(glUniform1iv,GLint,1)
DEFINE_UNIFORM_VECTOR(glUniform2iv,GLint,2)
DEFINE_UNIFORM_VECTOR(glUniform3iv,GLint,3)
DEFINE_UNIFORM_VECTOR(glUniform4iv,GLint,4)
DEFINE_UNIFORM_MATRIX(glUniformMatrix2fv,2)
DEFINE_UNIFORM_MATRIX(glUniformMatrix3fv,3)
DEFINE_UNIFORM_MATRIX(glUniformMatrix4fv,4)
DEFINE_UNIFORM_SCALAR(glUniform1f,GLfloat)
DEFINE_UNIFORM_COMPONENTS_2(glUniform2f,GLfloat)
DEFINE_UNIFORM_COMPONENTS_2(glUniform2i,GLint)
DEFINE_UNIFORM_COMPONENTS_3(glUniform3f,GLfloat)
DEFINE_UNIFORM_COMPONENTS_4(glUniform4f,GLfloat)
DEFINE_UNIFORM_COMPONENTS_3(glUniform3i,GLint)
DEFINE_UNIFORM_COMPONENTS_4(glUniform4i,GLint)
#undef DEFINE_UNIFORM_VECTOR
#undef DEFINE_UNIFORM_SCALAR
#undef DEFINE_UNIFORM_MATRIX
#undef DEFINE_UNIFORM_COMPONENTS_3
#undef DEFINE_UNIFORM_COMPONENTS_4
#undef DEFINE_UNIFORM_COMPONENTS_2
static void gl_uniform1i(GLint location,GLint value) {
    static void (*real_fn)(GLint,GLint)=glUniform1i;
    if(frame_control.mode!=REFERENCE && measurement_active()) {
        event(HOOK_GL_UNIFORM,0,0); current_frame.uniform_calls++;
        current_frame.uniform_bytes+=sizeof(value);
        if(frame_control.mode!=REFERENCE && measurement_active() && detail_active) {
            uintptr_t caller=(uintptr_t)__builtin_return_address(0);
            detail_line5("u",current_frame.render_id,
                caller>=image_base?caller-image_base:0,active_program,
                (uint32_t)location,(uint32_t)value);
        }
    }
    if(real_fn) real_fn(location,value);
}
static uint64_t state_pack3(uint64_t a,uint64_t b,uint64_t c) {
    return ((a&0x1fffffull)<<42)|((b&0x1fffffull)<<21)|(c&0x1fffffull);
}
static void state_record(uintptr_t caller,uint64_t operation,uint64_t value) {
    if(!measurement_active()) return;
    event(HOOK_GL_STATE,0,0); current_frame.state_calls++;
    if(detail_active) detail_line("S",current_frame.render_id,
        caller>=image_base?caller-image_base:0,operation,value);
}
#define DEFINE_STATE_ONE(name,type,op) \
static void gl_##name(type value) { \
    typedef void (*Fn)(type); static Fn real_fn; \
    if(!real_fn) real_fn=name; \
    if(intervention_active()) state_record((uintptr_t)__builtin_return_address(0),op,(uint64_t)value); \
    if(real_fn) real_fn(value); \
}
DEFINE_STATE_ONE(glDepthFunc,GLenum,20)
DEFINE_STATE_ONE(glDepthMask,GLboolean,21)
DEFINE_STATE_ONE(glCullFace,GLenum,22)
DEFINE_STATE_ONE(glFrontFace,GLenum,23)
DEFINE_STATE_ONE(glStencilMask,GLuint,24)
DEFINE_STATE_ONE(glClear,GLbitfield,25)
DEFINE_STATE_ONE(glBlendEquation,GLenum,36)
static void gl_glBlendFunc(GLenum source,GLenum destination) {
    static void (*real_fn)(GLenum,GLenum)=glBlendFunc;
    if(intervention_active()) state_record((uintptr_t)__builtin_return_address(0),26,
        ((uint64_t)source<<32)|destination);
    if(real_fn) real_fn(source,destination);
}
static void gl_glBlendFuncSeparate(GLenum sr,GLenum dr,GLenum sa,GLenum da) {
    static void (*real_fn)(GLenum,GLenum,GLenum,GLenum)=glBlendFuncSeparate;
    if(intervention_active()) state_record((uintptr_t)__builtin_return_address(0),27,
        state_pack3(((uint64_t)sr<<32)|dr,sa,da));
    if(real_fn) real_fn(sr,dr,sa,da);
}
static void gl_glBlendEquationSeparate(GLenum rgb,GLenum alpha) {
    static void (*real_fn)(GLenum,GLenum)=glBlendEquationSeparate;
    if(intervention_active()) state_record((uintptr_t)__builtin_return_address(0),37,
        ((uint64_t)rgb<<32)|alpha);
    if(real_fn) real_fn(rgb,alpha);
}
static void gl_glColorMask(GLboolean r,GLboolean g,GLboolean b,GLboolean a) {
    static void (*real_fn)(GLboolean,GLboolean,GLboolean,GLboolean)=glColorMask;
    if(intervention_active()) state_record((uintptr_t)__builtin_return_address(0),28,
        ((uint64_t)r<<3)|((uint64_t)g<<2)|((uint64_t)b<<1)|a);
    if(real_fn) real_fn(r,g,b,a);
}
static void gl_glStencilFunc(GLenum func,GLint ref,GLuint mask) {
    static void (*real_fn)(GLenum,GLint,GLuint)=glStencilFunc;
    if(intervention_active()) state_record((uintptr_t)__builtin_return_address(0),29,
        state_pack3(func,(uint32_t)ref,mask));
    if(real_fn) real_fn(func,ref,mask);
}
static void gl_glStencilOp(GLenum fail,GLenum depth_fail,GLenum pass) {
    static void (*real_fn)(GLenum,GLenum,GLenum)=glStencilOp;
    if(intervention_active()) state_record((uintptr_t)__builtin_return_address(0),30,
        state_pack3(fail,depth_fail,pass));
    if(real_fn) real_fn(fail,depth_fail,pass);
}
static void gl_glScissor(GLint x,GLint y,GLsizei w,GLsizei h) {
    static void (*real_fn)(GLint,GLint,GLsizei,GLsizei)=glScissor;
    if(intervention_active()) state_record((uintptr_t)__builtin_return_address(0),31,
        (((uint64_t)(uint32_t)x<<32)|(uint32_t)y) ^
        (((uint64_t)(uint32_t)w<<32)|(uint32_t)h));
    if(real_fn) real_fn(x,y,w,h);
}
static void gl_glViewport(GLint x,GLint y,GLsizei w,GLsizei h) {
    static void (*real_fn)(GLint,GLint,GLsizei,GLsizei)=glViewport;
    if(intervention_active()) state_record((uintptr_t)__builtin_return_address(0),32,
        (((uint64_t)(uint32_t)x<<32)|(uint32_t)y) ^
        (((uint64_t)(uint32_t)w<<32)|(uint32_t)h));
    if(real_fn) real_fn(x,y,w,h);
}
static void gl_glTexParameteri(GLenum target,GLenum pname,GLint value) {
    static void (*real_fn)(GLenum,GLenum,GLint)=glTexParameteri;
    if(intervention_active()) state_record((uintptr_t)__builtin_return_address(0),33,
        ((uint64_t)target<<32)^((uint64_t)pname<<16)^(uint32_t)value);
    if(real_fn) real_fn(target,pname,value);
}
static void gl_glTexParameterf(GLenum target,GLenum pname,GLfloat value) {
    static void (*real_fn)(GLenum,GLenum,GLfloat)=glTexParameterf;
    uint32_t bits=0;memcpy(&bits,&value,sizeof(bits));
    if(intervention_active()) state_record((uintptr_t)__builtin_return_address(0),34,
        ((uint64_t)target<<32)^((uint64_t)pname<<16)^bits);
    if(real_fn) real_fn(target,pname,value);
}
static void gl_glPixelStorei(GLenum pname,GLint value) {
    static void (*real_fn)(GLenum,GLint)=glPixelStorei;
    if(intervention_active()) state_record((uintptr_t)__builtin_return_address(0),35,
        ((uint64_t)pname<<32)|(uint32_t)value);
    if(real_fn) real_fn(pname,value);
}
static void gl_glPolygonMode(GLenum face,GLenum mode) {
    static void (*real_fn)(GLenum,GLenum)=glPolygonMode;
    if(intervention_active()) state_record((uintptr_t)__builtin_return_address(0),38,
        ((uint64_t)face<<32)|mode);
    if(real_fn) real_fn(face,mode);
}
static void gl_glBindFramebuffer(GLenum target,GLuint framebuffer) {
    static void (*real_fn)(GLenum,GLuint)=glBindFramebuffer;
    if(intervention_active()) state_record((uintptr_t)__builtin_return_address(0),39,
        ((uint64_t)target<<32)|framebuffer);
    if(real_fn) real_fn(target,framebuffer);
}
#undef DEFINE_STATE_ONE
static void gl_use_program(GLuint program) {
    static void (*real_fn)(GLuint)=glUseProgram;
    if(intervention_active()) {
        if(frame_control.mode!=REFERENCE && measurement_active()) {
            event(HOOK_GL_STATE,0,0);
            current_frame.state_calls++;
            if(active_program!=program) current_frame.program_switches++;
        }
        active_program=program;
        if(frame_control.mode!=REFERENCE && measurement_active() && detail_active) {
            uintptr_t caller=(uintptr_t)__builtin_return_address(0);
            detail_line("S",current_frame.render_id,caller>=image_base?caller-image_base:0,1,program);
        }
    }
    if(real_fn) real_fn(program);
}
typedef void (*UseProgramArbFn)(GLhandleARB);
void eu4_frame_model_forward_arb(GLhandleARB program,UseProgramArbFn forward) {
    if(forward) forward(program);
}
static void gl_use_program_arb(GLhandleARB program) {
    static UseProgramArbFn real_fn;
    if(!real_fn) real_fn=glUseProgramObjectARB;
    if(intervention_active()) {
        uintptr_t handle=(uintptr_t)program;
        if(frame_control.mode!=REFERENCE && measurement_active()) {
            event(HOOK_GL_STATE,0,0); current_frame.state_calls++;
            if(active_program!=handle) current_frame.program_switches++;
        }
        active_program=handle;
        if(frame_control.mode!=REFERENCE && measurement_active() && detail_active) {
            uintptr_t caller=(uintptr_t)__builtin_return_address(0);
            detail_line("S",current_frame.render_id,caller>=image_base?caller-image_base:0,
                        6,handle);
        }
    }
    eu4_frame_model_forward_arb(program,real_fn);
}
static void gl_bind_buffer(GLenum target,GLuint buffer) {
    static void (*real_fn)(GLenum,GLuint)=glBindBuffer;
    if(intervention_active()) {
        if(frame_control.mode!=REFERENCE && measurement_active()) {event(HOOK_GL_STATE,0,0);current_frame.state_calls++;current_frame.buffer_binds++;}
        if(target==GL_ARRAY_BUFFER) bound_array_buffer=buffer;
        if(target==GL_ELEMENT_ARRAY_BUFFER) {
            bound_element_buffer=buffer;
            if(vertex_array_state) vertex_array_state->element_buffer=buffer;
        }
        if(frame_control.mode!=REFERENCE && measurement_active() && detail_active) {
            uintptr_t caller=(uintptr_t)__builtin_return_address(0);
            detail_line("S",current_frame.render_id,caller>=image_base?caller-image_base:0,2,
                        ((uint64_t)target<<32)|buffer);
        }
    }
    if(real_fn) real_fn(target,buffer);
}
static void gl_bind_vertex_array(GLuint vao) {
    static void (*real_fn)(GLuint)=glBindVertexArray;
    if(real_fn) real_fn(vao);
    if(!intervention_active()) return;
    CGLContextObj ctx=CGLGetCurrentContext();
    vertex_array_state=vertex_state_for(ctx,vao,true);
    if(!vertex_array_state) {current_frame.flags|=2048;return;}
    bound_element_buffer=vertex_array_state->element_buffer;
    if(frame_control.mode!=REFERENCE && measurement_active()) {
        event(HOOK_GL_STATE,0,0);current_frame.state_calls++;
        if(detail_active) {
            uintptr_t caller=(uintptr_t)__builtin_return_address(0);
            detail_line("S",current_frame.render_id,caller>=image_base?caller-image_base:0,7,vao);
        }
    }
}
static void gl_vertex_attrib_pointer(GLuint index,GLint size,GLenum type,
        GLboolean normalized,GLsizei stride,const void *pointer) {
    static void (*real_fn)(GLuint,GLint,GLenum,GLboolean,GLsizei,const void *)=glVertexAttribPointer;
    if(real_fn) real_fn(index,size,type,normalized,stride,pointer);
    if(!intervention_active()) return;
    if(!vertex_array_state || !state_seeded) seed_gl_state();
    if(vertex_array_state && index<32) {
        VertexAttribute *a=&vertex_array_state->attributes[index];
        *a=(VertexAttribute){.buffer=bound_array_buffer,.size=size,.type=type,
            .normalized=normalized,.stride=stride,.pointer=(uintptr_t)pointer,
            .divisor=a->divisor,.enabled=a->enabled};
    }
    if(frame_control.mode!=REFERENCE && measurement_active()) {event(HOOK_GL_STATE,0,0);current_frame.state_calls++;}
}
static void gl_enable_vertex_attrib(GLuint index) {
    static void (*real_fn)(GLuint)=glEnableVertexAttribArray;
    if(real_fn) real_fn(index);
    if(!intervention_active()) return;
    if(!vertex_array_state || !state_seeded) seed_gl_state();
    if(vertex_array_state && index<32) vertex_array_state->attributes[index].enabled=GL_TRUE;
    if(frame_control.mode!=REFERENCE && measurement_active()) {event(HOOK_GL_STATE,0,0);current_frame.state_calls++;}
}
static void gl_disable_vertex_attrib(GLuint index) {
    static void (*real_fn)(GLuint)=glDisableVertexAttribArray;
    if(real_fn) real_fn(index);
    if(!intervention_active()) return;
    if(!vertex_array_state || !state_seeded) seed_gl_state();
    if(vertex_array_state && index<32) vertex_array_state->attributes[index].enabled=GL_FALSE;
    if(frame_control.mode!=REFERENCE && measurement_active()) {event(HOOK_GL_STATE,0,0);current_frame.state_calls++;}
}
static void gl_vertex_attrib_divisor(GLuint index,GLuint divisor) {
    static void (*real_fn)(GLuint,GLuint)=glVertexAttribDivisor;
    if(real_fn) real_fn(index,divisor);
    if(!intervention_active()) return;
    if(!vertex_array_state || !state_seeded) seed_gl_state();
    if(vertex_array_state && index<32) vertex_array_state->attributes[index].divisor=divisor;
    if(frame_control.mode!=REFERENCE && measurement_active()) {event(HOOK_GL_STATE,0,0);current_frame.state_calls++;}
}
static void gl_tex_image_2d(GLenum target,GLint level,GLint internal,GLsizei width,
        GLsizei height,GLint border,GLenum format,GLenum type,const void *pixels) {
    static void (*real_fn)(GLenum,GLint,GLint,GLsizei,GLsizei,GLint,GLenum,GLenum,const void *)=glTexImage2D;
    if(!intervention_active()) {
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
    static void (*real_fn)(GLenum,GLint,GLint,GLint,GLsizei,GLsizei,GLenum,GLenum,const void *)=glTexSubImage2D;
    if(!intervention_active()) {
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
    static void (*real_fn)(GLenum,GLuint)=glBindTexture;
    if(frame_control.mode!=REFERENCE && measurement_active()) {
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
    static void (*real_fn)(GLenum)=glEnable;
    if(frame_control.mode!=REFERENCE && measurement_active()) {
        event(HOOK_GL_STATE,0,0);
        current_frame.state_calls++;
        if(detail_active) {
            uintptr_t caller=(uintptr_t)__builtin_return_address(0);
            detail_line("S",current_frame.render_id,caller>=image_base?caller-image_base:0,4,
                        ((uint64_t)cap<<1)|1);
        }
    }
    if(raster_active && cap==GL_RASTERIZER_DISCARD) {
        bool found=eu4_raster_request(raster_contexts,raster_context_count,
                                     (uintptr_t)CGLGetCurrentContext(),true);
        current_frame.raster_challenges++;
        if(!found) current_frame.flags|=32;
        glEnable(GL_RASTERIZER_DISCARD);
        if(!glIsEnabled(GL_RASTERIZER_DISCARD)) current_frame.flags|=32;
        return;
    }
    if(real_fn) real_fn(cap);
}
static void gl_disable(GLenum cap) {
    static void (*real_fn)(GLenum)=glDisable;
    if(frame_control.mode!=REFERENCE && measurement_active()) {
        event(HOOK_GL_STATE,0,0);
        current_frame.state_calls++;
        if(detail_active) {
            uintptr_t caller=(uintptr_t)__builtin_return_address(0);
            detail_line("S",current_frame.render_id,caller>=image_base?caller-image_base:0,4,
                        (uint64_t)cap<<1);
        }
    }
    if(raster_active && cap==GL_RASTERIZER_DISCARD) {
        bool found=eu4_raster_request(raster_contexts,raster_context_count,
                                     (uintptr_t)CGLGetCurrentContext(),false);
        current_frame.raster_challenges++;
        if(!found) current_frame.flags|=32;
        glEnable(GL_RASTERIZER_DISCARD);
        if(!glIsEnabled(GL_RASTERIZER_DISCARD)) current_frame.flags|=32;
        return;
    }
    if(real_fn) real_fn(cap);
}
static void gl_active_texture(GLenum unit) {
    static void (*real_fn)(GLenum)=glActiveTexture;
    if(frame_control.mode!=REFERENCE && measurement_active()) {
        event(HOOK_GL_STATE,0,0);
        current_frame.state_calls++;
        if(detail_active) {
            uintptr_t caller=(uintptr_t)__builtin_return_address(0);
            detail_line("S",current_frame.render_id,caller>=image_base?caller-image_base:0,5,unit);
        }
    }
    if(real_fn) real_fn(unit);
}
// Forward through this interposer image's direct framework imports. Looking up
// re-exported CGL symbols with RTLD_NEXT can return our replacement and recurse.
static CGLError tracked_set_context(CGLContextObj ctx) {
    static CGLError (*real_fn)(CGLContextObj)=CGLSetCurrentContext;
    bool segment=gpu_manager.rendering;
    if(segment) { pthread_mutex_lock(&gpu_lock); eu4_gpu_close(&gpu_manager,&gpu_api); pthread_mutex_unlock(&gpu_lock); }
    CGLError result=real_fn?real_fn(ctx):kCGLBadContext;
    if(segment) { pthread_mutex_lock(&gpu_lock); eu4_gpu_open(&gpu_manager,&gpu_api); pthread_mutex_unlock(&gpu_lock); }
    if(result==kCGLNoError && ctx!=state_context) {
        state_seeded=false;vertex_array_state=NULL;
    }
    if(result==kCGLNoError && raster_active && ctx) {
        bool found=false;
        for(unsigned i=0;i<raster_context_count;i++)
            if(raster_contexts[i].context==(uintptr_t)ctx) { found=true; break; }
        if(!found) {
            if(raster_context_count>=64) current_frame.flags|=32;
            else {
                GLboolean (*is_enabled)(GLenum)=glIsEnabled;
                void (*enable)(GLenum)=glEnable;
                if(!is_enabled || !enable) current_frame.flags|=8;
                else {
                    GLboolean previous=is_enabled(GL_RASTERIZER_DISCARD);
                    raster_contexts[raster_context_count++]=(RasterContext){(uintptr_t)ctx,previous};
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
static CGLError tracked_destroy_context(CGLContextObj ctx) {
    static CGLError (*real_fn)(CGLContextObj)=CGLDestroyContext;
    pthread_mutex_lock(&gpu_lock); gpu_manager.registry=&gpu_registry;
    bool closing=gpu_manager.rendering && gpu_manager.owner && gpu_manager.owner->context==(uintptr_t)ctx;
    if(closing) eu4_gpu_close(&gpu_manager,&gpu_api);
    CGLError result=real_fn?real_fn(ctx):kCGLBadContext;
    if(result==kCGLNoError) {
        eu4_gpu_destroy(&gpu_manager,&gpu_api,(uintptr_t)ctx);
        for(unsigned i=0;i<vertex_array_count;i++)
            if(vertex_arrays[i].context==ctx) memset(&vertex_arrays[i],0,sizeof(vertex_arrays[i]));
        if(state_context==ctx) {state_context=NULL;state_seeded=false;}
    }
    if(closing && result!=kCGLNoError) eu4_gpu_open(&gpu_manager,&gpu_api);
    pthread_mutex_unlock(&gpu_lock);
    if(result==kCGLNoError && raster_active) {
        for(unsigned i=0;i<raster_context_count;i++) if(raster_contexts[i].context==(uintptr_t)ctx) {
            current_frame.flags|=32; raster_contexts[i]=raster_contexts[--raster_context_count]; break;
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
    if(!strcmp(name,"glUniform1i") || !strcmp(name,"glUniform1iARB")) return gl_uniform1i;
    if(!strcmp(name,"glUniform1fv") || !strcmp(name,"glUniform1fvARB")) return gl_glUniform1fv;
    if(!strcmp(name,"glUniform2fv") || !strcmp(name,"glUniform2fvARB")) return gl_glUniform2fv;
    if(!strcmp(name,"glUniform3fv") || !strcmp(name,"glUniform3fvARB")) return gl_glUniform3fv;
    if(!strcmp(name,"glUniform1iv") || !strcmp(name,"glUniform1ivARB")) return gl_glUniform1iv;
    if(!strcmp(name,"glUniform2iv") || !strcmp(name,"glUniform2ivARB")) return gl_glUniform2iv;
    if(!strcmp(name,"glUniform3iv") || !strcmp(name,"glUniform3ivARB")) return gl_glUniform3iv;
    if(!strcmp(name,"glUniform4iv") || !strcmp(name,"glUniform4ivARB")) return gl_glUniform4iv;
    if(!strcmp(name,"glUniformMatrix2fv") || !strcmp(name,"glUniformMatrix2fvARB")) return gl_glUniformMatrix2fv;
    if(!strcmp(name,"glUniformMatrix3fv") || !strcmp(name,"glUniformMatrix3fvARB")) return gl_glUniformMatrix3fv;
    if(!strcmp(name,"glUniformMatrix4fv") || !strcmp(name,"glUniformMatrix4fvARB")) return gl_glUniformMatrix4fv;
    if(!strcmp(name,"glUniform1f") || !strcmp(name,"glUniform1fARB")) return gl_glUniform1f;
    if(!strcmp(name,"glUniform2f") || !strcmp(name,"glUniform2fARB")) return gl_glUniform2f;
    if(!strcmp(name,"glUniform3f") || !strcmp(name,"glUniform3fARB")) return gl_glUniform3f;
    if(!strcmp(name,"glUniform4f") || !strcmp(name,"glUniform4fARB")) return gl_glUniform4f;
    if(!strcmp(name,"glUniform2i") || !strcmp(name,"glUniform2iARB")) return gl_glUniform2i;
    if(!strcmp(name,"glUniform3i") || !strcmp(name,"glUniform3iARB")) return gl_glUniform3i;
    if(!strcmp(name,"glUniform4i") || !strcmp(name,"glUniform4iARB")) return gl_glUniform4i;
    if(!strcmp(name,"glUseProgram")) return gl_use_program;
    if(!strcmp(name,"glUseProgramObjectARB")) return gl_use_program_arb;
    if(!strcmp(name,"glDepthFunc")) return gl_glDepthFunc;
    if(!strcmp(name,"glDepthMask")) return gl_glDepthMask;
    if(!strcmp(name,"glCullFace")) return gl_glCullFace;
    if(!strcmp(name,"glFrontFace")) return gl_glFrontFace;
    if(!strcmp(name,"glStencilMask")) return gl_glStencilMask;
    if(!strcmp(name,"glClear")) return gl_glClear;
    if(!strcmp(name,"glBlendFunc")) return gl_glBlendFunc;
    if(!strcmp(name,"glBlendFuncSeparate")) return gl_glBlendFuncSeparate;
    if(!strcmp(name,"glColorMask")) return gl_glColorMask;
    if(!strcmp(name,"glStencilFunc")) return gl_glStencilFunc;
    if(!strcmp(name,"glStencilOp")) return gl_glStencilOp;
    if(!strcmp(name,"glScissor")) return gl_glScissor;
    if(!strcmp(name,"glViewport")) return gl_glViewport;
    if(!strcmp(name,"glTexParameteri")) return gl_glTexParameteri;
    if(!strcmp(name,"glTexParameterf")) return gl_glTexParameterf;
    if(!strcmp(name,"glPixelStorei")) return gl_glPixelStorei;
    if(!strcmp(name,"glPolygonMode")) return gl_glPolygonMode;
    if(!strcmp(name,"glBlendEquation")) return gl_glBlendEquation;
    if(!strcmp(name,"glBlendEquationSeparate")) return gl_glBlendEquationSeparate;
    if(!strcmp(name,"glBindFramebuffer") || !strcmp(name,"glBindFramebufferEXT")) return gl_glBindFramebuffer;
    if(!strcmp(name,"glBindBuffer") || !strcmp(name,"glBindBufferARB")) return gl_bind_buffer;
    if(!strcmp(name,"glBindVertexArray") || !strcmp(name,"glBindVertexArrayAPPLE")) return gl_bind_vertex_array;
    if(!strcmp(name,"glVertexAttribPointer") || !strcmp(name,"glVertexAttribPointerARB")) return gl_vertex_attrib_pointer;
    if(!strcmp(name,"glEnableVertexAttribArray") || !strcmp(name,"glEnableVertexAttribArrayARB")) return gl_enable_vertex_attrib;
    if(!strcmp(name,"glDisableVertexAttribArray") || !strcmp(name,"glDisableVertexAttribArrayARB")) return gl_disable_vertex_attrib;
    if(!strcmp(name,"glVertexAttribDivisor") || !strcmp(name,"glVertexAttribDivisorARB")) return gl_vertex_attrib_divisor;
    if(!strcmp(name,"glTexImage2D") || !strcmp(name,"glTexImage2DEXT")) return gl_tex_image_2d;
    if(!strcmp(name,"glTexSubImage2D") || !strcmp(name,"glTexSubImage2DEXT")) return gl_tex_sub_image_2d;
    if(!strcmp(name,"glBindTexture")) return gl_bind_texture;
    if(!strcmp(name,"glEnable")) return gl_enable;
    if(!strcmp(name,"glDisable")) return gl_disable;
    if(!strcmp(name,"glActiveTexture") || !strcmp(name,"glActiveTextureARB")) return gl_active_texture;
    return original;
}

#ifdef EU4_FRAME_MODEL_TEST
static void (*test_workload)(void);
static char test_self,test_context,test_camera,test_record;
static void test_expect(bool condition) { if(!condition) abort(); }
static void test_add(const void *self,int layer,const void *camera) {
    test_expect(self==&test_self && layer==-17 && camera==&test_camera);
}
static void test_append(void *self,const void *record) {
    test_expect(self==&test_self && record==&test_record);
}
static void test_present(void *self) { test_expect(self==&test_self); }
static void test_map(void *self,void *ctx,const void *camera,float alpha,bool flag) {
    test_expect(self==&test_self && ctx==&test_context && camera==&test_camera && alpha==.25f && !flag);
    hook_add(self,-17,&test_camera); hook_append(self,&test_record); test_workload();
}
static void test_render(void *self) {
    test_expect(self==&test_self); hook_map(self,&test_context,&test_camera,.25f,false); hook_present(self);
}
static void test_idle(void *self,bool force) {
    test_expect(self==&test_self && !force); hook_render(self);
}
static void test_update(void *self,bool force) {
    test_expect(self==&test_self && force); hook_idle(self,false);
}
__attribute__((visibility("default"))) void eu4_frame_model_test_arm(unsigned frames) {
    atomic_store_explicit(&control->command_seq,3,memory_order_release);
    control->flags|=MEASURE_ENABLED; control->detail_frames=frames;
    control->generation=2; control->measurement_epoch=1;
    atomic_store_explicit(&control->command_seq,4,memory_order_release);
}
__attribute__((visibility("default"))) void eu4_frame_model_test_frame(void (*workload)(void)) {
    real_update=test_update; real_idle=test_idle; real_render=test_render; real_map=test_map;
    real_add=test_add; real_append=test_append; real_present=test_present; test_workload=workload;
    hook_update(&test_self,true);
}
#endif

static void initialize(void) __attribute__((constructor));
static void initialize(void) {
    const char *path=getenv("EU4_FRAME_MODEL_CONTROL");
    if(path) { int fd=open(path,O_RDWR); if(fd>=0) { control=mmap(NULL,4096,PROT_READ|PROT_WRITE,MAP_SHARED,fd,0); close(fd); } }
    const char *log=getenv("EU4_FRAME_MODEL_LOG");
    if(log) log_fd=open(log,O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC,0600);
    if(!control || control==MAP_FAILED || log_fd<0) return;
    (void)write(log_fd,"H,3\n",4);
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
    if(!image_base) {
#ifdef EU4_FRAME_MODEL_TEST
        atomic_store(&writer_running,1);
        writer_started=pthread_create(&writer_thread,NULL,writer,NULL)==0;
        if(!writer_started) atomic_fetch_add(&control->hook_failures,1);
#else
        atomic_fetch_add(&control->hook_failures,1);
#endif
        return;
    }
    install_one(0,hook_update,(void **)&real_update);
    install_one(1,hook_idle,(void **)&real_idle);
    install_one(2,hook_render,(void **)&real_render);
    install_one(3,hook_map,(void **)&real_map);
    install_one(4,hook_present,(void **)&real_present);
    install_one(5,hook_add,(void **)&real_add);
    install_one(6,hook_append,(void **)&real_append);
    if(atomic_load(&control->hook_failures)) { restore_detours(); return; }
    count_line("Z",atomic_load(&control->hook_failures),EU4_HOOK_COUNT,atomic_load(&control->dropped_records));
    atomic_store(&writer_running,1);
    writer_started=pthread_create(&writer_thread,NULL,writer,NULL)==0;
    if(!writer_started) { atomic_fetch_add(&control->hook_failures,1); restore_detours(); }
    // Imported calls are covered by __interpose; dlsym results are covered by
    // the wrapper below. OpenGL exports are not modified in the game image.
}
static void shutdown_probe(void) __attribute__((destructor));
static void shutdown_probe(void) {
    flush_counters();
    if(!control || control==MAP_FAILED) return;
    atomic_store(&writer_running,0); if(writer_started) (void)pthread_join(writer_thread,NULL);
    restore_detours();
    for(unsigned i=0;i<HOOK_COUNT;i++) hook_line(i);
    count_line("X",atomic_load(&updates),atomic_load(&renders),atomic_load(&presents));
    count_line("Z",atomic_load(&control->hook_failures),EU4_HOOK_COUNT,atomic_load(&control->dropped_records));
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
    {(const void *)gl_uniform1i,(const void *)glUniform1i},
    {(const void *)gl_glUniform1fv,(const void *)glUniform1fv},
    {(const void *)gl_glUniform2fv,(const void *)glUniform2fv},
    {(const void *)gl_glUniform3fv,(const void *)glUniform3fv},
    {(const void *)gl_glUniform1iv,(const void *)glUniform1iv},
    {(const void *)gl_glUniform2iv,(const void *)glUniform2iv},
    {(const void *)gl_glUniform3iv,(const void *)glUniform3iv},
    {(const void *)gl_glUniform4iv,(const void *)glUniform4iv},
    {(const void *)gl_glUniformMatrix2fv,(const void *)glUniformMatrix2fv},
    {(const void *)gl_glUniformMatrix3fv,(const void *)glUniformMatrix3fv},
    {(const void *)gl_glUniformMatrix4fv,(const void *)glUniformMatrix4fv},
    {(const void *)gl_glUniform1f,(const void *)glUniform1f},
    {(const void *)gl_glUniform2f,(const void *)glUniform2f},
    {(const void *)gl_glUniform3f,(const void *)glUniform3f},
    {(const void *)gl_glUniform4f,(const void *)glUniform4f},
    {(const void *)gl_glUniform2i,(const void *)glUniform2i},
    {(const void *)gl_glUniform3i,(const void *)glUniform3i},
    {(const void *)gl_glUniform4i,(const void *)glUniform4i},
    {(const void *)gl_glDepthFunc,(const void *)glDepthFunc},
    {(const void *)gl_glDepthMask,(const void *)glDepthMask},
    {(const void *)gl_glCullFace,(const void *)glCullFace},
    {(const void *)gl_glFrontFace,(const void *)glFrontFace},
    {(const void *)gl_glStencilMask,(const void *)glStencilMask},
    {(const void *)gl_glClear,(const void *)glClear},
    {(const void *)gl_glBlendFunc,(const void *)glBlendFunc},
    {(const void *)gl_glBlendFuncSeparate,(const void *)glBlendFuncSeparate},
    {(const void *)gl_glColorMask,(const void *)glColorMask},
    {(const void *)gl_glStencilFunc,(const void *)glStencilFunc},
    {(const void *)gl_glStencilOp,(const void *)glStencilOp},
    {(const void *)gl_glScissor,(const void *)glScissor},
    {(const void *)gl_glViewport,(const void *)glViewport},
    {(const void *)gl_glTexParameteri,(const void *)glTexParameteri},
    {(const void *)gl_glTexParameterf,(const void *)glTexParameterf},
    {(const void *)gl_glPixelStorei,(const void *)glPixelStorei},
    {(const void *)gl_glPolygonMode,(const void *)glPolygonMode},
    {(const void *)gl_glBlendEquation,(const void *)glBlendEquation},
    {(const void *)gl_glBlendEquationSeparate,(const void *)glBlendEquationSeparate},
    {(const void *)gl_glBindFramebuffer,(const void *)glBindFramebuffer},
    {(const void *)gl_use_program,(const void *)glUseProgram},
    {(const void *)gl_use_program_arb,(const void *)glUseProgramObjectARB},
    {(const void *)gl_bind_buffer,(const void *)glBindBuffer},
    {(const void *)gl_bind_vertex_array,(const void *)glBindVertexArray},
    {(const void *)gl_vertex_attrib_pointer,(const void *)glVertexAttribPointer},
    {(const void *)gl_enable_vertex_attrib,(const void *)glEnableVertexAttribArray},
    {(const void *)gl_disable_vertex_attrib,(const void *)glDisableVertexAttribArray},
    {(const void *)gl_vertex_attrib_divisor,(const void *)glVertexAttribDivisor},
    {(const void *)gl_tex_image_2d,(const void *)glTexImage2D},
    {(const void *)gl_tex_sub_image_2d,(const void *)glTexSubImage2D},
    {(const void *)gl_bind_texture,(const void *)glBindTexture},
    {(const void *)gl_enable,(const void *)glEnable},
    {(const void *)gl_disable,(const void *)glDisable},
    {(const void *)gl_active_texture,(const void *)glActiveTexture},
    {(const void *)tracked_set_context,(const void *)CGLSetCurrentContext},
    {(const void *)tracked_destroy_context,(const void *)CGLDestroyContext}
};
