#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>

int main(void) {
    const char *control_path = getenv("EU4_FRAME_MODEL_CONTROL");
    const char *log_path = getenv("EU4_FRAME_MODEL_LOG");
    if (!control_path || !log_path)
        return 1;
    int (*verify)(void) = dlsym(RTLD_DEFAULT, "eu4_frame_model_test_verify_arb_instanced_interpose");
    if (!verify) {
        fprintf(stderr, "interposer not loaded: verify export missing\n");
        return 1;
    }
    int code = verify();
    if (code == 1) {
        fprintf(stderr, "glDrawArraysInstancedARB: wrapper must differ from RTLD_NEXT (from interposer)\n");
        return 2;
    }
    if (code == 2) {
        fprintf(stderr, "glDrawElementsInstancedARB: wrapper must differ from RTLD_NEXT (from interposer)\n");
        return 3;
    }
    if (code == 10 || code == 11) {
        fprintf(stderr, "draw_observer_resolve disagrees with profiler wrapper (code %d)\n", code);
        return 4;
    }
    if (code != 0) {
        fprintf(stderr, "unexpected verify code %d\n", code);
        return 5;
    }
    puts("ARB instanced interpose: profiler wrappers differ from RTLD_NEXT and targets are non-NULL");
    return 0;
}
