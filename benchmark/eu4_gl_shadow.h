#ifndef EU4_GL_SHADOW_H
#define EU4_GL_SHADOW_H
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#define EU4_SHADOW_CONTEXTS 16
#define EU4_SHADOW_VAOS 64
/* Thread-owned storage. The caller supplies a serialized lifetime/ownership stamp. */
typedef struct { uint32_t buffer; int32_t size; uint32_t type; unsigned char normalized,enabled;
                 int32_t stride; uintptr_t pointer; uint32_t divisor; } VertexAttribute;
typedef struct { bool used,prepared; uintptr_t context; uint32_t vao,element_buffer;
                 VertexAttribute attributes[32]; } VertexArrayState;
typedef struct { uintptr_t context; uint64_t stamp; bool prepared;
                 uintptr_t program; uint32_t array_buffer,vao; } Eu4ShadowContext;
typedef struct { Eu4ShadowContext contexts[EU4_SHADOW_CONTEXTS];
                 VertexArrayState vaos[EU4_SHADOW_VAOS]; Eu4ShadowContext *active;
                 VertexArrayState *vertex; unsigned reason; } Eu4GlShadow;
enum { EU4_SHADOW_MISSING=1,EU4_SHADOW_CAPACITY,EU4_SHADOW_INVALIDATED,EU4_SHADOW_MUTATION };
static inline void eu4_shadow_invalidate(Eu4GlShadow *s,uintptr_t context) {
    for(unsigned i=0;i<EU4_SHADOW_CONTEXTS;i++) if(s->contexts[i].context==context)
        memset(&s->contexts[i],0,sizeof(s->contexts[i]));
    for(unsigned i=0;i<EU4_SHADOW_VAOS;i++) if(s->vaos[i].context==context)
        memset(&s->vaos[i],0,sizeof(s->vaos[i]));
    if(s->active && s->active->context==0) {s->active=NULL;s->vertex=NULL;}
    s->reason=EU4_SHADOW_INVALIDATED;
}
static inline VertexArrayState *eu4_shadow_vao(Eu4GlShadow *s,uintptr_t context,uint32_t vao,bool create) {
    VertexArrayState *free_slot=NULL;
    for(unsigned i=0;i<EU4_SHADOW_VAOS;i++) {
        VertexArrayState *v=&s->vaos[i];
        if(v->used && v->context==context && v->vao==vao) return v;
        if(!v->used && !free_slot) free_slot=v;
    }
    if(!create || !free_slot) {s->reason=create?EU4_SHADOW_CAPACITY:EU4_SHADOW_MISSING;return NULL;}
    *free_slot=(VertexArrayState){.used=true,.context=context,.vao=vao};return free_slot;
}
static inline bool eu4_shadow_select(Eu4GlShadow *s,uintptr_t context,uint64_t stamp,bool measured) {
    s->active=NULL;s->vertex=NULL;
    if(!context || !stamp) {s->reason=EU4_SHADOW_MISSING;return false;}
    Eu4ShadowContext *slot=NULL;
    for(unsigned i=0;i<EU4_SHADOW_CONTEXTS;i++) {
        Eu4ShadowContext *c=&s->contexts[i];
        if(c->context==context && c->stamp!=stamp) eu4_shadow_invalidate(s,context);
    }
    for(unsigned i=0;i<EU4_SHADOW_CONTEXTS;i++) {
        Eu4ShadowContext *c=&s->contexts[i];
        if(c->context==context) {slot=c;break;}
        if(!c->context && !slot) slot=c;
    }
    if(!slot) {s->reason=EU4_SHADOW_CAPACITY;return false;}
    if(!slot->context) {
        if(measured) {s->reason=EU4_SHADOW_MISSING;return false;}
        *slot=(Eu4ShadowContext){.context=context,.stamp=stamp};
    }
    s->active=slot;s->vertex=eu4_shadow_vao(s,context,slot->vao,false);
    if(!slot->prepared || !s->vertex || !s->vertex->prepared) {s->reason=EU4_SHADOW_MISSING;return false;}
    s->reason=0;return true;
}
#endif
