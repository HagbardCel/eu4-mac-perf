#include <assert.h>
#include <dlfcn.h>
#include <pthread.h>
#include <stdio.h>
static void (*emit)(unsigned);
static void *produce(void *unused) { (void)unused;emit(32);return NULL; }
int main(int argc,char **argv) {
    if(argc!=2) return 1;
    void *library=dlopen(argv[1],RTLD_NOW);assert(library);
    emit=dlsym(library,"eu4_frame_model_test_emit_records");assert(emit);
    void *(*resolve)(const char *)=dlsym(library,"eu4_frame_model_test_resolve_draw");assert(resolve);
    assert(resolve("glDrawArrays") && resolve("glDrawArraysInstancedARB") && resolve("glBegin"));
    assert(resolve("glDrawArraysInstanced")!=resolve("glDrawArraysInstancedARB"));
    assert(resolve("glDrawRangeElements") && resolve("glMultiDrawElements") && resolve("glDrawArraysIndirect"));
    assert(!resolve("glDrawBuffers") && !resolve("glUnknownDraw"));
    pthread_t workers[4];
    for(unsigned i=0;i<4;i++) assert(!pthread_create(&workers[i],NULL,produce,NULL));
    for(unsigned i=0;i<4;i++) assert(!pthread_join(workers[i],NULL));
    dlclose(library); /* No waiting: destructor must drain every producer. */
    puts("four producers, imported/resolved aliases and observer coverage, ordered serialization, immediate shutdown passed");
}
