#include "eu4_gfx_startup_trace.h"

#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <mach-o/getsect.h>
#include <mach-o/loader.h>
#include <mach/machine.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#define STARTUP_PATH 1024u
#define STARTUP_MODULE_SHA \
    "b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d"

static const uint8_t kPatch[EU4_GFX_CAPTURE_PATCH_SIZE] = {
    0x55, 0x48, 0x89, 0xe5, 0x41, 0x57, 0x41, 0x56, 0x41, 0x55, 0x41, 0x54, 0x53,
};

static char g_run_dir[STARTUP_PATH];
static char g_session[256];
static char g_sha[80];
static uintptr_t g_image_base;
static int g_pid;
static pthread_t g_finalizer;

static bool write_status(const char *run_dir, const char *state,
                         const char *session, const char *executable,
                         uintptr_t image_base, uintptr_t settings_site,
                         uintptr_t init_site, const char *error) {
    if (run_dir == NULL) {
        return false;
    }
    char final_path[STARTUP_PATH];
    char partial_path[STARTUP_PATH];
    int final_length =
        snprintf(final_path, sizeof(final_path), "%s/startup.status", run_dir);
    int partial_length = snprintf(partial_path, sizeof(partial_path),
                                  "%s/startup.status.partial", run_dir);
    if (final_length < 0 || (size_t)final_length >= sizeof(final_path) ||
        partial_length < 0 ||
        (size_t)partial_length >= sizeof(partial_path)) {
        return false;
    }
    int descriptor = open(partial_path, O_WRONLY | O_CREAT | O_TRUNC, 0600);
    if (descriptor < 0) {
        return false;
    }
    FILE *file = fdopen(descriptor, "w");
    if (file == NULL) {
        (void)close(descriptor);
        return false;
    }
    fprintf(file, "EU4-GFX-STARTUP v1\n");
    fprintf(file, "state=%s\n", state);
    fprintf(file, "session=%s\n", session != NULL ? session : "");
    fprintf(file, "pid=%d\n", (int)getpid());
    fprintf(file, "executable=%s\n", executable != NULL ? executable : "");
    fprintf(file, "arch=x86_64\n");
    fprintf(file, "image_base=0x%llx\n", (unsigned long long)image_base);
    fprintf(file, "settings_site=0x%llx\n", (unsigned long long)settings_site);
    fprintf(file, "init_site=0x%llx\n", (unsigned long long)init_site);
    fprintf(file, "module=%s\n", STARTUP_MODULE_SHA);
    fprintf(file, "error=%s\n", error != NULL ? error : "");
    fprintf(file, "END\n");
    bool ok = ferror(file) == 0;
    if (ok && fflush(file) != 0) {
        ok = false;
    }
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
                   uintptr_t *image_base, uintptr_t *settings_site,
                   uintptr_t *init_site, uintptr_t *text_begin,
                   uintptr_t *text_end, const char **error) {
    uint32_t size = (uint32_t)actual_size;
    if (_NSGetExecutablePath(actual, &size) != 0) {
        *error = "executable-path-size";
        return false;
    }
    char resolved_actual[PATH_MAX];
    char resolved_expected[PATH_MAX];
    if (realpath(actual, resolved_actual) == NULL) {
        *error = "executable-path-unresolved";
        return false;
    }
    if (realpath(expected, resolved_expected) == NULL) {
        *error = "expected-path-unresolved";
        return false;
    }
    if (strcmp(resolved_actual, resolved_expected) != 0) {
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
        if (name == NULL || realpath(name, resolved_image) == NULL) {
            continue;
        }
        if (strcmp(resolved_image, resolved_expected) == 0) {
            header64 = candidate64;
            break;
        }
    }
    if (header64 == NULL) {
        *error = "main-image-missing";
        return false;
    }
    uintptr_t base = (uintptr_t)header64;
    uintptr_t settings = base + (uintptr_t)EU4_GFX_STARTUP_RVA_SETTINGS;
    uintptr_t init = base + (uintptr_t)EU4_GFX_STARTUP_RVA_INIT;
    unsigned long text_size = 0;
    const uint8_t *text =
        (const uint8_t *)getsectiondata(header64, "__TEXT", "__text",
                                        &text_size);
    if (text == NULL || text_size == 0) {
        *error = "text-missing";
        return false;
    }
    uintptr_t text_lo = (uintptr_t)text;
    uintptr_t text_hi = text_lo + text_size;
    if (settings < text_lo ||
        settings + EU4_GFX_CAPTURE_PATCH_SIZE > text_hi || init < text_lo ||
        init + EU4_GFX_CAPTURE_PATCH_SIZE > text_hi) {
        *error = "site-outside-main-text";
        return false;
    }
    if (memcmp((const void *)settings, kPatch, sizeof(kPatch)) != 0 ||
        memcmp((const void *)init, kPatch, sizeof(kPatch)) != 0) {
        *error = "unexpected-entry-bytes";
        return false;
    }
    *image_base = base;
    *settings_site = settings;
    *init_site = init;
    *text_begin = text_lo;
    *text_end = text_hi;
    return true;
}

static void *finalizer_main(void *arg) {
    (void)arg;
    unsigned deadline_s = 180;
    const char *text = getenv("EU4_GFX_STARTUP_DEADLINE_S");
    if (text != NULL) {
        char *end = NULL;
        unsigned long value = strtoul(text, &end, 10);
        if (end != NULL && *end == '\0' && value > 0 && value <= 3600) {
            deadline_s = (unsigned)value;
        }
    }
    unsigned waited = 0;
    while (waited < deadline_s * 1000u) {
        if (eu4_gfx_startup_state() == EU4_GFX_STARTUP_SEALED) {
            break;
        }
        if (eu4_gfx_startup_state() == EU4_GFX_STARTUP_ARMED &&
            waited > deadline_s * 500u) {
            eu4_gfx_startup_request_drain();
        }
        usleep(1000);
        waited += 1;
    }
    if (eu4_gfx_startup_state() != EU4_GFX_STARTUP_SEALED) {
        eu4_gfx_startup_request_drain();
        (void)eu4_gfx_startup_wait_sealed(2000);
    }
    (void)eu4_gfx_startup_finalize(g_run_dir, g_session, g_sha, g_pid,
                                   g_image_base);
    return NULL;
}

__attribute__((constructor)) static void initialize_startup_trace(void) {
    const char *run_dir = getenv("EU4_GFX_STARTUP_DIR");
    const char *session = getenv("EU4_GFX_STARTUP_ID");
    const char *expected = getenv("EU4_GFX_EXPECTED_EXE");
    const char *sha = getenv("EU4_GFX_EXECUTABLE_SHA256");
    char actual[PATH_MAX] = {0};
    uintptr_t image_base = 0;
    uintptr_t settings_site = 0;
    uintptr_t init_site = 0;
    uintptr_t text_begin = 0;
    uintptr_t text_end = 0;
    const char *error = NULL;

    if (run_dir == NULL || session == NULL || expected == NULL) {
        (void)write_status(run_dir, "error", session, actual, 0, 0, 0,
                           "missing-environment");
        return;
    }
    if (snprintf(g_run_dir, sizeof(g_run_dir), "%s", run_dir) < 0 ||
        snprintf(g_session, sizeof(g_session), "%s", session) < 0) {
        (void)write_status(run_dir, "error", session, actual, 0, 0, 0,
                           "path-copy");
        return;
    }
    if (sha != NULL) {
        (void)snprintf(g_sha, sizeof(g_sha), "%s", sha);
    }
    g_pid = (int)getpid();
    if (!attest(expected, actual, sizeof(actual), &image_base, &settings_site,
                &init_site, &text_begin, &text_end, &error)) {
        (void)write_status(run_dir, "error", session, actual, image_base,
                           settings_site, init_site, error);
        return;
    }
    g_image_base = image_base;
    eu4_gfx_startup_set_image_range(image_base, text_begin, text_end);
    eu4_gfx_startup_arm();
    if (!eu4_gfx_startup_install((void *)settings_site, (void *)init_site, kPatch,
                                 kPatch)) {
        (void)write_status(run_dir, "error", session, actual, image_base,
                           settings_site, init_site, "install-failed");
        return;
    }
    if (pthread_create(&g_finalizer, NULL, finalizer_main, NULL) != 0) {
        (void)write_status(run_dir, "error", session, actual, image_base,
                           settings_site, init_site, "finalizer");
        return;
    }
    (void)write_status(run_dir, "installed", session, actual, image_base,
                       settings_site, init_site, "");
}

__attribute__((destructor)) static void finalize_startup_trace(void) {
    eu4_gfx_startup_request_drain();
    (void)eu4_gfx_startup_wait_sealed(2000);
    if (g_finalizer != 0) {
        (void)pthread_join(g_finalizer, NULL);
        g_finalizer = 0;
        return;
    }
    /* Finalizer never started: this thread owns a single finalize. */
    (void)eu4_gfx_startup_finalize(g_run_dir, g_session, g_sha, g_pid,
                                   g_image_base);
}
