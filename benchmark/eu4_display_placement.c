/* Deterministic SDL window placement for background EU4 sessions.
 *
 * Patches the game's own SDL_CreateWindow call site (RVA 0x13cc1d7) and
 * rewrites the requested position before SDL creates the Cocoa window.  It
 * clears fullscreen flags so the drawable keeps the scenario's logical extent;
 * it never resizes the window.  Only active when EU4_DISPLAY_MODE=background.
 */

#include "../renderer/integration/eu4_metal_boot_patch.h"

#include <CoreGraphics/CoreGraphics.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define PLACEMENT_CALL_RVA 0x13cc1d7u
#define PLACEMENT_CREATE_RVA 0x12cadf0u
#define SDL_WINDOW_FULLSCREEN 0x00000001u
#define SDL_WINDOW_OPENGL 0x00000002u
#define SDL_WINDOW_FULLSCREEN_DESKTOP 0x00001000u

typedef void *(*CreateWindowFn)(const char *, int, int, int, int, uint32_t);

static const uint8_t kExpectedCall[5] = {0xe8, 0x14, 0xec, 0xef, 0xff};
static CreateWindowFn g_original;
static FILE *g_trace;
static CGDirectDisplayID g_display;
static int g_margin = 24;

static void trace_create(const char *title, int x, int y, int w, int h,
                         uint32_t flags) {
    if (g_trace == NULL) {
        return;
    }
    fprintf(g_trace,
            "{\"title\":\"%s\",\"x\":%d,\"y\":%d,\"w\":%d,\"h\":%d,"
            "\"flags\":%u}\n",
            title != NULL ? title : "", x, y, w, h, flags);
    fflush(g_trace);
}

static void *placed_create(const char *title, int x, int y, int w, int h,
                           uint32_t flags) {
    trace_create(title, x, y, w, h, flags);
    if (g_original == NULL) {
        return NULL;
    }
    if ((flags & (SDL_WINDOW_FULLSCREEN | SDL_WINDOW_FULLSCREEN_DESKTOP)) != 0) {
        flags &= ~(uint32_t)(SDL_WINDOW_FULLSCREEN |
                             SDL_WINDOW_FULLSCREEN_DESKTOP);
    }
    CGRect bounds = CGDisplayBounds(g_display);
    x = (int)bounds.origin.x + g_margin;
    y = (int)bounds.origin.y + g_margin;
    if (g_trace != NULL) {
        fprintf(g_trace,
                "{\"placed\":true,\"x\":%d,\"y\":%d,\"w\":%d,\"h\":%d,"
                "\"flags\":%u}\n",
                x, y, w, h, flags);
        fflush(g_trace);
    }
    return g_original(title, x, y, w, h, flags);
}

__attribute__((constructor)) static void install_placement(void) {
    const char *mode = getenv("EU4_DISPLAY_MODE");
    if (mode == NULL || strcmp(mode, "background") != 0) {
        return;
    }
    const char *display = getenv("EU4_DISPLAY_ID");
    if (display == NULL || display[0] == 0) {
        return;
    }
    g_display = (CGDirectDisplayID)strtoul(display, NULL, 0);
    const char *margin = getenv("EU4_DISPLAY_MARGIN");
    if (margin != NULL && margin[0] != 0) {
        g_margin = (int)strtol(margin, NULL, 0);
    }
    const char *path = getenv("EU4_DISPLAY_WINDOW_TRACE");
    if (path != NULL && path[0] != 0) {
        g_trace = fopen(path, "a");
    }
    const struct mach_header_64 *header =
        (const struct mach_header_64 *)_dyld_get_image_header(0);
    if (header == NULL) {
        return;
    }
    uintptr_t slide = (uintptr_t)_dyld_get_image_vmaddr_slide(0);
    void *callsite = (void *)((uintptr_t)header + slide + PLACEMENT_CALL_RVA);
    void *expected_target = (void *)((uintptr_t)header + slide +
                                     PLACEMENT_CREATE_RVA);
    eu4_metal_boot_patch patch = {0};
    if (!eu4_metal_boot_install(callsite, kExpectedCall, expected_target,
                                (void *)placed_create, &patch)) {
        return;
    }
    g_original = (CreateWindowFn)patch.original;
}
