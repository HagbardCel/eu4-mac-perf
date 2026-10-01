#include "../benchmark/eu4_spsc.h"
#include "../benchmark/eu4_raster_policy.h"
#include <assert.h>
#include <pthread.h>
#include <sched.h>
#include <stdio.h>
#define CAP 16
#define TOTAL 100000
#define THREADS 4
typedef struct { Eu4Spsc queue; uint64_t payload[CAP]; } Stream;
static Stream streams[THREADS];
static void *produce(void *arg) {
    Stream *s=arg;
    for(uint64_t i=1;i<=TOTAL;i++) {
        uint64_t position;
        while(!eu4_spsc_reserve(&s->queue,CAP,&position)) sched_yield();
        s->payload[position%CAP]=i;
        eu4_spsc_publish(&s->queue,position);
    }
    return NULL;
}
int main(void) {
    Eu4Spsc full={0}; uint64_t p;
    for(unsigned i=0;i<CAP;i++) { assert(eu4_spsc_reserve(&full,CAP,&p));eu4_spsc_publish(&full,p); }
    assert(!eu4_spsc_reserve(&full,CAP,&p));
    assert(eu4_spsc_read(&full,&p));eu4_spsc_release(&full,p);
    assert(eu4_spsc_reserve(&full,CAP,&p));
    pthread_t threads[THREADS];uint64_t expected[THREADS]={1,1,1,1};
    for(unsigned i=0;i<THREADS;i++) assert(!pthread_create(&threads[i],NULL,produce,&streams[i]));
    unsigned finished=0;
    while(finished<THREADS) {
        finished=0;
        for(unsigned i=0;i<THREADS;i++) {
            if(eu4_spsc_read(&streams[i].queue,&p)) {
                assert(streams[i].payload[p%CAP]==expected[i]++);
                eu4_spsc_release(&streams[i].queue,p);
            }
            if(expected[i]==TOTAL+1) finished++;
        }
    }
    for(unsigned i=0;i<THREADS;i++) pthread_join(threads[i],NULL);
    Eu4RasterContext contexts[]={{1,false},{2,true}};
    assert(eu4_raster_request(contexts,2,1,false));
    assert(!contexts[0].requested && contexts[1].requested);
    assert(eu4_raster_request(contexts,2,2,false));
    assert(eu4_raster_request(contexts,2,1,true));
    assert(contexts[0].requested && !contexts[1].requested);
    assert(!eu4_raster_request(contexts,2,3,false));
    puts("four independent producers, saturation, reuse, ordered publication, and raster restoration policy passed");
}
