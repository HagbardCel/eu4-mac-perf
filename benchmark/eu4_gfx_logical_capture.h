#ifndef EU4_GFX_LOGICAL_CAPTURE_H
#define EU4_GFX_LOGICAL_CAPTURE_H

#include <stdint.h>

#include "eu4_gfx_simple_draw.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Diagnostic logical-extent capture of the same reconstructed draw.
 *
 * The M3 live path renders into the physical (Retina) engine target. The
 * original OpenGL game renders into a logical-size drawable that the window
 * server scales. To compare the reconstruction with the original GL image at
 * identical extent, this captures the same draw inputs through the same Metal
 * encoder into a logical-size target, once, after the live physical draw was
 * submitted. It never participates in presentation and is disabled unless
 * EU4_GFX_LOGICAL_CAPTURE=1.
 *
 * Returns 1 only when the GPU command completed and a complete payload with
 * its declaration was written. Every failure writes a machine-readable record
 * and returns 0; the live physical path is unaffected.
 */
/* Reset the persistent prefix target at a frame/clear/generation boundary.
   The target is retained and the next encoded draw clears it. */
void eu4_gfx_logical_capture_reset(void);

/* Record the engine clear colour for the next prefix frame. `color` is the
   same four-float value passed to _GfxClear; NULL means opaque black. */
void eu4_gfx_logical_capture_set_clear(const float *color);

int eu4_gfx_logical_capture_from_env(
    void *device, void *queue, const void *immediate_context, int32_t count,
    int32_t first, const eu4_gfx_simple_draw_inputs *base_inputs,
    const eu4_gfx_simple_draw_state *state, uint64_t caller_rva);

#ifdef __cplusplus
}
#endif

#endif
