/* Offline driver for the frame-trace writer/selection regressions.

   No OpenGL context is created.  The trace module's documented TEST_ONLY mode
   replaces the drawable readback with a synthetic reference and skips GL
   queries, so the real capture, selection, finalize and manifest paths run. */
#include "eu4_gfx_frame_trace.h"

#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

bool eu4_gfx_observe_read(const void *src, void *dest, size_t n) {
    (void)src;
    (void)dest;
    (void)n;
    return false;
}

bool eu4_gfx_observe_read_bytes(const void *src, void *dest, size_t n) {
    (void)src;
    (void)dest;
    (void)n;
    return false;
}

void eu4_gfx_observe_set_kind(eu4_gfx_observe_call *call, uint32_t kind) {
    if (call != NULL) {
        call->kind = kind;
    }
}

int eu4_gfx_frame_observe_init(const char *backend, const char *directory,
                               const char *session) {
    (void)backend;
    (void)directory;
    (void)session;
    return 0;
}

int eu4_gfx_frame_observe_wants(uint32_t scene) {
    (void)scene;
    return 0;
}

int eu4_gfx_frame_observe_lockstep_enabled(void) { return 0; }

int eu4_gfx_frame_observe_lockstep_wait(uint32_t scene, uint32_t timeout) {
    (void)scene;
    (void)timeout;
    return 0;
}

int eu4_gfx_frame_observe_emit_metadata(uint32_t width, uint32_t height,
                                        const char *origin, uint32_t scene,
                                        uint32_t draws, uint64_t signature,
                                        uint32_t distinct, int ok,
                                        const char *error) {
    (void)width;
    (void)height;
    (void)origin;
    (void)scene;
    (void)draws;
    (void)signature;
    (void)distinct;
    (void)ok;
    (void)error;
    return 0;
}

int eu4_gfx_frame_observe_emit(const uint8_t *pixels, uint32_t width,
                               uint32_t height, const char *origin,
                               uint32_t scene, uint32_t draws,
                               uint64_t signature, uint32_t distinct,
                               int content_ok, int health_ok,
                               const char *error) {
    (void)pixels;
    (void)width;
    (void)height;
    (void)origin;
    (void)scene;
    (void)draws;
    (void)signature;
    (void)distinct;
    (void)content_ok;
    (void)health_ok;
    (void)error;
    return 0;
}

int eu4_gfx_gl_drawable_extent(uint32_t *width, uint32_t *height) {
    if (width != NULL) {
        *width = 4;
    }
    if (height != NULL) {
        *height = 4;
    }
    return 0;
}

static void reset_env(void) {
    unsetenv("EU4_GFX_FRAME_TRACE_BOUNDARY");
    unsetenv("EU4_GFX_FRAME_TRACE_BOUNDARY_FB");
    unsetenv("EU4_GFX_FRAME_TRACE_CALLERS");
    unsetenv("EU4_GFX_FRAME_TRACE_MIN_DRAWS");
    unsetenv("EU4_GFX_FRAME_TRACE_MAX_FRAME_DRAWS");
    unsetenv("EU4_GFX_FRAME_TRACE_OCCURRENCE");
    unsetenv("EU4_GFX_FRAME_TRACE_AT_FRAME");
    unsetenv("EU4_GFX_FRAME_TRACE_MAX_DRAWS");
    unsetenv("EU4_GFX_FRAME_TRACE_TIMELINE");
    unsetenv("EU4_GFX_FRAME_TRACE_TEST_ONLY");
    unsetenv("EU4_GFX_FRAME_OBSERVE_DIR");
    unsetenv("EU4_GFX_FRAME_TRACE_TEST_FAIL_FLUSH");
}

static int configure(const char *directory) {
    reset_env();
    setenv("EU4_GFX_FRAME_TRACE_DIR", directory, 1);
    setenv("EU4_GFX_FRAME_TRACE_TEST_ONLY", "1", 1);
    return eu4_gfx_frame_trace_configure_from_env();
}

static void make_call(eu4_gfx_observe_call *call, uint64_t caller) {
    memset(call, 0, sizeof(*call));
    call->caller_rva = caller;
    call->caller_known = 1;
    call->call_id = 1;
    call->thread_id = 1;
}

static void draw(bool entry, uint64_t caller) {
    eu4_gfx_capture_frame frame;
    eu4_gfx_observe_call call;
    memset(&frame, 0, sizeof(frame));
    make_call(&call, caller);
    if (entry) {
        eu4_gfx_frame_trace_draw_entry(&frame, &call);
    } else {
        eu4_gfx_frame_trace_draw_exit(&frame, &call);
    }
}

static int arm_via_present(void) {
    eu4_gfx_capture_frame frame;
    eu4_gfx_observe_call call;
    memset(&frame, 0, sizeof(frame));
    make_call(&call, 0x1000);
    setenv("EU4_GFX_FRAME_TRACE_MIN_DRAWS", "1", 1);
    draw(true, 0x1000);
    draw(false, 0x1000);
    eu4_gfx_frame_trace_present_exit(&frame, &call, NULL);
    return 0;
}

static void finish_via_present(void) {
    eu4_gfx_capture_frame frame;
    eu4_gfx_observe_call call;
    memset(&frame, 0, sizeof(frame));
    make_call(&call, 0x1000);
    eu4_gfx_frame_trace_present_entry(&frame, &call);
}

static void valid_copy(int destination) {
    eu4_gfx_copy_event event;
    memset(&event, 0, sizeof(event));
    event.source_framebuffer = 0;
    event.read_buffer = 0x0405; /* GL_BACK */
    event.draw_buffer = 0x0405;
    event.double_buffered = 1;
    event.source_texture = 0;
    event.source_level = 0;
    event.source_width = 4;
    event.source_height = 4;
    event.source_internal_format = 0x8058;
    event.source_resolved = 1;
    event.destination_texture = destination;
    event.destination_level = 0;
    event.destination_width = 4;
    event.destination_height = 4;
    event.destination_internal_format = 0x8058;
    event.destination_resolved = 1;
    event.x = 1;
    event.y = 1;
    event.width = 2;
    event.height = 2;
    event.dx = 1;
    event.dy = 1;
    (void)eu4_gfx_frame_trace_copy_event(&event);
}

static int append_line(const char *directory, const char *line) {
    char path[1024];
    snprintf(path, sizeof(path), "%s/frame-events.jsonl", directory);
    FILE *file = fopen(path, "a");
    if (file == NULL) {
        return -1;
    }
    fputs(line, file);
    fclose(file);
    return 0;
}

static int scenario_copy(const char *directory, int capacity) {
    reset_env();
    setenv("EU4_GFX_FRAME_TRACE_DIR", directory, 1);
    setenv("EU4_GFX_FRAME_TRACE_TEST_ONLY", "1", 1);
    if (capacity) {
        setenv("EU4_GFX_FRAME_TRACE_MAX_DRAWS", "1", 1);
    }
    if (eu4_gfx_frame_trace_configure_from_env() <= 0) {
        return 2;
    }
    arm_via_present();
    /* One draw inside the armed capture proves draws and copies share the
       ordered stream. */
    draw(true, 0x1000);
    draw(false, 0x1000);
    valid_copy(442);
    valid_copy(443);
    finish_via_present();
    return 0;
}

static int scenario_flush_fail(const char *directory) {
    if (configure(directory) <= 0) {
        return 2;
    }
    setenv("EU4_GFX_FRAME_TRACE_TEST_FAIL_FLUSH", "1", 1);
    /* Reconfigure after enabling the test-only forced flush failure. */
    if (eu4_gfx_frame_trace_configure_from_env() <= 0) {
        return 2;
    }
    arm_via_present();
    draw(true, 0x1000);
    draw(false, 0x1000);
    finish_via_present();
    return 0;
}

static int scenario_stream_corruption(const char *directory, const char *line) {
    if (configure(directory) <= 0) {
        return 2;
    }
    arm_via_present();
    draw(true, 0x1000);
    draw(false, 0x1000);
    if (append_line(directory, line) != 0) {
        return 3;
    }
    finish_via_present();
    return 0;
}

static int scenario_scene(const char *directory, int at_frame_reject) {
    reset_env();
    setenv("EU4_GFX_FRAME_TRACE_DIR", directory, 1);
    setenv("EU4_GFX_FRAME_TRACE_TEST_ONLY", "1", 1);
    setenv("EU4_GFX_FRAME_TRACE_BOUNDARY", "scene", 1);
    setenv("EU4_GFX_FRAME_TRACE_MIN_DRAWS", "2", 1);
    setenv("EU4_GFX_FRAME_TRACE_MAX_FRAME_DRAWS", "4", 1);
    setenv("EU4_GFX_FRAME_TRACE_OCCURRENCE", "2", 1);
    setenv("EU4_GFX_FRAME_TRACE_CALLERS", "0x1000", 1);
    setenv("EU4_GFX_FRAME_TRACE_TIMELINE", "1", 1);
    if (at_frame_reject) {
        setenv("EU4_GFX_FRAME_TRACE_AT_FRAME", "3", 1);
    }
    int configured = eu4_gfx_frame_trace_configure_from_env();
    if (at_frame_reject) {
        printf("configure_rejected=%d\n", configured);
        return configured < 0 ? 0 : 4;
    }
    if (configured <= 0) {
        return 2;
    }
    static const int draws_per_scene[] = {5, 1, 3, 3, 3, 3, 2};
    static const uint64_t callers[] = {0x1000, 0x1000, 0x1000, 0x2000, 0x1000,
                                       0x1000, 0x1000};
    eu4_gfx_capture_frame frame;
    eu4_gfx_observe_call call;
    memset(&frame, 0, sizeof(frame));
    make_call(&call, 0x1000);
    for (size_t scene = 0; scene < sizeof(draws_per_scene) / sizeof(int);
         scene++) {
        eu4_gfx_frame_trace_boundary_entry(&frame, &call);
        for (int draw_index = 0; draw_index < draws_per_scene[scene];
             draw_index++) {
            draw(true, callers[scene]);
            draw(false, callers[scene]);
        }
        eu4_gfx_frame_trace_boundary_exit(&frame, &call, NULL);
    }
    return 0;
}

int main(int argc, char **argv) {
    if (argc < 3) {
        fprintf(stderr, "usage: %s <scenario> <dir> [arg]\n", argv[0]);
        return 2;
    }
    const char *scenario = argv[1];
    const char *directory = argv[2];
    if (strcmp(scenario, "manifest") == 0) {
        return eu4_gfx_frame_trace_test_manifest(directory) == 0 ? 0 : 1;
    }
    if (strcmp(scenario, "copy-capacity") == 0) {
        return scenario_copy(directory, 1);
    }
    if (strcmp(scenario, "copy-valid") == 0) {
        return scenario_copy(directory, 0);
    }
    if (strcmp(scenario, "flush-fail") == 0) {
        return scenario_flush_fail(directory);
    }
    if (strcmp(scenario, "malformed") == 0) {
        return scenario_stream_corruption(directory, "not-json\n");
    }
    if (strcmp(scenario, "count-mismatch") == 0) {
        return scenario_stream_corruption(directory, "{\"kind\":\"draw\"}\n");
    }
    if (strcmp(scenario, "scene-sequence") == 0) {
        return scenario_scene(directory, 0);
    }
    if (strcmp(scenario, "scene-at-frame") == 0) {
        return scenario_scene(directory, 1);
    }
    fprintf(stderr, "unknown scenario %s\n", scenario);
    return 2;
}
