#include "eu4_gfx_draw_indexed_observe.h"

#include <fcntl.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <mach-o/getsect.h>
#include <mach-o/loader.h>
#include <mach/machine.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

#define DRAW_PATH 1024u
#define DRAW_TARGET_RVA 0x15eaa77ull
#define DRAW_MODULE_SHA \
    "b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d"

static const uint8_t kEntry[EU4_GFX_CAPTURE_PATCH_SIZE] = {
    0x55, 0x48, 0x89, 0xe5, 0x41, 0x57, 0x41, 0x56,
    0x41, 0x55, 0x41, 0x54, 0x53,
};

static _Atomic uint64_t *flush_word;
static char samples_path[DRAW_PATH];
static char ready_path[DRAW_PATH];
static pthread_t writer_thread;

static bool write_status(const char *run_dir, const char *state,
                         const char *session, const char *executable,
                         uintptr_t image_base, uintptr_t site,
                         const char *error) {
    if (run_dir == NULL) {
        return false;
    }
    char final_path[DRAW_PATH];
    char partial_path[DRAW_PATH];
    int final_length = snprintf(final_path, sizeof(final_path), "%s/startup.status",
                                run_dir);
    int partial_length = snprintf(partial_path, sizeof(partial_path),
                                  "%s/startup.status.partial", run_dir);
    if (final_length < 0 || (size_t)final_length >= sizeof(final_path) ||
        partial_length < 0 || (size_t)partial_length >= sizeof(partial_path)) {
        return false;
    }
    FILE *file = fopen(partial_path, "w");
    if (file == NULL) {
        return false;
    }
    fprintf(file, "EU4-GFX-DRAW-STARTUP v1\n");
    fprintf(file, "state=%s\n", state);
    fprintf(file, "session=%s\n", session != NULL ? session : "");
    fprintf(file, "pid=%d\n", (int)getpid());
    fprintf(file, "executable=%s\n", executable != NULL ? executable : "");
    fprintf(file, "image_base=0x%llx\n", (unsigned long long)image_base);
    fprintf(file, "site=0x%llx\n", (unsigned long long)site);
    fprintf(file, "entry_rva=0x%llx\n", (unsigned long long)DRAW_TARGET_RVA);
    fprintf(file, "module=%s\n", DRAW_MODULE_SHA);
    fprintf(file, "error=%s\n", error != NULL ? error : "");
    fprintf(file, "END\n");
    bool ok = ferror(file) == 0 && fflush(file) == 0;
    if (ok && fsync(fileno(file)) != 0) {
        ok = false;
    }
    if (fclose(file) != 0) {
        ok = false;
    }
    if (!ok) {
        return false;
    }
    return rename(partial_path, final_path) == 0;
}

static bool attest(const char *expected, char *actual, size_t actual_size,
                   uintptr_t *image_base, uintptr_t *site, const char **error) {
    uint32_t size = (uint32_t)actual_size;
    if (_NSGetExecutablePath(actual, &size) != 0) {
        *error = "executable-path-size";
        return false;
    }
    char resolved_actual[PATH_MAX];
    char resolved_expected[PATH_MAX];
    if (realpath(actual, resolved_actual) == NULL ||
        realpath(expected, resolved_expected) == NULL ||
        strcmp(resolved_actual, resolved_expected) != 0) {
        *error = "executable-mismatch";
        return false;
    }
    if (snprintf(actual, actual_size, "%s", resolved_actual) < 0) {
        *error = "executable-path-copy";
        return false;
    }
    const struct mach_header_64 *header64 = NULL;
    uint32_t image_count = _dyld_image_count();
    for (uint32_t index = 0; index < image_count; index++) {
        const struct mach_header *candidate = _dyld_get_image_header(index);
        if (candidate == NULL || candidate->magic != MH_MAGIC_64) {
            continue;
        }
        const struct mach_header_64 *candidate64 =
            (const struct mach_header_64 *)candidate;
        if (candidate64->filetype != MH_EXECUTE ||
            candidate64->cputype != CPU_TYPE_X86_64) {
            continue;
        }
        const char *name = _dyld_get_image_name(index);
        char resolved_image[PATH_MAX];
        if (name == NULL || realpath(name, resolved_image) == NULL ||
            strcmp(resolved_image, resolved_expected) != 0) {
            continue;
        }
        header64 = candidate64;
        break;
    }
    if (header64 == NULL) {
        *error = "main-image-missing";
        return false;
    }
    uintptr_t base = (uintptr_t)header64;
    uintptr_t target = base + (uintptr_t)DRAW_TARGET_RVA;
    unsigned long text_size = 0;
    const uint8_t *text = (const uint8_t *)getsectiondata(
        header64, "__TEXT", "__text", &text_size);
    if (text == NULL || text_size == 0 || target < (uintptr_t)text ||
        target + EU4_GFX_CAPTURE_PATCH_SIZE > (uintptr_t)text + text_size) {
        *error = "site-outside-main-text";
        return false;
    }
    if (memcmp((const void *)target, kEntry, sizeof kEntry) != 0) {
        *error = "entry-bytes";
        return false;
    }
    *image_base = base;
    *site = target;
    return true;
}

static int reserved_outstanding(void) {
    uint64_t posted = eu4_gfx_draw_indexed_observe_post_ready();
    unsigned limit = posted > EU4_GFX_DRAW_OBSERVE_SAMPLES
                         ? EU4_GFX_DRAW_OBSERVE_SAMPLES
                         : (unsigned)posted;
    for (unsigned index = 0; index < limit; index++) {
        if (eu4_gfx_draw_indexed_observe_slot_state(index) == 1) {
            return 1;
        }
    }
    return 0;
}

static bool publish_samples(void) {
    for (int spin = 0; spin < 1000 && reserved_outstanding(); spin++) {
        usleep(1000);
    }
    char partial[DRAW_PATH];
    int length = snprintf(partial, sizeof partial, "%s.partial", samples_path);
    if (length < 0 || (size_t)length >= sizeof partial) {
        return false;
    }
    FILE *file = fopen(partial, "w");
    if (file == NULL) {
        return false;
    }
    bool ok = eu4_gfx_draw_indexed_observe_write(file);
    if (ok && fflush(file) != 0) {
        ok = false;
    }
    if (ok && fsync(fileno(file)) != 0) {
        ok = false;
    }
    if (fclose(file) != 0) {
        ok = false;
    }
    if (!ok || rename(partial, samples_path) != 0) {
        return false;
    }
    FILE *ready = fopen(ready_path, "w");
    if (ready == NULL) {
        return false;
    }
    fputs("ok\n", ready);
    bool ready_ok = fflush(ready) == 0 && fsync(fileno(ready)) == 0;
    if (fclose(ready) != 0) {
        ready_ok = false;
    }
    return ready_ok;
}

static void *writer_main(void *unused) {
    (void)unused;
    for (;;) {
        unsigned done = eu4_gfx_draw_indexed_observe_completed();
        uint64_t flush = flush_word == NULL
                             ? 0
                             : atomic_load_explicit(flush_word, memory_order_acquire);
        if (done >= EU4_GFX_DRAW_OBSERVE_SAMPLES || flush != 0) {
            (void)publish_samples();
            break;
        }
        usleep(20000);
    }
    return NULL;
}

__attribute__((constructor)) static void initialize_draw_observe(void) {
    const char *run_dir = getenv("EU4_GFX_DRAW_DIR");
    const char *control = getenv("EU4_GFX_DRAW_CONTROL");
    const char *session = getenv("EU4_GFX_DRAW_ID");
    const char *expected = getenv("EU4_GFX_EXPECTED_EXE");
    char actual[PATH_MAX] = {0};
    uintptr_t image_base = 0;
    uintptr_t site = 0;
    const char *error = NULL;

    if (run_dir == NULL || control == NULL || session == NULL || expected == NULL) {
        (void)write_status(run_dir, "error", session, actual, 0, 0,
                           "missing-environment");
        return;
    }
    int samples_length = snprintf(samples_path, sizeof samples_path, "%s/samples.txt",
                                  run_dir);
    int ready_length = snprintf(ready_path, sizeof ready_path, "%s/samples.ready",
                                run_dir);
    if (samples_length < 0 || (size_t)samples_length >= sizeof samples_path ||
        ready_length < 0 || (size_t)ready_length >= sizeof ready_path) {
        (void)write_status(run_dir, "error", session, actual, 0, 0, "path");
        return;
    }
    if (!attest(expected, actual, sizeof actual, &image_base, &site, &error)) {
        (void)write_status(run_dir, "error", session, actual, image_base, site, error);
        return;
    }
    int descriptor = open(control, O_RDWR);
    if (descriptor < 0) {
        (void)write_status(run_dir, "error", session, actual, image_base, site,
                           "control-open");
        return;
    }
    void *mapped = mmap(NULL, 4096, PROT_READ | PROT_WRITE, MAP_SHARED, descriptor, 0);
    (void)close(descriptor);
    if (mapped == MAP_FAILED) {
        (void)write_status(run_dir, "error", session, actual, image_base, site,
                           "control-map");
        return;
    }
    _Atomic uint64_t *words = mapped;
    eu4_gfx_draw_indexed_observe_bind(&words[0]);
    flush_word = &words[1];
    eu4_gfx_capture_entry_ptr = eu4_gfx_draw_indexed_observe_entry;
    if (!eu4_gfx_capture_install((void *)site, kEntry)) {
        (void)write_status(run_dir, "error", session, actual, image_base, site,
                           "install");
        return;
    }
    if (pthread_create(&writer_thread, NULL, writer_main, NULL) != 0) {
        (void)write_status(run_dir, "error", session, actual, image_base, site,
                           "writer");
        return;
    }
    (void)write_status(run_dir, "installed", session, actual, image_base, site, "");
}
