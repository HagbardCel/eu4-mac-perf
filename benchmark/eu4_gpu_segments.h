#ifndef EU4_GPU_SEGMENTS_H
#define EU4_GPU_SEGMENTS_H
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#define EU4_GPU_CONTEXTS 16
#define EU4_GPU_SLOTS 8
enum { EU4_GPU_FREE,EU4_GPU_ACTIVE,EU4_GPU_PENDING };
enum { EU4_GPU_NULL=1,EU4_GPU_UNSUPPORTED,EU4_GPU_CAPACITY,EU4_GPU_POOL,
       EU4_GPU_OWNERSHIP,EU4_GPU_DESTROYED,EU4_GPU_UNCOLLECTED,EU4_GPU_INVALID,EU4_GPU_UNPREPARED };
typedef struct {
    uintptr_t context;
    uint64_t phase,render,epoch,update,lifetime,pass,sequence,thread,generation,window;
} Eu4GpuIdentity;
typedef struct { unsigned query[2],state; Eu4GpuIdentity identity; } Eu4GpuSlot;
typedef struct {
    uintptr_t context; uint64_t lifetime; bool supported;
    Eu4GpuSlot slots[EU4_GPU_SLOTS]; int active;
} Eu4GpuContext;
typedef struct {
    uintptr_t (*current)(void);
    bool (*initialize)(unsigned *queries,unsigned count);
    void (*stamp)(unsigned query);
    bool (*available)(unsigned query);
    uint64_t (*result)(unsigned query);
    void (*emit)(Eu4GpuIdentity identity,uint64_t start,uint64_t end,unsigned missing);
} Eu4GpuApi;
typedef struct {
    Eu4GpuContext contexts[EU4_GPU_CONTEXTS]; unsigned count; uint64_t next_lifetime;
} Eu4GpuRegistry;
typedef struct {
    Eu4GpuRegistry local,*registry;
    uint64_t sequence,owner_lifetime; bool rendering;
    Eu4GpuIdentity identity; Eu4GpuContext *owner;
} Eu4GpuManager;
/* Callers serialize shared registry operations. Query polling always uses current(). */
static inline Eu4GpuRegistry *eu4_gpu_registry(Eu4GpuManager *m) {
    return m->registry?m->registry:&m->local;
}
static inline void eu4_gpu_missing(Eu4GpuManager *m,Eu4GpuApi *api,unsigned reason) {
    Eu4GpuIdentity id=m->identity; id.sequence=++m->sequence;
    id.lifetime=m->owner?m->owner->lifetime:0; api->emit(id,0,0,reason);
}
static inline void eu4_gpu_poll(Eu4GpuContext *ctx,Eu4GpuApi *api) {
    if(!ctx || !ctx->supported || api->current()!=ctx->context) return;
    for(unsigned i=0;i<EU4_GPU_SLOTS;i++) {
        Eu4GpuSlot *s=&ctx->slots[i];
        if(s->state!=EU4_GPU_PENDING || !api->available(s->query[0]) || !api->available(s->query[1])) continue;
        uint64_t start=api->result(s->query[0]),end=api->result(s->query[1]);
        api->emit(s->identity,start,end,end<start?EU4_GPU_INVALID:0); s->state=EU4_GPU_FREE;
    }
}
static inline void eu4_gpu_close(Eu4GpuManager *m,Eu4GpuApi *api) {
    Eu4GpuContext *ctx=m->owner;
    if(!ctx || ctx->lifetime!=m->owner_lifetime || ctx->active<0) return;
    Eu4GpuSlot *s=&ctx->slots[ctx->active];
    if(s->identity.render!=m->identity.render || s->identity.thread!=m->identity.thread) return;
    if(api->current()!=ctx->context) {
        api->emit(s->identity,0,0,EU4_GPU_OWNERSHIP); s->state=EU4_GPU_FREE;
    } else { api->stamp(s->query[1]); s->state=EU4_GPU_PENDING; }
    ctx->active=-1;
}
/* Preparation is explicitly called only with measurement disabled and current ownership. */
static inline void eu4_gpu_prepare(Eu4GpuManager *m,Eu4GpuApi *api) {
    uintptr_t current=api->current();if(!current) return;
    unsigned index=0;
    while(index<eu4_gpu_registry(m)->count && eu4_gpu_registry(m)->contexts[index].context!=current) index++;
    if(index==eu4_gpu_registry(m)->count) {
        index=0; while(index<eu4_gpu_registry(m)->count && eu4_gpu_registry(m)->contexts[index].context) index++;
        if(index==EU4_GPU_CONTEXTS) { return; }
        if(index==eu4_gpu_registry(m)->count) eu4_gpu_registry(m)->count++;
        Eu4GpuContext *ctx=&eu4_gpu_registry(m)->contexts[index]; memset(ctx,0,sizeof(*ctx));
        ctx->context=current; ctx->lifetime=++eu4_gpu_registry(m)->next_lifetime; ctx->active=-1;
        unsigned ids[EU4_GPU_SLOTS*2]={0};
        ctx->supported=api->initialize(ids,EU4_GPU_SLOTS*2);
        for(unsigned i=0;i<EU4_GPU_SLOTS;i++) {
            ctx->slots[i].query[0]=ids[i*2]; ctx->slots[i].query[1]=ids[i*2+1];
        }
    }
}
static inline void eu4_gpu_open(Eu4GpuManager *m,Eu4GpuApi *api) {
    if(!m->rendering) return;
    uintptr_t current=api->current();m->owner=NULL;
    if(!current) {eu4_gpu_missing(m,api,EU4_GPU_NULL);return;}
    m->identity.context=current;
    unsigned index=0;
    while(index<eu4_gpu_registry(m)->count && eu4_gpu_registry(m)->contexts[index].context!=current) index++;
    if(index==eu4_gpu_registry(m)->count) {eu4_gpu_missing(m,api,EU4_GPU_UNPREPARED);return;}
    Eu4GpuContext *ctx=m->owner=&eu4_gpu_registry(m)->contexts[index];
    m->owner_lifetime=ctx->lifetime;
    if(!ctx->supported) { eu4_gpu_missing(m,api,EU4_GPU_UNSUPPORTED); return; }
    eu4_gpu_poll(ctx,api);
    if(ctx->active>=0) { eu4_gpu_missing(m,api,EU4_GPU_OWNERSHIP); return; }
    for(unsigned i=0;i<EU4_GPU_SLOTS;i++) if(ctx->slots[i].state==EU4_GPU_FREE) {
        Eu4GpuSlot *s=&ctx->slots[i]; s->identity=m->identity;
        s->identity.lifetime=ctx->lifetime; s->identity.sequence=++m->sequence;
        s->state=EU4_GPU_ACTIVE; ctx->active=(int)i; api->stamp(s->query[0]); return;
    }
    eu4_gpu_missing(m,api,EU4_GPU_POOL);
}
static inline void eu4_gpu_begin(Eu4GpuManager *m,Eu4GpuApi *api,Eu4GpuIdentity identity) {
    if(m->rendering) eu4_gpu_close(m,api);
    m->rendering=true; m->sequence=0; m->identity=identity; eu4_gpu_open(m,api);
}
static inline void eu4_gpu_pass(Eu4GpuManager *m,Eu4GpuApi *api,uint64_t pass) {
    if(!m->rendering) return;
    eu4_gpu_close(m,api); m->identity.pass=pass; eu4_gpu_open(m,api);
}
static inline void eu4_gpu_end(Eu4GpuManager *m,Eu4GpuApi *api) {
    eu4_gpu_close(m,api); m->rendering=false;
    if(m->owner) eu4_gpu_poll(m->owner,api);
}
/* Called only after successful destruction; no GL calls in the dead context. */
static inline void eu4_gpu_destroy(Eu4GpuManager *m,Eu4GpuApi *api,uintptr_t context) {
    for(unsigned i=0;i<eu4_gpu_registry(m)->count;i++) if(eu4_gpu_registry(m)->contexts[i].context==context) {
        Eu4GpuContext *ctx=&eu4_gpu_registry(m)->contexts[i];
        for(unsigned j=0;j<EU4_GPU_SLOTS;j++) if(ctx->slots[j].state!=EU4_GPU_FREE)
            api->emit(ctx->slots[j].identity,0,0,EU4_GPU_DESTROYED);
        if(m->owner==ctx) m->owner=NULL;
        memset(ctx,0,sizeof(*ctx)); ctx->active=-1;
    }
}
static inline void eu4_gpu_drain(Eu4GpuManager *m,Eu4GpuApi *api) {
    eu4_gpu_end(m,api);
    for(unsigned i=0;i<eu4_gpu_registry(m)->count;i++) {
        Eu4GpuContext *ctx=&eu4_gpu_registry(m)->contexts[i]; eu4_gpu_poll(ctx,api);
        for(unsigned j=0;j<EU4_GPU_SLOTS;j++) if(ctx->slots[j].state!=EU4_GPU_FREE && ctx->slots[j].identity.thread==m->identity.thread &&
            ctx->slots[j].identity.epoch==m->identity.epoch) {
            api->emit(ctx->slots[j].identity,0,0,EU4_GPU_UNCOLLECTED);
            ctx->slots[j].state=EU4_GPU_FREE;
        }
    }
}
#endif
