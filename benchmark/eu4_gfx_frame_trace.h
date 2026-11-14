#ifndef EU4_GFX_FRAME_TRACE_H
#define EU4_GFX_FRAME_TRACE_H

#include "eu4_gfx_observe.h"

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
