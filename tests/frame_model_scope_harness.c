#define _POSIX_C_SOURCE 200809L
#include "../benchmark/eu4_scope_tree.h"
#include <assert.h>
#include <time.h>
static uint64_t clock_ns(clockid_t clock) {
    struct timespec t; assert(clock_gettime(clock,&t)==0);
    return (uint64_t)t.tv_sec*1000000000ull+(uint64_t)t.tv_nsec;
}
int main(void) {
    Eu4ScopeTree tree={0};
    int root=eu4_scope_begin(&tree,8,true,0,0);
    int update=eu4_scope_begin(&tree,0,true,10,5);
    int a=eu4_scope_begin(&tree,2,true,20,10);
    int grandchild=eu4_scope_begin(&tree,5,true,30,20);
    eu4_scope_end(&tree,5,grandchild,50,30);
    eu4_scope_end(&tree,2,a,70,40);
    int b=eu4_scope_begin(&tree,4,true,80,50);
    eu4_scope_end(&tree,4,b,100,60);
    eu4_scope_end(&tree,0,update,110,70);
    eu4_scope_end(&tree,8,root,120,80);
    assert(!tree.flags && !tree.depth && tree.count==5);
    assert(tree.nodes[0].parent==-1 && tree.nodes[1].parent==0);
    assert(tree.nodes[2].parent==1 && tree.nodes[3].parent==2 && tree.nodes[4].parent==1);
    assert(tree.nodes[2].inclusive_wall==50 && tree.nodes[2].exclusive_wall==30);
    assert(tree.nodes[1].exclusive_wall==30 && tree.nodes[1].exclusive_cpu==25);
    char line[512];
    for(unsigned i=0;i<tree.count;i++) {
        assert(eu4_scope_record(line,sizeof(line),&tree,i,1,1,1)>0); fputs(line,stdout);
    }
    /* Stale storage must never supply a parent; stack slot reuse and recursion. */
    memset(&tree,0,sizeof(tree));
    root=eu4_scope_begin(&tree,0,true,0,0);
    a=eu4_scope_begin(&tree,0,true,10,10);
    b=eu4_scope_begin(&tree,0,true,20,20);
    eu4_scope_end(&tree,0,b,30,30); eu4_scope_end(&tree,0,a,40,40);
    a=eu4_scope_begin(&tree,4,true,50,50); eu4_scope_end(&tree,4,a,60,60);
    b=eu4_scope_begin(&tree,4,true,70,70); eu4_scope_end(&tree,4,b,80,80);
    eu4_scope_end(&tree,0,root,90,90);
    assert(tree.count==4 && tree.nodes[3].parent==0 && tree.nodes[3].calls==2);
    assert(tree.nodes[2].parent==1 && tree.nodes[1].parent==0);
    assert(eu4_scope_begin(&tree,5,false,100,100)==0 && tree.count==4);
    memset(&tree,0,sizeof(tree));
    for(unsigned i=0;i<EU4_SCOPE_DEPTH;i++) assert(eu4_scope_begin(&tree,i,true,i,i));
    assert(!eu4_scope_begin(&tree,99,true,100,100) && tree.flags==EU4_SCOPE_OVERFLOW);
    memset(&tree,0,sizeof(tree));
    for(unsigned i=0;i<EU4_SCOPE_NODES;i++) {
        a=eu4_scope_begin(&tree,i,true,0,0); eu4_scope_end(&tree,i,a,1,1);
    }
    assert(!eu4_scope_begin(&tree,999,true,0,0) && tree.flags==EU4_SCOPE_OVERFLOW);
    memset(&tree,0,sizeof(tree)); a=eu4_scope_begin(&tree,0,true,0,0);
    eu4_scope_end(&tree,1,a,1,1); assert(tree.flags==EU4_SCOPE_INVALID && !tree.depth);
    memset(&tree,0,sizeof(tree)); a=eu4_scope_begin(&tree,0,true,10,10);
    eu4_scope_end(&tree,0,a,5,5); assert(tree.flags==EU4_SCOPE_INVALID);
    memset(&tree,0,sizeof(tree));
    uint64_t wall=clock_ns(CLOCK_MONOTONIC),cpu=clock_ns(CLOCK_THREAD_CPUTIME_ID);
    root=eu4_scope_begin(&tree,0,true,wall,cpu);
    volatile unsigned long total=0;
    for(unsigned long i=0;i<2000000;i++) total+=i;
    struct timespec sleep={0,20000000}; nanosleep(&sleep,NULL);
    eu4_scope_end(&tree,0,root,clock_ns(CLOCK_MONOTONIC),clock_ns(CLOCK_THREAD_CPUTIME_ID));
    assert(!tree.flags && tree.nodes[0].inclusive_wall>tree.nodes[0].inclusive_cpu+10000000);
    (void)total;
    puts("scope harness passed"); return 0;
}
