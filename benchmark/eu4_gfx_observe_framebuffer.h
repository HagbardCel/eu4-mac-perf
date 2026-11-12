#ifndef EU4_GFX_OBSERVE_FRAMEBUFFER_H
#define EU4_GFX_OBSERVE_FRAMEBUFFER_H

#include "eu4_gfx_observe.h"

#include <stdbool.h>
#include <stdint.h>

/* Generic original-GL framebuffer readback for an observed draw.
 *
 * This is observation only: it never replaces a GL call, never changes engine
 * state (the pack alignment it touches is restored), and stays disabled unless
 * EU4_GFX_OPS_FRAMEBUFFER is non-empty. When armed it reads the *draw
 * framebuffer currently bound by the original engine* before and after the
 * selected draw exits, so the payload is produced by the original OpenGL
 * implementation rather than by a reconstruction.
 *
 * The capture is one-shot per process: the first observed draw whose entry hook
 * selects it is captured. The readback happens in the draw's exit hook, which
 * runs after the original body (and therefore after glDrawArrays) has returned.
 */

typedef enum {
    EU4_GFX_FRAMEBUFFER_CAPTURE_OK = 0,
    EU4_GFX_FRAMEBUFFER_CAPTURE_DISABLED = 1,
    EU4_GFX_FRAMEBUFFER_CAPTURE_NOT_SELECTED = 2,
    EU4_GFX_FRAMEBUFFER_CAPTURE_ALREADY = 3,
    EU4_GFX_FRAMEBUFFER_CAPTURE_GL = 4,
    EU4_GFX_FRAMEBUFFER_CAPTURE_IO = 5,
    EU4_GFX_FRAMEBUFFER_CAPTURE_ARGUMENT = 6,
} eu4_gfx_framebuffer_status;

/* Read EU4_GFX_OPS_FRAMEBUFFER. Accepted values: "" (disabled), "post" and
 * "pre+post". Requires EU4_GFX_STARTUP_DIR to be an absolute path. */
bool eu4_gfx_observe_framebuffer_configure_from_env(void);

bool eu4_gfx_observe_framebuffer_enabled(void);

/* Called from the observed draw entry hook after the target draw is claimed.
 * Reads the pre-draw contents when "pre+post" was requested. */
eu4_gfx_framebuffer_status eu4_gfx_observe_framebuffer_before_draw(
    const eu4_gfx_observe_call *call, int32_t mode, int32_t effective_count,
    int32_t first);

/* Record the vertex range the selected draw consumes so the post-draw hook can
 * read the live GL vertex buffer contents (in addition to the engine resource
 * payloads). The readback binds the draw's array buffer, reads the exact range,
 * and restores the previous binding; it is observation only. */
void eu4_gfx_observe_framebuffer_set_vertex_range(uint32_t vertex_stride,
                                                  uint32_t vertex_offset,
                                                  int32_t first,
                                                  int32_t count);

/* Called from the observed draw exit hook for the selected target draw.
 * Performs glFinish, reads the framebuffer, writes the payload and metadata,
 * and returns OK only when the post-draw payload is complete. */
eu4_gfx_framebuffer_status eu4_gfx_observe_framebuffer_after_draw(
    const eu4_gfx_observe_call *call, int32_t mode, int32_t effective_count,
    int32_t first);

/* Exposed for the host harness: the number of completed post-draw captures. */
uint32_t eu4_gfx_observe_framebuffer_captures(void);

/* Weak in the host: returns the GL drawable extent when a Cocoa NSOpenGL view
 * is current. Returns 0 when unavailable; callers then report the viewport. */
int eu4_gfx_gl_drawable_extent(uint32_t *width, uint32_t *height);

/* Focused native test hooks. Not part of the observer path. */
void eu4_gfx_observe_framebuffer_test_fail_pack_config(int on);
int eu4_gfx_observe_framebuffer_test_pack_roundtrip(void);
int eu4_gfx_observe_framebuffer_test_pack_failure(void);
int eu4_gfx_observe_framebuffer_test_framebuffer(int draw_framebuffer,
                                                 int read_framebuffer,
                                                 int read_buffer,
                                                 int draw_buffer);

#endif
