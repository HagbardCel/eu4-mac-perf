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
    assert(id.epoch==7 && id.render==42); if(reason) missing[reason]++;
    else { assert(end>start && id.lifetime && id.sequence);emitted++; }
}
static Eu4GpuApi api={current,initialize,stamp,available,result,emit};
static void switch_to(Eu4GpuManager *m,uintptr_t next,bool success) {
    eu4_gpu_close(m,&api); if(success) current_context=next; eu4_gpu_open(m,&api);
}
int main(void) {
    Eu4GpuManager m={0};
    Eu4GpuIdentity identity={.phase=1,.render=42,.epoch=7,.update=8};
    ready=false; eu4_gpu_begin(&m,&api,identity);
    assert(m.contexts[0].slots[0].state==EU4_GPU_ACTIVE);
    eu4_gpu_poll(&m.contexts[0],&api); assert(!reads); /* Active query never polled. */
    switch_to(&m,2,true); switch_to(&m,1,true); assert(m.count==2 && !reads);
    switch_to(&m,3,false); assert(m.count==2 && m.owner->context==1);
    eu4_gpu_pass(&m,&api,1); eu4_gpu_pass(&m,&api,0);
    eu4_gpu_end(&m,&api); assert(!reads);
    ready=true; eu4_gpu_poll(&m.contexts[1],&api); assert(!reads); /* Wrong owner. */
    eu4_gpu_poll(&m.contexts[0],&api); assert(reads && emitted);
    current_context=2; eu4_gpu_poll(&m.contexts[1],&api);
    uint64_t lifetime=m.contexts[1].lifetime;
    eu4_gpu_destroy(&m,&api,2); assert(m.count==2);
    eu4_gpu_begin(&m,&api,identity);assert(m.count==2 && m.owner->lifetime>lifetime);
    switch_to(&m,0,true); assert(missing[EU4_GPU_NULL]);
    switch_to(&m,99,true); assert(missing[EU4_GPU_UNSUPPORTED]);
    switch_to(&m,1,true); current_context=2; eu4_gpu_close(&m,&api);
    assert(missing[EU4_GPU_OWNERSHIP]);
    eu4_gpu_open(&m,&api); ready=false; eu4_gpu_end(&m,&api);
    eu4_gpu_destroy(&m,&api,2); assert(missing[EU4_GPU_DESTROYED]);
    current_context=1;
    for(unsigned i=0;i<20;i++) { eu4_gpu_begin(&m,&api,identity);eu4_gpu_end(&m,&api); }
    assert(missing[EU4_GPU_POOL]);
    for(uintptr_t i=3;i<30;i++) { current_context=i;eu4_gpu_begin(&m,&api,identity);eu4_gpu_end(&m,&api); }
    assert(m.count==EU4_GPU_CONTEXTS && missing[EU4_GPU_CAPACITY]);
    eu4_gpu_drain(&m,&api); assert(missing[EU4_GPU_UNCOLLECTED]);
    puts("GPU segment harness passed"); return 0;
}
