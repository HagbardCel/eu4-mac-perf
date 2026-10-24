#include "eu4_gfx_capture_detour.h"
#include "eu4_gfx_live_capture.h"

#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <mach-o/getsect.h>
#include <mach-o/loader.h>
#include <mach/machine.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#define LIVE_DYLIB_PATH 1024u
#define LIVE_TARGET_RVA 0x15ec36aull
#define LIVE_MODULE_SHA \
    "b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d"

static bool write_status(const char *run_dir, const char *state,
                         const char *session, const char *executable,
                         uintptr_t image_base, uintptr_t site,
                         const char *error) {
    if (run_dir == NULL) {
        return false;
    }
    char final_path[LIVE_DYLIB_PATH];
    char partial_path[LIVE_DYLIB_PATH];
    int final_length = snprintf(final_path, sizeof(final_path),
                                "%s/startup.status", run_dir);
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
    fprintf(file, "EU4-GFX-LIVE-STARTUP v1\n");
    fprintf(file, "state=%s\n", state);
    fprintf(file, "session=%s\n", session != NULL ? session : "");
    fprintf(file, "pid=%d\n", (int)getpid());
    fprintf(file, "executable=%s\n", executable != NULL ? executable : "");
    fprintf(file, "arch=x86_64\n");
    fprintf(file, "image_base=0x%llx\n", (unsigned long long)image_base);
    fprintf(file, "site=0x%llx\n", (unsigned long long)site);
    fprintf(file, "module=%s\n", LIVE_MODULE_SHA);
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
                   uintptr_t *image_base, uintptr_t *site,
                   const char **error) {
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

    /* An inserted dylib may be image 0 under Rosetta. Find the executable by
     * path instead of assuming dyld's ordering. */
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
    uintptr_t target = base + (uintptr_t)LIVE_TARGET_RVA;
    unsigned long text_size = 0;
    const uint8_t *text =
        (const uint8_t *)getsectiondata(header64, "__TEXT", "__text",
                                        &text_size);
    if (text == NULL || text_size == 0 ||
        target < (uintptr_t)text ||
        target + EU4_GFX_CAPTURE_PATCH_SIZE >
            (uintptr_t)text + text_size) {
        *error = "site-outside-main-text";
        return false;
    }
    *image_base = base;
    *site = target;
    return true;
}

__attribute__((constructor)) static void initialize_capture(void) {
    const char *run_dir = getenv("EU4_GFX_CAPTURE_DIR");
    const char *control = getenv("EU4_GFX_CAPTURE_CONTROL");
    const char *session = getenv("EU4_GFX_CAPTURE_ID");
    const char *expected = getenv("EU4_GFX_EXPECTED_EXE");
    const char *deadline_text = getenv("EU4_GFX_CAPTURE_DEADLINE_S");
    char actual[PATH_MAX] = {0};
    uintptr_t image_base = 0;
    uintptr_t site = 0;
    const char *error = NULL;

    if (run_dir == NULL || control == NULL || session == NULL ||
        expected == NULL) {
        (void)write_status(run_dir, "error", session, actual, 0, 0,
                           "missing-environment");
        return;
    }
    if (!attest(expected, actual, sizeof(actual), &image_base, &site,
                &error)) {
        (void)write_status(run_dir, "error", session, actual, image_base, site,
                           error);
        return;
    }
    unsigned deadline = 120;
    if (deadline_text != NULL) {
        char *end = NULL;
        unsigned long value = strtoul(deadline_text, &end, 10);
        if (end == NULL || *end != '\0' || value == 0 || value > 3600) {
            (void)write_status(run_dir, "error", session, actual, image_base,
                               site, "invalid-deadline");
            return;
        }
        deadline = (unsigned)value;
    }
    if (!eu4_gfx_live_capture_start((void *)site, run_dir, control, session,
                                    deadline, false)) {
        (void)write_status(run_dir, "error", session, actual, image_base, site,
                           "capture-start");
        return;
    }
    (void)write_status(run_dir, "installed", session, actual, image_base, site,
                       "");
}

__attribute__((destructor)) static void finalize_capture(void) {
    eu4_gfx_live_capture_stop();
}
