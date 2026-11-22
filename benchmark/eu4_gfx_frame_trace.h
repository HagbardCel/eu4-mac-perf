#ifndef EU4_GFX_FRAME_TRACE_H
#define EU4_GFX_FRAME_TRACE_H

#include "eu4_gfx_frame_seal.h"
#include "eu4_gfx_observe.h"

#include <stddef.h>
#include <stdint.h>

/* Complete-frame original-GL trace for differential Metal validation.
 *
 * The tracer observes the engine's real frame boundary (_GfxPresent) and
 * records every render-affecting draw between two presents together with the
 * effective OpenGL state read back from the live driver. It never replaces a
 * GL call and restores every binding it has to change for a readback.
 *
 * Frame selection is content based: the caller set of one frame selects the
 * *next* frame for full recording. That makes the reference reproducible even
 * though the title scene is animated, and it keeps the predicate in run-time
 * configuration rather than in the renderer.
 *
 * Environment (configured by automation.m6_frame):
 *   EU4_GFX_FRAME_TRACE_DIR        absolute output directory (required)
 *   EU4_GFX_FRAME_TRACE_CALLERS    comma-separated caller RVAs that must all
 *                                  appear in the selecting frame (hex, "0x")
 *   EU4_GFX_FRAME_TRACE_MIN_DRAWS  minimum draws in the selecting frame
 *   EU4_GFX_FRAME_TRACE_OCCURRENCE selecting frame to capture (default 1)
 *   EU4_GFX_FRAME_TRACE_AT_FRAME   capture this present index directly
 *   EU4_GFX_FRAME_TRACE_MAX_DRAWS  hard draw cap inside a captured frame
 *   EU4_GFX_FRAME_TRACE_DEFAULT_CHECKPOINT_EVENT
 *                                  optional single event index whose
 *                                  default-framebuffer contents are captured
 *                                  immediately before it executes.  Only draw,
 *                                  clear and the recorded copy expose this
 *                                  pre-execution boundary; an event that never
 *                                  reaches one fails the capture closed.
 *   EU4_GFX_FRAME_TRACE_DEFAULT_CHECKPOINT_EVENTS
 *                                  ordered, comma-separated form of the above
 *                                  for a bounded set of checkpoints in one
 *                                  frame.  Selecting both forms is refused.
 *                                  Each checkpoint carries the queried default
 *                                  drawable properties (samples, bit depths,
 *                                  draw/read buffer, sRGB/dither/multisample)
 *                                  plus the exact colour, depth and stencil
 *                                  contents at that pre-event boundary.  A
 *                                  checkpoint selected at an event that does
 *                                  not target framebuffer zero fails closed.
 *   EU4_GFX_FRAME_TRACE_MAX_CHECKPOINTS
 *                                  maximum accepted checkpoint count
 *                                  (default 4, hard bound 8)
 *   EU4_GFX_FRAME_TRACE_CHECKPOINT_MAX_BYTES
 *                                  aggregate readback-byte budget for the
 *                                  whole checkpoint set (default 128 MiB;
 *                                  colour + depth + stencil cost about
 *                                  9 bytes per drawable pixel)
 *   EU4_GFX_FRAME_TRACE_TIMEOUT_MS bounded time from arming to finish; an
 *                                  expired frame is published as an explicit
 *                                  incomplete result
 *   EU4_GFX_FRAME_SEAL             "1" publishes frame-seal.json and enables
 *                                  the one-shot completion notification
 *   EU4_GFX_FRAME_TRACE_TEST_ONLY  "1" allows frame tracing without a dir
 *                                  (host tests)
 */

#ifdef __cplusplus
extern "C" {
#endif

/* Reads the environment. Returns 1 when tracing is enabled, 0 when disabled,
 * -1 on a malformed configuration. */
int eu4_gfx_frame_trace_configure_from_env(void);

int eu4_gfx_frame_trace_enabled(void);

/* Host-test only: write one manifest through the production writer. The GL
 * identity strings resolve to "" without a context; the file shape and digests
 * are what the test exercises. */
int eu4_gfx_frame_trace_test_manifest(const char *directory);

/* Draw hooks. `entry` runs before the original body, `exit` after it. They are
 * called from the existing draw observer, so the draw capture path keeps a
 * single hook per address. */
void eu4_gfx_frame_trace_draw_entry(eu4_gfx_capture_frame *frame,
                                    eu4_gfx_observe_call *call);
void eu4_gfx_frame_trace_draw_exit(eu4_gfx_capture_frame *frame,
                                   eu4_gfx_observe_call *call);

/* Direct entry/exit callbacks registered in eu4_gfx_observe_entries.def. */
/* State-restoration probe used by the tracer regression host: binds `name` to
   the active unit for `target` and restores the previous binding and pack
   state. Returns 0 when the bind took effect. */
int eu4_gfx_frame_trace_texture_probe(unsigned target, unsigned name);
int eu4_gfx_frame_trace_readback_probe(void);
/* Host-test only: returns 1 when the trace writer's mutex is not held by the
 * calling thread (the callback contract). */
int eu4_gfx_frame_trace_lock_probe(void);
/* Host-test only: clears the seal registry and counters. */
void eu4_gfx_frame_trace_test_reset_seal(void);
/* Host-test only: run the production final-reference readback/validation.
   Returns 0 only when the reference was written and its digest/size checks
   passed. */
int eu4_gfx_frame_trace_test_reference(const char *directory);
/* Host-test only: run the production default-framebuffer checkpoint readback
   and writer without an event stream.  ``out_file`` receives the content-
   addressed colour blob name. */
int eu4_gfx_frame_trace_test_checkpoint(const char *directory, int event,
                                        char *out_file, size_t out_size);
/* Host-test only: run one real observation (shared guarded readback and
   writer) and return its single record. */
int eu4_gfx_frame_trace_test_observe(const char *directory, int event,
                                     const char *timing, const char *kind,
                                     int texture_name, char *out_record,
                                     size_t out_size);
/* Host-test only: force the recorded target framebuffer (no GL context). */
void eu4_gfx_frame_trace_test_set_target(int framebuffer);
void eu4_gfx_frame_trace_test_set_target_format(int internal_format);
/* Host-test only: run the production selected-event checkpoint path with the
   current default framebuffer and GL test state. */
int eu4_gfx_frame_trace_test_checkpoint_production(const char *directory,
                                                   int event,
                                                   char *out_file,
                                                   size_t out_size);
/* Host-test only: the last checkpoint's JSON record and depth/stencil blob
   names, so a synthetic GL context can verify provenance and precision. */
int eu4_gfx_frame_trace_test_checkpoint_records(
    char *json, size_t json_size, char *depth_file, size_t depth_size,
    char *stencil_file, size_t stencil_size);
/* Host-test only: invoke the selected-event hook directly (duplicate guard). */
int eu4_gfx_frame_trace_test_checkpoint_hook(void);
/* Host-test only: 1 when at least one requested slot has been captured. */
int eu4_gfx_frame_trace_test_checkpoint_captured(void);
/* Host-test only: number of requested checkpoint slots in the current
   configuration and whether one specific slot has been captured. */
int eu4_gfx_frame_trace_test_checkpoint_count(void);
int eu4_gfx_frame_trace_test_checkpoint_captured_at(int slot);

/* Effective state of one glCopyTexSubImage2D/glBlitFramebuffer call.  The
   source attachment and the default framebuffer's read buffer are part of the
   copied pixels' identity; read_fbo == 0 alone is not enough. */
typedef struct {
    int is_blit;
    int source_framebuffer;
    int read_buffer;
    int draw_buffer;
    int double_buffered;
    int source_color_type;
    int source_color_name;
    int source_color_level;
    int source_attachment;
    int source_texture;
    int source_level;
    int source_width;
    int source_height;
    int source_internal_format;
    int source_resolved;
    int destination_resolved;
    int destination_texture;
    int destination_level;
    int destination_width;
    int destination_height;
    int destination_internal_format;
    int x;
    int y;
    int width;
    int height;
    int dx;
    int dy;
} eu4_gfx_copy_event;

int eu4_gfx_frame_trace_copy_event(const eu4_gfx_copy_event *event);

/* Post-copy notification.  Called immediately after the real GL copy when the
   opt-in exact-prefix mode is active, so the destination is observed after the
   operation executed.  It is a no-op otherwise. */
void eu4_gfx_frame_trace_copy_exit(void);

/* GL lifecycle notification from the copy/texture interposer.  Deleted GL
   names are removed from the carried render-target set immediately and are
   published in the next continuous frame's lifecycle record, so a recycled
   name cannot silently reuse a previous Metal image. */
void eu4_gfx_frame_trace_texture_deleted(int count, const unsigned *names);

/* GL CPU-write notification from the texture interposer.  ``target`` is the
   actual GL texture target (a cube face is normalised to GL_TEXTURE_CUBE_MAP)
   so the cached captured contents of exactly that (target, name) are
   invalidated.  Ordered glTexImage2D/glTexSubImage2D semantics are not
   reconstructed by the replay, so a mutation of a renderer-generated/carried
   resource additionally invalidates that frame's resource history instead of
   silently reusing a stale Metal texture.  The engine call is always forwarded
   unchanged. */
void eu4_gfx_frame_trace_texture_mutated(unsigned name, int level,
                                         int sub_image, unsigned target);

/* Test-only analogue of a renderer write for the offline continuous-writer
   host.  It is ignored unless TEST_ONLY capture is active, so production
   callers cannot inject a synthetic renderer target. */
void eu4_gfx_frame_trace_test_register_target(const char *name);

/* Cheap per-call probe for the GL interposer.  Returns 1 when the caller must
   collect the full effective state (an armed capture or the first copy of a
   scene).  Returns 0 after counting the copy without any GL queries. */
int eu4_gfx_frame_trace_copy_mode(void);

void eu4_gfx_frame_trace_resolve_entry(eu4_gfx_capture_frame *frame,
                                       eu4_gfx_observe_call *call);

void eu4_gfx_frame_trace_clear_entry(eu4_gfx_capture_frame *frame,
                                     eu4_gfx_observe_call *call);
void eu4_gfx_frame_trace_clear_exit(eu4_gfx_capture_frame *frame,
                                    eu4_gfx_observe_call *call,
                                    eu4_gfx_observe_record *record);
void eu4_gfx_frame_trace_present_entry(eu4_gfx_capture_frame *frame,
                                       eu4_gfx_observe_call *call);
void eu4_gfx_frame_trace_boundary_entry(eu4_gfx_capture_frame *frame,
                                        eu4_gfx_observe_call *call);
void eu4_gfx_frame_trace_boundary_exit(eu4_gfx_capture_frame *frame,
                                       eu4_gfx_observe_call *call,
                                       eu4_gfx_observe_record *record);
void eu4_gfx_frame_trace_present_exit(eu4_gfx_capture_frame *frame,
                                      eu4_gfx_observe_call *call,
                                      eu4_gfx_observe_record *record);

/* The indexed entries have no other observer; their callbacks are registered
 * directly and reuse the draw body. */
void eu4_gfx_frame_trace_draw_indexed_entry(eu4_gfx_capture_frame *frame,
                                            eu4_gfx_observe_call *call);
void eu4_gfx_frame_trace_draw_indexed_exit(eu4_gfx_capture_frame *frame,
                                           eu4_gfx_observe_call *call,
                                           eu4_gfx_observe_record *record);
void eu4_gfx_frame_trace_draw_instanced_entry(eu4_gfx_capture_frame *frame,
                                              eu4_gfx_observe_call *call);
void eu4_gfx_frame_trace_draw_instanced_exit(eu4_gfx_capture_frame *frame,
                                             eu4_gfx_observe_call *call,
                                             eu4_gfx_observe_record *record);

#ifdef __cplusplus
}
#endif

#endif
