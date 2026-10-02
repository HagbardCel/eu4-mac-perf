#include <assert.h>
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>

static int check_interposed(const char *name) {
    void *wrapped = dlsym(RTLD_DEFAULT, name);
    void *next = dlsym(RTLD_NEXT, name);
    if (!next) {
        fprintf(stderr, "%s: RTLD_NEXT symbol is NULL\n", name);
        return 1;
    }
    if (!wrapped) {
        fprintf(stderr, "%s: RTLD_DEFAULT symbol is NULL with interposer loaded\n", name);
        return 1;
    }
    if (wrapped == next) {
        fprintf(stderr, "%s: profiler wrapper must differ from RTLD_NEXT target\n", name);
        return 1;
    }
    return 0;
}

int main(void) {
    const char *control_path = getenv("EU4_FRAME_MODEL_CONTROL");
    const char *log_path = getenv("EU4_FRAME_MODEL_LOG");
    if (!control_path || !log_path)
        return 1;
    if (check_interposed("glDrawArraysInstancedARB"))
        return 2;
    if (check_interposed("glDrawElementsInstancedARB"))
        return 3;
    void *(*resolve)(const char *) = dlsym(RTLD_DEFAULT, "eu4_frame_model_test_resolve_draw");
    if (resolve) {
        void *arrays = resolve("glDrawArraysInstancedARB");
        void *elements = resolve("glDrawElementsInstancedARB");
        assert(arrays && elements);
        assert(arrays == dlsym(RTLD_DEFAULT, "glDrawArraysInstancedARB"));
        assert(elements == dlsym(RTLD_DEFAULT, "glDrawElementsInstancedARB"));
        assert(arrays != dlsym(RTLD_NEXT, "glDrawArraysInstancedARB"));
        assert(elements != dlsym(RTLD_NEXT, "glDrawElementsInstancedARB"));
    }
    puts("ARB instanced interpose: profiler wrappers differ from RTLD_NEXT and targets are non-NULL");
    return 0;
}
