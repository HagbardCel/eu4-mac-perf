#include <assert.h>
#include <dlfcn.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

enum { PROFILE = 1, DROP_DRAWS = 3 };

static void read_stats(void (*reader)(uint64_t *, uint64_t *, uint64_t *, uint64_t *, uint64_t *),
                       uint64_t *draws, uint64_t *suppressed, uint64_t *forwarded,
                       uint64_t *api_count, uint64_t *api_suppressed) {
    reader(draws, suppressed, forwarded, api_count, api_suppressed);
}

int main(int argc, char **argv) {
    if (argc != 2) return 1;
    const char *control_path = getenv("EU4_FRAME_MODEL_CONTROL");
    const char *log_path = getenv("EU4_FRAME_MODEL_LOG");
    if (!control_path || !log_path) return 1;
    void *library = dlopen(argv[1], RTLD_NOW);
    if (!library) {
        fprintf(stderr, "dlopen: %s\n", dlerror());
        return 2;
    }
    void (*reset)(void) = dlsym(library, "eu4_frame_model_test_reset_draw_alias");
    void (*arm)(unsigned) = dlsym(library, "eu4_frame_model_test_arm_draw_alias");
    void (*set_stub)(unsigned) = dlsym(library, "eu4_frame_model_test_set_draw_alias_stub");
    unsigned (*hits)(unsigned) = dlsym(library, "eu4_frame_model_test_draw_alias_forward_hits");
    void (*invoke)(const char *) = dlsym(library, "eu4_frame_model_test_invoke_draw_alias");
    void (*stats)(uint64_t *, uint64_t *, uint64_t *, uint64_t *, uint64_t *) =
        dlsym(library, "eu4_frame_model_test_read_draw_alias_stats");
    void *(*resolve)(const char *) = dlsym(library, "eu4_frame_model_test_resolve_draw");
    if (!reset || !arm || !set_stub || !hits || !invoke || !stats || !resolve) return 3;
    assert(resolve("glDrawArraysInstanced"));
    assert(resolve("glDrawArraysInstancedARB"));
    assert(resolve("glDrawArraysInstanced") != resolve("glDrawArraysInstancedARB"));

    uint64_t draws, suppressed, forwarded, api_count, api_suppressed;

    reset();
    arm(PROFILE);
    set_stub(1);
    invoke("glDrawArraysInstancedARB");
    read_stats(stats, &draws, &suppressed, &forwarded, &api_count, &api_suppressed);
    assert(hits(1) == 1 && hits(0) == 0);
    assert(draws == 1 && suppressed == 0 && forwarded == 1 && api_count == 1 && api_suppressed == 0);

    reset();
    arm(DROP_DRAWS);
    set_stub(1);
    invoke("glDrawArraysInstancedARB");
    read_stats(stats, &draws, &suppressed, &forwarded, &api_count, &api_suppressed);
    assert(hits(1) == 0 && hits(0) == 0);
    assert(draws == 1 && suppressed == 1 && forwarded == 0 && api_count == 1 && api_suppressed == 1);

    reset();
    arm(PROFILE);
    set_stub(0);
    invoke("glDrawArraysInstanced");
    read_stats(stats, &draws, &suppressed, &forwarded, &api_count, &api_suppressed);
    assert(hits(0) == 1 && hits(1) == 0);
    assert(draws == 1 && suppressed == 0 && forwarded == 1 && api_count == 1 && api_suppressed == 0);

    dlclose(library);
    puts("draw alias forwarding, DROP_DRAWS suppression, and distinct core/ARB stubs passed");
    return 0;
}
