// Passive, bounded telemetry for the x86_64 GOG EU IV process.
#define GL_SILENCE_DEPRECATION 1
#include <OpenGL/OpenGL.h>
#include <OpenGL/gl.h>
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-W#warnings"
#include <OpenGL/gl3.h>
#pragma clang diagnostic pop
#include <dlfcn.h>
#include <errno.h>
#include <execinfo.h>
#include <fcntl.h>
#include <mach/mach_time.h>
#include <poll.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/select.h>
#include <time.h>
#include <unistd.h>

enum { SWAP, DRAW_ELEMENTS, DRAW_BASE, DRAW_ARRAYS, BIND_FBO, CLEAR, VIEWPORT,
       USE_PROGRAM, ACTIVE_TEXTURE, BIND_TEXTURE, BIND_BUFFER, ENABLE, DISABLE,
       BLEND_FUNC, BLEND_SEPARATE, DEPTH_FUNC, DEPTH_MASK, CULL_FACE, COLOR_MASK,
       ATTRIB_PTR, ATTRIB_ENABLE, ATTRIB_DISABLE, UNIFORM_1I, UNIFORM_1F,
       UNIFORM_2F, UNIFORM_3F, UNIFORM_4F, UNIFORM_1IV, UNIFORM_1FV,
       UNIFORM_2FV, UNIFORM_3FV, UNIFORM_4FV, UNIFORM_MAT4, BUFFER_DATA,
       BUFFER_SUBDATA, TEX_IMAGE, TEX_SUBIMAGE, FINISH, GL_FLUSH, GET_ERROR,
       GET_INTEGER, GET_BOOLEAN, GET_FLOAT, READ_PIXELS, COPY_TEX,
       DRAW_ARRAYS_INSTANCED, DRAW_ELEMENTS_INSTANCED, BIND_VERTEX_ARRAY,
       SCISSOR, STENCIL_FUNC, STENCIL_MASK, STENCIL_OP, TEX_PARAMETER_I,
       TEX_PARAMETER_F, PIXEL_STORE, GET_TEX_IMAGE, TEX_IMAGE_3D,
       TEX_SUBIMAGE_3D, DELETE_TEXTURES, DELETE_BUFFERS, MAP_BUFFER,
       UNMAP_BUFFER, DELETE_PROGRAM, LINK_PROGRAM, BIND_RENDERBUFFER,
       DELETE_FRAMEBUFFERS, FRAMEBUFFER_TEXTURE, FRAMEBUFFER_RENDERBUFFER,
       GENERATE_MIPMAP, BLIT_FRAMEBUFFER, BIND_SAMPLER,
       UNIFORM_2I, UNIFORM_3I, UNIFORM_4I, UNIFORM_2IV,
       UNIFORM_3IV, UNIFORM_4IV, UNIFORM_MAT2, UNIFORM_MAT3,
       ALPHA_FUNC, BEGIN_PRIMITIVE, CLEAR_COLOR, COLOR_4F, END_PRIMITIVE,
       FRONT_FACE, GEN_TEXTURES, GET_STRING, LINE_WIDTH, LOAD_IDENTITY,
       MATRIX_MODE, ORTHO, POLYGON_MODE, TEX_ENV_F, VERTEX_2F,
       ACTIVE_TEXTURE_ARB, BIND_BUFFER_ARB, BIND_FBO_EXT, USE_PROGRAM_ARB,
       UNIFORM_1F_ARB, UNIFORM_1I_ARB, UNIFORM_MAT4_ARB, ATTRIB_PTR_ARB,
       BIND_VERTEX_ARRAY_APPLE, ATTRIB_ENABLE_ARB, ATTRIB_DISABLE_ARB,
       BUFFER_DATA_ARB, BUFFER_SUBDATA_ARB, FRAMEBUFFER_TEXTURE_EXT,
       GENERATE_MIPMAP_EXT, UNIFORM_1FV_ARB, UNIFORM_2FV_ARB,
       UNIFORM_3FV_ARB, UNIFORM_4FV_ARB, UNIFORM_MAT2_ARB,
       UNIFORM_MAT3_ARB,
       NANO_SLEEP, U_SLEEP, MACH_WAIT, COND_WAIT, POLL_WAIT, SELECT_WAIT, N_FUNCS };
static const char *names[N_FUNCS] = {
    "CGLFlushDrawable","glDrawElements","glDrawElementsBaseVertex","glDrawArrays",
    "glBindFramebuffer","glClear","glViewport","glUseProgram","glActiveTexture",
    "glBindTexture","glBindBuffer","glEnable","glDisable","glBlendFunc",
    "glBlendFuncSeparate","glDepthFunc","glDepthMask","glCullFace","glColorMask",
    "glVertexAttribPointer","glEnableVertexAttribArray","glDisableVertexAttribArray",
    "glUniform1i","glUniform1f","glUniform2f","glUniform3f","glUniform4f",
    "glUniform1iv","glUniform1fv","glUniform2fv","glUniform3fv","glUniform4fv",
    "glUniformMatrix4fv","glBufferData","glBufferSubData","glTexImage2D",
    "glTexSubImage2D","glFinish","glFlush","glGetError","glGetIntegerv",
    "glGetBooleanv","glGetFloatv","glReadPixels","glCopyTexSubImage2D",
    "glDrawArraysInstanced","glDrawElementsInstanced","glBindVertexArray",
    "glScissor","glStencilFunc","glStencilMask","glStencilOp",
    "glTexParameteri","glTexParameterf","glPixelStorei","glGetTexImage",
    "glTexImage3D","glTexSubImage3D","glDeleteTextures","glDeleteBuffers",
    "glMapBuffer","glUnmapBuffer","glDeleteProgram","glLinkProgram",
    "glBindRenderbuffer","glDeleteFramebuffers","glFramebufferTexture2D",
    "glFramebufferRenderbuffer","glGenerateMipmap","glBlitFramebuffer",
    "glBindSampler","glUniform2i","glUniform3i","glUniform4i",
    "glUniform2iv","glUniform3iv","glUniform4iv","glUniformMatrix2fv","glUniformMatrix3fv",
    "glAlphaFunc","glBegin","glClearColor","glColor4f","glEnd",
    "glFrontFace","glGenTextures","glGetString","glLineWidth","glLoadIdentity",
    "glMatrixMode","glOrtho","glPolygonMode","glTexEnvf","glVertex2f",
    "glActiveTextureARB","glBindBufferARB","glBindFramebufferEXT",
    "glUseProgramObjectARB","glUniform1fARB","glUniform1iARB",
    "glUniformMatrix4fvARB","glVertexAttribPointerARB",
    "glBindVertexArrayAPPLE","glEnableVertexAttribArrayARB",
    "glDisableVertexAttribArrayARB","glBufferDataARB","glBufferSubDataARB",
    "glFramebufferTexture2DEXT","glGenerateMipmapEXT","glUniform1fvARB",
    "glUniform2fvARB","glUniform3fvARB","glUniform4fvARB",
    "glUniformMatrix2fvARB","glUniformMatrix3fvARB",
    "nanosleep","usleep","mach_wait_until","pthread_cond_timedwait","poll","select"};

typedef struct { _Atomic uintptr_t caller; _Atomic unsigned short function; _Atomic uint64_t calls; } Caller;
typedef struct { uint64_t key, value; int valid; } Slot;
typedef struct { _Atomic GLuint fbo; _Atomic uint64_t passes, draws, repeated; _Atomic int valid; } PassStat;
typedef struct ThreadData {
    struct ThreadData *next;
    uint64_t tid;
    _Atomic uint64_t calls[N_FUNCS], redundant[N_FUNCS], time_ns[N_FUNCS], timed[N_FUNCS];
    Caller callers[256];
    uint64_t serial;
    CGLContextObj context;
    Slot state[128], uniforms[128];
    uintptr_t program;
    GLuint fbo, active_unit, array_buffer, element_buffer, vertex_array;
    uint64_t uniform_epoch, texture_epoch, buffer_epoch, render_epoch;
    uint64_t uniform_fingerprint, texture_fingerprint, buffer_fingerprint, render_fingerprint;
    uint64_t prev_program, prev_texture, prev_buffer, prev_render, prev_uniform, prev_complete;
    int have_prev_draw;
    _Atomic uint64_t draws, adjacent_program, adjacent_texture, adjacent_buffer;
    _Atomic uint64_t adjacent_render, adjacent_uniform, adjacent_complete;
    _Atomic uint64_t draw_modes[16], draw_sizes[5], submitted_vertices;
    uint64_t pass_hash, pass_draws, previous_pass_hash[24];
    GLuint previous_pass_fbo[24];
    unsigned pass_index;
    _Atomic uint64_t pass_total_draws, pass_repeated_draws, pass_overflow;
    PassStat pass_stats[32];
} ThreadData;

static _Thread_local ThreadData *local_data;
static ThreadData *threads;
static pthread_mutex_t list_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t output_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_once_t init_once = PTHREAD_ONCE_INIT;
static int output_fd = -1;
static volatile unsigned char *control_mode;
static _Atomic uint64_t last_snapshot_ns;
static _Atomic uint64_t frames;
static _Atomic uint64_t resolved[N_FUNCS];
static _Atomic uint64_t direct_lookup_count;
static _Atomic uint64_t unknown_gl_lookups;
static _Atomic uint64_t wait_buckets[6][2][5];
static _Atomic uint64_t caller_overflow, shadow_overflow, unknown_overflow;
static pthread_mutex_t deep_lock = PTHREAD_MUTEX_INITIALIZER;
static struct { int function, depth; void *addresses[6]; } deep_stacks[64];
static int deep_count, deep_reported;
static pthread_once_t timebase_once = PTHREAD_ONCE_INIT;
static mach_timebase_info_data_t mach_timebase;
static void setup_timebase(void) { mach_timebase_info(&mach_timebase); }
static pthread_mutex_t unknown_lock = PTHREAD_MUTEX_INITIALIZER;
static struct { char name[80]; uint64_t count, reported; } unknown_names[512];
static int unknown_count;

static uint64_t now_ns(void) {
    struct timespec t;
    // Python time.monotonic_ns() uses mach_absolute_time / CLOCK_UPTIME_RAW on macOS.
    return clock_gettime(CLOCK_UPTIME_RAW, &t) ? 0 : (uint64_t)t.tv_sec * 1000000000ull + t.tv_nsec;
}
static uint64_t wall_ns(void) {
    struct timespec t;
    return clock_gettime(CLOCK_REALTIME, &t) ? 0 :
           (uint64_t)t.tv_sec * 1000000000ull + t.tv_nsec;
}
static uint64_t hash_mix(uint64_t h, uint64_t x) { return (h ^ x) * 1099511628211ull; }
static uint64_t hash_bytes(const void *data, size_t length) {
    const unsigned char *p = data;
    uint64_t h = 1469598103934665603ull;
    for (size_t i = 0; i < length; i++) h = hash_mix(h, p[i]);
    return h;
}
static void setup(void) {
    const char *path = getenv("EU4_DIAG_LOG");
    if (path && path[0] == '/') output_fd = open(path, O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC, 0600);
    const char *control = getenv("EU4_DIAG_CONTROL");
    if (control && control[0] == '/') {
        int fd = open(control, O_RDONLY);
        if (fd >= 0) {
            void *mapping = mmap(NULL, 4096, PROT_READ, MAP_SHARED, fd, 0);
            if (mapping != MAP_FAILED) control_mode = mapping;
            close(fd);
        }
    }
}
static ThreadData *current(void) {
    pthread_once(&init_once, setup);
    if (local_data) return local_data;
    ThreadData *t = calloc(1, sizeof(*t));
    if (!t) return NULL;
    pthread_threadid_np(NULL, &t->tid);
    t->context = CGLGetCurrentContext();
    t->active_unit = GL_TEXTURE0;
    t->pass_hash = 1469598103934665603ull;
    pthread_mutex_lock(&list_lock);
    t->next = threads; threads = t;
    pthread_mutex_unlock(&list_lock);
    local_data = t;
    return t;
}
static void reset_shadow(ThreadData *t) {
    memset(t->state, 0, sizeof(t->state));
    memset(t->uniforms, 0, sizeof(t->uniforms));
    t->program = t->fbo = t->array_buffer = t->element_buffer = t->vertex_array = 0;
    t->active_unit = GL_TEXTURE0;
    t->uniform_epoch = t->texture_epoch = t->buffer_epoch = t->render_epoch = 0;
    t->uniform_fingerprint=t->texture_fingerprint=t->buffer_fingerprint=t->render_fingerprint=0;
    t->have_prev_draw = 0; t->pass_index = 0; t->pass_hash = 1469598103934665603ull;
}
static void invalidate_resource_shadow(ThreadData *t) {
    if (!t) return;
    memset(t->state,0,sizeof(t->state));
    t->texture_fingerprint=t->buffer_fingerprint=t->render_fingerprint=0;
    t->texture_epoch++; t->buffer_epoch++; t->render_epoch++;
}
static void check_context(ThreadData *t) {
    if ((++t->serial & 1023) == 0) {
        CGLContextObj context = CGLGetCurrentContext();
        if (context != t->context) { t->context = context; reset_shadow(t); }
    }
}
static int shadow_set(Slot *slots, size_t size, uint64_t key, uint64_t value,
                      uint64_t *fingerprint) {
    size_t index = (size_t)(hash_mix(1469598103934665603ull, key) % size);
    for (size_t i = 0; i < size; i++) {
        Slot *s = &slots[(index + i) % size];
        if (!s->valid) {
            s->valid = 1; s->key = key; s->value = value;
            *fingerprint ^= hash_mix(key,value);
            return 0;
        }
        if (s->key == key) {
            int same = s->value == value;
            if (!same) *fingerprint ^= hash_mix(key,s->value) ^ hash_mix(key,value);
            s->value = value;
            return same;
        }
    }
    atomic_fetch_add(&shadow_overflow,1);
    return 0;
}
static void caller_add(ThreadData *t, int id, uintptr_t address) {
    size_t start = (size_t)(hash_mix(address, (uint64_t)id) % 256);
    for (size_t i = 0; i < 256; i++) {
        Caller *c = &t->callers[(start+i)%256];
        if (!atomic_load(&c->caller)) {
            atomic_store(&c->function,id);
            atomic_store(&c->caller,address);
            atomic_store(&c->calls,1);
            return;
        }
        if (atomic_load(&c->caller) == address && atomic_load(&c->function) == id) {
            atomic_fetch_add(&c->calls,1); return;
        }
    }
    atomic_fetch_add(&caller_overflow,1);
}
typedef struct { ThreadData *t; uint64_t started; int sample; } Event;
static Event begin_event(int id, uintptr_t caller) {
    ThreadData *t = current();
    if (!t || output_fd < 0) return (Event){0};
    atomic_fetch_add(&t->calls[id], 1);
    check_context(t);
    int detailed = !control_mode || *control_mode == 1;
    int sample = detailed && ((++t->serial & 63) == 0 || id == SWAP || id == FINISH ||
                              id == GL_FLUSH || id == READ_PIXELS || id >= NANO_SLEEP);
    if (sample) caller_add(t,id,caller);
    if (sample && id < NANO_SLEEP && (t->serial & 8191) == 0) {
        pthread_mutex_lock(&deep_lock);
        if (deep_count < 64) {
            deep_stacks[deep_count].function=id;
            deep_stacks[deep_count].depth=backtrace(deep_stacks[deep_count].addresses,6);
            deep_count++;
        }
        pthread_mutex_unlock(&deep_lock);
    }
    return (Event){t, sample ? now_ns() : 0, sample};
}
static void end_event(Event event, int id) {
    if (event.sample) {
        uint64_t elapsed = now_ns() - event.started;
        atomic_fetch_add(&event.t->time_ns[id], elapsed);
        atomic_fetch_add(&event.t->timed[id], 1);
    }
}
static void state_change(Event event, int id, uint64_t key, uint64_t value, int kind) {
    ThreadData *t = event.t;
    if (!t || (control_mode && *control_mode == 0)) return;
    int shadow_id = id == DISABLE ? ENABLE :
                    id == ATTRIB_DISABLE ? ATTRIB_ENABLE :
                    id == ATTRIB_DISABLE_ARB ? ATTRIB_ENABLE_ARB : id;
    uint64_t *fingerprint = kind == 1 ? &t->texture_fingerprint :
                            kind == 2 ? &t->buffer_fingerprint : &t->render_fingerprint;
    if (shadow_set(t->state,128,((uint64_t)shadow_id<<32)|key,value,fingerprint))
        atomic_fetch_add(&t->redundant[id],1);
    else if (kind == 1) t->texture_epoch++;
    else if (kind == 2) t->buffer_epoch++;
    else t->render_epoch++;
}
static void uniform_value(Event event, int id, GLint location, const void *value, size_t length) {
    ThreadData *t = event.t;
    if (!t || !value || location < 0 || t->program == 0 || length > 256 ||
        (control_mode && *control_mode == 0)) return;
    uint64_t key = hash_mix(t->program,((uint64_t)(uint32_t)location<<32)|id);
    if (shadow_set(t->uniforms,128,key,hash_bytes(value,length),&t->uniform_fingerprint))
        atomic_fetch_add(&t->redundant[id],1);
    else t->uniform_epoch++;
}
static void pass_end(ThreadData *t) {
    unsigned index = t->pass_index;
    if (index < 24 && t->pass_draws) {
        atomic_fetch_add(&t->pass_total_draws,t->pass_draws);
        int repeated=t->previous_pass_fbo[index] == t->fbo &&
                     t->previous_pass_hash[index] == t->pass_hash;
        if (repeated)
            atomic_fetch_add(&t->pass_repeated_draws,t->pass_draws);
        size_t start=t->fbo%32;
        for (size_t i=0;i<32;i++) {
            PassStat *stat=&t->pass_stats[(start+i)%32];
            if (!atomic_load(&stat->valid)) {
                atomic_store(&stat->fbo,t->fbo);
                atomic_store(&stat->valid,1);
            }
            if (atomic_load(&stat->fbo)==t->fbo) {
                atomic_fetch_add(&stat->passes,1);
                atomic_fetch_add(&stat->draws,t->pass_draws);
                if (repeated) atomic_fetch_add(&stat->repeated,t->pass_draws);
                break;
            }
            if (i==31) atomic_fetch_add(&t->pass_overflow,1);
        }
        t->previous_pass_fbo[index] = t->fbo;
        t->previous_pass_hash[index] = t->pass_hash;
    } else if (index >= 24) atomic_fetch_add(&t->pass_overflow,1);
    t->pass_hash = 1469598103934665603ull;
    t->pass_draws = 0;
}
static void draw(Event event, GLenum mode, GLsizei count) {
    ThreadData *t = event.t;
    if (!t || (control_mode && *control_mode == 0)) return;
    uint64_t program=t->program, textures=t->texture_epoch, buffers=t->buffer_epoch;
    uint64_t render=t->render_epoch, uniform=t->uniform_epoch;
    uint64_t complete=hash_mix(hash_mix(hash_mix(program,textures),buffers),hash_mix(render,uniform));
    atomic_fetch_add(&t->draws,1);
    if (mode < 16) atomic_fetch_add(&t->draw_modes[mode],1);
    int size_bin = count <= 3 ? 0 : count <= 31 ? 1 : count <= 255 ? 2 :
                   count <= 4095 ? 3 : 4;
    atomic_fetch_add(&t->draw_sizes[size_bin],1);
    if (count > 0) atomic_fetch_add(&t->submitted_vertices,(uint64_t)count);
    if (t->have_prev_draw) {
        if (program==t->prev_program) atomic_fetch_add(&t->adjacent_program,1);
        if (textures==t->prev_texture) atomic_fetch_add(&t->adjacent_texture,1);
        if (buffers==t->prev_buffer) atomic_fetch_add(&t->adjacent_buffer,1);
        if (render==t->prev_render) atomic_fetch_add(&t->adjacent_render,1);
        if (uniform==t->prev_uniform) atomic_fetch_add(&t->adjacent_uniform,1);
        if (complete==t->prev_complete) atomic_fetch_add(&t->adjacent_complete,1);
    }
    t->have_prev_draw=1; t->prev_program=program; t->prev_texture=textures;
    t->prev_buffer=buffers; t->prev_render=render; t->prev_uniform=uniform;
    t->prev_complete=complete;
    uint64_t structural=hash_mix(hash_mix(hash_mix(program,t->texture_fingerprint),
                                  t->buffer_fingerprint),
                                  hash_mix(t->render_fingerprint,t->uniform_fingerprint));
    t->pass_hash=hash_mix(hash_mix(t->pass_hash,structural),((uint64_t)mode<<32)|(uint32_t)count);
    t->pass_draws++;
}
static void write_line(const char *line, int length) {
    if (output_fd >= 0 && length > 0) (void)write(output_fd,line,(size_t)length);
}
static void snapshot(uint64_t timestamp) {
    if (output_fd < 0 || timestamp-atomic_load(&last_snapshot_ns) < 1000000000ull) return;
    pthread_mutex_lock(&output_lock);
    if (timestamp-atomic_load(&last_snapshot_ns) < 1000000000ull) { pthread_mutex_unlock(&output_lock); return; }
    atomic_store(&last_snapshot_ns,timestamp);
    char line[1024];
    int n=snprintf(line,sizeof(line),"S,%llu,%u,%llu,%llu,%llu\n",
        (unsigned long long)timestamp,control_mode?*control_mode:1,
        (unsigned long long)atomic_exchange(&frames,0),
        (unsigned long long)atomic_exchange(&unknown_gl_lookups,0),
        (unsigned long long)wall_ns());
    write_line(line,n);
    n=snprintf(line,sizeof(line),"Q,%llu,%llu,%llu,%llu\n",
        (unsigned long long)timestamp,
        (unsigned long long)atomic_exchange(&caller_overflow,0),
        (unsigned long long)atomic_exchange(&shadow_overflow,0),
        (unsigned long long)atomic_exchange(&unknown_overflow,0));
    write_line(line,n);
    pthread_mutex_lock(&unknown_lock);
    for (int i=0;i<unknown_count;i++) {
        if (unknown_names[i].count == unknown_names[i].reported) continue;
        n=snprintf(line,sizeof(line),"U,%llu,%s,%llu\n",
            (unsigned long long)timestamp,unknown_names[i].name,
            (unsigned long long)(unknown_names[i].count-unknown_names[i].reported));
        write_line(line,n);
        unknown_names[i].reported=unknown_names[i].count;
    }
    pthread_mutex_unlock(&unknown_lock);
    pthread_mutex_lock(&deep_lock);
    for (int i=deep_reported;i<deep_count;i++) {
        char stack[512]={0};
        size_t used=0;
        for (int j=0;j<deep_stacks[i].depth;j++) {
            Dl_info info={0};
            dladdr(deep_stacks[i].addresses[j],&info);
            const char *image=info.dli_fname?strrchr(info.dli_fname,'/'):NULL;
            image=image?image+1:(info.dli_fname?info.dli_fname:"unknown");
            uintptr_t offset=info.dli_fbase?(uintptr_t)deep_stacks[i].addresses[j]-(uintptr_t)info.dli_fbase:
                             (uintptr_t)deep_stacks[i].addresses[j];
            int added=snprintf(stack+used,sizeof(stack)-used,"%s%s+0x%llx",
                               j?"|":"",image,(unsigned long long)offset);
            if (added<0 || (size_t)added>=sizeof(stack)-used) break;
            used+=(size_t)added;
        }
        n=snprintf(line,sizeof(line),"B,%llu,%s,%.700s\n",
                   (unsigned long long)timestamp,names[deep_stacks[i].function],stack);
        write_line(line,n);
    }
    deep_reported=deep_count;
    pthread_mutex_unlock(&deep_lock);
    pthread_mutex_lock(&list_lock);
    for (ThreadData *t=threads;t;t=t->next) {
        for (int i=0;i<N_FUNCS;i++) {
            uint64_t calls=atomic_exchange(&t->calls[i],0);
            uint64_t redundant=atomic_exchange(&t->redundant[i],0);
            uint64_t time=atomic_exchange(&t->time_ns[i],0);
            uint64_t timed=atomic_exchange(&t->timed[i],0);
            if (!calls) continue;
            n=snprintf(line,sizeof(line),"F,%llu,%llu,%s,%llu,%llu,%llu,%llu\n",
                (unsigned long long)timestamp,(unsigned long long)t->tid,names[i],
                (unsigned long long)calls,(unsigned long long)redundant,
                (unsigned long long)time,(unsigned long long)timed);
            write_line(line,n);
        }
        for (int i=0;i<256;i++) {
            Caller *c=&t->callers[i];
            uint64_t calls=atomic_exchange(&c->calls,0);
            if (!calls) continue;
            uintptr_t caller=atomic_load(&c->caller);
            Dl_info info={0};
            dladdr((void*)caller,&info);
            const char *image=info.dli_fname?strrchr(info.dli_fname,'/'):NULL;
            image=image?image+1:(info.dli_fname?info.dli_fname:"unknown");
            uintptr_t offset=info.dli_fbase?caller-(uintptr_t)info.dli_fbase:caller;
            n=snprintf(line,sizeof(line),"C,%llu,%llu,%s,%s,0x%llx,%llu\n",
                (unsigned long long)timestamp,(unsigned long long)t->tid,names[atomic_load(&c->function)],
                image,(unsigned long long)offset,(unsigned long long)calls);
            write_line(line,n);
        }
        uint64_t draws=atomic_exchange(&t->draws,0);
        if (draws) {
            n=snprintf(line,sizeof(line),"D,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%llu\n",
                (unsigned long long)timestamp,(unsigned long long)t->tid,
                (unsigned long long)draws,
                (unsigned long long)atomic_exchange(&t->adjacent_program,0),
                (unsigned long long)atomic_exchange(&t->adjacent_texture,0),
                (unsigned long long)atomic_exchange(&t->adjacent_buffer,0),
                (unsigned long long)atomic_exchange(&t->adjacent_render,0),
                (unsigned long long)atomic_exchange(&t->adjacent_uniform,0),
                (unsigned long long)atomic_exchange(&t->adjacent_complete,0),
                (unsigned long long)atomic_exchange(&t->pass_total_draws,0),
                (unsigned long long)atomic_exchange(&t->pass_repeated_draws,0));
            write_line(line,n);
            n=snprintf(line,sizeof(line),"V,%llu,%llu,%llu\n",
                (unsigned long long)timestamp,(unsigned long long)t->tid,
                (unsigned long long)atomic_exchange(&t->submitted_vertices,0));
            write_line(line,n);
            for (int mode=0;mode<16;mode++) {
                uint64_t count=atomic_exchange(&t->draw_modes[mode],0);
                if (!count) continue;
                n=snprintf(line,sizeof(line),"R,%llu,%llu,%d,%llu\n",
                    (unsigned long long)timestamp,(unsigned long long)t->tid,mode,
                    (unsigned long long)count);
                write_line(line,n);
            }
            for (int bin=0;bin<5;bin++) {
                uint64_t count=atomic_exchange(&t->draw_sizes[bin],0);
                if (!count) continue;
                n=snprintf(line,sizeof(line),"Z,%llu,%llu,%d,%llu\n",
                    (unsigned long long)timestamp,(unsigned long long)t->tid,bin,
                    (unsigned long long)count);
                write_line(line,n);
            }
        }
        for (int i=0;i<32;i++) {
            PassStat *stat=&t->pass_stats[i];
            if (!atomic_load(&stat->valid) || !atomic_load(&stat->passes)) continue;
            n=snprintf(line,sizeof(line),"P,%llu,%llu,%u,%llu,%llu,%llu\n",
                (unsigned long long)timestamp,(unsigned long long)t->tid,atomic_load(&stat->fbo),
                (unsigned long long)atomic_exchange(&stat->passes,0),
                (unsigned long long)atomic_exchange(&stat->draws,0),
                (unsigned long long)atomic_exchange(&stat->repeated,0));
            write_line(line,n);
        }
        uint64_t overflow=atomic_exchange(&t->pass_overflow,0);
        if (overflow) {
            n=snprintf(line,sizeof(line),"O,%llu,%llu,%llu\n",
                (unsigned long long)timestamp,(unsigned long long)t->tid,
                (unsigned long long)overflow);
            write_line(line,n);
        }
    }
    pthread_mutex_unlock(&list_lock);
    for (int i=0;i<6;i++) for (int kind=0;kind<2;kind++) for (int b=0;b<5;b++) {
        uint64_t calls=atomic_exchange(&wait_buckets[i][kind][b],0);
        if (!calls) continue;
        n=snprintf(line,sizeof(line),"W,%llu,%s,%s,%d,%llu\n",
            (unsigned long long)timestamp,names[NANO_SLEEP+i],kind?"actual":"requested",
            b,(unsigned long long)calls);
        write_line(line,n);
    }
    pthread_mutex_unlock(&output_lock);
}
static int wait_bin(uint64_t duration) {
    return duration<1000000?0:duration<2000000?1:duration<3000000?2:duration<5000000?3:4;
}
static void wait_record(int index, uint64_t requested, uint64_t actual) {
    if (requested != UINT64_MAX)
        atomic_fetch_add(&wait_buckets[index][0][wait_bin(requested)],1);
    atomic_fetch_add(&wait_buckets[index][1][wait_bin(actual)],1);
}

#define CALLER ((uintptr_t)__builtin_return_address(0))
#define WRAP_VOID(name,id,args,call,extra) \
    static void tracked_##name args { \
        if (control_mode && *control_mode==3) { name call; return; } \
        Event e=begin_event(id,CALLER); extra; name call; end_event(e,id); }
#define WRAP_RET(type,name,id,args,call,extra) \
    static type tracked_##name args { \
        if (control_mode && *control_mode==3) return name call; \
        Event e=begin_event(id,CALLER); extra; type result=name call; end_event(e,id); return result; }
WRAP_VOID(glDrawElements,DRAW_ELEMENTS,(GLenum mode,GLsizei count,GLenum type,const GLvoid *indices),(mode,count,type,indices),draw(e,mode,count))
WRAP_VOID(glDrawElementsBaseVertex,DRAW_BASE,(GLenum mode,GLsizei count,GLenum type,const GLvoid *indices,GLint base),(mode,count,type,indices,base),draw(e,mode,count))
WRAP_VOID(glDrawArrays,DRAW_ARRAYS,(GLenum mode,GLint first,GLsizei count),(mode,first,count),draw(e,mode,count))
WRAP_VOID(glBindFramebuffer,BIND_FBO,(GLenum target,GLuint fbo),(target,fbo),{if(e.t && (target==GL_FRAMEBUFFER || target==GL_DRAW_FRAMEBUFFER) && e.t->fbo!=fbo){pass_end(e.t);e.t->pass_index++;e.t->fbo=fbo;}state_change(e,BIND_FBO,target,fbo,0);})
WRAP_VOID(glClear,CLEAR,(GLbitfield mask),(mask),{if(e.t)e.t->pass_hash=hash_mix(e.t->pass_hash,mask);})
WRAP_VOID(glViewport,VIEWPORT,(GLint x,GLint y,GLsizei w,GLsizei h),(x,y,w,h),state_change(e,VIEWPORT,0,hash_mix(((uint64_t)(uint32_t)x<<32)|(uint32_t)y,((uint64_t)(uint32_t)w<<32)|(uint32_t)h),0))
WRAP_VOID(glUseProgram,USE_PROGRAM,(GLuint p),(p),{state_change(e,USE_PROGRAM,0,p,0);if(e.t)e.t->program=p;})
WRAP_VOID(glActiveTexture,ACTIVE_TEXTURE,(GLenum unit),(unit),{state_change(e,ACTIVE_TEXTURE,0,unit,1);if(e.t)e.t->active_unit=unit;})
WRAP_VOID(glBindTexture,BIND_TEXTURE,(GLenum target,GLuint tex),(target,tex),state_change(e,BIND_TEXTURE,((uint64_t)(e.t?e.t->active_unit:0)<<32)|target,tex,1))
WRAP_VOID(glBindBuffer,BIND_BUFFER,(GLenum target,GLuint buffer),(target,buffer),{state_change(e,BIND_BUFFER,target,buffer,2);if(e.t){if(target==GL_ARRAY_BUFFER)e.t->array_buffer=buffer;if(target==GL_ELEMENT_ARRAY_BUFFER)e.t->element_buffer=buffer;}})
WRAP_VOID(glEnable,ENABLE,(GLenum cap),(cap),state_change(e,ENABLE,cap,1,0))
WRAP_VOID(glDisable,DISABLE,(GLenum cap),(cap),state_change(e,ENABLE,cap,0,0))
WRAP_VOID(glBlendFunc,BLEND_FUNC,(GLenum src,GLenum dst),(src,dst),state_change(e,BLEND_FUNC,0,((uint64_t)src<<32)|dst,0))
WRAP_VOID(glBlendFuncSeparate,BLEND_SEPARATE,(GLenum sr,GLenum dr,GLenum sa,GLenum da),(sr,dr,sa,da),state_change(e,BLEND_SEPARATE,0,hash_mix(((uint64_t)sr<<32)|dr,((uint64_t)sa<<32)|da),0))
WRAP_VOID(glDepthFunc,DEPTH_FUNC,(GLenum func),(func),state_change(e,DEPTH_FUNC,0,func,0))
WRAP_VOID(glDepthMask,DEPTH_MASK,(GLboolean flag),(flag),state_change(e,DEPTH_MASK,0,flag,0))
WRAP_VOID(glCullFace,CULL_FACE,(GLenum mode),(mode),state_change(e,CULL_FACE,0,mode,0))
WRAP_VOID(glColorMask,COLOR_MASK,(GLboolean r,GLboolean g,GLboolean b,GLboolean a),(r,g,b,a),state_change(e,COLOR_MASK,0,((uint64_t)r<<24)|((uint64_t)g<<16)|((uint64_t)b<<8)|a,0))
WRAP_VOID(glVertexAttribPointer,ATTRIB_PTR,(GLuint index,GLint size,GLenum type,GLboolean normalized,GLsizei stride,const GLvoid *ptr),(index,size,type,normalized,stride,ptr),state_change(e,ATTRIB_PTR,((uint64_t)(e.t?e.t->vertex_array:0)<<32)|index,hash_mix(hash_mix(hash_mix((uint64_t)size,type),((uint64_t)normalized<<32)|(uint32_t)stride),hash_mix((uintptr_t)ptr,e.t?e.t->array_buffer:0)),2))
WRAP_VOID(glEnableVertexAttribArray,ATTRIB_ENABLE,(GLuint index),(index),state_change(e,ATTRIB_ENABLE,((uint64_t)(e.t?e.t->vertex_array:0)<<32)|index,1,2))
WRAP_VOID(glDisableVertexAttribArray,ATTRIB_DISABLE,(GLuint index),(index),state_change(e,ATTRIB_DISABLE,((uint64_t)(e.t?e.t->vertex_array:0)<<32)|index,0,2))
#define UNIFORM_SCALAR(name,id,type,decl,call) WRAP_VOID(name,id,decl,call,uniform_value(e,id,location,&v0,sizeof(v0)))
UNIFORM_SCALAR(glUniform1i,UNIFORM_1I,GLint,(GLint location,GLint v0),(location,v0))
UNIFORM_SCALAR(glUniform1f,UNIFORM_1F,GLfloat,(GLint location,GLfloat v0),(location,v0))
WRAP_VOID(glUniform2f,UNIFORM_2F,(GLint location,GLfloat v0,GLfloat v1),(location,v0,v1),{GLfloat v[2];v[0]=v0;v[1]=v1;uniform_value(e,UNIFORM_2F,location,v,sizeof(v));})
WRAP_VOID(glUniform3f,UNIFORM_3F,(GLint location,GLfloat v0,GLfloat v1,GLfloat v2),(location,v0,v1,v2),{GLfloat v[3];v[0]=v0;v[1]=v1;v[2]=v2;uniform_value(e,UNIFORM_3F,location,v,sizeof(v));})
WRAP_VOID(glUniform4f,UNIFORM_4F,(GLint location,GLfloat v0,GLfloat v1,GLfloat v2,GLfloat v3),(location,v0,v1,v2,v3),{GLfloat v[4];v[0]=v0;v[1]=v1;v[2]=v2;v[3]=v3;uniform_value(e,UNIFORM_4F,location,v,sizeof(v));})
WRAP_VOID(glUniform1iv,UNIFORM_1IV,(GLint location,GLsizei count,const GLint *v),(location,count,v),uniform_value(e,UNIFORM_1IV,location,v,count>0?(size_t)count*sizeof(*v):0))
WRAP_VOID(glUniform1fv,UNIFORM_1FV,(GLint location,GLsizei count,const GLfloat *v),(location,count,v),uniform_value(e,UNIFORM_1FV,location,v,count>0?(size_t)count*sizeof(*v):0))
WRAP_VOID(glUniform2fv,UNIFORM_2FV,(GLint location,GLsizei count,const GLfloat *v),(location,count,v),uniform_value(e,UNIFORM_2FV,location,v,count>0?(size_t)count*2*sizeof(*v):0))
WRAP_VOID(glUniform3fv,UNIFORM_3FV,(GLint location,GLsizei count,const GLfloat *v),(location,count,v),uniform_value(e,UNIFORM_3FV,location,v,count>0?(size_t)count*3*sizeof(*v):0))
WRAP_VOID(glUniform4fv,UNIFORM_4FV,(GLint location,GLsizei count,const GLfloat *v),(location,count,v),uniform_value(e,UNIFORM_4FV,location,v,count>0?(size_t)count*4*sizeof(*v):0))
WRAP_VOID(glUniformMatrix4fv,UNIFORM_MAT4,(GLint location,GLsizei count,GLboolean transpose,const GLfloat *v),(location,count,transpose,v),uniform_value(e,UNIFORM_MAT4,location,v,count>0?(size_t)count*16*sizeof(*v):0))
WRAP_VOID(glBufferData,BUFFER_DATA,(GLenum target,GLsizeiptr size,const GLvoid *data,GLenum usage),(target,size,data,usage),{if(e.t)e.t->buffer_epoch++;})
WRAP_VOID(glBufferSubData,BUFFER_SUBDATA,(GLenum target,GLintptr offset,GLsizeiptr size,const GLvoid *data),(target,offset,size,data),{if(e.t)e.t->buffer_epoch++;})
WRAP_VOID(glTexImage2D,TEX_IMAGE,(GLenum target,GLint level,GLint internal,GLsizei w,GLsizei h,GLint border,GLenum format,GLenum type,const GLvoid *pixels),(target,level,internal,w,h,border,format,type,pixels),{if(e.t)e.t->texture_epoch++;})
WRAP_VOID(glTexSubImage2D,TEX_SUBIMAGE,(GLenum target,GLint level,GLint x,GLint y,GLsizei w,GLsizei h,GLenum format,GLenum type,const GLvoid *pixels),(target,level,x,y,w,h,format,type,pixels),{if(e.t)e.t->texture_epoch++;})
WRAP_VOID(glFinish,FINISH,(void),(),{})
WRAP_VOID(glFlush,GL_FLUSH,(void),(),{})
WRAP_RET(GLenum,glGetError,GET_ERROR,(void),(),{})
WRAP_VOID(glGetIntegerv,GET_INTEGER,(GLenum name,GLint *value),(name,value),{})
WRAP_VOID(glGetBooleanv,GET_BOOLEAN,(GLenum name,GLboolean *value),(name,value),{})
WRAP_VOID(glGetFloatv,GET_FLOAT,(GLenum name,GLfloat *value),(name,value),{})
WRAP_VOID(glReadPixels,READ_PIXELS,(GLint x,GLint y,GLsizei w,GLsizei h,GLenum format,GLenum type,GLvoid *data),(x,y,w,h,format,type,data),{})
WRAP_VOID(glCopyTexSubImage2D,COPY_TEX,(GLenum target,GLint level,GLint xoff,GLint yoff,GLint x,GLint y,GLsizei w,GLsizei h),(target,level,xoff,yoff,x,y,w,h),{})
WRAP_VOID(glDrawArraysInstanced,DRAW_ARRAYS_INSTANCED,(GLenum mode,GLint first,GLsizei count,GLsizei instances),(mode,first,count,instances),draw(e,mode,count))
WRAP_VOID(glDrawElementsInstanced,DRAW_ELEMENTS_INSTANCED,(GLenum mode,GLsizei count,GLenum type,const GLvoid *indices,GLsizei instances),(mode,count,type,indices,instances),draw(e,mode,count))
WRAP_VOID(glBindVertexArray,BIND_VERTEX_ARRAY,(GLuint array),(array),{state_change(e,BIND_VERTEX_ARRAY,0,array,2);if(e.t)e.t->vertex_array=array;})
WRAP_VOID(glScissor,SCISSOR,(GLint x,GLint y,GLsizei w,GLsizei h),(x,y,w,h),state_change(e,SCISSOR,0,hash_mix(((uint64_t)(uint32_t)x<<32)|(uint32_t)y,((uint64_t)(uint32_t)w<<32)|(uint32_t)h),0))
WRAP_VOID(glStencilFunc,STENCIL_FUNC,(GLenum func,GLint ref,GLuint mask),(func,ref,mask),state_change(e,STENCIL_FUNC,0,hash_mix(func,((uint64_t)(uint32_t)ref<<32)|mask),0))
WRAP_VOID(glStencilMask,STENCIL_MASK,(GLuint mask),(mask),state_change(e,STENCIL_MASK,0,mask,0))
WRAP_VOID(glStencilOp,STENCIL_OP,(GLenum fail,GLenum zfail,GLenum zpass),(fail,zfail,zpass),state_change(e,STENCIL_OP,0,hash_mix(fail,((uint64_t)zfail<<32)|zpass),0))
WRAP_VOID(glTexParameteri,TEX_PARAMETER_I,(GLenum target,GLenum pname,GLint param),(target,pname,param),{if(e.t)e.t->texture_epoch++;})
WRAP_VOID(glTexParameterf,TEX_PARAMETER_F,(GLenum target,GLenum pname,GLfloat param),(target,pname,param),{if(e.t)e.t->texture_epoch++;})
WRAP_VOID(glPixelStorei,PIXEL_STORE,(GLenum pname,GLint param),(pname,param),state_change(e,PIXEL_STORE,pname,(uint32_t)param,0))
WRAP_VOID(glGetTexImage,GET_TEX_IMAGE,(GLenum target,GLint level,GLenum format,GLenum type,GLvoid *pixels),(target,level,format,type,pixels),{})
WRAP_VOID(glTexImage3D,TEX_IMAGE_3D,(GLenum target,GLint level,GLint internal,GLsizei w,GLsizei h,GLsizei depth,GLint border,GLenum format,GLenum type,const GLvoid *pixels),(target,level,internal,w,h,depth,border,format,type,pixels),{if(e.t)e.t->texture_epoch++;})
WRAP_VOID(glTexSubImage3D,TEX_SUBIMAGE_3D,(GLenum target,GLint level,GLint x,GLint y,GLint z,GLsizei w,GLsizei h,GLsizei depth,GLenum format,GLenum type,const GLvoid *pixels),(target,level,x,y,z,w,h,depth,format,type,pixels),{if(e.t)e.t->texture_epoch++;})
WRAP_VOID(glDeleteTextures,DELETE_TEXTURES,(GLsizei n,const GLuint *textures),(n,textures),invalidate_resource_shadow(e.t))
WRAP_VOID(glDeleteBuffers,DELETE_BUFFERS,(GLsizei n,const GLuint *buffers),(n,buffers),invalidate_resource_shadow(e.t))
WRAP_RET(GLvoid*,glMapBuffer,MAP_BUFFER,(GLenum target,GLenum access),(target,access),{if(e.t)e.t->buffer_epoch++;})
WRAP_RET(GLboolean,glUnmapBuffer,UNMAP_BUFFER,(GLenum target),(target),{if(e.t)e.t->buffer_epoch++;})
WRAP_VOID(glDeleteProgram,DELETE_PROGRAM,(GLuint program),(program),{if(e.t){memset(e.t->uniforms,0,sizeof(e.t->uniforms));e.t->uniform_fingerprint=0;e.t->uniform_epoch++;}})
WRAP_VOID(glLinkProgram,LINK_PROGRAM,(GLuint program),(program),{if(e.t){memset(e.t->uniforms,0,sizeof(e.t->uniforms));e.t->uniform_fingerprint=0;e.t->uniform_epoch++;}})
WRAP_VOID(glBindRenderbuffer,BIND_RENDERBUFFER,(GLenum target,GLuint buffer),(target,buffer),state_change(e,BIND_RENDERBUFFER,target,buffer,0))
WRAP_VOID(glDeleteFramebuffers,DELETE_FRAMEBUFFERS,(GLsizei n,const GLuint *buffers),(n,buffers),invalidate_resource_shadow(e.t))
WRAP_VOID(glFramebufferTexture2D,FRAMEBUFFER_TEXTURE,(GLenum target,GLenum attachment,GLenum textarget,GLuint texture,GLint level),(target,attachment,textarget,texture,level),{if(e.t)e.t->pass_hash=hash_mix(e.t->pass_hash,((uint64_t)attachment<<32)|texture);})
WRAP_VOID(glFramebufferRenderbuffer,FRAMEBUFFER_RENDERBUFFER,(GLenum target,GLenum attachment,GLenum renderbuffertarget,GLuint buffer),(target,attachment,renderbuffertarget,buffer),{if(e.t)e.t->pass_hash=hash_mix(e.t->pass_hash,((uint64_t)attachment<<32)|buffer);})
WRAP_VOID(glGenerateMipmap,GENERATE_MIPMAP,(GLenum target),(target),{if(e.t)e.t->texture_epoch++;})
WRAP_VOID(glBlitFramebuffer,BLIT_FRAMEBUFFER,(GLint sx0,GLint sy0,GLint sx1,GLint sy1,GLint dx0,GLint dy0,GLint dx1,GLint dy1,GLbitfield mask,GLenum filter),(sx0,sy0,sx1,sy1,dx0,dy0,dx1,dy1,mask,filter),{})
WRAP_VOID(glBindSampler,BIND_SAMPLER,(GLuint unit,GLuint sampler),(unit,sampler),state_change(e,BIND_SAMPLER,unit,sampler,1))
WRAP_VOID(glUniform2i,UNIFORM_2I,(GLint location,GLint v0,GLint v1),(location,v0,v1),{GLint v[2];v[0]=v0;v[1]=v1;uniform_value(e,UNIFORM_2I,location,v,sizeof(v));})
WRAP_VOID(glUniform3i,UNIFORM_3I,(GLint location,GLint v0,GLint v1,GLint v2),(location,v0,v1,v2),{GLint v[3];v[0]=v0;v[1]=v1;v[2]=v2;uniform_value(e,UNIFORM_3I,location,v,sizeof(v));})
WRAP_VOID(glUniform4i,UNIFORM_4I,(GLint location,GLint v0,GLint v1,GLint v2,GLint v3),(location,v0,v1,v2,v3),{GLint v[4];v[0]=v0;v[1]=v1;v[2]=v2;v[3]=v3;uniform_value(e,UNIFORM_4I,location,v,sizeof(v));})
WRAP_VOID(glUniform2iv,UNIFORM_2IV,(GLint location,GLsizei count,const GLint *v),(location,count,v),uniform_value(e,UNIFORM_2IV,location,v,count>0?(size_t)count*2*sizeof(*v):0))
WRAP_VOID(glUniform3iv,UNIFORM_3IV,(GLint location,GLsizei count,const GLint *v),(location,count,v),uniform_value(e,UNIFORM_3IV,location,v,count>0?(size_t)count*3*sizeof(*v):0))
WRAP_VOID(glUniform4iv,UNIFORM_4IV,(GLint location,GLsizei count,const GLint *v),(location,count,v),uniform_value(e,UNIFORM_4IV,location,v,count>0?(size_t)count*4*sizeof(*v):0))
WRAP_VOID(glUniformMatrix2fv,UNIFORM_MAT2,(GLint location,GLsizei count,GLboolean transpose,const GLfloat *v),(location,count,transpose,v),uniform_value(e,UNIFORM_MAT2,location,v,count>0?(size_t)count*4*sizeof(*v):0))
WRAP_VOID(glUniformMatrix3fv,UNIFORM_MAT3,(GLint location,GLsizei count,GLboolean transpose,const GLfloat *v),(location,count,transpose,v),uniform_value(e,UNIFORM_MAT3,location,v,count>0?(size_t)count*9*sizeof(*v):0))
WRAP_VOID(glAlphaFunc,ALPHA_FUNC,(GLenum func,GLclampf ref),(func,ref),{})
WRAP_VOID(glBegin,BEGIN_PRIMITIVE,(GLenum mode),(mode),{})
WRAP_VOID(glClearColor,CLEAR_COLOR,(GLclampf r,GLclampf g,GLclampf b,GLclampf a),(r,g,b,a),{})
WRAP_VOID(glColor4f,COLOR_4F,(GLfloat r,GLfloat g,GLfloat b,GLfloat a),(r,g,b,a),{})
WRAP_VOID(glEnd,END_PRIMITIVE,(void),(),{})
WRAP_VOID(glFrontFace,FRONT_FACE,(GLenum mode),(mode),state_change(e,FRONT_FACE,0,mode,0))
WRAP_VOID(glGenTextures,GEN_TEXTURES,(GLsizei count,GLuint *textures),(count,textures),{})
WRAP_RET(const GLubyte*,glGetString,GET_STRING,(GLenum name),(name),{})
WRAP_VOID(glLineWidth,LINE_WIDTH,(GLfloat width),(width),{})
WRAP_VOID(glLoadIdentity,LOAD_IDENTITY,(void),(),{})
WRAP_VOID(glMatrixMode,MATRIX_MODE,(GLenum mode),(mode),{})
WRAP_VOID(glOrtho,ORTHO,(GLdouble left,GLdouble right,GLdouble bottom,GLdouble top,GLdouble near,GLdouble far),(left,right,bottom,top,near,far),{})
WRAP_VOID(glPolygonMode,POLYGON_MODE,(GLenum face,GLenum mode),(face,mode),state_change(e,POLYGON_MODE,face,mode,0))
WRAP_VOID(glTexEnvf,TEX_ENV_F,(GLenum target,GLenum pname,GLfloat param),(target,pname,param),{})
WRAP_VOID(glVertex2f,VERTEX_2F,(GLfloat x,GLfloat y),(x,y),{})
WRAP_VOID(glActiveTextureARB,ACTIVE_TEXTURE_ARB,(GLenum unit),(unit),{state_change(e,ACTIVE_TEXTURE_ARB,0,unit,1);if(e.t)e.t->active_unit=unit;})
WRAP_VOID(glBindBufferARB,BIND_BUFFER_ARB,(GLenum target,GLuint buffer),(target,buffer),{state_change(e,BIND_BUFFER_ARB,target,buffer,2);if(e.t){if(target==GL_ARRAY_BUFFER)e.t->array_buffer=buffer;if(target==GL_ELEMENT_ARRAY_BUFFER)e.t->element_buffer=buffer;}})
WRAP_VOID(glBindFramebufferEXT,BIND_FBO_EXT,(GLenum target,GLuint fbo),(target,fbo),{if(e.t && (target==GL_FRAMEBUFFER || target==GL_DRAW_FRAMEBUFFER) && e.t->fbo!=fbo){pass_end(e.t);e.t->pass_index++;e.t->fbo=fbo;}state_change(e,BIND_FBO_EXT,target,fbo,0);})
WRAP_VOID(glUseProgramObjectARB,USE_PROGRAM_ARB,(GLhandleARB program),(program),{state_change(e,USE_PROGRAM_ARB,0,(uintptr_t)program,0);if(e.t)e.t->program=(uintptr_t)program;})
WRAP_VOID(glUniform1fARB,UNIFORM_1F_ARB,(GLint location,GLfloat v0),(location,v0),uniform_value(e,UNIFORM_1F_ARB,location,&v0,sizeof(v0)))
WRAP_VOID(glUniform1iARB,UNIFORM_1I_ARB,(GLint location,GLint v0),(location,v0),uniform_value(e,UNIFORM_1I_ARB,location,&v0,sizeof(v0)))
WRAP_VOID(glUniformMatrix4fvARB,UNIFORM_MAT4_ARB,(GLint location,GLsizei count,GLboolean transpose,const GLfloat *v),(location,count,transpose,v),uniform_value(e,UNIFORM_MAT4_ARB,location,v,count>0?(size_t)count*16*sizeof(*v):0))
WRAP_VOID(glVertexAttribPointerARB,ATTRIB_PTR_ARB,(GLuint index,GLint size,GLenum type,GLboolean normalized,GLsizei stride,const GLvoid *ptr),(index,size,type,normalized,stride,ptr),state_change(e,ATTRIB_PTR_ARB,((uint64_t)(e.t?e.t->vertex_array:0)<<32)|index,hash_mix(hash_mix(hash_mix((uint64_t)size,type),((uint64_t)normalized<<32)|(uint32_t)stride),hash_mix((uintptr_t)ptr,e.t?e.t->array_buffer:0)),2))
WRAP_VOID(glBindVertexArrayAPPLE,BIND_VERTEX_ARRAY_APPLE,(GLuint array),(array),{state_change(e,BIND_VERTEX_ARRAY_APPLE,0,array,2);if(e.t)e.t->vertex_array=array;})
WRAP_VOID(glEnableVertexAttribArrayARB,ATTRIB_ENABLE_ARB,(GLuint index),(index),state_change(e,ATTRIB_ENABLE_ARB,((uint64_t)(e.t?e.t->vertex_array:0)<<32)|index,1,2))
WRAP_VOID(glDisableVertexAttribArrayARB,ATTRIB_DISABLE_ARB,(GLuint index),(index),state_change(e,ATTRIB_DISABLE_ARB,((uint64_t)(e.t?e.t->vertex_array:0)<<32)|index,0,2))
WRAP_VOID(glBufferDataARB,BUFFER_DATA_ARB,(GLenum target,GLsizeiptrARB size,const GLvoid *data,GLenum usage),(target,size,data,usage),{if(e.t)e.t->buffer_epoch++;})
WRAP_VOID(glBufferSubDataARB,BUFFER_SUBDATA_ARB,(GLenum target,GLintptrARB offset,GLsizeiptrARB size,const GLvoid *data),(target,offset,size,data),{if(e.t)e.t->buffer_epoch++;})
WRAP_VOID(glFramebufferTexture2DEXT,FRAMEBUFFER_TEXTURE_EXT,(GLenum target,GLenum attachment,GLenum textarget,GLuint texture,GLint level),(target,attachment,textarget,texture,level),{if(e.t)e.t->pass_hash=hash_mix(e.t->pass_hash,((uint64_t)attachment<<32)|texture);})
WRAP_VOID(glGenerateMipmapEXT,GENERATE_MIPMAP_EXT,(GLenum target),(target),{if(e.t)e.t->texture_epoch++;})
WRAP_VOID(glUniform1fvARB,UNIFORM_1FV_ARB,(GLint location,GLsizei count,const GLfloat *v),(location,count,v),uniform_value(e,UNIFORM_1FV_ARB,location,v,count>0?(size_t)count*sizeof(*v):0))
WRAP_VOID(glUniform2fvARB,UNIFORM_2FV_ARB,(GLint location,GLsizei count,const GLfloat *v),(location,count,v),uniform_value(e,UNIFORM_2FV_ARB,location,v,count>0?(size_t)count*2*sizeof(*v):0))
WRAP_VOID(glUniform3fvARB,UNIFORM_3FV_ARB,(GLint location,GLsizei count,const GLfloat *v),(location,count,v),uniform_value(e,UNIFORM_3FV_ARB,location,v,count>0?(size_t)count*3*sizeof(*v):0))
WRAP_VOID(glUniform4fvARB,UNIFORM_4FV_ARB,(GLint location,GLsizei count,const GLfloat *v),(location,count,v),uniform_value(e,UNIFORM_4FV_ARB,location,v,count>0?(size_t)count*4*sizeof(*v):0))
WRAP_VOID(glUniformMatrix2fvARB,UNIFORM_MAT2_ARB,(GLint location,GLsizei count,GLboolean transpose,const GLfloat *v),(location,count,transpose,v),uniform_value(e,UNIFORM_MAT2_ARB,location,v,count>0?(size_t)count*4*sizeof(*v):0))
WRAP_VOID(glUniformMatrix3fvARB,UNIFORM_MAT3_ARB,(GLint location,GLsizei count,GLboolean transpose,const GLfloat *v),(location,count,transpose,v),uniform_value(e,UNIFORM_MAT3_ARB,location,v,count>0?(size_t)count*9*sizeof(*v):0))
static CGLError tracked_CGLFlushDrawable(CGLContextObj context) {
    Event e=begin_event(SWAP,CALLER);
    CGLError result=CGLFlushDrawable(context);
    end_event(e,SWAP);
    if (e.t) { pass_end(e.t); e.t->pass_index=0; e.t->have_prev_draw=0; atomic_fetch_add(&frames,1); }
    snapshot(now_ns());
    return result;
}
static int tracked_nanosleep(const struct timespec *req,struct timespec *rem) {
    if (control_mode && *control_mode==3) return nanosleep(req,rem);
    Event e=begin_event(NANO_SLEEP,CALLER); int result=nanosleep(req,rem);
    int saved_errno=errno;
    if(e.sample) wait_record(0,req?(uint64_t)req->tv_sec*1000000000ull+req->tv_nsec:UINT64_MAX,now_ns()-e.started); end_event(e,NANO_SLEEP); errno=saved_errno; return result;
}
static int tracked_usleep(useconds_t usec) {
    if (control_mode && *control_mode==3) return usleep(usec);
    Event e=begin_event(U_SLEEP,CALLER); int result=usleep(usec);
    int saved_errno=errno;
    if(e.sample) wait_record(1,(uint64_t)usec*1000ull,now_ns()-e.started); end_event(e,U_SLEEP); errno=saved_errno; return result;
}
static kern_return_t tracked_mach_wait_until(uint64_t deadline) {
    if (control_mode && *control_mode==3) return mach_wait_until(deadline);
    uint64_t before=mach_absolute_time();
    pthread_once(&timebase_once,setup_timebase);
    uint64_t requested=deadline>before && mach_timebase.denom?
        (deadline-before)*mach_timebase.numer/mach_timebase.denom:0;
    Event e=begin_event(MACH_WAIT,CALLER); kern_return_t result=mach_wait_until(deadline);
    if(e.sample) wait_record(2,requested,now_ns()-e.started); end_event(e,MACH_WAIT); return result;
}
static int tracked_pthread_cond_timedwait(pthread_cond_t *cond,pthread_mutex_t *mutex,const struct timespec *deadline) {
    if (control_mode && *control_mode==3) return pthread_cond_timedwait(cond,mutex,deadline);
    Event e=begin_event(COND_WAIT,CALLER); int result=pthread_cond_timedwait(cond,mutex,deadline);
    if(e.sample) wait_record(3,UINT64_MAX,now_ns()-e.started); end_event(e,COND_WAIT); return result;
}
static int tracked_poll(struct pollfd *fds,nfds_t n,int timeout) {
    if (control_mode && *control_mode==3) return poll(fds,n,timeout);
    Event e=begin_event(POLL_WAIT,CALLER); int result=poll(fds,n,timeout);
    int saved_errno=errno;
    if(e.sample) wait_record(4,timeout>=0?(uint64_t)timeout*1000000ull:UINT64_MAX,now_ns()-e.started); end_event(e,POLL_WAIT); errno=saved_errno; return result;
}
static int tracked_select(int n,fd_set *r,fd_set *w,fd_set *x,struct timeval *timeout) {
    if (control_mode && *control_mode==3) return select(n,r,w,x,timeout);
    uint64_t requested=timeout?(uint64_t)timeout->tv_sec*1000000000ull+
        (uint64_t)timeout->tv_usec*1000ull:UINT64_MAX;
    Event e=begin_event(SELECT_WAIT,CALLER); int result=select(n,r,w,x,timeout);
    int saved_errno=errno;
    if(e.sample) wait_record(5,requested,now_ns()-e.started); end_event(e,SELECT_WAIT); errno=saved_errno; return result;
}
#define ENTRY(name,id) if (strcmp(symbol,#name)==0) { atomic_fetch_add(&resolved[id],1); return (void*)tracked_##name; }
static void *tracked_dlsym(void *handle,const char *symbol) {
    void *original=dlsym(handle,symbol);
    if (!original || !symbol) return original;
    if (symbol[0]=='g' && symbol[1]=='l' && symbol[2]>='A' && symbol[2]<='Z') {
        ENTRY(glDrawElements,DRAW_ELEMENTS) ENTRY(glDrawElementsBaseVertex,DRAW_BASE)
        ENTRY(glDrawArrays,DRAW_ARRAYS) ENTRY(glBindFramebuffer,BIND_FBO)
        ENTRY(glClear,CLEAR) ENTRY(glViewport,VIEWPORT) ENTRY(glUseProgram,USE_PROGRAM)
        ENTRY(glActiveTexture,ACTIVE_TEXTURE) ENTRY(glBindTexture,BIND_TEXTURE)
        ENTRY(glBindBuffer,BIND_BUFFER) ENTRY(glEnable,ENABLE) ENTRY(glDisable,DISABLE)
        ENTRY(glBlendFunc,BLEND_FUNC) ENTRY(glBlendFuncSeparate,BLEND_SEPARATE)
        ENTRY(glDepthFunc,DEPTH_FUNC) ENTRY(glDepthMask,DEPTH_MASK)
        ENTRY(glCullFace,CULL_FACE) ENTRY(glColorMask,COLOR_MASK)
        ENTRY(glVertexAttribPointer,ATTRIB_PTR) ENTRY(glEnableVertexAttribArray,ATTRIB_ENABLE)
        ENTRY(glDisableVertexAttribArray,ATTRIB_DISABLE) ENTRY(glUniform1i,UNIFORM_1I)
        ENTRY(glUniform1f,UNIFORM_1F) ENTRY(glUniform2f,UNIFORM_2F)
        ENTRY(glUniform3f,UNIFORM_3F) ENTRY(glUniform4f,UNIFORM_4F)
        ENTRY(glUniform1iv,UNIFORM_1IV) ENTRY(glUniform1fv,UNIFORM_1FV)
        ENTRY(glUniform2fv,UNIFORM_2FV) ENTRY(glUniform3fv,UNIFORM_3FV)
        ENTRY(glUniform4fv,UNIFORM_4FV) ENTRY(glUniformMatrix4fv,UNIFORM_MAT4)
        ENTRY(glBufferData,BUFFER_DATA) ENTRY(glBufferSubData,BUFFER_SUBDATA)
        ENTRY(glTexImage2D,TEX_IMAGE) ENTRY(glTexSubImage2D,TEX_SUBIMAGE)
        ENTRY(glFinish,FINISH) ENTRY(glFlush,GL_FLUSH) ENTRY(glGetError,GET_ERROR)
        ENTRY(glGetIntegerv,GET_INTEGER) ENTRY(glGetBooleanv,GET_BOOLEAN)
        ENTRY(glGetFloatv,GET_FLOAT) ENTRY(glReadPixels,READ_PIXELS)
        ENTRY(glCopyTexSubImage2D,COPY_TEX)
        ENTRY(glDrawArraysInstanced,DRAW_ARRAYS_INSTANCED)
        ENTRY(glDrawElementsInstanced,DRAW_ELEMENTS_INSTANCED)
        ENTRY(glBindVertexArray,BIND_VERTEX_ARRAY) ENTRY(glScissor,SCISSOR)
        ENTRY(glStencilFunc,STENCIL_FUNC) ENTRY(glStencilMask,STENCIL_MASK)
        ENTRY(glStencilOp,STENCIL_OP) ENTRY(glTexParameteri,TEX_PARAMETER_I)
        ENTRY(glTexParameterf,TEX_PARAMETER_F) ENTRY(glPixelStorei,PIXEL_STORE)
        ENTRY(glGetTexImage,GET_TEX_IMAGE) ENTRY(glTexImage3D,TEX_IMAGE_3D)
        ENTRY(glTexSubImage3D,TEX_SUBIMAGE_3D)
        ENTRY(glDeleteTextures,DELETE_TEXTURES) ENTRY(glDeleteBuffers,DELETE_BUFFERS)
        ENTRY(glMapBuffer,MAP_BUFFER) ENTRY(glUnmapBuffer,UNMAP_BUFFER)
        ENTRY(glDeleteProgram,DELETE_PROGRAM) ENTRY(glLinkProgram,LINK_PROGRAM)
        ENTRY(glBindRenderbuffer,BIND_RENDERBUFFER)
        ENTRY(glDeleteFramebuffers,DELETE_FRAMEBUFFERS)
        ENTRY(glFramebufferTexture2D,FRAMEBUFFER_TEXTURE)
        ENTRY(glFramebufferRenderbuffer,FRAMEBUFFER_RENDERBUFFER)
        ENTRY(glGenerateMipmap,GENERATE_MIPMAP) ENTRY(glBlitFramebuffer,BLIT_FRAMEBUFFER)
        ENTRY(glBindSampler,BIND_SAMPLER) ENTRY(glUniform2i,UNIFORM_2I)
        ENTRY(glUniform3i,UNIFORM_3I) ENTRY(glUniform4i,UNIFORM_4I)
        ENTRY(glUniform2iv,UNIFORM_2IV) ENTRY(glUniform3iv,UNIFORM_3IV)
        ENTRY(glUniform4iv,UNIFORM_4IV) ENTRY(glUniformMatrix2fv,UNIFORM_MAT2)
        ENTRY(glUniformMatrix3fv,UNIFORM_MAT3)
        ENTRY(glAlphaFunc,ALPHA_FUNC) ENTRY(glBegin,BEGIN_PRIMITIVE)
        ENTRY(glClearColor,CLEAR_COLOR) ENTRY(glColor4f,COLOR_4F)
        ENTRY(glEnd,END_PRIMITIVE) ENTRY(glFrontFace,FRONT_FACE)
        ENTRY(glGenTextures,GEN_TEXTURES) ENTRY(glGetString,GET_STRING)
        ENTRY(glLineWidth,LINE_WIDTH) ENTRY(glLoadIdentity,LOAD_IDENTITY)
        ENTRY(glMatrixMode,MATRIX_MODE) ENTRY(glOrtho,ORTHO)
        ENTRY(glPolygonMode,POLYGON_MODE) ENTRY(glTexEnvf,TEX_ENV_F)
        ENTRY(glVertex2f,VERTEX_2F)
        ENTRY(glActiveTextureARB,ACTIVE_TEXTURE_ARB)
        ENTRY(glBindBufferARB,BIND_BUFFER_ARB)
        ENTRY(glBindFramebufferEXT,BIND_FBO_EXT)
        ENTRY(glUseProgramObjectARB,USE_PROGRAM_ARB)
        ENTRY(glUniform1fARB,UNIFORM_1F_ARB)
        ENTRY(glUniform1iARB,UNIFORM_1I_ARB)
        ENTRY(glUniformMatrix4fvARB,UNIFORM_MAT4_ARB)
        ENTRY(glVertexAttribPointerARB,ATTRIB_PTR_ARB)
        ENTRY(glBindVertexArrayAPPLE,BIND_VERTEX_ARRAY_APPLE)
        ENTRY(glEnableVertexAttribArrayARB,ATTRIB_ENABLE_ARB)
        ENTRY(glDisableVertexAttribArrayARB,ATTRIB_DISABLE_ARB)
        ENTRY(glBufferDataARB,BUFFER_DATA_ARB)
        ENTRY(glBufferSubDataARB,BUFFER_SUBDATA_ARB)
        ENTRY(glFramebufferTexture2DEXT,FRAMEBUFFER_TEXTURE_EXT)
        ENTRY(glGenerateMipmapEXT,GENERATE_MIPMAP_EXT)
        ENTRY(glUniform1fvARB,UNIFORM_1FV_ARB)
        ENTRY(glUniform2fvARB,UNIFORM_2FV_ARB)
        ENTRY(glUniform3fvARB,UNIFORM_3FV_ARB)
        ENTRY(glUniform4fvARB,UNIFORM_4FV_ARB)
        ENTRY(glUniformMatrix2fvARB,UNIFORM_MAT2_ARB)
        ENTRY(glUniformMatrix3fvARB,UNIFORM_MAT3_ARB)
        atomic_fetch_add(&unknown_gl_lookups,1);
        pthread_mutex_lock(&unknown_lock);
        int i;
        for (i=0;i<unknown_count;i++) if (strcmp(unknown_names[i].name,symbol)==0) break;
        if (i==unknown_count && unknown_count<512) {
            snprintf(unknown_names[i].name,sizeof(unknown_names[i].name),"%s",symbol);
            unknown_count++;
        }
        if (i<unknown_count) unknown_names[i].count++;
        else atomic_fetch_add(&unknown_overflow,1);
        pthread_mutex_unlock(&unknown_lock);
    } else atomic_fetch_add(&direct_lookup_count,1);
    return original;
}
#define INTERPOSE(name) { (const void*)tracked_##name,(const void*)name },
__attribute__((used,section("__DATA,__interpose")))
static const struct {const void *replacement,*replacee;} interposes[] = {
    INTERPOSE(dlsym) INTERPOSE(CGLFlushDrawable) INTERPOSE(glDrawElements)
    INTERPOSE(glDrawElementsBaseVertex) INTERPOSE(glDrawArrays) INTERPOSE(glBindFramebuffer)
    INTERPOSE(glClear) INTERPOSE(glViewport) INTERPOSE(glUseProgram)
    INTERPOSE(glActiveTexture) INTERPOSE(glBindTexture) INTERPOSE(glBindBuffer)
    INTERPOSE(glEnable) INTERPOSE(glDisable) INTERPOSE(glBlendFunc)
    INTERPOSE(glBlendFuncSeparate) INTERPOSE(glDepthFunc) INTERPOSE(glDepthMask)
    INTERPOSE(glCullFace) INTERPOSE(glColorMask) INTERPOSE(glVertexAttribPointer)
    INTERPOSE(glEnableVertexAttribArray) INTERPOSE(glDisableVertexAttribArray)
    INTERPOSE(glUniform1i) INTERPOSE(glUniform1f) INTERPOSE(glUniform2f)
    INTERPOSE(glUniform3f) INTERPOSE(glUniform4f) INTERPOSE(glUniform1iv)
    INTERPOSE(glUniform1fv) INTERPOSE(glUniform2fv) INTERPOSE(glUniform3fv)
    INTERPOSE(glUniform4fv) INTERPOSE(glUniformMatrix4fv) INTERPOSE(glBufferData)
    INTERPOSE(glBufferSubData) INTERPOSE(glTexImage2D) INTERPOSE(glTexSubImage2D)
    INTERPOSE(glFinish) INTERPOSE(glFlush) INTERPOSE(glGetError)
    INTERPOSE(glGetIntegerv) INTERPOSE(glGetBooleanv) INTERPOSE(glGetFloatv)
    INTERPOSE(glReadPixels) INTERPOSE(glCopyTexSubImage2D) INTERPOSE(nanosleep)
    INTERPOSE(glDrawArraysInstanced) INTERPOSE(glDrawElementsInstanced)
    INTERPOSE(glBindVertexArray) INTERPOSE(glScissor) INTERPOSE(glStencilFunc)
    INTERPOSE(glStencilMask) INTERPOSE(glStencilOp) INTERPOSE(glTexParameteri)
    INTERPOSE(glTexParameterf) INTERPOSE(glPixelStorei) INTERPOSE(glGetTexImage)
    INTERPOSE(glTexImage3D) INTERPOSE(glTexSubImage3D) INTERPOSE(glDeleteTextures)
    INTERPOSE(glDeleteBuffers) INTERPOSE(glMapBuffer) INTERPOSE(glUnmapBuffer)
    INTERPOSE(glDeleteProgram) INTERPOSE(glLinkProgram) INTERPOSE(glBindRenderbuffer)
    INTERPOSE(glDeleteFramebuffers) INTERPOSE(glFramebufferTexture2D)
    INTERPOSE(glFramebufferRenderbuffer) INTERPOSE(glGenerateMipmap)
    INTERPOSE(glBlitFramebuffer) INTERPOSE(glBindSampler)
    INTERPOSE(glUniform2i) INTERPOSE(glUniform3i) INTERPOSE(glUniform4i)
    INTERPOSE(glUniform2iv) INTERPOSE(glUniform3iv) INTERPOSE(glUniform4iv)
    INTERPOSE(glUniformMatrix2fv) INTERPOSE(glUniformMatrix3fv)
    INTERPOSE(glAlphaFunc) INTERPOSE(glBegin) INTERPOSE(glClearColor)
    INTERPOSE(glColor4f) INTERPOSE(glEnd) INTERPOSE(glFrontFace)
    INTERPOSE(glGenTextures) INTERPOSE(glGetString) INTERPOSE(glLineWidth)
    INTERPOSE(glLoadIdentity) INTERPOSE(glMatrixMode) INTERPOSE(glOrtho)
    INTERPOSE(glPolygonMode) INTERPOSE(glTexEnvf) INTERPOSE(glVertex2f)
    INTERPOSE(glActiveTextureARB) INTERPOSE(glBindBufferARB)
    INTERPOSE(glBindFramebufferEXT) INTERPOSE(glUseProgramObjectARB)
    INTERPOSE(glUniform1fARB) INTERPOSE(glUniform1iARB)
    INTERPOSE(glUniformMatrix4fvARB) INTERPOSE(glVertexAttribPointerARB)
    INTERPOSE(glBindVertexArrayAPPLE) INTERPOSE(glEnableVertexAttribArrayARB)
    INTERPOSE(glDisableVertexAttribArrayARB) INTERPOSE(glBufferDataARB)
    INTERPOSE(glBufferSubDataARB) INTERPOSE(glFramebufferTexture2DEXT)
    INTERPOSE(glGenerateMipmapEXT) INTERPOSE(glUniform1fvARB)
    INTERPOSE(glUniform2fvARB) INTERPOSE(glUniform3fvARB)
    INTERPOSE(glUniform4fvARB) INTERPOSE(glUniformMatrix2fvARB)
    INTERPOSE(glUniformMatrix3fvARB)
    INTERPOSE(usleep) INTERPOSE(mach_wait_until) INTERPOSE(pthread_cond_timedwait)
    INTERPOSE(poll) INTERPOSE(select)
};
