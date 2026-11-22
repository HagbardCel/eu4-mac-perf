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
    unsetenv("EU4_GFX_FRAME_TRACE_TEST_FAIL_REFERENCE");
    unsetenv("EU4_GFX_FRAME_TRACE_TEST_FAIL_MANIFEST_HASH");
    unsetenv("EU4_GFX_FRAME_TRACE_TEST_FAIL_EVENT_DIGEST");
    unsetenv("EU4_GFX_FRAME_TRACE_TIMEOUT_MS");
    unsetenv("EU4_GFX_FRAME_SEAL");
    unsetenv("EU4_GFX_FRAME_OBSERVE_DIR");
    unsetenv("EU4_GFX_FRAME_TRACE_TEST_FAIL_FLUSH");
    unsetenv("EU4_GFX_FRAME_TRACE_CONTINUOUS");
    unsetenv("EU4_GFX_FRAME_TRACE_MAX_FRAMES");
    unsetenv("EU4_GFX_FRAME_TRACE_MAX_PENDING");
    unsetenv("EU4_GFX_FRAME_TRACE_MAX_BYTES");
    unsetenv("EU4_GFX_FRAME_TRACE_DEFAULT_CHECKPOINT_EVENT");
    unsetenv("EU4_GFX_FRAME_TRACE_DEFAULT_CHECKPOINT_EVENTS");
    unsetenv("EU4_GFX_FRAME_TRACE_MAX_CHECKPOINTS");
    unsetenv("EU4_GFX_FRAME_TRACE_CHECKPOINT_MAX_BYTES");
    unsetenv("EU4_GFX_FRAME_TRACE_PREFIX_END");
    unsetenv("EU4_GFX_FRAME_TRACE_PREFIX_MAX_BYTES");
    unsetenv("EU4_GFX_FRAME_TRACE_PREFIX_MAX_NS");
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

static int scenario_scene(const char *directory, int at_frame_reject) {    reset_env();
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
        /* PresentScene brackets the end of a rendered frame: draws first, then
           entry, then exit (which may call _GfxPresent). */
        for (int draw_index = 0; draw_index < draws_per_scene[scene];
             draw_index++) {
            draw(true, callers[scene]);
            draw(false, callers[scene]);
        }
        eu4_gfx_frame_trace_boundary_entry(&frame, &call);
        eu4_gfx_frame_trace_boundary_exit(&frame, &call, NULL);
    }
    return 0;
}

typedef struct {
    int calls;
    int lock_free;
    int files_ready;
    int abi_ok;
    int reenter;
    int fail;
    eu4_frame_capture_descriptor descriptor;
} seal_state;

static int seal_test_callback(const eu4_frame_capture_descriptor *descriptor,
                              void *context) {
    seal_state *state = context;
    state->calls++;
    state->lock_free = eu4_gfx_frame_trace_lock_probe();
    state->abi_ok = descriptor->struct_size == sizeof(*descriptor) &&
                    descriptor->abi_version == EU4_FRAME_CAPTURE_ABI_VERSION;
    state->descriptor = *descriptor;
    char manifest[2048];
    char reference[2048];
    snprintf(manifest, sizeof(manifest), "%s/%s", descriptor->capture_dir,
             descriptor->manifest_file);
    snprintf(reference, sizeof(reference), "%s/%s", descriptor->capture_dir,
             descriptor->reference_file);
    state->files_ready = access(manifest, R_OK) == 0 &&
                         access(reference, R_OK) == 0;
    if (state->reenter) {
        eu4_gfx_frame_sealed_dispatch();
    }
    return state->fail ? 3 : 0;
}

static void draw_indexed(bool entry) {
    eu4_gfx_capture_frame frame;
    eu4_gfx_observe_call call;
    memset(&frame, 0, sizeof(frame));
    make_call(&call, 0x2000);
    if (entry) {
        eu4_gfx_frame_trace_draw_indexed_entry(&frame, &call);
    } else {
        eu4_gfx_frame_trace_draw_indexed_exit(&frame, &call, NULL);
    }
}

static void clear_one(void) {
    eu4_gfx_capture_frame frame;
    eu4_gfx_observe_call call;
    memset(&frame, 0, sizeof(frame));
    memset(&call, 0, sizeof(call));
    call.caller_rva = 0x1000;
    call.caller_known = 1;
    call.call_id = 1;
    frame.rsi = 1;
    eu4_gfx_frame_trace_clear_entry(&frame, &call);
    eu4_gfx_frame_trace_clear_exit(&frame, &call, NULL);
}


static int configure_prefix(const char *directory, const char *end) {
    reset_env();
    setenv("EU4_GFX_FRAME_TRACE_DIR", directory, 1);
    setenv("EU4_GFX_FRAME_TRACE_TEST_ONLY", "1", 1);
    setenv("EU4_GFX_FRAME_TRACE_PREFIX_END", end, 1);
    setenv("EU4_GFX_FRAME_TRACE_PREFIX_MAX_BYTES", "1048576", 1);
    return eu4_gfx_frame_trace_configure_from_env();
}

/* Exact-prefix mode captures one post-event observation for every executed
   operation from event 0 through the declared end, plus the initial pre-event-0
   state.  Clear events are observed on their exit hook, draws on draw exit. */
static int scenario_prefix(const char *directory, const char *end,
                           int target_framebuffer) {
    if (configure_prefix(directory, end) <= 0) {
        return 2;
    }
    if (target_framebuffer >= 0) {
        eu4_gfx_frame_trace_test_set_target(target_framebuffer);
    }
    arm_via_present();
    clear_one();
    draw(true, 0x1000);
    draw(false, 0x1000);
    clear_one();
    finish_via_present();
    return 0;
}

/* The same mode must fail closed when an executed prefix event has no
   observation (here event 2 is never executed before finalization). */
static int scenario_prefix_incomplete(const char *directory) {
    if (configure_prefix(directory, "2") <= 0) {
        return 2;
    }
    arm_via_present();
    clear_one();
    draw(true, 0x1000);
    draw(false, 0x1000);
    finish_via_present();
    return 0;
}

/* Copies are observed after the real transfer, matching the post-event
   boundary the Metal replay checkpoints. */
/* The test-only target override simulates an R32F colour attachment so the
   format-aware observation path is exercised without a GL context. */
static int scenario_prefix_r32f(const char *directory) {
    if (configure_prefix(directory, "0") <= 0) {
        return 2;
    }
    arm_via_present();
    eu4_gfx_frame_trace_test_set_target(6);
    eu4_gfx_frame_trace_test_set_target_format(0x822E);
    draw(true, 0x1000);
    draw(false, 0x1000);
    finish_via_present();
    return 0;
}

static int scenario_prefix_copy(const char *directory) {
    if (configure_prefix(directory, "1") <= 0) {
        return 2;
    }
    arm_via_present();
    draw(true, 0x1000);
    draw(false, 0x1000);
    valid_copy(442);
    eu4_gfx_frame_trace_copy_exit();
    finish_via_present();
    return 0;
}

static int configure_checkpoint(const char *directory, const char *event) {
    reset_env();
    setenv("EU4_GFX_FRAME_TRACE_DIR", directory, 1);
    setenv("EU4_GFX_FRAME_TRACE_TEST_ONLY", "1", 1);
    setenv("EU4_GFX_FRAME_TRACE_DEFAULT_CHECKPOINT_EVENT", event, 1);
    return eu4_gfx_frame_trace_configure_from_env();
}

/* The selected checkpoint is captured on entry, before the draw record (which
   is emitted on exit) and before the draw itself. */
/* A checkpoint selected at an event that writes an offscreen FBO is a
   configuration error and must fail the frame, not label fb:0 as its prestate. */
static int scenario_checkpoint_offscreen(const char *directory) {
    if (configure_checkpoint(directory, "1") <= 0) {
        return 2;
    }
    arm_via_present();
    draw(true, 0x1000);
    eu4_gfx_frame_trace_test_set_target(10);
    draw(false, 0x1000);
    draw(true, 0x1000);
    draw(false, 0x1000);
    eu4_gfx_frame_trace_test_set_target(-1);
    finish_via_present();
    return 0;
}

static int scenario_checkpoint_draw(const char *directory) {
    if (configure_checkpoint(directory, "1") <= 0) {
        return 2;
    }
    arm_via_present();
    draw(true, 0x1000);
    draw(false, 0x1000);
    if (eu4_gfx_frame_trace_test_checkpoint_captured()) {
        return 4;
    }
    draw(true, 0x1000);
    if (!eu4_gfx_frame_trace_test_checkpoint_captured()) {
        return 5;
    }
    draw(false, 0x1000);
    finish_via_present();
    return 0;
}

static int scenario_checkpoint_clear(const char *directory) {
    if (configure_checkpoint(directory, "1") <= 0) {
        return 2;
    }
    arm_via_present();
    draw(true, 0x1000);
    draw(false, 0x1000);
    if (eu4_gfx_frame_trace_test_checkpoint_captured()) {
        return 4;
    }
    clear_one();
    if (!eu4_gfx_frame_trace_test_checkpoint_captured()) {
        return 5;
    }
    finish_via_present();
    return 0;
}

static int scenario_checkpoint_copy(const char *directory) {
    if (configure_checkpoint(directory, "1") <= 0) {
        return 2;
    }
    arm_via_present();
    draw(true, 0x1000);
    draw(false, 0x1000);
    if (eu4_gfx_frame_trace_test_checkpoint_captured()) {
        return 4;
    }
    valid_copy(442);
    if (!eu4_gfx_frame_trace_test_checkpoint_captured()) {
        return 5;
    }
    finish_via_present();
    return 0;
}

static int scenario_checkpoint_missed(const char *directory) {
    if (configure_checkpoint(directory, "5") <= 0) {
        return 2;
    }
    arm_via_present();
    draw(true, 0x1000);
    draw(false, 0x1000);
    finish_via_present();
    return 0;
}

static int scenario_checkpoint_duplicate(const char *directory) {
    if (configure_checkpoint(directory, "0") <= 0) {
        return 2;
    }
    arm_via_present();
    if (eu4_gfx_frame_trace_test_checkpoint_hook() != 1) {
        return 4;
    }
    draw(true, 0x1000);
    draw(false, 0x1000);
    finish_via_present();
    return 0;
}

/* The boundary-clear capture is armed by one clear of the configured
   framebuffer and finalized by the next one.  The pre-draw satisfies min_draws
   before the opening clear; it is not part of the captured event stream. */
static int setup_boundary_checkpoint(const char *directory, const char *event) {
    reset_env();
    setenv("EU4_GFX_FRAME_TRACE_DIR", directory, 1);
    setenv("EU4_GFX_FRAME_TRACE_TEST_ONLY", "1", 1);
    setenv("EU4_GFX_FRAME_SEAL", "1", 1);
    setenv("EU4_GFX_FRAME_TRACE_BOUNDARY_FB", "0", 1);
    setenv("EU4_GFX_FRAME_TRACE_MIN_DRAWS", "1", 1);
    setenv("EU4_GFX_FRAME_TRACE_DEFAULT_CHECKPOINT_EVENT", event, 1);
    return eu4_gfx_frame_trace_configure_from_env();
}

static void pre_draw_for_selection(void) {
    draw(true, 0x1000);
    draw(false, 0x1000);
}

static int scenario_checkpoint_opening_clear(const char *directory) {
    if (setup_boundary_checkpoint(directory, "0") <= 0) {
        return 2;
    }
    pre_draw_for_selection();
    /* This clear opens the capture; the selected event is the clear itself and
       must be observed as event 0, after the frame has been armed. */
    clear_one();
    if (!eu4_gfx_frame_trace_test_checkpoint_captured()) {
        return 4;
    }
    draw(true, 0x1000);
    draw(false, 0x1000);
    /* This clear closes the capture and is not recorded. */
    clear_one();
    return 0;
}

static int scenario_checkpoint_closing_clear(const char *directory) {
    if (setup_boundary_checkpoint(directory, "1") <= 0) {
        return 2;
    }
    pre_draw_for_selection();
    clear_one(); /* opening clear, event 0 */
    draw(true, 0x1000);
    draw(false, 0x1000); /* last recorded event, selected here */
    if (!eu4_gfx_frame_trace_test_checkpoint_captured()) {
        return 4;
    }
    clear_one(); /* closing clear finalizes the frame, no event 2 */
    return 0;
}

static int scenario_checkpoint_closing_clear_selected(const char *directory) {
    if (setup_boundary_checkpoint(directory, "2") <= 0) {
        return 2;
    }
    pre_draw_for_selection();
    clear_one(); /* opening clear, event 0 */
    draw(true, 0x1000);
    draw(false, 0x1000); /* event 1 */
    if (eu4_gfx_frame_trace_test_checkpoint_captured()) {
        return 4;
    }
    clear_one(); /* closing clear would be event 2 but is not recorded */
    return 0;
}

static int scenario_checkpoint_out_of_range(const char *directory) {
    reset_env();
    setenv("EU4_GFX_FRAME_TRACE_DIR", directory, 1);
    setenv("EU4_GFX_FRAME_TRACE_TEST_ONLY", "1", 1);
    setenv("EU4_GFX_FRAME_TRACE_MAX_DRAWS", "4", 1);
    setenv("EU4_GFX_FRAME_TRACE_DEFAULT_CHECKPOINT_EVENT", "4", 1);
    int configured = eu4_gfx_frame_trace_configure_from_env();
    printf("configure_rejected=%d\n", configured);
    return configured < 0 ? 0 : 4;
}

static int configure_checkpoint_multi(const char *directory,
                                      const char *events,
                                      const char *max_count,
                                      const char *max_bytes) {
    reset_env();
    setenv("EU4_GFX_FRAME_TRACE_DIR", directory, 1);
    setenv("EU4_GFX_FRAME_TRACE_TEST_ONLY", "1", 1);
    setenv("EU4_GFX_FRAME_SEAL", "1", 1);
    setenv("EU4_GFX_FRAME_TRACE_DEFAULT_CHECKPOINT_EVENTS", events, 1);
    if (max_count != NULL) {
        setenv("EU4_GFX_FRAME_TRACE_MAX_CHECKPOINTS", max_count, 1);
    }
    if (max_bytes != NULL) {
        setenv("EU4_GFX_FRAME_TRACE_CHECKPOINT_MAX_BYTES", max_bytes, 1);
    }
    return eu4_gfx_frame_trace_configure_from_env();
}

/* Three selected checkpoints in one frame across the three supported
   pre-operation boundaries: draw, clear and copy. */
static int scenario_checkpoint_multi(const char *directory) {
    if (configure_checkpoint_multi(directory, "0,1,2", NULL, NULL) <= 0) {
        return 2;
    }
    arm_via_present();
    draw(true, 0x1000);
    if (!eu4_gfx_frame_trace_test_checkpoint_captured_at(0)) {
        return 4;
    }
    draw(false, 0x1000); /* event 0: draw */
    clear_one();         /* event 1: clear */
    if (!eu4_gfx_frame_trace_test_checkpoint_captured_at(1)) {
        return 5;
    }
    valid_copy(442);     /* event 2: copy */
    if (eu4_gfx_frame_trace_test_checkpoint_count() != 3 ||
        !eu4_gfx_frame_trace_test_checkpoint_captured_at(2)) {
        return 6;
    }
    finish_via_present();
    return 0;
}

/* A requested event beyond the observed frame may not be silently dropped. */
static int scenario_checkpoint_multi_missed(const char *directory) {
    if (configure_checkpoint_multi(directory, "0,3", NULL, NULL) <= 0) {
        return 2;
    }
    arm_via_present();
    draw(true, 0x1000);
    draw(false, 0x1000);
    clear_one();
    valid_copy(442);
    finish_via_present();
    return 0;
}

/* The aggregate readback budget is enforced before the second readback. */
static int scenario_checkpoint_multi_budget(const char *directory) {
    /* One checkpoint now costs colour + depth + stencil (144 bytes at 4x4);
       allow exactly one so the second is refused before its readback. */
    if (configure_checkpoint_multi(directory, "0,1", NULL, "200") <= 0) {
        return 2;
    }
    arm_via_present();
    draw(true, 0x1000);
    draw(false, 0x1000);
    clear_one();
    finish_via_present();
    return 0;
}

/* A captured checkpoint whose record is never emitted cannot be attached. */
static int scenario_checkpoint_multi_pending(const char *directory) {
    if (configure_checkpoint_multi(directory, "0", NULL, NULL) <= 0) {
        return 2;
    }
    arm_via_present();
    if (eu4_gfx_frame_trace_test_checkpoint_hook() != 1) {
        return 4;
    }
    finish_via_present();
    return 0;
}

static int reject_selection(const char *directory, const char *events,
                            const char *max_count) {
    int configured = configure_checkpoint_multi(directory, events, max_count,
                                                NULL);
    printf("configure_rejected=%d\n", configured);
    return configured < 0 ? 0 : 4;
}

static int scenario_checkpoint_multi_unordered(const char *directory) {
    return reject_selection(directory, "2,1", NULL);
}

static int scenario_checkpoint_multi_duplicate(const char *directory) {
    return reject_selection(directory, "1,1", NULL);
}

static int scenario_checkpoint_multi_empty(const char *directory) {
    return reject_selection(directory, "0,,1", NULL);
}

static int scenario_checkpoint_multi_over_count(const char *directory) {
    return reject_selection(directory, "0,1,2", "2");
}

static int scenario_checkpoint_multi_both_forms(const char *directory) {
    reset_env();
    setenv("EU4_GFX_FRAME_TRACE_DIR", directory, 1);
    setenv("EU4_GFX_FRAME_TRACE_TEST_ONLY", "1", 1);
    setenv("EU4_GFX_FRAME_TRACE_DEFAULT_CHECKPOINT_EVENT", "0", 1);
    setenv("EU4_GFX_FRAME_TRACE_DEFAULT_CHECKPOINT_EVENTS", "0,1", 1);
    int configured = eu4_gfx_frame_trace_configure_from_env();
    printf("configure_rejected=%d\n", configured);
    return configured < 0 ? 0 : 4;
}

static int scenario_continuous(const char *directory, int max_frames,
                               int max_bytes) {
    reset_env();
    setenv("EU4_GFX_FRAME_TRACE_DIR", directory, 1);
    setenv("EU4_GFX_FRAME_TRACE_TEST_ONLY", "1", 1);
    setenv("EU4_GFX_FRAME_TRACE_CONTINUOUS", "1", 1);
    setenv("EU4_GFX_FRAME_TRACE_BOUNDARY", "scene", 1);
    setenv("EU4_GFX_FRAME_TRACE_MIN_DRAWS", "1", 1);
    setenv("EU4_GFX_FRAME_SEAL", "1", 1);
    char frames[32];
    snprintf(frames, sizeof(frames), "%d", max_frames);
    setenv("EU4_GFX_FRAME_TRACE_MAX_FRAMES", frames, 1);
    setenv("EU4_GFX_FRAME_TRACE_MAX_PENDING", "2", 1);
    char bytes[64];
    snprintf(bytes, sizeof(bytes), "%d", max_bytes);
    setenv("EU4_GFX_FRAME_TRACE_MAX_BYTES", bytes, 1);
    if (eu4_gfx_frame_trace_configure_from_env() != 1) {
        return 1;
    }
    eu4_gfx_capture_frame frame;
    eu4_gfx_observe_call call;
    memset(&frame, 0, sizeof(frame));
    make_call(&call, 0x2000);
    for (int scene = 0; scene < max_frames + 2; scene++) {
        draw(true, 0x2000);
        draw(false, 0x2000);
        eu4_gfx_frame_trace_boundary_entry(&frame, &call);
        eu4_gfx_frame_trace_boundary_exit(&frame, &call, NULL);
        if (scene == 1) {
            unsigned deleted = 443;
            eu4_gfx_frame_trace_texture_deleted(1, &deleted);
        }
    }
    return 0;
}

static int scenario_continuous_carried(const char *directory, int max_frames,
                                       int max_bytes, int lifecycle) {
    reset_env();
    setenv("EU4_GFX_FRAME_TRACE_DIR", directory, 1);
    setenv("EU4_GFX_FRAME_TRACE_TEST_ONLY", "1", 1);
    setenv("EU4_GFX_FRAME_TRACE_CONTINUOUS", "1", 1);
    setenv("EU4_GFX_FRAME_TRACE_BOUNDARY", "scene", 1);
    setenv("EU4_GFX_FRAME_TRACE_MIN_DRAWS", "1", 1);
    setenv("EU4_GFX_FRAME_SEAL", "1", 1);
    char frames[32];
    snprintf(frames, sizeof(frames), "%d", max_frames);
    setenv("EU4_GFX_FRAME_TRACE_MAX_FRAMES", frames, 1);
    setenv("EU4_GFX_FRAME_TRACE_MAX_PENDING", "2", 1);
    char bytes[64];
    snprintf(bytes, sizeof(bytes), "%d", max_bytes);
    setenv("EU4_GFX_FRAME_TRACE_MAX_BYTES", bytes, 1);
    if (eu4_gfx_frame_trace_configure_from_env() != 1) {
        return 1;
    }
    eu4_gfx_capture_frame frame;
    eu4_gfx_observe_call call;
    memset(&frame, 0, sizeof(frame));
    make_call(&call, 0x2000);
    for (int scene = 0; scene < max_frames + 2; scene++) {
        draw(true, 0x2000);
        draw(false, 0x2000);
        if (lifecycle == 2 && scene == 3) {
            /* Delete while the second captured frame is armed but before its
               end boundary.  The frame-start snapshot must still advertise
               the carried image for samples before this event. */
            unsigned deleted = 443;
            eu4_gfx_frame_trace_texture_deleted(1, &deleted);
        }
        eu4_gfx_frame_trace_boundary_entry(&frame, &call);
        eu4_gfx_frame_trace_boundary_exit(&frame, &call, NULL);
        /* Capture starts at the scene exit.  Writing the target on scene 1
           puts it in the first captured frame; frames 2 and 3 never write it,
           so only a persistent carried set can advertise it to frame 3. */
        if (scene == 0) {
            eu4_gfx_frame_trace_test_register_target("443");
        }
        if (lifecycle == 1 && scene == 1) {
            /* Delete and recreate the same numeric name.  The delete must be
               published in this frame's lifecycle and the target must be
               re-carried by its new write. */
            unsigned deleted = 443;
            eu4_gfx_frame_trace_texture_deleted(1, &deleted);
            eu4_gfx_frame_trace_test_register_target("443");
        }
    }
    return 0;
}

static int configure_continuous_test(const char *directory, int max_frames) {
    reset_env();
    setenv("EU4_GFX_FRAME_TRACE_DIR", directory, 1);
    setenv("EU4_GFX_FRAME_TRACE_TEST_ONLY", "1", 1);
    setenv("EU4_GFX_FRAME_TRACE_CONTINUOUS", "1", 1);
    setenv("EU4_GFX_FRAME_TRACE_BOUNDARY", "scene", 1);
    setenv("EU4_GFX_FRAME_TRACE_MIN_DRAWS", "1", 1);
    setenv("EU4_GFX_FRAME_SEAL", "1", 1);
    char frames[32];
    snprintf(frames, sizeof(frames), "%d", max_frames);
    setenv("EU4_GFX_FRAME_TRACE_MAX_FRAMES", frames, 1);
    setenv("EU4_GFX_FRAME_TRACE_MAX_PENDING", "2", 1);
    setenv("EU4_GFX_FRAME_TRACE_MAX_BYTES", "268435456", 1);
    return eu4_gfx_frame_trace_configure_from_env();
}

/* Arm one continuous frame, then perform the scenario inside that frame so
   event indices are the ones the lifecycle record publishes. */
static int arm_continuous_by_scene(void) {
    eu4_gfx_capture_frame frame;
    eu4_gfx_observe_call call;
    memset(&frame, 0, sizeof(frame));
    make_call(&call, 0x2000);
    draw(true, 0x2000);
    draw(false, 0x2000);
    eu4_gfx_frame_trace_boundary_entry(&frame, &call);
    eu4_gfx_frame_trace_boundary_exit(&frame, &call, NULL);
    return 0;
}

static int finish_continuous_by_scene(void) {
    eu4_gfx_capture_frame frame;
    eu4_gfx_observe_call call;
    memset(&frame, 0, sizeof(frame));
    make_call(&call, 0x2000);
    eu4_gfx_frame_trace_boundary_entry(&frame, &call);
    eu4_gfx_frame_trace_boundary_exit(&frame, &call, NULL);
    return 0;
}

static int scenario_continuous_reuse(const char *directory) {
    if (configure_continuous_test(directory, 1) != 1) {
        return 1;
    }
    arm_continuous_by_scene();
    /* Captured frame.  delete(2) -> draw(3) -> recreate/render(4) ->
       delete(5): the second deletion must not be folded into the first. */
    draw(true, 0x2000);
    draw(false, 0x2000);
    draw(true, 0x2000);
    draw(false, 0x2000);
    unsigned deleted = 443;
    eu4_gfx_frame_trace_texture_deleted(1, &deleted);
    draw(true, 0x2000);
    draw(false, 0x2000);
    eu4_gfx_frame_trace_test_register_target("443");
    draw(true, 0x2000);
    draw(false, 0x2000);
    eu4_gfx_frame_trace_texture_deleted(1, &deleted);
    finish_continuous_by_scene();
    return 0;
}

static int scenario_mutation_target(const char *directory) {
    if (configure_continuous_test(directory, 1) != 1) {
        return 1;
    }
    arm_continuous_by_scene();
    draw(true, 0x2000);
    draw(false, 0x2000);
    eu4_gfx_frame_trace_test_register_target("443");
    eu4_gfx_frame_trace_texture_mutated(443, 0, 1, 0x0DE1u);
    finish_continuous_by_scene();
    return 0;
}

static int scenario_mutation_cube(const char *directory) {
    if (configure_continuous_test(directory, 1) != 1) {
        return 1;
    }
    arm_continuous_by_scene();
    draw(true, 0x2000);
    draw(false, 0x2000);
    eu4_gfx_frame_trace_test_register_target("443");
    /* A cube face is the same object as the cube map; a reallocation is
       glTexImage2D, not glCopyTexImage2D. */
    eu4_gfx_frame_trace_texture_mutated(443, 0, 0, 0x8515u);
    finish_continuous_by_scene();
    return 0;
}

/* Lifecycle transitions that share one captured event boundary.  No draw is
   recorded between them, so only the per-transition sequence can preserve the
   real call order. */
static int scenario_lifecycle_order(const char *directory, const char *mode) {
    if (configure_continuous_test(directory, 1) != 1) {
        return 1;
    }
    arm_continuous_by_scene();
    /* Draws precede the transitions; no draw may sit between them. */
    draw(true, 0x2000);
    draw(false, 0x2000);
    unsigned name = 443;
    if (strcmp(mode, "delete-mutate-delete") == 0) {
        eu4_gfx_frame_trace_texture_deleted(1, &name);
        eu4_gfx_frame_trace_texture_mutated(443, 0, 0, 0x0DE1u);
        eu4_gfx_frame_trace_texture_deleted(1, &name);
    } else if (strcmp(mode, "delete-mutate") == 0) {
        eu4_gfx_frame_trace_texture_deleted(1, &name);
        eu4_gfx_frame_trace_texture_mutated(443, 0, 0, 0x0DE1u);
    } else if (strcmp(mode, "mutate-delete") == 0) {
        eu4_gfx_frame_trace_test_register_target("443");
        eu4_gfx_frame_trace_texture_mutated(443, 0, 0, 0x0DE1u);
        eu4_gfx_frame_trace_texture_deleted(1, &name);
    } else if (strcmp(mode, "two-calls") == 0) {
        eu4_gfx_frame_trace_texture_deleted(1, &name);
        eu4_gfx_frame_trace_texture_deleted(1, &name);
    } else if (strcmp(mode, "duplicate-call") == 0) {
        unsigned names[2] = {443, 443};
        eu4_gfx_frame_trace_texture_deleted(2, names);
    } else {
        return 2;
    }
    finish_continuous_by_scene();
    return 0;
}

static int scenario_capacity(const char *directory, const char *kind,
                             int count) {
    if (configure_continuous_test(directory, 1) != 1) {
        return 1;
    }
    arm_continuous_by_scene();
    draw(true, 0x2000);
    draw(false, 0x2000);
    if (strcmp(kind, "targets") == 0) {
        for (int i = 0; i < count; i++) {
            char name[32];
            snprintf(name, sizeof(name), "%d", 9000 + i);
            eu4_gfx_frame_trace_test_register_target(name);
        }
    } else if (strcmp(kind, "deletes") == 0) {
        for (int i = 0; i < count; i++) {
            unsigned name = (unsigned)(8000 + i);
            eu4_gfx_frame_trace_texture_deleted(1, &name);
        }
    } else if (strcmp(kind, "mutations") == 0) {
        eu4_gfx_frame_trace_test_register_target("443");
        for (int i = 0; i < count; i++) {
            eu4_gfx_frame_trace_texture_mutated(443, 0, 1, 0x0DE1u);
        }
    } else {
        return 2;
    }
    finish_continuous_by_scene();
    return 0;
}

static int scenario_carried_capacity(const char *directory) {
    if (configure_continuous_test(directory, 3) != 1) {
        return 1;
    }
    eu4_gfx_capture_frame frame;
    eu4_gfx_observe_call call;
    memset(&frame, 0, sizeof(frame));
    make_call(&call, 0x2000);
    /* Frame 1: 64 distinct renderer targets fill the carried set exactly. */
    draw(true, 0x2000);
    draw(false, 0x2000);
    eu4_gfx_frame_trace_boundary_entry(&frame, &call);
    eu4_gfx_frame_trace_boundary_exit(&frame, &call, NULL);
    for (int i = 0; i < 64; i++) {
        char name[32];
        snprintf(name, sizeof(name), "%d", 7000 + i);
        eu4_gfx_frame_trace_test_register_target(name);
    }
    draw(true, 0x2000);
    draw(false, 0x2000);
    eu4_gfx_frame_trace_boundary_entry(&frame, &call);
    eu4_gfx_frame_trace_boundary_exit(&frame, &call, NULL);
    /* Frame 2: one additional target.  Frame 3 merges it into the carried
       set, which would become 65 and must fail closed instead of silently
       omitting the resource. */
    draw(true, 0x2000);
    draw(false, 0x2000);
    eu4_gfx_frame_trace_test_register_target("6999");
    draw(true, 0x2000);
    draw(false, 0x2000);
    eu4_gfx_frame_trace_boundary_entry(&frame, &call);
    eu4_gfx_frame_trace_boundary_exit(&frame, &call, NULL);
    draw(true, 0x2000);
    draw(false, 0x2000);
    eu4_gfx_frame_trace_boundary_entry(&frame, &call);
    eu4_gfx_frame_trace_boundary_exit(&frame, &call, NULL);
    return 0;
}

static void report_seal(const char *scenario, const seal_state *state) {
    printf("scenario=%s callback_calls=%d lock_free=%d files_ready=%d abi_ok=%d reentrant=%u "
           "notifications=%u callback_status=%d seal_written=%d complete=%u "
           "ops=%llu draws=%llu clears=%llu copies=%llu frame_id=%llu "
           "width=%u height=%u boundary=%s reason=%s\n",
           scenario, state->calls, state->lock_free, state->files_ready,
           state->abi_ok, eu4_gfx_frame_sealed_reentrant_rejections(),
           eu4_gfx_frame_sealed_notifications(),
           eu4_gfx_frame_sealed_last_callback_status(),
           eu4_gfx_frame_sealed_last_seal_written(),
           state->descriptor.complete,
           (unsigned long long)state->descriptor.operation_count,
           (unsigned long long)state->descriptor.draw_count,
           (unsigned long long)state->descriptor.clear_count,
           (unsigned long long)state->descriptor.copy_count,
           (unsigned long long)state->descriptor.capture_frame_id,
           state->descriptor.width, state->descriptor.height,
           state->descriptor.boundary, state->descriptor.failure_reason);
}

/* A sealed 21-event prefix frame.  The synthetic readback is event-dependent,
   so every post-event observation is a distinct reference image. */
/* A sparse late-event selection: 130 events, observations only at 118, 120
   and 129.  No earlier post-event image is read back. */
static int scenario_prefix_late(const char *directory) {
    seal_state state;
    memset(&state, 0, sizeof(state));
    reset_env();
    setenv("EU4_GFX_FRAME_TRACE_DIR", directory, 1);
    setenv("EU4_GFX_FRAME_TRACE_TEST_ONLY", "1", 1);
    setenv("EU4_GFX_FRAME_SEAL", "1", 1);
    setenv("EU4_GFX_FRAME_TRACE_MIN_DRAWS", "1", 1);
    setenv("EU4_GFX_FRAME_TRACE_MAX_DRAWS", "140", 1);
    setenv("EU4_GFX_FRAME_TRACE_OBSERVE_EVENTS", "118,120,129", 1);
    setenv("EU4_GFX_FRAME_TRACE_PREFIX_MAX_BYTES", "1048576", 1);
    eu4_gfx_frame_trace_test_reset_seal();
    if (eu4_gfx_frame_sealed_subscribe(seal_test_callback, &state) != 0) {
        return 3;
    }
    if (eu4_gfx_frame_trace_configure_from_env() <= 0) {
        return 2;
    }
    arm_via_present();
    clear_one();
    for (int index = 1; index <= 129; index++) {
        draw(true, 0x1000);
        draw(false, 0x1000);
    }
    finish_via_present();
    report_seal("prefix-late", &state);
    return state.fail ? 3 : 0;
}

static int scenario_prefix_seal(const char *directory) {
    seal_state state;
    memset(&state, 0, sizeof(state));
    reset_env();
    setenv("EU4_GFX_FRAME_TRACE_DIR", directory, 1);
    setenv("EU4_GFX_FRAME_TRACE_TEST_ONLY", "1", 1);
    setenv("EU4_GFX_FRAME_SEAL", "1", 1);
    setenv("EU4_GFX_FRAME_TRACE_MIN_DRAWS", "1", 1);
    setenv("EU4_GFX_FRAME_TRACE_PREFIX_END", "20", 1);
    setenv("EU4_GFX_FRAME_TRACE_PREFIX_MAX_BYTES", "1048576", 1);
    eu4_gfx_frame_trace_test_reset_seal();
    if (eu4_gfx_frame_sealed_subscribe(seal_test_callback, &state) != 0) {
        return 3;
    }
    if (eu4_gfx_frame_trace_configure_from_env() <= 0) {
        return 2;
    }
    arm_via_present();
    /* Event 0 is a full default-framebuffer clear so the initial pre-frame
       state is provably overwritten before any draw consumes it. */
    clear_one();
    for (int index = 1; index <= 20; index++) {
        draw(true, 0x1000);
        draw(false, 0x1000);
    }
    finish_via_present();
    report_seal("prefix-seal", &state);
    return state.fail ? 3 : 0;
}

static int scenario_seal(const char *directory, const char *mode) {
    seal_state state;
    memset(&state, 0, sizeof(state));
    if (strcmp(mode, "reentrant") == 0) {
        state.reenter = 1;
    }
    if (strcmp(mode, "callback-fail") == 0) {
        state.fail = 1;
    }
    reset_env();
    setenv("EU4_GFX_FRAME_TRACE_DIR", directory, 1);
    setenv("EU4_GFX_FRAME_TRACE_TEST_ONLY", "1", 1);
    setenv("EU4_GFX_FRAME_SEAL", "1", 1);
    setenv("EU4_GFX_FRAME_TRACE_MIN_DRAWS", "1", 1);
    if (strcmp(mode, "incomplete") == 0) {
        setenv("EU4_GFX_FRAME_TRACE_MAX_DRAWS", "1", 1);
    }
    if (strcmp(mode, "no-reference") == 0) {
        setenv("EU4_GFX_FRAME_TRACE_TEST_FAIL_REFERENCE", "1", 1);
    }
    if (strcmp(mode, "manifest-hash") == 0) {
        setenv("EU4_GFX_FRAME_TRACE_TEST_FAIL_MANIFEST_HASH", "1", 1);
    }
    if (strcmp(mode, "event-digest") == 0) {
        setenv("EU4_GFX_FRAME_TRACE_TEST_FAIL_EVENT_DIGEST", "1", 1);
    }
    if (strcmp(mode, "timeout") == 0) {
        setenv("EU4_GFX_FRAME_TRACE_TIMEOUT_MS", "1", 1);
    }
    if (strcmp(mode, "scene") == 0 || strcmp(mode, "scene-missed") == 0) {
        setenv("EU4_GFX_FRAME_TRACE_BOUNDARY", "scene", 1);
        setenv("EU4_GFX_FRAME_TRACE_TIMELINE", "1", 1);
    }
    eu4_gfx_frame_trace_test_reset_seal();
    if (strcmp(mode, "late-consumer") != 0 &&
        eu4_gfx_frame_sealed_subscribe(seal_test_callback, &state) != 0) {
        return 3;
    }
    if (eu4_gfx_frame_trace_configure_from_env() <= 0) {
        return 2;
    }
    if (strcmp(mode, "scene") == 0) {
        eu4_gfx_capture_frame frame;
        eu4_gfx_observe_call call;
        memset(&frame, 0, sizeof(frame));
        make_call(&call, 0x1000);
        /* Frame 1: draws then the end-of-frame PresentScene entry/exit. */
        draw(true, 0x1000);
        draw(false, 0x1000);
        eu4_gfx_frame_trace_boundary_entry(&frame, &call);
        eu4_gfx_frame_trace_boundary_exit(&frame, &call, NULL);
        /* Frame 2 is selected at the previous exit and finalized at this
           PresentScene entry, before the exit that may swap. */
        draw(true, 0x1000);
        draw(false, 0x1000);
        clear_one();
        draw(true, 0x1000);
        draw(false, 0x1000);
        valid_copy(442);
        eu4_gfx_frame_trace_boundary_entry(&frame, &call);
        /* PresentScene's body may call _GfxPresent; with the entry boundary the
           frame is already sealed and these hooks must not alter it. */
        eu4_gfx_frame_trace_present_entry(&frame, &call);
        eu4_gfx_frame_trace_present_exit(&frame, &call, NULL);
        eu4_gfx_frame_trace_boundary_exit(&frame, &call, NULL);
    } else if (strcmp(mode, "scene-missed") == 0) {
        eu4_gfx_capture_frame frame;
        eu4_gfx_observe_call call;
        memset(&frame, 0, sizeof(frame));
        make_call(&call, 0x1000);
        draw(true, 0x1000);
        draw(false, 0x1000);
        eu4_gfx_frame_trace_boundary_entry(&frame, &call);
        eu4_gfx_frame_trace_boundary_exit(&frame, &call, NULL);
        draw(true, 0x1000);
        draw(false, 0x1000);
        /* No boundary_entry: finalizing at exit would read after a possible
           swap, so this must be an explicit incomplete frame. */
        eu4_gfx_frame_trace_boundary_exit(&frame, &call, NULL);
    } else if (strcmp(mode, "timeout") == 0) {
        arm_via_present();
        usleep(5000);
        draw(true, 0x1000);
        draw(false, 0x1000);
    } else {
        arm_via_present();
        draw(true, 0x1000);
        draw(false, 0x1000);
        if (strcmp(mode, "indexed") == 0) {
            draw_indexed(true);
            draw_indexed(false);
        }
        clear_one();
        draw(true, 0x1000);
        draw(false, 0x1000);
        valid_copy(442);
        if (strcmp(mode, "malformed") == 0) {
            append_line(directory, "not-json\n");
        } else if (strcmp(mode, "count-mismatch") == 0) {
            append_line(directory, "{\"kind\":\"draw\"}\n");
        }
        finish_via_present();
        if (strcmp(mode, "late-consumer") == 0) {
            if (eu4_gfx_frame_sealed_subscribe(seal_test_callback, &state) != 0) {
                return 3;
            }
            eu4_gfx_frame_sealed_dispatch();
        }
        if (strcmp(mode, "second-frame") == 0) {
            /* The one-shot gate must not notify or capture a second frame. */
            arm_via_present();
            draw(true, 0x1000);
            draw(false, 0x1000);
            finish_via_present();
        }
        if (strcmp(mode, "reconfigure") == 0) {
            /* A reconfigure must not restart the process identity or the
               one-shot gate. */
            if (eu4_gfx_frame_trace_configure_from_env() <= 0) {
                return 2;
            }
            arm_via_present();
            draw(true, 0x1000);
            draw(false, 0x1000);
            finish_via_present();
        }
    }
    report_seal(mode, &state);
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
    if (strcmp(scenario, "prefix-valid") == 0) {
        return scenario_prefix(directory, "2", -1);
    }
    if (strcmp(scenario, "prefix-offscreen") == 0) {
        return scenario_prefix(directory, "2", 6);
    }
    if (strcmp(scenario, "prefix-incomplete") == 0) {
        return scenario_prefix_incomplete(directory);
    }
    if (strcmp(scenario, "prefix-copy") == 0) {
        return scenario_prefix_copy(directory);
    }
    if (strcmp(scenario, "prefix-seal") == 0) {
        return scenario_prefix_seal(directory);
    }
    if (strcmp(scenario, "prefix-late") == 0) {
        return scenario_prefix_late(directory);
    }
    if (strcmp(scenario, "prefix-r32f") == 0) {
        return scenario_prefix_r32f(directory);
    }
    if (strcmp(scenario, "checkpoint-offscreen") == 0) {
        return scenario_checkpoint_offscreen(directory);
    }
    if (strcmp(scenario, "checkpoint-draw") == 0) {
        return scenario_checkpoint_draw(directory);
    }
    if (strcmp(scenario, "checkpoint-clear") == 0) {
        return scenario_checkpoint_clear(directory);
    }
    if (strcmp(scenario, "checkpoint-copy") == 0) {
        return scenario_checkpoint_copy(directory);
    }
    if (strcmp(scenario, "checkpoint-missed") == 0) {
        return scenario_checkpoint_missed(directory);
    }
    if (strcmp(scenario, "checkpoint-duplicate") == 0) {
        return scenario_checkpoint_duplicate(directory);
    }
    if (strcmp(scenario, "checkpoint-opening-clear") == 0) {
        return scenario_checkpoint_opening_clear(directory);
    }
    if (strcmp(scenario, "checkpoint-closing-clear") == 0) {
        return scenario_checkpoint_closing_clear(directory);
    }
    if (strcmp(scenario, "checkpoint-closing-clear-selected") == 0) {
        return scenario_checkpoint_closing_clear_selected(directory);
    }
    if (strcmp(scenario, "checkpoint-out-of-range") == 0) {
        return scenario_checkpoint_out_of_range(directory);
    }
    if (strcmp(scenario, "checkpoint-multi") == 0) {
        return scenario_checkpoint_multi(directory);
    }
    if (strcmp(scenario, "checkpoint-multi-missed") == 0) {
        return scenario_checkpoint_multi_missed(directory);
    }
    if (strcmp(scenario, "checkpoint-multi-budget") == 0) {
        return scenario_checkpoint_multi_budget(directory);
    }
    if (strcmp(scenario, "checkpoint-multi-pending") == 0) {
        return scenario_checkpoint_multi_pending(directory);
    }
    if (strcmp(scenario, "checkpoint-multi-unordered") == 0) {
        return scenario_checkpoint_multi_unordered(directory);
    }
    if (strcmp(scenario, "checkpoint-multi-duplicate") == 0) {
        return scenario_checkpoint_multi_duplicate(directory);
    }
    if (strcmp(scenario, "checkpoint-multi-empty") == 0) {
        return scenario_checkpoint_multi_empty(directory);
    }
    if (strcmp(scenario, "checkpoint-multi-over-count") == 0) {
        return scenario_checkpoint_multi_over_count(directory);
    }
    if (strcmp(scenario, "checkpoint-multi-both-forms") == 0) {
        return scenario_checkpoint_multi_both_forms(directory);
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
    if (strcmp(scenario, "continuous") == 0) {
        int frames = argc > 3 ? atoi(argv[3]) : 3;
        int bytes = argc > 4 ? atoi(argv[4]) : 67108864;
        return scenario_continuous(directory, frames, bytes);
    }
    if (strcmp(scenario, "continuous-carried") == 0) {
        int frames = argc > 3 ? atoi(argv[3]) : 3;
        int bytes = argc > 4 ? atoi(argv[4]) : 64 * 1024 * 1024;
        return scenario_continuous_carried(directory, frames, bytes, 0);
    }
    if (strcmp(scenario, "continuous-carried-lifecycle") == 0) {
        int frames = argc > 3 ? atoi(argv[3]) : 3;
        int bytes = argc > 4 ? atoi(argv[4]) : 64 * 1024 * 1024;
        return scenario_continuous_carried(directory, frames, bytes, 1);
    }
    if (strcmp(scenario, "continuous-carried-delete") == 0) {
        int frames = argc > 3 ? atoi(argv[3]) : 3;
        int bytes = argc > 4 ? atoi(argv[4]) : 64 * 1024 * 1024;
        return scenario_continuous_carried(directory, frames, bytes, 2);
    }
    if (strcmp(scenario, "continuous-byte-budget") == 0) {
        return scenario_continuous(directory, 8, 1);
    }
    if (strcmp(scenario, "continuous-reuse") == 0) {
        return scenario_continuous_reuse(directory);
    }
    if (strcmp(scenario, "mutation-target") == 0) {
        return scenario_mutation_target(directory);
    }
    if (strcmp(scenario, "mutation-cube") == 0) {
        return scenario_mutation_cube(directory);
    }
    if (strcmp(scenario, "lifecycle-order") == 0) {
        return scenario_lifecycle_order(directory,
                                        argc > 3 ? argv[3] : "two-calls");
    }
    if (strcmp(scenario, "capacity-carried") == 0) {
        return scenario_carried_capacity(directory);
    }
    if (strncmp(scenario, "capacity-", 9) == 0) {
        int count = argc > 3 ? atoi(argv[3]) : 64;
        return scenario_capacity(directory, scenario + 9, count);
    }
    if (strcmp(scenario, "scene-sequence") == 0) {
        return scenario_scene(directory, 0);
    }
    if (strcmp(scenario, "scene-at-frame") == 0) {
        return scenario_scene(directory, 1);
    }
    if (strncmp(scenario, "seal-", 5) == 0) {
        return scenario_seal(directory, scenario + 5);
    }
    fprintf(stderr, "unknown scenario %s\n", scenario);
    return 2;
}
