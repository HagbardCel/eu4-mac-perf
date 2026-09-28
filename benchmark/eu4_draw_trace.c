// Passive, bounded GL draw trace for the pinned GOG EU IV x86-64 executable.
#define GL_SILENCE_DEPRECATION 1
#include <OpenGL/OpenGL.h>
#include <OpenGL/gl.h>
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-W#warnings"
#include <OpenGL/gl3.h>
#pragma clang diagnostic pop
#include <dlfcn.h>
#include <fcntl.h>
#include <mach-o/dyld.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
#include "eu4_draw_sites.h"

enum { TRACE_CAPACITY=1000000, MAX_CONTEXTS=8, STATE_SLOTS=4096,
       WINDOW_FRAMES=32, BAD_STATE=1, BAD_THREAD=2, BAD_STACK=4,
       BAD_CAPACITY=8, BAD_CONTEXT=16 };

#pragma pack(push,1)
typedef struct {
    uint32_t frame, ordinal;
    uint64_t caller_offset, immediate_offset;
    uint64_t context, texture_sig, uniform_sig, render_sig, vertex_sig;
    uint64_t program;
    uint32_t array_buffer, element_buffer, framebuffer;
    uint32_t mode, count;
    uint64_t index_offset;
    int32_t base_vertex;
    uint16_t window, api;
    uint32_t flags;
} DrawRecord;
#pragma pack(pop)

typedef struct {
    char magic[8];
    uint32_t version, record_size, capacity;
    _Atomic uint32_t used, overflow, gl_draws, known_callers;
    _Atomic uint32_t completed_windows, bad_flags;
    uint32_t padding;
    uint64_t image_base;
    _Atomic uint32_t window_draws[3];
    uint32_t padding2;
    uint64_t window_start_ns[3],window_end_ns[3];
    uint8_t reserved[4096-8-4*3-4*6-4-8-4*3-4-8*6];
} TraceHeader;
_Static_assert(sizeof(TraceHeader)==4096,"Trace header must occupy one page");
_Static_assert(sizeof(DrawRecord)==112,"Trace record format changed");

typedef struct {uint64_t key,value;uint8_t occupied;} Slot;
typedef struct {
    CGLContextObj context;
    uintptr_t program;
    GLuint active_unit,array_buffer,element_buffer,framebuffer;
    uint64_t texture_sig,uniform_sig,render_sig,vertex_sig;
    uint64_t attrib_pointer[32],texenv[16];
    GLuint bound_texture_2d[16];
    uint32_t attrib_enabled;
    Slot texture[STATE_SLOTS],uniform[STATE_SLOTS],render[256],vertex[256];
    bool overflow;
} GLState;

static TraceHeader *header;
static DrawRecord *records;
static volatile uint8_t *control;
static int log_fd=-1;
static uintptr_t image_base;
static uintptr_t image_limit;
static GLState contexts[MAX_CONTEXTS];
static _Thread_local GLState *thread_context;
static _Thread_local uint64_t cached_tid;
static _Atomic uint64_t render_tid;
static uint64_t bucket_start,swaps,paused_swaps;
static uint32_t frame_number,ordinal,window_frames,last_window;
static unsigned tracked_mode;
static void *(*app_instance)(void);
static bool (*actually_paused)(void *);
static void *idler_vtable;
static pthread_once_t pause_once=PTHREAD_ONCE_INIT;

static uint64_t clock_ns(clockid_t kind) {
    struct timespec t;
    return clock_gettime(kind,&t) ? 0 : (uint64_t)t.tv_sec*1000000000ull+t.tv_nsec;
}
static uint64_t tid(void) {
    if (!cached_tid) (void)pthread_threadid_np(NULL,&cached_tid);
    return cached_tid;
}
static uint64_t mix(uint64_t x) {
    x^=x>>30;x*=0xbf58476d1ce4e5b9ull;x^=x>>27;
    x*=0x94d049bb133111ebull;return x^(x>>31);
}
static uint64_t hash_bytes(const void *data,size_t length) {
    const unsigned char *p=data;
    uint64_t h=1469598103934665603ull;
    for (size_t i=0;i<length;i++) h=(h^p[i])*1099511628211ull;
    return h;
}
static void setup(void) {
    void *marker=dlsym(RTLD_MAIN_ONLY,"_ZN12CApplication14AccessInstanceEv");
#ifdef EU4_DRAW_TEST_CALLSITE
    if (!marker) marker=dlsym(RTLD_MAIN_ONLY,"main");
#endif
    Dl_info info;
    if (marker && dladdr(marker,&info)) image_base=(uintptr_t)info.dli_fbase;
    // All pinned draw helpers and their direct callers lie within this span.
    image_limit=image_base+0x2000000u;
    const char *path=getenv("EU4_DRAW_LOG");
    if (path && path[0]=='/') log_fd=open(path,O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC,0600);
    path=getenv("EU4_DRAW_CONTROL");
    if (path && path[0]=='/') {
        int fd=open(path,O_RDONLY);
        if (fd>=0) {
            void *m=mmap(NULL,4096,PROT_READ,MAP_SHARED,fd,0);
            if (m!=MAP_FAILED) control=m;
            close(fd);
        }
    }
    path=getenv("EU4_DRAW_BUFFER");
    if (path && path[0]=='/') {
        int fd=open(path,O_RDWR);
        size_t length=4096+(size_t)TRACE_CAPACITY*sizeof(DrawRecord);
        struct stat statbuf;
        if (fd>=0 && !fstat(fd,&statbuf) && statbuf.st_size>=(off_t)length) {
            void *m=mmap(NULL,length,PROT_READ|PROT_WRITE,MAP_SHARED,fd,0);
            if (m!=MAP_FAILED) {
                header=m;records=(DrawRecord *)((char *)m+4096);
                memcpy(header->magic,"EU4DRAW1",8);
                header->version=1;header->record_size=sizeof(DrawRecord);
                header->capacity=TRACE_CAPACITY;
                header->image_base=image_base;
            }
        }
        if (fd>=0) close(fd);
    }
}
__attribute__((constructor)) static void initialize(void) {setup();}
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
static GLState *current_state(void) {
    uint64_t owner=atomic_load(&render_tid);
    if (!owner || owner!=tid()) return NULL;
    unsigned mode=control?control[0]:0;
    if (mode!=1 && mode!=2) {tracked_mode=0;return NULL;}
    if (mode==2 && tracked_mode==0) {
        memset(contexts,0,sizeof(contexts));
        thread_context=NULL;
    }
    tracked_mode=mode;
    if (thread_context) return thread_context;
    CGLContextObj context=CGLGetCurrentContext();
    if (!context) return NULL;
    for (unsigned i=0;i<MAX_CONTEXTS;i++) {
        if (contexts[i].context==context) return thread_context=&contexts[i];
        if (!contexts[i].context) {
            contexts[i].context=context;
            contexts[i].active_unit=GL_TEXTURE0;
            return thread_context=&contexts[i];
        }
    }
    if (header) atomic_fetch_or(&header->bad_flags,BAD_CONTEXT);
    return NULL;
}
static void change(Slot *table,size_t capacity,uint64_t key,uint64_t value,
                   uint64_t *fingerprint,GLState *s) {
    size_t pos=(size_t)(mix(key)&(capacity-1));
    for (size_t i=0;i<capacity;i++) {
        Slot *slot=&table[(pos+i)&(capacity-1)];
        if (!slot->occupied) {
            slot->occupied=1;slot->key=key;slot->value=value;
            *fingerprint^=mix(key^mix(value));return;
        }
        if (slot->key==key) {
            if (slot->value!=value) {
                *fingerprint^=mix(key^mix(slot->value))^mix(key^mix(value));
                slot->value=value;
            }
            return;
        }
    }
    s->overflow=true;
    if (header) atomic_fetch_or(&header->bad_flags,BAD_STATE);
}
static void update(GLState *s,unsigned family,uint64_t a,uint64_t b,uint64_t value) {
    if (!s) return;
    uint64_t key=mix(family)^mix(a)^mix(b);
    if (family==1) change(s->texture,STATE_SLOTS,key,value,&s->texture_sig,s);
    else if (family==2) change(s->uniform,STATE_SLOTS,key,value,&s->uniform_sig,s);
    else if (family==3) change(s->render,256,key,value,&s->render_sig,s);
    else change(s->vertex,256,key,value,&s->vertex_sig,s);
}
static bool known_site(uintptr_t offset) {
    size_t low=0,high=EU4_DRAW_SITE_COUNT;
    while (low<high) {
        size_t mid=(low+high)/2;
        if (eu4_draw_site_returns[mid]<offset) low=mid+1;
        else high=mid;
    }
    return low<EU4_DRAW_SITE_COUNT && eu4_draw_site_returns[low]==offset;
}
static __attribute__((always_inline)) inline uintptr_t parent_return(void) {
    void **frame=__builtin_frame_address(0);
    if (!frame) return 0;
    void **parent=(void **)*frame;
    if ((uintptr_t)parent<=(uintptr_t)frame ||
        (uintptr_t)parent-(uintptr_t)frame>16384 ||
        ((uintptr_t)parent&7)) return 0;
    return (uintptr_t)parent[1];
}
static void capture(unsigned api,GLenum mode,GLsizei count,const void *indices,
                    GLint base,uintptr_t immediate,uintptr_t parent) {
    if (!header) return;
    atomic_fetch_add(&header->gl_draws,1);
    if (!control || control[0]!=1 || !control[1]) return;
    if (last_window!=control[1]) {last_window=control[1];window_frames=0;}
    if (window_frames>=WINDOW_FRAMES) return;
    if (control[1]>3) {atomic_fetch_or(&header->bad_flags,BAD_STATE);return;}
    if (!header->window_start_ns[control[1]-1])
        header->window_start_ns[control[1]-1]=clock_ns(CLOCK_UPTIME_RAW);
    atomic_fetch_add(&header->window_draws[control[1]-1],1);
    GLState *s=current_state();
    if (!s) {atomic_fetch_or(&header->bad_flags,BAD_THREAD);return;}
    uint32_t index=atomic_fetch_add(&header->used,1);
    if (index>=TRACE_CAPACITY) {
        atomic_fetch_add(&header->overflow,1);
        atomic_fetch_or(&header->bad_flags,BAD_CAPACITY);return;
    }
    uintptr_t immediate_offset=immediate>=image_base && immediate<image_limit ?
                               immediate-image_base:0;
    bool direct_engine_site=known_site(immediate_offset);
    bool helper=!direct_engine_site && immediate_offset>=0x15ea789u &&
                immediate_offset<0x15ead87u;
#ifdef EU4_DRAW_TEST_CALLSITE
    helper=true;
#endif
    uintptr_t caller_offset=helper && parent>=image_base && parent<image_limit ?
                            parent-image_base:immediate_offset;
    bool known=direct_engine_site || (helper && known_site(caller_offset));
#ifdef EU4_DRAW_TEST_CALLSITE
    immediate_offset=immediate;
    caller_offset=parent;
    known=caller_offset!=immediate_offset && caller_offset!=0;
#endif
    if (known) atomic_fetch_add(&header->known_callers,1);
    DrawRecord *row=&records[index];
    *row=(DrawRecord){.frame=frame_number,.ordinal=ordinal++,
        .caller_offset=caller_offset,.immediate_offset=immediate_offset,
        .context=(uintptr_t)s->context,.texture_sig=s->texture_sig,
        .uniform_sig=s->uniform_sig,.render_sig=s->render_sig,
        .vertex_sig=s->vertex_sig,.program=s->program,
        .array_buffer=s->array_buffer,.element_buffer=s->element_buffer,
        .framebuffer=s->framebuffer,.mode=mode,.count=(uint32_t)count,
        .index_offset=(uintptr_t)indices,.base_vertex=base,
        .window=control[1],.api=api,
        .flags=(s->overflow?BAD_STATE:0)|(helper && !known?BAD_STACK:0)};
#ifdef EU4_DRAW_TEST_CALLSITE
    row->context=image_base;
#endif
}
static void state_uniform(GLint location,uint64_t value,unsigned type) {
    GLState *s=current_state();
    if (s) update(s,2,((uint64_t)s->program<<16)|(uint32_t)location,type,value);
}
static void tracked_uniform1i(GLint location,GLint value) {
    // The pinned SetAll path assigns each sampler location its fixed unit.
    // Program identity and texture bindings carry that draw-visible state.
    glUniform1i(location,value);
}
static void tracked_uniform4fv(GLint location,GLsizei count,const GLfloat *values) {
    if (control && (control[0]==1 || control[0]==2) &&
        count>0 && count<=1024 && values)
        state_uniform(location,hash_bytes(values,(size_t)count*4*sizeof(GLfloat)),
                      (unsigned)count<<8|4);
    else if (control && (control[0]==1 || control[0]==2) &&
             count>0 && header) atomic_fetch_or(&header->bad_flags,BAD_STATE);
    glUniform4fvARB(location,count,values);
}
static void tracked_program(GLhandleARB program) {
    GLState *s=current_state();
    if (s) s->program=(uintptr_t)program;
    glUseProgramObjectARB(program);
}
static void tracked_active(GLenum unit) {
    GLState *s=current_state();
    if (s) s->active_unit=unit;
    glActiveTextureARB(unit);
}
static void tracked_buffer(GLenum target,GLuint buffer) {
    GLState *s=current_state();
    if (s) {
        if (target==GL_ARRAY_BUFFER_ARB) s->array_buffer=buffer;
        else if (target==GL_ELEMENT_ARRAY_BUFFER_ARB) s->element_buffer=buffer;
    }
    glBindBufferARB(target,buffer);
}
static void tracked_attrib(GLuint index,GLint size,GLenum type,GLboolean normalized,
                           GLsizei stride,const void *pointer) {
    GLState *s=current_state();
    if (s && index<32) {
        uint64_t value=mix((uintptr_t)pointer^((uint64_t)s->array_buffer<<32)^
              ((uint64_t)(uint32_t)size<<24)^((uint64_t)type<<8)^
              ((uint64_t)normalized<<4)^(uint32_t)stride);
        if (s->attrib_pointer[index]!=value) {
            s->vertex_sig^=mix(index^mix(s->attrib_pointer[index]))^mix(index^mix(value));
            s->attrib_pointer[index]=value;
        }
    } else if (s) {s->overflow=true;if(header)atomic_fetch_or(&header->bad_flags,BAD_STATE);}
    glVertexAttribPointerARB(index,size,type,normalized,stride,pointer);
}
static void tracked_attrib_enable(GLuint index) {
    GLState *s=current_state();
    if (s && index<32 && !(s->attrib_enabled&(1u<<index))) {
        s->attrib_enabled|=1u<<index;s->vertex_sig^=mix(0x1000u+index);
    }
    glEnableVertexAttribArrayARB(index);
}
static void tracked_attrib_disable(GLuint index) {
    GLState *s=current_state();
    if (s && index<32 && (s->attrib_enabled&(1u<<index))) {
        s->attrib_enabled&=~(1u<<index);s->vertex_sig^=mix(0x1000u+index);
    }
    glDisableVertexAttribArrayARB(index);
}
static void tracked_attrib_divisor(GLuint index,GLuint divisor) {
    update(current_state(),4,index,3,divisor);
    glVertexAttribDivisorARB(index,divisor);
}
static void tracked_texture(GLenum target,GLuint texture) {
    GLState *s=current_state();
    if (s && target==GL_TEXTURE_2D && s->active_unit>=GL_TEXTURE0 &&
        s->active_unit<GL_TEXTURE0+16) {
        unsigned unit=s->active_unit-GL_TEXTURE0;
        if (s->bound_texture_2d[unit]!=texture) {
            s->texture_sig^=mix(unit^mix(s->bound_texture_2d[unit]))^
                            mix(unit^mix(texture));
            s->bound_texture_2d[unit]=texture;
        }
    } else if (s) update(s,1,s->active_unit,target,texture);
    glBindTexture(target,texture);
}
static void tracked_tex_parameter_i(GLenum target,GLenum pname,GLint param) {
    GLState *s=current_state();
    if (s) update(s,1,((uint64_t)s->active_unit<<16)|target,pname,(uint32_t)param);
    glTexParameteri(target,pname,param);
}
static void tracked_tex_parameter_f(GLenum target,GLenum pname,GLfloat param) {
    uint32_t bits;memcpy(&bits,&param,sizeof(bits));
    GLState *s=current_state();
    if (s) update(s,1,((uint64_t)s->active_unit<<16)|target,pname,bits);
    glTexParameterf(target,pname,param);
}
static void tracked_tex_env(GLenum target,GLenum pname,GLfloat param) {
    uint32_t bits;memcpy(&bits,&param,sizeof(bits));
    GLState *s=current_state();
    if (s && s->active_unit>=GL_TEXTURE0 && s->active_unit<GL_TEXTURE0+16 &&
        target==GL_TEXTURE_ENV && pname==GL_TEXTURE_ENV_MODE) {
        unsigned unit=s->active_unit-GL_TEXTURE0;
        if (s->texenv[unit]!=bits) {
            s->texture_sig^=mix(0x2000u+unit^mix(s->texenv[unit]))^
                            mix(0x2000u+unit^mix(bits));
            s->texenv[unit]=bits;
        }
    } else if (s) update(s,1,((uint64_t)s->active_unit<<16)|target,pname,bits);
    glTexEnvf(target,pname,param);
}
static void tracked_enable(GLenum cap) {update(current_state(),3,cap,1,1);glEnable(cap);}
static void tracked_disable(GLenum cap) {update(current_state(),3,cap,1,0);glDisable(cap);}
static void tracked_blend(GLenum src,GLenum dst) {
    update(current_state(),3,1,0,((uint64_t)src<<32)|dst);glBlendFunc(src,dst);
}
static void tracked_depth(GLenum func) {update(current_state(),3,2,0,func);glDepthFunc(func);}
static void tracked_depth_mask(GLboolean value) {
    update(current_state(),3,3,0,value);glDepthMask(value);
}
static void tracked_cull(GLenum mode) {update(current_state(),3,4,0,mode);glCullFace(mode);}
static void tracked_color(GLboolean r,GLboolean g,GLboolean b,GLboolean a) {
    update(current_state(),3,5,0,(r<<3)|(g<<2)|(b<<1)|a);glColorMask(r,g,b,a);
}
static void tracked_blend_separate(GLenum src_rgb,GLenum dst_rgb,
                                   GLenum src_alpha,GLenum dst_alpha) {
    update(current_state(),3,7,0,((uint64_t)src_rgb<<48)|((uint64_t)dst_rgb<<32)|
           ((uint64_t)src_alpha<<16)|dst_alpha);
    glBlendFuncSeparate(src_rgb,dst_rgb,src_alpha,dst_alpha);
}
static void tracked_stencil_func(GLenum func,GLint ref,GLuint mask) {
    update(current_state(),3,8,0,mix(((uint64_t)func<<48)|((uint64_t)(uint32_t)ref<<32)|mask));
    glStencilFunc(func,ref,mask);
}
static void tracked_stencil_mask(GLuint mask) {
    update(current_state(),3,9,0,mask);glStencilMask(mask);
}
static void tracked_stencil_op(GLenum fail,GLenum zfail,GLenum zpass) {
    update(current_state(),3,10,0,((uint64_t)fail<<32)|((uint64_t)zfail<<16)|zpass);
    glStencilOp(fail,zfail,zpass);
}
static void tracked_scissor(GLint x,GLint y,GLsizei width,GLsizei height) {
    update(current_state(),3,11,0,mix(((uint64_t)(uint32_t)x<<32)|(uint32_t)y)^
           mix(((uint64_t)(uint32_t)width<<32)|(uint32_t)height));
    glScissor(x,y,width,height);
}
static void tracked_viewport(GLint x,GLint y,GLsizei width,GLsizei height) {
    update(current_state(),3,12,0,mix(((uint64_t)(uint32_t)x<<32)|(uint32_t)y)^
           mix(((uint64_t)(uint32_t)width<<32)|(uint32_t)height));
    glViewport(x,y,width,height);
}
static void tracked_front(GLenum mode) {update(current_state(),3,13,0,mode);glFrontFace(mode);}
static void tracked_polygon(GLenum face,GLenum mode) {
    update(current_state(),3,14,face,mode);glPolygonMode(face,mode);
}
static void tracked_alpha(GLenum func,GLclampf ref) {
    uint32_t bits;memcpy(&bits,&ref,sizeof(bits));
    update(current_state(),3,15,func,bits);glAlphaFunc(func,ref);
}
static void tracked_fbo(GLenum target,GLuint fbo) {
    GLState *s=current_state();
    if (s) {
        s->framebuffer=fbo;update(s,3,6,target,fbo);
    }
    glBindFramebufferEXT(target,fbo);
}
static void tracked_draw_elements(GLenum mode,GLsizei count,GLenum type,const GLvoid *indices) {
    capture(1,mode,count,indices,0,(uintptr_t)__builtin_return_address(0),parent_return());
    glDrawElements(mode,count,type,indices);
}
static void tracked_draw_base(GLenum mode,GLsizei count,GLenum type,const GLvoid *indices,GLint base) {
    capture(2,mode,count,indices,base,(uintptr_t)__builtin_return_address(0),parent_return());
    glDrawElementsBaseVertex(mode,count,type,indices,base);
}
static void tracked_draw_arrays(GLenum mode,GLint first,GLsizei count) {
    capture(3,mode,count,(void *)(uintptr_t)(uint32_t)first,0,
            (uintptr_t)__builtin_return_address(0),parent_return());
    glDrawArrays(mode,first,count);
}
static void tracked_draw_elements_instanced(GLenum mode,GLsizei count,GLenum type,
                                            const GLvoid *indices,GLsizei instances) {
    capture(4,mode,count,indices,instances,(uintptr_t)__builtin_return_address(0),parent_return());
    glDrawElementsInstanced(mode,count,type,indices,instances);
}
static void tracked_draw_arrays_instanced(GLenum mode,GLint first,GLsizei count,GLsizei instances) {
    capture(5,mode,count,(void *)(uintptr_t)(uint32_t)first,instances,
            (uintptr_t)__builtin_return_address(0),parent_return());
    glDrawArraysInstanced(mode,first,count,instances);
}
static CGLError tracked_set_context(CGLContextObj context) {
    CGLError result=CGLSetCurrentContext(context);
    thread_context=NULL;
    return result;
}
static CGLError tracked_flush(CGLContextObj context) {
    CGLError result=CGLFlushDrawable(context);
    if (!header) return result;
    uint64_t owner=atomic_load(&render_tid),current=tid();
    if (!owner) atomic_compare_exchange_strong(&render_tid,&owner,current);
    if (atomic_load(&render_tid)!=current) {
        atomic_fetch_or(&header->bad_flags,BAD_THREAD);return result;
    }
    uint64_t now=clock_ns(CLOCK_UPTIME_RAW);
    if (!bucket_start) bucket_start=now;
    swaps++;if (result==kCGLNoError && paused_in_game()) paused_swaps++;
    if (control && control[0]==1 && control[1]) {
        if (window_frames<WINDOW_FRAMES && ++window_frames==WINDOW_FRAMES) {
            header->window_end_ns[control[1]-1]=now;
            atomic_store(&header->completed_windows,last_window);
        }
    }
    frame_number++;ordinal=0;
    if (log_fd>=0 && now-bucket_start>=1000000000ull) {
        char line[160];
        int n=snprintf(line,sizeof(line),"S,%llu,%llu,%llu,%llu,%llu\n",
            (unsigned long long)now,(unsigned long long)clock_ns(CLOCK_REALTIME),
            (unsigned long long)(now-bucket_start),
            (unsigned long long)swaps,(unsigned long long)paused_swaps);
        if (n>0 && (size_t)n<sizeof(line)) (void)write(log_fd,line,(size_t)n);
        bucket_start=now;swaps=paused_swaps=0;
    }
    return result;
}
static void *tracked_dlsym(void *handle,const char *symbol) {
    void *original=dlsym(handle,symbol);
    if (!original || !symbol) return original;
#define MAP(name,replacement) if (!strcmp(symbol,#name)) return (void *)replacement
    MAP(glUniform1i,tracked_uniform1i);
    MAP(glUniform4fvARB,tracked_uniform4fv);
    MAP(glUseProgramObjectARB,tracked_program);
    MAP(glActiveTextureARB,tracked_active);
    MAP(glBindBufferARB,tracked_buffer);
    MAP(glVertexAttribPointerARB,tracked_attrib);
    MAP(glEnableVertexAttribArrayARB,tracked_attrib_enable);
    MAP(glDisableVertexAttribArrayARB,tracked_attrib_disable);
    MAP(glVertexAttribDivisorARB,tracked_attrib_divisor);
    MAP(glBindFramebufferEXT,tracked_fbo);
    MAP(glDrawElementsBaseVertex,tracked_draw_base);
    MAP(glDrawElementsInstanced,tracked_draw_elements_instanced);
    MAP(glDrawArraysInstanced,tracked_draw_arrays_instanced);
#undef MAP
    return original;
}
#define INTERPOSE(name,replacement) {(const void *)replacement,(const void *)name}
__attribute__((used,section("__DATA,__interpose")))
static const struct {const void *replacement,*replacee;} interposes[]={
    INTERPOSE(dlsym,tracked_dlsym),
    INTERPOSE(CGLSetCurrentContext,tracked_set_context),
    INTERPOSE(CGLFlushDrawable,tracked_flush),
    INTERPOSE(glBindTexture,tracked_texture),
    INTERPOSE(glTexParameteri,tracked_tex_parameter_i),
    INTERPOSE(glTexParameterf,tracked_tex_parameter_f),
    INTERPOSE(glTexEnvf,tracked_tex_env),
    INTERPOSE(glEnable,tracked_enable),
    INTERPOSE(glDisable,tracked_disable),
    INTERPOSE(glBlendFunc,tracked_blend),
    INTERPOSE(glBlendFuncSeparate,tracked_blend_separate),
    INTERPOSE(glDepthFunc,tracked_depth),
    INTERPOSE(glDepthMask,tracked_depth_mask),
    INTERPOSE(glCullFace,tracked_cull),
    INTERPOSE(glColorMask,tracked_color),
    INTERPOSE(glStencilFunc,tracked_stencil_func),
    INTERPOSE(glStencilMask,tracked_stencil_mask),
    INTERPOSE(glStencilOp,tracked_stencil_op),
    INTERPOSE(glScissor,tracked_scissor),
    INTERPOSE(glViewport,tracked_viewport),
    INTERPOSE(glFrontFace,tracked_front),
    INTERPOSE(glPolygonMode,tracked_polygon),
    INTERPOSE(glAlphaFunc,tracked_alpha),
    INTERPOSE(glDrawElements,tracked_draw_elements),
    INTERPOSE(glDrawElementsBaseVertex,tracked_draw_base),
    INTERPOSE(glDrawArrays,tracked_draw_arrays),
    INTERPOSE(glDrawElementsInstanced,tracked_draw_elements_instanced),
    INTERPOSE(glDrawArraysInstanced,tracked_draw_arrays_instanced),
};
