#include <assert.h>
#include <dlfcn.h>
#include <stdio.h>

int main(int argc, char **argv) {
    if (argc != 2) {
        return 1;
    }
    void *library = dlopen(argv[1], RTLD_NOW);
    assert(library);
    void (*emit)(unsigned) = dlsym(library, "eu4_frame_model_test_emit_lean_reference_records");
    assert(emit);
    emit(2);
    dlclose(library);
    puts("lean reference writer serialization passed");
    return 0;
}
