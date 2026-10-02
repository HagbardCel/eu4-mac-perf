#include "../benchmark/eu4_gl_shadow.h"
#include <assert.h>
#include <stdio.h>
static unsigned queries;
static void prepare(Eu4GlShadow *s,uintptr_t ctx,uint64_t stamp) {
    if(eu4_shadow_select(s,ctx,stamp,false)) return;
    assert(s->active);queries++;
    s->vertex=eu4_shadow_vao(s,ctx,0,true);assert(s->vertex);
    s->vertex->prepared=true;s->vertex->element_buffer=(uint32_t)ctx+10;
    s->active->program=ctx+100;s->active->prepared=true;
}
int main(void) {
    Eu4GlShadow s={0};
    prepare(&s,1,1);prepare(&s,2,2);assert(queries==2);
    assert(eu4_shadow_select(&s,1,1,true) && s.active->program==101 && s.vertex->element_buffer==11);
    assert(eu4_shadow_select(&s,2,2,true) && s.active->program==102);
    assert(eu4_shadow_select(&s,1,1,true));assert(queries==2);
    assert(!eu4_shadow_select(&s,3,3,true));assert(!s.active && queries==2);
    /* Migration and destruction/reuse stamps invalidate old prepared state. */
    assert(!eu4_shadow_select(&s,1,4,true));assert(queries==2);
    prepare(&s,1,4);eu4_shadow_invalidate(&s,1);
    assert(!eu4_shadow_select(&s,1,5,true));prepare(&s,1,5);
    for(uintptr_t ctx=3;ctx<=EU4_SHADOW_CONTEXTS;ctx++) prepare(&s,ctx,ctx+10);
    assert(!eu4_shadow_select(&s,100,100,false) && s.reason==EU4_SHADOW_CAPACITY);
    eu4_shadow_invalidate(&s,2);prepare(&s,100,100); /* Freed capacity reusable. */
    for(unsigned vao=1;vao<EU4_SHADOW_VAOS;vao++) (void)eu4_shadow_vao(&s,100,vao,true);
    assert(!eu4_shadow_vao(&s,100,999,true) && s.reason==EU4_SHADOW_CAPACITY);
    unsigned count=queries;
    assert(!eu4_shadow_select(&s,100,101,true));assert(queries==count);
    puts("shadow preparation, measured query prohibition, switching, migration, reuse and bounds passed");
}
