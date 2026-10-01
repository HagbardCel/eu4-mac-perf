#include <OpenGL/gl.h>
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>

typedef void (*ForwardArb)(GLhandleARB,void (*)(GLhandleARB));
static uintptr_t observed;
static void mock_forward(GLhandleARB value) { observed=(uintptr_t)value; }

int main(int argc,char **argv) {
    if(argc!=2 || sizeof(GLhandleARB)!=sizeof(uintptr_t)) return 2;
    void *library=dlopen(argv[1],RTLD_NOW|RTLD_LOCAL);
    if(!library) { fprintf(stderr,"dlopen: %s\n",dlerror()); return 3; }
    ForwardArb forward=(ForwardArb)dlsym(library,"eu4_frame_model_forward_arb");
    if(!forward) { fprintf(stderr,"missing ARB forwarding function\n"); return 4; }
    uintptr_t expected=(uintptr_t)0x12345678abcdef12ull;
    forward((GLhandleARB)expected,mock_forward);
    dlclose(library);
    if(observed!=expected) {
        fprintf(stderr,"ARB handle changed: expected 0x%llx got 0x%llx\n",
                (unsigned long long)expected,(unsigned long long)observed);
        return 5;
    }
    puts("GLhandleARB forwarding preserved all pointer bits");
    return 0;
}
