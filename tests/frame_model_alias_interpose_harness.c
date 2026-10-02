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
        fprintf(stderr, "glDrawArraysInstancedARB: profiler wrapper must stay uninterposed in Stage 0\n");
        return 2;
    }
    if (code == 2) {
        fprintf(stderr, "glDrawElementsInstancedARB: profiler wrapper must stay uninterposed in Stage 0\n");
        return 3;
    }
    if (code == 3) {
        fprintf(stderr, "core instanced suppression wrappers missing\n");
        return 4;
    }
    if (code != 0) {
        fprintf(stderr, "unexpected verify code %d\n", code);
        return 7;
    }
    puts("ARB instanced aliases uncovered in Stage 0 (core suppression wrappers present)");
    return 0;
}
