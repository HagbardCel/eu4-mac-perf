#include <assert.h>
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void *underlying_gl_symbol(const char *arb_name, const char *core_name) {
    void *next = dlsym(RTLD_NEXT, arb_name);
    if (!next && core_name)
        next = dlsym(RTLD_NEXT, core_name);
    return next;
}

static int check_profiler_wrapper(const char *arb_name, const char *core_name,
        void *(*resolve)(const char *)) {
    void *wrapper = resolve(arb_name);
    void *next = underlying_gl_symbol(arb_name, core_name);
    if (!wrapper) {
        fprintf(stderr, "%s: profiler resolver returned NULL\n", arb_name);
        return 1;
    }
    if (!next) {
        fprintf(stderr, "%s: no RTLD_NEXT target (%s%s)\n", arb_name, arb_name,
                core_name ? " or core" : "");
        return 1;
    }
    if (wrapper == next) {
        fprintf(stderr, "%s: profiler wrapper must differ from RTLD_NEXT target\n", arb_name);
        return 1;
    }
    return 0;
}

int main(void) {
    const char *control_path = getenv("EU4_FRAME_MODEL_CONTROL");
    const char *log_path = getenv("EU4_FRAME_MODEL_LOG");
    if (!control_path || !log_path)
        return 1;
    void *(*resolve)(const char *) = dlsym(RTLD_DEFAULT, "eu4_frame_model_test_resolve_draw");
    if (!resolve) {
        fprintf(stderr, "interposer not loaded: eu4_frame_model_test_resolve_draw missing\n");
        return 1;
    }
    if (check_profiler_wrapper("glDrawArraysInstancedARB", "glDrawArraysInstanced", resolve))
        return 2;
    if (check_profiler_wrapper("glDrawElementsInstancedARB", "glDrawElementsInstanced", resolve))
        return 3;
    assert(resolve("glDrawArraysInstancedARB") != resolve("glDrawArraysInstanced"));
    assert(resolve("glDrawElementsInstancedARB") != resolve("glDrawElementsInstanced"));
    puts("ARB instanced interpose: profiler wrappers differ from RTLD_NEXT and targets are non-NULL");
    return 0;
}
