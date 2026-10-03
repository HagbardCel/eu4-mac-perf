#include "../benchmark/eu4_gpu_segments.h"
#include <assert.h>
#include <stdio.h>
static uintptr_t current_context=1;
static unsigned next_query,reads,emitted,missing[16];
static uintptr_t owners[8192];
static bool ready=true;
static uint64_t timestamp;
static uintptr_t current(void) { return current_context; }
static bool initialize(unsigned *ids,unsigned count) {
    if(current_context==99) return false;
    for(unsigned i=0;i<count;i++) { ids[i]=++next_query;owners[ids[i]]=current_context; }
    return true;
}
static void stamp(unsigned query) { assert(owners[query]==current_context); }
static bool available(unsigned query) { assert(owners[query]==current_context); return ready; }
static uint64_t result(unsigned query) { assert(ready && owners[query]==current_context);reads++; return ++timestamp; }
static void emit(Eu4GpuIdentity id,uint64_t start,uint64_t end,unsigned reason) {
    assert(id.epoch==7 && (id.render==42 || id.render==100 || id.render==101)); if(reason) missing[reason]++;
    else { assert(end>start && id.lifetime && id.sequence);emitted++; }
}
static Eu4GpuApi api={current,initialize,stamp,available,result,emit};
static void switch_to(Eu4GpuManager *m,uintptr_t next,bool success) {
    eu4_gpu_close(m,&api); if(success) {current_context=next;eu4_gpu_prepare(m,&api);} eu4_gpu_open(m,&api);
}
int main(void) {
    Eu4GpuManager m={0};
    Eu4GpuIdentity identity={.phase=1,.render=42,.epoch=7,.update=8};
    ready=false; eu4_gpu_prepare(&m,&api);eu4_gpu_begin(&m,&api,identity);
    assert(m.local.contexts[0].slots[0].state==EU4_GPU_ACTIVE);
    eu4_gpu_poll(&m.local.contexts[0],&api); assert(!reads); /* Active query never polled. */
    switch_to(&m,2,true); switch_to(&m,1,true); assert(m.local.count==2 && !reads);
    switch_to(&m,3,false); assert(m.local.count==2 && m.owner->context==1);
    eu4_gpu_pass(&m,&api,1); eu4_gpu_pass(&m,&api,0);
    eu4_gpu_end(&m,&api); assert(!reads);
    ready=true; eu4_gpu_poll(&m.local.contexts[1],&api); assert(!reads); /* Wrong owner. */
    eu4_gpu_poll(&m.local.contexts[0],&api); assert(reads && emitted);
    current_context=2; eu4_gpu_poll(&m.local.contexts[1],&api);
    uint64_t lifetime=m.local.contexts[1].lifetime;
    eu4_gpu_destroy(&m,&api,2); assert(m.local.count==2);
    eu4_gpu_prepare(&m,&api);eu4_gpu_begin(&m,&api,identity);assert(m.local.count==2 && m.owner->lifetime>lifetime);
    switch_to(&m,0,true); assert(missing[EU4_GPU_NULL]);
    switch_to(&m,99,true); assert(missing[EU4_GPU_UNSUPPORTED]);
    switch_to(&m,1,true); current_context=2; eu4_gpu_close(&m,&api);
    assert(missing[EU4_GPU_OWNERSHIP]);
    eu4_gpu_open(&m,&api); ready=false; eu4_gpu_end(&m,&api);
    eu4_gpu_destroy(&m,&api,2); assert(missing[EU4_GPU_DESTROYED]);
    current_context=1;
    for(unsigned i=0;i<20;i++) { eu4_gpu_begin(&m,&api,identity);eu4_gpu_end(&m,&api); }
    assert(missing[EU4_GPU_POOL]);
    for(uintptr_t i=3;i<30;i++) { current_context=i;eu4_gpu_prepare(&m,&api);eu4_gpu_begin(&m,&api,identity);eu4_gpu_end(&m,&api); }
    assert(m.local.count==EU4_GPU_CONTEXTS && missing[EU4_GPU_UNPREPARED]);
    eu4_gpu_drain(&m,&api); assert(missing[EU4_GPU_UNCOLLECTED]);
    /* A pending query survives a context migration to another render thread. */
    Eu4GpuRegistry shared={0};
    Eu4GpuManager first={.registry=&shared},second={.registry=&shared};
    current_context=1; ready=false;
    identity.thread=1; identity.render=100;
    eu4_gpu_prepare(&first,&api);eu4_gpu_begin(&first,&api,identity); eu4_gpu_end(&first,&api);
    unsigned previous_reads=reads;
    identity.thread=2; identity.render=101; ready=true;
    eu4_gpu_begin(&second,&api,identity);
    assert(shared.count==1 && reads>previous_reads);
    eu4_gpu_end(&second,&api);
    uint64_t old_lifetime=first.owner_lifetime;
    eu4_gpu_destroy(&second,&api,1);
    eu4_gpu_prepare(&second,&api);eu4_gpu_begin(&second,&api,identity);
    assert(second.owner_lifetime>old_lifetime);
    eu4_gpu_close(&first,&api); /* Stale cursor cannot terminate the new owner's query. */
    assert(second.owner->active>=0);
    eu4_gpu_end(&second,&api);
    Eu4GpuManager unprepared={0};current_context=55;
    unsigned prior_queries=next_query;
    eu4_gpu_begin(&unprepared,&api,identity);eu4_gpu_end(&unprepared,&api);
    assert(next_query==prior_queries && missing[EU4_GPU_UNPREPARED]);
    puts("GPU segment harness passed"); return 0;
}
