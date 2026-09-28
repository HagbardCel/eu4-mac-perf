// Experimental, version-pinned GL state cache for the x86_64 GOG EU IV.
// A zero/unknown state always forwards. No GL calls are made by the cache.
#define GL_SILENCE_DEPRECATION 1
#include <OpenGL/OpenGL.h>
#include <OpenGL/gl.h>
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-W#warnings"
#include <OpenGL/gl3.h>
#pragma clang diagnostic pop
#include <dlfcn.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

enum { TEXTURE, VERTEX, UNIFORM, FAMILIES };
enum { ACTIVE=1, BIND_TEX, TEX_ENV, TEX_PARAM, BIND_BUFFER, ATTRIB_ENABLE,
       ATTRIB_POINTER, ATTRIB_DIVISOR, PROGRAM, UNIFORM_VALUE };
typedef struct { uint32_t kind,a,b,c; uint64_t value[6]; unsigned valid; } Slot;
typedef struct {
    CGLContextObj context;
    Slot slots[4096];
    uint32_t active, array_buffer, vao;
    uintptr_t program;
    unsigned active_known, program_known, array_known, vao_known, vao_checked;
    unsigned last_mode;
    unsigned generation;
    uint64_t forwarded[FAMILIES], suppressed[FAMILIES], swaps, bucket_start, overflow;
} Shadow;
static _Thread_local Shadow shadow;
static _Thread_local uint64_t local_tid;
static volatile unsigned char *control;
static int log_fd = -1;
static _Atomic uint64_t render_thread;
static _Atomic unsigned global_generation;
static _Atomic uint64_t context_switches;
static _Atomic uint64_t other_thread_calls;

static uint64_t clock_ns(clockid_t id) {
    struct timespec t;
    return clock_gettime(id,&t) ? 0 : (uint64_t)t.tv_sec*1000000000ull+t.tv_nsec;
}
static void setup(void) {
    const char *path=getenv("EU4_CACHE_LOG");
    if (path && path[0]=='/') log_fd=open(path,O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC,0600);
    path=getenv("EU4_CACHE_CONTROL");
    if (path && path[0]=='/') {
        int fd=open(path,O_RDONLY);
        if (fd>=0) {
            void *p=mmap(NULL,4096,PROT_READ,MAP_SHARED,fd,0);
            if (p!=MAP_FAILED) control=p;
            close(fd);
        }
    }
}
__attribute__((constructor)) static void initialize(void) { setup(); }
static unsigned mode(void) {
    unsigned m=control ? *control : 0;
    return m<=3 && log_fd>=0 ? m : 0;
}
static void reset_state(Shadow *s) {
    memset(s->slots,0,sizeof(s->slots));
    s->active=s->program=s->array_buffer=s->vao=0;
    s->active_known=s->program_known=s->array_known=s->vao_known=s->vao_checked=0;
}
static void sync_mode(Shadow *s,unsigned m) {
    if (m==s->last_mode) return;
    reset_state(s); s->last_mode=m;
    memset(s->forwarded,0,sizeof(s->forwarded));
    memset(s->suppressed,0,sizeof(s->suppressed));
    s->bucket_start=0; s->swaps=0;
    s->overflow=0;
}
static unsigned allowed(unsigned family, Shadow **out) {
    unsigned m=mode();
    Shadow *s=&shadow;
    sync_mode(s,m);
    if (m<family+1) return 0;
    if (!local_tid) pthread_threadid_np(NULL,&local_tid);
    uint64_t owner=atomic_load(&render_thread);
    if (!owner || owner!=local_tid) {
        atomic_fetch_add(&other_thread_calls,1);
        atomic_fetch_add(&global_generation,1);
        return 0;
    }
    CGLContextObj context=CGLGetCurrentContext();
    if (!context) return 0;
    unsigned generation=atomic_load(&global_generation);
    if (s->generation!=generation) {s->generation=generation;reset_state(s);}
    if (s->context!=context) {
        if (s->context) atomic_fetch_add(&context_switches,1);
        s->context=context; reset_state(s);
    }
    if (family==VERTEX && !s->vao_checked) {
        s->vao_checked=1;
        const char *extensions=(const char *)glGetString(GL_EXTENSIONS);
        if (extensions && strstr(extensions,"GL_APPLE_vertex_array_object")) {
            GLint current=0;
            glGetIntegerv(GL_VERTEX_ARRAY_BINDING_APPLE,&current);
            s->vao=(uint32_t)current; s->vao_known=1;
        }
    }
    *out=s;
    return 1;
}
static Slot *slot(Shadow *s,uint32_t kind,uint32_t a,uint32_t b,uint32_t c) {
    uint32_t h=kind*2654435761u ^ a*2246822519u ^ b*3266489917u ^ c*668265263u;
    for (unsigned i=0;i<4096;i++) {
        Slot *p=&s->slots[(h+i)&4095];
        if (!p->valid || (p->kind==kind && p->a==a && p->b==b && p->c==c)) return p;
    }
    s->overflow++;
    reset_state(s); // Overflow: forget everything and forward the current call.
    return NULL;
}
static int duplicate(Shadow *s,uint32_t kind,uint32_t a,uint32_t b,uint32_t c,
                     const uint64_t *value,unsigned count) {
    Slot *p=slot(s,kind,a,b,c);
    if (!p) return 0;
    if (p->valid && !memcmp(p->value,value,count*sizeof(uint64_t))) return 1;
    p->kind=kind; p->a=a; p->b=b; p->c=c; p->valid=1;
    memcpy(p->value,value,count*sizeof(uint64_t));
    return 0;
}
static void invalidate_uniform(Shadow *s,GLint location,GLsizei count) {
    if (!s->program_known || !s->program || location<0 || count<=0) return;
    // Array elements occupy successive uniform locations. Fall back to a full
    // reset for an unusually large range rather than walking it per call.
    if (count>64 || location>INT32_MAX-count) {reset_state(s);return;}
    for (GLsizei i=0;i<count;i++) {
        Slot *p=slot(s,UNIFORM_VALUE,(uint32_t)s->program,
                     (uint32_t)(location+i),(uint32_t)(s->program>>32));
        if (p) p->valid=0;
    }
}
static void count(Shadow *s,unsigned family,int skipped) {
    if (skipped) s->suppressed[family]++; else s->forwarded[family]++;
}
static void snapshot(Shadow *s,unsigned m) {
    if (log_fd<0) return;
    uint64_t now=clock_ns(CLOCK_UPTIME_RAW);
    if (!s->bucket_start) { s->bucket_start=now; return; }
    if (now-s->bucket_start<1000000000ull) return;
    char line[330];
    int n=snprintf(line,sizeof(line),"S,%llu,%llu,%u,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%u,%llu,%llu,%llu\n",
        (unsigned long long)now,(unsigned long long)clock_ns(CLOCK_REALTIME),m,
        (unsigned long long)(now-s->bucket_start),(unsigned long long)s->swaps,
        (unsigned long long)s->forwarded[TEXTURE],(unsigned long long)s->suppressed[TEXTURE],
        (unsigned long long)s->forwarded[VERTEX],(unsigned long long)s->suppressed[VERTEX],
        (unsigned long long)s->forwarded[UNIFORM],(unsigned long long)s->suppressed[UNIFORM],
        0u,(unsigned long long)s->overflow,
        (unsigned long long)atomic_exchange(&context_switches,0),
        (unsigned long long)atomic_exchange(&other_thread_calls,0));
    if (n>0 && (size_t)n<sizeof(line)) (void)write(log_fd,line,(size_t)n);
    s->bucket_start=now; s->swaps=0;
    memset(s->forwarded,0,sizeof(s->forwarded));
    memset(s->suppressed,0,sizeof(s->suppressed));
    s->overflow=0;
}
static CGLError cached_CGLFlushDrawable(CGLContextObj ctx) {
    CGLError result=CGLFlushDrawable(ctx);
    if (!local_tid) pthread_threadid_np(NULL,&local_tid);
    uint64_t empty=0;
    (void)atomic_compare_exchange_strong(&render_thread,&empty,local_tid);
    unsigned m=mode();
    Shadow *s=&shadow;
    sync_mode(s,m);
    s->swaps++;
    snapshot(s,m);
    // External overlays commonly touch GL state around a swap. Do not carry
    // assumptions about their unobserved calls into the next EU IV frame.
    reset_state(s);
    return result;
}
static CGLError cached_CGLSetCurrentContext(CGLContextObj ctx) {
    CGLError result=CGLSetCurrentContext(ctx);
    if (result==kCGLNoError) {
        if (shadow.context && shadow.context!=ctx) atomic_fetch_add(&context_switches,1);
        shadow.context=ctx; reset_state(&shadow);
    }
    return result;
}

static void cached_glActiveTextureARB(GLenum unit) {
    Shadow *s=NULL; int skip=0;
    if (allowed(TEXTURE,&s) && unit>=GL_TEXTURE0 && unit<GL_TEXTURE0+16) {
        uint64_t v=unit; skip=duplicate(s,ACTIVE,0,0,0,&v,1);
        s->active=unit-GL_TEXTURE0; s->active_known=1; count(s,TEXTURE,skip);
    }
    if (!skip) glActiveTextureARB(unit);
}
static void cached_glActiveTexture(GLenum unit) {
    Shadow *s=NULL; int skip=0;
    if (allowed(TEXTURE,&s) && unit>=GL_TEXTURE0 && unit<GL_TEXTURE0+16) {
        uint64_t v=unit; skip=duplicate(s,ACTIVE,0,0,0,&v,1);
        s->active=unit-GL_TEXTURE0; s->active_known=1; count(s,TEXTURE,skip);
    }
    if (!skip) glActiveTexture(unit);
}
static void cached_glBindTexture(GLenum target,GLuint texture) {
    if (!control || *control==0) {glBindTexture(target,texture);return;}
    Shadow *s=NULL; int skip=0;
    if (allowed(TEXTURE,&s) && s->active_known && (target==GL_TEXTURE_2D || target==GL_TEXTURE_CUBE_MAP)) {
        uint64_t v=texture; skip=duplicate(s,BIND_TEX,s->active,target,0,&v,1);
        count(s,TEXTURE,skip);
    }
    if (!skip) glBindTexture(target,texture);
}
static void cached_glTexEnvf(GLenum target,GLenum pname,GLfloat value) {
    if (!control || *control==0) {glTexEnvf(target,pname,value);return;}
    Shadow *s=NULL; int skip=0;
    if (allowed(TEXTURE,&s) && s->active_known && target==GL_TEXTURE_ENV && pname==GL_TEXTURE_ENV_MODE) {
        uint32_t bits; memcpy(&bits,&value,sizeof(bits)); uint64_t v=bits;
        skip=duplicate(s,TEX_ENV,s->active,target,pname,&v,1); count(s,TEXTURE,skip);
    }
    if (!skip) glTexEnvf(target,pname,value);
}
static GLuint bound_texture(Shadow *s,GLenum target) {
    Slot *p=slot(s,BIND_TEX,s->active,target,0);
    return p && p->valid ? (GLuint)p->value[0] : 0;
}
static void cached_glTexParameteri(GLenum target,GLenum pname,GLint value) {
    if (!control || *control==0) {glTexParameteri(target,pname,value);return;}
    Shadow *s=NULL; int skip=0;
    if (allowed(TEXTURE,&s) && s->active_known && (target==GL_TEXTURE_2D || target==GL_TEXTURE_CUBE_MAP)) {
        GLuint texture=bound_texture(s,target);
        if (texture) { uint64_t v[2]={1,(uint32_t)value};
            skip=duplicate(s,TEX_PARAM,texture,target,pname,v,2); count(s,TEXTURE,skip); }
    }
    if (!skip) glTexParameteri(target,pname,value);
}
static void cached_glTexParameterf(GLenum target,GLenum pname,GLfloat value) {
    if (!control || *control==0) {glTexParameterf(target,pname,value);return;}
    Shadow *s=NULL; int skip=0;
    if (allowed(TEXTURE,&s) && s->active_known && (target==GL_TEXTURE_2D || target==GL_TEXTURE_CUBE_MAP)) {
        GLuint texture=bound_texture(s,target);
        if (texture) { uint32_t bits; memcpy(&bits,&value,sizeof(bits)); uint64_t v[2]={2,bits};
            skip=duplicate(s,TEX_PARAM,texture,target,pname,v,2); count(s,TEXTURE,skip); }
    }
    if (!skip) glTexParameterf(target,pname,value);
}
static void cached_glDeleteTextures(GLsizei n,const GLuint *textures) {
    glDeleteTextures(n,textures); atomic_fetch_add(&global_generation,1); reset_state(&shadow);
}
static void cached_glTexParameterfv(GLenum target,GLenum pname,const GLfloat *v) {
    glTexParameterfv(target,pname,v); atomic_fetch_add(&global_generation,1); reset_state(&shadow);
}
static void cached_glTexParameteriv(GLenum target,GLenum pname,const GLint *v) {
    glTexParameteriv(target,pname,v); atomic_fetch_add(&global_generation,1); reset_state(&shadow);
}
static void cached_glTexEnvi(GLenum target,GLenum pname,GLint v) {
    glTexEnvi(target,pname,v); atomic_fetch_add(&global_generation,1); reset_state(&shadow);
}
static void cached_glTexEnvfv(GLenum target,GLenum pname,const GLfloat *v) {
    glTexEnvfv(target,pname,v); atomic_fetch_add(&global_generation,1); reset_state(&shadow);
}

static void cached_glBindBufferARB(GLenum target,GLuint buffer) {
    Shadow *s=NULL; int skip=0;
    if (allowed(VERTEX,&s) && (target==GL_ARRAY_BUFFER || target==GL_ELEMENT_ARRAY_BUFFER)) {
        uint64_t v=buffer; skip=duplicate(s,BIND_BUFFER,target,0,0,&v,1);
        if (target==GL_ARRAY_BUFFER) {s->array_buffer=buffer;s->array_known=1;}
        count(s,VERTEX,skip);
    }
    if (!skip) glBindBufferARB(target,buffer);
}
static void cached_glBindBuffer(GLenum target,GLuint buffer) {
    Shadow *s=NULL; int skip=0;
    if (allowed(VERTEX,&s) && (target==GL_ARRAY_BUFFER || target==GL_ELEMENT_ARRAY_BUFFER)) {
        uint64_t v=buffer; skip=duplicate(s,BIND_BUFFER,target,0,0,&v,1);
        if (target==GL_ARRAY_BUFFER) {s->array_buffer=buffer;s->array_known=1;}
        count(s,VERTEX,skip);
    }
    if (!skip) glBindBuffer(target,buffer);
}
static void cached_glBindVertexArrayAPPLE(GLuint vao) {
    glBindVertexArrayAPPLE(vao);
    shadow.vao=vao; shadow.vao_known=1;
    // VAO binding changes array/element bindings and attribute state.
    reset_state(&shadow); shadow.vao=vao; shadow.vao_known=shadow.vao_checked=1;
}
static void cached_glBindVertexArray(GLuint vao) {
    glBindVertexArray(vao); reset_state(&shadow); shadow.vao=vao; shadow.vao_known=shadow.vao_checked=1;
}
static void cached_glDeleteBuffersARB(GLsizei n,const GLuint *buffers) {
    glDeleteBuffersARB(n,buffers); atomic_fetch_add(&global_generation,1); reset_state(&shadow);
}
static void cached_glDeleteBuffers(GLsizei n,const GLuint *buffers) {
    glDeleteBuffers(n,buffers); atomic_fetch_add(&global_generation,1); reset_state(&shadow);
}
static void cached_glEnableVertexAttribArrayARB(GLuint index) {
    Shadow *s=NULL; int skip=0;
    if (allowed(VERTEX,&s) && s->vao_known && index<32) {
        uint64_t v=1; skip=duplicate(s,ATTRIB_ENABLE,s->vao,index,0,&v,1); count(s,VERTEX,skip);
    }
    if (!skip) glEnableVertexAttribArrayARB(index);
}
static void cached_glDisableVertexAttribArrayARB(GLuint index) {
    Shadow *s=NULL; int skip=0;
    if (allowed(VERTEX,&s) && s->vao_known && index<32) {
        uint64_t v=0; skip=duplicate(s,ATTRIB_ENABLE,s->vao,index,0,&v,1); count(s,VERTEX,skip);
    }
    if (!skip) glDisableVertexAttribArrayARB(index);
}
static void cached_glEnableVertexAttribArray(GLuint i) {
    Shadow *s=NULL; int skip=0;
    if (allowed(VERTEX,&s) && s->vao_known && i<32) {
        uint64_t v=1; skip=duplicate(s,ATTRIB_ENABLE,s->vao,i,0,&v,1); count(s,VERTEX,skip);
    }
    if (!skip) glEnableVertexAttribArray(i);
}
static void cached_glDisableVertexAttribArray(GLuint i) {
    Shadow *s=NULL; int skip=0;
    if (allowed(VERTEX,&s) && s->vao_known && i<32) {
        uint64_t v=0; skip=duplicate(s,ATTRIB_ENABLE,s->vao,i,0,&v,1); count(s,VERTEX,skip);
    }
    if (!skip) glDisableVertexAttribArray(i);
}
static void cached_glVertexAttribPointerARB(GLuint i,GLint size,GLenum type,GLboolean normalized,GLsizei stride,const GLvoid *pointer) {
    Shadow *s=NULL; int skip=0;
    if (allowed(VERTEX,&s) && s->vao_known && s->array_known && i<32 && s->array_buffer) {
        uint64_t v[6]={(uint32_t)size,type,normalized,(uint32_t)stride,(uintptr_t)pointer,s->array_buffer};
        skip=duplicate(s,ATTRIB_POINTER,s->vao,i,0,v,6); count(s,VERTEX,skip);
    }
    if (!skip) glVertexAttribPointerARB(i,size,type,normalized,stride,pointer);
}
static void cached_glVertexAttribPointer(GLuint i,GLint size,GLenum type,GLboolean normalized,GLsizei stride,const GLvoid *p) {
    Shadow *s=NULL; int skip=0;
    if (allowed(VERTEX,&s) && s->vao_known && s->array_known && i<32 && s->array_buffer) {
        uint64_t v[6]={(uint32_t)size,type,normalized,(uint32_t)stride,(uintptr_t)p,s->array_buffer};
        skip=duplicate(s,ATTRIB_POINTER,s->vao,i,0,v,6); count(s,VERTEX,skip);
    }
    if (!skip) glVertexAttribPointer(i,size,type,normalized,stride,p);
}
static void cached_glVertexAttribDivisorARB(GLuint i,GLuint divisor) {
    Shadow *s=NULL; int skip=0;
    if (allowed(VERTEX,&s) && s->vao_known && i<32) {
        uint64_t v=divisor; skip=duplicate(s,ATTRIB_DIVISOR,s->vao,i,0,&v,1); count(s,VERTEX,skip);
    }
    if (!skip) glVertexAttribDivisorARB(i,divisor);
}

static void cached_glUseProgramObjectARB(GLhandleARB program) {
    Shadow *s=NULL; int skip=0;
    if (allowed(UNIFORM,&s)) { uint64_t v=(uintptr_t)program;
        skip=duplicate(s,PROGRAM,0,0,0,&v,1);
        s->program=(uintptr_t)program; s->program_known=1; count(s,UNIFORM,skip);
    }
    if (!skip) glUseProgramObjectARB(program);
}
static void cached_glUseProgram(GLuint p) {
    Shadow *s=NULL; int skip=0;
    if (allowed(UNIFORM,&s)) {uint64_t v=p;
        skip=duplicate(s,PROGRAM,0,0,0,&v,1);
        s->program=p; s->program_known=1; count(s,UNIFORM,skip);
    }
    if (!skip) glUseProgram(p);
}
static void cached_glUniform1i(GLint location,GLint value) {
    Shadow *s=NULL; int skip=0;
    if (allowed(UNIFORM,&s) && s->program_known && s->program && location>=0) {
        uint64_t v[2]={1,(uint32_t)value};
        skip=duplicate(s,UNIFORM_VALUE,(uint32_t)s->program,(uint32_t)location,(uint32_t)(s->program>>32),v,2); count(s,UNIFORM,skip);
    }
    if (!skip) glUniform1i(location,value);
}
static void cached_glUniform1iARB(GLint location,GLint value) {
    Shadow *s=NULL; int skip=0;
    if (allowed(UNIFORM,&s) && s->program_known && s->program && location>=0) {
        uint64_t v[2]={1,(uint32_t)value};
        skip=duplicate(s,UNIFORM_VALUE,(uint32_t)s->program,(uint32_t)location,(uint32_t)(s->program>>32),v,2); count(s,UNIFORM,skip);
    }
    if (!skip) glUniform1iARB(location,value);
}
static void cached_glUniform4fvARB(GLint location,GLsizei n,const GLfloat *value) {
    Shadow *s=NULL; int skip=0;
    if (allowed(UNIFORM,&s) && s->program_known && s->program && location>=0 && n==1 && value) {
        uint64_t v[3]={4,0,0}; memcpy(&v[1],value,4*sizeof(GLfloat));
        skip=duplicate(s,UNIFORM_VALUE,(uint32_t)s->program,(uint32_t)location,(uint32_t)(s->program>>32),v,3); count(s,UNIFORM,skip);
    } else if (s) invalidate_uniform(s,location,n);
    if (!skip) glUniform4fvARB(location,n,value);
}
static void cached_glUniform4fv(GLint location,GLsizei n,const GLfloat *value) {
    Shadow *s=NULL; int skip=0;
    if (allowed(UNIFORM,&s) && s->program_known && s->program && location>=0 && n==1 && value) {
        uint64_t v[3]={4,0,0}; memcpy(&v[1],value,4*sizeof(GLfloat));
        skip=duplicate(s,UNIFORM_VALUE,(uint32_t)s->program,(uint32_t)location,(uint32_t)(s->program>>32),v,3); count(s,UNIFORM,skip);
    } else if (s) invalidate_uniform(s,location,n);
    if (!skip) glUniform4fv(location,n,value);
}
// Other measured uniform setters can overwrite the same location.
#define OTHER_UNIFORM(name,ARGS,CALL,N) \
 static void cached_##name ARGS { name CALL; invalidate_uniform(&shadow,location,N); }
OTHER_UNIFORM(glUniform1fARB,(GLint location,GLfloat v),(location,v),1)
OTHER_UNIFORM(glUniform1fvARB,(GLint location,GLsizei n,const GLfloat *v),(location,n,v),n)
OTHER_UNIFORM(glUniform2fvARB,(GLint location,GLsizei n,const GLfloat *v),(location,n,v),n)
OTHER_UNIFORM(glUniform3fvARB,(GLint location,GLsizei n,const GLfloat *v),(location,n,v),n)
OTHER_UNIFORM(glUniformMatrix2fvARB,(GLint location,GLsizei n,GLboolean t,const GLfloat *v),(location,n,t,v),n)
OTHER_UNIFORM(glUniformMatrix3fvARB,(GLint location,GLsizei n,GLboolean t,const GLfloat *v),(location,n,t,v),n)
OTHER_UNIFORM(glUniformMatrix4fvARB,(GLint location,GLsizei n,GLboolean t,const GLfloat *v),(location,n,t,v),n)
OTHER_UNIFORM(glUniform1f,(GLint location,GLfloat v),(location,v),1)
OTHER_UNIFORM(glUniform2f,(GLint location,GLfloat x,GLfloat y),(location,x,y),1)
OTHER_UNIFORM(glUniform3f,(GLint location,GLfloat x,GLfloat y,GLfloat z),(location,x,y,z),1)
OTHER_UNIFORM(glUniform4f,(GLint location,GLfloat x,GLfloat y,GLfloat z,GLfloat w),(location,x,y,z,w),1)
OTHER_UNIFORM(glUniform1iv,(GLint location,GLsizei n,const GLint *v),(location,n,v),n)
OTHER_UNIFORM(glUniform1fv,(GLint location,GLsizei n,const GLfloat *v),(location,n,v),n)
OTHER_UNIFORM(glUniform2fv,(GLint location,GLsizei n,const GLfloat *v),(location,n,v),n)
OTHER_UNIFORM(glUniform3fv,(GLint location,GLsizei n,const GLfloat *v),(location,n,v),n)
OTHER_UNIFORM(glUniformMatrix4fv,(GLint location,GLsizei n,GLboolean t,const GLfloat *v),(location,n,t,v),n)
OTHER_UNIFORM(glUniform2i,(GLint location,GLint x,GLint y),(location,x,y),1)
OTHER_UNIFORM(glUniform3i,(GLint location,GLint x,GLint y,GLint z),(location,x,y,z),1)
OTHER_UNIFORM(glUniform4i,(GLint location,GLint x,GLint y,GLint z,GLint w),(location,x,y,z,w),1)
OTHER_UNIFORM(glUniform2iv,(GLint location,GLsizei n,const GLint *v),(location,n,v),n)
OTHER_UNIFORM(glUniform3iv,(GLint location,GLsizei n,const GLint *v),(location,n,v),n)
OTHER_UNIFORM(glUniform4iv,(GLint location,GLsizei n,const GLint *v),(location,n,v),n)
OTHER_UNIFORM(glUniformMatrix2fv,(GLint location,GLsizei n,GLboolean t,const GLfloat *v),(location,n,t,v),n)
OTHER_UNIFORM(glUniformMatrix3fv,(GLint location,GLsizei n,GLboolean t,const GLfloat *v),(location,n,t,v),n)
static void cached_glDeleteProgram(GLuint p) {glDeleteProgram(p);atomic_fetch_add(&global_generation,1);reset_state(&shadow);}
static void cached_glLinkProgram(GLuint p) {glLinkProgram(p);atomic_fetch_add(&global_generation,1);reset_state(&shadow);}
static void cached_glDeleteObjectARB(GLhandleARB p) {glDeleteObjectARB(p);atomic_fetch_add(&global_generation,1);reset_state(&shadow);}
static void cached_glLinkProgramARB(GLhandleARB p) {glLinkProgramARB(p);atomic_fetch_add(&global_generation,1);reset_state(&shadow);}

#define PICK(name) if (!strcmp(symbol,#name)) return (void *)cached_##name
static void *cached_dlsym(void *handle,const char *symbol) {
    void *original=dlsym(handle,symbol);
    if (!original || !symbol) return original;
    PICK(glActiveTextureARB); PICK(glActiveTexture); PICK(glBindTexture);
    PICK(glTexEnvf); PICK(glTexParameteri); PICK(glTexParameterf);
    PICK(glTexParameterfv); PICK(glTexParameteriv); PICK(glTexEnvi); PICK(glTexEnvfv);
    PICK(glDeleteTextures); PICK(glBindBufferARB); PICK(glBindBuffer);
    PICK(glBindVertexArrayAPPLE); PICK(glBindVertexArray);
    PICK(glDeleteBuffersARB); PICK(glDeleteBuffers);
    PICK(glEnableVertexAttribArrayARB); PICK(glDisableVertexAttribArrayARB);
    PICK(glEnableVertexAttribArray); PICK(glDisableVertexAttribArray);
    PICK(glVertexAttribPointerARB); PICK(glVertexAttribPointer); PICK(glVertexAttribDivisorARB);
    PICK(glUseProgramObjectARB); PICK(glUseProgram); PICK(glUniform1i); PICK(glUniform1iARB);
    PICK(glUniform4fvARB); PICK(glUniform4fv);
    PICK(glUniform1fARB); PICK(glUniform1fvARB); PICK(glUniform2fvARB);
    PICK(glUniform3fvARB); PICK(glUniformMatrix2fvARB);
    PICK(glUniformMatrix3fvARB); PICK(glUniformMatrix4fvARB);
    PICK(glUniform1f); PICK(glUniform2f); PICK(glUniform3f); PICK(glUniform4f);
    PICK(glUniform1iv); PICK(glUniform1fv); PICK(glUniform2fv); PICK(glUniform3fv);
    PICK(glUniformMatrix4fv); PICK(glUniform2i); PICK(glUniform3i); PICK(glUniform4i);
    PICK(glUniform2iv); PICK(glUniform3iv); PICK(glUniform4iv);
    PICK(glUniformMatrix2fv); PICK(glUniformMatrix3fv);
    PICK(glDeleteProgram); PICK(glLinkProgram); PICK(glDeleteObjectARB); PICK(glLinkProgramARB);
    return original;
}
#define INTERPOSE(name) {(const void *)cached_##name,(const void *)name},
__attribute__((used,section("__DATA,__interpose")))
static const struct {const void *replacement,*replacee;} interposes[]={
    INTERPOSE(dlsym) INTERPOSE(CGLFlushDrawable) INTERPOSE(CGLSetCurrentContext)
    INTERPOSE(glBindTexture) INTERPOSE(glTexEnvf) INTERPOSE(glTexParameteri)
    INTERPOSE(glTexParameterf) INTERPOSE(glTexParameterfv) INTERPOSE(glTexParameteriv)
    INTERPOSE(glTexEnvi) INTERPOSE(glTexEnvfv) INTERPOSE(glDeleteTextures)
    INTERPOSE(glLinkProgram) INTERPOSE(glDeleteProgram) INTERPOSE(glDeleteBuffers)
    INTERPOSE(glUseProgram) INTERPOSE(glUniform1i) INTERPOSE(glUniform4fv)
};
